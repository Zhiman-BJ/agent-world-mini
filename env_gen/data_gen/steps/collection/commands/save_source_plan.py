"""校验并保存 Agent 维护的来源采集计划。"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from env_gen.data_gen.analysis.source_plan import SourcePlanIssue, SourcePlanValidator
from env_gen.data_gen.config import CollectionPolicy

from ...step1_research_scenario import read_saved_scenario_research
from ...common.constants import (
    CONTROL_RUN_CONFIG,
    CONTROL_SOURCE_PLAN_RECEIPT,
    SOURCE_PLAN_PATH,
)
from ...common.control_io import control_path, read_json, write_json
from ...common.workspace_files import file_sha256
from ..support.checkpoint_builder import checkpoint_from_workspace


def _schema_error_messages(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    messages: list[str] = []
    for error in errors[:12]:
        pointer = "$"
        for part in error.absolute_path:
            pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
        messages.append(f"{pointer}: {error.message}")
    if len(errors) > 12:
        messages.append(f"另有 {len(errors) - 12} 条 Schema 错误")
    return messages


def _source_plan_research_issues(
    payload: dict[str, Any],
    research: dict[str, Any],
) -> list[str]:
    """检查来源计划能否回溯到预调研需求和来源线索。"""

    issues: list[str] = []
    research_requirements = {
        str(item.get("need_id")): item
        for item in research.get("data_needs", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    source_leads = {
        str(item.get("source_lead_id")): item
        for item in research.get("source_leads", [])
        if isinstance(item, dict) and isinstance(item.get("source_lead_id"), str)
    }
    coverage = [
        item for item in payload.get("data_need_coverage", []) if isinstance(item, dict)
    ]
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    source_ids = {
        str(item.get("source_id"))
        for item in sources
        if isinstance(item.get("source_id"), str)
    }

    coverage_ids = [
        str(item.get("need_id"))
        for item in coverage
        if isinstance(item.get("need_id"), str)
    ]
    duplicate_coverage = sorted(
        {value for value in coverage_ids if coverage_ids.count(value) > 1}
    )
    if duplicate_coverage:
        issues.append(f"data_need_coverage 重复：{duplicate_coverage}")
    missing_coverage = sorted(set(research_requirements) - set(coverage_ids))
    unknown_coverage = sorted(set(coverage_ids) - set(research_requirements))
    if missing_coverage:
        issues.append(f"缺少预调研需求覆盖：{missing_coverage}")
    if unknown_coverage:
        issues.append(f"覆盖项引用了未知预调研需求：{unknown_coverage}")

    listed_source_ids = [
        str(item.get("source_id"))
        for item in sources
        if isinstance(item.get("source_id"), str)
    ]
    duplicate_sources = sorted(
        {value for value in listed_source_ids if listed_source_ids.count(value) > 1}
    )
    if duplicate_sources:
        issues.append(f"source_id 重复：{duplicate_sources}")

    raw_owners: dict[str, list[str]] = {}
    for source in sources:
        source_id = str(source.get("source_id") or "")
        for relative in source.get("raw_files", []):
            if isinstance(relative, str):
                raw_owners.setdefault(relative, []).append(source_id)
    duplicate_raw_ownership = {
        relative: owners for relative, owners in raw_owners.items() if len(owners) > 1
    }
    if duplicate_raw_ownership:
        issues.append(f"同一 raw 文件不能归属多个来源：{duplicate_raw_ownership}")

    for index, source in enumerate(sources):
        source_requirements = {
            str(value)
            for value in source.get("need_ids", [])
            if isinstance(value, str)
        }
        unknown_requirements = sorted(source_requirements - set(research_requirements))
        if unknown_requirements:
            issues.append(
                f"sources[{index}].need_ids 引用了未知需求：{unknown_requirements}"
            )
        source_lead_id = source.get("scenario_source_lead_id")
        if isinstance(source_lead_id, str):
            source_lead = source_leads.get(source_lead_id)
            if source_lead is None:
                issues.append(
                    f"sources[{index}].scenario_source_lead_id 不存在：{source_lead_id}"
                )
            else:
                lead_requirements = {
                    str(value)
                    for value in source_lead.get("need_ids", [])
                    if isinstance(value, str)
                }
                if not source_requirements.intersection(lead_requirements):
                    issues.append(
                        f"sources[{index}] 与来源线索 {source_lead_id} 没有共同需求"
                    )

    for index, item in enumerate(coverage):
        unknown_sources = sorted(
            {
                str(value)
                for value in item.get("source_ids", [])
                if isinstance(value, str)
            }
            - source_ids
        )
        if unknown_sources:
            issues.append(
                f"data_need_coverage[{index}].source_ids 引用了未知来源：{unknown_sources}"
            )
        evidence_entities = {
            str(value)
            for value in item.get("evidence_entity_types", [])
            if isinstance(value, str)
        }
        invalid_fields = sorted(
            str(value)
            for value in item.get("evidence_fields", [])
            if isinstance(value, str)
            and value.split(".", 1)[0] not in evidence_entities
        )
        if invalid_fields:
            issues.append(
                f"data_need_coverage[{index}].evidence_fields 不属于声明实体：{invalid_fields}"
            )

    shape_value = research.get("data_shape_hypothesis", {})
    shape = shape_value if isinstance(shape_value, dict) else {}
    research_mode = shape.get("likely_mode")
    if payload.get("data_mode") != research_mode and not str(
        payload.get("research_deviation_note") or ""
    ).strip():
        issues.append(
            "data_mode 与预调研建议不同，必须填写 research_deviation_note 说明新证据"
        )
    expected_file_paths = {
        str(value)
        for value in shape.get("file_relevant_seed_paths", [])
        if isinstance(value, str)
    }
    actual_file_paths = {
        str(value)
        for value in payload.get("file_dependent_seed_paths", [])
        if isinstance(value, str)
    }
    if actual_file_paths != expected_file_paths and not str(
        payload.get("research_deviation_note") or ""
    ).strip():
        issues.append(
            "file_dependent_seed_paths 与预调研建议不同，必须填写 research_deviation_note"
        )
    return issues


def _state_validation_issues(
    run_dir: Path,
    *,
    payload: dict[str, Any],
    plan_path: Path,
    config: dict[str, Any],
    research: dict[str, Any],
) -> list[SourcePlanIssue]:
    """Apply the same Raw/receipt/status gate in every phase that updates a plan."""

    checkpoint = checkpoint_from_workspace(
        run_dir,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        source_plan=payload,
    )
    validator = SourcePlanValidator(
        Path(config["source_plan_schema_path"]),
        CollectionPolicy(**config["collection_policy"]),
    )
    _plan, issues = validator.validate(
        plan_path,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        checkpoint=checkpoint,
        scenario_research=research,
    )
    return issues


def save_source_plan_payload(
    run_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """验证 Schema、Seed 和预调研映射后，原子保存正式来源计划。"""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    schema = read_json(Path(config["source_plan_schema_path"]), "来源计划 Schema")
    errors = _schema_error_messages(payload, schema)
    if payload.get("seed_global_id") != config.get("seed_global_id"):
        errors.append("$.seed_global_id: 与当前运行不一致")
    if payload.get("seed_sha256") != config.get("seed_sha256"):
        errors.append("$.seed_sha256: 与当前选中 Seed 不一致")
    research = read_saved_scenario_research(run_dir)
    errors.extend(_source_plan_research_issues(payload, research))
    temporary_path: Path | None = None
    if not errors:
        provenance = run_dir / "provenance"
        provenance.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-plan-candidate-", suffix=".json", dir=provenance,
        )
        os.close(descriptor)
        try:
            temporary_path = Path(temporary_name)
            write_json(temporary_path, payload)
            errors.extend(
                f"{issue.path}: {issue.message}"
                for issue in _state_validation_issues(
                    run_dir,
                    payload=payload,
                    plan_path=temporary_path,
                    config=config,
                    research=research,
                )
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    if errors:
        raise RuntimeError("source_plan 不符合要求：" + "; ".join(errors[:16]))

    target = run_dir / SOURCE_PLAN_PATH
    write_json(target, payload)
    digest = file_sha256(target)
    receipt = {
        "schema_version": "1.0",
        "path": SOURCE_PLAN_PATH,
        "sha256": digest,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_count": len(payload.get("sources", [])),
        "data_need_count": len(payload.get("data_need_coverage", [])),
    }
    write_json(control_path(run_dir, CONTROL_SOURCE_PLAN_RECEIPT), receipt)
    return {
        "status": "saved",
        "path": receipt["path"],
        "sha256": digest,
        "source_count": receipt["source_count"],
        "data_need_count": receipt["data_need_count"],
    }


def save_source_plan(run_dir: Path, *, input_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    input_path = input_path.resolve()
    try:
        input_path.relative_to(run_dir)
    except ValueError as error:
        raise RuntimeError(
            "source_plan 草稿必须位于当前运行目录内；"
            "请使用 .datagen/drafts/，避免并行运行共享临时文件"
        ) from error
    payload = read_json(input_path, "source_plan 草稿")
    return save_source_plan_payload(run_dir, payload)


def source_plan_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    """检测绕过 save-source-plan 的直接写入或保存中断。"""

    run_dir = run_dir.resolve()
    plan_path = run_dir / SOURCE_PLAN_PATH
    receipt_path = control_path(run_dir, CONTROL_SOURCE_PLAN_RECEIPT)
    if not plan_path.is_file() or not receipt_path.is_file():
        return [
            {
                "code": "source_plan_not_saved",
                "path": SOURCE_PLAN_PATH,
                "message": "来源计划必须通过 datagenctl save-source-plan 保存",
            }
        ]
    try:
        receipt = read_json(receipt_path, "来源计划保存收据")
        actual = file_sha256(plan_path)
    except (OSError, RuntimeError) as error:
        return [
            {
                "code": "invalid_source_plan_receipt",
                "path": SOURCE_PLAN_PATH,
                "message": str(error),
            }
        ]
    if receipt.get("sha256") != actual:
        return [
            {
                "code": "source_plan_modified_after_save",
                "path": SOURCE_PLAN_PATH,
                "message": "来源计划保存后被直接改写；请修改临时草稿并重新保存",
            }
        ]
    return []


def source_plan_state_issues(run_dir: Path) -> list[dict[str, str]]:
    """Validate terminal/source evidence only at assessment boundaries.

    Downloads temporarily make Raw newer than the saved plan, so ordinary plan reads
    must remain possible until the Agent saves the next complete ownership snapshot.
    """

    run_dir = run_dir.resolve()
    plan_path = run_dir / SOURCE_PLAN_PATH
    try:
        config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
        plan = read_json(plan_path, "来源计划")
        research = read_saved_scenario_research(run_dir)
        issues = _state_validation_issues(
            run_dir,
            payload=plan,
            plan_path=plan_path,
            config=config,
            research=research,
        )
    except (OSError, RuntimeError) as error:
        return [{
            "code": "source_plan_state_validation_failed",
            "path": SOURCE_PLAN_PATH,
            "message": str(error),
        }]
    return [
        {"code": issue.code, "path": issue.path, "message": issue.message}
        for issue in issues
    ]


def read_saved_source_plan(run_dir: Path) -> dict[str, Any]:
    issues = source_plan_receipt_issues(run_dir)
    if issues:
        raise RuntimeError("; ".join(issue["message"] for issue in issues))
    return read_json(run_dir.resolve() / SOURCE_PLAN_PATH, "source_plan")


__all__ = [
    "read_saved_source_plan",
    "save_source_plan",
    "save_source_plan_payload",
    "source_plan_receipt_issues",
    "source_plan_state_issues",
]
