from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .structured_io import count_structured_records


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
_FILE_FORMAT_ALIASES = {
    "gds": "gdsii",
    "oas": "oasis",
    "md": "markdown",
    "sqlite3": "sqlite",
    "db": "sqlite",
    "tif": "tiff",
    "jpeg": "jpg",
}
_FORMAT_OPERATION_ROLES = {
    "xml": ("parse", "search", "edit", "validate"),
    "gdsii": ("parse", "inspect_hierarchy", "edit", "aggregate_geometry", "render", "validate"),
    "oasis": ("parse", "inspect_hierarchy", "edit", "aggregate_geometry", "render", "validate"),
    "lef": ("parse", "inspect_hierarchy", "edit", "validate"),
    "def": ("parse", "inspect_hierarchy", "edit", "validate"),
    "liberty": ("parse", "search", "edit", "validate"),
    "spice": ("parse", "search", "edit", "simulate", "validate"),
    "touchstone": ("parse", "aggregate", "render", "simulate", "validate"),
    "dxf": ("parse", "inspect_hierarchy", "edit", "render", "validate"),
    "dwg": ("parse", "inspect_hierarchy", "edit", "render", "validate"),
    "step": ("parse", "inspect_hierarchy", "edit", "render", "validate"),
    "iges": ("parse", "inspect_hierarchy", "edit", "render", "validate"),
    "stl": ("parse", "aggregate_geometry", "edit", "render", "validate"),
    "obj": ("parse", "aggregate_geometry", "edit", "render", "validate"),
    "geojson": ("parse", "search", "aggregate_geometry", "edit", "render", "validate"),
    "shp": ("parse", "search", "aggregate_geometry", "edit", "render", "validate"),
    "gpkg": ("parse", "search", "aggregate_geometry", "edit", "render", "validate"),
    "pdf": ("read", "search", "render", "extract", "annotate"),
    "svg": ("parse", "search", "edit", "render", "validate"),
    "png": ("inspect", "edit", "render"),
    "jpg": ("inspect", "edit", "render"),
    "tiff": ("inspect", "edit", "render"),
    "wav": ("inspect", "edit", "transcode"),
    "mp3": ("inspect", "edit", "transcode"),
    "mp4": ("inspect", "edit", "transcode"),
}


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


def _is_file_reference_field(field: str) -> bool:
    lowered = field.lower()
    return lowered == "file_path" or lowered.endswith("_file_path")


def _normalized_file_format(value: str) -> str:
    suffix = Path(value).suffix.lower().lstrip(".") or "binary"
    return _FILE_FORMAT_ALIASES.get(suffix, suffix)


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
    identifier = _is_technical_field(field)
    file_reference = _is_file_reference_field(field)
    technical = identifier or file_reference
    if identifier:
        roles.append("identifier")
    if file_reference and value_type == "string":
        roles.append("file_reference")
    if any(token in lowered for token in _TEMPORAL_TOKENS):
        roles.append("temporal")
    if value_type in {"integer", "number"} and not technical and "temporal" not in roles:
        roles.append("numeric_measure")
    if value_type == "boolean" and not technical and distinct > 1:
        roles.append("boolean")
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
            field_profile = {
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
            if "file_reference" in field_profile["roles"]:
                field_profile["file_formats"] = sorted(
                    {
                        _normalized_file_format(str(value))
                        for value in non_empty
                    }
                )
            fields[field] = field_profile
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
            format_name = _FILE_FORMAT_ALIASES.get(suffix, suffix)
            record_count: int | None = None
            if format_name in {
                "json",
                "jsonl",
                "ndjson",
                "csv",
                "parquet",
                "sqlite",
            }:
                record_count = count_structured_records(path)
            roles = ["inspect", "copy", "hash"]
            if format_name in {"json", "jsonl", "csv", "tsv", "xml", "yaml", "yml", "sqlite"}:
                roles.append("parse")
            if format_name in {"txt", "markdown", "html", "htm", "json", "jsonl", "csv", "tsv", "xml", "yaml", "yml"}:
                roles.extend(["read", "search"])
            roles.extend(_FORMAT_OPERATION_ROLES.get(format_name, ()))
            profiles.append(
                {
                    "path": relative,
                    "bucket": bucket_field.removesuffix("_files"),
                    "format": format_name,
                    "bytes": path.stat().st_size,
                    "record_count": record_count,
                    "operation_roles": list(dict.fromkeys(roles)),
                }
            )
    return profiles
