"""Step 2 来源探索的确定性评估和收口命令。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.source_inventory import (
    build_source_inventory,
    validate_source_inventory,
)

from ..collection.commands.download_raw import download_receipt_issues
from ..collection.commands.save_source_plan import (
    read_saved_source_plan,
    source_plan_receipt_issues,
    source_plan_state_issues,
)
from ..common.constants import (
    CONTROL_EXPLORATION_ASSESSMENT,
    CONTROL_EXPLORATION_FINALIZATION,
    CONTROL_RAW_INTEGRITY_SNAPSHOT,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    SOURCE_INVENTORY_PATH,
)
from ..common.control_io import control_path, read_json, write_json
from ..common.workspace_files import append_only_issues
from ..common.workspace_files import workspace_files
from ..step1_research_scenario import scenario_research_receipt_issues


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def assess_exploration(run_dir: Path) -> dict[str, Any]:
    """检查来源探索是否产生了足以进入集成阶段的真实样本。"""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    current_snapshot, raw_issues = append_only_issues(run_dir)
    blocking = (
        raw_issues
        + scenario_research_receipt_issues(run_dir)
        + source_plan_receipt_issues(run_dir)
        + download_receipt_issues(run_dir)
        + source_plan_state_issues(run_dir)
    )
    if blocking:
        assessment = {
            "workflow_version": "3.0",
            "decision": "fix",
            "blocking_issues": blocking,
            "next_actions": [],
            "all_sources_resolved": False,
            "usable_core_source_count": 0,
        }
        write_json(control_path(run_dir, CONTROL_EXPLORATION_ASSESSMENT), assessment)
        return assessment

    plan = read_saved_source_plan(run_dir)
    inventory = build_source_inventory(
        run_dir,
        seed_global_id=str(seed.get("global_id") or ""),
        seed_sha256=str(config["seed_sha256"]),
        source_plan=plan,
    )
    schema_path = Path(config["source_inventory_schema_path"])
    schema_errors = validate_source_inventory(inventory, schema_path)
    if schema_errors:
        raise RuntimeError("source_inventory 不符合 Schema：" + "; ".join(schema_errors[:12]))
    write_json(run_dir / SOURCE_INVENTORY_PATH, inventory)

    by_source = {
        str(item.get("source_id")): item
        for item in inventory.get("sources", [])
        if isinstance(item, dict)
    }
    sources = [item for item in plan.get("sources", []) if isinstance(item, dict)]
    declared_raw_owners: dict[str, list[str]] = {}
    for source in sources:
        source_id = str(source.get("source_id") or "")
        for relative in source.get("raw_files", []):
            if isinstance(relative, str):
                declared_raw_owners.setdefault(relative, []).append(source_id)
    actual_raw = set(workspace_files(run_dir)["raw"])
    declared_raw = set(declared_raw_owners)
    terminal = {"complete", "blocked", "unavailable"}
    unresolved = [
        str(item.get("source_id"))
        for item in sources
        if item.get("status") not in terminal
    ]
    next_actions: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    for relative in sorted(actual_raw - declared_raw):
        issues.append(_issue(
            "downloaded_raw_without_source",
            relative,
            "已下载 Raw 必须在 source_plan 中归属恰好一个来源。",
        ))
    for relative in sorted(declared_raw - actual_raw):
        issues.append(_issue(
            "source_plan_missing_raw",
            relative,
            "source_plan 声明的 Raw 文件不存在。",
        ))
    for relative, owners in sorted(declared_raw_owners.items()):
        if len(owners) != 1:
            issues.append(_issue(
                "raw_with_multiple_source_owners",
                relative,
                f"Raw 只能归属一个来源，当前为：{owners}",
            ))
    usable_core = 0
    for source in sources:
        source_id = str(source.get("source_id") or "")
        profile = by_source.get(source_id, {})
        status = str(source.get("status") or "planned")
        if status == "complete":
            if int(profile.get("usable_file_count", 0)) == 0:
                issues.append(_issue(
                    "complete_source_without_usable_sample",
                    f"provenance/source_plan.json#sources/{source_id}",
                    "complete 来源必须至少有一份通过内容检查的真实 Raw 样本。",
                ))
            elif source.get("priority") == "core":
                usable_core += 1
        elif status not in terminal:
            next_actions.append({
                "code": "finish_source_exploration",
                "source_id": source_id,
                "action": "探测代表性样本，并将来源收口为 complete、blocked 或 unavailable。",
            })
    core_sources = [item for item in sources if item.get("priority") == "core"]
    if core_sources and usable_core == 0 and all(item.get("status") != "complete" for item in core_sources):
        issues.append(_issue(
            "no_usable_core_source",
            "provenance/source_plan.json#sources",
            "没有任何核心来源提供可进入集成阶段的真实样本。",
        ))

    decision = "fix" if issues else "ready" if not unresolved else "continue"
    assessment = {
        "workflow_version": "3.0",
        "decision": decision,
        "blocking_issues": issues,
        "next_actions": next_actions,
        "all_sources_resolved": not unresolved,
        "usable_core_source_count": usable_core,
        "source_inventory_summary": inventory["summary"],
    }
    write_json(control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT), current_snapshot)
    write_json(control_path(run_dir, CONTROL_EXPLORATION_ASSESSMENT), assessment)
    return assessment


def finalize_exploration(run_dir: Path, *, result: str) -> dict[str, Any]:
    if result not in {"ready", "insufficient_public_data"}:
        raise RuntimeError("探索结果必须是 ready 或 insufficient_public_data")
    assessment = assess_exploration(run_dir)
    if result == "ready" and assessment.get("decision") != "ready":
        raise RuntimeError("来源探索尚未 ready，不能收口")
    if result == "insufficient_public_data":
        if assessment.get("all_sources_resolved") is not True:
            raise RuntimeError("仍有未收口来源，不能声明公开数据不足")
        if int(assessment.get("usable_core_source_count", 0)) > 0:
            raise RuntimeError("已有可用核心来源，不能声明公开数据不足")
    payload = {
        "workflow_version": "3.0",
        "decision": "finalized",
        "result": result,
        "source_inventory_path": SOURCE_INVENTORY_PATH,
        "summary": assessment.get("source_inventory_summary", {}),
    }
    write_json(control_path(run_dir, CONTROL_EXPLORATION_FINALIZATION), payload)
    return payload


__all__ = ["assess_exploration", "finalize_exploration"]
