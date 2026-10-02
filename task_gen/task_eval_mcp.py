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
from harness.resources import ResourceCatalog  # noqa: E402
from env_gen.tool_gen.mcp_protocol import (  # noqa: E402
    PROTOCOL_VERSION, SERVER_VERSION, RpcError, public_tools, serve_jsonrpc, tool_call_result,
)


def bind_delivery(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve a delivery while retaining the caller's task-specific state path."""
    if not config.get('binding_path'):
        return config
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
    bound = {
        **config,
        'tools': list(delivery.package.tools),
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
        state_value = config.get("state_root", config.get("workspace"))
        if not isinstance(state_value, str) or not state_value:
            raise ValueError("MCP 配置缺少 state_root（兼容字段：workspace）")
        self.state_root = Path(state_value).resolve()
        self.workspace = self.state_root  # Compatibility for existing callers.
        self.trace = Path(config["trace"]).resolve()
        self.resources = None
        if config.get("environment", {}).get("filesystem_scopes"):
            self.resources = ResourceCatalog(
                config["environment"],
                lambda scope_id: self.state_root / "filesystem_scopes" / scope_id,
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
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {
                          "tools": {"listChanged": False},
                          "resources": {"listChanged": False, "subscribe": False},
                      },
                      "serverInfo": {"name": "agent-world-task-eval", "version": SERVER_VERSION}}
            if self.resources is not None:
                result["instructions"] = (
                    "Use resources/list and resources/read to find files. "
                    "For business tools, pass the returned Scope-relative path with its "
                    "scope_id; do not pass the resource URI as a business argument."
                )
            return result
        if method == "tools/list":
            tools = public_tools(self.tools.values())
            if self.choice_tool:
                tools.append(self.choice_tool)
            return {"tools": tools}
        if method == "resources/list":
            return {"resources": self._resource_list(request.get("params"))}
        if method == "resources/read":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
                raise RpcError(-32602, "resources/read 需要 uri")
            return {"contents": [self._resource_read(params["uri"])]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
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
        if self.calls >= int(self.config["max_tool_calls"]):
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
                name, arguments, self.tools, self.state_root,
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

    def _resource_list(self, params: Any) -> list[dict[str, Any]]:
        if self.resources is None:
            return []
        if params not in (None, {}) and not isinstance(params, dict):
            raise RpcError(-32602, "resources/list params 必须是 object")
        return [
            {
                "uri": item["ref"],
                "name": item["name"],
                "description": item.get("description", ""),
                "mimeType": item.get("media_type") or "application/octet-stream",
                "scope_id": item["scope_id"],
                "relative_path": item["relative_path"],
            }
            for item in self.resources.list(limit=500)
        ]

    def _resource_read(self, uri: str) -> dict[str, Any]:
        import base64
        if self.resources is None:
            raise RpcError(-32602, "当前环境没有 Filesystem Scope")
        resource = self.resources.inspect(uri, preview_chars=0)
        path = self.resources.resolve(uri, must_exist=True)
        if path.is_dir():
            raise RpcError(-32602, "resources/read 只能读取文件")
        raw = path.read_bytes()
        if len(raw) > 50 * 1024 * 1024:
            raise RpcError(-32000, "资源读取超过 50 MiB 限制，请使用业务工具处理该资源")
        payload: dict[str, Any] = {
            "uri": uri,
            "mimeType": resource.get("media_type") or "application/octet-stream",
        }
        try:
            payload["text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            payload["blob"] = base64.b64encode(raw).decode("ascii")
        return payload


def serve(config_path: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    server = TaskEvalMcpServer(json.loads(config_path.read_text(encoding="utf-8")))
    serve_jsonrpc(server.handle, stdin=stdin, stdout=stdout)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: task_eval_mcp.py CONFIG_JSON")
    serve(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
