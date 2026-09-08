"""Step 8：按稳定性过滤，并改写最终对求解 Agent 可见的任务正文。

输入：``step7_consistency.jsonl``。
输出：``step8_filter_rewrite.jsonl`` 和 ``step8_filter_rewrite_kept.jsonl``。
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.contracts import TaskFields
from ..utils.environment import load_frozen_package
from ..utils.io import read_json, read_records, write_json, write_jsonl


STEP8_REWRITE_VERSION = "2026-08-17.1"


class TaskRewriteAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step8Result:
    output_path: Path
    kept_path: Path
    total: int
    kept: int


def build_rewrite_prompt() -> str:
    return """你正在执行 Program-form TaskGen Step 8。

读取 `rewrite_request.json`，将 task_internal 改写成自然、真实、面向最终用户的
task_public。必须保留业务目标、范围、硬约束、选择规则和期望结果，但不得泄露：

- 工具名或调用顺序；
- Python、call_tool、solution_code、final_answer；
- 数据库字段路径、内部资源 ID；
- Ground Truth 答案或参考轨迹中的具体结论。

output_schema 是独立协议，不要复制进任务正文。最终只写：

```json
{"task_public": "改写后的完整任务正文"}
```
"""


def filter_task(task: dict[str, Any]) -> tuple[bool, str]:
    if not task.get(TaskFields.SOLUTION_DEBUG_SUCCESS):
        return False, "Solution debug failed"
    if not task.get(TaskFields.GROUND_TRUTH):
        return False, "No ground truth available"
    if not task.get(TaskFields.VERIFIER_DEBUG_SUCCESS):
        return False, "Verifier debug failed"
    if TaskFields.CONSISTENCY_KEEP not in task:
        return False, "Missing canonical Step 7 consistency metrics"
    if not task[TaskFields.CONSISTENCY_KEEP]:
        return False, (
            f"Consistency pass count {task.get(TaskFields.CONSISTENCY_PASS_COUNT, 0)} "
            f"is below threshold {task.get(TaskFields.CONSISTENCY_THRESHOLD)}"
        )
    return True, "Passed canonical Step 7 consistency threshold"


def process_single_task(
    task: dict[str, Any],
    *,
    public_environment: dict[str, Any],
    initial_state: Any,
    agent: TaskRewriteAgent | None,
    debug_root: Path,
) -> dict[str, Any]:
    item = deepcopy(task)
    keep, reason = filter_task(task)
    item[TaskFields.FILTER_STATUS] = "kept" if keep else "filtered"
    item[TaskFields.FILTER_REASON] = reason
    item["step8_rewrite_version"] = STEP8_REWRITE_VERSION
    if not keep:
        item[TaskFields.REWRITE_SUCCESS] = False
        return item
    if agent is None:
        item[TaskFields.REWRITE_SUCCESS] = False
        item[TaskFields.FILTER_STATUS] = "filtered"
        item[TaskFields.FILTER_REASON] = "Task rewrite agent is unavailable"
        return item

    task_dir = debug_root / str(task[TaskFields.TASK_ID])
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)
    write_json(task_dir / "rewrite_request.json", {
        "task_internal": task.get(TaskFields.TASK_INTERNAL_FINETUNED)
        or task.get(TaskFields.TASK_INTERNAL),
        "output_schema": task.get(TaskFields.OUTPUT_SCHEMA),
        "environment": public_environment,
        "initial_state": initial_state,
        "solution_trace": task.get(TaskFields.SOLUTION_TRACE, []),
    })
    prompt = build_rewrite_prompt()
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    try:
        agent.run(prompt, working_directory=task_dir)
        payload = read_json(task_dir / "rewrite.json")
        task_public = str(payload.get("task_public") or "").strip()
        if len(task_public) < 40:
            raise ValueError("task_public 过短或缺失")
        item[TaskFields.TASK_PUBLIC] = task_public
        item[TaskFields.REWRITE_SUCCESS] = True
    except Exception as error:
        item[TaskFields.TASK_PUBLIC] = task.get(TaskFields.TASK_INTERNAL_FINETUNED) or ""
        item[TaskFields.REWRITE_SUCCESS] = False
        item[TaskFields.FILTER_STATUS] = "filtered"
        item[TaskFields.FILTER_REASON] = f"Task rewrite failed: {type(error).__name__}: {error}"
    return item


def run_step8(
    *,
    step1_path: Path,
    step7_path: Path,
    output_dir: Path,
    agent: TaskRewriteAgent | None,
) -> Step8Result:
    package = load_frozen_package(step1_path)
    receipt = read_json(step1_path)
    initial_state = receipt.get("initial_state", {}) if isinstance(receipt, dict) else {}
    public_environment = package.public_environment()
    tasks = read_records(step7_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            public_environment=public_environment,
            initial_state=initial_state,
            agent=agent,
            debug_root=output_dir / "step8_debug",
        )
        for task in tasks
    ]
    kept = [item for item in records if item.get(TaskFields.FILTER_STATUS) == "kept"]
    output_path = output_dir / "step8_filter_rewrite.jsonl"
    kept_path = output_dir / "step8_filter_rewrite_kept.jsonl"
    write_jsonl(output_path, records)
    write_jsonl(kept_path, kept)
    return Step8Result(output_path, kept_path, len(records), len(kept))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step7-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient
    result = run_step8(
        step1_path=arguments.step1_path, step7_path=arguments.step7_path,
        output_dir=arguments.output_dir,
        agent=CodexAgentClient(model=arguments.model, sandbox="workspace-write"),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
