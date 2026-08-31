from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


_TEMPORAL_TOKENS = (
    "year", "date", "time", "timestamp", "created", "updated", "published",
    "started", "ended", "period", "month", "day",
)
_TECHNICAL_TOKENS = (
    "id", "uuid", "uri", "url", "href", "code", "hash", "checksum",
)
_TEXT_TOKENS = (
    "name", "title", "description", "summary", "body", "abstract", "text",
    "content", "message", "caption", "label",
)


def _value_key(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _primitive_type(values: list[Any]) -> str:
    non_empty = [value for value in values if value not in (None, "")]
    if not non_empty:
        return "string"
    if all(isinstance(value, bool) for value in non_empty):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in non_empty):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in non_empty):
        return "number"
    return "string"


def _is_technical_field(field: str) -> bool:
    lowered = field.lower()
    return (
        lowered in _TECHNICAL_TOKENS
        or lowered.endswith(("_id", "_ids", "_url", "_uri", "_code", "_hash"))
        or lowered.startswith(("id_", "url_", "uri_"))
    )


def _field_roles(
    field: str,
    value_type: str,
    values: list[Any],
    *,
    record_count: int,
) -> list[str]:
    lowered = field.lower()
    non_empty = [value for value in values if value not in (None, "")]
    distinct = len({_value_key(value) for value in non_empty})
    roles: list[str] = []
    technical = _is_technical_field(field)
    if technical:
        roles.append("identifier")
    if any(token in lowered for token in _TEMPORAL_TOKENS):
        roles.append("temporal")
    if value_type in {"integer", "number"} and not technical and "temporal" not in roles:
        roles.append("numeric_measure")
    if value_type == "string" and not technical and non_empty:
        average_length = sum(len(str(value)) for value in non_empty) / len(non_empty)
        category_ceiling = max(8, min(100, int(math.sqrt(max(record_count, 1)) * 4)))
        if 1 < distinct <= category_ceiling and average_length < 80:
            roles.append("category")
        if average_length >= 20 or any(token in lowered for token in _TEXT_TOKENS):
            roles.append("text")
        elif distinct > 1:
            roles.append("display")
    if distinct > 1 and not technical:
        roles.append("varied")
    return list(dict.fromkeys(roles))


def profile_entity_groups(
    entity_groups: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """为规范化实体生成字段基数、非空率和可操作角色画像。"""

    profiles: dict[str, dict[str, Any]] = {}
    for entity_type, records in sorted(entity_groups.items()):
        if not records:
            continue
        field_names = sorted({field for record in records for field in record})
        fields: dict[str, dict[str, Any]] = {}
        for field in field_names:
            values = [record.get(field) for record in records]
            non_empty = [value for value in values if value not in (None, "")]
            value_type = _primitive_type(values)
            fields[field] = {
                "type": value_type,
                "non_null_count": len(non_empty),
                "non_null_ratio": round(len(non_empty) / len(records), 6),
                "distinct_count": len({_value_key(value) for value in non_empty}),
                "roles": _field_roles(
                    field,
                    value_type,
                    values,
                    record_count=len(records),
                ),
            }
        primary_fields = [
            field
            for field, profile in fields.items()
            if "identifier" in profile["roles"]
            and profile["non_null_count"] == len(records)
            and profile["distinct_count"] == len(records)
        ]
        profiles[entity_type] = {
            "record_count": len(records),
            "field_count": len(fields),
            "primary_key_candidates": primary_fields,
            "fields": fields,
        }
    return profiles


def _json_record_count(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        counts = [len(value) for value in payload.values() if isinstance(value, list)]
        return sum(counts) if counts else 1
    return 1


def _csv_record_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            return max(0, sum(1 for _row in csv.reader(stream)) - 1)
    except (OSError, UnicodeError, csv.Error):
        return None


def profile_workspace_files(
    workspace: Path,
    checkpoint: dict[str, Any],
) -> list[dict[str, Any]]:
    """生成文件级画像；不把协议/provenance 文件算作业务内容。"""

    profiles: list[dict[str, Any]] = []
    for bucket_field in ("raw_files", "entity_files", "derived_files"):
        for relative in checkpoint.get(bucket_field, []):
            if not isinstance(relative, str):
                continue
            path = workspace / relative
            if not path.is_file():
                continue
            suffix = path.suffix.lower().lstrip(".") or "binary"
            record_count: int | None = None
            if suffix == "json":
                record_count = _json_record_count(path)
            elif suffix == "csv":
                record_count = _csv_record_count(path)
            roles = ["inspect", "copy", "hash"]
            if suffix in {"json", "jsonl", "csv", "tsv", "xml", "yaml", "yml", "sqlite"}:
                roles.append("parse")
            if suffix in {"txt", "md", "html", "htm", "json", "jsonl", "csv", "tsv", "xml", "yaml", "yml"}:
                roles.extend(["read", "search"])
            if suffix in {"gds", "gdsii"}:
                roles.extend(["parse_gdsii", "inspect_hierarchy", "aggregate_geometry", "render"])
            profiles.append(
                {
                    "path": relative,
                    "bucket": bucket_field.removesuffix("_files"),
                    "format": suffix,
                    "bytes": path.stat().st_size,
                    "record_count": record_count,
                    "operation_roles": list(dict.fromkeys(roles)),
                }
            )
    return profiles
