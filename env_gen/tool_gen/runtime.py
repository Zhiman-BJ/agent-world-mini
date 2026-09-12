from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
import tempfile
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from jsonschema import Draft202012Validator

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_native(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 不是严格 JSON 数据：{error}") from error


def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.path),
        )
    ]


def _table_digest(database: Path, table: str) -> str:
    quoted = _quote(table)
    with closing(sqlite3.connect(database)) as connection:
        columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")]
        rows = [list(row) for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")]
    payload = json.dumps(
        {"columns": columns, "rows": rows}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_v2_package(package_root: Path, environment: dict[str, Any]) -> None:
    schema = json.loads((SCHEMA_ROOT / "environment.schema.json").read_text(encoding="utf-8"))
    errors = _schema_errors(schema, environment)
    if errors:
        raise ValueError("environment.json 不符合 v2 Schema：" + " | ".join(errors[:12]))
    state = package_root / "state"
    record_sets = environment.get("record_sets", [])
    database = state / "records.sqlite"
    if record_sets and not database.is_file():
        raise ValueError("Record Set 非空但 state/records.sqlite 不存在")
    if record_sets:
        with closing(sqlite3.connect(database)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        declared = {str(item["record_set_id"]) for item in record_sets}
        if tables != declared:
            raise ValueError(f"SQLite 表与 Record Set 不一致：实际={sorted(tables)} 声明={sorted(declared)}")
    scopes_root = state / "filesystem_scopes"
    for scope in environment.get("filesystem_scopes", []):
        scope_root = scopes_root / str(scope["scope_id"])
        if not scope_root.is_dir():
            raise ValueError(f"Filesystem Scope 不存在：{scope['scope_id']}")


def _decode_record(row: sqlite3.Row, fields: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for name, definition in fields.items():
        value = result.get(name)
        if value is not None and definition.get("type") in {"object", "array"}:
            result[name] = json.loads(value)
        elif value is not None and definition.get("type") == "boolean":
            result[name] = bool(value)
    return result


def _encode_value(value: Any, definition: dict[str, Any]) -> Any:
    if value is None:
        return None
    if definition.get("type") in {"object", "array"}:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if definition.get("type") == "boolean":
        return int(bool(value))
    return value


@dataclass(frozen=True)
class ToolPackage:
    package_root: Path
    environment: dict[str, Any]
    tools: tuple[dict[str, Any], ...]

    @classmethod
    def load(
        cls,
        package_root: Path,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> "ToolPackage":
        package_root = package_root.resolve()
        environment_path = package_root / "environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        _validate_v2_package(package_root, environment)

        if tools is None:
            tools_path = package_root / "tools.json"
            document = json.loads(tools_path.read_text(encoding="utf-8"))
            tools = document.get("tools") if isinstance(document, dict) else None
        if not isinstance(tools, list) or not tools:
            raise ValueError("tools.json 的 tools 必须是非空数组")

        tool_schema = json.loads(
            (SCHEMA_ROOT / "validation/tool.schema.json").read_text(encoding="utf-8")
        )
        names: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise ValueError(f"tools[{index}] 必须是 object")
            errors = _schema_errors(tool_schema, tool)
            if errors:
                raise ValueError(f"工具 {tool.get('name', index)} 不符合 Schema：{' | '.join(errors)}")
            name = str(tool["name"])
            if name in names:
                raise ValueError(f"工具名重复：{name}")
            names.add(name)
            normalized.append(deepcopy(tool))
        return cls(package_root, environment, tuple(normalized))


class RecordStore:
    def __init__(self, database: Path, environment: dict[str, Any]) -> None:
        self.database = database
        self.definitions = {
            str(item["record_set_id"]): item
            for item in environment.get("record_sets", [])
        }

    def _definition(self, record_set_id: str) -> dict[str, Any]:
        try:
            return self.definitions[record_set_id]
        except KeyError as error:
            raise ValueError(f"未知 Record Set：{record_set_id}") from error

    def _where(
        self,
        definition: dict[str, Any],
        filters: dict[str, Any],
    ) -> tuple[str, list[Any]]:
        fields = definition["fields"]
        unknown = sorted(set(filters) - set(fields))
        if unknown:
            raise ValueError(f"未知字段：{', '.join(unknown)}")
        clauses: list[str] = []
        values: list[Any] = []
        for name, value in filters.items():
            clauses.append(f"{_quote(name)} IS NULL" if value is None else f"{_quote(name)} = ?")
            if value is not None:
                values.append(_encode_value(value, fields[name]))
        return (" AND ".join(clauses) or "1 = 1"), values

    def list(
        self,
        record_set_id: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        definition = self._definition(record_set_id)
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit 必须在 1..1000，offset 不能小于 0")
        fields = definition["fields"]
        if order_by is not None and order_by not in fields:
            raise ValueError(f"未知排序字段：{order_by}")
        where, values = self._where(definition, filters or {})
        order = f" ORDER BY {_quote(order_by)} {'DESC' if descending else 'ASC'}" if order_by else ""
        sql = f"SELECT * FROM {_quote(record_set_id)} WHERE {where}{order} LIMIT ? OFFSET ?"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, [*values, limit, offset]).fetchall()
        return [_decode_record(row, fields) for row in rows]

    def get(self, record_set_id: str, key: dict[str, Any]) -> dict[str, Any] | None:
        definition = self._definition(record_set_id)
        if not key or set(key) != set(definition.get("key_fields", [])):
            raise ValueError(f"key 必须恰好包含：{definition.get('key_fields', [])}")
        rows = self.list(record_set_id, filters=key, limit=2)
        return rows[0] if rows else None

    def create(self, record_set_id: str, record: dict[str, Any]) -> None:
        definition = self._definition(record_set_id)
        fields = definition["fields"]
        if set(record) != set(fields):
            raise ValueError("record 必须包含且只包含声明字段")
        columns = list(fields)
        values = [_encode_value(record[name], fields[name]) for name in columns]
        sql = (
            f"INSERT INTO {_quote(record_set_id)} "
            f"({', '.join(_quote(name) for name in columns)}) VALUES "
            f"({', '.join('?' for _ in columns)})"
        )
        with closing(sqlite3.connect(self.database)) as connection:
            try:
                connection.execute(sql, values)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def update(
        self,
        record_set_id: str,
        key: dict[str, Any],
        changes: dict[str, Any],
    ) -> int:
        definition = self._definition(record_set_id)
        fields = definition["fields"]
        if not changes:
            raise ValueError("changes 不能为空")
        unknown = sorted(set(changes) - set(fields))
        if unknown:
            raise ValueError(f"未知字段：{', '.join(unknown)}")
        where, where_values = self._where(definition, key)
        assignments = ", ".join(f"{_quote(name)} = ?" for name in changes)
        values = [_encode_value(value, fields[name]) for name, value in changes.items()]
        with closing(sqlite3.connect(self.database)) as connection:
            try:
                cursor = connection.execute(
                    f"UPDATE {_quote(record_set_id)} SET {assignments} WHERE {where}",
                    [*values, *where_values],
                )
                connection.commit()
                return cursor.rowcount
            except Exception:
                connection.rollback()
                raise

    def delete(self, record_set_id: str, key: dict[str, Any]) -> int:
        definition = self._definition(record_set_id)
        where, values = self._where(definition, key)
        with closing(sqlite3.connect(self.database)) as connection:
            try:
                cursor = connection.execute(
                    f"DELETE FROM {_quote(record_set_id)} WHERE {where}", values
                )
                connection.commit()
                return cursor.rowcount
            except Exception:
                connection.rollback()
                raise


def snapshot_state(root: Path, environment: dict[str, Any]) -> dict[str, Any]:
    database = root / "state/records.sqlite"
    records = {
        str(item["record_set_id"]): _table_digest(database, str(item["record_set_id"]))
        for item in environment.get("record_sets", [])
    }
    scopes: dict[str, dict[str, bytes]] = {}
    for scope in environment.get("filesystem_scopes", []):
        scope_id = str(scope["scope_id"])
        scope_root = root / "state/filesystem_scopes" / scope_id
        scopes[scope_id] = {
            path.relative_to(scope_root).as_posix(): path.read_bytes()
            for path in sorted(item for item in scope_root.rglob("*") if item.is_file())
        }
    return {"record_sets": records, "filesystem_scopes": scopes}


def state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed_records = sorted(
        name
        for name in set(before["record_sets"]) | set(after["record_sets"])
        if before["record_sets"].get(name) != after["record_sets"].get(name)
    )
    changed_scopes = sorted(
        name
        for name in set(before["filesystem_scopes"]) | set(after["filesystem_scopes"])
        if before["filesystem_scopes"].get(name) != after["filesystem_scopes"].get(name)
    )
    return {"record_sets": changed_records, "filesystem_scopes": changed_scopes}


class ToolRuntime:
    def __init__(self, package: ToolPackage) -> None:
        self.package = package
        self._temporary = tempfile.TemporaryDirectory(prefix="agent-world-tool-runtime-")
        self.root = Path(self._temporary.name)
        shutil.copy2(package.package_root / "environment.json", self.root / "environment.json")
        shutil.copytree(package.package_root / "state", self.root / "state")
        self._tools = {str(tool["name"]): deepcopy(tool) for tool in package.tools}
        self._handlers = {
            name: self._compile_handler(name, tool["internal"]["code"])
            for name, tool in self._tools.items()
        }
        self.context = SimpleNamespace(
            software_root=package.package_root / "tool_generation/software",
            environment=deepcopy(package.environment),
            records=RecordStore(self.root / "state/records.sqlite", package.environment),
            scope_root=lambda scope_id: self._scope_root(str(scope_id)),
        )

    @staticmethod
    def _compile_handler(name: str, source: str) -> Callable[[dict[str, Any], Any], Any]:
        namespace: dict[str, Any] = {}
        try:
            exec(compile(source, f"<tool:{name}>", "exec"), namespace, namespace)
        except Exception as error:
            raise ValueError(f"工具 {name} 无法编译：{type(error).__name__}: {error}") from error
        handler = namespace.get("run")
        if not callable(handler):
            raise ValueError(f"工具 {name} 没有定义 run(arguments, context)")
        return handler

    def _scope_root(self, scope_id: str) -> Path:
        known = {str(item["scope_id"]) for item in self.package.environment.get("filesystem_scopes", [])}
        if scope_id not in known:
            raise ValueError(f"未知 Filesystem Scope：{scope_id}")
        return self.root / "state/filesystem_scopes" / scope_id

    def snapshot(self) -> dict[str, Any]:
        return snapshot_state(self.root, self.package.environment)

    def _restore(self, backup: Path) -> None:
        shutil.rmtree(self.root / "state")
        shutil.copytree(backup, self.root / "state")
        self.context.records = RecordStore(
            self.root / "state/records.sqlite", self.package.environment
        )

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        tool = self._tools[name]
        arguments = _json_native(arguments, label=f"工具 {name} 的 arguments")
        input_errors = _schema_errors(tool["inputSchema"], arguments)
        if input_errors:
            raise ValueError(f"工具 {name} 输入不符合 Schema：{' | '.join(input_errors)}")

        before = self.snapshot()
        with tempfile.TemporaryDirectory(prefix="agent-world-tool-backup-") as temporary:
            backup = Path(temporary) / "state"
            shutil.copytree(self.root / "state", backup)
            try:
                result = _json_native(
                    self._handlers[name](deepcopy(arguments), self.context),
                    label=f"工具 {name} 的返回值",
                )
                output_errors = _schema_errors(tool["outputSchema"], result)
                if output_errors:
                    raise ValueError(f"工具 {name} 输出不符合 Schema：{' | '.join(output_errors)}")
                after = self.snapshot()
                change = state_diff(before, after)
                if result.get("success") is False and any(change.values()):
                    raise RuntimeError(f"工具 {name} 业务失败后仍修改了环境状态")
                access = {
                    str(item.get("record_set_id") or item.get("scope_id")): item.get("access")
                    for item in [
                        *self.package.environment.get("record_sets", []),
                        *self.package.environment.get("filesystem_scopes", []),
                    ]
                }
                changed_assets = [*change["record_sets"], *change["filesystem_scopes"]]
                read_only = [asset for asset in changed_assets if access.get(asset) != "copy_on_write"]
                if read_only:
                    raise RuntimeError(f"工具 {name} 修改了只读资源：{', '.join(read_only)}")
                if any(change.values()):
                    _validate_v2_package(self.root, self.package.environment)
            except Exception:
                self._restore(backup)
                raise
        return deepcopy(result)

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "ToolRuntime":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
