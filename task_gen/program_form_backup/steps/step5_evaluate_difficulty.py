"""Step 5: run independent hard tests, classify difficulty, and publish tasks.

The solving Agent receives only the public task, its output Schema, the public
environment declaration, and a fresh MCP-backed environment. Research,
Solution, Ground Truth, and scoring rules are never included in its prompt.

Each valid rollout is scored independently by the answer Verifier, state
Verifier, and rubric. Infrastructure failures are retried and never counted as
failed solutions. Tasks with no successful rollout are sent to rework instead
of being mislabeled as extremely difficult.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator

from utils.search_agent.codex import CodexAgentClient

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.environment import load_frozen_package
from ..utils.io import read_json, read_records, write_json, write_jsonl
from ..utils.verifier import execute_state_verifier_code, execute_verifier_code


class RubricJudgeAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class SolverResult:
    answer: dict[str, Any] | None
    trace: list[dict[str, Any]]
    final_state: dict[str, Any] | None
    raw_response: str
    error: str | None
    infrastructure_error: bool = False


SolveFn = Callable[..., SolverResult]


@dataclass(frozen=True)
class Step5Result:
    difficulty_path: Path
    rework_path: Path
    final_path: Path
    total: int
    published: int
    rework: int


_FENCE = re.compile(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", re.IGNORECASE)


class _SolverClient(CodexAgentClient):
    """Attach exactly one temporary Agent-World MCP server to a solver."""

    def __init__(self, server: Path, config: Path, **options: Any):
        super().__init__(**options)
        self.server = server
        self.config = config

    def _llm_arguments(self, environment: dict[str, str]) -> list[str]:
        arguments = super()._llm_arguments(environment)
        arguments.extend([
            "--config",
            "mcp_servers={}",
            "--config",
            f"mcp_servers.agent_world_program.command={json.dumps(sys.executable)}",
            "--config",
            "mcp_servers.agent_world_program.args="
            + json.dumps([str(self.server), str(self.config)]),
        ])
        return arguments


def _parse_answer(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("求解 Agent 没有返回 JSON object")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("求解 Agent 最终答案必须是 JSON object")
    return value


def _solver_artifacts(
    trace_path: Path,
    state_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    trace = []
    if trace_path.is_file():
        trace = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    final_state = read_json(state_path) if state_path.is_file() else None
    return trace, final_state


def solve_with_codex(
    *,
    step1_path: Path,
    task_text: str,
    output_schema: dict[str, Any],
    model: str,
    run_dir: Path,
    max_tool_calls: int = 100,
    timeout_seconds: int = 1800,
) -> SolverResult:
    """Run one stateless solver against a fresh MCP environment copy."""
    package = load_frozen_package(step1_path)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    prompt = json.dumps({
        "role": (
            "完成给定业务任务。你只能通过本会话提供的 Agent-World MCP 工具了解和"
            "修改业务状态，不得读取本地文件、猜测标识符或使用网络。完成后只返回"
            "符合 output_schema 的 JSON object。"
        ),
        "task": task_text,
        "output_schema": output_schema,
        "environment": package.public_environment(),
    }, ensure_ascii=False)
    raw = ""
    with (
        tempfile.TemporaryDirectory(prefix="agent-world-solver-control-") as control,
        tempfile.TemporaryDirectory(prefix="agent-world-solver-workspace-") as workspace,
    ):
        control_dir = Path(control)
        solver_workspace = Path(workspace)
        trace_path = control_dir / "tool_calls.jsonl"
        state_path = control_dir / "final_state.json"
        config_path = control_dir / "mcp_config.json"
        write_json(config_path, {
            "step1_path": str(step1_path.resolve()),
            "trace_path": str(trace_path),
            "state_path": str(state_path),
            "max_tool_calls": max_tool_calls,
        })
        server = Path(__file__).resolve().parents[1] / "utils" / "solver_mcp.py"
        client = _SolverClient(
            server,
            config_path,
            model=model,
            timeout_seconds=timeout_seconds,
            sandbox="read-only",
            network_access=False,
            reasoning_effort="high",
            disabled_mcp_servers=("openaiDeveloperDocs",),
        )
        try:
            raw = client.run(prompt, working_directory=solver_workspace)
            answer = _parse_answer(raw)
            errors = list(Draft202012Validator(output_schema).iter_errors(answer))
            if errors:
                raise ValueError(f"Agent 答案不符合 output_schema: {errors[0].message}")
            trace, final_state = _solver_artifacts(trace_path, state_path)
            _persist_solver_artifacts(run_dir, trace_path, state_path, raw)
            if final_state is None:
                return SolverResult(
                    None,
                    trace,
                    None,
                    raw,
                    "MCP 没有写出最终状态",
                    infrastructure_error=True,
                )
            return SolverResult(answer, trace, final_state, raw, None)
        except Exception as error:
            trace, final_state = _solver_artifacts(trace_path, state_path)
            _persist_solver_artifacts(run_dir, trace_path, state_path, raw)
            retryable = bool(getattr(error, "retryable", False))
            infrastructure_error = retryable or final_state is None
            return SolverResult(
                None,
                trace,
                final_state,
                raw,
                f"{type(error).__name__}: {error}",
                infrastructure_error=infrastructure_error,
            )


def _persist_solver_artifacts(
    run_dir: Path,
    trace_path: Path,
    state_path: Path,
    raw_response: str,
) -> None:
    """Copy control artifacts only after the solver Agent has exited."""
    if trace_path.is_file():
        shutil.copy2(trace_path, run_dir / "tool_calls.jsonl")
    if state_path.is_file():
        shutil.copy2(state_path, run_dir / "final_state.json")
    (run_dir / "agent_response.txt").write_text(raw_response, encoding="utf-8")


def build_rubric_judge_prompt(attempt: int) -> str:
    return f"""# Step 5 独立评分

这是第 {attempt} 次评分提交。读取 `rubric_judge_request.json`，逐项检查求解 Agent
的结构化答案、真实 MCP 工具轨迹和最终状态是否满足 rubric_items。

你不知道隐藏 Solution，也不能修改求解结果。不得因为措辞相似、调用了某个工具或
声称成功就给分。每项只能完整通过或不通过，reason 必须指出实际证据。最终只写
`rubric_review.json`：

```json
{{
  "items": [
    {{"id": "G1", "passed": true, "reason": "可核对的证据"}}
  ]
}}
```

items 必须与输入评分项一一对应、顺序一致，不得新增、删除或改写 ID。Python 会按
输入 points 重新计算分数，不读取模型声明的总分。
"""


def _judge_rubric(
    *,
    agent: RubricJudgeAgent,
    task: dict[str, Any],
    result: SolverResult,
    run_dir: Path,
    max_attempts: int,
) -> tuple[float, dict[str, Any]]:
    judge_root = run_dir / "rubric_judge"
    if judge_root.exists():
        shutil.rmtree(judge_root)
    expected = task[TaskFields.RUBRIC_ITEMS]
    request = {
        "task_public": task[TaskFields.TASK_PUBLIC],
        "output_schema": task[TaskFields.OUTPUT_SCHEMA],
        "rubric_items": expected,
        "candidate_answer": result.answer,
        "candidate_final_state": result.final_state,
        "executed_tool_sequence": result.trace,
    }
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        audit_dir = judge_root / f"attempt_{attempt:02d}"
        with tempfile.TemporaryDirectory(
            prefix="agent-world-rubric-judge-"
        ) as temporary:
            judge_dir = Path(temporary)
            write_json(judge_dir / "rubric_judge_request.json", request)
            write_json(judge_dir / "validation_feedback.json", {"errors": errors})
            prompt = build_rubric_judge_prompt(attempt)
            (judge_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            try:
                agent.run(prompt, working_directory=judge_dir)
                review = read_json(judge_dir / "rubric_review.json")
                if not isinstance(review, dict) or not isinstance(review.get("items"), list):
                    raise ValueError("rubric_review.json 缺少 items")
                if len(review["items"]) != len(expected):
                    raise ValueError("评分结果数量与 rubric_items 不一致")
                normalized: list[dict[str, Any]] = []
                for index, (actual, rubric) in enumerate(zip(review["items"], expected)):
                    if not isinstance(actual, dict):
                        raise ValueError(f"items[{index}] 必须是 object")
                    identifier = str(rubric["id"])
                    if actual.get("id") != identifier:
                        raise ValueError(
                            f"items[{index}].id 应为 {identifier!r}，"
                            f"实际为 {actual.get('id')!r}"
                        )
                    if type(actual.get("passed")) is not bool:
                        raise ValueError(f"items[{index}].passed 必须是 boolean")
                    reason = str(actual.get("reason") or "").strip()
                    if not reason:
                        raise ValueError(f"items[{index}].reason 不能为空")
                    points = int(rubric["points"])
                    passed = actual["passed"]
                    normalized.append({
                        "id": identifier,
                        "section": rubric["section"],
                        "points": points,
                        "passed": passed,
                        "earned_points": points if passed else 0,
                        "reason": reason,
                    })
                earned = sum(item["earned_points"] for item in normalized)
                total = sum(item["points"] for item in normalized)
                score = earned / total if total else 0.0
                shutil.copytree(judge_dir, audit_dir)
                return score, {
                    "success": True,
                    "passed": score == 1.0,
                    "score": score,
                    "earned_points": earned,
                    "total_points": total,
                    "items": normalized,
                }
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
                shutil.copytree(judge_dir, audit_dir)
    raise RuntimeError("Rubric Judge 连续失败: " + " | ".join(errors))


def estimate_pass_at_k(n: int, c: int, k: int) -> float | None:
    if k < 1 or n < k:
        return None
    if c <= 0:
        return 0.0
    if c >= n:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def classify_difficulty(pass_count: int, runs: int) -> str:
    if runs <= 0:
        return "not_evaluated"
    if pass_count <= 0:
        return "unsolved_rework"
    rate = pass_count / runs
    if rate <= 0.2:
        return "very_hard"
    if rate < 0.5:
        return "hard"
    if rate < 0.8:
        return "medium"
    return "easy"


def _evaluate_valid_rollout(
    *,
    task: dict[str, Any],
    result: SolverResult,
    rubric_agent: RubricJudgeAgent,
    run_dir: Path,
    judge_attempts: int,
) -> dict[str, Any]:
    ground_truth = task[TaskFields.GROUND_TRUTH]
    answer_score = 0.0
    answer_error = result.error
    scoring_runtime_errors: list[str] = []
    if result.answer is not None:
        try:
            answer_score = execute_verifier_code(
                task[TaskFields.VERIFIER_CODE],
                result.answer,
                ground_truth["candidate_answer"],
            )
            answer_error = None
        except Exception as error:
            answer_error = f"{type(error).__name__}: {error}"
            scoring_runtime_errors.append(answer_error)

    state_score = 0.0
    state_error = None
    try:
        state_score = execute_state_verifier_code(
            task[TaskFields.STATE_VERIFIER_CODE],
            result.final_state or {},
            ground_truth["final_state"],
            ground_truth["init_state"],
        )
    except Exception as error:
        state_error = f"{type(error).__name__}: {error}"
        scoring_runtime_errors.append(state_error)

    rubric_score, rubric_result = _judge_rubric(
        agent=rubric_agent,
        task=task,
        result=result,
        run_dir=run_dir,
        max_attempts=judge_attempts,
    )
    passed = answer_score == 1.0 and state_score == 1.0 and rubric_score == 1.0
    return {
        "candidate_answer": deepcopy(result.answer),
        "candidate_final_state": deepcopy(result.final_state),
        "tool_trace": deepcopy(result.trace),
        "agent_error": result.error,
        "answer_verifier": {
            "score": answer_score,
            "passed": answer_score == 1.0,
            "error": answer_error,
        },
        "state_verifier": {
            "score": state_score,
            "passed": state_score == 1.0,
            "error": state_error,
            "mode": task.get("state_verification", {}).get("mode"),
        },
        "rubric": rubric_result,
        "scoring_runtime_errors": scoring_runtime_errors,
        "combined_passed": passed,
        "combined_score": (answer_score + state_score + rubric_score) / 3.0,
    }


def _missing_scoring_fields(task: dict[str, Any]) -> list[str]:
    required = (
        TaskFields.TASK_ID,
        TaskFields.TASK_PUBLIC,
        TaskFields.OUTPUT_SCHEMA,
        TaskFields.GROUND_TRUTH,
        TaskFields.VERIFIER_CODE,
        TaskFields.STATE_VERIFIER_CODE,
        TaskFields.RUBRIC_ITEMS,
    )
    return [field for field in required if not task.get(field)]


def process_single_task(
    task: dict[str, Any],
    *,
    step1_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    rubric_agent: RubricJudgeAgent,
    solve_fn: SolveFn = solve_with_codex,
    solver_timeout_seconds: int = 1800,
    max_solver_tool_calls: int = 100,
) -> dict[str, Any]:
    """Collect the requested number of valid rollouts for one task."""
    item = deepcopy(task)
    evaluations: list[dict[str, Any]] = []
    infrastructure_failures: list[dict[str, Any]] = []
    reasons: list[str] = []
    missing = _missing_scoring_fields(task)
    if task.get(TaskFields.SCORING_READY) is not True:
        reasons.append("scoring_not_ready")
    if missing:
        reasons.append("missing_fields:" + ",".join(missing))

    if not reasons:
        for run_index in range(policy.difficulty_eval_runs):
            accepted_rollout = False
            for attempt in range(1, policy.infrastructure_retries + 2):
                run_dir = (
                    output_dir
                    / "step5_runs"
                    / str(task[TaskFields.TASK_ID])
                    / f"run_{run_index:02d}"
                    / f"attempt_{attempt:02d}"
                )
                try:
                    result = solve_fn(
                        step1_path=step1_path,
                        task_text=task[TaskFields.TASK_PUBLIC],
                        output_schema=task[TaskFields.OUTPUT_SCHEMA],
                        model=model,
                        run_dir=run_dir,
                        max_tool_calls=max_solver_tool_calls,
                        timeout_seconds=solver_timeout_seconds,
                    )
                except Exception as error:
                    result = SolverResult(
                        None,
                        [],
                        None,
                        "",
                        f"{type(error).__name__}: {error}",
                        infrastructure_error=True,
                    )
                if result.infrastructure_error or result.final_state is None:
                    infrastructure_failures.append({
                        "run_id": run_index,
                        "attempt": attempt,
                        "phase": "solver",
                        "error": result.error or "missing final_state",
                    })
                    continue
                try:
                    evaluation = _evaluate_valid_rollout(
                        task=task,
                        result=result,
                        rubric_agent=rubric_agent,
                        run_dir=run_dir,
                        judge_attempts=policy.infrastructure_retries + 1,
                    )
                except Exception as error:
                    infrastructure_failures.append({
                        "run_id": run_index,
                        "attempt": attempt,
                        "phase": "rubric_judge",
                        "error": f"{type(error).__name__}: {error}",
                    })
                    continue
                evaluation["run_id"] = run_index
                evaluation["infrastructure_attempt"] = attempt
                evaluations.append(evaluation)
                if evaluation["scoring_runtime_errors"]:
                    reasons.append(
                        "scoring_runtime_error:"
                        + " | ".join(evaluation["scoring_runtime_errors"])
                    )
                accepted_rollout = True
                break
            if not accepted_rollout:
                reasons.append(f"infrastructure_retry_exhausted:run_{run_index:02d}")

    passes = sum(record["combined_passed"] for record in evaluations)
    valid_runs = len(evaluations)
    difficulty = classify_difficulty(passes, valid_runs)
    if valid_runs != policy.difficulty_eval_runs:
        reasons.append(
            f"incomplete_valid_rollouts:{valid_runs}/{policy.difficulty_eval_runs}"
        )
    if valid_runs == policy.difficulty_eval_runs and passes == 0:
        reasons.append("zero_successful_rollouts")
    elif valid_runs == policy.difficulty_eval_runs and passes < policy.minimum_passing_runs:
        reasons.append(
            f"insufficient_passing_rollouts:{passes}/{policy.minimum_passing_runs}"
        )

    item[TaskFields.DIFFICULTY_EVALUATIONS] = evaluations
    item[TaskFields.INFRASTRUCTURE_FAILURES] = infrastructure_failures
    item[TaskFields.VALID_ROLLOUTS] = valid_runs
    item[TaskFields.PASS_COUNT] = passes
    item[TaskFields.EMPIRICAL_PASS_RATE] = passes / valid_runs if valid_runs else None
    item[TaskFields.PASS_AT_K] = {
        f"pass_at_{k}": estimate_pass_at_k(valid_runs, passes, k)
        for k in (1, 2, 3)
    }
    item[TaskFields.DIFFICULTY_BUCKET] = difficulty
    item[TaskFields.PUBLISH_STATUS] = "published" if not reasons else "rework"
    item[TaskFields.PUBLISH_REASON] = reasons
    return item


def _publication_record(task: dict[str, Any], step1_receipt: dict[str, Any]) -> dict[str, Any]:
    ground_truth = task[TaskFields.GROUND_TRUTH]
    public_environment = step1_receipt["public_environment"]
    declaration = public_environment["environment"]
    return {
        "schema_version": "1.0",
        "env_id": task[TaskFields.ENV_ID],
        "environment_summary": (
            declaration.get("summary") or declaration.get("description", "")
        ),
        "environment_contract": deepcopy(declaration),
        "candidate_tools": deepcopy(public_environment["tools"]),
        "environment_package": step1_receipt["environment_package"],
        "initial_state": deepcopy(ground_truth["init_state"]),
        "task_id": task[TaskFields.TASK_ID],
        "task": task[TaskFields.TASK_PUBLIC],
        "output_schema": deepcopy(task[TaskFields.OUTPUT_SCHEMA]),
        "rubric_items": deepcopy(task[TaskFields.RUBRIC_ITEMS]),
        "ground_truth_answer": deepcopy(ground_truth["candidate_answer"]),
        "ground_truth_state": deepcopy(ground_truth["final_state"]),
        "ground_truth_state_diff": deepcopy(ground_truth["state_diff"]),
        "answer_verifier_code": task[TaskFields.VERIFIER_CODE],
        "state_verifier_code": task[TaskFields.STATE_VERIFIER_CODE],
        "state_verification": deepcopy(task.get("state_verification")),
        "difficulty": {
            "bucket": task[TaskFields.DIFFICULTY_BUCKET],
            "valid_rollouts": task[TaskFields.VALID_ROLLOUTS],
            "pass_count": task[TaskFields.PASS_COUNT],
            "empirical_pass_rate": task[TaskFields.EMPIRICAL_PASS_RATE],
            "pass_at_k": deepcopy(task[TaskFields.PASS_AT_K]),
        },
        "provenance": {
            "archetype_id": task[TaskFields.ARCHETYPE_ID],
            "task_archetype": deepcopy(task.get("task_archetype")),
        },
    }


def run_step5(
    *,
    step1_path: Path,
    step4_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    model: str,
    rubric_agent: RubricJudgeAgent,
    solve_fn: SolveFn = solve_with_codex,
    solver_timeout_seconds: int = 1800,
    max_solver_tool_calls: int = 100,
) -> Step5Result:
    """Evaluate every scoring-ready task and split publication from rework."""
    policy.validate()
    step1_receipt = read_json(step1_path.resolve())
    tasks = read_records(step4_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            step1_path=step1_path,
            output_dir=output_dir,
            policy=policy,
            model=model,
            rubric_agent=rubric_agent,
            solve_fn=solve_fn,
            solver_timeout_seconds=solver_timeout_seconds,
            max_solver_tool_calls=max_solver_tool_calls,
        )
        for task in tasks
    ]
    published = [
        _publication_record(task, step1_receipt)
        for task in records
        if task[TaskFields.PUBLISH_STATUS] == "published"
    ]
    rework = [
        task for task in records if task[TaskFields.PUBLISH_STATUS] == "rework"
    ]
    difficulty_path = output_dir / "step5_difficulty.jsonl"
    rework_path = output_dir / "step5_rework.jsonl"
    final_path = output_dir / "final" / "task_gen_final.json"
    write_jsonl(difficulty_path, records)
    write_jsonl(rework_path, rework)
    write_json(final_path, published)
    return Step5Result(
        difficulty_path,
        rework_path,
        final_path,
        len(records),
        len(published),
        len(rework),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step4-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--minimum-passing-runs", type=int, default=1)
    parser.add_argument("--infrastructure-retries", type=int, default=2)
    parser.add_argument("--agent-timeout-seconds", type=int, default=1800)
    arguments = parser.parse_args()
    agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.agent_timeout_seconds,
        sandbox="workspace-write",
        network_access=False,
        reasoning_effort="high",
    )
    result = run_step5(
        step1_path=arguments.step1_path,
        step4_path=arguments.step4_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            difficulty_eval_runs=arguments.runs,
            minimum_passing_runs=arguments.minimum_passing_runs,
            infrastructure_retries=arguments.infrastructure_retries,
        ),
        model=arguments.model,
        rubric_agent=agent,
        solver_timeout_seconds=arguments.agent_timeout_seconds,
    )
    print(result.final_path)


if __name__ == "__main__":
    main()
