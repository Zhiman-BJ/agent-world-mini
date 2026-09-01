"""把受控 Record Set 和文件材料物化为环境候选状态。"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from datetime import date, datetime, time
from urllib.parse import urlparse
from uuid import UUID

from .structured_io import StructuredDataError, read_entity_groups


_SQLITE_TYPES = {
    "string": "TEXT",
    "integer": "INTEGER",
    "number": "REAL",
    "boolean": "INTEGER",
    "object": "TEXT",
    "array": "TEXT",
}


def _matches_string_format(value: str, format_name: str) -> bool:
    try:
        if format_name == "date":
            date.fromisoformat(value)
        elif format_name == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif format_name == "time":
            time.fromisoformat(value.replace("Z", "+00:00"))
        elif format_name == "uuid":
            UUID(value)
        elif format_name == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme and (parsed.netloc or parsed.path))
        elif format_name == "email":
            return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))
        elif format_name == "hostname":
            return bool(re.fullmatch(
                r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                value,
            ))
        elif format_name in {"ipv4", "ipv6"}:
            import ipaddress
            address = ipaddress.ip_address(value)
            return (
                (format_name == "ipv4" and address.version == 4)
                or (format_name == "ipv6" and address.version == 6)
            )
        elif format_name == "duration":
            return bool(re.fullmatch(
                r"P(?=\d|T\d)(?:\d+Y)?(?:\d+M)?(?:\d+D)?"
                r"(?:T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?",
                value,
            ))
    except (ValueError, TypeError):
        return False
    return True


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _value_matches(value: Any, definition: dict[str, Any], *, path: str) -> list[str]:
    if value is None:
        return [] if definition.get("nullable") is True else [f"{path} 不允许 null"]
    field_type = definition.get("type")
    valid = (
        (field_type == "string" and isinstance(value, str))
        or (field_type == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (field_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (field_type == "boolean" and isinstance(value, bool))
        or (field_type == "object" and isinstance(value, dict))
        or (field_type == "array" and isinstance(value, list))
    )
    if not valid:
        return [f"{path} 应为 {field_type}，实际是 {type(value).__name__}"]
    issues: list[str] = []
    if field_type == "string":
        if isinstance(definition.get("minLength"), int) and len(value) < definition["minLength"]:
            issues.append(f"{path} 长度小于 minLength")
        if isinstance(definition.get("maxLength"), int) and len(value) > definition["maxLength"]:
            issues.append(f"{path} 长度大于 maxLength")
        pattern = definition.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value) is not None
            except re.error as error:
                issues.append(f"{path} 的 pattern 无效：{error}")
            else:
                if not matches:
                    issues.append(f"{path} 不匹配 pattern")
        format_name = definition.get("format")
        if isinstance(format_name, str) and not _matches_string_format(value, format_name):
            issues.append(f"{path} 不符合 format={format_name}")
    if field_type in {"integer", "number"}:
        if "minimum" in definition and value < definition["minimum"]:
            issues.append(f"{path} 小于 minimum")
        if "maximum" in definition and value > definition["maximum"]:
            issues.append(f"{path} 大于 maximum")
        if "exclusiveMinimum" in definition and value <= definition["exclusiveMinimum"]:
            issues.append(f"{path} 不大于 exclusiveMinimum")
        if "exclusiveMaximum" in definition and value >= definition["exclusiveMaximum"]:
            issues.append(f"{path} 不小于 exclusiveMaximum")
        multiple = definition.get("multipleOf")
        if isinstance(multiple, (int, float)) and multiple > 0:
            quotient = value / multiple
            if not math.isclose(quotient, round(quotient), rel_tol=1e-9, abs_tol=1e-9):
                issues.append(f"{path} 不满足 multipleOf")
    if "enum" in definition and value not in definition["enum"]:
        issues.append(f"{path} 不在 enum 中")
    if "const" in definition and value != definition["const"]:
        issues.append(f"{path} 不等于 const")
    if field_type == "object":
        properties = definition.get("properties", {})
        required = set(definition.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            issues.append(f"{path} 缺少属性 {missing}")
        unknown = sorted(set(value) - set(properties))
        if unknown:
            issues.append(f"{path} 包含未知属性 {unknown}")
        for name, child in properties.items():
            if name in value and isinstance(child, dict):
                issues.extend(_value_matches(value[name], child, path=f"{path}.{name}"))
    if field_type == "array":
        if isinstance(definition.get("minItems"), int) and len(value) < definition["minItems"]:
            issues.append(f"{path} 元素数小于 minItems")
        if isinstance(definition.get("maxItems"), int) and len(value) > definition["maxItems"]:
            issues.append(f"{path} 元素数大于 maxItems")
        items = definition.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                issues.extend(_value_matches(item, items, path=f"{path}[{index}]"))
        if definition.get("uniqueItems") is True:
            serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(serialized) != len(set(serialized)):
                issues.append(f"{path} 不满足 uniqueItems")
    return issues


def validate_records(
    records: list[dict[str, Any]],
    record_set: dict[str, Any],
) -> list[str]:
    fields = record_set.get("fields", {})
    issues: list[str] = []
    for index, record in enumerate(records):
        unknown = sorted(set(record) - set(fields))
        if unknown:
            issues.append(f"record[{index}] 包含未声明字段 {unknown}")
        for name, definition in fields.items():
            if not isinstance(definition, dict):
                continue
            value = record.get(name)
            issues.extend(_value_matches(value, definition, path=f"record[{index}].{name}"))
        if len(issues) >= 30:
            break
    key_fields = record_set.get("key_fields", [])
    if key_fields:
        seen: set[tuple[str, ...]] = set()
        for index, record in enumerate(records):
            key = tuple(
                json.dumps(record.get(name), ensure_ascii=False, sort_keys=True)
                for name in key_fields
            )
            if key in seen:
                issues.append(f"record[{index}] 的 key_fields 重复")
                break
            seen.add(key)
    return issues


def _records_from_input(path: Path, record_set_id: str) -> list[dict[str, Any]]:
    try:
        groups = read_entity_groups(path, entity_name=record_set_id)
    except StructuredDataError as error:
        raise RuntimeError(f"结构化输入无法读取：{error}") from error
    if record_set_id in groups:
        return groups[record_set_id]
    if len(groups) == 1:
        return next(iter(groups.values()))
    raise RuntimeError(
        f"输入包含多个记录组且没有 {record_set_id!r}：{sorted(groups)}"
    )


def _sqlite_value(value: Any, field_type: str) -> Any:
    if value is None:
        return None
    if field_type == "boolean":
        return 1 if value else 0
    if field_type in {"object", "array"}:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def materialize_record_set(
    database_path: Path,
    *,
    record_set: dict[str, Any],
    input_path: Path,
) -> int:
    """验证输入并原子替换一个 Record Set 表。"""

    record_set_id = str(record_set["record_set_id"])
    records = _records_from_input(input_path, record_set_id)
    if not records:
        raise RuntimeError(f"Record Set {record_set_id} 不能物化为空表")
    issues = validate_records(records, record_set)
    if issues:
        raise RuntimeError("Record Set 数据不符合计划：" + "; ".join(issues[:20]))
    fields = record_set["fields"]
    names = list(fields)
    columns = ", ".join(
        f"{_quote_identifier(name)} {_SQLITE_TYPES[str(fields[name]['type'])]}"
        + (" NOT NULL" if fields[name].get("nullable") is False else "")
        for name in names
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f"DROP TABLE IF EXISTS {_quote_identifier(record_set_id)}")
        connection.execute(f"CREATE TABLE {_quote_identifier(record_set_id)} ({columns}) STRICT")
        placeholders = ", ".join("?" for _ in names)
        connection.executemany(
            f"INSERT INTO {_quote_identifier(record_set_id)} "
            f"({', '.join(_quote_identifier(name) for name in names)}) VALUES ({placeholders})",
            [
                tuple(_sqlite_value(record.get(name), str(fields[name]["type"])) for name in names)
                for record in records
            ],
        )
        key_fields = list(record_set.get("key_fields", []))
        if key_fields:
            index_name = f"ux_{record_set_id}_key"
            connection.execute(
                f"CREATE UNIQUE INDEX {_quote_identifier(index_name)} ON "
                f"{_quote_identifier(record_set_id)} "
                f"({', '.join(_quote_identifier(name) for name in key_fields)})"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(records)


def _safe_archive_member(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _extract_archive(source: Path, target: Path) -> None:
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for item in archive.infolist():
                if not _safe_archive_member(item.filename):
                    raise RuntimeError(f"归档包含不安全路径：{item.filename}")
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError(f"归档包含符号链接：{item.filename}")
            archive.extractall(target)
        return
    if tarfile.is_tarfile(source):
        with tarfile.open(source) as archive:
            members = archive.getmembers()
            for item in members:
                if not _safe_archive_member(item.name) or item.issym() or item.islnk():
                    raise RuntimeError(f"归档包含不安全成员：{item.name}")
            archive.extractall(target, members=members, filter="data")
        return
    raise RuntimeError(f"不支持的归档格式：{source}")


def materialize_scope(
    scopes_root: Path,
    *,
    scope_id: str,
    sources: list[Path],
    mode: str,
) -> int:
    """从不可变 Raw 复制或安全解包一个 Scope，成功后原子替换。"""

    if mode not in {"copy", "extract"}:
        raise RuntimeError("Scope 物化模式必须是 copy 或 extract")
    if not sources:
        raise RuntimeError("Scope 至少需要一个来源文件")
    scopes_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{scope_id}.building-", dir=scopes_root))
    target = scopes_root / scope_id
    try:
        for source in sources:
            if not source.is_file():
                raise RuntimeError(f"Scope 来源不存在：{source}")
            if mode == "copy":
                destination = temporary / source.name
                if destination.exists():
                    raise RuntimeError(f"Scope 来源文件名冲突：{source.name}")
                shutil.copy2(source, destination)
            else:
                _extract_archive(source, temporary)
        file_count = sum(path.is_file() for path in temporary.rglob("*"))
        if file_count == 0:
            raise RuntimeError("Scope 物化后没有普通文件")
        backup = scopes_root / f".{scope_id}.previous-{os.getpid()}"
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            target.replace(backup)
        temporary.replace(target)
        shutil.rmtree(backup, ignore_errors=True)
        return file_count
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def environment_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """删除集成过程字段，确定性生成 v2.0 environment.json。"""

    record_keys = {"record_set_id", "name", "description", "access", "key_fields", "fields"}
    scope_keys = {"scope_id", "name", "description", "access", "structure"}
    return {
        "schema_version": "2.0",
        "environment_id": plan["environment_id"],
        "name": plan["name"],
        "description": plan["description"],
        "record_sets": [
            {key: value for key, value in item.items() if key in record_keys}
            for item in plan.get("record_sets", [])
        ],
        "relationships": list(plan.get("relationships", [])),
        "filesystem_scopes": [
            {key: value for key, value in item.items() if key in scope_keys}
            for item in plan.get("filesystem_scopes", [])
        ],
    }


__all__ = [
    "environment_from_plan",
    "materialize_record_set",
    "materialize_scope",
    "validate_records",
]
