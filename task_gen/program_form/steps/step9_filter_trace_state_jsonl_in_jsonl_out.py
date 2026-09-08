"""Step 9：检查公开任务、参考轨迹和最终状态变化是否语义一致。

输入：``step8_filter_rewrite_kept.jsonl``。
输出：``step9_filter_trace_state.jsonl`` 和对应 kept JSONL。
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.environment import load_frozen_package
from ..utils.io import read_json, read_records, write_json, write_jsonl


class TraceStateJudgeAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step9Result:
    output_path: Path
    kept_path: Path
    total: int
    kept: int


def build_trace_state_prompt() -> str:
    return """你正在执行 Program-form TaskGen Step 9 语义一致性审查。

读取 `consistency_request.json`，判断 task_public 是否能够合理解释参考工具轨迹和
最终状态变化。重点检查：任务要求的对象、范围、约束和结果是否都得到轨迹支持；
轨迹是否执行了任务没有要求的额外业务动作；最终状态是否符合任务目标。

不要因为轨迹不是唯一可行方案而拒绝，也不要要求任务正文泄露工具名。
最终只写 `review.json`：

```json
{"consistent": true, "reason": "具体证据"}
```
"""


def process_single_task(
    task: dict[str, Any],
    *,
    public_environment: dict[str, Any],
    policy: ProgramGenerationPolicy,
    agent: TraceStateJudgeAgent | None,
    debug_root: Path,
) -> dict[str, Any]:
    item = deepcopy(task)
    task_public = str(task.get(TaskFields.TASK_PUBLIC) or "").strip()
    ground_truth = task.get(TaskFields.GROUND_TRUTH)
    trace = task.get(TaskFields.SOLUTION_TRACE)
    if not task_public or not isinstance(ground_truth, dict) or not isinstance(trace, list):
        item[TaskFields.STEP9_CONSISTENT] = False
        item[TaskFields.STEP9_FILTER_REASON] = "Missing task_public, ground_truth, or solution_trace"
        return item
    if agent is None:
        item[TaskFields.STEP9_CONSISTENT] = False
        item[TaskFields.STEP9_FILTER_REASON] = "Trace/state judge agent is unavailable"
        return item

    votes: list[dict[str, Any]] = []
    for run_index in range(policy.trace_state_judge_runs):
        run_dir = debug_root / str(task[TaskFields.TASK_ID]) / f"run_{run_index:02d}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        request = {
            "environment": public_environment,
            "task_public": task_public,
            "output_schema": task.get(TaskFields.OUTPUT_SCHEMA),
            "solution_trace": trace,
            "initial_state": ground_truth.get("init_state"),
            "final_state": ground_truth.get("final_state"),
            "state_diff": ground_truth.get("state_diff"),
        }
        write_json(run_dir / "consistency_request.json", request)
        prompt = build_trace_state_prompt()
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            agent.run(prompt, working_directory=run_dir)
            review = read_json(run_dir / "review.json")
            if not isinstance(review, dict) or type(review.get("consistent")) is not bool:
                raise ValueError("review.json 缺少 boolean consistent")
            votes.append({
                "run_id": run_index,
                "parsed": True,
                "consistent": review["consistent"],
                "reason": str(review.get("reason") or ""),
            })
        except Exception as error:
            votes.append({
                "run_id": run_index,
                "parsed": False,
                "consistent": False,
                "reason": f"Judge error: {type(error).__name__}: {error}",
            })
    pass_count = sum(bool(vote["consistent"]) for vote in votes)
    consistent = pass_count > 0
    split_vote = 0 < pass_count < len(votes)
    item["step9_judgments"] = votes
    item["step9_judge_runs"] = len(votes)
    item["step9_review_required"] = split_vote
    item["state_trace_prompt_length"] = len(
        str(request) if "request" in locals() else ""
    )
    item[TaskFields.STEP9_CONSISTENT] = consistent
    item["state_trace_pass"] = consistent
    item[TaskFields.STEP9_FILTER_REASON] = (
        f"Step 9 vote {pass_count}/{len(votes)} consistent"
        + (" (split; retained for review)" if split_vote else "")
    )
    return item


def run_step9(
    *,
    step1_path: Path,
    step8_kept_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    agent: TraceStateJudgeAgent | None,
) -> Step9Result:
    package = load_frozen_package(step1_path)
    public_environment = package.public_environment()
    tasks = read_records(step8_kept_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            public_environment=public_environment,
            policy=policy,
            agent=agent,
            debug_root=output_dir / "step9_debug",
        )
        for task in tasks
    ]
    kept = [item for item in records if item.get(TaskFields.STEP9_CONSISTENT)]
    output_path = output_dir / "step9_filter_trace_state.jsonl"
    kept_path = output_dir / "step9_filter_trace_state_kept.jsonl"
    write_jsonl(output_path, records)
    write_jsonl(kept_path, kept)
    return Step9Result(output_path, kept_path, len(records), len(kept))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step8-kept-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--judge-runs", type=int, default=3)
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient
    result = run_step9(
        step1_path=arguments.step1_path,
        step8_kept_path=arguments.step8_kept_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(trace_state_judge_runs=arguments.judge_runs),
        agent=CodexAgentClient(model=arguments.model, sandbox="workspace-write"),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
