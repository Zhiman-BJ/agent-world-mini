"""Generic MCP server for one ToolGen delivery, independent of the client harness."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .delivery_contract import DeliveryPackage
from .mcp_protocol import PROTOCOL_VERSION, SERVER_VERSION, RpcError, public_tools, tool_call_result
from .runtime import ToolRuntime, state_diff


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


RESOURCE_TOOLS = (
    {
        "name": "get_environment_overview",
        "description": (
            "Show the current environment's record sets and filesystem scopes. "
            "Use this before choosing files when the task does not already identify a resource."
        ),
        "inputSchema": _object_schema({}, []),
        "outputSchema": {"type": "object"},
    },
    {
        "name": "list_environment_resources",
        "description": (
            "List real files and directories in the environment and return stable aw:// resource "
            "references. Filter by scope or name instead of guessing a workspace path."
        ),
        "inputSchema": _object_schema(
            {
                "scope_id": {"type": "string", "minLength": 1},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            [],
        ),
        "outputSchema": {"type": "object"},
    },
    {
        "name": "inspect_environment_resource",
        "description": (
            "Inspect one aw:// resource and optionally preview UTF-8 text. "
            "Use the returned reference directly in a business tool call."
        ),
        "inputSchema": _object_schema(
            {
                "ref": {"type": "string", "pattern": "^aw://"},
                "preview_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 20000,
                },
            },
            ["ref"],
        ),
        "outputSchema": {"type": "object"},
    },
)


class ToolMcpServer:
    def __init__(
        self,
        delivery: DeliveryPackage,
        *,
        trace_path: Path | None = None,
        max_tool_calls: int = 100,
        temp_root: Path | None = None,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        self.delivery = delivery
        self.trace_path = trace_path.expanduser().resolve() if trace_path else None
        self.max_tool_calls = max_tool_calls
        self.calls = 0
        software_root = (
            Path(str(delivery.runtime["software_root"]))
            if delivery.runtime.get("backend") == "docker"
            else delivery.software_root
        )
        self.runtime = ToolRuntime(
            delivery.package,
            software_root=software_root,
            temp_root=temp_root,
        )
        self._tools = {str(tool["name"]): tool for tool in delivery.package.tools}
        reserved = {tool["name"] for tool in RESOURCE_TOOLS}
        conflicts = sorted(reserved & set(self._tools))
        if conflicts:
            self.runtime.close()
            raise ValueError("业务工具名与环境资源工具冲突：" + ", ".join(conflicts))

    def close(self) -> None:
        self.runtime.close()

    def __enter__(self) -> "ToolMcpServer":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _trace(self, record: dict[str, Any]) -> None:
        if self.trace_path is None:
            return
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def _resource_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_environment_overview":
            data = self.runtime.resources.overview()
        elif name == "list_environment_resources":
            data = {
                "resources": self.runtime.resources.list(
                    scope_id=arguments.get("scope_id"),
                    query=arguments.get("query"),
                    limit=arguments.get("limit", 100),
                )
            }
        elif name == "inspect_environment_resource":
            data = self.runtime.resources.inspect(
                str(arguments.get("ref", "")),
                preview_chars=arguments.get("preview_chars", 4000),
            )
        else:
            raise RpcError(-32602, f"未知环境资源工具：{name}")
        return {"success": True, "data": data}

    def _call_tool(self, params: Any) -> dict[str, Any]:
        if self.calls >= self.max_tool_calls:
            raise RpcError(-32000, "工具调用次数已达上限")
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call 缺少 params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        resource_names = {tool["name"] for tool in RESOURCE_TOOLS}
        if not isinstance(name, str) or (
            name not in self._tools and name not in resource_names
        ):
            raise RpcError(-32602, f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "工具 arguments 必须是 object")

        self.calls += 1
        before = self.runtime.snapshot()
        runtime_error: str | None = None
        try:
            result = (
                self._resource_call(name, arguments)
                if name in resource_names
                else self.runtime.call(name, arguments)
            )
        except Exception as error:
            runtime_error = f"{type(error).__name__}: {error}"
            result = {
                "success": False,
                "error": {
                    "code": "runtime_error",
                    "path": "$",
                    "message": runtime_error,
                    "retryable": False,
                },
            }
        after = self.runtime.snapshot()
        changes = state_diff(before, after)
        is_error = runtime_error is not None or result.get("success") is False
        self._trace(
            {
                "sequence": self.calls,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "package_id": self.delivery.binding["package_id"],
                "environment_id": self.delivery.binding["environment_id"],
                "tool": name,
                "arguments": arguments,
                "result": result,
                "runtime_error": runtime_error,
                "state_changes": changes,
            }
        )
        return tool_call_result(result, is_error=is_error)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "agent-world-toolgen",
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "Use get_environment_overview and list_environment_resources to find files. "
                    "Pass the returned aw:// references to business tools instead of guessing "
                    "workspace paths."
                ),
            }
        if method == "tools/list":
            return {"tools": [*deepcopy(RESOURCE_TOOLS), *public_tools(self.delivery.package.tools)]}
        if method == "tools/call":
            return self._call_tool(request.get("params"))
        if method == "ping":
            return {}
        if request_id is None:
            return None
        raise RpcError(-32601, f"不支持的 MCP 方法：{method}")
