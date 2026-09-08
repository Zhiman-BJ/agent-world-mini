"""Step 2：联合生成任务、结果 Schema 和隐藏参考程序。

输入：Step 1 冻结的环境包、工具契约和初始 ``state/``。
输出：``step2_task_solution.json``。

与 OmniaBench Step 2 一致，本步同时生成 ``task_internal``、
``output_schema`` 和 ``solution_code``，并做一次真实执行 preflight。
只有 preflight 成功的候选才进入 Step 3。
"""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from ..utils.contracts import ProgramGenerationPolicy
from ..utils.contracts import ProgramTaskCandidate, TaskFields
from ..utils.environment import CompleteEnvironmentPackage, load_frozen_package
from ..utils.io import read_json, write_json
from ..utils.reference_program import execute_reference_program
from ..utils.tool_runtime import snapshot_state


DEFAULT_PROGRAM_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "high"


def build_program_generation_prompt(
    *,
    round_index: int,
    policy: ProgramGenerationPolicy,
) -> str:
    """构造 Step 2 完整任务生成 Prompt。"""
    mutation_requirement = (
        "每个任务都必须完成一项由业务目标自然要求的状态变更，并在变更后验证结果。"
        if policy.require_state_change
        else "任务可以是只读分析，也可以包含状态变更；由真实业务目标决定，不要为了显得复杂而强行写入。"
    )
    return f"""# 你的身份

你是 Agent-World 的资深业务任务架构师。你不是在展示工具，也不是在编写工具测试；你要从一个已经构建好的真实业务环境中，设计能够衡量 Agent 是否会调查、判断并完成工作的 Program-form benchmark 任务。

你设计的任务稍后会交给另一个求解 Agent。求解 Agent 只能看到任务正文、结果 Schema 和公开工具协议，不会直接看到 ``state/``、工具内部代码、你的设计说明或参考程序。因此，任务必须仅凭正文和工具调用可解，不能依赖隐藏提示。

# 你拥有的材料

工作目录中只有本次任务设计所需的材料：

- `environment.public.json`：环境名称/描述、Record Set 字段、已验证 Relationship、Filesystem Scope 结构/访问权限，以及公开工具的完整 inputSchema/outputSchema；
- `state/`：初始业务状态的隔离只读副本。Record Set 位于 `state/records.sqlite`，文件资料位于 `state/filesystem_scopes/<scope_id>/`，供你确认真实候选对象、取值、关系和可解性；
- `generation_request.json`：数量与执行质量门；
- `candidate.schema.json`：最终候选文件的严格格式；
- `validation_feedback.json`：已接受任务和上一轮候选的真实执行失败原因。

`environment.public.json` 是环境结构和工具接口的权威事实来源，`state/` 是初始业务事实来源。工具 `outputSchema` 明确规定成功数据在 `data` 中的真实层级，不得自行猜测返回字段。不要查看工作目录之外的仓库、历史任务或其它环境，也不要修改 `state/`。环境没有提供的能力、数据或业务规则不能自行补造。

# 合格任务的核心定义

一个合格任务应当对应现实工作中一个完整、连贯的业务结果。它通常具有这样的因果结构：

```text
发现候选或确定范围
  -> 收集会影响决策的多类证据
  -> 应用硬约束排除不合格对象或方案
  -> 在剩余候选中比较、排序、聚合或作出判断
  -> 执行必要动作（如果业务目标要求）
  -> 返回并核实用户真正关心的结果
```

不要求每个任务机械包含上述全部步骤，但每次工具调用必须服务于同一个业务闭环，并且前一步结果应实际影响后续判断、参数或最终答案。

任务难度应主要来自：

- 多个真实候选对象或方案；
- 至少三类语义不同、需要联合使用的证据；
- 至少一个表面相关但因状态、资格、权限、关系、阈值或证据不足而应排除的候选；
- 先过滤后比较，或先调查后执行的依赖关系；
- 需要从工具结果动态取得 ID、范围、数值或状态，而不是把答案预先写死。

工具调用数量只是最低质量门。本次每个任务真实执行至少需要 {policy.min_tool_calls} 次调用，并覆盖至少 {policy.min_distinct_tools} 个不同工具。不得通过重复查询、把一次查询拆成多次、无意义验证或调用与结论无关的工具来凑数量。

{mutation_requirement}

# 任务设计方法

对每个候选，在内部依次完成以下工作：

1. 盘点环境中可形成闭环的业务对象、关系、状态和工具能力。
2. 从 `state/` 中选择真实存在且能够形成多个候选与唯一结论的数据范围。SQLite 查询必须是只读的；不得为了让候选成立而改数据。
3. 明确一个用户真正关心的最终业务结果，而不是“调用若干工具并汇总”。
4. 写出决策所需的证据类别、硬排除条件和剩余候选的比较规则。
5. 为每个计划使用的工具说明它对业务结论的独立贡献；删除没有贡献的调用。
6. 确认公开工具能够取得所有必要信息并完成必要动作。
7. 编写参考程序，并在思考中逐项核对参数来源、完整 inputSchema、成功返回的真实 `data` 层级和输出字段。
8. 最后审查任务正文：求解 Agent 是否可以理解目标，但无法直接从正文猜出答案或照抄调用顺序。

不同候选任务必须具有不同的核心业务目标或决策结构。仅更换对象 ID、时间范围、阈值或措辞不算不同任务。`validation_feedback.json` 中已经接受的任务不得再次生成。

# 用户任务正文

`task_internal` 应像真实用户向专业同事提出的工作请求：清楚说明业务目标、处理范围、必须遵守的限制、选择偏好和期望返回结果。使用环境主要语言书写。

任务正文可以包含现实用户本来就知道的业务名称、日期、对象标识和阈值，但不能泄露实现方式。不要出现工具名、JSON 字段路径、参数名清单、调用顺序、Python、Schema、`call_tool`、`final_answer` 或“先调用 A 再调用 B”一类执行提示。不要把任务写成 SOP、验收测试或多个无关问题的清单。

# 隐藏参考程序

`solution_code` 不是给求解 Agent 的答案，而是用来证明任务可解并固化 Ground Truth 的参考实现。

唯一环境接口是：

```python
result = call_tool("tool_name", {{"argument": value}})
```

工具返回：

```python
{{"success": True, "data": ...}}
{{"success": False, "error": ...}}
```

参考程序应从工具结果动态取得候选、ID、证据和状态，使用 `for`、`if`、过滤、排序或聚合完成任务所需判断。每个 `call_tool` 的参数必须来自任务正文、环境规则或之前的成功工具结果。所有工具调用都应业务成功；严格按 `outputSchema` 读取 `data`。不得读取 `state/`、导入模块、访问环境对象、调用内部状态接口或硬编码最终业务答案。

程序最后一条语句必须且只能负责提交结构化结果：

```python
final_answer = {{"field": computed_value}}
```

`final_answer` 的字段必须与 `output_schema.properties` 完全相同。

# 结构化结果

`output_schema` 描述用户最终需要的业务结果，而不是工具轨迹。它必须是 Draft 2020-12 的封闭 object：

- `type` 为 `object`；
- `properties` 非空；
- `required` 恰好列出全部输出字段；
- `additionalProperties` 为 `false`；
- 每个字段给出明确类型，复杂对象和数组继续声明内部结构。

# 本轮修复要求

这是第 {round_index} 轮。先读取 `validation_feedback.json`：

- 根据 `remaining_tasks` 生成足够的新候选；
- 避开 `accepted_task_texts` 中已经覆盖的业务目标；
- 对 `previous_rejections` 定位根因。参数或返回结果层级错误必须回到公开 Schema 修正程序；数据不足、结论不唯一、调用无贡献或业务闭环不成立时，应重新设计任务，而不是只改最终答案。

# 提交

生成前按 `candidate.schema.json` 自检。最终只写一个 UTF-8 JSON 文件 `candidates.json`，不得修改其它文件。文件写完并确认能重新解析后结束。不要在最终回复中重复候选内容。"""


class ProgramTaskAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step2Result:
    output_path: Path
    task_count: int
    rejection_count: int


def _payload_errors(payload: Any, schema: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]


def _candidate_errors(
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
) -> list[str]:
    errors: list[str] = []
    task_lower = candidate.task_internal.lower()
    leaked_tools = [name for name in package.tool_names if name.lower() in task_lower]
    if leaked_tools:
        errors.append(f"任务正文泄露工具名：{', '.join(leaked_tools)}")
    for word in ("call_tool", "solution_code", "output_schema", "final_answer"):
        if word in task_lower:
            errors.append(f"任务正文泄露内部执行概念：{word}")
    schema = candidate.output_schema
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errors.append("output_schema 根节点必须声明 type=object")
    elif not isinstance(properties, dict) or not properties:
        errors.append("output_schema.properties 必须是非空 object")
    else:
        if set(schema.get("required", [])) != set(properties):
            errors.append("output_schema.required 必须且只能包含全部输出字段")
        if schema.get("additionalProperties") is not False:
            errors.append("output_schema.additionalProperties 必须是 false")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            errors.append(f"output_schema 本身不合法：{error}")
    return errors


def _preflight(
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
    policy: ProgramGenerationPolicy,
) -> tuple[dict[str, Any], list[str]]:
    errors = _candidate_errors(package, candidate)
    if errors:
        return {"success": False, "errors": errors}, errors
    execution = execute_reference_program(
        package,
        candidate.solution_code,
        candidate.output_schema,
        timeout_seconds=policy.execution_timeout_seconds,
    )
    if not execution.success:
        errors.append(f"真实执行失败：{execution.error_type}: {execution.error}")
    distinct_tools = {item["tool"] for item in execution.trace}
    if len(execution.trace) < policy.min_tool_calls:
        errors.append(
            f"实际工具调用 {len(execution.trace)} 次，少于要求的 {policy.min_tool_calls} 次"
        )
    if len(distinct_tools) < policy.min_distinct_tools:
        errors.append(
            f"实际只使用 {len(distinct_tools)} 个不同工具，少于要求的 "
            f"{policy.min_distinct_tools} 个"
        )
    changed = bool(execution.state_diff.get("changed_assets")) or any(
        execution.state_diff.get(field) for field in ("created", "modified", "deleted")
    )
    if policy.require_state_change and not changed:
        errors.append("任务要求修改环境，但 preflight 没有检测到真实状态变化")
    return {
        "success": not errors,
        "errors": errors,
        "candidate_answer": deepcopy(execution.answer),
        "tool_calls": deepcopy(execution.trace),
        "state_diff": deepcopy(execution.state_diff),
    }, errors


def run_step2(
    *,
    step1_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    agent: ProgramTaskAgent | None = None,
    candidates_path: Path | None = None,
) -> Step2Result:
    """生成候选并执行 OmniaBench Step 2 同等的真实 preflight。"""
    package = load_frozen_package(step1_path)
    if candidates_path is None and agent is None:
        raise ValueError("Step 2 没有 candidates_path 时必须提供 ProgramTaskAgent")
    policy.validate()
    output_dir = output_dir.resolve()
    rounds_root = output_dir / "step2_rounds"
    rounds_root.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "step2_task_solution.json"
    candidate_schema = read_json(
        Path(__file__).resolve().parents[1] / "schemas" / "candidate.schema.json"
    )
    public_environment = package.public_environment()
    accepted: list[dict[str, Any]] = []
    accepted_keys: set[str] = set()
    rejections: list[dict[str, Any]] = []
    rounds = 1 if candidates_path is not None else policy.task_generation_attempts

    for round_index in range(rounds):
        if len(accepted) >= policy.task_count:
            break
        round_dir = rounds_root / f"round_{round_index:03d}"
        if round_dir.exists():
            shutil.rmtree(round_dir)
        round_dir.mkdir()
        feedback = {
            "round": round_index,
            "remaining_tasks": policy.task_count - len(accepted),
            "accepted_task_texts": [item[TaskFields.TASK_INTERNAL] for item in accepted],
            "previous_rejections": rejections[-12:],
        }
        write_json(round_dir / "validation_feedback.json", feedback)
        prompt = build_program_generation_prompt(round_index=round_index, policy=policy)
        (round_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        if candidates_path is not None:
            payload = read_json(candidates_path.resolve())
        else:
            assert agent is not None
            with tempfile.TemporaryDirectory(prefix="agent-world-program-step2-") as temporary:
                authoring = Path(temporary)
                shutil.copytree(package.state_root, authoring / "state")
                initial_state = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                write_json(authoring / "environment.public.json", public_environment)
                write_json(authoring / "generation_request.json", {
                    "environment_id": package.environment["environment_id"],
                    **policy.to_dict(),
                    "round": round_index,
                })
                write_json(authoring / "candidate.schema.json", candidate_schema)
                write_json(authoring / "validation_feedback.json", feedback)
                try:
                    agent.run(prompt, working_directory=authoring)
                except Exception as error:
                    rejections.append({
                        "round": round_index, "candidate": None,
                        "reasons": [f"任务生成 Agent 调用失败：{type(error).__name__}: {error}"],
                    })
                    continue
                state_after = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                if state_after != initial_state:
                    rejections.append({
                        "round": round_index, "candidate": None,
                        "reasons": ["任务生成 Agent 修改了只读初始 state/"],
                    })
                    continue
                generated = authoring / "candidates.json"
                if not generated.is_file():
                    rejections.append({
                        "round": round_index, "candidate": None,
                        "reasons": ["生成 Agent 没有写出 candidates.json"],
                    })
                    continue
                payload = read_json(generated)

        write_json(round_dir / "candidates.json", payload)
        structural_errors = _payload_errors(payload, candidate_schema)
        if structural_errors:
            rejections.append({
                "round": round_index, "candidate": None, "reasons": structural_errors,
            })
            continue
        for candidate_index, value in enumerate(payload["candidates"]):
            candidate = ProgramTaskCandidate.from_dict(value)
            task_key = re.sub(r"\s+", " ", candidate.task_internal.strip().lower())
            if task_key in accepted_keys:
                continue
            preflight, errors = _preflight(package, candidate, policy)
            if errors:
                rejections.append({
                    "round": round_index, "candidate": candidate_index,
                    "task": candidate.task_internal, "reasons": errors,
                })
                continue
            task_index = len(accepted)
            tool_calls = preflight.get("tool_calls", [])
            state_diff = preflight.get("state_diff", {})
            state_changed = bool(state_diff.get("changed_assets")) or any(
                state_diff.get(field) for field in ("created", "modified", "deleted")
            )
            accepted.append({
                TaskFields.ENV_ID: str(package.environment["environment_id"]),
                TaskFields.ENV_CLASS_NAME: str(package.environment["name"]),
                TaskFields.ENVIRONMENT_PACKAGE: "baseline_environment",
                TaskFields.TASK_ID: f"{package.environment['environment_id']}_program_{task_index:03d}",
                TaskFields.TASK_INTERNAL: candidate.task_internal,
                TaskFields.OUTPUT_SCHEMA: deepcopy(candidate.output_schema),
                "difficulty_level": "standard",
                TaskFields.SOLUTION_CODE: candidate.solution_code,
                "step2_preflight": {
                    "success": True,
                    "candidate_attempt": round_index + 1,
                    "tool_call_count": len(tool_calls),
                    "state_changed": state_changed,
                    "rejected_candidates": deepcopy(rejections),
                },
                TaskFields.SOLUTION_DEBUG_SUCCESS: False,
                TaskFields.SOLUTION_DEBUG_HISTORY: [],
                TaskFields.SOLUTION_EXECUTION_TRAJECTORY: None,
            })
            accepted_keys.add(task_key)
            if len(accepted) >= policy.task_count:
                break

    write_json(output_path, accepted)
    write_json(output_dir / "step2_validation.json", {
        "status": "passed" if len(accepted) >= policy.task_count else "failed",
        "requested": policy.task_count,
        "accepted": len(accepted),
        "rejections": rejections,
    })
    if len(accepted) < policy.task_count:
        raise RuntimeError(
            f"Step 2 只生成了 {len(accepted)}/{policy.task_count} 个通过 preflight 的任务"
        )
    return Step2Result(output_path, len(accepted), len(rejections))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-count", type=int, default=2)
    parser.add_argument("--candidate-multiplier", type=int, default=2)
    parser.add_argument("--task-generation-attempts", type=int, default=3)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--model", default=DEFAULT_PROGRAM_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    arguments = parser.parse_args()

    from utils.search_agent.codex import CodexAgentClient

    policy = ProgramGenerationPolicy(
        task_count=arguments.task_count,
        candidate_multiplier=arguments.candidate_multiplier,
        task_generation_attempts=arguments.task_generation_attempts,
    )
    agent = None if arguments.candidates else CodexAgentClient(
        model=arguments.model,
        sandbox="workspace-write",
        enable_web_search=False,
        network_access=False,
        reasoning_effort=arguments.reasoning_effort,
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    result = run_step2(
        step1_path=arguments.step1_path,
        output_dir=arguments.output_dir,
        policy=policy,
        agent=agent,
        candidates_path=arguments.candidates,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
