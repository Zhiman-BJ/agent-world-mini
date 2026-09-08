"""Program-form 参考程序的隔离环境 Runtime。

Runtime 每次从基线 ``state/``（v2）或 ``workspace/``（v1 兼容）复制一份
独立状态，校验工具输入/输出 Schema，检查只读边界，并保证
``success=false`` 时不留下任何状态变化。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

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
        "record_sets": _snapshot_record_sets(
            root / "records.sqlite",
            [item for item in environment.get("record_sets", []) if isinstance(item, dict)],
        ),
        "database_metadata": _snapshot_sqlite_metadata(root / "records.sqlite"),
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


class CompleteEnvironmentRuntime:
    """在一份独立状态副本上执行完整环境的工具。"""

    def __init__(self, package: CompleteEnvironmentPackage):
        self.package = package
        self._temporary = tempfile.TemporaryDirectory(prefix="agent-world-program-runtime-")
        self.state_root = Path(self._temporary.name) / "state"
        shutil.copytree(package.state_root, self.state_root)
        # workspace_root 作为工具契约 v1 的兼容别名；v2 工具应优先使用
        # records_path 和 filesystem_scopes_root。
        self.workspace_root = self.state_root
        self.records_path = self.state_root / "records.sqlite"
        self.filesystem_scopes_root = self.state_root / "filesystem_scopes"
        self._tools = {str(tool["name"]): deepcopy(tool) for tool in package.tools}
        self._handlers = {
            name: self._compile_handler(name, str(tool["internal"]["code"]))
            for name, tool in self._tools.items()
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
        return ToolContext(
            workspace_root=self.workspace_root,
            state_root=self.state_root,
            records_path=self.records_path,
            filesystem_scopes_root=self.filesystem_scopes_root,
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
        tool = self._tools[name]
        input_errors = self._schema_errors(tool["inputSchema"], arguments)
        if input_errors:
            raise ValueError(f"工具 {name} 输入不符合 Schema：{' | '.join(input_errors)}")

        before = self.snapshot()
        with tempfile.TemporaryDirectory(prefix="agent-world-call-backup-") as temporary:
            backup = Path(temporary) / "state"
            shutil.copytree(self.state_root, backup)
            try:
                result = self._handlers[name](deepcopy(arguments), self._context())
                result = _json_native(result, label=f"工具 {name} 的返回值")
                output_errors = self._schema_errors(tool["outputSchema"], result)
                if output_errors:
                    raise ValueError(f"工具 {name} 输出不符合 Schema：{' | '.join(output_errors)}")
                if not isinstance(result, dict) or type(result.get("success")) is not bool:
                    raise ValueError(f"工具 {name} 未返回统一 success envelope")
                after = self.snapshot()
                change = workspace_diff(before, after)
                read_only_changes = self._read_only_changes(change)
                if read_only_changes:
                    raise RuntimeError(
                        f"non_writable_state: 工具 {name} 修改了只读或未声明状态："
                        f"{', '.join(read_only_changes)}"
                    )
                if result["success"] is False and _has_changes(change):
                    raise RuntimeError(f"工具 {name} 业务失败后仍修改了状态")
            except Exception:
                self._restore(backup)
                raise

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
