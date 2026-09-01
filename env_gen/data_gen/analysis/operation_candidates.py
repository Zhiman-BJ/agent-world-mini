from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from env_gen.data_gen.analysis.record_extraction import (
    _infer_relations,
)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "operation_candidate"


def infer_closed_relations(
    entity_groups: dict[str, list[dict[str, Any]]],
    entity_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """复用环境编译器的闭合关系推断，并补充可遍历容量统计。"""

    entity_fields = {
        entity_type: {
            field: str(field_profile.get("type") or "string")
            for field, field_profile in profile.get("fields", {}).items()
        }
        for entity_type, profile in entity_profiles.items()
    }
    relations = _infer_relations(entity_groups, entity_fields)
    enriched: list[dict[str, Any]] = []
    for relation in relations:
        source_records = entity_groups.get(relation["from_entity"], [])
        target_records = entity_groups.get(relation["to_entity"], [])
        source_field = relation["field"]
        target_field = relation["target_field"]
        target_values = {
            str(record.get(target_field))
            for record in target_records
            if record.get(target_field) not in (None, "")
        }
        edges = [
            str(record.get(source_field))
            for record in source_records
            if record.get(source_field) not in (None, "")
        ]
        enriched.append(
            {
                **relation,
                "edge_count": len(edges),
                "source_record_count": len(source_records),
                "target_cardinality": len(
                    {value for value in edges if value in target_values}
                ),
            }
        )
    return enriched


def _candidate(
    candidate_id: str,
    operation_family: str,
    *,
    input_kind: str,
    output_kind: str,
    evidence: list[str],
    support_count: int,
    parameter_cardinality: int,
    description: str,
) -> dict[str, Any]:
    return {
        "candidate_id": _slug(candidate_id),
        "operation_family": operation_family,
        "input_kind": input_kind,
        "output_kind": output_kind,
        "evidence": evidence,
        "support_count": max(0, int(support_count)),
        "parameter_cardinality": max(0, int(parameter_cardinality)),
        "description": description,
    }


def build_operation_candidates(
    entity_profiles: dict[str, dict[str, Any]],
    file_profiles: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从实际字段、文件格式和闭合关系中提取候选操作证据。"""

    candidates: list[dict[str, Any]] = []
    for entity_type, profile in sorted(entity_profiles.items()):
        record_count = int(profile.get("record_count", 0))
        fields = profile.get("fields", {})
        business_fields = [
            field
            for field, field_profile in fields.items()
            if not set(field_profile.get("roles", [])).issubset({"identifier"})
        ]
        if business_fields:
            candidates.append(
                _candidate(
                    f"inspect_{entity_type}",
                    "inspect",
                    input_kind=entity_type,
                    output_kind=entity_type,
                    evidence=[f"{entity_type}.{field}" for field in business_fields[:3]],
                    support_count=record_count,
                    parameter_cardinality=record_count,
                    description=f"按稳定标识读取 {entity_type} 的真实业务属性。",
                )
            )
            candidates.append(
                _candidate(
                    f"list_{entity_type}",
                    "list",
                    input_kind=entity_type,
                    output_kind=entity_type,
                    evidence=[f"{entity_type}.{field}" for field in business_fields[:2]],
                    support_count=record_count,
                    parameter_cardinality=record_count,
                    description=f"列出并分页浏览 {entity_type}。",
                )
            )
        for field, field_profile in sorted(fields.items()):
            roles = set(field_profile.get("roles", []))
            distinct = int(field_profile.get("distinct_count", 0))
            support = int(field_profile.get("non_null_count", 0))
            evidence = [f"{entity_type}.{field}"]
            if "file_reference" in roles:
                for format_name in field_profile.get("file_formats", []) or ["binary"]:
                    candidates.append(
                        _candidate(
                            f"resolve_{entity_type}_{field}_{format_name}",
                            "resolve_file",
                            input_kind=entity_type,
                            output_kind=f"file:{format_name}",
                            evidence=[*evidence, f"format:{format_name}"],
                            support_count=support,
                            parameter_cardinality=distinct,
                            description=(
                                f"由 {entity_type}.{field} 定位实际 {format_name} 文件。"
                            ),
                        )
                    )
            if "text" in roles or "display" in roles:
                candidates.append(
                    _candidate(
                        f"search_{entity_type}_by_{field}",
                        "search",
                        input_kind=entity_type,
                        output_kind=entity_type,
                        evidence=evidence,
                        support_count=support,
                        parameter_cardinality=distinct,
                        description=f"使用 {entity_type}.{field} 检索真实记录。",
                    )
                )
            if "category" in roles:
                candidates.append(
                    _candidate(
                        f"filter_{entity_type}_by_{field}",
                        "filter",
                        input_kind=entity_type,
                        output_kind=entity_type,
                        evidence=evidence,
                        support_count=support,
                        parameter_cardinality=distinct,
                        description=f"按 {entity_type}.{field} 的真实类别筛选。",
                    )
                )
            if "numeric_measure" in roles:
                for family in ("rank", "compare", "aggregate"):
                    candidates.append(
                        _candidate(
                            f"{family}_{entity_type}_by_{field}",
                            family,
                            input_kind=entity_type,
                            output_kind=entity_type,
                            evidence=evidence,
                            support_count=support,
                            parameter_cardinality=distinct,
                            description=f"对 {entity_type}.{field} 执行 {family}。",
                        )
                    )
            if "temporal" in roles:
                candidates.append(
                    _candidate(
                        f"timeline_{entity_type}_by_{field}",
                        "timeline",
                        input_kind=entity_type,
                        output_kind=entity_type,
                        evidence=evidence,
                        support_count=support,
                        parameter_cardinality=distinct,
                        description=f"沿 {entity_type}.{field} 分析时间变化。",
                    )
                )

    for relation in relations:
        source = str(relation["from_entity"])
        target = str(relation["to_entity"])
        evidence = [
            f"{source}.{relation['field']}",
            f"{target}.{relation['target_field']}",
        ]
        edge_count = int(relation.get("edge_count", 0))
        cardinality = int(relation.get("target_cardinality", 0))
        candidates.append(
            _candidate(
                f"traverse_{source}_to_{target}",
                "traverse",
                input_kind=source,
                output_kind=target,
                evidence=evidence,
                support_count=edge_count,
                parameter_cardinality=cardinality,
                description=f"沿闭合关系 {source}.{relation['field']} 访问 {target}。",
            )
        )
        candidates.append(
            _candidate(
                f"reverse_traverse_{target}_to_{source}",
                "traverse",
                input_kind=target,
                output_kind=source,
                evidence=evidence,
                support_count=edge_count,
                parameter_cardinality=cardinality,
                description=f"从 {target} 反查引用它的 {source} 记录。",
            )
        )

    # 文件候选操作按格式合并。同一格式的一百个文件会增加 support_count，
    # 但不会凭空制造一百份重复候选。
    files_by_format: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in file_profiles:
        if profile.get("bucket") in {"raw", "derived"}:
            files_by_format[str(profile.get("format") or "binary")].append(profile)
    role_family = {
        "inspect": "inspect",
        "copy": "export",
        "hash": "audit",
        "read": "inspect",
        "search": "search",
        "parse": "transform",
        "parse_gdsii": "transform",
        "edit": "edit",
        "extract": "transform",
        "annotate": "edit",
        "simulate": "simulate",
        "transcode": "transform",
        "validate": "validate",
        "inspect_hierarchy": "traverse",
        "aggregate": "aggregate",
        "aggregate_geometry": "aggregate",
        "render": "export",
    }
    for format_name, profiles in sorted(files_by_format.items()):
        roles = sorted(
            {
                role
                for profile in profiles
                for role in profile.get("operation_roles", [])
                if role in role_family
            }
        )
        record_support = sum(
            int(profile.get("record_count") or 0) for profile in profiles
        )
        support = record_support or len(profiles)
        for role in roles:
            candidates.append(
                _candidate(
                    f"{role}_{format_name}_files",
                    role_family[role],
                    input_kind=f"file:{format_name}",
                    output_kind=f"file:{format_name}",
                    evidence=[f"format:{format_name}", f"feature:{role}"],
                    support_count=support,
                    parameter_cardinality=len(profiles),
                    description=f"对 {format_name} 文件执行 {role}。",
                )
            )

    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        unique.setdefault(candidate["candidate_id"], candidate)
    return [unique[key] for key in sorted(unique)]
