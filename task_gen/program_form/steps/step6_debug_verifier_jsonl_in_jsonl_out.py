"""Step 6：执行、检查并按需修复 Step 5 的答案 Verifier。

输入：``step5_verifier_code.jsonl``。
输出：``step6_debug_verifier.jsonl``。
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.io import read_json, read_records, write_json, write_jsonl
from ..utils.verifier import verifier_smoke_test


class VerifierRepairAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step6Result:
    output_path: Path
    total: int
    succeeded: int


def build_verifier_repair_prompt(round_index: int) -> str:
    return f"""你正在执行 Program-form TaskGen Step 6 的第 {round_index} 轮 Verifier 修复。

读取 `verifier_debug_request.json`。只修复语法、运行时、变量引用和正负例暴露的
验证漏洞，不得降低标准。标准答案与自身比较必须返回 1.0；缺字段、多字段、错值、
错类型均不能返回 1.0。Verifier 不得读取环境或调用工具。

最终只写 `verifier.json`，格式为：
```json
{{"verifier_code": "修复后的完整 Python 代码", "modification": "修改说明"}}
```
"""


def process_single_task(
    task: dict[str, Any],
    *,
    policy: ProgramGenerationPolicy,
    agent: VerifierRepairAgent | None,
    debug_root: Path,
) -> dict[str, Any]:
    item = deepcopy(task)
    ground_truth = task.get(TaskFields.GROUND_TRUTH)
    answer = ground_truth.get("candidate_answer") if isinstance(ground_truth, dict) else None
    code = str(task.get(TaskFields.VERIFIER_CODE) or "")
    if not isinstance(answer, dict) or not code:
        item[TaskFields.VERIFIER_DEBUG_SUCCESS] = False
        item[TaskFields.VERIFIER_DEBUG_HISTORY] = []
        return item

    success, errors = verifier_smoke_test(code, answer)
    history: list[dict[str, Any]] = []
    for round_index in range(1, policy.max_repair_rounds + 1):
        if success or agent is None:
            break
        task_dir = debug_root / str(task[TaskFields.TASK_ID]) / f"round_{round_index:02d}"
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True)
        write_json(task_dir / "verifier_debug_request.json", {
            "task": task.get(TaskFields.TASK_INTERNAL_FINETUNED)
            or task.get(TaskFields.TASK_INTERNAL),
            "output_schema": task.get(TaskFields.OUTPUT_SCHEMA),
            "ground_truth_answer": answer,
            "verifier_code": code,
            "smoke_test_errors": errors,
        })
        prompt = build_verifier_repair_prompt(round_index)
        (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            agent.run(prompt, working_directory=task_dir)
            payload = read_json(task_dir / "verifier.json")
            code = str(payload.get("verifier_code") or "")
            history.append({
                "round": round_index,
                "errors": errors,
                "modification": payload.get("modification", ""),
            })
            success, errors = verifier_smoke_test(code, answer)
        except Exception as error:
            history.append({
                "round": round_index,
                "errors": errors,
                "repair_error": f"{type(error).__name__}: {error}",
            })
            break

    item[TaskFields.VERIFIER_CODE] = code
    item[TaskFields.VERIFIER_DEBUG_SUCCESS] = success
    item[TaskFields.VERIFIER_DEBUG_HISTORY] = history
    if errors:
        item["verifier_debug_error"] = " | ".join(errors)
    return item


def run_step6(
    *,
    step5_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    agent: VerifierRepairAgent | None = None,
) -> Step6Result:
    tasks = read_records(step5_path)
    debug_root = output_dir.resolve() / "step6_debug"
    records = [
        process_single_task(task, policy=policy, agent=agent, debug_root=debug_root)
        for task in tasks
    ]
    output_path = output_dir.resolve() / "step6_debug_verifier.jsonl"
    write_jsonl(output_path, records)
    return Step6Result(
        output_path,
        len(records),
        sum(bool(item.get(TaskFields.VERIFIER_DEBUG_SUCCESS)) for item in records),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step5-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-debug-rounds", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.6-terra")
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient
    result = run_step6(
        step5_path=arguments.step5_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(max_repair_rounds=arguments.max_debug_rounds),
        agent=CodexAgentClient(model=arguments.model, sandbox="workspace-write"),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
