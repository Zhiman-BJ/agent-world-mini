"""Step 2：来源探索、代表性采样和来源画像。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from env_gen.data_gen.config import CollectionPolicy

from .common.constants import (
    CONTROL_EXPLORATION_LAUNCHER,
    CONTROL_EXPLORATION_FINALIZATION,
    EXPLORATION_GUIDE_FILE,
    SOURCE_INVENTORY_PATH,
)
from .common.control_io import control_path, read_json
from .common.round_budget import is_last_available_round
from .collection.support.run_integrity import protected_input_snapshot, verify_protected_inputs
from .collection.commands.download_raw import cleanup_download_temporaries
from .exploration.commands import assess_exploration, finalize_exploration
from .exploration.workflow import (
    build_exploration_continuation_prompt,
    build_exploration_prompt,
    prepare_exploration,
)


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]


@dataclass(frozen=True)
class SourceExplorationResult:
    result: str
    source_inventory: dict[str, Any]
    agent_calls: int


class SourceExplorationError(RuntimeError):
    """Step 2 没有形成可验证的来源画像。"""


def run_source_exploration(
    *,
    run_dir: Path,
    collection_policy: CollectionPolicy,
    agent_runner: AgentRunner,
) -> SourceExplorationResult:
    run_dir = run_dir.resolve()
    prepare_exploration(run_dir)
    protected = protected_input_snapshot(
        run_dir,
        phase_control_files=(
            CONTROL_EXPLORATION_LAUNCHER,
            EXPLORATION_GUIDE_FILE,
        ),
    )
    finalization_path = control_path(run_dir, CONTROL_EXPLORATION_FINALIZATION)
    inventory_path = run_dir / SOURCE_INVENTORY_PATH
    deadline = time.monotonic() + collection_policy.exploration_total_seconds
    calls = 0
    last_error: Exception | None = None
    for round_index in range(1, collection_policy.max_exploration_rounds + 1):
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            break
        calls += 1
        final_round = is_last_available_round(
            round_index=round_index,
            max_rounds=collection_policy.max_exploration_rounds,
            remaining_seconds=remaining,
            per_round_seconds=collection_policy.exploration_seconds,
        )
        prompt = (
            build_exploration_prompt(run_dir)
            if round_index == 1 else
            build_exploration_continuation_prompt(
                run_dir,
                round_index=round_index,
                final_round=final_round,
            )
        )
        try:
            agent_runner(
                prompt,
                min(collection_policy.exploration_seconds, remaining),
                (finalization_path, inventory_path),
            )
        except Exception as error:
            last_error = error
        cleanup_download_temporaries(run_dir)
        verify_protected_inputs(protected)
        if finalization_path.is_file() and inventory_path.is_file():
            finalization = read_json(finalization_path, "来源探索收口")
            assessment = assess_exploration(run_dir)
            result = str(finalization.get("result") or "")
            if result == "ready" and assessment.get("decision") != "ready":
                finalization_path.unlink(missing_ok=True)
            elif result == "insufficient_public_data" and int(assessment.get("usable_core_source_count", 0)) > 0:
                finalization_path.unlink(missing_ok=True)
            else:
                return SourceExplorationResult(
                    result=result,
                    source_inventory=read_json(inventory_path, "来源画像"),
                    agent_calls=calls,
                )
        assessment = assess_exploration(run_dir)
        if assessment.get("decision") == "ready":
            finalize_exploration(run_dir, result="ready")
            return SourceExplorationResult(
                result="ready",
                source_inventory=read_json(inventory_path, "来源画像"),
                agent_calls=calls,
            )
    suffix = f"：{last_error}" if last_error else ""
    raise SourceExplorationError(
        f"Step 2 在 {calls}/{collection_policy.max_exploration_rounds} 轮内没有完成来源探索{suffix}"
    ) from last_error


__all__ = ["SourceExplorationError", "SourceExplorationResult", "run_source_exploration"]
