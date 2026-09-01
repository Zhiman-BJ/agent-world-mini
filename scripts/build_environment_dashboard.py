#!/usr/bin/env python3
"""Build a compact, browser-friendly snapshot of the published environments.

The environment packages remain the source of truth.  This script only creates
dashboard data: resource/file counts, entity samples, relationship evidence,
quality metrics and a small set of deterministic audit signals.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ENV_ROOT = Path(
    "/mnt/oss-bucket/sunshuo/AgentWorld/env_without_tools/"
    "data_gen_high_quality_3env_20260828_v2/rich"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "dashboard" / "data" / "environments.json"


def read_json(path: Path, *, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return default


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_for_resource(workspace: Path, resource: dict[str, Any]) -> list[Path]:
    path = resource.get("path")
    storage = resource.get("storage_type")
    if not isinstance(path, str):
        return []
    if storage == "file_collection":
        return sorted(
            candidate
            for candidate in workspace.glob(path)
            if candidate.is_file()
        )
    target = workspace / path
    if storage == "directory":
        return sorted(candidate for candidate in target.rglob("*") if candidate.is_file()) if target.is_dir() else []
    return [target] if target.is_file() else []


def file_info(workspace: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(workspace).as_posix()
    return {
        "path": relative,
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "extension": path.suffix.lower().lstrip("."),
    }


def scalar_sample(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def bounded_preview(value: Any, *, depth: int = 0) -> Any:
    """Return a small JSON-safe preview without copying an entire raw response."""

    if depth >= 3:
        if isinstance(value, dict):
            return f"<{len(value)} keys>"
        if isinstance(value, list):
            return f"<{len(value)} items>"
    if isinstance(value, dict):
        return {
            str(key): bounded_preview(item, depth=depth + 1)
            for key, item in list(value.items())[:10]
        }
    if isinstance(value, list):
        return [bounded_preview(item, depth=depth + 1) for item in value[:2]]
    if isinstance(value, str) and len(value) > 240:
        return value[:237] + "..."
    return value


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def json_content_summary(path: Path) -> dict[str, Any]:
    """Describe the real JSON container and its likely record-bearing arrays."""

    payload = read_json(path, default=None)
    if payload is None:
        return {
            "kind": "json",
            "valid": False,
            "summary": "文件无法解析为 JSON。",
            "structure": [],
            "preview": None,
        }

    root_type = json_type(payload)
    structure: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    record_count: int | None = None
    preview: Any = payload

    if isinstance(payload, dict):
        for key, value in list(payload.items())[:16]:
            value_type = json_type(value)
            size = len(value) if isinstance(value, (dict, list, str)) else None
            structure.append({"name": str(key), "type": value_type, "count": size})
        candidate_arrays = [value for value in payload.values() if isinstance(value, list)]
        record_count = max((len(value) for value in candidate_arrays), default=None)
        for key in ("meta", "metadata", "pagination"):
            if isinstance(payload.get(key), dict):
                metadata = bounded_preview(payload[key])
                break
        preferred = next(
            (payload.get(key) for key in ("results", "data", "items", "records") if isinstance(payload.get(key), list)),
            None,
        )
        if preferred:
            preview = preferred[0]
    elif isinstance(payload, list):
        structure = [
            {
                "name": f"[{index}]",
                "type": json_type(value),
                "count": len(value) if isinstance(value, (dict, list, str)) else None,
            }
            for index, value in enumerate(payload[:8])
        ]
        if payload and isinstance(payload[0], dict):
            metadata = bounded_preview(payload[0])
        candidate_arrays = [value for value in payload if isinstance(value, list)]
        record_count = max((len(value) for value in candidate_arrays), default=len(payload))
        if candidate_arrays and candidate_arrays[0]:
            preview = candidate_arrays[0][0]
        elif payload:
            preview = payload[0]

    record_phrase = f"，主要记录数组 {record_count:,} 项" if record_count is not None else ""
    return {
        "kind": "json",
        "valid": True,
        "root_type": root_type,
        "record_count": record_count,
        "summary": f"有效 JSON，根节点为 {root_type}{record_phrase}。",
        "structure": structure,
        "metadata": metadata,
        "preview": bounded_preview(preview),
    }


def html_content_summary(path: Path) -> dict[str, Any]:
    """Extract inspection-oriented facts from an HTML source snapshot."""

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"kind": "html", "valid": False, "summary": "文件无法读取。"}
    title_match = re.search(r"<title[^>]*>(.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else "未提供标题"
    without_programs = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    visible_text = html.unescape(re.sub(r"<[^>]+>", " ", without_programs))
    visible_text = re.sub(r"\s+", " ", visible_text).strip()
    return {
        "kind": "html",
        "valid": True,
        "title": title,
        "text_characters": len(visible_text),
        "summary": f"HTML 来源快照，页面标题为“{title}”，约 {len(visible_text):,} 个可见文本字符。",
        "structure": [],
        "preview": visible_text[:360] + ("..." if len(visible_text) > 360 else ""),
    }


def content_summary(path: Path, format_name: str) -> dict[str, Any]:
    if format_name == "json" or path.suffix.lower() == ".json":
        return json_content_summary(path)
    if format_name == "html" or path.suffix.lower() in {".html", ".htm"}:
        return html_content_summary(path)
    return {
        "kind": format_name or path.suffix.lower().lstrip(".") or "file",
        "valid": True,
        "summary": "保留该格式的原始文件；仪表盘未对其内容做字段级解析。",
        "structure": [],
        "preview": None,
    }


def non_entity_usage(data_type: str) -> str:
    if data_type == "raw":
        return "用于核对真实来源、审计采集结果或重新抽取 Entity；常规业务查询应优先读取规范实体。"
    if data_type == "derived":
        return "可供工具直接读取以加速统计或索引查询；Entity 变化后必须从声明的上游资源重新计算。"
    return "这是后续工具写入报告、导出文件和任务中间结果的位置，不是当前业务输入。"


def entity_records(workspace: Path, resource: dict[str, Any], files: list[Path]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        str(entity_type): [] for entity_type in (resource.get("entity_schema") or {})
    }
    format_name = str(resource.get("format") or "")
    for path in files:
        try:
            if format_name == "json":
                payload = read_json(path, default={})
                if not isinstance(payload, dict):
                    continue
                for entity_type in result:
                    records = payload.get(entity_type, [])
                    if isinstance(records, list):
                        result[entity_type].extend(
                            item for item in records if isinstance(item, dict)
                        )
            elif format_name == "jsonl":
                entity_type = next(iter(result), None)
                if entity_type:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        item = json.loads(line)
                        if isinstance(item, dict):
                            result[entity_type].append(item)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return result


def entity_snapshot(
    workspace: Path,
    resources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    records_by_type: dict[str, list[dict[str, Any]]] = {}
    entity_rows: list[dict[str, Any]] = []
    for resource in resources:
        if resource.get("data_type") != "entity":
            continue
        files = files_for_resource(workspace, resource)
        groups = entity_records(workspace, resource, files)
        for entity_type, records in groups.items():
            records_by_type.setdefault(entity_type, []).extend(records)
            definition = (resource.get("entity_schema") or {}).get(entity_type, {})
            field_defs = definition.get("fields", {}) if isinstance(definition, dict) else {}
            if not isinstance(field_defs, dict):
                field_defs = {}
            entity_rows.append(
                {
                    "entity_type": entity_type,
                    "resource_id": resource.get("resource_id"),
                    "description": definition.get("description", "") if isinstance(definition, dict) else "",
                    "record_count": len(records),
                    "field_count": len(field_defs),
                    "fields": [
                        {
                            "name": field,
                            "type": spec.get("type") if isinstance(spec, dict) else str(spec),
                            "description": spec.get("description", "") if isinstance(spec, dict) else "",
                            "distinct_count": len(
                                {
                                    json.dumps(record.get(field), ensure_ascii=False, sort_keys=True)
                                    for record in records
                                    if field in record and record.get(field) is not None
                                }
                            ),
                            "sample_values": [
                                scalar_sample(value)
                                for value in list({
                                    json.dumps(record.get(field), ensure_ascii=False, sort_keys=True): record.get(field)
                                    for record in records
                                    if field in record and record.get(field) is not None
                                }.values())[:3]
                            ],
                        }
                        for field, spec in field_defs.items()
                    ],
                    "samples": [
                        {key: scalar_sample(value) for key, value in record.items()}
                        for record in records[:3]
                    ],
                }
            )
    # Multiple resources may expose the same entity type.  The UI should show
    # one row with the total count, while retaining the first schema/sample.
    merged: dict[str, dict[str, Any]] = {}
    for row in entity_rows:
        entity_type = row["entity_type"]
        if entity_type not in merged:
            merged[entity_type] = dict(row)
        else:
            current = merged[entity_type]
            current["record_count"] += row["record_count"]
            if not current.get("samples"):
                current["samples"] = row.get("samples", [])
    return sorted(merged.values(), key=lambda item: item["record_count"], reverse=True), records_by_type


def duplicate_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hash: dict[str, list[str]] = {}
    for item in files:
        by_hash.setdefault(item["sha256"], []).append(item["path"])
    return [
        {"sha256": digest, "paths": paths, "bytes": next(item["bytes"] for item in files if item["sha256"] == digest)}
        for digest, paths in by_hash.items()
        if len(paths) > 1
    ]


def non_entity_snapshot(
    workspace: Path,
    resources: list[dict[str, Any]],
    source_urls_by_resource: dict[str, list[str]],
    source_urls_by_file: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Build one explanatory row per Raw/Derived file and Output target."""

    rows: list[dict[str, Any]] = []
    for resource in resources:
        data_type = str(resource.get("data_type") or "")
        if data_type not in {"raw", "derived", "output"}:
            continue
        resource_id = str(resource.get("resource_id") or "")
        matched = files_for_resource(workspace, resource)
        declared_target = workspace / str(resource.get("path") or "")
        target_exists = declared_target.exists() if resource.get("storage_type") != "file_collection" else bool(matched)
        targets: list[Path | None] = list(matched) or [None]
        for path in targets:
            relative_path = path.relative_to(workspace).as_posix() if path else str(resource.get("path") or "")
            info = file_info(workspace, path) if path else {
                "path": relative_path,
                "name": Path(relative_path).name or relative_path,
                "bytes": 0,
                "sha256": None,
                "extension": "",
            }
            if path:
                summary = content_summary(path, str(resource.get("format") or ""))
            else:
                is_directory = resource.get("storage_type") == "directory"
                summary = {
                    "kind": "directory" if is_directory else "missing",
                    "valid": is_directory and target_exists,
                    "summary": (
                        "目录当前存在且为空，已作为后续工具的可写结果位置保留。"
                        if is_directory and target_exists
                        else "契约声明了该输出目录，但该目录当前尚未建立。"
                        if is_directory
                        else "契约声明了该文件，但当前没有匹配到落盘文件。"
                    ),
                    "structure": [],
                    "preview": None,
                }
            exact_urls = source_urls_by_file.get(relative_path, [])
            rows.append(
                {
                    **info,
                    "resource_id": resource_id,
                    "resource_name": resource.get("name"),
                    "resource_description": resource.get("description"),
                    "data_type": data_type,
                    "storage_type": resource.get("storage_type"),
                    "format": resource.get("format"),
                    "writable": resource.get("writable"),
                    "exists": path is not None,
                    "target_exists": target_exists,
                    "directory_file_count": len(matched) if resource.get("storage_type") == "directory" else None,
                    "source_resources": resource.get("source_resources", []),
                    "source_urls": exact_urls or source_urls_by_resource.get(resource_id, []),
                    "usage": non_entity_usage(data_type),
                    "content_summary": summary,
                }
            )
    order = {"raw": 0, "derived": 1, "output": 2}
    return sorted(rows, key=lambda item: (order.get(str(item["data_type"]), 9), str(item["path"])))


def build_environment(package: Path) -> dict[str, Any]:
    environment = read_json(package / "environment.json", default={}) or {}
    validation = read_json(package / "validation.json", default={}) or {}
    quality = read_json(package / "provenance/quality_profile.json", default={}) or {}
    report = read_json(package / "provenance/research_report.json", default={}) or {}
    inventory = read_json(package / "provenance/source_inventory.json", default={}) or {}
    sources = read_json(package / "provenance/sources.json", default={}) or {}
    workspace = package / "workspace"

    all_raw_files = [
        file_info(workspace, path)
        for path in sorted((workspace / "raw").rglob("*"))
        if path.is_file()
    ] if (workspace / "raw").is_dir() else []
    all_workspace_files = [
        file_info(workspace, path)
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    ] if workspace.is_dir() else []
    entity_rows, records_by_type = entity_snapshot(
        workspace,
        [item for item in environment.get("resources", []) if isinstance(item, dict)],
    )

    declared_resources = [item for item in environment.get("resources", []) if isinstance(item, dict)]
    resource_rows: list[dict[str, Any]] = []
    source_urls_by_resource: dict[str, list[str]] = {}
    source_urls_by_file: dict[str, list[str]] = {}
    for source in sources.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            continue
        for resource_id in source.get("resource_ids", []):
            if isinstance(resource_id, str):
                source_urls_by_resource.setdefault(resource_id, []).append(source["url"])
        for source_file in source.get("files", []):
            if isinstance(source_file, dict) and isinstance(source_file.get("path"), str):
                source_urls_by_file.setdefault(source_file["path"], []).append(source["url"])
    for item in declared_resources:
        matched = files_for_resource(workspace, item)
        resource_rows.append(
            {
                "resource_id": item.get("resource_id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "data_type": item.get("data_type"),
                "storage_type": item.get("storage_type"),
                "path": item.get("path"),
                "format": item.get("format"),
                "writable": item.get("writable"),
                "source_resources": item.get("source_resources", []),
                "source_urls": source_urls_by_resource.get(str(item.get("resource_id")), []),
                "entity_types": sorted((item.get("entity_schema") or {}).keys()),
                "field_count": sum(
                    len(definition.get("fields", {}))
                    for definition in (item.get("entity_schema") or {}).values()
                    if isinstance(definition, dict)
                ),
                "file_count": len(matched),
                "bytes": sum(path.stat().st_size for path in matched),
                "files": [file_info(workspace, path) for path in matched[:30]],
            }
        )

    non_entity_files = non_entity_snapshot(
        workspace,
        declared_resources,
        source_urls_by_resource,
        source_urls_by_file,
    )

    resource_type_counts = {
        data_type: len([item for item in resource_rows if item.get("data_type") == data_type])
        for data_type in ("raw", "entity", "derived", "output")
    }
    lineage_edges = [
        {
            "from_resource": source_id,
            "to_resource": item["resource_id"],
        }
        for item in resource_rows
        for source_id in item.get("source_resources", [])
        if isinstance(source_id, str)
    ]

    relations = []
    for relation in quality.get("relation_profile", {}).get("relations", []):
        if not isinstance(relation, dict):
            continue
        relations.append(
            {
                "relation_id": relation.get("relation_id"),
                "from_entity": relation.get("from_entity"),
                "field": relation.get("field"),
                "to_entity": relation.get("to_entity"),
                "target_field": relation.get("target_field"),
                "edge_count": relation.get("edge_count", 0),
                "description": relation.get("description", ""),
            }
        )
    if not relations:
        relations = [
            {
                "relation_id": item.get("relation_id"),
                "from_entity": item.get("from_entity"),
                "field": item.get("field"),
                "to_entity": item.get("to_entity"),
                "target_field": item.get("target_field"),
                "edge_count": 0,
                "description": item.get("description", ""),
            }
            for item in report.get("relations", [])
            if isinstance(item, dict)
        ]

    capabilities = [
        {
            "capability_id": item.get("capability_id"),
            "description": item.get("description", ""),
            "operation_family": item.get("operation_family", "other"),
        }
        for item in report.get("extensions", [])
        if isinstance(item, dict)
    ]
    operation_families = quality.get("capability_profile", {}).get("operation_families", [])
    if isinstance(operation_families, dict):
        operation_families = list(operation_families)
    operation_families = sorted({str(item) for item in operation_families})

    surfaces = []
    for surface in inventory.get("surfaces", []):
        if not isinstance(surface, dict):
            continue
        pagination = surface.get("pagination") or {}
        surfaces.append(
            {
                "surface_id": surface.get("surface_id"),
                "name": surface.get("name"),
                "url": surface.get("url"),
                "priority": surface.get("priority"),
                "kind": surface.get("kind"),
                "entities": surface.get("entities", []),
                "collection_mode": surface.get("collection_mode"),
                "collection_status": surface.get("collection_status"),
                "records_collected": surface.get("records_collected", 0),
                "raw_file_count": len(surface.get("raw_files", [])),
                "raw_files": surface.get("raw_files", []),
                "pages_collected": pagination.get("pages_collected"),
                "reported_total": pagination.get("reported_total"),
                "exhaustion_evidence": surface.get("exhaustion_evidence"),
            }
        )

    warnings: list[dict[str, str]] = []
    if validation.get("valid") is not True:
        warnings.append({"severity": "critical", "code": "validation_failed", "message": "环境没有通过最终验证。"})
    for duplicate in duplicate_files(all_raw_files):
        warnings.append(
            {
                "severity": "warning",
                "code": "duplicate_raw_file",
                "message": f"检测到内容相同的 raw 文件：{'、'.join(duplicate['paths'])}",
            }
        )
    if len(all_raw_files) <= 5:
        warnings.append(
            {
                "severity": "info",
                "code": "narrow_raw_file_count",
                "message": f"当前只有 {len(all_raw_files)} 个 raw 文件；单个文件可能是大批量响应，文件数不等于记录数。",
            }
        )
    if len(surfaces) < 5:
        warnings.append(
            {
                "severity": "info",
                "code": "narrow_surface_count",
                "message": f"当前登记了 {len(surfaces)} 个独立数据面，业务覆盖面相对集中。",
            }
        )
    program_suffixes = {".py", ".js", ".ts", ".sh", ".sql", ".java", ".go", ".rs"}
    leaked = [item["path"] for item in all_workspace_files if Path(item["path"]).suffix.lower() in program_suffixes and item["path"].split("/", 1)[0] in {"entities", "derived"}]
    if leaked:
        warnings.append({"severity": "critical", "code": "program_in_business_data", "message": f"业务数据目录中存在程序文件：{'、'.join(leaked)}"})

    records_total = sum(int(row["record_count"]) for row in entity_rows)
    quality_composition = quality.get("composition_profile", {})
    return {
        "environment_id": environment.get("environment_id", package.name),
        "name": environment.get("name", package.name),
        "description": environment.get("description", ""),
        "quality_tier": quality.get("quality_tier", "unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation": {
            "valid": validation.get("valid", False),
            "errors": validation.get("errors", []),
            "warnings": validation.get("warnings", []),
            "validated_at": validation.get("validated_at"),
        },
        "metrics": {
            "raw_file_count": len(all_raw_files),
            "raw_bytes": sum(item["bytes"] for item in all_raw_files),
            "raw_record_count": sum(int(item.get("records_collected", 0) or 0) for item in surfaces),
            "resource_count": len(resource_rows),
            "surface_count": len(surfaces),
            "source_count": len(sources.get("sources", [])),
            "entity_type_count": len(entity_rows),
            "entity_record_count": records_total,
            "closed_relation_count": int(quality.get("relation_profile", {}).get("closed_relation_count", len(relations)) or 0),
            "relation_gap_count": int(quality.get("relation_profile", {}).get("relation_gap_count", 0) or 0),
            "capability_count": int(quality.get("capability_profile", {}).get("capability_atom_count", len(capabilities)) or 0),
            "operation_family_count": len(operation_families),
            "chain_shape_count": int(quality_composition.get("chain_shape_count", 0) or 0),
            "task_instance_count": int(quality_composition.get("estimated_task_instances", 0) or 0),
            "resource_type_counts": resource_type_counts,
        },
        "resources": resource_rows,
        "non_entity_files": non_entity_files,
        "entities": entity_rows,
        "relations": relations,
        "relation_gaps": quality.get("relation_profile", {}).get("relation_gaps", report.get("relation_gaps", [])),
        "capabilities": capabilities,
        "operation_families": operation_families,
        "dimensions": [item for item in report.get("dimensions", []) if isinstance(item, dict)],
        "surfaces": surfaces,
        "coverage": [item for item in report.get("coverage", []) if isinstance(item, dict)],
        "warnings": warnings,
        "rules": environment.get("rules", []),
        "lineage_edges": lineage_edges,
        "files": all_workspace_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AgentWorld environment dashboard data")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_ENV_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    environments = [
        build_environment(package)
        for package in sorted(args.source_root.iterdir())
        if package.is_dir() and (package / "environment.json").is_file()
    ]
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(args.source_root.resolve()),
        "environments": environments,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(environments)} environments to {args.output}")


if __name__ == "__main__":
    main()
