from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.capability_extraction import (
    extract_capability_atoms,
    infer_closed_relations,
)
from env_gen.data_gen.analysis.task_space_estimation import (
    build_composition_profile,
)
from env_gen.data_gen.analysis.record_extraction import (
    deterministic_entity_groups,
)
from env_gen.data_gen.analysis.entity_profiling import (
    profile_entity_groups,
    profile_workspace_files,
)


@dataclass(frozen=True)
class RichnessPolicy:
    """面向批量任务生成的环境丰富度门槛。

    门槛作用于独立能力、证据和可组合任务空间，而不是实体总行数。
    每种链最多贡献 100 个估算实例，单一大表不能靠组合数爆炸过门。
    """

    min_capability_atoms: int = 24
    min_operation_families: int = 6
    min_evidence_features: int = 12
    min_transition_shapes: int = 3
    min_chain_shapes: int = 30
    min_long_chain_shapes: int = 10
    min_estimated_task_instances: int = 1_000

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"RichnessPolicy.{name} 必须是非负整数")


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


def build_quality_profile(
    package_root: Path,
    *,
    research_request: dict[str, Any],
    checkpoint: dict[str, Any],
    source_inventory: dict[str, Any],
    policy: RichnessPolicy | None = None,
    data_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从已落盘真实文件确定性计算环境能力和可生成任务空间。"""

    policy = policy or RichnessPolicy()
    package_root = package_root.resolve()
    if data_profile is None:
        workspace = package_root / "workspace"
        entity_groups = deterministic_entity_groups(
            package_root,
            research_request=research_request,
            checkpoint=checkpoint,
            authoritative_raw=True,
        )
        entity_profiles = profile_entity_groups(entity_groups)
        file_profiles = profile_workspace_files(workspace, checkpoint)
        relations = infer_closed_relations(entity_groups, entity_profiles)
    else:
        entity_profiles = dict(data_profile.get("entities", {}))
        file_profiles = list(data_profile.get("files", []))
        relations = list(data_profile.get("relation_candidates", []))
    capabilities = extract_capability_atoms(entity_profiles, file_profiles, relations)
    composition = build_composition_profile(capabilities)

    operation_families = sorted(
        {
            str(atom.get("operation_family"))
            for atom in capabilities
            if atom.get("operation_family")
        }
    )
    evidence_features = sorted(
        {
            evidence
            for atom in capabilities
            for evidence in atom.get("evidence", [])
            if isinstance(evidence, str)
            and not evidence.rsplit(".", 1)[-1].lower().endswith(
                ("_id", "_url", "_uri", "_code", "_hash")
            )
        }
    )
    surfaces = [
        surface
        for surface in source_inventory.get("surfaces", [])
        if isinstance(surface, dict)
    ]
    core_surfaces = [surface for surface in surfaces if surface.get("priority") == "core"]
    unsettled_core = [
        str(surface.get("surface_id") or "unknown")
        for surface in core_surfaces
        if surface.get("collection_status") != "complete"
    ]

    gaps: list[dict[str, Any]] = []
    gates = (
        (
            "insufficient_capability_atoms",
            len(capabilities),
            policy.min_capability_atoms,
            "独立能力原子不足。",
            "继续发现新的业务字段、相关数据面或文件内部结构；不要复制已有字段来凑能力。",
        ),
        (
            "insufficient_operation_families",
            len(operation_families),
            policy.min_operation_families,
            "操作族覆盖不足。",
            "优先补充当前缺少的时间、聚合、关系遍历、全文检索或文件处理数据面。",
        ),
        (
            "insufficient_evidence_features",
            len(evidence_features),
            policy.min_evidence_features,
            "独立业务字段或格式特征不足。",
            "采集字段更完整的批量响应，并补充互补的数值、类别、时间、文本或格式特征。",
        ),
        (
            "insufficient_transition_shapes",
            int(composition["transition_shape_count"]),
            policy.min_transition_shapes,
            "可组合能力之间的转换路径不足。",
            "补齐关系两端或能够承接上一步输出的相关资源，使能力可以串联。",
        ),
        (
            "insufficient_chain_shapes",
            int(composition["chain_shape_count"]),
            policy.min_chain_shapes,
            "可执行工具链形状不足。",
            "扩展新的操作族和跨资源关系，优先形成检索、查看、遍历、分析和导出的组合链。",
        ),
        (
            "insufficient_long_chain_shapes",
            int(composition["long_chain_shape_count"]),
            policy.min_long_chain_shapes,
            "长度至少为 3 的工具链不足。",
            "补充可闭合的中间实体或文件处理阶段，使任务不止停留在单步查询。",
        ),
        (
            "insufficient_task_capacity",
            int(composition["estimated_task_instances"]),
            policy.min_estimated_task_instances,
            "可实例化任务空间不足。",
            "继续完成分页、增加真实参数取值并补齐关系，但不要生成合成业务记录。",
        ),
    )
    for code, observed, required, message, action in gates:
        if observed < required:
            gaps.append(
                _gap(
                    code,
                    message,
                    action,
                    observed=observed,
                    required=required,
                )
            )
    if unsettled_core or not core_surfaces:
        gaps.append(
            _gap(
                "unsettled_core_surfaces",
                "仍有核心数据面未完成，或清单没有声明核心数据面。",
                "继续处理所有 core 数据面；complete 必须附带分页结束、总量取完或分层采集完成的证据。",
                observed=len(core_surfaces) - len(unsettled_core),
                required=max(1, len(core_surfaces)),
            )
        )

    quality_tier = "rich" if not gaps else "not_rich"
    return {
        "schema_version": "1.0",
        "request_sha256": research_request.get("request_sha256", ""),
        "quality_tier": quality_tier,
        "summary": (
            "环境具有足够的独立能力、真实参数空间和多步组合路径。"
            if quality_tier == "rich"
            else "环境数据合法但暂不足以稳定支撑大规模、多样化任务生成。"
        ),
        "policy": asdict(policy),
        "collection": {
            "surface_count": len(surfaces),
            "core_surface_count": len(core_surfaces),
            "settled_core_surface_count": len(core_surfaces) - len(unsettled_core),
            "unsettled_core_surface_ids": unsettled_core,
        },
        "data_profile": {
            "entity_type_count": len(entity_profiles),
            "entity_record_count": sum(
                int(profile.get("record_count", 0))
                for profile in entity_profiles.values()
            ),
            "file_count": len(file_profiles),
            "file_bytes": sum(int(profile.get("bytes", 0)) for profile in file_profiles),
            "entities": entity_profiles,
            "files": file_profiles,
        },
        "capability_profile": {
            "capability_atom_count": len(capabilities),
            "operation_families": operation_families,
            "evidence_feature_count": len(evidence_features),
            "evidence_features": evidence_features,
            "atoms": capabilities,
        },
        "relation_profile": {
            "closed_relation_count": len(relations),
            "relations": relations,
        },
        "composition_profile": composition,
        "gaps": gaps,
    }


def validate_quality_profile(
    profile: dict[str, Any],
    schema_path: Path,
) -> list[str]:
    """使用机器 Schema 检查 quality_profile，返回可直接报错的消息。"""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [
        error.message
        for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.path))
    ]


def quality_gain(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, int]:
    """计算两轮采集真正新增了多少能力、关系和任务空间。"""

    previous_capabilities = previous.get("capability_profile", {})
    current_capabilities = current.get("capability_profile", {})
    previous_composition = previous.get("composition_profile", {})
    current_composition = current.get("composition_profile", {})
    previous_tasks = int(previous_composition.get("estimated_task_instances", 0))
    current_tasks = int(current_composition.get("estimated_task_instances", 0))
    growth_percent = (
        int(round((current_tasks - previous_tasks) * 100 / previous_tasks))
        if previous_tasks > 0
        else (100 if current_tasks > 0 else 0)
    )
    return {
        "new_capability_atoms": int(current_capabilities.get("capability_atom_count", 0))
        - int(previous_capabilities.get("capability_atom_count", 0)),
        "new_relations": int(current.get("relation_profile", {}).get("closed_relation_count", 0))
        - int(previous.get("relation_profile", {}).get("closed_relation_count", 0)),
        "new_chain_shapes": int(current_composition.get("chain_shape_count", 0))
        - int(previous_composition.get("chain_shape_count", 0)),
        "task_capacity_growth_percent": growth_percent,
    }
