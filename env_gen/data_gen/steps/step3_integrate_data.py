"""Step 3：定向深采、确定性物化、集成画像和最小修复循环。"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from env_gen.data_gen.config import CollectionPolicy

from .collection.support.run_integrity import (
    protected_input_snapshot,
    verify_protected_inputs,
)
from .common.constants import (
    CONTROL_INTEGRATION_ASSESSMENT,
    CONTROL_INTEGRATION_FINALIZATION,
    CONTROL_INTEGRATION_LAUNCHER,
    INTEGRATION_GUIDE_FILE,
    INTEGRATION_PLAN_PATH,
    INTEGRATION_PROFILE_PATH,
    SOURCE_PLAN_PATH,
)
from .common.control_io import control_path, read_json
from .common.round_budget import is_last_available_round
from .common.workspace_files import file_sha256
from .collection.commands.download_raw import cleanup_download_temporaries
from .integration.commands import assess_integration, finalize_integration
from .integration.workflow import (
    build_integration_continuation_prompt,
    build_integration_prompt,
    prepare_integration,
)


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]


@dataclass(frozen=True)
class IntegrationResult:
    integration_plan: dict[str, Any]
    integration_profile: dict[str, Any]
    agent_calls: int
    assessment_runs: int


class IntegrationFinalizationError(RuntimeError):
    """Step 3 未能形成通过程序重算的 integrated 环境。"""


_NARRATIVE_KEYS = {
    "description",
    "name",
    "reason",
    "summary",
    "transformation",
    "standalone_reason",
}


def _stable_structure(value: Any) -> Any:
    """Progress 只看可执行结构，不把改写说明当成数据进展。"""

    if isinstance(value, dict):
        return {
            key: _stable_structure(child)
            for key, child in sorted(value.items())
            if key not in _NARRATIVE_KEYS
            and key not in {"saved_at", "materialized_at", "retrieved_at"}
        }
    if isinstance(value, list):
        return [_stable_structure(child) for child in value]
    return value


def _file_tree(root: Path, *, relative_to: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(relative_to).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def integration_progress_snapshot(run_dir: Path) -> dict[str, Any]:
    """联合 Raw、计划和实际状态，判断一轮是否有实质进展。"""

    run_dir = run_dir.resolve()
    workspace = run_dir / "workspace"
    state = run_dir / "state"
    structured: dict[str, Any] = {}
    for label, relative in (
        ("source_plan", SOURCE_PLAN_PATH),
        ("integration_plan", INTEGRATION_PLAN_PATH),
        ("integration_profile", INTEGRATION_PROFILE_PATH),
    ):
        path = run_dir / relative
        if path.is_file():
            try:
                structured[label] = _stable_structure(read_json(path, label))
            except RuntimeError:
                structured[label] = {"invalid": True}
    payload = {
        "structured": structured,
        "raw": _file_tree(workspace / "raw", relative_to=workspace),
        "state": _file_tree(state, relative_to=run_dir),
        "transformations": _file_tree(
            run_dir / "provenance/transformations", relative_to=run_dir
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        **payload,
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def _assessment_after_round(run_dir: Path) -> dict[str, Any]:
    try:
        return assess_integration(run_dir)
    except Exception as error:
        return {
            "workflow_version": "3.0",
            "decision": "fix",
            "quality_tier": None,
            "blocking_issues": [{
                "code": "integration_assessment_failed",
                "path": control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT).as_posix(),
                "message": str(error),
            }],
            "next_actions": [],
        }


def run_integration_phase(
    *,
    run_dir: Path,
    collection_policy: CollectionPolicy,
    agent_runner: AgentRunner,
) -> IntegrationResult:
    """边定向采集边集成，每轮都由 Python 重算集成画像。"""

    run_dir = run_dir.resolve()
    prepare_integration(run_dir)
    protected = protected_input_snapshot(
        run_dir,
        phase_control_files=(
            CONTROL_INTEGRATION_LAUNCHER,
            INTEGRATION_GUIDE_FILE,
        ),
    )
    finalization_path = control_path(run_dir, CONTROL_INTEGRATION_FINALIZATION)
    profile_path = run_dir / INTEGRATION_PROFILE_PATH
    deadline = time.monotonic() + collection_policy.integration_total_seconds
    calls = 0
    assessments = 0
    no_progress_rounds = 0
    last_error: Exception | None = None
    last_assessment: dict[str, Any] | None = None

    for round_index in range(1, collection_policy.max_integration_rounds + 1):
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            last_error = TimeoutError(
                f"Step 3 超过集成预算 {collection_policy.integration_total_seconds} 秒"
            )
            break
        before = integration_progress_snapshot(run_dir)
        final_round = is_last_available_round(
            round_index=round_index,
            max_rounds=collection_policy.max_integration_rounds,
            remaining_seconds=remaining,
            per_round_seconds=collection_policy.integration_seconds,
        )
        prompt = (
            build_integration_prompt(run_dir)
            if round_index == 1
            else build_integration_continuation_prompt(
                run_dir,
                round_index=round_index,
                final_round=final_round,
            )
        )
        calls += 1
        try:
            agent_runner(
                prompt,
                min(collection_policy.integration_seconds, remaining),
                (finalization_path, profile_path),
            )
        except Exception as error:
            last_error = error
        cleanup_download_temporaries(run_dir)
        verify_protected_inputs(protected)
        last_assessment = _assessment_after_round(run_dir)
        assessments += 1

        if last_assessment.get("decision") in {"ready", "exhausted"}:
            try:
                finalize_integration(
                    run_dir,
                    result=str(last_assessment["decision"]),
                )
                verify_protected_inputs(protected)
                plan = read_json(run_dir / INTEGRATION_PLAN_PATH, "集成计划")
                profile = read_json(profile_path, "集成画像")
            except Exception as error:
                last_error = error
            else:
                return IntegrationResult(plan, profile, calls, assessments)

        after = integration_progress_snapshot(run_dir)
        if before["fingerprint"] == after["fingerprint"]:
            no_progress_rounds += 1
        else:
            no_progress_rounds = 0
        if no_progress_rounds >= collection_policy.max_no_progress_rounds:
            last_error = RuntimeError(
                f"连续 {no_progress_rounds} 轮没有新增 Raw、可执行模型或物化状态"
            )
            break

    details: list[str] = []
    if last_assessment:
        for issue in last_assessment.get("blocking_issues", [])[:6]:
            if isinstance(issue, dict):
                details.append(str(issue.get("message") or issue.get("code")))
        for action in last_assessment.get("next_actions", [])[:6]:
            if isinstance(action, dict):
                details.append(str(action.get("action") or action.get("code")))
    if last_error is not None:
        details.append(str(last_error))
    suffix = "：" + "；".join(value for value in details if value) if details else ""
    raise IntegrationFinalizationError(
        f"Step 3 在 {calls}/{collection_policy.max_integration_rounds} 轮内未形成 integrated 环境{suffix}"
    ) from last_error


__all__ = [
    "IntegrationFinalizationError",
    "IntegrationResult",
    "integration_progress_snapshot",
    "run_integration_phase",
]
