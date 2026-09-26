"""Public resource tools and the corresponding model-facing allowlist."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


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


def resource_tool_names(environment: dict[str, Any] | None) -> tuple[str, ...]:
    """Return resource tools exactly when the MCP server will expose them."""
    if not environment or not environment.get("filesystem_scopes"):
        return ()
    return tuple(str(tool["name"]) for tool in RESOURCE_TOOLS)


def expected_mcp_tool_names(
    tools: Iterable[dict[str, Any]],
    environment: dict[str, Any] | None,
    *,
    server_name: str,
    support_tools: Iterable[str] = (),
) -> set[str]:
    """Build the model-request allowlist from the same catalog served by MCP."""
    names = [str(tool["name"]) for tool in tools]
    names.extend(resource_tool_names(environment))
    names.extend(str(name) for name in support_tools)
    return {f"mcp__{server_name}__{name}" for name in names}


def public_resource_tools(environment: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not resource_tool_names(environment):
        return []
    return deepcopy(list(RESOURCE_TOOLS))


__all__ = [
    "RESOURCE_TOOLS",
    "expected_mcp_tool_names",
    "public_resource_tools",
    "resource_tool_names",
]
