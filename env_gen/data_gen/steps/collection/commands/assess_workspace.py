"""重算 Step 2 数据事实、质量缺口和下一步动作。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.entity_profiling import (
    profile_entity_groups,
    profile_workspace_files,
)
from env_gen.data_gen.analysis.operation_candidates import infer_closed_relations
from env_gen.data_gen.analysis.quality import (
    RichnessPolicy,
    build_quality_profile,
    validate_quality_profile,
)
from env_gen.data_gen.analysis.record_compiler import deterministic_entity_groups
from env_gen.data_gen.analysis.record_relations import _infer_relation_gaps
from env_gen.data_gen.analysis.seed import core_entity_hints
from env_gen.data_gen.analysis.source_plan import (
    SourcePlanValidator,
    build_next_actions,
    core_sources_resolved,
)
from env_gen.data_gen.analysis.validator import (
    EnvironmentPackageValidator,
    ValidationIssue,
    ValidationReport,
)
from env_gen.data_gen.config import CollectionPolicy

from ...common.constants import (
    CONTROL_ASSESSMENT,
    CONTROL_RAW_INTEGRITY_SNAPSHOT,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    CONTROL_WORKSPACE_CHECKPOINT,
    SCENARIO_RESEARCH_PATH,
    SOURCE_PLAN_PATH,
    WORKFLOW_VERSION,
)
from ...common.control_io import control_path, read_json, write_json
from ...common.workspace_files import append_only_issues, business_snapshot
from ...step1_research_scenario import (
    read_saved_scenario_research,
    scenario_research_receipt_issues,
)
from .add_workspace_data import data_file_receipt_issues
from .download_raw import download_receipt_issues
from .save_source_plan import read_saved_source_plan, source_plan_receipt_issues
from ..support.checkpoint_builder import checkpoint_from_workspace


def _profile_data(
    run_dir: Path,
    *,
    seed: dict[str, Any],
    source_plan: dict[str, Any],
    seed_sha256: str,
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    groups = deterministic_entity_groups(
        run_dir,
        entity_hints=core_entity_hints(seed, source_plan),
        checkpoint=checkpoint,
        authoritative_raw=False,
    )
    entities = profile_entity_groups(groups)
    files = profile_workspace_files(run_dir / "workspace", checkpoint)
    relations = infer_closed_relations(groups, entities)
    fields = {
        entity_type: {
            field_name: str(field.get("type") or "string")
            for field_name, field in profile.get("fields", {}).items()
        }
        for entity_type, profile in entities.items()
    }
    relation_gaps = _infer_relation_gaps(groups, fields)
    profile = {
        "schema_version": "2.0",
        "seed_global_id": str(seed.get("global_id") or ""),
        "seed_sha256": seed_sha256,
        "summary": {
            "entity_type_count": len(entities),
            "entity_record_count": sum(
                int(item.get("record_count", 0)) for item in entities.values()
            ),
            "file_count": len(files),
            "file_bytes": sum(int(item.get("bytes", 0)) for item in files),
            "relation_candidate_count": len(relations),
            "relation_gap_candidate_count": len(relation_gaps),
        },
        "entities": entities,
        "files": files,
        "relation_candidates": relations,
        "relation_gap_candidates": relation_gaps,
    }
    write_json(run_dir / "provenance/data_profile.json", profile)
    return profile


def _validation_error_payload(report: ValidationReport) -> list[dict[str, str]]:
    """按错误码保留有限样例，避免一个字段错误淹没反馈。"""

    grouped: dict[str, list[dict[str, str]]] = {}
    for issue in report.errors:
        payload = issue.to_dict()
        grouped.setdefault(str(payload.get("code") or "validation_error"), []).append(payload)
    examples: list[dict[str, str]] = []
    omitted: dict[str, int] = {}
    for code, items in grouped.items():
        retained = min(len(items), 3, max(0, 47 - len(examples)))
        examples.extend(items[:retained])
        if retained < len(items):
            omitted[code] = len(items) - retained
    if not omitted:
        return examples
    counts = ", ".join(f"{code}={count}" for code, count in omitted.items())
    return [
        {
            "code": "validation_issues_truncated",
            "path": "$.validation",
            "message": f"另有 {sum(omitted.values())} 条同类错误未展开（{counts}）。",
        },
        *examples,
    ]


def _fix_assessment(run_dir: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    assessment = {
        "workflow_version": WORKFLOW_VERSION,
        "decision": "fix",
        "quality_tier": None,
        "blocking_issues": issues,
        "next_actions": [],
        "core_sources_resolved": False,
        "all_sources_resolved": False,
        "all_data_needs_assessed": False,
    }
    write_json(control_path(run_dir, CONTROL_ASSESSMENT), assessment)
    return assessment


def assess_workspace(run_dir: Path) -> dict[str, Any]:
    """从当前磁盘事实独立重算 Step 2 是否可收口。"""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    seed_global_id = str(config["seed_global_id"])
    seed_sha256 = str(config["seed_sha256"])
    current_snapshot, raw_issues = append_only_issues(run_dir)
    preparation_issues = (
        raw_issues
        + scenario_research_receipt_issues(run_dir)
        + source_plan_receipt_issues(run_dir)
        + download_receipt_issues(run_dir)
        + data_file_receipt_issues(run_dir)
    )
    if preparation_issues:
        return _fix_assessment(run_dir, preparation_issues)

    try:
        scenario_research = read_saved_scenario_research(run_dir)
        source_plan = read_saved_source_plan(run_dir)
    except RuntimeError as error:
        return _fix_assessment(
            run_dir,
            [{
                "code": "missing_or_invalid_collection_input",
                "path": "provenance",
                "message": str(error),
            }],
        )

    checkpoint = checkpoint_from_workspace(
        run_dir,
        seed_global_id=seed_global_id,
        seed_sha256=seed_sha256,
        source_plan=source_plan,
    )
    checkpoint_path = control_path(run_dir, CONTROL_WORKSPACE_CHECKPOINT)
    write_json(checkpoint_path, checkpoint)
    collection_policy = CollectionPolicy(**config["collection_policy"])
    validator = EnvironmentPackageValidator(
        Path(config["validation_schema_path"]),
        seed=seed,
        seed_sha256=seed_sha256,
        collection_policy=collection_policy,
    )
    report = validator.validate_data_checkpoint(
        run_dir,
        checkpoint_path=checkpoint_path,
    )
    plan_validator = SourcePlanValidator(
        Path(config["source_plan_schema_path"]),
        collection_policy,
    )
    _plan, plan_issues = plan_validator.validate(
        run_dir / SOURCE_PLAN_PATH,
        seed_global_id=seed_global_id,
        seed_sha256=seed_sha256,
        checkpoint=checkpoint,
        scenario_research=scenario_research,
    )
    report.errors.extend(
        ValidationIssue(issue.code, issue.path, issue.message)
        for issue in plan_issues
    )
    if not report.valid:
        return _fix_assessment(run_dir, _validation_error_payload(report))

    data_profile = _profile_data(
        run_dir,
        seed=seed,
        source_plan=source_plan,
        seed_sha256=seed_sha256,
        checkpoint=checkpoint,
    )
    quality = build_quality_profile(
        run_dir,
        seed=seed,
        seed_sha256=seed_sha256,
        checkpoint=checkpoint,
        scenario_research=scenario_research,
        source_plan=source_plan,
        policy=RichnessPolicy(**config["richness_policy"]),
        data_profile=data_profile,
    )
    quality_errors = validate_quality_profile(
        quality,
        Path(config["quality_profile_schema_path"]),
    )
    if quality_errors:
        raise RuntimeError(
            "quality_profile 不符合内部 Schema：" + "; ".join(quality_errors[:12])
        )
    quality_path = run_dir / "provenance/quality_profile.json"
    write_json(quality_path, quality)
    history_dir = run_dir / "provenance/quality_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(history_dir.glob("assessment_*.json"))
    previous: dict[str, Any] | None = None
    if existing:
        try:
            previous = read_json(existing[-1], "上一份质量评估")
        except RuntimeError:
            previous = None
    if previous != quality:
        write_json(
            history_dir / f"assessment_{len(existing) + 1:02d}.json",
            quality,
        )

    sources = [item for item in source_plan.get("sources", []) if isinstance(item, dict)]
    resolved = {"complete", "blocked", "unavailable"}
    all_sources_resolved = bool(sources) and all(
        item.get("status") in resolved for item in sources
    )
    data_needs = quality.get("data_need_profile", {})
    all_data_needs_assessed = int(data_needs.get("unassessed_count", 0)) == 0
    next_actions = build_next_actions(source_plan, quality)
    decision = (
        "ready"
        if quality["quality_tier"] == "rich"
        and all_sources_resolved
        and all_data_needs_assessed
        and not next_actions
        else "continue"
    )
    assessment = {
        "workflow_version": WORKFLOW_VERSION,
        "decision": decision,
        "quality_tier": quality["quality_tier"],
        "blocking_issues": [],
        "next_actions": next_actions,
        "core_sources_resolved": core_sources_resolved(source_plan),
        "all_sources_resolved": all_sources_resolved,
        "all_data_needs_assessed": all_data_needs_assessed,
    }
    write_json(
        control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT),
        current_snapshot,
    )
    write_json(control_path(run_dir, CONTROL_ASSESSMENT), assessment)
    return assessment


__all__ = ["_profile_data", "_validation_error_payload", "assess_workspace"]
