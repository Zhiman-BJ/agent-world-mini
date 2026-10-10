"""Minimal stdio MCP server exposing one generated environment's tools."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.delivery import load_delivery  # noqa: E402
from harness.execution import (  # noqa: E402, F401
    CallToolFn,
    _schema_error,
    call_environment_tool,
)
from harness.tool_budget import claim_tool_call  # noqa: E402
from harness.mcp_protocol import (  # noqa: E402
    PROTOCOL_VERSION, SERVER_VERSION, RpcError, public_tools, serve_jsonrpc, tool_call_result,
)


def bind_delivery(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delivery while retaining the caller's task-specific state path."""
    if not config.get('binding_path'):
        return config
    delivery = load_delivery(Path(config['binding_path']))
    selected_tools = list(delivery.package.tools)
    if config.get("tools_path"):
        tools_path = Path(str(config["tools_path"])).expanduser().resolve()
        package_root = delivery.binding_path.parent.resolve()
        if tools_path.name != "tools_repaired.json" or not tools_path.is_relative_to(package_root):
            raise ValueError("修复工具文件必须是当前交付包内的 tools/tools_repaired.json")
        try:
            tool_document = json.loads(tools_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"无法读取 tools_repaired.json：{tools_path}: {error}") from error
        if not isinstance(tool_document, dict) or not isinstance(tool_document.get("tools"), list):
            raise ValueError(f"tools_repaired.json 缺少 tools 数组：{tools_path}")
        if tool_document.get("environment_id") != delivery.binding.get("environment_id"):
            raise ValueError("tools_repaired.json environment_id 与 binding 不一致")
        selected_tools = tool_document["tools"]
        names = [tool.get("name") for tool in selected_tools if isinstance(tool, dict)]
        if len(names) != len(selected_tools) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("tools_repaired.json 包含无效工具")
        if len(names) != len(set(names)):
            raise ValueError("tools_repaired.json 存在重复工具名")
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
    if 'tools' in config and {
        tool.get("name") for tool in config["tools"]
    } != {tool.get("name") for tool in selected_tools}:
        raise ValueError('任务工具名称与修复后的交付工具不一致')
    if 'environment' in config and any(config['environment'].get(k) != v
                                      for k, v in delivery.package.environment.items()):
        raise ValueError('任务环境与 binding 交付环境不一致')
    bound = {
        **config,
        'tools': selected_tools,
        'environment': delivery.package.environment,
        'delivery_runtime': dict(delivery.runtime),
    }
    if delivery.runtime.get('backend') == 'docker':
        bound['software'] = None
        bound['software_root'] = str(delivery.runtime['software_root'])
    else:
        bound['software'] = (
            {'root': str(delivery.software_root), 'python': str(delivery.python_path)}
            if delivery.software_root else None
        )
        bound.pop('software_root', None)
    return bound


class TaskEvalMcpServer:
    """Task-scoped executor using ToolGen's shared public MCP contract."""

    def __init__(self, config: dict[str, Any]):
        config = bind_delivery(config)
        self.config = config
        self.tools = {tool["name"]: tool for tool in config["tools"]}
        if len(self.tools) != len(config["tools"]):
            raise ValueError("重复工具名")
        workspace_value = config.get("workspace_root")
        if not isinstance(workspace_value, str) or not workspace_value:
            raise ValueError("MCP 配置缺少 workspace_root")
        self.workspace_root = Path(workspace_value).resolve()
        database_value = config.get("record_database")
        self.record_database = (
            Path(database_value).resolve()
            if isinstance(database_value, str) and database_value
            else None
        )
        mounts = config.get("scope_mounts", {})
        if not isinstance(mounts, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mounts.items()
        ):
            raise ValueError("MCP 配置 scope_mounts 必须是字符串映射")
        self.scope_mounts = mounts
        public_workspace_value = config.get("public_workspace_root")
        self.public_workspace_root = (
            Path(public_workspace_value).resolve()
            if isinstance(public_workspace_value, str) and public_workspace_value
            else self.workspace_root
        )
        self.trace = Path(config["trace"]).resolve()
        marker_value = config.get("budget_stop_marker")
        self.budget_stop_marker = (
            Path(marker_value).resolve()
            if isinstance(marker_value, str) and marker_value
            else None
        )
        counter_value = config.get("tool_budget_counter")
        self.tool_budget_counter = (
            Path(counter_value).resolve()
            if isinstance(counter_value, str) and counter_value
            else None
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
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "agent-world-task-eval",
                    "version": SERVER_VERSION,
                },
            }
        if method == "tools/list":
            tools = public_tools(self.tools.values())
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
        if not isinstance(name, str) or (
            name not in self.tools and not is_choice
        ):
            raise RpcError(-32602, f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "工具 arguments 必须是 object")
        limit = int(self.config["max_tool_calls"])
        if self.tool_budget_counter is not None and self.budget_stop_marker is not None:
            budget_stop = claim_tool_call(
                self.tool_budget_counter, self.budget_stop_marker, limit, name
            )
            budget_exceeded = not budget_stop["allowed"]
        else:
            budget_exceeded = self.calls >= limit
            budget_stop = {
                "allowed": not budget_exceeded,
                "reason": "tool_call_limit_exceeded",
                "limit": limit,
                "observed_count": self.calls,
                "attempted_tool": name,
            }
        if budget_exceeded:
            if self.budget_stop_marker is not None:
                self.budget_stop_marker.parent.mkdir(parents=True, exist_ok=True)
                self.budget_stop_marker.write_text(
                    json.dumps(budget_stop, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            self.trace.parent.mkdir(parents=True, exist_ok=True)
            with self.trace.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "tool": name,
                    "arguments": arguments,
                    "result": None,
                    "error": "工具调用次数已达到上限",
                    "failure_kind": "tool_budget_exceeded",
                    "limit": limit,
                }, ensure_ascii=False, allow_nan=False) + "\n")
            raise RpcError(-32000, "工具调用次数已达到上限")
        self.calls += 1
        if is_choice:
            try:
                error = _schema_error(self.choice_tool['inputSchema'], arguments)
                if error:
                    raise ValueError(error)
                record = {'tool': name, 'arguments': arguments, 'result': self.choices.choose(arguments), 'error': None}
            except ValueError as error:
                record = {'tool': name, 'arguments': arguments, 'result': None, 'error': str(error)}
        else:
            record = call_environment_tool(
                name, arguments, self.tools, self.workspace_root,
                timeout=int(self.config["timeout"]), memory_limit=int(self.config["memory_limit"]),
                write_limit=int(self.config["write_limit"]),
                process_limit=int(self.config.get("process_limit", 1024)),
                environment=self.config.get("environment", {}),
                record_database=self.record_database,
                scope_mounts=self.scope_mounts or None,
                public_workspace_root=self.public_workspace_root,
                **({'software': self.config['software']} if self.config.get('software') else {}),
                **({'software_root': self.config['software_root']} if self.config.get('software_root') else {}),
            )
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        payload = record["result"]
        # Business failures retain their schema-defined shape; the trace keeps
        # the existing error semantics used by evaluators and trajectory export.
        business_failure = (
            name in self.tools and not is_choice and isinstance(payload, dict) and payload.get("success") is False
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
