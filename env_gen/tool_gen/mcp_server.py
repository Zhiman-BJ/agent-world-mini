"""Generic MCP server for one ToolGen delivery, independent of the client harness."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from harness.runtime import ToolRuntime, state_diff
from harness.execution import call_environment_tool

from .delivery_contract import DeliveryPackage
from .mcp_protocol import PROTOCOL_VERSION, SERVER_VERSION, RpcError, public_tools, tool_call_result


class ToolMcpServer:
    def __init__(
        self,
        delivery: DeliveryPackage,
        *,
        trace_path: Path | None = None,
        max_tool_calls: int = 100,
        temp_root: Path | None = None,
        session_root: Path | None = None,
        timeout: int = 300,
        memory_limit: int = 2 * 1024 * 1024 * 1024,
        write_limit: int = 256 * 1024 * 1024,
        process_limit: int = 1024,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        self.delivery = delivery
        self.trace_path = trace_path.expanduser().resolve() if trace_path else None
        self.max_tool_calls = max_tool_calls
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.write_limit = write_limit
        self.process_limit = process_limit
        self.calls = 0
        self._closed = False
        software_root = (
            Path(str(delivery.runtime["software_root"]))
            if delivery.runtime.get("backend") == "docker"
            else delivery.software_root
        )
        self.runtime = ToolRuntime(
            delivery.package,
            software_root=software_root,
            temp_root=temp_root,
            session_root=session_root,
        )
        self._tools = {str(tool["name"]): tool for tool in delivery.package.tools}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.runtime.persistent:
            receipt = {
                "schema_version": "1.0",
                "package_id": self.delivery.binding["package_id"],
                "environment_id": self.delivery.binding["environment_id"],
                "tool_calls": self.calls,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "final_snapshot": self.runtime.snapshot(),
            }
            (self.runtime.root / "session.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
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

    def _call_tool(self, params: Any) -> dict[str, Any]:
        if self.calls >= self.max_tool_calls:
            raise RpcError(-32000, "工具调用次数已达上限")
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call 缺少 params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in self._tools:
            raise RpcError(-32602, f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "工具 arguments 必须是 object")

        self.calls += 1
        before = self.runtime.snapshot()
        runtime_error: str | None = None
        try:
            software = None
            software_root = None
            if self.delivery.runtime.get("backend") == "docker":
                software_root = Path(str(self.delivery.runtime.get("software_root", "/opt/tool-software")))
            elif self.delivery.software_root is not None and self.delivery.python_path is not None:
                software_root = self.delivery.software_root
                software = {
                    "root": str(self.delivery.software_root),
                    "python": str(self.delivery.python_path),
                }
            record = call_environment_tool(
                name,
                arguments,
                self._tools,
                self.runtime.root / "state",
                timeout=self.timeout,
                memory_limit=self.memory_limit,
                write_limit=self.write_limit,
                process_limit=self.process_limit,
                environment=self.delivery.package.environment,
                software=software,
                software_root=software_root,
            )
            result = record.get("result")
            # Harness reports schema/transaction failures in ``error`` but
            # keeps a valid business ``success=false`` result as normal
            # tool output. Preserve that distinction on the MCP wire.
            runtime_error = (
                record.get("error")
                if not (
                    isinstance(result, dict)
                    and result.get("success") is False
                    and not record.get("failure_kind")
                )
                else None
            )
            if not isinstance(result, dict):
                runtime_error = runtime_error or "工具返回值必须是 object"
                result = {
                    "success": False,
                    "error": {
                        "code": "runtime_error",
                        "path": "$",
                        "message": runtime_error,
                        "retryable": False,
                    },
                }
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
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                },
                "serverInfo": {
                    "name": "agent-world-toolgen",
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "Use resources/list and resources/read to discover or inspect files. "
                    "For business tools, pass the returned Scope-relative path with its "
                    "scope_id; do not pass the resource URI as a business argument."
                ),
            }
        if method == "tools/list":
            return {"tools": public_tools(self.delivery.package.tools)}
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
            return self._call_tool(request.get("params"))
        if method == "ping":
            return {}
        if request_id is None:
            return None
        raise RpcError(-32601, f"不支持的 MCP 方法：{method}")

    def _resource_list(self, params: Any) -> list[dict[str, Any]]:
        if params not in (None, {}) and not isinstance(params, dict):
            raise RpcError(-32602, "resources/list params 必须是 object")
        values = self.runtime.resources.list(limit=500)
        return [
            {
                "uri": item["ref"],
                "name": item["name"],
                "description": item.get("description", ""),
                "mimeType": item.get("media_type") or "application/octet-stream",
                "scope_id": item["scope_id"],
                "relative_path": item["relative_path"],
            }
            for item in values
        ]

    def _resource_read(self, uri: str) -> dict[str, Any]:
        import base64
        resource = self.runtime.resources.inspect(uri, preview_chars=0)
        path = self.runtime.resources.resolve(uri, must_exist=True)
        payload: dict[str, Any] = {"uri": uri, "mimeType": resource.get("media_type") or "application/octet-stream"}
        if path.is_dir():
            raise RpcError(-32602, "resources/read 只能读取文件")
        raw = path.read_bytes()
        if len(raw) > 50 * 1024 * 1024:
            raise RpcError(-32000, "资源读取超过 50 MiB 限制，请使用业务工具处理该资源")
        try:
            payload["text"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            payload["blob"] = base64.b64encode(raw).decode("ascii")
        return payload
