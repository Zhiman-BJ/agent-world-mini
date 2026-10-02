"""Program-form Solution 与 clean replay 共用的 Harness Runtime。

每次执行都通过 ``create_task_run_layout`` 分离 ``execution-state`` 与
``model-workspace``，所有业务工具统一委托 ``call_environment_tool`` 完成
Schema、资源 Scope、沙箱、状态提交和失败回滚。运行后端由 ToolGen
``binding.json`` 对应的 ``runtime.json`` 决定。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from harness.resources import ResourceCatalog
from harness.execution import call_environment_tool
from harness.layout import create_task_run_layout

from .schema import Draft202012Validator

from .environment import CompleteEnvironmentPackage


MAX_INLINE_STATE_BYTES = 128 * 1024


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_native(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 不是严格 JSON-native 数据：{error}") from error


def _snapshot_files(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return files
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        item: dict[str, Any] = {
            "sha256": _sha256_bytes(content),
            "size": len(content),
        }
        if len(content) <= MAX_INLINE_STATE_BYTES:
            try:
                item["json"] = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        files[relative] = item
    return files


def snapshot_workspace(root: Path) -> dict[str, Any]:
    """保留旧 Runtime/ToolGen 使用的文件树快照接口。"""
    return {"format": "files", "files": _snapshot_files(root)}


def _decode_sqlite_value(value: Any, field: dict[str, Any]) -> Any:
    if value is None:
        return None
    field_type = field.get("type")
    if field_type == "boolean":
        return bool(value)
    if field_type in {"object", "array"}:
        return json.loads(value)
    return value


def _record_identity(row: dict[str, Any], key_fields: list[str], ordinal: int) -> str:
    if key_fields:
        return _canonical_json([row.get(field) for field in key_fields])
    return f"row:{ordinal:08d}:{_sha256_bytes(_canonical_json(row).encode('utf-8'))}"


def _snapshot_record_sets(
    database: Path,
    record_sets: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not record_sets:
        return result
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for declaration in record_sets:
            record_set_id = str(declaration["record_set_id"])
            field_definitions = declaration["fields"]
            columns = list(field_definitions)
            quoted_table = '"' + record_set_id.replace('"', '""') + '"'
            rows: list[dict[str, Any]] = []
            for raw in connection.execute(f"SELECT * FROM {quoted_table}"):
                rows.append({
                    name: _decode_sqlite_value(raw[name], field_definitions[name])
                    for name in columns
                })
            key_fields = [str(item) for item in declaration.get("key_fields", [])]
            if key_fields:
                rows.sort(key=lambda row: _canonical_json([row.get(key) for key in key_fields]))
            else:
                rows.sort(key=_canonical_json)
            record_hashes = {
                _record_identity(row, key_fields, index): _sha256_bytes(
                    _canonical_json(row).encode("utf-8")
                )
                for index, row in enumerate(rows)
            }
            canonical = _canonical_json(rows).encode("utf-8")
            item: dict[str, Any] = {
                "row_count": len(rows),
                "sha256": _sha256_bytes(canonical),
                "key_fields": key_fields,
                "record_hashes": record_hashes,
            }
            if len(canonical) <= MAX_INLINE_STATE_BYTES:
                item["records"] = rows
            result[record_set_id] = item
    finally:
        connection.close()
    return result


def _snapshot_sqlite_metadata(database: Path) -> dict[str, Any]:
    """捕获 SQLite 中声明记录之外的结构事实，防止未声明表逃过 diff。"""
    if not database.is_file():
        return {"sha256": None, "objects": [], "user_version": None, "application_id": None}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        objects = [
            {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    finally:
        connection.close()
    payload = {
        "objects": objects,
        "user_version": user_version,
        "application_id": application_id,
    }
    return {"sha256": _sha256_bytes(_canonical_json(payload).encode("utf-8")), **payload}


def _sqlite_state_signature(database: Path) -> tuple[tuple[str, bool, int, int], ...]:
    """Return the filesystem facts that can change SQLite's visible state."""
    paths = (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    )
    signature: list[tuple[str, bool, int, int]] = []
    for path in paths:
        try:
            status = path.stat()
        except FileNotFoundError:
            signature.append((str(path), False, 0, 0))
        else:
            signature.append((str(path), True, status.st_size, status.st_mtime_ns))
    return tuple(signature)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_content_signature(database: Path) -> tuple[tuple[str, bool, int, str], ...]:
    """Identify SQLite content across Harness transaction copies."""
    paths = (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    )
    signature: list[tuple[str, bool, int, str]] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            signature.append((path.name, False, 0, ""))
        else:
            signature.append((path.name, True, size, _sha256_file(path)))
    return tuple(signature)


def _snapshot_database_state_by_content(
    database: Path,
    record_sets_json: str,
    content_signature: tuple[tuple[str, bool, int, str], ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    key = (record_sets_json, content_signature)
    with _DATABASE_CONTENT_CACHE_LOCK:
        cached = _DATABASE_CONTENT_CACHE.get(key)
        if cached is not None:
            _DATABASE_CONTENT_CACHE.move_to_end(key)
            return cached
    record_sets = json.loads(record_sets_json)
    result = (
        _snapshot_record_sets(database, record_sets),
        _snapshot_sqlite_metadata(database),
    )
    with _DATABASE_CONTENT_CACHE_LOCK:
        _DATABASE_CONTENT_CACHE[key] = result
        _DATABASE_CONTENT_CACHE.move_to_end(key)
        while len(_DATABASE_CONTENT_CACHE) > 4:
            _DATABASE_CONTENT_CACHE.popitem(last=False)
    return result


_DATABASE_CONTENT_CACHE: OrderedDict[
    tuple[str, tuple[tuple[str, bool, int, str], ...]],
    tuple[dict[str, dict[str, Any]], dict[str, Any]],
] = OrderedDict()
_DATABASE_CONTENT_CACHE_LOCK = threading.RLock()


def _clear_snapshot_database_cache() -> None:
    _snapshot_database_state_cached.cache_clear()
    with _DATABASE_CONTENT_CACHE_LOCK:
        _DATABASE_CONTENT_CACHE.clear()


@lru_cache(maxsize=2)
def _snapshot_database_state_cached(
    database_path: str,
    record_sets_json: str,
    _state_signature: tuple[tuple[str, bool, int, int], ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Cache file checks, then reuse logical state across transaction copies."""
    database = Path(database_path)
    content_signature = _sqlite_content_signature(database)
    # ``database_path`` cannot be part of the content-cache key: Harness
    # intentionally gives every tool call a fresh transaction directory.
    # The content digest and record-set declaration are the stable identity.
    return _snapshot_database_state_by_content(
        database,
        record_sets_json,
        content_signature,
    )


def _snapshot_database_state(
    database: Path,
    record_sets: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    cached = _snapshot_database_state_cached(
        str(database.resolve()),
        _canonical_json(record_sets),
        _sqlite_state_signature(database),
    )
    # Callers may compact or otherwise transform snapshots. Never expose the
    # cache-owned objects directly.
    return deepcopy(cached)


def snapshot_state(
    root: Path,
    environment: dict[str, Any] | None = None,
    *,
    package_format: str | None = None,
) -> dict[str, Any]:
    """生成可稳定比较的逻辑状态快照。

    v2 SQLite 按规范 JSON 行内容计算摘要，而不直接比较数据库文件字节，
    避免同一逻辑状态因 SQLite 页布局不同被误判为不确定。
    """
    if package_format != "v2":
        return snapshot_workspace(root)
    environment = environment or {}
    database = root / "records.sqlite"
    record_sets = [
        item for item in environment.get("record_sets", []) if isinstance(item, dict)
    ]
    database_record_sets, database_metadata = _snapshot_database_state(
        database,
        record_sets,
    )
    scopes: dict[str, Any] = {}
    scopes_root = root / "filesystem_scopes"
    scope_ids: set[str] = set()
    for scope in environment.get("filesystem_scopes", []):
        scope_id = str(scope["scope_id"])
        scope_ids.add(scope_id)
        scopes[scope_id] = {"files": _snapshot_files(scopes_root / scope_id)}
    undeclared_files: dict[str, Any] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"records.sqlite", "records.sqlite-wal", "records.sqlite-shm"}:
            continue
        parts = Path(relative).parts
        if len(parts) >= 3 and parts[0] == "filesystem_scopes" and parts[1] in scope_ids:
            continue
        content = path.read_bytes()
        undeclared_files[relative] = {"sha256": _sha256_bytes(content), "size": len(content)}
    return {
        "format": "state-v2",
        "record_sets": database_record_sets,
        "database_metadata": database_metadata,
        "filesystem_scopes": scopes,
        "undeclared_files": undeclared_files,
    }


def _file_diff(
    before_files: dict[str, Any],
    after_files: dict[str, Any],
) -> dict[str, Any]:
    before_names = set(before_files)
    after_names = set(after_files)
    modified = sorted(
        name
        for name in before_names & after_names
        if before_files[name].get("sha256") != after_files[name].get("sha256")
    )
    changed = sorted((after_names - before_names) | (before_names - after_names) | set(modified))
    return {
        "created": sorted(after_names - before_names),
        "modified": modified,
        "deleted": sorted(before_names - after_names),
        "changes": {
            name: {
                "before": deepcopy(before_files.get(name)),
                "after": deepcopy(after_files.get(name)),
            }
            for name in changed
        },
    }


def _record_set_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_hashes = before.get("record_hashes", {})
    after_hashes = after.get("record_hashes", {})
    before_keys = set(before_hashes)
    after_keys = set(after_hashes)
    return {
        "before_count": int(before.get("row_count", 0)),
        "after_count": int(after.get("row_count", 0)),
        "inserted": sorted(after_keys - before_keys),
        "updated": sorted(
            key for key in before_keys & after_keys if before_hashes[key] != after_hashes[key]
        ),
        "deleted": sorted(before_keys - after_keys),
        "before_sha256": before.get("sha256"),
        "after_sha256": after.get("sha256"),
    }


def workspace_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """比较 v1 文件快照或 v2 逻辑状态快照。"""
    if before.get("format") != "state-v2" or after.get("format") != "state-v2":
        return _file_diff(before.get("files", {}), after.get("files", {}))

    record_changes: dict[str, Any] = {}
    before_sets = before.get("record_sets", {})
    after_sets = after.get("record_sets", {})
    for record_set_id in sorted(set(before_sets) | set(after_sets)):
        change = _record_set_diff(
            before_sets.get(record_set_id, {}),
            after_sets.get(record_set_id, {}),
        )
        if change["inserted"] or change["updated"] or change["deleted"]:
            record_changes[record_set_id] = change

    scope_changes: dict[str, Any] = {}
    before_scopes = before.get("filesystem_scopes", {})
    after_scopes = after.get("filesystem_scopes", {})
    for scope_id in sorted(set(before_scopes) | set(after_scopes)):
        change = _file_diff(
            before_scopes.get(scope_id, {}).get("files", {}),
            after_scopes.get(scope_id, {}).get("files", {}),
        )
        if change["created"] or change["modified"] or change["deleted"]:
            scope_changes[scope_id] = change
    undeclared_change = _file_diff(
        before.get("undeclared_files", {}),
        after.get("undeclared_files", {}),
    )
    database_schema_changed = (
        before.get("database_metadata", {}).get("sha256")
        != after.get("database_metadata", {}).get("sha256")
    )
    changed_assets = [*record_changes, *scope_changes]
    if database_schema_changed:
        changed_assets.append("__database_schema__")
    if undeclared_change["created"] or undeclared_change["modified"] or undeclared_change["deleted"]:
        changed_assets.append("__undeclared_state__")
    return {
        "record_sets": record_changes,
        "filesystem_scopes": scope_changes,
        "database_schema_changed": database_schema_changed,
        "undeclared_files": undeclared_change,
        "changed_assets": sorted(changed_assets),
    }


def compact_state_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """删除只为 diff 服务的逐记录哈希和小表全量内联数据。"""
    if snapshot.get("format") != "state-v2":
        return deepcopy(snapshot)
    compact = deepcopy(snapshot)
    for item in compact.get("record_sets", {}).values():
        if isinstance(item, dict):
            item.pop("record_hashes", None)
            item.pop("records", None)
    metadata = compact.get("database_metadata")
    if isinstance(metadata, dict):
        metadata.pop("objects", None)
    return compact


def _has_changes(change: dict[str, Any]) -> bool:
    if "changed_assets" in change:
        return bool(change["changed_assets"])
    return any(change.get(field) for field in ("created", "modified", "deleted"))


@dataclass(frozen=True)
class ToolContext:
    """工具 ``run(arguments, context)`` 可使用的受控路径。"""

    workspace_root: Path
    state_root: Path
    records_path: Path
    filesystem_scopes_root: Path
    environment: dict[str, Any]
    records: Any
    software_root: Path

    def scope_root(self, scope_id: str) -> Path:
        if not isinstance(scope_id, str) or not scope_id or "/" in scope_id or "\\" in scope_id:
            raise ValueError("非法 scope_id")
        path = (self.filesystem_scopes_root / scope_id).resolve()
        if path.parent != self.filesystem_scopes_root.resolve():
            raise ValueError("scope_id 越界")
        return path


@dataclass
class ToolCallRecord:
    index: int
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    state_diff: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "arguments": deepcopy(self.arguments),
            "result": deepcopy(self.result),
            "state_diff": deepcopy(self.state_diff),
        }


def execute_profile_tool(
    *,
    profile_python: Path,
    software_root: Path,
    source: str,
    tool_name: str,
    arguments: dict[str, Any],
    state_root: Path,
    environment_contract: dict[str, Any],
    timeout_seconds: int = 300,
) -> Any:
    """Run one tool in the bound Profile Python against the supplied state copy."""
    if not profile_python.is_file() or not os.access(profile_python, os.X_OK):
        raise RuntimeError(f"ToolGen Profile Python 不可执行：{profile_python}")
    with tempfile.TemporaryDirectory(prefix="agent-world-profile-call-") as temporary:
        root = Path(temporary)
        request = root / "request.json"
        response = root / "response.json"
        request.write_text(json.dumps({
            "source": source,
            "tool_name": tool_name,
            "arguments": arguments,
            "state_root": str(state_root),
            "environment": environment_contract,
            "software_root": str(software_root),
        }, ensure_ascii=False), encoding="utf-8")
        environment = os.environ.copy()
        # Relocatable ToolGen profiles may retain an OpenSSL default path from
        # their original build root. Give the worker a valid host CA bundle
        # when the caller did not already provide one.
        ssl_file = environment.get("SSL_CERT_FILE")
        system_ssl_file = Path("/etc/ssl/certs/ca-certificates.crt")
        if (not ssl_file or not Path(ssl_file).is_file()) and system_ssl_file.is_file():
            environment["SSL_CERT_FILE"] = str(system_ssl_file)
        ssl_dir = environment.get("SSL_CERT_DIR")
        system_ssl_dir = Path("/etc/ssl/certs")
        if (not ssl_dir or not Path(ssl_dir).is_dir()) and system_ssl_dir.is_dir():
            environment["SSL_CERT_DIR"] = str(system_ssl_dir)
        project_root = str(Path(__file__).resolve().parents[3])
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            project_root
            if not current_pythonpath
            else project_root + os.pathsep + current_pythonpath
        )
        node_modules = software_root / "node" / "node_modules"
        if node_modules.is_dir():
            environment["NODE_PATH"] = str(node_modules)
        completed = subprocess.run(
            [
                str(profile_python),
                "-m",
                "task_gen.program.utils.profile_tool_worker",
                str(request),
                str(response),
            ],
            cwd=state_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not response.is_file():
            detail = (completed.stderr or completed.stdout or "no worker output")[-4000:]
            raise RuntimeError(
                f"工具 {tool_name} 的 Profile 工作进程失败({completed.returncode})：{detail}"
            )
        payload = json.loads(response.read_text(encoding="utf-8"))
        if payload.get("ok") is not True:
            raise RuntimeError(
                f"工具 {tool_name} 在 Profile 中执行失败：{payload.get('error')}"
            )
        return payload.get("result")


def _docker_call_tool(
    package: CompleteEnvironmentPackage,
    code: str,
    arguments: dict[str, Any],
    state_root: Path,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    environment: dict[str, Any] | None = None,
    software_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one Harness sandbox call inside the delivery's Docker image."""
    from harness.delivery import load_delivery
    from env_gen.tool_gen.runtime_launch import docker_stdio_launch

    metadata = package.delivery or {}
    binding = metadata.get("binding_path")
    if not isinstance(binding, str) or not binding:
        return {
            "kind": "runtime_error",
            "result": None,
            "error": "Docker 环境缺少原始 binding_path，无法启动交付镜像",
        }
    delivery = load_delivery(Path(binding))
    if delivery.runtime.get("backend") != "docker":
        return {
            "kind": "runtime_error",
            "result": None,
            "error": "冻结环境声明为 Docker，但原始 binding 未选择 Docker 后端",
        }
    with tempfile.TemporaryDirectory(prefix="agent-world-docker-call-") as temporary:
        root = Path(temporary)
        request = root / "request.json"
        response = root / "response.json"
        request_software_root = (
            "/opt/tool-software"
            if os.environ.get("AGENT_WORLD_DOCKER_COMPAT_PATCH") == "1"
            else (str(software_root) if software_root is not None else None)
        )
        request.write_text(json.dumps({
            "code": code,
            "arguments": arguments,
            "timeout": timeout,
            "memory_limit": memory_limit,
            "write_limit": write_limit,
            "environment": environment or {},
            "software_root": request_software_root,
            "compat_direct": os.environ.get("AGENT_WORLD_DOCKER_COMPAT_PATCH") == "1",
        }, ensure_ascii=False), encoding="utf-8")
        launch_delivery = delivery
        if os.environ.get("AGENT_WORLD_DOCKER_COMPAT_PATCH") == "1":
            # Older Docker receipts contain host paths from a removed delivery
            # batch. The image keeps its runtime under the command `python`,
            # while the current source tree is mounted at /opt/agent-world.
            from dataclasses import replace

            runtime = dict(delivery.runtime)
            runtime["python_command"] = "python"
            runtime["software_root"] = "/opt/tool-software"
            launch_delivery = replace(delivery, runtime=runtime)
        launch = docker_stdio_launch(
            launch_delivery,
            server_path=Path(__file__),
            project_root=Path(__file__).resolve().parents[3],
            arguments=[
                "-m",
                "task_gen.program.utils.harness_docker_worker",
                str(request),
                str(response),
                str(state_root),
            ],
            writable_paths=(request, response, state_root),
        )
        if os.environ.get("AGENT_WORLD_DOCKER_COMPAT_PATCH") == "1":
            launch_args = list(launch.arguments)
            image = str(launch_delivery.runtime["image"])
            image_index = launch_args.index(image)
            launch_args[image_index:image_index] = [
                "--env",
                "PYTHONPATH=/opt/agent-world",
            ]
            launch = type(launch)(
                command=launch.command,
                arguments=tuple(launch_args),
                environment=launch.environment,
            )
        completed = subprocess.run(
            [str(launch.command), *launch.arguments],
            cwd=state_root,
            env={**os.environ, **launch.environment},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout + 60,
            check=False,
        )
        if completed.returncode != 0 or not response.is_file():
            detail = (completed.stderr or completed.stdout or "no Docker worker output")[-4000:]
            return {
                "kind": "runtime_error",
                "result": None,
                "error": f"Docker 工具进程失败({completed.returncode})：{detail}",
            }
        payload = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {
                "kind": "runtime_error",
                "result": None,
                "error": "Docker 工具进程返回的不是 object",
            }
        return payload


def _program_v2_sandbox_call_tool(
    code: str,
    arguments: dict[str, Any],
    state_root: Path,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    environment: dict[str, Any] | None = None,
    software_root: Path | None = None,
    *,
    process_limit: int = 1024,
    software: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one v2 sandbox inside ``call_environment_tool``'s transaction.

    ``call_environment_tool`` already copies state and commits it atomically.
    Calling Harness ``call_tool`` here would create a second transaction and
    scan large SQLite tables twice more. This adapter retains the sandbox and
    performs the same writable-resource check before the outer transaction can
    commit.
    """
    from harness.sandbox import run_tool_in_sandbox

    environment = environment or {}
    try:
        before = snapshot_state(state_root, environment, package_format="v2")
        outcome = run_tool_in_sandbox(
            code,
            arguments,
            state_root,
            timeout,
            memory_limit,
            write_limit,
            environment,
            software_root,
            process_limit=process_limit,
            software=software,
        )
        # Some delivered Python profiles use an absolute symlink whose target
        # is outside the profile tree. The host resolves it, but bwrap may not
        # expose the symlink alias inside its mount namespace. Keep this
        # authoring fallback opt-in; final evaluation must use the published
        # sandbox runtime.
        if (
            outcome.get("error")
            and os.environ.get("AGENT_WORLD_ALLOW_UNSANDBOXED_PROFILE_FALLBACK") == "1"
            and software
            and "bwrap" in str(outcome.get("error"))
            and "execvp" in str(outcome.get("error"))
        ):
            fallback_python = Path(str(software["python"]))
            fallback_root = Path(str(software["root"]))
            result = execute_profile_tool(
                profile_python=fallback_python,
                software_root=fallback_root,
                source=code,
                tool_name="authoring_profile_fallback",
                arguments=arguments,
                state_root=state_root,
                environment_contract=environment,
                timeout_seconds=timeout,
            )
            outcome = {"kind": None, "result": result, "error": None}
        result = outcome.get("result")
        if outcome.get("error") or not isinstance(result, dict) or result.get("success") is not True:
            return outcome
        after = snapshot_state(state_root, environment, package_format="v2")
        change = workspace_diff(before, after)
        writable = {
            str(item["record_set_id"])
            for item in environment.get("record_sets", [])
            if item.get("access") == "copy_on_write"
        }
        writable.update({
            str(item["scope_id"])
            for item in environment.get("filesystem_scopes", [])
            if item.get("access") == "copy_on_write"
        })
        forbidden = set(change.get("changed_assets", [])) - writable
        if forbidden:
            raise PermissionError(
                "工具修改了只读或未声明状态：" + ", ".join(sorted(forbidden))
            )
        return outcome
    except Exception as error:
        return {
            "kind": "exception",
            "result": None,
            "error": f"{type(error).__name__}: {error}",
        }


def _with_harness_context_compatibility(tool: dict[str, Any]) -> dict[str, Any]:
    """Adapt pre-Harness context attributes without bypassing the Harness sandbox."""
    adapted = deepcopy(tool)
    internal = adapted.get("internal")
    if not isinstance(internal, dict) or not isinstance(internal.get("code"), str):
        return adapted
    internal["code"] = internal["code"] + r'''

_agent_world_original_run = run
def run(arguments, context):
    if hasattr(context, "state_root"):
        if not hasattr(context, "workspace_root"):
            context.workspace_root = context.state_root
        if not hasattr(context, "records_path"):
            context.records_path = context.state_root / "records.sqlite"
        if not hasattr(context, "filesystem_scopes_root"):
            context.filesystem_scopes_root = context.state_root / "filesystem_scopes"
    return _agent_world_original_run(arguments, context)
'''
    return adapted


class CompleteEnvironmentRuntime:
    """在一份独立状态副本上执行完整环境的工具。"""

    def __init__(self, package: CompleteEnvironmentPackage):
        self.package = package
        self._temporary = tempfile.TemporaryDirectory(prefix="agent-world-program-runtime-")
        self.layout = create_task_run_layout(
            package.state_root,
            Path(self._temporary.name) / "task-run",
        )
        self.state_root = self.layout.execution_state
        # 模型工作区与工具执行状态严格分离；业务工具只接触 state_root。
        self.workspace_root = self.layout.model_workspace
        self.records_path = self.state_root / "records.sqlite"
        self.filesystem_scopes_root = self.state_root / "filesystem_scopes"
        self.software_root = package.software_root or package.package_root
        self.resources = ResourceCatalog(
            package.environment,
            lambda scope_id: self.filesystem_scopes_root / scope_id,
        )
        self._tools = {
            str(tool["name"]): _with_harness_context_compatibility(tool)
            for tool in package.tools
        }
        self.trace: list[ToolCallRecord] = []

    @staticmethod
    def _compile_handler(name: str, source: str) -> Callable[[dict[str, Any], Any], Any]:
        try:
            code = compile(source, f"<tool:{name}>", "exec")
            namespace: dict[str, Any] = {}
            exec(code, namespace, namespace)
        except Exception as error:
            raise ValueError(f"工具 {name} 无法编译：{type(error).__name__}: {error}") from error
        handler = namespace.get("run")
        if not callable(handler):
            raise ValueError(f"工具 {name} 的 internal.code 没有定义可调用的 run")
        return handler

    @staticmethod
    def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
        return _schema_validation_errors(schema, value)

    def _restore(self, backup: Path) -> None:
        shutil.rmtree(self.state_root)
        shutil.copytree(backup, self.state_root)

    def _context(self) -> ToolContext:
        from env_gen.tool_gen.runtime import RecordStore

        return ToolContext(
            workspace_root=self.workspace_root,
            state_root=self.state_root,
            records_path=self.records_path,
            filesystem_scopes_root=self.filesystem_scopes_root,
            environment=deepcopy(self.package.environment),
            records=RecordStore(self.records_path, self.package.environment),
            software_root=self.software_root,
        )

    def _run_in_profile(
        self,
        name: str,
        source: str,
        arguments: dict[str, Any],
    ) -> Any:
        python = self.package.profile_python
        if python is None:
            return self._handlers[name](deepcopy(arguments), self._context())
        return execute_profile_tool(
            profile_python=python,
            software_root=self.software_root,
            source=source,
            tool_name=name,
            arguments=arguments,
            state_root=self.state_root,
            environment_contract=self.package.environment,
        )

    def _read_only_changes(self, change: dict[str, Any]) -> list[str]:
        if not _has_changes(change):
            return []
        if self.package.package_format == "v2":
            access = {
                str(item["record_set_id"]): str(item["access"])
                for item in self.package.environment.get("record_sets", [])
            }
            access.update({
                str(item["scope_id"]): str(item["access"])
                for item in self.package.environment.get("filesystem_scopes", [])
            })
            return [
                asset for asset in change.get("changed_assets", [])
                if access.get(asset) != "copy_on_write"
            ]

        resources = self.package.environment.get("resources", [])
        changed_paths = [
            *change.get("created", []),
            *change.get("modified", []),
            *change.get("deleted", []),
        ]
        rejected: list[str] = []
        for path in changed_paths:
            if not any(
                item.get("writable") is True and _path_matches_v1_resource(path, item)
                for item in resources if isinstance(item, dict)
            ):
                rejected.append(path)
        return rejected

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        arguments = _json_native(arguments, label=f"工具 {name} 的 arguments")
        if not isinstance(arguments, dict):
            raise TypeError("工具 arguments 必须是 object")
        before = self.snapshot()
        backend = str((self.package.runtime or {}).get("backend") or (
            "python_profile" if self.package.profile_python is not None else "host_python"
        ))
        software: dict[str, str] | None = None
        # Legacy Program tools always received a usable software_root, even
        # when the environment was loaded directly instead of through a
        # ToolGen binding.  Preserve that contract when Harness falls back to
        # host_python; otherwise older tools fail before their business logic
        # runs because Context has no software_root attribute.
        software_root: Path | None = self.software_root
        call_tool_fn: Callable[..., dict[str, Any]] | None = None
        if backend == "python_profile":
            if self.package.profile_python is None or self.package.software_root is None:
                raise RuntimeError("python_profile 后端缺少 Python 或 software root")
            software = {
                "root": str(self.package.software_root),
                "python": str(self.package.profile_python),
            }
        elif backend == "docker":
            runtime_root = (self.package.runtime or {}).get("software_root", "/opt/tool-software")
            software_root = Path(str(runtime_root))
            call_tool_fn = lambda *args, **kwargs: _docker_call_tool(
                self.package, *args, **kwargs
            )
        elif backend != "host_python":
            raise RuntimeError(f"不支持的 ToolGen runtime backend：{backend}")
        if self.package.package_format == "v2" and backend in {"host_python", "python_profile"}:
            call_tool_fn = _program_v2_sandbox_call_tool

        record = call_environment_tool(
            name,
            arguments,
            self._tools,
            self.state_root,
            timeout=300,
            memory_limit=2 * 1024 * 1024 * 1024,
            write_limit=256 * 1024 * 1024,
            process_limit=1024,
            call_tool_fn=call_tool_fn,
            environment=self.package.environment,
            software=software,
            software_root=software_root,
        )
        result = record.get("result")
        is_business_failure = (
            isinstance(result, dict)
            and result.get("success") is False
            and not record.get("failure_kind")
        )
        if record.get("error") and not is_business_failure:
            raise RuntimeError(
                f"工具 {name} 执行失败"
                f"[{record.get('failure_kind') or 'validation'}]：{record['error']}"
            )
        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            raise RuntimeError(f"工具 {name} 未返回统一 success envelope")
        arguments = deepcopy(record.get("arguments", arguments))
        after = self.snapshot()
        change = workspace_diff(before, after)

        record = ToolCallRecord(
            index=len(self.trace) + 1,
            tool=name,
            arguments=arguments,
            result=result,
            state_diff=change,
        )
        self.trace.append(record)
        return deepcopy(result)

    def snapshot(self) -> dict[str, Any]:
        return snapshot_state(
            self.state_root,
            self.package.environment,
            package_format=self.package.package_format,
        )

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "CompleteEnvironmentRuntime":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _schema_validation_errors(schema: dict[str, Any], value: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


def _path_matches_v1_resource(path: str, resource: dict[str, Any]) -> bool:
    import fnmatch

    declared = str(resource.get("path") or "").replace("\\", "/").rstrip("/")
    if resource.get("storage_type") == "directory":
        return path == declared or path.startswith(declared + "/")
    return bool(declared and fnmatch.fnmatchcase(path, declared))
