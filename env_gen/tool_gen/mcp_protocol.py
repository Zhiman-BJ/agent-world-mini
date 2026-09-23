"""Shared MCP wire contract for ToolGen deliveries and TaskGen evaluations."""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from typing import Any, Callable, Iterable, TextIO


PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "1.0"


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _usage_description(tool: dict[str, Any]) -> str:
    description = str(tool.get("description", "")).strip()
    usage = tool.get("usageConditions")
    if not isinstance(usage, dict):
        return description
    lines: list[str] = []
    for label, key in (
        ("Target resources", "targetResources"),
        ("Target objects", "targetObjects"),
        ("Preconditions", "preconditions"),
        ("Side effects", "sideEffects"),
    ):
        values = usage.get(key)
        if isinstance(values, list) and values:
            rendered = [
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                if isinstance(item, dict)
                else str(item)
                for item in values
            ]
            lines.append(f"{label}: " + "; ".join(rendered))
    if usage is not None:
        lines.append(
            "Filesystem Scope paths are relative to the named scope root. "
            "Pass paths returned by one tool unchanged to the next tool; never prepend "
            "/workspace, filesystem_scopes/<scope_id>, or a host absolute path."
        )
    return "\n".join([description, *lines])


def mcp_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the MCP object-root form while accepting older ToolGen packages."""
    normalized = deepcopy(schema)
    normalized.setdefault("type", "object")
    return normalized


def public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Project an internal ToolGen tool into the exact MCP tools/list shape."""
    return {
        "name": str(tool["name"]),
        "description": _usage_description(tool),
        "inputSchema": deepcopy(tool["inputSchema"]),
        "outputSchema": mcp_output_schema(tool["outputSchema"]),
    }


def public_tools(tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_tool(tool) for tool in tools]


def tool_call_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, allow_nan=False),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


RequestHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


def _write_response(stdout: TextIO, response: dict[str, Any]) -> None:
    stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
    stdout.flush()


def serve_jsonrpc(
    handler: RequestHandler,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    """Serve newline-framed MCP JSON-RPC requests over stdio."""
    for line in stdin:
        if not line.strip():
            continue
        request: dict[str, Any] | None = None
        try:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as error:
                raise RpcError(-32700, f"JSON 解析失败：{error}") from error
            if not isinstance(parsed, dict):
                raise RpcError(-32600, "JSON-RPC request 必须是 object")
            request = parsed
            if request.get("jsonrpc") != "2.0":
                raise RpcError(-32600, "jsonrpc 必须为 2.0")
            result = handler(request)
            if result is not None and "id" in request:
                _write_response(
                    stdout,
                    {"jsonrpc": "2.0", "id": request["id"], "result": result},
                )
        except Exception as error:
            request_id = request.get("id") if request is not None else None
            if request is not None and "id" not in request:
                continue
            code = error.code if isinstance(error, RpcError) else -32603
            message = (
                str(error)
                if isinstance(error, RpcError)
                else f"{type(error).__name__}: {error}"
            )
            _write_response(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": code, "message": message},
                },
            )
