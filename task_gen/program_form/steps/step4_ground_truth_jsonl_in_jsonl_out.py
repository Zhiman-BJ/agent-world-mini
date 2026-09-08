"""Step 4：从干净基线重放修复后的参考程序并固化 Ground Truth。

输入：``step3_debug_solution.jsonl``。
输出：``step4_ground_truth.jsonl``。

本步不调用模型。Step 3 的临时执行结果不会被直接复制为标准答案；每条任务都
重新从 Step 1 冻结状态执行 ``solution_code_fixed``。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.environment import load_frozen_package
from ..utils.io import read_records, write_jsonl
from ..utils.reference_program import execute_reference_program


@dataclass(frozen=True)
class Step4Result:
    output_path: Path
    total: int
    ground_truth_count: int


def process_single_task(
    task: dict[str, Any],
    *,
    package: Any,
    policy: ProgramGenerationPolicy,
) -> dict[str, Any]:
    item = deepcopy(task)
    if not task.get(TaskFields.SOLUTION_DEBUG_SUCCESS):
        item[TaskFields.GROUND_TRUTH] = None
        item[TaskFields.SOLUTION_CODE_FIXED] = None
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = None
        item[TaskFields.SOLUTION_TRACE] = None
        return item

    code = str(task.get(TaskFields.SOLUTION_CODE_FIXED) or "")
    schema = task.get(TaskFields.OUTPUT_SCHEMA)
    execution = execute_reference_program(
        package,
        code,
        schema,
        timeout_seconds=policy.execution_timeout_seconds,
    )
    changed = bool(execution.state_diff.get("changed_assets")) or any(
        execution.state_diff.get(field) for field in ("created", "modified", "deleted")
    )
    if execution.success and (changed or not policy.require_state_change):
        item[TaskFields.GROUND_TRUTH] = {
            "candidate_answer": deepcopy(execution.answer),
            "init_state": deepcopy(execution.initial_state),
            "final_state": deepcopy(execution.final_state),
            "state_diff": deepcopy(execution.state_diff),
        }
        item[TaskFields.SOLUTION_TRACE] = deepcopy(execution.trace)
        item["solution_trace_length"] = len(execution.trace)
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = deepcopy(execution.final_state)
        item.pop("ground_truth_error", None)
    else:
        item[TaskFields.GROUND_TRUTH] = None
        item[TaskFields.SOLUTION_DEBUG_SUCCESS] = False
        item[TaskFields.SOLUTION_CODE_FIXED] = None
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = None
        item[TaskFields.SOLUTION_TRACE] = None
        item["ground_truth_error"] = (
            f"{execution.error_type}: {execution.error}"
            if not execution.success
            else "Ground Truth replay did not produce the required state change"
        )
    return item


def run_step4(
    *,
    step1_path: Path,
    step3_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
) -> Step4Result:
    package = load_frozen_package(step1_path)
    tasks = read_records(step3_path)
    results = [
        process_single_task(task, package=package, policy=policy)
        for task in tasks
    ]
    output_path = output_dir.resolve() / "step4_ground_truth.jsonl"
    write_jsonl(output_path, results)
    return Step4Result(
        output_path,
        len(results),
        sum(item.get(TaskFields.GROUND_TRUTH) is not None for item in results),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step3-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-state-change", action="store_true")
    arguments = parser.parse_args()
    result = run_step4(
        step1_path=arguments.step1_path,
        step3_path=arguments.step3_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(require_state_change=arguments.require_state_change),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
