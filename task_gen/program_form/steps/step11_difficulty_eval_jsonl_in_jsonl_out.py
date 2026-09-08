"""Step 11：以多次独立求解和 Rubric Judge 估计任务难度与 pass@k。

输入：``step10_rubric.jsonl``。
输出：``step11_difficulty.jsonl``。
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any, Callable, Protocol

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.io import read_json, read_records, write_json, write_jsonl
from ..utils.solver import SolverResult, solve_with_codex
from ..utils.verifier import execute_verifier_code


class RubricJudgeAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


SolveFn = Callable[..., SolverResult]


@dataclass(frozen=True)
class Step11Result:
    output_path: Path
    total: int


def estimate_pass_at_k(n: int, c: int, k: int) -> float:
    if c == 0 or k == 0 or n < k or c < 0:
        return 0.0
    if c == n:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def classify_difficulty(pass_count: int, runs: int) -> str:
    if runs <= 0 or pass_count <= 0:
        return "unsolved"
    rate = pass_count / runs
    if rate <= 0.2:
        return "barely_solved"
    if rate < 0.8:
        return "partially_solved"
    if rate < 1.0:
        return "often_solved"
    return "always_solved"


def build_rubric_judge_prompt() -> str:
    return """你正在执行 Program-form TaskGen Step 11 的 Rubric 评分。

读取 `rubric_judge_request.json`，逐项核对 Agent 的最终答案、真实工具轨迹和最终
状态是否满足 rubric_items。不得根据措辞相似度给分；每项只能获得满分或 0 分。
最终只写 `rubric_review.json`：

```json
{
  "earned_score": 0,
  "total_score": 0,
  "avg_result": 0.0,
  "summary": "总体结论",
  "items": [
    {
      "tag": "G1",
      "category": "general",
      "points": 1,
      "passed": false,
      "earned_score": 0,
      "reason": "证据"
    }
  ]
}
```

每个 tag 和 points 必须与输入 rubric_items 对应。代码会根据逐项 passed 重新计算
earned_score、total_score 和 avg_result，不采用模型自行声明的总分。
"""


def _judge_rubric(
    *,
    agent: RubricJudgeAgent,
    task: dict[str, Any],
    result: SolverResult,
    run_dir: Path,
) -> tuple[float, dict[str, Any]]:
    judge_dir = run_dir / "rubric_judge"
    if judge_dir.exists():
        shutil.rmtree(judge_dir)
    judge_dir.mkdir(parents=True)
    write_json(judge_dir / "rubric_judge_request.json", {
        "task": task.get(TaskFields.TASK_PUBLIC),
        "init_state": (task.get(TaskFields.GROUND_TRUTH) or {}).get("init_state"),
        "output_schema": task.get(TaskFields.OUTPUT_SCHEMA),
        "rubric_items": task.get(TaskFields.RUBRIC_ITEMS),
        "candidate_answer": result.answer,
        "candidate_final_state": result.final_state,
        "executed_tool_sequence": result.trace,
        "reference_final_state": (task.get(TaskFields.GROUND_TRUTH) or {}).get("final_state"),
    })
    prompt = build_rubric_judge_prompt()
    (judge_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    agent.run(prompt, working_directory=judge_dir)
    review = read_json(judge_dir / "rubric_review.json")
    if not isinstance(review, dict) or not isinstance(review.get("items"), list):
        raise ValueError("Rubric Judge 缺少 items")
    expected_items = task.get(TaskFields.RUBRIC_ITEMS, [])
    if len(review["items"]) != len(expected_items):
        raise ValueError("Rubric Judge items 数量与 rubric_items 不一致")
    normalized_items: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    for index, (result, expected) in enumerate(zip(review["items"], expected_items)):
        if not isinstance(result, dict):
            raise ValueError(f"Rubric Judge items[{index}] 必须是 object")
        identifier = str(expected.get("id") or f"R{index + 1}")
        section = str(expected.get("section") or "general")
        points = int(expected.get("points", 1))
        if result.get("tag") != identifier:
            validation_errors.append(
                f"item {index} tag mismatch: expected {identifier}, got {result.get('tag')}"
            )
        if result.get("points") != points:
            validation_errors.append(
                f"item {index} points mismatch: expected {points}, got {result.get('points')}"
            )
        passed = bool(result.get("passed", False))
        normalized_items.append({
            "tag": identifier,
            "category": section,
            "points": points,
            "passed": passed,
            "earned_score": points if passed else 0,
            "reason": str(result.get("reason") or ""),
        })
    earned = sum(item["earned_score"] for item in normalized_items)
    total = sum(item["points"] for item in normalized_items)
    computed_score = round(earned / total, 4) if total else 0.0
    for field, computed in (
        ("earned_score", earned),
        ("total_score", total),
        ("avg_result", computed_score),
    ):
        declared = review.get(field)
        if isinstance(declared, bool) or not isinstance(declared, (int, float)) or abs(float(declared) - computed) > 1e-9:
            validation_errors.append(
                f"{field} mismatch: declared={declared}, computed={computed}"
            )
    normalized_review = {
        "success": True,
        "passed": computed_score >= 1.0,
        "score": computed_score,
        "earned_score": earned,
        "total_score": total,
        "avg_result": computed_score,
        "summary": str(review.get("summary") or ""),
        "reason": str(review.get("summary") or ""),
        "items": normalized_items,
        "validation_errors": validation_errors,
    }
    return computed_score, normalized_review


def process_single_task(
    task: dict[str, Any],
    *,
    step1_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    solve_fn: SolveFn,
    rubric_agent: RubricJudgeAgent | None,
) -> dict[str, Any]:
    item = deepcopy(task)
    ground_truth = task.get(TaskFields.GROUND_TRUTH)
    verifier_code = str(task.get(TaskFields.VERIFIER_CODE) or "")
    rubrics = task.get(TaskFields.RUBRIC_ITEMS)
    if not isinstance(ground_truth, dict) or not verifier_code or not isinstance(rubrics, list):
        item[TaskFields.DIFFICULTY_EVAL_RESULTS_3] = []
        item[TaskFields.DIFFICULTY_PASS_COUNT_3] = 0
        item[TaskFields.DIFFICULTY_EVAL_N] = 0
        item[TaskFields.EMPIRICAL_PASS_RATE_3] = 0.0
        item[TaskFields.PASS_AT_K_ESTIMATE] = {}
        item[TaskFields.PASS_AT_K] = {}
        item[TaskFields.DIFFICULTY_BUCKET] = "missing_data"
        item["step11_error"] = "Missing verifier, ground truth, or rubric_items"
        return item

    evaluations: list[dict[str, Any]] = []
    task_text = task.get(TaskFields.TASK_PUBLIC) or task.get(TaskFields.TASK_INTERNAL)
    expected_answer = ground_truth["candidate_answer"]
    expected_state = ground_truth["final_state"]
    for run_index in range(policy.difficulty_eval_runs):
        run_dir = output_dir / "step11_debug" / str(task[TaskFields.TASK_ID]) / f"run_{run_index:02d}"
        result = solve_fn(
            step1_path=step1_path,
            task_text=task_text,
            output_schema=task[TaskFields.OUTPUT_SCHEMA],
            model=model,
            run_dir=run_dir,
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
        state_score = 1.0 if state_match else 0.0
        state_result = {
            "success": state_available,
            "passed": state_match,
            "score": state_score,
            "mode": "exact_final_state",
            "error": None if state_available else (result.error or "Missing candidate final state"),
            "mismatches": [] if state_match else ["candidate final state differs from reference"],
        }
        rubric_score = 0.0
        rubric_review: dict[str, Any] = {}
        rubric_error = None
        if rubric_agent is not None and result.answer is not None:
            try:
                rubric_score, rubric_review = _judge_rubric(
                    agent=rubric_agent,
                    task=task,
                    result=result,
                    run_dir=run_dir,
                )
            except Exception as error:
                rubric_error = f"{type(error).__name__}: {error}"
        rubric_passed = rubric_score >= 1.0
        passed = verifier_passed and state_match and rubric_passed
        evaluations.append({
            "run_id": run_index,
            "candidate_answer": deepcopy(result.answer),
            "candidate_state": deepcopy(result.final_state),
            "verifier_passed": verifier_passed,
            "verifier_score": verifier_score,
            "verifier_result": {
                "success": verifier_error is None,
                "passed": verifier_passed,
                "score": verifier_score,
                "error": verifier_error,
            },
            "state_verifier_passed": state_match,
            "state_verifier_score": state_score,
            "state_verifier_result": state_result,
            "rubric_passed": rubric_passed,
            "rubric_score": rubric_score,
            "rubric_result": rubric_review | ({"error": rubric_error} if rubric_error else {}),
            "combined_passed": passed,
            "combined_score": (verifier_score + state_score + rubric_score) / 3.0,
            "steps_taken": len(result.trace),
            "trajectory": deepcopy(result.trace),
            "tool_sequence": deepcopy(result.trace),
            "agent_error": result.error,
        })
    passes = sum(bool(run["combined_passed"]) for run in evaluations)
    runs = len(evaluations)
    pass_at_k = {
        "pass_at_1": estimate_pass_at_k(runs, passes, 1),
        "pass_at_2": estimate_pass_at_k(runs, passes, 2),
        "pass_at_3": estimate_pass_at_k(runs, passes, 3),
    }
    item[TaskFields.DIFFICULTY_EVAL_RESULTS_3] = evaluations
    item[TaskFields.DIFFICULTY_PASS_COUNT_3] = passes
    item[TaskFields.DIFFICULTY_EVAL_N] = runs
    item[TaskFields.EMPIRICAL_PASS_RATE_3] = passes / runs if runs else 0.0
    item["avg_verifier_score"] = sum(run["verifier_score"] for run in evaluations) / runs
    item["avg_state_verifier_score"] = sum(run["state_verifier_score"] for run in evaluations) / runs
    item["avg_rubric_score"] = sum(run["rubric_score"] for run in evaluations) / runs
    item[TaskFields.PASS_AT_K_ESTIMATE] = pass_at_k
    item[TaskFields.PASS_AT_K] = pass_at_k
    item[TaskFields.DIFFICULTY_BUCKET] = classify_difficulty(passes, runs)
    return item


def run_step11(
    *,
    step1_path: Path,
    step10_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    rubric_agent: RubricJudgeAgent | None,
    solve_fn: SolveFn = solve_with_codex,
) -> Step11Result:
    tasks = read_records(step10_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            step1_path=step1_path,
            output_dir=output_dir,
            policy=policy,
            model=model,
            solve_fn=solve_fn,
            rubric_agent=rubric_agent,
        )
        for task in tasks
    ]
    output_path = output_dir / "step11_difficulty.jsonl"
    write_jsonl(output_path, records)
    return Step11Result(output_path, len(records))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step10-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient
    result = run_step11(
        step1_path=arguments.step1_path,
        step10_path=arguments.step10_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(difficulty_eval_runs=arguments.runs),
        model=arguments.model,
        rubric_agent=CodexAgentClient(model=arguments.model, sandbox="workspace-write"),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
