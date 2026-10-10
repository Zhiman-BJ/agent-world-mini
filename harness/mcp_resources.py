"""MCP Resources projection shared by ToolGen and evaluation servers."""

from __future__ import annotations

import base64
from typing import Any

from .mcp_protocol import RpcError
from .resources import ResourceCatalog


MAX_RESOURCE_BYTES = 50 * 1024 * 1024


def list_mcp_resources(
    catalog: ResourceCatalog | None,
    params: Any,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Project the logical resource catalog into MCP ``resources/list`` items."""
    if params not in (None, {}) and not isinstance(params, dict):
        raise RpcError(-32602, "resources/list params 必须是 object")
    if catalog is None:
        return []
    return [
        {
            "uri": item["ref"],
            "name": item["name"],
            "description": item.get("description", ""),
            "mimeType": item.get("media_type") or "application/octet-stream",
            "scope_id": item["scope_id"],
            "relative_path": item["relative_path"],
        }
        for item in catalog.list(limit=limit)
    ]


def read_mcp_resource(
    catalog: ResourceCatalog | None,
    uri: str,
    *,
    max_bytes: int = MAX_RESOURCE_BYTES,
) -> dict[str, Any]:
    """Return one MCP resource content item with a bounded file read."""
    if catalog is None:
        raise RpcError(-32602, "当前环境没有 Filesystem Scope")
    resource = catalog.inspect(uri, preview_chars=0)
    path = catalog.resolve(uri, must_exist=True)
    if path.is_dir():
        raise RpcError(-32602, "resources/read 只能读取文件")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
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


__all__ = [
    "MAX_RESOURCE_BYTES",
    "list_mcp_resources",
    "read_mcp_resource",
]
