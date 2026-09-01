"""把一次 Step 2 会话的磁盘变化压缩成下一轮可执行反馈。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...common.constants import (
    CONTROL_DOWNLOAD_ATTEMPTS,
    CONTROL_ROUND_FEEDBACK,
    CONTROL_ROUND_HISTORY,
    SOURCE_PLAN_PATH,
)
from ...common.control_io import control_path, read_json, write_json
from ...common.workspace_files import file_sha256, workspace_files


def _json_digest(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collection_progress_snapshot(run_dir: Path) -> dict[str, Any]:
    """记录业务文件和来源计划状态；时间戳不会被当作进展。"""

    run_dir = run_dir.resolve()
    workspace = run_dir / "workspace"
    files = workspace_files(run_dir)
    digests = {
        relative: file_sha256(workspace / relative)
        for bucket in ("raw", "entities", "derived")
        for relative in files[bucket]
    }
    plan_path = run_dir / SOURCE_PLAN_PATH
    plan_state: dict[str, Any] | None = None
    if plan_path.is_file():
        try:
            plan = read_json(plan_path, "source plan")
            plan_state = {
                "data_mode": plan.get("data_mode"),
                "sources": [
                    {
                        "source_id": item.get("source_id"),
                        "url": item.get("url"),
                        "status": item.get("status"),
                        "record_count": item.get("record_count"),
                        "raw_files": item.get("raw_files"),
                    }
                    for item in plan.get("sources", [])
                    if isinstance(item, dict)
                ],
                "data_need_coverage": plan.get("data_need_coverage", []),
                "research_refinements": plan.get("research_refinements", []),
            }
        except RuntimeError:
            plan_state = {"invalid": True}
    payload = {"files": digests, "source_plan_state": plan_state}
    return {
        **payload,
        "fingerprint": _json_digest(payload),
        "raw_file_count": len(files["raw"]),
        "entity_file_count": len(files["entities"]),
        "derived_file_count": len(files["derived"]),
    }


def _recent_failures(run_dir: Path, limit: int = 12) -> list[dict[str, Any]]:
    path = control_path(run_dir, CONTROL_DOWNLOAD_ATTEMPTS)
    if not path.is_file():
        return []
    try:
        payload = read_json(path, "下载尝试")
    except RuntimeError:
        return []
    failures = [
        item
        for item in payload.get("attempts", [])
        if isinstance(item, dict) and item.get("status") == "failed"
    ]
    return failures[-limit:]


def write_round_feedback(
    run_dir: Path,
    *,
    round_index: int,
    max_rounds: int,
    assessment: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    agent_error: str | None = None,
    finalization_error: str | None = None,
) -> dict[str, Any]:
    changed = before.get("fingerprint") != after.get("fingerprint")
    decision = str(assessment.get("decision") or "fix")
    recommended_result = (
        "complete"
        if decision == "ready"
        else (
            "exhausted"
            if assessment.get("all_sources_resolved") is True
            and assessment.get("all_data_needs_assessed") is True
            else None
        )
    )
    feedback = {
        "workflow_version": "2.0",
        "round_index": round_index,
        "max_rounds": max_rounds,
        "progress_changed": changed,
        "decision": decision,
        "quality_tier": assessment.get("quality_tier"),
        "blocking_issues": assessment.get("blocking_issues", []),
        "next_actions": assessment.get("next_actions", []),
        "recent_download_failures": _recent_failures(run_dir),
        "recommended_result": recommended_result,
        "agent_error": agent_error,
        "finalization_error": finalization_error,
        "before": before,
        "after": after,
    }
    write_json(control_path(run_dir, CONTROL_ROUND_FEEDBACK), feedback)
    history_path = control_path(run_dir, CONTROL_ROUND_HISTORY)
    history = (
        read_json(history_path, "采集轮次历史")
        if history_path.is_file()
        else {"schema_version": "2.0", "rounds": []}
    )
    rounds = [item for item in history.get("rounds", []) if isinstance(item, dict)]
    rounds.append(feedback)
    history["rounds"] = rounds
    write_json(history_path, history)
    return feedback


__all__ = ["collection_progress_snapshot", "write_round_feedback"]
