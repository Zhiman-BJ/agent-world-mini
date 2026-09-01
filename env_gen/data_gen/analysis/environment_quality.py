"""基于 v2 实际状态计算形态自适应的环境丰富度。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .file_formats import normalize_file_format


@dataclass(frozen=True)
class EnvironmentQualityPolicy:
    """v2 环境按实际访问形态评估时使用的最小数据深度。"""

    min_total_records: int = 500
    min_records_per_substantial_record_set: int = 25
    min_core_records: int = 25
    min_core_business_fields: int = 4
    min_core_field_non_null_percent: int = 50
    min_collection_members: int = 8
    min_need_coverage_percent: int = 50
    min_realized_need_count: int = 1

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"EnvironmentQualityPolicy.{name} 必须是非负整数")
        if self.min_need_coverage_percent > 100:
            raise ValueError("min_need_coverage_percent 不能超过 100")
        if self.min_core_field_non_null_percent > 100:
            raise ValueError("min_core_field_non_null_percent 不能超过 100")


def _gap(
    code: str,
    message: str,
    action: str,
    *,
    observed: int | str,
    required: int | str,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "action": action,
        "observed": observed,
        "required": required,
    }


def _declared_formats(structure: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    value = structure.get("format")
    if isinstance(value, str):
        result.add(normalize_file_format(value))
    for child in structure.get("layout", []):
        if isinstance(child, dict):
            result.update(_declared_formats(child))
    return result


def _scope_collection_profiles(run_dir: Path, scope: dict[str, Any]) -> list[dict[str, Any]]:
    """递归计算 Scope 中每个集合节点的真实成员数。"""

    structure = scope.get("structure", {})
    if not isinstance(structure, dict):
        return []
    root = run_dir / "state/filesystem_scopes" / str(scope.get("scope_id"))
    if not root.is_dir():
        return []
    profiles: list[dict[str, Any]] = []

    def matches(directory: Path, node: dict[str, Any]) -> list[Path]:
        pattern = str(node.get("path") or "")
        candidates = [directory] if pattern == "." else sorted(directory.glob(pattern))
        want_directory = node.get("kind") in {"directory", "directory_collection"}
        return [
            item for item in candidates
            if item.is_dir() if want_directory
        ] if want_directory else [item for item in candidates if item.is_file()]

    def visit(parents: list[Path], node: dict[str, Any], pointer: str) -> None:
        kind = str(node.get("kind") or "")
        grouped = [matches(parent, node) for parent in parents]
        matched = [item for group in grouped for item in group]
        if kind in {"file_collection", "directory_collection"}:
            counts = [len(group) for group in grouped]
            profiles.append({
                "node": pointer,
                "kind": kind,
                "path": str(node.get("path") or ""),
                "member_count": len(matched),
                "parent_instance_count": len(parents),
                "minimum_members_per_parent": min(counts) if counts else 0,
                "maximum_members_per_parent": max(counts) if counts else 0,
            })
        if kind in {"directory", "directory_collection"}:
            for index, child in enumerate(node.get("layout", [])):
                if isinstance(child, dict):
                    visit(matched, child, f"{pointer}.layout[{index}]")

    visit([root], structure, "structure")
    return profiles


def _scope_member_count(run_dir: Path, scope: dict[str, Any]) -> int:
    """返回主要操作集合轴的成员数；同构项目按项目数而非内部文件数。"""

    profiles = _scope_collection_profiles(run_dir, scope)
    if profiles:
        structure = scope.get("structure", {})
        if isinstance(structure, dict) and structure.get("kind") in {
            "file_collection", "directory_collection",
        }:
            root_profile = next(
                (item for item in profiles if item.get("node") == "structure"),
                None,
            )
            if root_profile is not None:
                return int(root_profile["member_count"])
        return max(int(item["member_count"]) for item in profiles)
    structure = scope.get("structure", {})
    if not isinstance(structure, dict):
        return 0
    root = run_dir / "state/filesystem_scopes" / str(scope.get("scope_id"))
    pattern = str(structure.get("path") or "")
    target = root if pattern == "." else root / pattern
    return int(target.is_file() if structure.get("kind") == "file" else target.is_dir())


def build_environment_quality_profile(
    run_dir: Path,
    *,
    plan: dict[str, Any],
    scenario_research: dict[str, Any] | None = None,
    source_plan: dict[str, Any],
    source_inventory: dict[str, Any],
    integration_profile: dict[str, Any],
    policy: EnvironmentQualityPolicy | None = None,
) -> dict[str, Any]:
    """只用落盘事实判断环境是否丰富；操作/任务数量不参与门槛。"""

    policy = policy or EnvironmentQualityPolicy()
    record_sets = [item for item in plan.get("record_sets", []) if isinstance(item, dict)]
    scopes = [item for item in plan.get("filesystem_scopes", []) if isinstance(item, dict)]
    mode = "hybrid" if record_sets and scopes else "structured_records" if record_sets else "file_native"
    asset_profile = integration_profile.get("asset_profile", {})
    relation_profile = integration_profile.get("relationship_profile", {})
    reference_profile = integration_profile.get("file_reference_profile", {})
    connectivity = integration_profile.get("connectivity_profile", {})
    source_profile = integration_profile.get("source_integration_profile", {})
    need_profile = integration_profile.get("need_binding_profile", {})
    record_counts = {
        str(key): int(value)
        for key, value in asset_profile.get("record_counts", {}).items()
    }
    actual_record_profiles = {
        str(item.get("record_set_id")): item
        for item in asset_profile.get("record_sets", [])
        if isinstance(item, dict) and isinstance(item.get("record_set_id"), str)
    }
    scope_counts = {
        str(key): int(value)
        for key, value in asset_profile.get("scope_file_counts", {}).items()
    }
    permitted_invalid = {
        str(item.get("scope_id")): list(item.get("permitted_invalid_files", []))
        for item in asset_profile.get("filesystem_scopes", [])
        if isinstance(item, dict) and item.get("permitted_invalid_files")
    }
    gaps: list[dict[str, Any]] = []

    if integration_profile.get("integration_tier") != "integrated":
        gaps.append(_gap(
            "environment_not_integrated",
            "关系、文件路径、物化状态或多源连接仍有集成缺口。",
            "先按 integration_profile.integration_gaps 完成最小范围修复。",
            observed=str(integration_profile.get("integration_tier") or "missing"),
            required="integrated",
        ))

    bindings = [item for item in plan.get("need_bindings", []) if isinstance(item, dict)]
    research_needs = [
        item for item in (scenario_research or {}).get("data_needs", [])
        if isinstance(item, dict)
    ]
    # Some older research artifacts omitted priority. Treat those needs as core so
    # existing environments remain subject to the stricter interpretation.
    core_need_ids = {
        str(item.get("need_id")) for item in research_needs
        if isinstance(item.get("need_id"), str)
        and item.get("priority", "core") == "core"
    }
    applicable = [item for item in bindings if item.get("status") != "not_applicable"]
    realized = [item for item in bindings if item.get("status") == "realized"]
    partial = [item for item in bindings if item.get("status") == "partial"]
    weighted_coverage = round(
        (len(realized) + 0.5 * len(partial)) * 100 / len(applicable)
    ) if applicable else 100
    if len(realized) < policy.min_realized_need_count:
        gaps.append(_gap(
            "insufficient_realized_needs",
            "完整落到真实最终资产的数据需求不足。",
            "优先补齐一个核心需求，不要用 unavailable 或说明文字替代真实数据。",
            observed=len(realized), required=policy.min_realized_need_count,
        ))
    if weighted_coverage < policy.min_need_coverage_percent:
        gaps.append(_gap(
            "insufficient_need_coverage",
            "Step 1 数据需求在最终资产中的加权覆盖率不足。",
            "只对未覆盖的高价值需求补采、建模或准确收口。",
            observed=weighted_coverage, required=policy.min_need_coverage_percent,
        ))

    total_records = sum(record_counts.values())
    core_record_sets = [item for item in record_sets if item.get("importance") == "core"]
    core_record_count = sum(
        record_counts.get(str(item.get("record_set_id")), 0)
        for item in core_record_sets
    )
    substantial = [
        item for item in core_record_sets
        if record_counts.get(str(item.get("record_set_id")), 0)
        >= policy.min_records_per_substantial_record_set
    ]
    if record_sets and core_record_count < policy.min_total_records:
        gaps.append(_gap(
            "insufficient_record_depth",
            "核心 Record Set 的真实记录总量不足以支持稳定筛选、比较和统计。",
            "对已经选中的核心来源定向补量，不增加装饰性小表。",
            observed=core_record_count, required=policy.min_total_records,
        ))
    required_substantial = min(
        max(1, len(core_record_sets)), len(record_sets)
    ) if core_record_sets else 0
    if len(substantial) < required_substantial:
        gaps.append(_gap(
            "insufficient_substantial_record_sets",
            "有足够真实记录深度的 Record Set 数量不足。",
            "扩充现有核心 Record Set，而不是拆分出稀疏集合。",
            observed=len(substantial), required=required_substantial,
        ))
    weak_core: list[str] = []
    usable_business_field_counts: dict[str, int] = {}
    for item in core_record_sets:
        record_set_id = str(item.get("record_set_id"))
        fields = item.get("fields", {})
        key_fields = set(item.get("key_fields", []))
        field_profile = actual_record_profiles.get(record_set_id, {}).get("fields", {})
        record_count = record_counts.get(record_set_id, 0)
        minimum_populated = (
            record_count * policy.min_core_field_non_null_percent + 99
        ) // 100
        business_fields = [
            name for name, definition in fields.items()
            if name not in key_fields
            and isinstance(definition, dict)
            and "reference" not in definition
            and (
                not field_profile
                or int(field_profile.get(name, {}).get("populated_count", 0))
                >= minimum_populated
            )
        ] if isinstance(fields, dict) else []
        usable_business_field_counts[record_set_id] = len(business_fields)
        if (
            record_count < policy.min_core_records
            or len(business_fields) < policy.min_core_business_fields
        ):
            weak_core.append(record_set_id)
    if weak_core:
        gaps.append(_gap(
            "weak_core_record_sets",
            "核心 Record Set 缺少记录深度或足够的业务字段。",
            "补充真实字段和记录，或把不应作为核心的集合降级/移除：" + ", ".join(weak_core),
            observed=len(core_record_sets) - len(weak_core), required=len(core_record_sets),
        ))

    active_core_bindings = [
        item for item in bindings
        if item.get("need_id") in core_need_ids
        and item.get("status") in {"realized", "partial"}
    ]
    need_source_ids = {
        str(item.get("need_id")): {
            str(value) for value in item.get("source_ids", [])
            if isinstance(value, str)
        }
        for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    asset_source_ids = {
        str(item.get("record_set_id") or item.get("scope_id")): {
            str(value) for value in item.get("source_ids", [])
            if isinstance(value, str)
        }
        for item in [*record_sets, *scopes]
    }
    unavailable_core_needs = sorted(
        str(item.get("need_id")) for item in bindings
        if item.get("need_id") in core_need_ids and item.get("status") == "unavailable"
    )
    if unavailable_core_needs:
        gaps.append(_gap(
            "unavailable_core_needs",
            "部分核心数据需求已确认无法由当前公开数据实现。",
            "补充真实可用来源；若研究证明该需求并非场景核心，应在上游研究中修订优先级："
            + ", ".join(unavailable_core_needs),
            observed=len(unavailable_core_needs), required=0,
        ))
    assets_for_core_needs = {
        *(str(value) for item in active_core_bindings for value in item.get("record_set_ids", [])),
        *(str(value) for item in active_core_bindings for value in item.get("scope_ids", [])),
    }
    declared_core_assets = {
        str(item.get("record_set_id") or item.get("scope_id"))
        for item in [*record_sets, *scopes]
        if item.get("importance") == "core"
    }
    misclassified_core_assets = sorted(declared_core_assets - assets_for_core_needs)
    if core_need_ids and misclassified_core_assets:
        gaps.append(_gap(
            "misclassified_core_assets",
            "部分 core 资产只服务 supporting 数据需求，不能代表环境的核心业务深度。",
            "将资产降为 supporting，或仅在有真实业务依据时绑定到 core 数据需求："
            + ", ".join(misclassified_core_assets),
            observed=len(misclassified_core_assets), required=0,
        ))

    healthy_core_record_sets = {
        str(item.get("record_set_id")) for item in core_record_sets
        if record_counts.get(str(item.get("record_set_id")), 0) >= policy.min_core_records
        and str(item.get("record_set_id")) not in weak_core
    }
    healthy_core_scopes = {
        str(item.get("scope_id")) for item in scopes
        if item.get("importance") == "core"
        and scope_counts.get(str(item.get("scope_id")), 0) > 0
        and (
            not _scope_collection_profiles(run_dir, item)
            or _scope_member_count(run_dir, item) >= policy.min_collection_members
        )
    }
    healthy_core_assets = healthy_core_record_sets | healthy_core_scopes
    evidence_backed_core_assets: dict[str, list[str]] = {}
    underdeveloped_core_needs: list[str] = []
    for item in active_core_bindings:
        need_id = str(item.get("need_id"))
        bound_ids = {
            *(str(value) for value in item.get("record_set_ids", [])),
            *(str(value) for value in item.get("scope_ids", [])),
        }
        evidence_sources = need_source_ids.get(need_id, set())
        healthy = sorted(
            asset_id for asset_id in healthy_core_assets.intersection(bound_ids)
            if asset_source_ids.get(asset_id, set()).intersection(evidence_sources)
        )
        evidence_backed_core_assets[need_id] = healthy
        if not healthy:
            underdeveloped_core_needs.append(need_id)
    underdeveloped_core_needs.sort()
    if underdeveloped_core_needs:
        gaps.append(_gap(
            "underdeveloped_core_needs",
            "部分核心数据需求只绑定了 supporting、稀疏或集合成员不足的资产。",
            "围绕这些需求补充真实数据并保留至少一个足够有深度的 core 资产："
            + ", ".join(underdeveloped_core_needs),
            observed=len(underdeveloped_core_needs), required=0,
        ))

    core_scopes = [item for item in scopes if item.get("importance") == "core"]
    empty_core_scopes = [
        str(item.get("scope_id")) for item in core_scopes
        if scope_counts.get(str(item.get("scope_id")), 0) == 0
    ]
    if empty_core_scopes:
        gaps.append(_gap(
            "empty_core_filesystem_scopes",
            "核心 Filesystem Scope 没有可操作的实际文件。",
            "物化真实文件或删除空 Scope：" + ", ".join(empty_core_scopes),
            observed=len(core_scopes) - len(empty_core_scopes), required=len(core_scopes),
        ))
    collection_profiles = {
        str(item.get("scope_id")): _scope_collection_profiles(run_dir, item)
        for item in scopes
    }
    thin_collections = {
        str(item.get("scope_id")): _scope_member_count(run_dir, item)
        for item in core_scopes
        if collection_profiles.get(str(item.get("scope_id")))
        and _scope_member_count(run_dir, item) < policy.min_collection_members
    }
    if thin_collections:
        gaps.append(_gap(
            "insufficient_scope_collection_members",
            "集合型核心 Scope 没有足够成员体现文件或工程之间的真实变化。",
            "补充代表性成员，或把实际单体材料准确建模为 file/directory："
            + ", ".join(f"{key}={value}" for key, value in thin_collections.items()),
            observed=min(thin_collections.values()), required=policy.min_collection_members,
        ))
    required_formats = {
        normalize_file_format(value)
        for value in source_plan.get("required_file_formats", [])
        if isinstance(value, str)
    }
    evidence_formats = {
        normalize_file_format(value)
        for value in source_plan.get("evidence_file_formats", [])
        if isinstance(value, str)
    }
    available_formats = set().union(*(
        _declared_formats(item.get("structure", {})) for item in scopes
        if isinstance(item.get("structure"), dict)
    )) if scopes else set()
    missing_formats = sorted(required_formats - available_formats)
    if missing_formats:
        gaps.append(_gap(
            "missing_required_scope_formats",
            "来源研究确认需要的领域文件格式没有进入最终 Scope。",
            "物化真实文件，或根据新证据修正 source_plan：" + ", ".join(missing_formats),
            observed=len(required_formats) - len(missing_formats), required=len(required_formats),
        ))
    if mode == "hybrid" and core_scopes and core_record_sets:
        valid_references = int(reference_profile.get("valid_reference_field_count", 0))
        if valid_references == 0:
            gaps.append(_gap(
                "hybrid_without_record_file_binding",
                "核心记录和核心文件同时存在，但没有可解析到实际 Scope 路径的 Record 字段。",
                "为能定位文件的顶层 string/array[string] 字段声明 filesystem_path reference。",
                observed=0, required=1,
            ))

    components = connectivity.get("components", [])
    core_nodes = {
        *(f"record:{item.get('record_set_id')}" for item in core_record_sets),
        *(f"scope:{item.get('scope_id')}" for item in core_scopes),
    }
    components_with_core = sum(
        bool(core_nodes.intersection(component))
        for component in components if isinstance(component, list)
    )
    if len(core_nodes) > 1 and components_with_core > 1:
        gaps.append(_gap(
            "core_assets_split_across_components",
            "核心资产分散在多个互不连接的业务分量中。",
            "建立有事实依据的关系或文件路径绑定；否则缩小环境主题。",
            observed=components_with_core, required=1,
        ))

    source_entries = {
        str(item.get("source_id")): item
        for item in source_plan.get("sources", []) if isinstance(item, dict)
    }
    inventory_entries = {
        str(item.get("source_id")): item
        for item in source_inventory.get("sources", []) if isinstance(item, dict)
    }
    selected_ids = {
        str(item.get("source_id"))
        for item in plan.get("source_decisions", [])
        if isinstance(item, dict) and item.get("decision") in {"core", "supporting"}
    }
    unusable_selected = sorted(
        source_id for source_id in selected_ids
        if source_entries.get(source_id, {}).get("status") != "complete"
        or inventory_entries.get(source_id, {}).get("profile_status") not in {"usable", "partial"}
    )
    if unusable_selected:
        gaps.append(_gap(
            "selected_sources_not_usable",
            "最终资产选择了未完成或没有可用真实内容的来源。",
            "补采后重新画像，或将来源改为 evidence_only/rejected：" + ", ".join(unusable_selected),
            observed=len(selected_ids) - len(unusable_selected), required=len(selected_ids),
        ))

    selected_source_paths = {
        str(path)
        for asset in [*record_sets, *scopes]
        for path in asset.get("source_paths", [])
    }
    inventory_files = {
        str(item.get("path")): item
        for item in source_inventory.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    mutable_repository_paths = sorted(
        path for path in selected_source_paths
        if inventory_files.get(path, {}).get("retrieval_stability") == "mutable_repository"
    )
    if mutable_repository_paths:
        gaps.append(_gap(
            "mutable_repository_sources",
            "最终资产依赖 main/master/branch 等可变 Git 仓库 URL。",
            "解析对应 commit SHA，登记 commit 固定 URL，重新下载同一内容并重建受影响资产："
            + ", ".join(mutable_repository_paths),
            observed=len(mutable_repository_paths), required=0,
        ))

    tier = "rich" if not gaps else "not_rich"
    return {
        "schema_version": "3.0",
        "seed_global_id": str(plan.get("seed_global_id") or ""),
        "seed_sha256": str(plan.get("seed_sha256") or ""),
        "quality_tier": tier,
        "summary": (
            "环境在实际状态上同时满足数据深度、需求覆盖、文件可操作性和核心集成要求。"
            if tier == "rich" else
            "环境已形成候选状态，但仍存在阻止 rich 的实质数据或集成缺口。"
        ),
        "shape": mode,
        "policy": asdict(policy),
        "record_profile": {
            "record_set_count": len(record_sets),
            "core_record_set_count": len(core_record_sets),
            "total_record_count": total_records,
            "core_record_count": core_record_count,
            "substantial_record_set_count": len(substantial),
            "weak_core_record_sets": weak_core,
            "misclassified_core_assets": misclassified_core_assets,
            "healthy_core_record_sets": sorted(healthy_core_record_sets),
            "usable_business_field_counts": usable_business_field_counts,
        },
        "file_profile": {
            "scope_count": len(scopes),
            "core_scope_count": len(core_scopes),
            "total_file_count": sum(scope_counts.values()),
            "collection_member_counts": {
                str(item.get("scope_id")): _scope_member_count(run_dir, item)
                for item in scopes
            },
            "collection_profiles": collection_profiles,
            "required_formats": sorted(required_formats),
            "evidence_formats": sorted(evidence_formats),
            "available_formats": sorted(available_formats),
            "missing_formats": missing_formats,
            "valid_file_reference_count": int(reference_profile.get("valid_reference_field_count", 0)),
            "permitted_invalid_file_count": sum(len(items) for items in permitted_invalid.values()),
            "permitted_invalid_files": permitted_invalid,
        },
        "relationship_profile": {
            "declared_count": int(relation_profile.get("declared_count", 0)),
            "valid_count": int(relation_profile.get("valid_count", 0)),
        },
        "need_profile": {
            "need_count": len(bindings),
            "realized_count": len(realized),
            "partial_count": len(partial),
            "weighted_coverage_percent": weighted_coverage,
            "core_need_count": len(core_need_ids),
            "core_need_bound_asset_count": len(assets_for_core_needs),
            "underdeveloped_core_needs": underdeveloped_core_needs,
            "evidence_backed_core_assets": evidence_backed_core_assets,
            "unavailable_core_needs": unavailable_core_needs,
        },
        "integration_profile": {
            "integration_tier": integration_profile.get("integration_tier"),
            "core_component_count": components_with_core,
            "selected_source_count": int(source_profile.get("selected_source_count", 0)),
            "unusable_selected_sources": unusable_selected,
            "mutable_repository_source_paths": mutable_repository_paths,
            "bound_asset_count": int(need_profile.get("bound_asset_count", 0)),
        },
        "quality_gaps": gaps,
    }


__all__ = ["EnvironmentQualityPolicy", "build_environment_quality_profile"]
