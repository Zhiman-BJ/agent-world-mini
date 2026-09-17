"""Execute one ToolGen call in a Profile Python using only the standard library."""

from __future__ import annotations

import json
import sqlite3
import sys
import traceback
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _encode(value: Any, field: dict[str, Any]) -> Any:
    if value is not None and field.get("type") in {"object", "array"}:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if value is not None and field.get("type") == "boolean":
        return int(bool(value))
    return value


def _decode(value: Any, field: dict[str, Any]) -> Any:
    if value is not None and field.get("type") in {"object", "array"}:
        return json.loads(value)
    if value is not None and field.get("type") == "boolean":
        return bool(value)
    return value


def _validate_value(value: Any, field: dict[str, Any], name: str) -> None:
    if value is None:
        if field.get("nullable") is not True:
            raise ValueError(f"{name} 不允许为 null")
        return
    expected = field.get("type")
    valid = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "boolean": type(value) is bool,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }.get(expected, True)
    if not valid:
        raise TypeError(f"{name} 不符合声明类型 {expected}")
    if "enum" in field and value not in field["enum"]:
        raise ValueError(f"{name} 不属于声明枚举")


class RecordStore:
    def __init__(self, database: Path, environment: dict[str, Any]) -> None:
        self.database = database
        self.definitions = {
            str(item["record_set_id"]): item
            for item in environment.get("record_sets", [])
        }

    def _definition(self, record_set_id: str) -> dict[str, Any]:
        if record_set_id not in self.definitions:
            raise ValueError(f"未知 Record Set：{record_set_id}")
        return self.definitions[record_set_id]

    def _values(
        self,
        definition: dict[str, Any],
        values: dict[str, Any],
        *,
        complete: bool,
    ) -> None:
        fields = definition["fields"]
        unknown = set(values) - set(fields)
        if unknown:
            raise ValueError("未知字段：" + ", ".join(sorted(unknown)))
        if complete and set(values) != set(fields):
            raise ValueError("记录必须包含且只包含声明字段")
        for name, value in values.items():
            _validate_value(value, fields[name], name)

    def _where(
        self,
        definition: dict[str, Any],
        filters: dict[str, Any],
    ) -> tuple[str, list[Any]]:
        self._values(definition, filters, complete=False)
        clauses: list[str] = []
        values: list[Any] = []
        for name, value in filters.items():
            clauses.append(
                f"{_quote(name)} IS NULL" if value is None else f"{_quote(name)} = ?"
            )
            if value is not None:
                values.append(_encode(value, definition["fields"][name]))
        return " AND ".join(clauses) or "1 = 1", values

    def list(
        self,
        record_set_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        definition = self._definition(record_set_id)
        if type(limit) is not int or not 0 <= limit <= 1000 or offset < 0:
            raise ValueError("分页参数非法")
        if order_by is not None and order_by not in definition["fields"]:
            raise ValueError(f"未知排序字段：{order_by}")
        where, values = self._where(definition, filters or {})
        order = (
            f" ORDER BY {_quote(order_by)} {'DESC' if descending else 'ASC'}"
            if order_by
            else ""
        )
        sql = (
            f"SELECT * FROM {_quote(record_set_id)} WHERE {where}{order} LIMIT ? OFFSET ?"
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, [*values, limit, offset]).fetchall()
        return [
            {
                name: _decode(row[name], field)
                for name, field in definition["fields"].items()
            }
            for row in rows
        ]

    def get(self, record_set_id: str, key: dict[str, Any]) -> dict[str, Any] | None:
        definition = self._definition(record_set_id)
        if set(key) != set(definition.get("key_fields", [])):
            raise ValueError(f"key 必须恰好包含：{definition.get('key_fields', [])}")
        rows = self.list(record_set_id, key, 2, 0)
        if len(rows) > 1:
            raise ValueError("声明的记录键不唯一")
        return rows[0] if rows else None

    def create(self, record_set_id: str, record: dict[str, Any]) -> dict[str, Any]:
        definition = self._definition(record_set_id)
        if definition.get("access") != "copy_on_write":
            raise PermissionError("Record Set 是只读的")
        self._values(definition, record, complete=True)
        columns = list(definition["fields"])
        sql = (
            f"INSERT INTO {_quote(record_set_id)} "
            f"({', '.join(_quote(name) for name in columns)}) VALUES "
            f"({', '.join('?' for _ in columns)})"
        )
        values = [_encode(record[name], definition["fields"][name]) for name in columns]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(sql, values)
            connection.commit()
        return deepcopy(record)

    def update(
        self,
        record_set_id: str,
        key: dict[str, Any],
        changes: dict[str, Any],
    ) -> int:
        definition = self._definition(record_set_id)
        if definition.get("access") != "copy_on_write":
            raise PermissionError("Record Set 是只读的")
        if not changes:
            raise ValueError("changes 不能为空")
        self._values(definition, changes, complete=False)
        where, where_values = self._where(definition, key)
        assignments = ", ".join(f"{_quote(name)} = ?" for name in changes)
        values = [
            _encode(value, definition["fields"][name])
            for name, value in changes.items()
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            cursor = connection.execute(
                f"UPDATE {_quote(record_set_id)} SET {assignments} WHERE {where}",
                [*values, *where_values],
            )
            connection.commit()
            return cursor.rowcount

    def delete(self, record_set_id: str, key: dict[str, Any]) -> int:
        definition = self._definition(record_set_id)
        if definition.get("access") != "copy_on_write":
            raise PermissionError("Record Set 是只读的")
        where, values = self._where(definition, key)
        with closing(sqlite3.connect(self.database)) as connection:
            cursor = connection.execute(
                f"DELETE FROM {_quote(record_set_id)} WHERE {where}", values
            )
            connection.commit()
            return cursor.rowcount


def _scope_root(
    state_root: Path,
    environment: dict[str, Any],
    scope_id: str,
) -> Path:
    declared = {
        str(item["scope_id"])
        for item in environment.get("filesystem_scopes", [])
    }
    if scope_id not in declared:
        raise ValueError(f"未知 Filesystem Scope：{scope_id}")
    return state_root / "filesystem_scopes" / scope_id


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: profile_tool_worker REQUEST RESPONSE")
    request_path = Path(sys.argv[1]).resolve()
    response_path = Path(sys.argv[2]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    state_root = Path(request["state_root"]).resolve()
    environment = request["environment"]
    context = SimpleNamespace(
        workspace_root=state_root,
        state_root=state_root,
        records_path=state_root / "records.sqlite",
        filesystem_scopes_root=state_root / "filesystem_scopes",
        environment=deepcopy(environment),
        records=RecordStore(state_root / "records.sqlite", environment),
        software_root=Path(request["software_root"]).resolve(),
        scope_root=lambda scope_id: _scope_root(state_root, environment, str(scope_id)),
    )
    try:
        namespace: dict[str, object] = {}
        exec(
            compile(request["source"], f"<tool:{request['tool_name']}>", "exec"),
            namespace,
            namespace,
        )
        handler = namespace.get("run")
        if not callable(handler):
            raise ValueError("internal.code 没有定义可调用的 run")
        result = handler(deepcopy(request["arguments"]), context)
        payload = {"ok": True, "result": result}
    except BaseException as error:
        payload = {
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(limit=12),
        }
    response_path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
