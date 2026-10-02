"""Model-request allowlists for business MCP tools."""

from __future__ import annotations

from typing import Any, Iterable


def expected_mcp_tool_names(
    tools: Iterable[dict[str, Any]],
    environment: dict[str, Any] | None,
    *,
    server_name: str,
) -> set[str]:
    """Build the model-request allowlist from the same catalog served by MCP."""
    names = [str(tool["name"]) for tool in tools]
    # ``resources/list`` and ``resources/read`` are intentionally omitted:
    # they are JSON-RPC methods and have no function-tool name.
    return {f"mcp__{server_name}__{name}" for name in names}


__all__ = [
    "expected_mcp_tool_names",
]
