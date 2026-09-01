"""Step 2 最终收口并原子写入 data checkpoint。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.source_plan import SourcePlanValidator
from env_gen.data_gen.analysis.validator import EnvironmentPackageValidator
from env_gen.data_gen.config import CollectionPolicy

from ...common.constants import (
    CONTROL_ASSESSMENT,
    CONTROL_FINALIZATION,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    CONTROL_WORKSPACE_CHECKPOINT,
    SOURCE_PLAN_PATH,
    WORKFLOW_VERSION,
)
from ...common.control_io import (
    atomic_write_text,
    control_path,
    json_text,
    read_json,
    write_json,
)
from ...step1_research_scenario import read_saved_scenario_research
from .assess_workspace import assess_workspace
from .save_source_plan import read_saved_source_plan
from ..support.checkpoint_builder import checkpoint_from_workspace


def read_verified_finalization(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """验证 finalization 与正式 checkpoint 的内容哈希绑定。"""

    run_dir = run_dir.resolve()
    checkpoint_path = run_dir / "provenance/data_checkpoint.json"
    finalization = read_json(
        control_path(run_dir, CONTROL_FINALIZATION),
        "采集收口记录",
    )
    checkpoint = read_json(checkpoint_path, "data checkpoint")
    digest = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    if finalization.get("decision") != "finalized":
        raise RuntimeError("finalization.decision 必须是 finalized")
    if finalization.get("checkpoint_sha256") != digest:
        raise RuntimeError("finalization 与 data checkpoint 的 SHA-256 不一致")
    result = finalization.get("result")
    if result not in {"complete", "exhausted", "insufficient_public_data"}:
        raise RuntimeError(f"finalization.result 非法：{result}")
    expected_status = (
        "insufficient_public_data"
        if result == "insufficient_public_data"
        else "ready"
    )
    if checkpoint.get("status") != expected_status:
        raise RuntimeError("finalization result 与 checkpoint status 不一致")
    return finalization, checkpoint


def _validate_insufficient(
    run_dir: Path,
    *,
    config: dict[str, Any],
    seed: dict[str, Any],
    scenario_research: dict[str, Any],
    source_plan: dict[str, Any],
    checkpoint: dict[str, Any],
) -> None:
    policy = CollectionPolicy(**config["collection_policy"])
    validator = EnvironmentPackageValidator(
        Path(config["validation_schema_path"]),
        seed=seed,
        seed_sha256=str(config["seed_sha256"]),
        collection_policy=policy,
    )
    checkpoint_path = control_path(run_dir, CONTROL_WORKSPACE_CHECKPOINT)
    write_json(checkpoint_path, checkpoint)
    report = validator.validate_data_checkpoint(run_dir, checkpoint_path=checkpoint_path)
    unexpected = [
        issue for issue in report.errors if issue.code != "insufficient_public_data"
    ]
    if unexpected:
        raise RuntimeError(
            "insufficient checkpoint 校验失败："
            + "; ".join(issue.message for issue in unexpected[:8])
        )
    plan_validator = SourcePlanValidator(
        Path(config["source_plan_schema_path"]),
        policy,
    )
    _plan, issues = plan_validator.validate(
        run_dir / SOURCE_PLAN_PATH,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        checkpoint=checkpoint,
        scenario_research=scenario_research,
    )
    if issues:
        raise RuntimeError(
            "source plan 仍有错误：" + "; ".join(issue.message for issue in issues[:8])
        )


def finalize_collection(run_dir: Path, *, result: str) -> dict[str, Any]:
    """校验收口条件；正式 checkpoint 最后出现。"""

    if result not in {"complete", "exhausted", "insufficient_public_data"}:
        raise RuntimeError(f"不支持的收口结果：{result}")
    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    scenario_research = read_saved_scenario_research(run_dir)
    source_plan = read_saved_source_plan(run_dir)
    checkpoint_path = run_dir / "provenance/data_checkpoint.json"

    if result == "insufficient_public_data":
        checkpoint = checkpoint_from_workspace(
            run_dir,
            seed_global_id=str(config["seed_global_id"]),
            seed_sha256=str(config["seed_sha256"]),
            source_plan=source_plan,
            status="insufficient_public_data",
        )
        _validate_insufficient(
            run_dir,
            config=config,
            seed=seed,
            scenario_research=scenario_research,
            source_plan=source_plan,
            checkpoint=checkpoint,
        )
    else:
        assessment = assess_workspace(run_dir)
        if assessment.get("decision") == "fix":
            raise RuntimeError("当前文件或来源计划仍有校验错误，请先修复 blocking_issues")
        if result == "complete" and assessment.get("decision") != "ready":
            raise RuntimeError("complete 要求 assessment.decision=ready")
        if result == "exhausted" and (
            assessment.get("all_sources_resolved") is not True
            or assessment.get("all_data_needs_assessed") is not True
        ):
            raise RuntimeError("exhausted 要求所有来源和数据需求均已准确收口")
        checkpoint = read_json(
            control_path(run_dir, CONTROL_WORKSPACE_CHECKPOINT),
            "workspace checkpoint",
        )

    checkpoint_text = json_text(checkpoint)
    finalization = {
        "workflow_version": WORKFLOW_VERSION,
        "decision": "finalized",
        "result": result,
        "checkpoint_sha256": hashlib.sha256(
            checkpoint_text.encode("utf-8")
        ).hexdigest(),
    }
    write_json(control_path(run_dir, CONTROL_FINALIZATION), finalization)
    atomic_write_text(checkpoint_path, checkpoint_text)
    return {key: value for key, value in finalization.items() if key != "checkpoint_sha256"}


__all__ = ["finalize_collection", "read_verified_finalization"]
