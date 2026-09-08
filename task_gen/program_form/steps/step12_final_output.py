"""Step 12：把 Step 11 记录投影成最终 Program-form benchmark 格式。

OmniaBench 源文件历史名称是 ``step13_final_output.py``，但 Runner 将它作为
Step 12。本项目直接使用 Runner 编号命名。

输入：``step11_difficulty.jsonl``。
输出：``final/task_gen_final_english.json``。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.contracts import TaskFields
from ..utils.environment import load_frozen_package
from ..utils.io import read_records, write_json


@dataclass(frozen=True)
class Step12Result:
    output_path: Path
    task_count: int


def _tool_sequence(trace: Any) -> list[dict[str, Any]]:
    if not isinstance(trace, list):
        return []
    return [
        {
            "step": item.get("step", item.get("index")),
            "name": item.get("tool"),
            "params": deepcopy(item.get("arguments", {})),
            "observation": deepcopy(item.get("result")),
        }
        for item in trace
        if isinstance(item, dict)
    ]


def process_single_task(
    task: dict[str, Any],
    *,
    package: Any,
    task_index: int,
) -> dict[str, Any]:
    ground_truth = task.get(TaskFields.GROUND_TRUTH) or {}
    rubrics = [
        {
            "section": item.get("section", "task_specific"),
            "id": item.get("id", ""),
            "points": item.get("points", 1),
            "text": item.get("text", ""),
        }
        for item in task.get(TaskFields.RUBRIC_ITEMS, [])
        if isinstance(item, dict)
    ]
    return {
        "env_id": task[TaskFields.ENV_ID],
        "environment_summary": package.environment.get("summary")
        or package.environment.get("description", ""),
        "task_id": f"{task[TaskFields.ENV_ID]}_P_3.0_{task_index}",
        "task": task.get(TaskFields.TASK_PUBLIC)
        or task.get(TaskFields.TASK_INTERNAL_FINETUNED)
        or task.get(TaskFields.TASK_INTERNAL, ""),
        "output_schema": deepcopy(task.get(TaskFields.OUTPUT_SCHEMA, {})),
        "candidate_tools": deepcopy(package.public_environment()["tools"]),
        # 这是与 OmniaBench init_config 唯一不同的最终字段：真实初态按路径交付。
        "initial_state": (
            "baseline_environment/state"
            if package.package_format == "v2"
            else "baseline_environment/workspace"
        ),
        "environment_package": "baseline_environment",
        "rubrics": rubrics,
        "ground_truth_answer": deepcopy(ground_truth.get("candidate_answer", {})),
        "verifier_code": task.get(TaskFields.VERIFIER_CODE, ""),
        "additional_information": {
            "final_state": deepcopy(ground_truth.get("final_state", {})),
            "tool_sequence": _tool_sequence(task.get(TaskFields.SOLUTION_TRACE)),
        },
    }


def run_step12(
    *,
    step1_path: Path,
    step11_path: Path,
    output_dir: Path,
) -> Step12Result:
    package = load_frozen_package(step1_path)
    tasks = read_records(step11_path)
    final = [
        process_single_task(task, package=package, task_index=index)
        for index, task in enumerate(tasks)
    ]
    output_path = output_dir.resolve() / "final" / "task_gen_final_english.json"
    write_json(output_path, final)
    return Step12Result(output_path, len(final))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step11-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(run_step12(
        step1_path=arguments.step1_path,
        step11_path=arguments.step11_path,
        output_dir=arguments.output_dir,
    ).output_path)


if __name__ == "__main__":
    main()
