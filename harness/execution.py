"""Task-scoped tool call transaction shared by MCP and direct TaskGen callers."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from jsonschema import validators

from .resources import ResourceCatalog


CallToolFn = Callable[..., dict[str, Any]]


def _schema_error(schema: dict[str, Any], value: Any) -> str | None:
    try:
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
        error = next(validator(schema).iter_errors(value), None)
    except Exception as caught:
        return f"Schema 校验器错误：{type(caught).__name__}: {caught}"
    if error is None:
        return None
    path = ".".join(str(item) for item in error.absolute_path) or "$"
    return f"{path}: {error.message}"


def _sandbox_call_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Kept lazy so the Harness API stays stable while the existing, exercised
    # bubblewrap implementation moves behind it without creating import cycles.
    from task_gen.tool_graph.step_3_chain_execute import _call_tool

    return _call_tool(*args, **kwargs)


def call_environment_tool(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    state_root: Path,
    *,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    process_limit: int = 1024,
    call_tool_fn: CallToolFn | None = None,
    environment: dict[str, Any] | None = None,
    software: dict[str, str] | None = None,
    software_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one tool transaction without exposing the state root to the model."""
    tool = tools.get(name)
    if tool is None:
        return {"tool": name, "arguments": arguments, "result": None, "error": "未知工具"}
    catalog: ResourceCatalog | None = None
    allowed_scopes: set[str] | None = None
    if environment and environment.get("filesystem_scopes"):
        catalog = ResourceCatalog(
            environment,
            lambda scope_id: state_root.resolve() / "filesystem_scopes" / scope_id,
        )
        known_scopes = {
            str(item["scope_id"])
            for item in environment.get("filesystem_scopes", [])
        }
        declared = {
            str(item)
            for item in tool.get("usageConditions", {}).get("targetResources", [])
        }
        allowed_scopes = known_scopes & declared
        arguments = catalog.normalize_arguments(
            arguments,
            schema=tool["inputSchema"],
            allowed_scopes=allowed_scopes,
        )
    schema_error = _schema_error(tool["inputSchema"], arguments)
    if schema_error:
        return {"tool": name, "arguments": arguments, "result": None, "error": schema_error}

    state_root = state_root.resolve()
    active_call = call_tool_fn or _sandbox_call_tool
    with tempfile.TemporaryDirectory(prefix=".task-eval-tool-", dir=state_root.parent) as temporary:
        temporary_path = Path(temporary)
        candidate = temporary_path / "state"
        shutil.copytree(state_root, candidate, symlinks=True)
        runtime_options: dict[str, Any] = {
            **({"software": software} if software else {}),
            **({"software_root": software_root} if software_root is not None else {}),
        }
        if call_tool_fn is None:
            runtime_options["process_limit"] = process_limit
        outcome = active_call(
            tool["internal"]["code"],
            arguments,
            candidate,
            timeout,
            memory_limit,
            write_limit,
            environment,
            **runtime_options,
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
            if catalog is not None:
                result = catalog.externalize_result(
                    result,
                    schema=tool["outputSchema"],
                    allowed_scopes=allowed_scopes,
                )
            error = _schema_error(tool["outputSchema"], result)
        if error is None:
            previous = temporary_path / "previous"
            state_root.rename(previous)
            try:
                candidate.rename(state_root)
            except Exception:
                previous.rename(state_root)
                raise
    record = {"tool": name, "arguments": arguments, "result": result, "error": error}
    if outcome.get("kind") is not None:
        record["failure_kind"] = outcome["kind"]
    return record


__all__ = ["CallToolFn", "call_environment_tool"]
