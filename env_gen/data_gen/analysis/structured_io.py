"""契约支持的结构化业务文件统一读取和 canonical 校验。"""

from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any


ENTITY_FORMATS = {"json", "jsonl", "csv", "parquet", "sqlite"}
_FORMAT_BY_SUFFIX = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".csv": "csv",
    ".parquet": "parquet",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
}
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_INTEGER_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_NUMBER_PATTERN = re.compile(
    r"^-?(?:(?:0|[1-9][0-9]*)\.[0-9]+|(?:0|[1-9][0-9]*)[eE][+-]?[0-9]+|"
    r"(?:0|[1-9][0-9]*)\.[0-9]+[eE][+-]?[0-9]+)$"
)


class StructuredDataError(ValueError):
    """结构化文件无法按环境契约解释。"""


def entity_format(path: Path, declared_format: str | None = None) -> str:
    detected = _FORMAT_BY_SUFFIX.get(path.suffix.lower())
    if declared_format is not None and detected is not None and declared_format != detected:
        raise StructuredDataError(
            f"声明格式 {declared_format} 与文件扩展名 {path.suffix} 不一致"
        )
    value = declared_format or detected
    if value not in ENTITY_FORMATS:
        raise StructuredDataError(
            f"不支持的 entity 格式：{declared_format or path.suffix or '<none>'}"
        )
    return value


def entity_name_from_path(path: Path) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", path.stem).strip("_").lower()
    value = re.sub(r"_+", "_", value)
    if not _NAME_PATTERN.fullmatch(value):
        raise StructuredDataError(
            f"单实体文件名必须能表示小写 snake_case 实体名：{path.name}"
        )
    return value


def infer_tabular_scalar(value: str | None) -> Any:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INTEGER_PATTERN.fullmatch(stripped):
        unsigned = stripped[1:] if stripped.startswith("-") else stripped
        if len(unsigned) == 1 or not unsigned.startswith("0"):
            try:
                return int(stripped)
            except ValueError:
                pass
    if _NUMBER_PATTERN.fullmatch(stripped):
        try:
            number = float(stripped)
        except ValueError:
            pass
        else:
            if math.isfinite(number):
                return number
    return value


def _infer_csv_value(field: str, value: str | None) -> Any:
    lowered = field.lower()
    if value is not None and (
        lowered in {"id", "code", "uuid"}
        or lowered.endswith(("_id", "_code", "_key", "_uuid"))
    ):
        return value if value.strip() else None
    return infer_tabular_scalar(value)


def _normalize_external_scalar(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _single_entity_name(path: Path, entity_name: str | None) -> str:
    value = entity_name or entity_name_from_path(path)
    if not _NAME_PATTERN.fullmatch(value):
        raise StructuredDataError(f"实体类型必须使用小写 snake_case：{value}")
    return value


def _read_json_groups(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StructuredDataError(f"JSON 无法读取：{path}: {error}") from error
    if not isinstance(payload, dict) or not payload:
        raise StructuredDataError("entity JSON 根节点必须是非空对象")
    groups: dict[str, list[dict[str, Any]]] = {}
    for name, records in payload.items():
        if not isinstance(records, list):
            raise StructuredDataError(f"实体 {name} 的值必须是记录数组")
        groups[str(name)] = records
    return groups


def _read_jsonl_groups(
    path: Path,
    entity_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise StructuredDataError(
                    f"JSONL 第 {line_number} 行必须是对象"
                )
            records.append(value)
    except StructuredDataError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StructuredDataError(f"JSONL 无法读取：{path}: {error}") from error
    return {_single_entity_name(path, entity_name): records}


def _read_csv_groups(
    path: Path,
    entity_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            if not fields or any(not str(field).strip() for field in fields):
                raise StructuredDataError("CSV 必须有完整非空表头")
            if len(fields) != len(set(fields)):
                raise StructuredDataError("CSV 表头不能包含重复字段")
            records: list[dict[str, Any]] = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise StructuredDataError(f"CSV 第 {row_number} 行列数超过表头")
                records.append(
                    {
                        str(field): _infer_csv_value(str(field), row.get(field))
                        for field in fields
                    }
                )
    except StructuredDataError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise StructuredDataError(f"CSV 无法读取：{path}: {error}") from error
    return {_single_entity_name(path, entity_name): records}


def _read_sqlite_groups(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            groups: dict[str, list[dict[str, Any]]] = {}
            for table in tables:
                quoted = table.replace('"', '""')
                rows = connection.execute(f'SELECT * FROM "{quoted}"')
                groups[table] = [
                    {
                        str(name): _normalize_external_scalar(value)
                        for name, value in dict(row).items()
                    }
                    for row in rows
                ]
            return groups
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise StructuredDataError(f"SQLite 无法读取：{path}: {error}") from error


def _read_parquet_groups(
    path: Path,
    entity_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise StructuredDataError(
            "读取 Parquet 需要可选依赖 pyarrow；请安装 agent-world-mini[data-gen]"
        ) from error
    try:
        rows = parquet.read_table(path).to_pylist()
    except Exception as error:
        raise StructuredDataError(f"Parquet 无法读取：{path}: {error}") from error
    records = [
        {
            str(name): _normalize_external_scalar(value)
            for name, value in row.items()
        }
        for row in rows
    ]
    return {_single_entity_name(path, entity_name): records}


def read_entity_groups(
    path: Path,
    *,
    entity_name: str | None = None,
    declared_format: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    format_name = entity_format(path, declared_format)
    if format_name == "json":
        return _read_json_groups(path)
    if format_name == "jsonl":
        return _read_jsonl_groups(path, entity_name)
    if format_name == "csv":
        return _read_csv_groups(path, entity_name)
    if format_name == "sqlite":
        return _read_sqlite_groups(path)
    return _read_parquet_groups(path, entity_name)


def validate_entity_groups(
    groups: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    if not groups:
        raise StructuredDataError("entity 文件至少需要一个实体类型")
    counts: dict[str, int] = {}
    for entity_type, records in groups.items():
        if not _NAME_PATTERN.fullmatch(str(entity_type)):
            raise StructuredDataError(
                f"实体类型必须使用小写 snake_case：{entity_type}"
            )
        if not isinstance(records, list) or not records:
            raise StructuredDataError(f"实体 {entity_type} 必须是非空记录数组")
        expected_fields: set[str] | None = None
        field_types: dict[str, type] = {}
        for index, record in enumerate(records):
            if not isinstance(record, dict) or not record:
                raise StructuredDataError(f"{entity_type}[{index}] 必须是非空对象")
            fields = set(record)
            invalid_fields = sorted(
                field for field in fields if not _NAME_PATTERN.fullmatch(str(field))
            )
            if invalid_fields:
                raise StructuredDataError(
                    f"{entity_type}[{index}] 字段必须使用小写 snake_case：{invalid_fields}"
                )
            if expected_fields is None:
                expected_fields = fields
            elif fields != expected_fields:
                raise StructuredDataError(
                    f"{entity_type}[{index}] 字段不一致；应为 {sorted(expected_fields)}"
                )
            for field, value in record.items():
                if value is None or isinstance(value, (dict, list, bytes, bytearray)):
                    raise StructuredDataError(
                        f"{entity_type}[{index}].{field} 必须是非空 JSON 标量"
                    )
                if not isinstance(value, (str, bool, int, float)):
                    raise StructuredDataError(
                        f"{entity_type}[{index}].{field} 类型不受支持：{type(value).__name__}"
                    )
                if isinstance(value, float) and not math.isfinite(value):
                    raise StructuredDataError(
                        f"{entity_type}[{index}].{field} 必须是有限数值"
                    )
                value_type = bool if isinstance(value, bool) else type(value)
                previous = field_types.setdefault(str(field), value_type)
                if previous is not value_type and not (
                    {previous, value_type} <= {int, float}
                ):
                    raise StructuredDataError(
                        f"{entity_type}.{field} 在不同记录中的类型不一致"
                    )
        counts[str(entity_type)] = len(records)
    return counts


def count_structured_records(path: Path) -> int | None:
    try:
        format_name = entity_format(path)
        if format_name == "json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return len(payload)
            if isinstance(payload, dict):
                lists = [value for value in payload.values() if isinstance(value, list)]
                return sum(len(value) for value in lists) if lists else 1
            return 1
        return sum(len(records) for records in read_entity_groups(path).values())
    except (StructuredDataError, OSError, UnicodeError, json.JSONDecodeError):
        return None


__all__ = [
    "ENTITY_FORMATS",
    "StructuredDataError",
    "count_structured_records",
    "entity_format",
    "entity_name_from_path",
    "infer_tabular_scalar",
    "read_entity_groups",
    "validate_entity_groups",
]
