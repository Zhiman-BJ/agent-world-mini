"""File and SQLite state snapshots shared by task execution backends."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

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
