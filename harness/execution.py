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
    # Kept lazy so the Harness API stays stable while the process implementation
    # remains isolated from MCP and direct TaskGen callers.
    from .sandbox import call_tool

    return call_tool(*args, **kwargs)


def _direct_call_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sandbox import run_tool_direct

    return run_tool_direct(*args, **kwargs)


def _restore_directory(root: Path, backup: Path) -> None:
    """Restore contents while preserving the cwd directory inode."""
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in backup.iterdir():
        target = root / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def call_environment_tool(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    workspace_root: Path,
    *,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    process_limit: int = 1024,
    call_tool_fn: CallToolFn | None = None,
    environment: dict[str, Any] | None = None,
    software: dict[str, str] | None = None,
    software_root: Path | None = None,
    runtime: dict[str, Any] | None = None,
    record_database: Path | None = None,
    scope_mounts: dict[str, str] | None = None,
    public_workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one tool transaction against the caller's validated workspace."""
    tool = tools.get(name)
    if tool is None:
        return {"tool": name, "arguments": arguments, "result": None, "error": "未知工具"}
    catalog: ResourceCatalog | None = None
    allowed_scopes: set[str] | None = None
    # Keep the resource boundary active for every explicit environment, even
    # when it declares no filesystem scopes. This prevents an opaque resource
    # URI from silently becoming an ordinary business string in a scope-less
    # tool invocation.
    workspace_mode = record_database is not None or scope_mounts is not None
    workspace_root = workspace_root.resolve()
    mounts = {str(key): str(value) for key, value in (scope_mounts or {}).items()}

    def scope_root(scope_id: str, root: Path = workspace_root) -> Path:
        mount = mounts.get(scope_id)
        if mount is None:
            return root / "filesystem_scopes" / scope_id
        return root if mount == "." else root / mount

    if environment is not None:
        catalog = ResourceCatalog(
            environment,
            scope_root,
            **({
                "workspace_root": workspace_root,
                "public_workspace_root": public_workspace_root,
                "scope_mounts": mounts,
            } if workspace_mode else {}),
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
            arguments, schema=tool['inputSchema'], allowed_scopes=allowed_scopes,
        )
    schema_error = _schema_error(tool["inputSchema"], arguments)
    if schema_error:
        return {"tool": name, "arguments": arguments, "result": None, "error": schema_error}

    active_call = call_tool_fn or (_direct_call_tool if workspace_mode else _sandbox_call_tool)
    read_only = (
        call_tool_fn is None
        and tool.get("usageConditions", {}).get("sideEffects") == []
    )

    if workspace_mode:
        database = Path(record_database).resolve() if record_database else None
        temporary_context = None
        workspace_backup = None
        database_backup = None
        database_existed = bool(database and database.is_file())
        if not read_only:
            temporary_context = tempfile.TemporaryDirectory(
                prefix=".task-eval-backup-", dir=workspace_root.parent
            )
            temporary_path = Path(temporary_context.name)
            workspace_backup = temporary_path / "workspace"
            shutil.copytree(workspace_root, workspace_backup)
            database_backup = temporary_path / "records.sqlite"
            if database_existed and database is not None:
                shutil.copy2(database, database_backup)
        try:
            outcome = active_call(
                tool["internal"]["code"],
                arguments,
                workspace_root,
                timeout,
                memory_limit,
                write_limit,
                environment,
                **({"software": software} if software else {}),
                **({"software_root": software_root} if software_root is not None else {}),
                process_limit=process_limit,
                read_only=read_only,
                record_database=database,
                scope_roots={scope_id: scope_root(scope_id) for scope_id in mounts},
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
                try:
                    if catalog is not None:
                        result = catalog.externalize_result(
                            result, schema=tool["outputSchema"], allowed_scopes=allowed_scopes,
                        )
                    error = _schema_error(tool["outputSchema"], result)
                except (ValueError, OSError) as caught:
                    error = str(caught)
            if error is not None and workspace_backup is not None:
                _restore_directory(workspace_root, workspace_backup)
                if database is not None:
                    if database_existed:
                        database.parent.mkdir(parents=True, exist_ok=True)
                        assert database_backup is not None
                        shutil.copy2(database_backup, database)
                    elif database.exists():
                        database.unlink()
        except BaseException:
            if workspace_backup is not None:
                _restore_directory(workspace_root, workspace_backup)
                if database is not None:
                    if database_existed:
                        database.parent.mkdir(parents=True, exist_ok=True)
                        assert database_backup is not None
                        shutil.copy2(database_backup, database)
                    elif database.exists():
                        database.unlink()
            raise
        finally:
            if temporary_context is not None:
                temporary_context.cleanup()
        record = {"tool": name, "arguments": arguments, "result": result, "error": error}
        if outcome.get("kind") is not None:
            record["failure_kind"] = outcome["kind"]
        return record

    with tempfile.TemporaryDirectory(prefix=".task-eval-tool-", dir=workspace_root.parent) as temporary:
        temporary_path = Path(temporary)
        candidate = workspace_root if read_only else temporary_path / "state"
        if not read_only:
            shutil.copytree(workspace_root, candidate, symlinks=True)
        runtime_options: dict[str, Any] = {
            **({"software": software} if software else {}),
            **({"software_root": software_root} if software_root is not None else {}),
        }
        if call_tool_fn is None:
            runtime_options["process_limit"] = process_limit
            if read_only:
                runtime_options["read_only"] = True
            if runtime is not None:
                runtime_options["runtime"] = runtime
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
            try:
                if catalog is not None:
                    output_catalog = ResourceCatalog(
                        environment, lambda scope_id: candidate / 'filesystem_scopes' / scope_id,
                    )
                    result = output_catalog.externalize_result(
                        result, schema=tool['outputSchema'], allowed_scopes=allowed_scopes,
                    )
                error = _schema_error(tool['outputSchema'], result)
            except (ValueError, OSError) as caught:
                error = str(caught)
        if error is None and not read_only:
            previous = temporary_path / "previous"
            workspace_root.rename(previous)
            try:
                candidate.rename(workspace_root)
            except Exception:
                previous.rename(workspace_root)
                raise
    record = {"tool": name, "arguments": arguments, "result": result, "error": error}
    if outcome.get("kind") is not None:
        record["failure_kind"] = outcome["kind"]
    return record


__all__ = ["CallToolFn", "call_environment_tool"]
