"""根据落盘数据事实计算 Step 2 质量，不把估算能力当作已实现能力。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .composition_estimation import build_composition_estimate
from .operation_candidates import build_operation_candidates
from .semantics import semantic_match_score


@dataclass(frozen=True)
class RichnessPolicy:
    """判断真实数据是否足以进入环境描述阶段的硬门槛。"""

    min_entity_types: int = 4
    min_total_entity_records: int = 500
    min_substantial_entity_types: int = 3
    min_records_per_substantial_entity: int = 25
    min_core_entity_records: int = 25
    min_core_business_fields: int = 4
    min_data_need_coverage_percent: int = 50
    min_supported_data_need_count: int = 1
    max_unassessed_data_needs: int = 0
    min_closed_relations: int = 2
    max_relation_gaps: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"RichnessPolicy.{name} 必须是非负整数")
        if self.min_data_need_coverage_percent > 100:
            raise ValueError("min_data_need_coverage_percent 不能超过 100")


def _gap(
    code: str,
    message: str,
    action: str,
    *,
    observed: int | bool,
    required: int | bool,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "action": action,
        "observed": observed,
        "required": required,
    }


def _business_field_count(profile: dict[str, Any]) -> int:
    return sum(
        1
        for field in profile.get("fields", {}).values()
        if isinstance(field, dict)
        and bool(set(field.get("roles", [])) - {"identifier", "file_reference"})
    )


def _best_entity_match(
    required_name: str,
    entity_profiles: dict[str, dict[str, Any]],
) -> str | None:
    candidates = sorted(
        (
            (semantic_match_score(required_name, entity_type), entity_type)
            for entity_type in entity_profiles
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return candidates[0][1] if candidates and candidates[0][0] > 0 else None


def _core_entity_profile(
    source_plan: dict[str, Any],
    entity_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    required = sorted(
        {
            str(entity_type)
            for source in source_plan.get("sources", [])
            if isinstance(source, dict) and source.get("priority") == "core"
            for entity_type in source.get("target_entity_types", [])
            if isinstance(entity_type, str) and entity_type
        }
    )
    result: list[dict[str, Any]] = []
    for entity_type in required:
        matched = _best_entity_match(entity_type, entity_profiles)
        profile = entity_profiles.get(matched or "", {})
        result.append(
            {
                "required_entity": entity_type,
                "matched_entity_type": matched,
                "record_count": int(profile.get("record_count", 0)),
                "business_field_count": _business_field_count(profile),
            }
        )
    return result


def _data_need_profile(
    scenario_research: dict[str, Any],
    source_plan: dict[str, Any],
    entity_profiles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    requirements = {
        str(item.get("need_id")): item
        for item in scenario_research.get("data_needs", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    declarations = {
        str(item.get("need_id")): item
        for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    sources = {
        str(item.get("source_id")): item
        for item in source_plan.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    count_names = (
        "supported", "partial", "blocked", "unavailable", "not_applicable",
        "planned", "missing", "unevidenced",
    )
    counts = {name: 0 for name in count_names}
    items: list[dict[str, Any]] = []
    for requirement_id, requirement in requirements.items():
        declaration = declarations.get(requirement_id)
        declared = str(declaration.get("status") or "planned") if declaration else None
        source_ids = [
            str(value)
            for value in (declaration or {}).get("source_ids", [])
            if isinstance(value, str)
        ]
        entities = [
            str(value)
            for value in (declaration or {}).get("evidence_entity_types", [])
            if isinstance(value, str)
        ]
        fields = [
            str(value)
            for value in (declaration or {}).get("evidence_fields", [])
            if isinstance(value, str)
        ]
        mapped_sources = [sources[value] for value in source_ids if value in sources]
        verified_entities = sorted(
            entity_type
            for entity_type in entities
            if int(entity_profiles.get(entity_type, {}).get("record_count", 0)) > 0
        )
        verified_fields: list[str] = []
        for field_ref in fields:
            entity_type, separator, field_name = field_ref.partition(".")
            field = entity_profiles.get(entity_type, {}).get("fields", {}).get(field_name)
            if separator and isinstance(field, dict) and int(field.get("non_null_count", 0)) > 0:
                verified_fields.append(field_ref)

        effective = declared or "missing"
        if declared in {"supported", "partial"}:
            usable_source = any(
                source.get("status") == "complete"
                and bool(source.get("raw_files"))
                and requirement_id in source.get("need_ids", [])
                for source in mapped_sources
            )
            if not usable_source or not verified_entities or not verified_fields:
                effective = "unevidenced"
        counts[effective if effective in counts else "missing"] += 1
        items.append(
            {
                "need_id": requirement_id,
                "title": str(requirement.get("title") or requirement.get("description") or requirement_id),
                "declared_status": declared,
                "effective_status": effective,
                "source_ids": source_ids,
                "evidence_entity_types": entities,
                "verified_entity_types": verified_entities,
                "evidence_fields": fields,
                "verified_fields": verified_fields,
            }
        )

    applicable = max(0, len(items) - counts["not_applicable"])
    weighted = counts["supported"] + counts["partial"] * 0.5
    coverage = round(weighted * 100 / applicable) if applicable else 100
    unassessed = counts["planned"] + counts["missing"] + counts["unevidenced"]
    return {
        "data_need_count": len(items),
        **{f"{name}_count": value for name, value in counts.items()},
        "unassessed_count": unassessed,
        "weighted_coverage_percent": coverage,
        "data_needs": items,
    }


def _representation_profile(
    source_plan: dict[str, Any],
    entity_profiles: dict[str, dict[str, Any]],
    file_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    required_formats = sorted(
        str(value)
        for value in source_plan.get("required_file_formats", [])
        if isinstance(value, str)
    )
    available_formats = sorted(
        {
            str(item.get("format"))
            for item in file_profiles
            if isinstance(item, dict)
            and item.get("bucket") in {"raw", "derived"}
            and isinstance(item.get("format"), str)
        }
    )
    indexed_formats = sorted(
        {
            str(format_name)
            for profile in entity_profiles.values()
            for field in profile.get("fields", {}).values()
            if isinstance(field, dict) and "file_reference" in field.get("roles", [])
            for format_name in field.get("file_formats", [])
            if isinstance(format_name, str)
        }
    )
    return {
        "data_mode": str(source_plan.get("data_mode") or "structured_records"),
        "data_mode_reason": str(source_plan.get("data_mode_reason") or "未说明"),
        "required_file_formats": required_formats,
        "file_dependent_seed_paths": sorted(
            str(value)
            for value in source_plan.get("file_dependent_seed_paths", [])
            if isinstance(value, str)
        ),
        "available_file_formats": available_formats,
        "indexed_file_formats": indexed_formats,
        "missing_file_formats": sorted(set(required_formats) - set(available_formats)),
        "unindexed_file_formats": sorted(set(required_formats) - set(indexed_formats)),
    }


def _source_summary(source_plan: dict[str, Any]) -> dict[str, Any]:
    sources = [item for item in source_plan.get("sources", []) if isinstance(item, dict)]
    core = [item for item in sources if item.get("priority") == "core"]
    resolved_statuses = {"complete", "blocked", "unavailable"}
    unresolved = [
        str(item.get("source_id") or "unknown")
        for item in sources
        if item.get("status") not in resolved_statuses
    ]
    unresolved_core = [
        str(item.get("source_id") or "unknown")
        for item in core
        if item.get("status") not in resolved_statuses
    ]
    return {
        "source_count": len(sources),
        "core_source_count": len(core),
        "resolved_source_count": len(sources) - len(unresolved),
        "resolved_core_source_count": len(core) - len(unresolved_core),
        "unresolved_source_ids": unresolved,
        "unresolved_core_source_ids": unresolved_core,
    }


def _diagnostics(
    entity_profiles: dict[str, dict[str, Any]],
    file_profiles: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = build_operation_candidates(entity_profiles, file_profiles, relations)
    families = sorted(
        {
            str(item.get("operation_family"))
            for item in candidates
            if item.get("operation_family")
        }
    )
    operation_profile = {
        "candidate_count": len(candidates),
        "operation_families": families,
        "candidates": candidates,
    }
    return operation_profile, build_composition_estimate(candidates)


def build_quality_profile(
    package_root: Path,
    *,
    seed: dict[str, Any],
    seed_sha256: str,
    checkpoint: dict[str, Any],
    scenario_research: dict[str, Any],
    source_plan: dict[str, Any],
    policy: RichnessPolicy | None = None,
    data_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """计算数据可交付性；候选操作和组合估算不参与 rich 判定。"""

    policy = policy or RichnessPolicy()
    package_root = package_root.resolve()
    if data_profile is None:
        from .entity_profiling import profile_entity_groups, profile_workspace_files
        from .operation_candidates import infer_closed_relations
        from .record_compiler import deterministic_entity_groups
        from .record_relations import _infer_relation_gaps
        from .seed import core_entity_hints

        groups = deterministic_entity_groups(
            package_root,
            entity_hints=core_entity_hints(seed, source_plan),
            checkpoint=checkpoint,
            authoritative_raw=False,
        )
        entity_profiles = profile_entity_groups(groups)
        file_profiles = profile_workspace_files(package_root / "workspace", checkpoint)
        relations = infer_closed_relations(groups, entity_profiles)
        fields = {
            entity_type: {
                field_name: str(field.get("type") or "string")
                for field_name, field in profile.get("fields", {}).items()
            }
            for entity_type, profile in entity_profiles.items()
        }
        relation_gaps = _infer_relation_gaps(groups, fields)
    else:
        entity_profiles = dict(data_profile.get("entities", {}))
        file_profiles = list(data_profile.get("files", []))
        relations = list(data_profile.get("relation_candidates", []))
        relation_gaps = list(data_profile.get("relation_gap_candidates", []))

    core_entities = _core_entity_profile(source_plan, entity_profiles)
    requirements = _data_need_profile(scenario_research, source_plan, entity_profiles)
    representation = _representation_profile(source_plan, entity_profiles, file_profiles)
    sources = _source_summary(source_plan)
    substantial = sorted(
        entity_type
        for entity_type, profile in entity_profiles.items()
        if int(profile.get("record_count", 0)) >= policy.min_records_per_substantial_entity
    )
    operation_diagnostics, composition = _diagnostics(
        entity_profiles,
        file_profiles,
        relations,
    )

    entity_record_count = sum(
        int(profile.get("record_count", 0)) for profile in entity_profiles.values()
    )
    quality_gaps: list[dict[str, Any]] = []
    gates = (
        (
            "insufficient_entity_types", len(entity_profiles), policy.min_entity_types,
            "规范 Entity 类型不足。", "从已采集 Raw 整理更多互补业务实体。",
        ),
        (
            "insufficient_entity_records", entity_record_count,
            policy.min_total_entity_records, "规范 Entity 记录总量不足。",
            "继续分页或批量采集核心来源，并重新生成规范 Entity。",
        ),
        (
            "insufficient_substantial_entities", len(substantial),
            policy.min_substantial_entity_types, "达到最小深度的 Entity 类型不足。",
            "优先扩充现有核心实体，而不是增加只有几行的装饰性实体。",
        ),
        (
            "insufficient_closed_relations", len(relations), policy.min_closed_relations,
            "实体间可验证的闭合关系不足。", "补齐关系两端及稳定外键字段。",
        ),
    )
    for code, observed, required, message, action in gates:
        if observed < required:
            quality_gaps.append(
                _gap(code, message, action, observed=observed, required=required)
            )

    weak_core = [
        item
        for item in core_entities
        if item["record_count"] < policy.min_core_entity_records
        or item["business_field_count"] < policy.min_core_business_fields
    ]
    if weak_core:
        quality_gaps.append(
            _gap(
                "weak_core_entities",
                "一个或多个核心实体缺少足够记录或业务字段。",
                "按 core 来源补齐这些实体的记录和非技术业务字段："
                + ", ".join(item["required_entity"] for item in weak_core),
                observed=len(core_entities) - len(weak_core),
                required=len(core_entities),
            )
        )
    if len(relation_gaps) > policy.max_relation_gaps:
        quality_gaps.append(
            _gap(
                "open_relation_gaps",
                "存在外键目标缺失或无法闭合的关系。",
                "补齐被引用实体，或删除无法由真实来源支持的错误关联。",
                observed=len(relation_gaps),
                required=policy.max_relation_gaps,
            )
        )

    if requirements["weighted_coverage_percent"] < policy.min_data_need_coverage_percent:
        quality_gaps.append(
            _gap(
                "insufficient_data_need_coverage",
                "Seed 数据需求的实际证据覆盖率不足。",
                "按未覆盖需求补充来源、Entity 和非空字段证据。",
                observed=int(requirements["weighted_coverage_percent"]),
                required=policy.min_data_need_coverage_percent,
            )
        )
    if requirements["supported_count"] < policy.min_supported_data_need_count:
        quality_gaps.append(
            _gap(
                "insufficient_supported_data_needs",
                "没有足够的数据需求得到完整支持。",
                "至少完整支持核心 Seed 需求，并绑定真实 Raw、Entity 和字段。",
                observed=int(requirements["supported_count"]),
                required=policy.min_supported_data_need_count,
            )
        )
    if requirements["unassessed_count"] > policy.max_unassessed_data_needs:
        quality_gaps.append(
            _gap(
                "unassessed_data_needs",
                "仍有 Seed 数据需求处于 planned、missing 或缺少事实证据。",
                "逐项完成需求，或用真实访问证据准确标记 blocked/unavailable。",
                observed=int(requirements["unassessed_count"]),
                required=policy.max_unassessed_data_needs,
            )
        )
    if sources["core_source_count"] == 0 or sources["unresolved_core_source_ids"]:
        quality_gaps.append(
            _gap(
                "unresolved_core_sources",
                "来源计划没有核心来源，或核心来源尚未收口。",
                "将所有 core 来源处理到 complete、blocked 或 unavailable，并保留证据。",
                observed=int(sources["resolved_core_source_count"]),
                required=max(1, int(sources["core_source_count"])),
            )
        )
    if representation["missing_file_formats"]:
        quality_gaps.append(
            _gap(
                "missing_required_domain_files",
                "Seed 需要的领域文件格式没有实际文件。",
                "下载真实领域文件，或在有调研依据时修正 data_mode 和格式需求。",
                observed=len(representation["required_file_formats"])
                - len(representation["missing_file_formats"]),
                required=len(representation["required_file_formats"]),
            )
        )
    if representation["unindexed_file_formats"]:
        quality_gaps.append(
            _gap(
                "unindexed_required_domain_files",
                "领域文件存在，但没有 Entity.file_path 索引连接业务实体。",
                "在规范 Entity 中增加指向实际文件的 file_path 字段及业务元数据。",
                observed=len(representation["required_file_formats"])
                - len(representation["unindexed_file_formats"]),
                required=len(representation["required_file_formats"]),
            )
        )

    quality_tier = "rich" if not quality_gaps else "not_rich"
    return {
        "schema_version": "2.0",
        "seed_global_id": str(seed.get("global_id") or ""),
        "seed_sha256": seed_sha256,
        "quality_tier": quality_tier,
        "summary": (
            "真实数据已覆盖核心需求、必要文件和关系，可进入环境描述阶段。"
            if quality_tier == "rich"
            else "数据结构合法，但仍有阻止进入环境描述阶段的事实缺口。"
        ),
        "policy": asdict(policy),
        "source_summary": sources,
        "representation_profile": representation,
        "data_need_profile": requirements,
        "data_profile": {
            "entity_type_count": len(entity_profiles),
            "entity_record_count": entity_record_count,
            "substantial_entity_type_count": len(substantial),
            "substantial_entity_types": substantial,
            "core_entity_coverage": core_entities,
            "file_count": len(file_profiles),
            "file_bytes": sum(int(item.get("bytes", 0)) for item in file_profiles),
            "entities": entity_profiles,
            "files": file_profiles,
        },
        "relation_profile": {
            "closed_relation_count": len(relations),
            "relation_gap_count": len(relation_gaps),
            "relations": relations,
            "relation_gaps": relation_gaps,
        },
        "operation_candidate_diagnostics": operation_diagnostics,
        "composition_estimate": composition,
        "diagnostic_only": {
            "operation_candidates": True,
            "composition_estimate": True,
            "note": "这些数字描述潜在的数据操作形状，不表示工具已实现或任务已执行。",
        },
        "quality_gaps": quality_gaps,
    }


def validate_quality_profile(profile: dict[str, Any], schema_path: Path) -> list[str]:
    """使用内部 Schema 检查 quality_profile。"""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.path)):
        pointer = "$"
        for part in error.absolute_path:
            pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
        messages.append(f"{pointer}: {error.message}")
    return messages


def quality_gain(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, int]:
    """计算两次评估间新增的真实记录、关系和已支持需求。"""

    previous_data = previous.get("data_profile", {})
    current_data = current.get("data_profile", {})
    previous_requirements = previous.get("data_need_profile", {})
    current_requirements = current.get("data_need_profile", {})
    return {
        "new_entity_records": int(current_data.get("entity_record_count", 0))
        - int(previous_data.get("entity_record_count", 0)),
        "new_entity_types": int(current_data.get("entity_type_count", 0))
        - int(previous_data.get("entity_type_count", 0)),
        "new_relations": int(
            current.get("relation_profile", {}).get("closed_relation_count", 0)
        )
        - int(previous.get("relation_profile", {}).get("closed_relation_count", 0)),
        "new_supported_data_needs": int(current_requirements.get("supported_count", 0))
        - int(previous_requirements.get("supported_count", 0)),
    }


__all__ = [
    "RichnessPolicy",
    "build_quality_profile",
    "quality_gain",
    "validate_quality_profile",
]
