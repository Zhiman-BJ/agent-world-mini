"""Step 7：让独立 Agent 多次求解任务并计算可解性和稳定性。

输入：``step6_debug_verifier.jsonl``。
输出：``step7_consistency.jsonl``。

本步不运行参考程序。每次求解都启动全新 Agent 和干净工具 Runtime；只有结构化
答案通过 Verifier 且最终逻辑状态等于 Ground Truth，才计为一次成功。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.io import read_records, write_jsonl
from ..utils.solver import SolverResult, solve_with_codex
from ..utils.verifier import execute_verifier_code


SolveFn = Callable[..., SolverResult]


@dataclass(frozen=True)
class Step7Result:
    output_path: Path
    total: int
    kept: int


def _apply_consistency_metrics(
    item: dict,
    runs: list[dict],
    *,
    expected_runs: int,
    threshold: int,
) -> dict:
    pass_count = sum(run.get("agent_success") is True for run in runs)
    scores = [float(run.get("combined_score", 0.0)) for run in runs]
    complete = (
        len(runs) == expected_runs
        and all(
            isinstance(run.get("state_verifier_result"), dict)
            and run["state_verifier_result"].get("success") is True
            for run in runs
        )
    )
    item[TaskFields.MULTI_EXEC_RESULTS] = runs
    item[TaskFields.CONSISTENCY_PASS_COUNT] = pass_count
    item[TaskFields.CONSISTENCY_PASS_RATE] = pass_count / len(runs) if runs else 0.0
    item["consistency_avg_score"] = sum(scores) / len(scores) if scores else 0.0
    item[TaskFields.CONSISTENCY_TOTAL_RUNS] = len(runs)
    item[TaskFields.CONSISTENCY_THRESHOLD] = threshold
    item["consistency_state_verification_complete"] = complete
    item[TaskFields.CONSISTENCY_KEEP] = complete and pass_count >= threshold
    return item


def process_single_task(
    task: dict,
    *,
    step1_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    solve_fn: SolveFn,
) -> dict:
    item = deepcopy(task)
    ground_truth = task.get(TaskFields.GROUND_TRUTH)
    verifier_code = str(task.get(TaskFields.VERIFIER_CODE) or "")
    if (
        not task.get(TaskFields.VERIFIER_DEBUG_SUCCESS)
        or not isinstance(ground_truth, dict)
        or not verifier_code
    ):
        return _apply_consistency_metrics(
            item,
            [],
            expected_runs=policy.consistency_runs,
            threshold=policy.consistency_threshold,
        )

    expected_answer = ground_truth["candidate_answer"]
    expected_state = ground_truth["final_state"]
    task_text = task.get(TaskFields.TASK_INTERNAL_FINETUNED) or task[TaskFields.TASK_INTERNAL]
    runs: list[dict] = []
    infrastructure_failures: list[dict] = []
    next_run_id = 0
    attempts_left = policy.consistency_runs * 3
    while len(runs) < policy.consistency_runs and attempts_left > 0:
        run_index = next_run_id
        next_run_id += 1
        attempts_left -= 1
        result = solve_fn(
            step1_path=step1_path,
            task_text=task_text,
            output_schema=task[TaskFields.OUTPUT_SCHEMA],
            model=model,
            run_dir=output_dir / "step7_debug" / str(task[TaskFields.TASK_ID]) / f"run_{run_index:02d}",
        )
        verifier_score = 0.0
        verifier_error = None
        if result.answer is not None:
            try:
                verifier_score = execute_verifier_code(
                    verifier_code, result.answer, expected_answer
                )
            except Exception as error:
                verifier_error = f"{type(error).__name__}: {error}"
                verifier_score = 0.0
        verifier_passed = verifier_score >= 1.0
        state_available = result.final_state is not None
        state_match = state_available and result.final_state == expected_state
        state_result = {
            "success": state_available,
            "passed": state_match,
            "score": 1.0 if state_match else 0.0,
            "mode": "exact_final_state",
            "error": None if state_available else (result.error or "Missing candidate final state"),
            "mismatches": [] if state_match else ["candidate final state differs from reference"],
        }
        passed = verifier_passed and state_match
        run = {
            "run_id": run_index,
            "candidate_answer": deepcopy(result.answer),
            "verifier_passed": verifier_passed,
            "verifier_score": verifier_score,
            "verifier_error": verifier_error,
            "state_verifier_passed": state_match,
            "state_verifier_score": 1.0 if state_match else 0.0,
            "state_verifier_result": state_result,
            "combined_score": (verifier_score + (1.0 if state_match else 0.0)) / 2.0,
            "agent_success": passed,
            "steps_taken": len(result.trace),
            "trajectory": deepcopy(result.trace),
            "agent_error": result.error,
        }
        if state_available:
            runs.append(run)
        else:
            infrastructure_failures.append(run)
    runs.sort(key=lambda run: run["run_id"])
    infrastructure_failures.sort(key=lambda run: run["run_id"])
    _apply_consistency_metrics(
        item,
        runs,
        expected_runs=policy.consistency_runs,
        threshold=policy.consistency_threshold,
    )
    item["consistency_infrastructure_failures"] = infrastructure_failures
    item["consistency_infrastructure_retry_count"] = len(infrastructure_failures)
    return item


def run_step7(
    *,
    step1_path: Path,
    step6_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    solve_fn: SolveFn = solve_with_codex,
) -> Step7Result:
    tasks = read_records(step6_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            step1_path=step1_path,
            output_dir=output_dir,
            policy=policy,
            model=model,
            solve_fn=solve_fn,
        )
        for task in tasks
    ]
    output_path = output_dir / "step7_consistency.jsonl"
    write_jsonl(output_path, records)
    return Step7Result(
        output_path,
        len(records),
        sum(bool(item.get(TaskFields.CONSISTENCY_KEEP)) for item in records),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step6-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--threshold", type=int, default=2)
    arguments = parser.parse_args()
    result = run_step7(
        step1_path=arguments.step1_path,
        step6_path=arguments.step6_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            consistency_runs=arguments.runs,
            consistency_threshold=arguments.threshold,
        ),
        model=arguments.model,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
