"""Step 3：真实执行并按需调试 Step 2 生成的参考程序。

处理顺序与 OmniaBench Step 3 对齐：先执行原代码；只有失败时才把任务、完整
公开工具契约、当前代码和真实错误交给修复 Agent；每次修复后从相同干净基线
重新执行，直到成功或耗尽轮数。

输入：``step2_task_solution.json``。
输出：``step3_debug_solution.jsonl`` 和 ``step3_debug_solution.json``。
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
from ..utils.reference_program import ProgramExecutionResult, execute_reference_program


class SolutionRepairAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step3Result:
    json_path: Path
    jsonl_path: Path
    total: int
    succeeded: int


def build_solution_repair_prompt(round_index: int) -> str:
    return f"""你正在执行 Program-form TaskGen Step 3 的第 {round_index} 轮参考程序修复。

读取工作目录中的：

- `repair_request.json`：任务正文、output_schema、当前 solution_code 和真实执行错误；
- `environment.public.json`：完整公开工具 description/inputSchema/outputSchema。

只修复参考程序的语法、参数来源、工具返回层级、控制流或 final_answer 构造错误。
不得改变任务目标，不得读取 state/，不得硬编码最终答案，不得增加环境没有的能力。
程序唯一环境接口是 `call_tool(name, arguments)`，最后一条语句必须给
`final_answer` 赋值。

最终只写 `repair.json`：

```json
{{
  "solution_code": "修复后的完整 Python 代码",
  "modification": "具体修改说明"
}}
```
"""


def _execute(
    package: Any,
    task: dict[str, Any],
    code: str,
    policy: ProgramGenerationPolicy,
) -> ProgramExecutionResult:
    return execute_reference_program(
        package,
        code,
        task[TaskFields.OUTPUT_SCHEMA],
        timeout_seconds=policy.execution_timeout_seconds,
    )


def _repair_code(
    *,
    agent: SolutionRepairAgent,
    package: Any,
    task: dict[str, Any],
    code: str,
    execution: ProgramExecutionResult,
    round_index: int,
    debug_root: Path,
) -> tuple[str, str]:
    round_dir = debug_root / str(task[TaskFields.TASK_ID]) / f"round_{round_index:02d}"
    if round_dir.exists():
        shutil.rmtree(round_dir)
    round_dir.mkdir(parents=True)
    write_json(round_dir / "environment.public.json", package.public_environment())
    write_json(round_dir / "repair_request.json", {
        "task_internal": task[TaskFields.TASK_INTERNAL],
        "output_schema": task[TaskFields.OUTPUT_SCHEMA],
        "solution_code": code,
        "execution_error_type": execution.error_type,
        "execution_error": execution.error,
        "tool_trace": execution.trace,
    })
    prompt = build_solution_repair_prompt(round_index)
    (round_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    agent.run(prompt, working_directory=round_dir)
    payload = read_json(round_dir / "repair.json")
    if not isinstance(payload, dict) or not str(payload.get("solution_code") or "").strip():
        raise ValueError("修复 Agent 没有返回非空 solution_code")
    return str(payload["solution_code"]).strip(), str(payload.get("modification") or "")


def process_single_task(
    task: dict[str, Any],
    *,
    package: Any,
    policy: ProgramGenerationPolicy,
    repair_agent: SolutionRepairAgent | None,
    debug_root: Path,
) -> dict[str, Any]:
    """执行一条任务，并在失败时迭代修复参考程序。"""
    item = deepcopy(task)
    code = str(task.get(TaskFields.SOLUTION_CODE) or "")
    history: list[dict[str, Any]] = []
    preflight = task.get("step2_preflight")
    if (
        not code
        or not isinstance(task.get(TaskFields.OUTPUT_SCHEMA), dict)
        or not isinstance(preflight, dict)
        or preflight.get("success") is not True
    ):
        item[TaskFields.SOLUTION_CODE_FIXED] = None
        item[TaskFields.SOLUTION_DEBUG_SUCCESS] = False
        item[TaskFields.SOLUTION_DEBUG_HISTORY] = [{
            "error": "Step 2 missing solution_code/output_schema or successful preflight"
        }]
        item[TaskFields.SOLUTION_EXECUTION_TRAJECTORY] = None
        item[TaskFields.SOLUTION_TRACE] = []
        item[TaskFields.TASK_INTERNAL_FINETUNED] = ""
        item[TaskFields.TASK_FINETUNE_SUCCESS] = False
        item[TaskFields.TASK_FINETUNE_MODIFICATION] = ""
        item[TaskFields.TASK_FINETUNE_NEEDED] = False
        item[TaskFields.GROUND_TRUTH] = None
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = None
        return item
    execution = _execute(package, task, code, policy)
    repair_round = 0
    while (
        not execution.success
        and repair_agent is not None
        and repair_round < policy.max_repair_rounds
    ):
        repair_round += 1
        before_error = f"{execution.error_type}: {execution.error}"
        try:
            code, modification = _repair_code(
                agent=repair_agent,
                package=package,
                task=task,
                code=code,
                execution=execution,
                round_index=repair_round,
                debug_root=debug_root,
            )
            history.append({
                "round": repair_round,
                "error": before_error,
                "modification": modification,
            })
        except Exception as error:
            history.append({
                "round": repair_round,
                "error": before_error,
                "repair_error": f"{type(error).__name__}: {error}",
            })
            break
        execution = _execute(package, task, code, policy)

    item[TaskFields.SOLUTION_CODE_FIXED] = code if execution.success else None
    item[TaskFields.SOLUTION_DEBUG_SUCCESS] = execution.success
    item[TaskFields.SOLUTION_DEBUG_HISTORY] = history
    item[TaskFields.SOLUTION_EXECUTION_TRAJECTORY] = deepcopy(execution.trace)
    item[TaskFields.SOLUTION_TRACE] = deepcopy(execution.trace) if execution.success else []
    item[TaskFields.TASK_INTERNAL_FINETUNED] = (
        item[TaskFields.TASK_INTERNAL] if execution.success else ""
    )
    item[TaskFields.TASK_FINETUNE_SUCCESS] = execution.success
    item[TaskFields.TASK_FINETUNE_MODIFICATION] = (
        "No Step 3 rewrite: preserve the generated task goal exactly."
        if execution.success else ""
    )
    item[TaskFields.TASK_FINETUNE_NEEDED] = False
    if execution.success:
        item[TaskFields.GROUND_TRUTH] = {
            "candidate_answer": deepcopy(execution.answer),
            "init_state": deepcopy(execution.initial_state),
            "final_state": deepcopy(execution.final_state),
        }
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = deepcopy(execution.final_state)
    else:
        item[TaskFields.GROUND_TRUTH] = None
        item[TaskFields.POST_SOLUTION_STATE_SNAPSHOT] = None
        item["solution_debug_error"] = f"{execution.error_type}: {execution.error}"
    return item


def run_step3(
    *,
    step1_path: Path,
    step2_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    repair_agent: SolutionRepairAgent | None = None,
) -> Step3Result:
    package = load_frozen_package(step1_path)
    tasks = read_records(step2_path)
    output_dir = output_dir.resolve()
    debug_root = output_dir / "step3_debug"
    results = [
        process_single_task(
            task,
            package=package,
            policy=policy,
            repair_agent=repair_agent,
            debug_root=debug_root,
        )
        for task in tasks
    ]
    json_path = output_dir / "step3_debug_solution.json"
    jsonl_path = output_dir / "step3_debug_solution.jsonl"
    write_jsonl(jsonl_path, results)
    write_json(json_path, results)
    return Step3Result(
        json_path=json_path,
        jsonl_path=jsonl_path,
        total=len(results),
        succeeded=sum(bool(item[TaskFields.SOLUTION_DEBUG_SUCCESS]) for item in results),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step2-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-debug-rounds", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--agent-timeout-seconds", type=int, default=1800)
    arguments = parser.parse_args()

    from utils.search_agent.codex import CodexAgentClient

    policy = ProgramGenerationPolicy(max_repair_rounds=arguments.max_debug_rounds)
    agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.agent_timeout_seconds,
        sandbox="workspace-write",
        enable_web_search=False,
        network_access=False,
        reasoning_effort="high",
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    print(run_step3(
        step1_path=arguments.step1_path,
        step2_path=arguments.step2_path,
        output_dir=arguments.output_dir,
        policy=policy,
        repair_agent=agent,
    ).json_path)


if __name__ == "__main__":
    main()
