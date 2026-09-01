#!/usr/bin/env python3
"""Build a self-contained HTML view of final generated environments."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from env_gen.data_gen.analysis.environment_quality import (
    EnvironmentQualityPolicy,
    build_environment_quality_profile,
)
from env_gen.data_gen.analysis.field_review import field_review_issues
from env_gen.data_gen.analysis.integration_profiling import build_integration_profile


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "step1_environment_audit.html"
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workspace_inventory(root: Path) -> set[str]:
    workspace = root / "workspace"
    return {
        path.relative_to(workspace).as_posix()
        for directory in ("raw", "entities", "derived")
        for path in (workspace / directory).rglob("*")
        if path.is_file()
    }


def _checkpoint_paths(checkpoint: dict[str, Any]) -> set[str]:
    return {
        str(path)
        for key in ("raw_files", "entity_files", "derived_files")
        for path in checkpoint.get(key, [])
        if isinstance(path, str)
    }


def _source_file_audit(
    root: Path,
    checkpoint: dict[str, Any],
    sources_payload: dict[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    owners: dict[str, list[str]] = {}
    source_rows = sources_payload.get("sources", [])
    source_rows = source_rows if isinstance(source_rows, list) else []
    for source in source_rows:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "unknown")
        for item in source.get("files", []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            relative = str(item["path"])
            owners.setdefault(relative, []).append(source_id)
            path = root / "workspace" / relative
            expected = item.get("sha256")
            if not path.is_file():
                issues.append(
                    {
                        "severity": "error",
                        "code": "source_file_missing",
                        "message": f"来源 {source_id} 映射的 Raw 不存在：{relative}",
                    }
                )
            elif not isinstance(expected, str) or _file_sha256(path) != expected:
                issues.append(
                    {
                        "severity": "error",
                        "code": "source_hash_mismatch",
                        "message": f"来源 {source_id} 的 Raw SHA-256 不匹配：{relative}",
                    }
                )

    raw_paths = {
        str(path)
        for path in checkpoint.get("raw_files", [])
        if isinstance(path, str)
    }
    missing = sorted(raw_paths - owners.keys())
    extra = sorted(owners.keys() - raw_paths)
    duplicate = sorted(path for path, values in owners.items() if len(values) != 1)
    if missing or extra or duplicate:
        issues.append(
            {
                "severity": "error",
                "code": "raw_source_mapping_mismatch",
                "message": (
                    "Raw 来源映射不闭合："
                    f"缺少 {len(missing)}，越界 {len(extra)}，非唯一 {len(duplicate)}。"
                ),
            }
        )
    return issues


def _resource_matches_file(resource: dict[str, Any], relative: str) -> bool:
    pattern = str(resource.get("path") or "").strip().lstrip("/")
    if not pattern or resource.get("storage_type") == "directory":
        return False
    if not any(marker in pattern for marker in "*?["):
        return relative == pattern.rstrip("/")
    if fnmatch.fnmatchcase(relative, pattern):
        return True
    prefix = pattern.split("*", 1)[0].rstrip("/")
    suffix = pattern.rsplit("*", 1)[-1].lstrip("/")
    return relative.startswith(prefix + "/") and (
        not suffix or relative.endswith(suffix)
    )


def _final_artifacts(
    environment: dict[str, Any],
    data_profile: dict[str, Any],
    sources_payload: dict[str, Any],
) -> dict[str, Any]:
    resources = [
        item for item in environment.get("resources", []) if isinstance(item, dict)
    ]
    file_profiles = [
        item for item in data_profile.get("files", []) if isinstance(item, dict)
    ]
    entity_profiles = data_profile.get("entities", {})
    entity_profiles = entity_profiles if isinstance(entity_profiles, dict) else {}
    relations = [
        item
        for item in data_profile.get("relation_candidates", [])
        if isinstance(item, dict)
    ]

    resource_by_id = {
        str(item.get("resource_id")): item
        for item in resources
        if isinstance(item.get("resource_id"), str)
    }
    entity_declarations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for resource in resources:
        schema = resource.get("entity_schema", {})
        if not isinstance(schema, dict):
            continue
        for entity_type, declaration in schema.items():
            if isinstance(entity_type, str) and isinstance(declaration, dict):
                entity_declarations[entity_type] = (resource, declaration)

    source_by_resource: dict[str, list[dict[str, str]]] = {}
    for source in sources_payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_row = {
            "source_id": str(source.get("source_id") or "unknown"),
            "url": str(source.get("url") or ""),
        }
        for resource_id in source.get("resource_ids", []):
            if isinstance(resource_id, str):
                source_by_resource.setdefault(resource_id, []).append(source_row)

    resource_rows: list[dict[str, Any]] = []
    file_to_resource: dict[str, str] = {}
    for resource in resources:
        resource_id = str(resource.get("resource_id") or "unknown")
        matched = [
            item
            for item in file_profiles
            if isinstance(item.get("path"), str)
            and _resource_matches_file(resource, str(item["path"]))
        ]
        for item in matched:
            file_to_resource[str(item["path"])] = resource_id
        resource_rows.append(
            {
                "resource_id": resource_id,
                "name": str(resource.get("name") or resource_id),
                "description": str(resource.get("description") or ""),
                "data_type": str(resource.get("data_type") or "unknown"),
                "storage_type": str(resource.get("storage_type") or "unknown"),
                "path": str(resource.get("path") or ""),
                "format": str(resource.get("format") or "unknown"),
                "writable": resource.get("writable") is True,
                "source_resources": [
                    str(value)
                    for value in resource.get("source_resources", [])
                    if isinstance(value, str)
                ],
                "public_sources": source_by_resource.get(resource_id, []),
                "file_count": len(matched),
                "bytes": sum(
                    int(item.get("bytes") or 0)
                    for item in matched
                    if isinstance(item.get("bytes"), (int, float))
                ),
                "record_count": sum(
                    int(item.get("record_count") or 0)
                    for item in matched
                    if isinstance(item.get("record_count"), (int, float))
                ),
                "files": matched,
            }
        )

    entities: list[dict[str, Any]] = []
    file_indexes: list[dict[str, Any]] = []
    for entity_type, profile in entity_profiles.items():
        if not isinstance(profile, dict):
            continue
        resource, declaration = entity_declarations.get(
            str(entity_type), ({}, {})
        )
        declared_fields = declaration.get("fields", {})
        declared_fields = declared_fields if isinstance(declared_fields, dict) else {}
        profile_fields = profile.get("fields", {})
        profile_fields = profile_fields if isinstance(profile_fields, dict) else {}
        fields: list[dict[str, Any]] = []
        for field_name, field_profile in profile_fields.items():
            if not isinstance(field_profile, dict):
                continue
            field_declaration = declared_fields.get(field_name, {})
            field_declaration = (
                field_declaration if isinstance(field_declaration, dict) else {}
            )
            roles = [
                str(value)
                for value in field_profile.get("roles", [])
                if isinstance(value, str)
            ]
            field_row = {
                "name": str(field_name),
                "type": str(field_profile.get("type") or "unknown"),
                "description": str(field_declaration.get("description") or ""),
                "non_null_ratio": field_profile.get("non_null_ratio"),
                "distinct_count": field_profile.get("distinct_count"),
                "roles": roles,
                "file_formats": [
                    str(value)
                    for value in field_profile.get("file_formats", [])
                    if isinstance(value, str)
                ],
            }
            fields.append(field_row)
            if "file_reference" in roles:
                file_indexes.append(
                    {
                        "entity_type": str(entity_type),
                        "entity_resource_id": str(
                            resource.get("resource_id") or "unknown"
                        ),
                        "field": str(field_name),
                        "formats": field_row["file_formats"],
                        "indexed_path_count": int(
                            field_profile.get("distinct_count") or 0
                        ),
                        "source_resource_ids": [
                            str(value)
                            for value in resource.get("source_resources", [])
                            if isinstance(value, str)
                        ],
                    }
                )
        entities.append(
            {
                "entity_type": str(entity_type),
                "name": str(resource.get("name") or entity_type),
                "description": str(
                    declaration.get("description")
                    or resource.get("description")
                    or ""
                ),
                "resource_id": str(resource.get("resource_id") or "unknown"),
                "resource_path": str(resource.get("path") or ""),
                "record_count": int(profile.get("record_count") or 0),
                "field_count": int(profile.get("field_count") or len(fields)),
                "primary_keys": [
                    str(value)
                    for value in profile.get("primary_key_candidates", [])
                    if isinstance(value, str)
                ],
                "fields": fields,
            }
        )
    entities.sort(key=lambda item: (-item["record_count"], item["entity_type"]))

    for item in file_profiles:
        path = str(item.get("path") or "")
        item["resource_id"] = file_to_resource.get(path, "unmapped")

    summary = data_profile.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    return {
        "summary": {
            "entity_type_count": int(
                summary.get("entity_type_count") or len(entities)
            ),
            "entity_record_count": int(
                summary.get("entity_record_count")
                or sum(item["record_count"] for item in entities)
            ),
            "file_count": int(summary.get("file_count") or len(file_profiles)),
            "file_bytes": int(
                summary.get("file_bytes")
                or sum(int(item.get("bytes") or 0) for item in file_profiles)
            ),
            "relation_count": len(relations),
            "file_index_count": len(file_indexes),
        },
        "entities": entities,
        "relations": relations,
        "resources": resource_rows,
        "file_indexes": file_indexes,
        "resource_names": {
            resource_id: str(item.get("name") or resource_id)
            for resource_id, item in resource_by_id.items()
        },
    }


_SYNTHETIC_RECORD_SUGGESTIONS = (
    re.compile(
        r"\b(?:use|generate|create|synthesize|fabricate)\s+"
        r"(?:some\s+|a\s+)?synthetic\s+(?:business\s+)?records?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsynthetic\s+(?:business\s+)?records?\s+"
        r"(?:can|could|should|may)\s+(?:be\s+)?(?:replace|fill|cover)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:使用|生成|创建|构造|伪造)(?:一些)?(?:合成|模拟)(?:业务)?记录"),
    re.compile(r"(?:合成|模拟)(?:业务)?记录(?:可以|可用于|用来)(?:替代|补齐|覆盖)"),
)


def _suggests_synthetic_business_records(value: str) -> bool:
    """只标记建议造业务记录的文本，不误报“禁止合成”的边界说明。"""

    for pattern in _SYNTHETIC_RECORD_SUGGESTIONS:
        match = pattern.search(value)
        if match is None:
            continue
        prefix = value[max(0, match.start() - 32):match.start()].lower()
        if any(
            marker in prefix
            for marker in (
                "do not",
                "don't",
                "never",
                "must not",
                "should not",
                "cannot",
                "禁止",
                "不得",
                "不要",
                "不能",
                "不可",
                "避免",
            )
        ):
            continue
        return True
    return False


def _v2_sql_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _v2_decode_value(value: Any, definition: dict[str, Any]) -> Any:
    if value is None:
        return None
    if definition.get("type") in {"object", "array"} and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if definition.get("type") == "boolean" and isinstance(value, int):
        return bool(value)
    return value


def _v2_value_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _v2_scope_formats(structure: dict[str, Any]) -> set[str]:
    formats: set[str] = set()
    value = structure.get("format")
    if isinstance(value, str):
        formats.add(value)
    for child in structure.get("layout", []):
        if isinstance(child, dict):
            formats.update(_v2_scope_formats(child))
    return formats


def _v2_scope_files(root: Path, scope: dict[str, Any]) -> list[dict[str, Any]]:
    scope_id = str(scope.get("scope_id") or "")
    scope_root = root / "state/filesystem_scopes" / scope_id
    if not scope_root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(item for item in scope_root.rglob("*") if item.is_file()):
        relative = path.relative_to(scope_root).as_posix()
        result.append({
            "path": f"state/filesystem_scopes/{scope_id}/{relative}",
            "scope_relative_path": relative,
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": _file_sha256(path),
            "format": path.suffix.lower().lstrip(".") or "binary",
            "record_count": None,
        })
    return result


def _v2_record_snapshot(
    root: Path,
    record_sets: list[dict[str, Any]],
    scope_formats: dict[str, set[str]] | None = None,
    integration: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read only bounded samples and field statistics from the frozen SQLite state."""

    database = root / "state/records.sqlite"
    if not database.is_file() or not record_sets:
        return [], {}
    entities: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    value_profiles = {
        str(item.get("record_set_id")): item
        for item in (integration or {}).get("asset_profile", {}).get("record_sets", [])
        if isinstance(item, dict)
    }
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for record_set in record_sets:
            record_set_id = str(record_set.get("record_set_id") or "")
            fields = record_set.get("fields", {})
            fields = fields if isinstance(fields, dict) else {}
            value_profile = value_profiles.get(record_set_id, {})
            profiled_fields = value_profile.get("fields", {})
            profiled_fields = profiled_fields if isinstance(profiled_fields, dict) else {}
            if record_set_id not in tables:
                counts[record_set_id] = 0
                continue
            table = _v2_sql_quote(record_set_id)
            count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            counts[record_set_id] = count
            rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table} LIMIT 3")]
            field_rows: list[dict[str, Any]] = []
            for field_name, definition in fields.items():
                definition = definition if isinstance(definition, dict) else {}
                column = _v2_sql_quote(str(field_name))
                non_null = int(connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
                ).fetchone()[0])
                values = [
                    row[0]
                    for row in connection.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 5000"
                    )
                ]
                decoded = [_v2_decode_value(value, definition) for value in values]
                roles: list[str] = []
                if field_name in record_set.get("key_fields", []):
                    roles.append("identifier")
                if isinstance(definition.get("reference"), dict):
                    roles.append("file_reference")
                reference = definition.get("reference")
                file_formats: list[str] = []
                if isinstance(reference, dict):
                    scope_id = str(reference.get("scope_id") or "")
                    file_formats = sorted((scope_formats or {}).get(scope_id, set()))
                sample_values: list[Any] = []
                seen: set[str] = set()
                for value in decoded:
                    key = _v2_value_key(value)
                    if key not in seen:
                        seen.add(key)
                        sample_values.append(value)
                    if len(sample_values) == 3:
                        break
                field_rows.append({
                    "name": str(field_name),
                    "type": str(definition.get("type") or "unknown"),
                    "description": str(definition.get("description") or ""),
                    "non_null_ratio": (non_null / count) if count else 0,
                    "distinct_count": len({
                        _v2_value_key(value) for value in decoded
                    }),
                    "roles": roles,
                    "file_formats": file_formats,
                    "sample_values": sample_values,
                    "top_values": (
                        profiled_fields.get(str(field_name), {}).get("top_values", [])
                        if isinstance(profiled_fields.get(str(field_name)), dict) else []
                    ),
                })
            entities.append({
                "entity_type": record_set_id,
                "name": str(record_set.get("name") or record_set_id),
                "description": str(record_set.get("description") or ""),
                "resource_id": record_set_id,
                "resource_path": f"state/records.sqlite#{record_set_id}",
                "record_count": count,
                "field_count": len(fields),
                "primary_keys": [str(value) for value in record_set.get("key_fields", [])],
                "fields": field_rows,
                "samples": [
                    {
                        name: _v2_decode_value(value, fields.get(name, {}))
                        for name, value in row.items()
                    }
                    for row in rows
                ],
                "review_findings": [
                    item for item in value_profile.get("review_findings", [])
                    if isinstance(item, dict)
                ],
            })
    finally:
        connection.close()
    return entities, counts


def _v2_artifacts(
    root: Path,
    *,
    environment: dict[str, Any],
    plan: dict[str, Any],
    inventory: dict[str, Any],
    source_plan: dict[str, Any],
    integration: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    record_sets = [item for item in plan.get("record_sets", []) if isinstance(item, dict)]
    scopes = [item for item in plan.get("filesystem_scopes", []) if isinstance(item, dict)]
    scope_files_by_id = {
        str(scope.get("scope_id")): _v2_scope_files(root, scope)
        for scope in scopes
    }
    scope_format_by_id = {
        str(scope.get("scope_id")): _v2_scope_formats(
            scope.get("structure", {}) if isinstance(scope.get("structure"), dict) else {}
        )
        for scope in scopes
    }
    entities, record_counts = _v2_record_snapshot(
        root, record_sets, scope_format_by_id, integration
    )
    resource_names: dict[str, str] = {}
    resources: list[dict[str, Any]] = []
    source_to_raw_resource: dict[str, str] = {}
    inventory_files = [item for item in inventory.get("files", []) if isinstance(item, dict)]
    inventory_by_source: dict[str, list[dict[str, Any]]] = {}
    for item in inventory_files:
        source_id = str(item.get("source_id") or "unknown")
        inventory_by_source.setdefault(source_id, []).append(item)
    for source_id, files in sorted(inventory_by_source.items()):
        resource_id = f"raw_{source_id}"
        source_to_raw_resource[source_id] = resource_id
        resource_names[resource_id] = f"Raw · {source_id}"
        raw_rows: list[dict[str, Any]] = []
        for item in files:
            relative = str(item.get("path") or "")
            path = root / "provenance" / relative
            if not path.is_file():
                continue
            raw_rows.append({
                "path": f"provenance/{relative}",
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": str(item.get("sha256") or _file_sha256(path)),
                "format": str(item.get("format") or path.suffix.lstrip(".")),
                "record_count": item.get("shape", {}).get("structured_record_count")
                if isinstance(item.get("shape"), dict) else None,
            })
        resources.append({
            "resource_id": resource_id,
            "name": f"Raw · {source_id}",
            "description": "Step 2/3 保留的来源证据；仅用于追溯和重建。",
            "data_type": "raw",
            "storage_type": "provenance",
            "path": f"provenance/raw/{source_id}",
            "format": "mixed",
            "writable": False,
            "source_resources": [],
            "public_sources": [],
            "file_count": len(raw_rows),
            "bytes": sum(int(item.get("bytes") or 0) for item in raw_rows),
            "record_count": sum(int(item.get("record_count") or 0) for item in raw_rows),
            "files": raw_rows,
        })
    for record_set in record_sets:
        record_set_id = str(record_set.get("record_set_id") or "")
        resource_names[record_set_id] = str(record_set.get("name") or record_set_id)
        resources.append({
            "resource_id": record_set_id,
            "name": str(record_set.get("name") or record_set_id),
            "description": str(record_set.get("description") or ""),
            "data_type": "entity",
            "storage_type": "sqlite_table",
            "path": f"state/records.sqlite#{record_set_id}",
            "format": "sqlite",
            "writable": record_set.get("access") == "copy_on_write",
            "source_resources": [
                source_to_raw_resource[str(source_id)]
                for source_id in record_set.get("source_ids", [])
                if str(source_id) in source_to_raw_resource
            ],
            "public_sources": [],
            "file_count": 0,
            "bytes": 0,
            "record_count": record_counts.get(record_set_id, 0),
            "files": [],
        })
    for scope in scopes:
        scope_id = str(scope.get("scope_id") or "")
        files = scope_files_by_id.get(scope_id, [])
        resource_names[scope_id] = str(scope.get("name") or scope_id)
        resources.append({
            "resource_id": scope_id,
            "name": str(scope.get("name") or scope_id),
            "description": str(scope.get("description") or ""),
            "data_type": "filesystem_scope",
            "storage_type": "directory",
            "path": f"state/filesystem_scopes/{scope_id}",
            "format": ", ".join(sorted(scope_format_by_id[scope_id])) or "mixed",
            "writable": scope.get("access") == "copy_on_write",
            "source_resources": [
                source_to_raw_resource[str(source_id)]
                for source_id in scope.get("source_ids", [])
                if str(source_id) in source_to_raw_resource
            ],
            "public_sources": [],
            "file_count": len(files),
            "bytes": sum(int(item.get("bytes") or 0) for item in files),
            "record_count": 0,
            "files": files,
        })
    relation_by_id = {
        str(item.get("relationship_id")): item
        for item in integration.get("relationship_profile", {}).get("relationships", [])
        if isinstance(item, dict)
    }
    relations: list[dict[str, Any]] = []
    for relationship in plan.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        relation_id = str(relationship.get("relationship_id") or "")
        profile = relation_by_id.get(relation_id, {})
        source = relationship.get("from", {})
        target = relationship.get("to", {})
        relations.append({
            "relation_id": relation_id,
            "from_entity": str(source.get("record_set_id") or ""),
            "field": ".".join(str(value) for value in source.get("fields", [])),
            "to_entity": str(target.get("record_set_id") or ""),
            "target_field": ".".join(str(value) for value in target.get("fields", [])),
            "edge_count": int(profile.get("source_non_null_count", 0) or 0),
            "description": str(relationship.get("description") or ""),
        })
    reference_profiles = {
        (str(item.get("record_set_id")), str(item.get("field"))): item
        for item in integration.get("file_reference_profile", {}).get("references", [])
        if isinstance(item, dict)
    }
    file_indexes: list[dict[str, Any]] = []
    for record_set in record_sets:
        record_set_id = str(record_set.get("record_set_id") or "")
        for field_name, definition in (record_set.get("fields", {}) or {}).items():
            if not isinstance(definition, dict) or not isinstance(definition.get("reference"), dict):
                continue
            reference = definition["reference"]
            scope_id = str(reference.get("scope_id") or "")
            profile = reference_profiles.get((record_set_id, str(field_name)), {})
            file_indexes.append({
                "entity_type": record_set_id,
                "entity_resource_id": record_set_id,
                "field": str(field_name),
                "formats": sorted(scope_format_by_id.get(scope_id, set())),
                "indexed_path_count": int(profile.get("checked_path_count", 0) or 0),
                "source_resource_ids": [scope_id] if scope_id else [],
            })
    return {
        "summary": {
            "entity_type_count": len(record_sets),
            "entity_record_count": sum(record_counts.values()),
            "file_count": sum(len(files) for files in scope_files_by_id.values()),
            "file_bytes": sum(
                int(item.get("bytes") or 0)
                for files in scope_files_by_id.values()
                for item in files
            ),
            "relation_count": len(relations),
            "file_index_count": len(file_indexes),
            "field_review_finding_count": int(
                integration.get("asset_profile", {}).get("field_review", {}).get(
                    "finding_count", 0
                ) or 0
            ),
        },
        "entities": entities,
        "relations": relations,
        "resources": resources,
        "file_indexes": file_indexes,
        "resource_names": resource_names,
    }


def _v2_environment_row(root: Path) -> dict[str, Any]:
    environment = _read_json(root / "environment.json") or {}
    scenario = _read_json(root / "provenance/scenario_research.json") or {}
    source_plan = _read_json(root / "provenance/source_plan.json") or {}
    inventory = _read_json(root / "provenance/source_inventory.json") or {}
    plan = _read_json(root / "provenance/integration_plan.json") or {}
    stored_integration = _read_json(root / "provenance/integration_profile.json") or {}
    stored_quality = _read_json(root / "provenance/quality_profile.json") or {}
    validation = _read_json(root / "validation.json") or {}
    integration = build_integration_profile(
        root,
        plan=plan,
        seed_global_id=str(plan.get("seed_global_id") or scenario.get("seed_global_id") or root.name),
        seed_sha256=str(plan.get("seed_sha256") or scenario.get("seed_sha256") or ""),
    )
    try:
        policy = EnvironmentQualityPolicy(**stored_quality.get("policy", {}))
    except (TypeError, ValueError):
        policy = EnvironmentQualityPolicy()
    quality = build_environment_quality_profile(
        root,
        plan=plan,
        scenario_research=scenario,
        source_plan=source_plan,
        source_inventory=inventory,
        integration_profile=integration,
        policy=policy,
    )
    artifacts = _v2_artifacts(
        root,
        environment=environment,
        plan=plan,
        inventory=inventory,
        source_plan=source_plan,
        integration=integration,
        quality=quality,
    )
    issues: list[dict[str, str]] = []
    if validation.get("valid") is not True:
        issues.append({
            "severity": "error",
            "code": "final_validation_failed",
            "message": "最终 v2 校验未通过。",
        })
    if integration.get("integration_tier") != "integrated":
        issues.append({
            "severity": "error",
            "code": "integration_not_closed",
            "message": "集成画像不是 integrated。",
        })
    if (
        stored_integration.get("integration_tier")
        and stored_integration.get("integration_tier") != integration.get("integration_tier")
    ):
        issues.append({
            "severity": "error",
            "code": "integration_tier_drift",
            "message": "当前代码从最终状态重算的集成等级与发布画像不一致。",
        })
    if (
        stored_quality.get("quality_tier")
        and stored_quality.get("quality_tier") != quality.get("quality_tier")
    ):
        issues.append({
            "severity": "warning",
            "code": "quality_tier_drift",
            "message": "当前代码从最终状态重算的丰富度等级与发布画像不一致。",
        })
    source_statuses = [
        item.get("status") for item in source_plan.get("sources", [])
        if isinstance(item, dict)
    ]
    if any(status in {"planned", "in_progress"} for status in source_statuses):
        issues.append({
            "severity": "error",
            "code": "unresolved_sources",
            "message": "来源计划仍有未收口来源。",
        })
    for gap in quality.get("quality_gaps", []):
        if isinstance(gap, dict):
            issues.append({
                "severity": "warning",
                "code": str(gap.get("code") or "quality_gap"),
                "message": str(gap.get("message") or "质量画像存在缺口。"),
            })
    review_issues = field_review_issues(
        root,
        profile=integration,
        plan=plan,
        review_path=root / "provenance/field_review.json",
        integration_plan_path=root / "provenance/integration_plan.json",
        integration_profile_path=root / "provenance/integration_profile.json",
    )
    for item in review_issues:
        issues.append({
            "severity": "warning",
            "code": str(item.get("code") or "field_review_required"),
            "message": str(item.get("message") or "字段分布仍需与 Raw 核对。"),
        })
    representation = quality.get("file_profile", {})
    representation = representation if isinstance(representation, dict) else {}
    source_entries = [item for item in source_plan.get("sources", []) if isinstance(item, dict)]
    source_assets: dict[str, list[str]] = {str(item.get("source_id")): [] for item in source_entries}
    for asset in [*plan.get("record_sets", []), *plan.get("filesystem_scopes", [])]:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("record_set_id") or asset.get("scope_id") or "")
        for source_id in asset.get("source_ids", []):
            source_assets.setdefault(str(source_id), []).append(asset_id)
    sources = [
        {
            "source_id": str(item.get("source_id") or "unknown"),
            "url": str(item.get("url") or ""),
            "retrieved_at": "",
            "resource_ids": sorted(set(source_assets.get(str(item.get("source_id")), []))),
            "file_count": len(item.get("raw_files", [])) if isinstance(item.get("raw_files"), list) else 0,
            "status": str(item.get("status") or ""),
        }
        for item in source_entries
    ]
    quality_tier = str(quality.get("quality_tier") or "unknown")
    indexed_formats = sorted({
        str(value)
        for index in artifacts["file_indexes"]
        for value in index.get("formats", [])
    })
    return {
        "global_id": str(environment.get("environment_id") or root.name),
        "name": str(environment.get("name") or root.name),
        "overview": str(environment.get("description") or ""),
        "counts": {
            "entity_types": artifacts["summary"]["entity_type_count"],
            "entity_records": artifacts["summary"]["entity_record_count"],
            "relations": artifacts["summary"]["relation_count"],
            "business_files": artifacts["summary"]["file_count"],
            "file_bytes": artifacts["summary"]["file_bytes"],
            "file_indexes": artifacts["summary"]["file_index_count"],
            "sources": len(sources),
            "raw_files": len(inventory.get("files", [])),
            "entity_files": len(plan.get("record_sets", [])),
            "derived_files": 0,
        },
        "data_shape": {
            "final_mode": str(quality.get("shape") or "unknown"),
            "final_rationale": str(quality.get("summary") or ""),
            "available_formats": [str(value) for value in representation.get("available_formats", [])],
            "indexed_formats": indexed_formats,
        },
        "artifacts": artifacts,
        "sources": sources,
        "quality_tier": quality_tier,
        "quality_gap_count": len(quality.get("quality_gaps", [])) if isinstance(quality.get("quality_gaps"), list) else 0,
        "field_review": {
            "finding_count": artifacts["summary"]["field_review_finding_count"],
            "complete": not review_issues,
        },
        "checkpoint_status": "ready",
        "finalization_result": "ready" if quality_tier == "rich" else "exhausted",
        "validation_valid": validation.get("valid") is True,
        "validation_error_count": len(validation.get("errors", [])) if isinstance(validation.get("errors"), list) else 0,
        "audit_issues": issues,
    }


def _environment_row(root: Path) -> dict[str, Any]:
    # v2 packages expose Record Sets in SQLite and files in named Scopes. Keep
    # the legacy branch below for historical v1 packages, but never interpret a
    # v2 package through workspace/entities or data_checkpoint.json.
    environment_probe = _read_json(root / "environment.json") or {}
    if environment_probe.get("schema_version") == "2.0" or "record_sets" in environment_probe:
        return _v2_environment_row(root)

    scenario = _read_json(root / "provenance/scenario_research.json") or {}
    source_plan = _read_json(root / "provenance/source_plan.json") or {}
    quality = _read_json(root / "provenance/quality_profile.json") or {}
    data_profile = _read_json(root / "provenance/data_profile.json") or {}
    environment = _read_json(root / "environment.json") or {}
    checkpoint = _read_json(root / "provenance/data_checkpoint.json") or {}
    validation = _read_json(root / "validation.json") or {}
    sources_payload = _read_json(root / "provenance/sources.json") or {}
    collection_audit = _read_json(root / "provenance/collection_audit.json") or {}
    controls = collection_audit.get("control_records", {})
    controls = controls if isinstance(controls, dict) else {}
    finalization = controls.get("finalization.json", {})
    finalization = finalization if isinstance(finalization, dict) else {}

    shape = scenario.get("data_shape_hypothesis", {})
    shape = shape if isinstance(shape, dict) else {}
    source_leads = [
        item for item in scenario.get("source_leads", []) if isinstance(item, dict)
    ]
    needs = [item for item in scenario.get("data_needs", []) if isinstance(item, dict)]
    refinements = [
        item for item in source_plan.get("research_refinements", [])
        if isinstance(item, dict)
    ]
    sources = [item for item in source_plan.get("sources", []) if isinstance(item, dict)]
    issues: list[dict[str, str]] = []

    required_sections = (
        "scenario_outline",
        "entity_candidates",
        "operation_candidates",
        "task_candidates",
        "data_needs",
        "data_shape_hypothesis",
        "source_leads",
        "seed_synthesis",
    )
    for name in required_sections:
        if not scenario.get(name):
            issues.append(
                {
                    "severity": "error",
                    "code": "missing_step1_section",
                    "message": f"Step 1 缺少或留空：{name}",
                }
            )
    synthetic_suggestions = [
        text
        for text in (
            [str(value) for value in scenario.get("risks", [])]
            + [str(value) for value in scenario.get("open_questions", [])]
            + [str(item.get("description") or "") for item in needs]
        )
        if _suggests_synthetic_business_records(text)
    ]
    if synthetic_suggestions:
        issues.append(
            {
                "severity": "warning",
                "code": "synthetic_data_language",
                "message": "Step 1 出现合成业务记录表述，需要人工确认没有替代真实公开事实。",
            }
        )
    file_mode = str(shape.get("likely_mode") or "unknown")
    formats = [str(value) for value in shape.get("candidate_file_formats", [])]
    file_paths = [str(value) for value in shape.get("file_relevant_seed_paths", [])]
    if file_mode != "structured_records" and (not formats or not file_paths):
        issues.append(
            {
                "severity": "error",
                "code": "incomplete_file_hypothesis",
                "message": "选择了文件型模式，但没有同时列出格式和对应 Seed 路径。",
            }
        )
    if len(source_leads) > 12:
        issues.append(
            {
                "severity": "warning",
                "code": "too_many_source_leads",
                "message": f"Step 1 给出 {len(source_leads)} 个来源线索，可能削弱 Step 2 优先级。",
            }
        )
    final_quality_gaps = quality.get("quality_gaps", [])
    final_quality_gaps = final_quality_gaps if isinstance(final_quality_gaps, list) else []
    representation = quality.get("representation_profile", {})
    representation = representation if isinstance(representation, dict) else {}
    final_data_mode = str(representation.get("data_mode") or file_mode)
    indexed_file_formats = [
        str(value) for value in representation.get("indexed_file_formats", [])
    ]
    if final_data_mode in {"hybrid", "file_native"} and not indexed_file_formats:
        issues.append(
            {
                "severity": "error",
                "code": "unindexed_final_file_mode",
                "message": "最终采用文件型模式，但画像中没有被 Entity 索引的文件格式。",
            }
        )
    validation_errors = validation.get("errors", [])
    validation_errors = validation_errors if isinstance(validation_errors, list) else []
    source_statuses = {
        status: sum(1 for item in sources if item.get("status") == status)
        for status in ("planned", "in_progress", "complete", "blocked", "unavailable")
    }
    if not source_plan:
        issues.append(
            {
                "severity": "error",
                "code": "missing_source_plan",
                "message": "缺少 Step 2 正式 source_plan，不能判断深调与来源收口。",
            }
        )
    elif source_statuses["planned"] or source_statuses["in_progress"]:
        issues.append(
            {
                "severity": "error",
                "code": "unresolved_sources",
                "message": (
                    f"仍有 {source_statuses['planned']} 个 planned 和 "
                    f"{source_statuses['in_progress']} 个 in_progress 来源。"
                ),
            }
        )
    if checkpoint.get("status") != "ready":
        issues.append(
            {
                "severity": "error",
                "code": "checkpoint_not_ready",
                "message": "最终 data checkpoint 不存在或尚未 ready。",
            }
        )
    if checkpoint.get("synthetic_business_record_count") not in (None, 0):
        issues.append(
            {
                "severity": "error",
                "code": "synthetic_business_records",
                "message": "最终 checkpoint 声明存在合成业务记录。",
            }
        )
    if not checkpoint.get("entity_files"):
        issues.append(
            {
                "severity": "error",
                "code": "no_entity_files",
                "message": "最终 checkpoint 没有规范 Entity 文件。",
            }
        )
    actual_files = _workspace_inventory(root)
    declared_files = _checkpoint_paths(checkpoint)
    if actual_files != declared_files:
        issues.append(
            {
                "severity": "error",
                "code": "checkpoint_inventory_mismatch",
                "message": (
                    "checkpoint 与 workspace 文件不一致："
                    f"漏记 {len(actual_files - declared_files)}，多记 "
                    f"{len(declared_files - actual_files)}。"
                ),
            }
        )
    issues.extend(_source_file_audit(root, checkpoint, sources_payload))
    need_coverage = [
        item
        for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict)
    ]
    unresolved_needs = [
        str(item.get("need_id") or "unknown")
        for item in need_coverage
        if item.get("status") in {None, "planned", "missing"}
    ]
    if unresolved_needs:
        issues.append(
            {
                "severity": "error",
                "code": "unresolved_data_needs",
                "message": f"仍有 {len(unresolved_needs)} 个数据需求没有收口。",
            }
        )
    if source_plan and not refinements:
        issues.append(
            {
                "severity": "error",
                "code": "missing_research_refinements",
                "message": "Step 2 没有记录对 Step 1 假设的深调修订。",
            }
        )
    if validation.get("valid") is not True or validation_errors:
        issues.append(
            {
                "severity": "error",
                "code": "final_validation_failed",
                "message": f"最终校验未通过，错误数：{len(validation_errors)}。",
            }
        )
    quality_tier = str(quality.get("quality_tier") or "unknown")
    if quality_tier not in {"rich", "not_rich"}:
        issues.append(
            {
                "severity": "error",
                "code": "missing_quality_tier",
                "message": "缺少最终 rich/not_rich 质量结论。",
            }
        )

    artifacts = _final_artifacts(environment, data_profile, sources_payload)

    return {
        "global_id": str(
            environment.get("environment_id")
            or scenario.get("seed_global_id")
            or root.name
        ),
        "name": str(environment.get("name") or root.name),
        "overview": str(environment.get("description") or ""),
        "counts": {
            "entity_types": artifacts["summary"]["entity_type_count"],
            "entity_records": artifacts["summary"]["entity_record_count"],
            "relations": artifacts["summary"]["relation_count"],
            "business_files": artifacts["summary"]["file_count"],
            "file_bytes": artifacts["summary"]["file_bytes"],
            "file_indexes": artifacts["summary"]["file_index_count"],
            "sources": len(
                [
                    item
                    for item in sources_payload.get("sources", [])
                    if isinstance(item, dict)
                ]
            ),
            "raw_files": len(checkpoint.get("raw_files", [])),
            "entity_files": len(checkpoint.get("entity_files", [])),
            "derived_files": len(checkpoint.get("derived_files", [])),
        },
        "data_shape": {
            "final_mode": final_data_mode,
            "final_rationale": str(representation.get("data_mode_reason") or ""),
            "available_formats": [
                str(value)
                for value in representation.get("available_file_formats", [])
            ],
            "indexed_formats": indexed_file_formats,
        },
        "artifacts": artifacts,
        "sources": [
            {
                "source_id": str(item.get("source_id") or "unknown"),
                "url": str(item.get("url") or ""),
                "retrieved_at": str(item.get("retrieved_at") or ""),
                "resource_ids": [
                    str(value)
                    for value in item.get("resource_ids", [])
                    if isinstance(value, str)
                ],
                "file_count": len(
                    [value for value in item.get("files", []) if isinstance(value, dict)]
                ),
            }
            for item in sources_payload.get("sources", [])
            if isinstance(item, dict)
        ],
        "quality_tier": quality_tier,
        "quality_gap_count": len(final_quality_gaps),
        "checkpoint_status": str(checkpoint.get("status") or "missing"),
        "finalization_result": str(finalization.get("result") or "unknown"),
        "validation_valid": validation.get("valid") is True,
        "validation_error_count": len(validation_errors),
        "audit_issues": issues,
    }


def build_payload(environment_dirs: list[Path]) -> dict[str, Any]:
    rows = [_environment_row(path.resolve()) for path in environment_dirs]
    rows.sort(key=lambda item: item["global_id"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment_count": len(rows),
        "environments": rows,
    }


def _document(payload: dict[str, Any]) -> str:
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"最终环境产物 · {payload['environment_count']} 个环境")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light; --ink:#18201d; --muted:#66716c; --line:#d9dfdc; --paper:#f7f8f7; --white:#fff; --green:#176b4d; --red:#a43b32; --amber:#9a650e; --blue:#285e8e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--paper); font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; letter-spacing:0; }}
button,input,select {{ font:inherit; letter-spacing:0; }}
header {{ background:#15201b; color:#fff; padding:24px max(24px,calc((100vw - 1440px)/2)); }}
h1 {{ margin:0 0 5px; font-size:24px; font-weight:700; }}
.subtitle {{ color:#b9c5bf; }}
.toolbar {{ display:flex; gap:10px; align-items:center; padding:14px max(24px,calc((100vw - 1440px)/2)); background:var(--white); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }}
.toolbar input {{ width:min(420px,45vw); padding:8px 10px; border:1px solid #aeb8b3; border-radius:4px; }}
.toolbar select {{ padding:8px 10px; border:1px solid #aeb8b3; border-radius:4px; background:#fff; }}
.meta {{ margin-left:auto; color:var(--muted); white-space:nowrap; }}
main {{ max-width:1440px; margin:0 auto; padding:20px 24px 48px; }}
.summary {{ display:grid; grid-template-columns:repeat(5,minmax(130px,1fr)); gap:1px; border:1px solid var(--line); background:var(--line); margin-bottom:20px; }}
.metric {{ background:#fff; padding:13px 15px; }}
.metric b {{ display:block; font-size:22px; }} .metric span {{ color:var(--muted); }}
.layout {{ display:grid; grid-template-columns:minmax(280px,360px) minmax(0,1fr); border:1px solid var(--line); background:#fff; min-height:680px; }}
.list {{ border-right:1px solid var(--line); }}
.env {{ width:100%; border:0; border-bottom:1px solid var(--line); background:#fff; text-align:left; padding:13px 15px; cursor:pointer; color:var(--ink); }}
.env:hover,.env.active {{ background:#eef4f1; box-shadow:inset 3px 0 var(--green); }}
.env strong {{ display:block; margin-bottom:3px; }} .env small {{ color:var(--muted); display:block; overflow-wrap:anywhere; }}
.badges {{ display:flex; gap:5px; flex-wrap:wrap; margin-top:7px; }}
.badge {{ padding:2px 6px; border:1px solid var(--line); border-radius:3px; font-size:11px; color:#43504a; background:#fafbfa; }}
.badge.rich {{ color:var(--green); border-color:#9bc7b5; }} .badge.warning {{ color:var(--amber); border-color:#dfc17f; }} .badge.error {{ color:var(--red); border-color:#dfaaa5; }}
.detail {{ min-width:0; padding:20px 22px; }}
.detail h2 {{ margin:0 0 4px; font-size:20px; }} .id {{ color:var(--muted); overflow-wrap:anywhere; }}
.overview {{ max-width:1000px; margin:13px 0 18px; }}
.tabs {{ display:flex; border-bottom:1px solid var(--line); margin-bottom:16px; gap:2px; overflow:auto; }}
.tab {{ border:0; background:transparent; padding:9px 11px; color:#4e5a55; cursor:pointer; border-bottom:2px solid transparent; white-space:nowrap; }}
.tab.active {{ color:var(--green); border-color:var(--green); font-weight:650; }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
.panel {{ border:1px solid var(--line); padding:13px 14px; border-radius:4px; min-width:0; overflow-x:auto; }}
.panel h3 {{ font-size:14px; margin:0 0 9px; }}
.panel.full {{ grid-column:1/-1; }}
.facts {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--line); margin:0 0 14px; }}
.fact {{ padding:11px 12px; border-right:1px solid var(--line); }} .fact:last-child {{ border-right:0; }}
.fact b {{ display:block; font-size:18px; }} .fact span {{ color:var(--muted); font-size:12px; }}
.bar-row {{ display:grid; grid-template-columns:minmax(160px,1.2fr) minmax(110px,2fr) 90px; gap:10px; align-items:center; padding:7px 0; border-bottom:1px solid var(--line); }}
.bar-track {{ height:8px; background:#e7ece9; }} .bar-fill {{ height:100%; background:var(--green); min-width:2px; }}
.entity-block,.resource-block {{ border-top:1px solid var(--line); padding:10px 0; }}
.entity-block summary,.resource-block summary {{ cursor:pointer; list-style-position:outside; font-weight:650; }}
.entity-meta {{ color:var(--muted); font-weight:400; margin-left:8px; }}
.lineage {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; padding:10px 0; }}
.node {{ border:1px solid #b9c8c1; background:#f8faf9; padding:6px 8px; border-radius:3px; }}
.arrow {{ color:var(--green); font-weight:700; }}
.type-raw {{ color:#285e8e; }} .type-entity {{ color:#176b4d; }} .type-derived {{ color:#8a5b0a; }} .type-output {{ color:#66716c; }}
ul {{ margin:6px 0 0; padding-left:20px; }} li {{ margin:4px 0; }}
.tree {{ list-style:none; padding:0; margin:0; }} .tree > li {{ border-left:2px solid #a9bdb4; padding:5px 0 5px 13px; margin:0 0 6px; }}
.tree code,.path {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; overflow-wrap:anywhere; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ border-bottom:1px solid var(--line); text-align:left; padding:8px 7px; vertical-align:top; }} th {{ color:#52605a; font-size:12px; background:#f7f9f8; }}
.empty {{ color:var(--muted); padding:30px; text-align:center; }}
.issue {{ border-left:3px solid var(--amber); padding:8px 10px; background:#fff9ec; margin:7px 0; }} .issue.error {{ border-color:var(--red); background:#fff4f3; }}
@media (max-width:900px) {{ .summary {{ grid-template-columns:repeat(2,1fr); }} .summary .metric:last-child:nth-child(odd) {{ grid-column:1/-1; }} .layout {{ grid-template-columns:1fr; }} .list {{ border-right:0; border-bottom:1px solid var(--line); max-height:330px; overflow:auto; }} .grid {{ grid-template-columns:1fr; }} .facts {{ grid-template-columns:repeat(2,1fr); }} .fact:nth-child(2) {{ border-right:0; }} .fact:nth-child(-n+2) {{ border-bottom:1px solid var(--line); }} .bar-row {{ grid-template-columns:minmax(125px,1fr) minmax(70px,1fr) 70px; }} .meta {{ display:none; }} }}
@media (max-width:520px) {{ main {{ padding-left:22px; padding-right:22px; }} .detail {{ padding:18px 20px; }} .tabs {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); overflow:visible; }} .tab {{ width:100%; padding:9px 5px; }} }}
</style>
</head>
<body>
<header><h1>最终环境产物</h1><div class="subtitle">Record Set、关系、Scope 文件、路径索引与来源血缘</div></header>
<div class="toolbar"><input id="search" type="search" placeholder="搜索环境、实体或文件"><select id="filter"><option value="all">全部环境</option><option value="issues">仅有审计提示</option><option value="hybrid">Hybrid</option><option value="file_native">File native</option><option value="structured_records">Structured</option></select><div class="meta" id="meta"></div></div>
<main><section class="summary" id="summary"></section><section class="layout"><nav class="list" id="list"></nav><article class="detail" id="detail"></article></section></main>
<script id="audit-data" type="application/json">{embedded}</script>
<script>
const DATA=JSON.parse(document.getElementById('audit-data').textContent);const VALID_TABS=new Set(['overview','entities','files','provenance']);let selected=null,tab=VALID_TABS.has(location.hash.slice(1))?location.hash.slice(1):'overview';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const arr=v=>Array.isArray(v)?v:[];const badge=(v,c='')=>`<span class="badge ${{c}}">${{esc(v)}}</span>`;
const fmt=n=>new Intl.NumberFormat('zh-CN').format(Number(n||0));
const bytes=n=>{{n=Number(n||0);if(n<1024)return `${{n}} B`;if(n<1048576)return `${{(n/1024).toFixed(1)}} KB`;return `${{(n/1048576).toFixed(1)}} MB`;}};
function filtered(){{const q=document.getElementById('search').value.trim().toLowerCase(),f=document.getElementById('filter').value;return DATA.environments.filter(e=>{{const text=JSON.stringify([e.global_id,e.name,e.overview,e.artifacts]).toLowerCase();return(!q||text.includes(q))&&(f==='all'||(f==='issues'&&e.audit_issues.length)||e.data_shape.final_mode===f);}})}}
function metrics(rows){{const sum=k=>rows.reduce((n,e)=>n+(e.counts[k]||0),0);document.getElementById('summary').innerHTML=[[rows.length,'环境'],[fmt(sum('entity_types')),'Record Set'],[fmt(sum('entity_records')),'记录'],[fmt(sum('relations')),'闭合关系'],[fmt(sum('business_files')),'Scope 文件']].map(x=>`<div class="metric"><b>${{x[0]}}</b><span>${{x[1]}}</span></div>`).join('');}}
function renderList(){{const rows=filtered();metrics(rows);document.getElementById('meta').textContent=`生成于 ${{new Date(DATA.generated_at).toLocaleString()}}`;if(!rows.some(e=>e.global_id===selected))selected=rows[0]?.global_id||null;document.getElementById('list').innerHTML=rows.map(e=>`<button class="env ${{e.global_id===selected?'active':''}}" data-id="${{esc(e.global_id)}}"><strong>${{esc(e.name)}}</strong><small>${{esc(e.global_id)}}</small><div class="badges">${{badge(`${{e.counts.entity_types}} Record Set`)}}${{badge(`${{fmt(e.counts.entity_records)}} 记录`)}}${{badge(`${{e.counts.business_files}} Scope 文件`)}}${{badge(e.quality_tier,e.quality_tier==='rich'?'rich':'')}}${{e.field_review?.finding_count?badge(`字段复核 ${{e.field_review.complete?'完成':'待处理'}}`,e.field_review.complete?'rich':'warning'):''}}${{e.audit_issues.length?badge(`${{e.audit_issues.length}} 提示`,'warning'):badge('校验通过','rich')}}</div></button>`).join('')||'<div class="empty">没有匹配环境</div>';document.querySelectorAll('.env').forEach(b=>b.onclick=()=>{{selected=b.dataset.id;tab='overview';renderList();renderDetail();}});renderDetail();}}
function fact(value,label){{return `<div class="fact"><b>${{esc(value)}}</b><span>${{esc(label)}}</span></div>`}}
function overview(e){{const max=Math.max(1,...e.artifacts.entities.map(x=>x.record_count));const bars=e.artifacts.entities.map(x=>`<div class="bar-row"><div><b>${{esc(x.entity_type)}}</b><br><small>${{esc(x.name)}}</small></div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(1,x.record_count/max*100)}}%"></div></div><div>${{fmt(x.record_count)}} 条</div></div>`).join('');const kinds=['entity','filesystem_scope'].map(t=>{{const rs=e.artifacts.resources.filter(x=>x.data_type===t),fc=rs.reduce((n,x)=>n+x.file_count,0),bs=rs.reduce((n,x)=>n+x.bytes,0),label=t==='filesystem_scope'?'Filesystem Scope':'Record Set';return `<tr><td class="type-${{t}}"><b>${{esc(label)}}</b></td><td>${{rs.length}}</td><td>${{fc}}</td><td>${{bytes(bs)}}</td></tr>`}}).join('');return `<div class="facts">${{fact(e.counts.entity_types,'Record Set')}}${{fact(fmt(e.counts.entity_records),'记录')}}${{fact(e.counts.relations,'闭合关系')}}${{fact(`${{e.counts.business_files}} / ${{bytes(e.counts.file_bytes)}}`,'Scope 文件 / 大小')}}</div><div class="grid"><section class="panel full"><h3>Record Set 记录构成</h3>${{bars||'<div class="empty">无 Record Set</div>'}}</section><section class="panel"><h3>任务侧数据构成</h3><table><tr><th>类型</th><th>资源</th><th>实际文件</th><th>大小</th></tr>${{kinds}}</table></section><section class="panel"><h3>最终状态</h3><div class="badges">${{badge(`数据形态: ${{e.data_shape.final_mode}}`)}}${{badge(`package: ${{e.checkpoint_status}}`,e.checkpoint_status==='ready'?'rich':'error')}}${{badge(`quality: ${{e.quality_tier}}`,e.quality_tier==='rich'?'rich':'')}}${{badge(`validation: ${{e.validation_valid?'passed':'failed'}}`,e.validation_valid?'rich':'error')}}</div><p>${{esc(e.data_shape.final_rationale)}}</p><div class="badges">${{e.data_shape.indexed_formats.map(x=>badge(`记录已索引 ${{x}} 文件`,'rich')).join('')}}</div></section></div>`}}
function entities(e){{const blocks=e.artifacts.entities.map(x=>`<details class="entity-block"><summary>${{esc(x.entity_type)}} <span class="entity-meta">${{fmt(x.record_count)}} 条记录 · ${{x.field_count}} 个字段 · ${{esc(x.resource_path)}}</span></summary><p>${{esc(x.description)}}</p><div class="badges">${{x.primary_keys.map(k=>badge(`主键候选 ${{k}}`,'rich')).join('')}}${{arr(x.review_findings).length?badge(`${{arr(x.review_findings).length}} 个字段复核提示`,'warning'):''}}</div>${{arr(x.review_findings).map(f=>`<div class="issue warning"><b>${{esc(f.finding_id)}}</b><br>${{esc(f.message)}}<br><small>${{esc(f.action)}}</small></div>`).join('')}}<table><tr><th>字段</th><th>类型</th><th>非空率</th><th>不同值</th><th>头部值（次数）</th><th>角色</th><th>说明</th></tr>${{x.fields.map(f=>`<tr><td><code>${{esc(f.name)}}</code></td><td>${{esc(f.type)}}</td><td>${{f.non_null_ratio==null?'—':`${{(f.non_null_ratio*100).toFixed(0)}}%`}}</td><td>${{f.distinct_count??'—'}}</td><td>${{arr(f.top_values).slice(0,3).map(v=>`<code>${{esc(v.value)}}</code> (${{fmt(v.count)}})`).join('<br>')||'—'}}</td><td>${{f.roles.map(r=>badge(r,r==='file_reference'?'rich':'')).join('')}}</td><td>${{esc(f.description)}}</td></tr>`).join('')}}</table></details>`).join('');const rel=e.artifacts.relations.map(r=>`<tr><td><code>${{esc(r.from_entity)}}.${{esc(r.field)}}</code></td><td>→</td><td><code>${{esc(r.to_entity)}}.${{esc(r.target_field)}}</code></td><td>${{fmt(r.edge_count)}} 条边</td><td>${{esc(r.description)}}</td></tr>`).join('');return `<div class="grid"><section class="panel full"><h3>最终实体 Schema、分布与复核</h3>${{blocks||'<div class="empty">无实体</div>'}}</section><section class="panel full"><h3>已闭合关系</h3>${{rel?`<table><tr><th>来源字段</th><th></th><th>目标字段</th><th>规模</th><th>证据</th></tr>${{rel}}</table>`:'<div class="empty">当前环境没有检测到闭合关系</div>'}}</section></div>`}}
function files(e){{const indexes=e.artifacts.file_indexes.map(x=>`<div class="lineage"><span class="node"><b>${{esc(x.entity_type)}}.${{esc(x.field)}}</b><br>${{fmt(x.indexed_path_count)}} 个不同路径</span><span class="arrow">→</span>${{x.source_resource_ids.map(id=>`<span class="node"><b>${{esc(e.artifacts.resource_names[id]||id)}}</b><br>${{esc(id)}}</span>`).join('<span class="arrow">+</span>')}}<span>${{x.formats.map(v=>badge(v,'rich')).join('')}}</span></div>`).join('');const resources=e.artifacts.resources.filter(x=>x.data_type!=='raw').map(x=>{{const shown=x.files.slice(0,30),remaining=x.files.length-shown.length;return `<details class="resource-block"><summary><span class="type-${{esc(x.data_type)}}">${{esc(x.data_type)}}</span> · ${{esc(x.name)}} <span class="entity-meta">${{x.file_count}} 个文件 · ${{bytes(x.bytes)}} · ${{esc(x.format)}}</span></summary><p>${{esc(x.description)}}</p><div class="lineage"><span class="node"><b>${{esc(x.resource_id)}}</b><br><span class="path">${{esc(x.path)}}</span></span>${{x.source_resources.length?`<span class="arrow">←</span>${{x.source_resources.map(id=>`<span class="node">${{esc(e.artifacts.resource_names[id]||id)}}</span>`).join('<span class="arrow">+</span>')}}`:''}}</div>${{shown.length?`<table><tr><th>实际文件</th><th>格式</th><th>大小</th><th>记录</th></tr>${{shown.map(f=>`<tr><td class="path">${{esc(f.path)}}</td><td>${{esc(f.format)}}</td><td>${{bytes(f.bytes)}}</td><td>${{f.record_count??'—'}}</td></tr>`).join('')}}</table>${{remaining?`<div class="empty">另有 ${{remaining}} 个文件，已计入资源总量</div>`:''}}`:'<div class="empty">SQLite Record Set 或无独立文件</div>'}}</details>`}}).join('');return `<div class="grid"><section class="panel full"><h3>Record 到真实文件的索引</h3>${{indexes||'<div class="empty">该环境没有 Record 到 Scope 的路径索引</div>'}}</section><section class="panel full"><h3>任务侧数据与实际文件</h3>${{resources}}</section></div>`}}
function provenance(e){{const sourceRows=e.sources.map(x=>`<tr><td><code>${{esc(x.source_id)}}</code></td><td class="path">${{esc(x.url)}}</td><td>${{x.file_count}}</td><td>${{esc(x.resource_ids.join(', '))}}</td></tr>`).join('');return `<div class="grid"><section class="panel full"><h3>最终公开来源</h3><table><tr><th>来源</th><th>URL</th><th>Raw 文件</th><th>资源</th></tr>${{sourceRows}}</table></section><section class="panel"><h3>发布状态</h3><div class="badges">${{badge(`checkpoint: ${{e.checkpoint_status}}`,e.checkpoint_status==='ready'?'rich':'error')}}${{badge(`finalize: ${{e.finalization_result}}`)}}${{badge(`quality: ${{e.quality_tier}}`,e.quality_tier==='rich'?'rich':'')}}${{badge(`validation: ${{e.validation_valid?'passed':'failed'}}`,e.validation_valid?'rich':'error')}}</div></section><section class="panel"><h3>独立审计</h3>${{e.audit_issues.length?e.audit_issues.map(x=>`<div class="issue ${{esc(x.severity)}}"><b>${{esc(x.code)}}</b><br>${{esc(x.message)}}</div>`).join(''):'<div class="empty">文件清单、来源哈希和最终校验均未发现问题</div>'}}</section></div>`}}
function renderDetail(){{const e=DATA.environments.find(x=>x.global_id===selected),el=document.getElementById('detail');if(!e){{el.innerHTML='<div class="empty">请选择环境</div>';return}}const tabs=[['overview','环境总览'],['entities','记录与关系'],['files','文件与索引'],['provenance','来源与校验']];const body={{overview,entities,files,provenance}}[tab](e);el.innerHTML=`<h2>${{esc(e.name)}}</h2><div class="id">${{esc(e.global_id)}}</div><p class="overview">${{esc(e.overview)}}</p><div class="tabs">${{tabs.map(x=>`<button class="tab ${{tab===x[0]?'active':''}}" data-tab="${{x[0]}}">${{x[1]}}</button>`).join('')}}</div>${{body}}`;document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{{tab=b.dataset.tab;history.replaceState(null,'',`#${{tab}}`);renderDetail();}})}}
window.onhashchange=()=>{{const next=location.hash.slice(1);if(VALID_TABS.has(next)){{tab=next;renderDetail();}}}};document.getElementById('search').oninput=renderList;document.getElementById('filter').onchange=renderList;renderList();
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="生成可直接双击打开的最终环境产物 HTML"
    )
    parser.add_argument(
        "environment_dirs",
        nargs="+",
        type=Path,
        help="已发布环境目录；每个目录应包含 provenance/scenario_research.json",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    missing = [
        path
        for path in arguments.environment_dirs
        if not (path / "provenance/scenario_research.json").is_file()
    ]
    if missing:
        raise SystemExit(
            "这些目录缺少 scenario_research.json："
            + ", ".join(str(path) for path in missing)
        )
    payload = build_payload(arguments.environment_dirs)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(_document(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(arguments.output.resolve()),
                "environment_count": payload["environment_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
