"""Minimal stdio MCP server exposing one generated environment's tools."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_gen.tool_graph.step_3_chain_execute import (  # noqa: E402
    _call_tool,
    _schema_error,
)
from env_gen.tool_gen.mcp_protocol import (  # noqa: E402
    PROTOCOL_VERSION, SERVER_VERSION, RpcError, public_tools, serve_jsonrpc, tool_call_result,
)
from env_gen.tool_gen.mcp_server import RESOURCE_TOOLS  # noqa: E402
from env_gen.tool_gen.resources import ResourceCatalog  # noqa: E402


CallToolFn = Callable[..., dict[str, Any]]


def bind_delivery(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delivery while retaining the caller's task-specific state path."""
    if not config.get('binding_path'):
        return config
    from env_gen.tool_gen.kimi_mcp import load_delivery
    delivery = load_delivery(Path(config['binding_path']))
    # Internal code is deliberately allowed to differ from the frozen delivery
    # artifact: the public MCP contract is the name, description, usage rules,
    # and input/output schemas.  Comparing ``internal.code`` rejects compatible
    # bug fixes (for example, creating an output directory before writing it)
    # before the tool can even be executed.
    public_fields = ('name', 'description', 'usageConditions', 'inputSchema', 'outputSchema')
    public_tools = lambda tools: [
        {field: tool.get(field) for field in public_fields}
        for tool in tools
    ]
    if 'tools' in config and public_tools(config['tools']) != public_tools(delivery.package.tools):
        raise ValueError('任务工具与 binding 交付工具不一致')
    if 'environment' in config and any(config['environment'].get(k) != v
                                      for k, v in delivery.package.environment.items()):
        raise ValueError('任务环境与 binding 交付环境不一致')
    return {**config, 'tools': list(delivery.package.tools), 'environment': delivery.package.environment,
            'software': {'root': str(delivery.software_root), 'python': str(delivery.python_path)}
                        if delivery.software_root else None}


def call_environment_tool(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    workspace: Path,
    *,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    process_limit: int = 1024,
    call_tool_fn: CallToolFn = _call_tool,
    environment: dict[str, Any] | None = None,
    software: dict[str, str] | None = None,
    software_root: Path | None = None,
) -> dict[str, Any]:
    tool = tools.get(name)
    if tool is None:
        return {"tool": name, "arguments": arguments, "result": None, "error": "未知工具"}
    if environment and environment.get("filesystem_scopes"):
        catalog = ResourceCatalog(
            environment,
            lambda scope_id: workspace.resolve() / "filesystem_scopes" / scope_id,
        )
        known_scopes = {
            str(item["scope_id"])
            for item in environment.get("filesystem_scopes", [])
        }
        declared = set(
            str(item)
            for item in tool.get("usageConditions", {}).get("targetResources", [])
        )
        arguments = catalog.normalize_arguments(
            arguments,
            schema=tool["inputSchema"],
            allowed_scopes=known_scopes & declared,
        )
    schema_error = _schema_error(tool["inputSchema"], arguments)
    if schema_error:
        return {"tool": name, "arguments": arguments, "result": None, "error": schema_error}
    workspace = workspace.resolve()
    with tempfile.TemporaryDirectory(prefix=".task-eval-tool-", dir=workspace.parent) as temporary:
        temporary_path = Path(temporary)
        candidate = temporary_path / "workspace"
        shutil.copytree(workspace, candidate, symlinks=True)
        runtime_options: dict[str, Any] = {
            **({'software': software} if software else {}),
            **({'software_root': software_root} if software_root is not None else {}),
        }
        if call_tool_fn is _call_tool:
            runtime_options['process_limit'] = process_limit
        outcome = call_tool_fn(
            tool["internal"]["code"], arguments, candidate, timeout, memory_limit, write_limit,
            environment, **runtime_options,
        )
        result = outcome.get("result")
        error = outcome.get("error")
        if outcome.get("kind") is not None:
            error = error or f"工具执行失败：{outcome['kind']}"
        elif not isinstance(result, dict):
            error = "工具返回值必须是 object"
        elif result.get("success") is not True:
            error = "工具返回值必须包含 success=true"
        else:
            error = _schema_error(tool["outputSchema"], result)
        if error is None:
            previous = temporary_path / "previous"
            workspace.rename(previous)
            try:
                candidate.rename(workspace)
            except Exception:
                previous.rename(workspace)
                raise
    record = {"tool": name, "arguments": arguments, "result": result, "error": error}
    if outcome.get("kind") is not None:
        record["failure_kind"] = outcome["kind"]
    return record


class TaskEvalMcpServer:
    """Task-scoped executor using ToolGen's shared public MCP contract."""

    def __init__(self, config: dict[str, Any]):
        config = bind_delivery(config)
        self.config = config
        self.tools = {tool["name"]: tool for tool in config["tools"]}
        if len(self.tools) != len(config["tools"]):
            raise ValueError("重复工具名")
        self.workspace = Path(config["workspace"]).resolve()
        self.trace = Path(config["trace"]).resolve()
        self.resources = None
        if config.get("environment", {}).get("filesystem_scopes"):
            self.resources = ResourceCatalog(
                config["environment"],
                lambda scope_id: self.workspace / "filesystem_scopes" / scope_id,
            )
        self.calls = 0
        self.choices = None
        self.choice_tool = None
        if 'review_choice_seed' in config:
            from task_gen.tool_graph.review_choices import ReviewChoices, TOOL
            if TOOL['name'] in self.tools:
                raise ValueError('review 工具名称冲突')
            self.choices = ReviewChoices(config['review_choice_seed'])
            self.choice_tool = TOOL
        if config.get('resume_trace') and self.trace.exists():
            for line in self.trace.read_text(encoding='utf-8').splitlines():
                record = json.loads(line)
                self.calls += 1
                if self.choices is not None and record['tool'] == self.choice_tool['name'] and record.get('error') is None:
                    if self.choices.choose(record['arguments']) != record['result']:
                        raise ValueError('方案选择历史无法一致恢复')

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "agent-world-task-eval", "version": SERVER_VERSION}}
            if self.resources is not None:
                result["instructions"] = (
                    "Use get_environment_overview and list_environment_resources to find files. "
                    "Pass aw:// references to business tools instead of guessing workspace paths."
                )
            return result
        if method == "tools/list":
            tools = public_tools(self.tools.values())
            if self.resources is not None:
                tools = [*deepcopy(RESOURCE_TOOLS), *tools]
            if self.choice_tool:
                tools.append(self.choice_tool)
            return {"tools": tools}
        if method == "tools/call":
            return self._call(request.get("params"))
        if method == "ping":
            return {}
        if "id" not in request:
            return None
        raise RpcError(-32601, f"不支持的 MCP 方法：{method}")

    def _call(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call 缺少 params")
        name, arguments = params.get("name"), params.get("arguments", {})
        is_choice = self.choice_tool is not None and name == self.choice_tool['name']
        resource_names = {tool["name"] for tool in RESOURCE_TOOLS} if self.resources else set()
        is_resource = name in resource_names
        if not isinstance(name, str) or (
            name not in self.tools and not is_choice and not is_resource
        ):
            raise RpcError(-32602, f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "工具 arguments 必须是 object")
        if self.calls >= int(self.config["max_tool_calls"]):
            raise RpcError(-32000, "工具调用次数已达到上限")
        self.calls += 1
        if is_resource:
            try:
                if name == "get_environment_overview":
                    data = self.resources.overview()
                elif name == "list_environment_resources":
                    data = {"resources": self.resources.list(
                        scope_id=arguments.get("scope_id"), query=arguments.get("query"),
                        limit=arguments.get("limit", 100))}
                else:
                    data = self.resources.inspect(
                        str(arguments.get("ref", "")),
                        preview_chars=arguments.get("preview_chars", 4000),
                    )
                record = {"tool": name, "arguments": arguments,
                          "result": {"success": True, "data": data}, "error": None}
            except Exception as error:
                record = {"tool": name, "arguments": arguments, "result": None,
                          "error": f"{type(error).__name__}: {error}"}
        elif is_choice:
            try:
                error = _schema_error(self.choice_tool['inputSchema'], arguments)
                if error:
                    raise ValueError(error)
                record = {'tool': name, 'arguments': arguments, 'result': self.choices.choose(arguments), 'error': None}
            except ValueError as error:
                record = {'tool': name, 'arguments': arguments, 'result': None, 'error': str(error)}
        else:
            record = call_environment_tool(
                name, arguments, self.tools, self.workspace,
                timeout=int(self.config["timeout"]), memory_limit=int(self.config["memory_limit"]),
                write_limit=int(self.config["write_limit"]),
                process_limit=int(self.config.get("process_limit", 1024)),
                environment=self.config.get("environment", {}),
                **({'software': self.config['software']} if self.config.get('software') else {}),
                **({'software_root': self.config['software_root']} if self.config.get('software_root') else {}),
            )
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        payload = record["result"]
        # Business failures retain their schema-defined shape; the trace keeps
        # the existing error semantics used by evaluators and ReAct.
        business_failure = (
            not is_choice and not is_resource and isinstance(payload, dict) and payload.get("success") is False
            and _schema_error(self.tools[name]["outputSchema"], payload) is None
        )
        if record["error"] is not None and not business_failure:
            timeout = record.get("failure_kind") == "timeout"
            payload = {"success": False, "error": {
                "code": "timeout" if timeout else "runtime_error",
                "path": "$",
                "message": str(record["error"]),
                "retryable": timeout,
            }, "tool_result": payload}
        return tool_call_result(payload, is_error=record["error"] is not None or business_failure)


def serve(config_path: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    server = TaskEvalMcpServer(json.loads(config_path.read_text(encoding="utf-8")))
    serve_jsonrpc(server.handle, stdin=stdin, stdout=stdout)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: task_eval_mcp.py CONFIG_JSON")
    serve(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
