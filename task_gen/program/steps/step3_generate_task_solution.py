"""Step 3：根据真实任务调研生成任务和 Solution，并固化可重放的标准结果。

生成 Agent 可以读取冻结状态副本来选择真实对象，但任务是否通过完全由 Python 的
真实 Runtime 决定。失败的 Solution 会收到真实错误并由 Agent 修复；修复成功后还要
经过多次干净重放和独立语义审查，才会写入 ``step3_task_solution.jsonl``。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
import tempfile
import time
import traceback
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.schema import Draft202012Validator

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.environment import CompleteEnvironmentPackage, load_frozen_package
from ..utils.io import read_json, write_json, write_jsonl
from ..utils.tool_runtime import (
    CompleteEnvironmentRuntime,
    compact_state_snapshot,
    snapshot_state,
    workspace_diff,
)


class TaskSolutionAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


class TaskSolutionReviewAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass
class ProgramTaskCandidate:
    archetype_id: str
    task_internal: str
    task_public: str
    output_schema: dict[str, Any]
    solution_code: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            archetype_id=str(value["archetype_id"]).strip(),
            task_internal=str(value["task_internal"]).strip(),
            task_public=str(value["task_public"]).strip(),
            output_schema=deepcopy(value["output_schema"]),
            solution_code=str(value["solution_code"]).strip(),
        )


FORBIDDEN_SOLUTION_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Global,
    ast.Nonlocal,
    ast.With,
    ast.AsyncWith,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Raise,
    ast.While,
    ast.Delete,
)
FORBIDDEN_SOLUTION_CALLS = {
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "type",
    "vars",
    "__import__",
}
SOLUTION_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


def validate_solution_code(source: str) -> list[str]:
    """Validate the hidden Solution language accepted by this step."""
    if not isinstance(source, str) or not source.strip():
        return ["solution_code 必须是非空 Python 源代码"]
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f"Python 语法错误：{error}"]
    errors: list[str] = []
    if not tree.body or not isinstance(tree.body[-1], ast.Assign):
        errors.append("最后一条语句必须给 final_answer 赋值")
    else:
        targets = tree.body[-1].targets
        if not any(
            isinstance(target, ast.Name) and target.id == "final_answer"
            for target in targets
        ):
            errors.append("最后一条语句必须给 final_answer 赋值")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_SOLUTION_NODES):
            errors.append(
                f"第 {getattr(node, 'lineno', '?')} 行禁止使用 {type(node).__name__}"
            )
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"第 {node.lineno} 行禁止访问双下划线名称 {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(f"第 {node.lineno} 行禁止访问内部属性 {node.attr}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_SOLUTION_CALLS
        ):
            errors.append(f"第 {node.lineno} 行禁止调用 {node.func.id}")
    return list(dict.fromkeys(errors))


@dataclass
class ProgramExecutionResult:
    success: bool
    answer: dict[str, Any] | None
    trace: list[dict[str, Any]]
    initial_state: dict[str, Any]
    final_state: dict[str, Any]
    state_diff: dict[str, Any]
    error_type: str | None = None
    error: str | None = None
    traceback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "answer": deepcopy(self.answer),
            "trace": deepcopy(self.trace),
            "initial_state": deepcopy(self.initial_state),
            "final_state": deepcopy(self.final_state),
            "state_diff": deepcopy(self.state_diff),
            "error_type": self.error_type,
            "error": self.error,
            "traceback": self.traceback,
        }


class _ExecutionBudget:
    def __init__(self, timeout_seconds: float, max_lines: int):
        self.deadline = time.monotonic() + timeout_seconds
        self.max_lines = max_lines
        self.lines = 0

    def trace(self, _frame: Any, event: str, _arg: Any) -> Any:
        if event == "line":
            self.lines += 1
            if self.lines > self.max_lines:
                raise TimeoutError("参考程序超过最大执行行数")
            if time.monotonic() > self.deadline:
                raise TimeoutError("参考程序执行超时")
        return self.trace


def _answer_schema_errors(schema: dict[str, Any], answer: Any) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(answer)
    ]


def execute_solution_code(
    package: CompleteEnvironmentPackage,
    source: str,
    output_schema: dict[str, Any],
    *,
    timeout_seconds: float = 15.0,
    max_executed_lines: int = 1_000_000,
) -> ProgramExecutionResult:
    """Execute one Solution against a fresh environment and capture Ground Truth."""
    source_errors = validate_solution_code(source)
    if source_errors:
        empty = {"files": {}}
        return ProgramExecutionResult(
            False,
            None,
            [],
            empty,
            empty,
            workspace_diff(empty, empty),
            "source_validation",
            " | ".join(source_errors),
        )

    with CompleteEnvironmentRuntime(package) as runtime:
        initial_state = runtime.snapshot()

        def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return runtime.call(name, arguments)

        namespace: dict[str, Any] = {
            "__builtins__": SOLUTION_BUILTINS,
            "call_tool": call_tool,
        }
        budget = _ExecutionBudget(timeout_seconds, max_executed_lines)
        previous_trace = sys.gettrace()
        try:
            compiled = compile(source, "<solution-code>", "exec")
            sys.settrace(budget.trace)
            exec(compiled, namespace, namespace)
            answer = namespace.get("final_answer")
            try:
                answer = json.loads(json.dumps(answer, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError) as error:
                raise ValueError(f"final_answer 不是严格 JSON-native 数据：{error}") from error
            if not isinstance(answer, dict):
                raise ValueError("final_answer 必须是 object")
            schema_errors = _answer_schema_errors(output_schema, answer)
            if schema_errors:
                raise ValueError(
                    "final_answer 不符合 output_schema：" + " | ".join(schema_errors)
                )
            failed_calls = [
                record
                for record in runtime.trace
                if record.result.get("success") is not True
            ]
            if failed_calls:
                names = ", ".join(record.tool for record in failed_calls)
                raise ValueError(f"参考程序包含业务失败的工具调用：{names}")
            final_state = runtime.snapshot()
            return ProgramExecutionResult(
                True,
                answer,
                [record.to_dict() for record in runtime.trace],
                compact_state_snapshot(initial_state),
                compact_state_snapshot(final_state),
                workspace_diff(initial_state, final_state),
            )
        except Exception as error:
            final_state = runtime.snapshot()
            return ProgramExecutionResult(
                False,
                None,
                [record.to_dict() for record in runtime.trace],
                compact_state_snapshot(initial_state),
                compact_state_snapshot(final_state),
                workspace_diff(initial_state, final_state),
                type(error).__name__,
                str(error),
                traceback.format_exc(limit=12),
            )
        finally:
            sys.settrace(previous_trace)


@dataclass(frozen=True)
class Step3Result:
    output_path: Path
    validation_path: Path
    requested: int
    accepted: int
    rejected: int


def build_task_solution_prompt(round_index: int, policy: ProgramGenerationPolicy) -> str:
    mutation_rule = (
        "每个任务都必须产生由业务目标要求的状态变化。"
        if policy.require_state_change
        else "任务可以只读，也可以修改状态；由真实任务原型决定，不要强行写入。"
    )
    return f"""# 任务：生成真实、可执行的 Program-form 任务和参考 Solution

这是生成轮次 {round_index}。你是一名 benchmark 任务作者。完整读取：

- `environment.public.json`：数据声明与公开工具 inputSchema/outputSchema；
- `task_research.json`：已通过环境能力映射的真实任务原型；
- `state/`：隔离的初始业务状态副本，只能读取，不能修改；
- `generation_request.json`：数量和质量要求；
- `validation_feedback.json`：已接受任务及之前失败的真实原因；
- `candidate.schema.json`：唯一输出格式。

只选择 `environment_support.generatable=true` 的任务原型。根据真实初始状态生成具体但
不泄露答案的任务实例。任务应像现实用户提出的完整工作请求，明确业务目标、范围、
硬约束、选择规则和期望结果；不得把多个无关目标拼在一起凑复杂度。

`task_internal` 用于内部审计，可以详细说明业务条件，但不能写标准答案。
`task_public` 是最终给求解 Agent 的正文，必须保留同一业务目标和所有必要约束，同时
不得出现工具名、调用顺序、Python、call_tool、solution_code、final_answer、数据库
字段路径、隐藏答案或“先调用 A 再调用 B”式提示。output_schema 独立存在，不要抄入正文。

`output_schema` 必须是 Draft 2020-12 的闭合 object：`properties` 非空，`required`
恰好包含全部字段，`additionalProperties=false`。字段描述用户真正需要的业务结果，
不是工具轨迹。

`solution_code` 是隐藏参考程序。唯一环境接口为：

```python
result = call_tool("tool_name", {{"argument": value}})
```

工具统一返回 `{{"success": true, "data": ...}}` 或失败 envelope。必须严格按照公开
outputSchema 读取 `data` 层级。程序要从任务条件和前序工具结果动态取得 ID、证据和
动作参数，不能读取 state/、导入模块、访问环境对象或硬编码最终答案。最后一条语句
必须给 `final_answer` 赋值，字段与 output_schema 完全一致。

每个 Solution 至少真实调用 {policy.min_tool_calls} 次工具，覆盖至少
{policy.min_distinct_tools} 个不同工具；调用必须服务于同一个业务闭环，不能重复查询凑数。
{mutation_rule}

不同候选必须具有不同的业务目标或决策结构。根据 validation_feedback 中的错误重新
设计或修复，不要仅修改 final_answer 来掩盖失败。

你写出的成功声明不作为验收依据；Python 会在真实 Runtime 中执行 Solution、校验工具
调用和最终状态，并将实际错误反馈给下一轮。

最终只写 `candidates.json`，写完后按 candidate.schema.json 自检，不修改其他文件。
"""


def build_solution_repair_prompt(round_index: int) -> str:
    return f"""# Step 3 Solution 修复

这是修复轮次 {round_index}。读取 `repair_request.json` 和 `environment.public.json`。
根据真实执行错误修复完整 Solution，只能修复语法、工具参数、返回层级、控制流或
final_answer 构造。不得改变任务目标、读取状态目录、增加环境没有的能力或硬编码答案。

最终只写：

```json
{{"solution_code": "修复后的完整 Python 代码", "modification": "修改说明"}}
```
"""


def build_task_solution_review_prompt() -> str:
    return """# Step 3 独立语义审查

读取 `review_request.json`，判断这条任务能否作为可靠 benchmark：

1. task_public 与所选现实任务原型具有相同角色、触发场景和业务结果；
2. task_public 保留了完成任务所需的范围、硬约束和返回要求；
3. task_public 没有泄露工具名、内部字段、调用顺序、参考答案或实现提示；
4. Solution 轨迹和最终状态完成了任务要求，没有额外业务动作；
5. output_schema 足以表达用户要求返回的全部结果；
6. 任务不依赖当前环境不存在的数据、工具、联网或人工操作。

不得因为参考轨迹不是唯一方案而拒绝。任一检查不成立都必须 accepted=false。
最终只写 `review.json`：

```json
{
  "accepted": true,
  "checks": {
    "research_grounded": true,
    "requirements_preserved": true,
    "no_implementation_leak": true,
    "execution_aligned": true,
    "output_schema_complete": true,
    "environment_supported": true
  },
  "issues": []
}
```
"""


def _schema_errors(payload: Any, schema: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]


def _candidate_errors(
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
    generatable_archetypes: set[str],
) -> list[str]:
    errors: list[str] = []
    if candidate.archetype_id not in generatable_archetypes:
        errors.append(f"archetype_id 不属于可生成原型：{candidate.archetype_id}")
    public_lower = candidate.task_public.casefold()
    leaked_tools = [name for name in package.tool_names if name.casefold() in public_lower]
    if leaked_tools:
        errors.append(f"task_public 泄露工具名：{', '.join(leaked_tools)}")
    for word in ("call_tool", "solution_code", "final_answer", "output_schema"):
        if word in public_lower:
            errors.append(f"task_public 泄露内部概念：{word}")
    schema = candidate.output_schema
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errors.append("output_schema 根节点必须为 type=object")
    elif not isinstance(properties, dict) or not properties:
        errors.append("output_schema.properties 必须是非空 object")
    else:
        if set(schema.get("required", [])) != set(properties):
            errors.append("output_schema.required 必须恰好包含全部 properties")
        if schema.get("additionalProperties") is not False:
            errors.append("output_schema.additionalProperties 必须为 false")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            errors.append(f"output_schema 非法：{error}")
    return errors


def _state_changed(execution: ProgramExecutionResult) -> bool:
    return bool(execution.state_diff.get("changed_assets")) or any(
        execution.state_diff.get(field) for field in ("created", "modified", "deleted")
    )


def _repair_solution(
    *,
    agent: TaskSolutionAgent,
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
    code: str,
    execution: ProgramExecutionResult,
    round_index: int,
    repair_root: Path,
) -> tuple[str, str]:
    audit_dir = repair_root / candidate.archetype_id / f"round_{round_index:02d}"
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    with tempfile.TemporaryDirectory(prefix="agent-world-program-repair-") as temporary:
        repair_dir = Path(temporary)
        write_json(repair_dir / "environment.public.json", package.public_environment())
        write_json(repair_dir / "repair_request.json", {
            "task_internal": candidate.task_internal,
            "task_public": candidate.task_public,
            "output_schema": candidate.output_schema,
            "solution_code": code,
            "error_type": execution.error_type,
            "error": execution.error,
            "tool_trace": execution.trace,
        })
        prompt = build_solution_repair_prompt(round_index)
        (repair_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            agent.run(prompt, working_directory=repair_dir)
            payload = read_json(repair_dir / "repair.json")
        finally:
            shutil.copytree(repair_dir, audit_dir)
    if not isinstance(payload, dict) or not str(payload.get("solution_code") or "").strip():
        raise ValueError("repair.json 缺少非空 solution_code")
    return str(payload["solution_code"]).strip(), str(payload.get("modification") or "")


def _execute_with_repairs(
    *,
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
    policy: ProgramGenerationPolicy,
    agent: TaskSolutionAgent | None,
    repair_root: Path,
) -> tuple[str, ProgramExecutionResult, list[dict[str, Any]]]:
    code = candidate.solution_code
    execution = execute_solution_code(
        package, code, candidate.output_schema,
        timeout_seconds=policy.execution_timeout_seconds,
    )
    history: list[dict[str, Any]] = []
    for round_index in range(1, policy.max_repair_rounds + 1):
        if execution.success or agent is None:
            break
        prior_error = f"{execution.error_type}: {execution.error}"
        try:
            code, modification = _repair_solution(
                agent=agent,
                package=package,
                candidate=candidate,
                code=code,
                execution=execution,
                round_index=round_index,
                repair_root=repair_root,
            )
            history.append({
                "round": round_index,
                "error": prior_error,
                "modification": modification,
            })
        except Exception as error:
            history.append({
                "round": round_index,
                "error": prior_error,
                "repair_error": f"{type(error).__name__}: {error}",
            })
            break
        execution = execute_solution_code(
            package, code, candidate.output_schema,
            timeout_seconds=policy.execution_timeout_seconds,
        )
    return code, execution, history


def _replay_solution(
    *,
    package: CompleteEnvironmentPackage,
    code: str,
    schema: dict[str, Any],
    policy: ProgramGenerationPolicy,
    first: ProgramExecutionResult,
) -> tuple[list[ProgramExecutionResult], list[str]]:
    runs = [first]
    while len(runs) < policy.clean_replays:
        runs.append(execute_solution_code(
            package, code, schema,
            timeout_seconds=policy.execution_timeout_seconds,
        ))
    errors: list[str] = []
    for index, run in enumerate(runs):
        if not run.success:
            errors.append(f"clean replay {index} 失败：{run.error_type}: {run.error}")
    if not errors:
        reference = runs[0]
        for index, run in enumerate(runs[1:], start=1):
            if run.answer != reference.answer:
                errors.append(f"clean replay {index} 的 final_answer 不稳定")
            if run.final_state != reference.final_state:
                errors.append(f"clean replay {index} 的 final_state 不稳定")
            if run.state_diff != reference.state_diff:
                errors.append(f"clean replay {index} 的 state_diff 不稳定")
    return runs, errors


def _review_task_solution(
    *,
    agent: TaskSolutionReviewAgent | None,
    package: CompleteEnvironmentPackage,
    archetype: dict[str, Any],
    candidate: ProgramTaskCandidate,
    execution: ProgramExecutionResult,
    review_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    if agent is None:
        return {}, ["没有提供独立 TaskSolutionReviewAgent"]
    audit_dir = review_dir
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    with tempfile.TemporaryDirectory(prefix="agent-world-program-review-") as temporary:
        review_dir = Path(temporary)
        write_json(review_dir / "review_request.json", {
            "environment": package.public_environment(),
            "task_archetype": archetype,
            "task_internal": candidate.task_internal,
            "task_public": candidate.task_public,
            "output_schema": candidate.output_schema,
            "solution_trace": execution.trace,
            "initial_state": execution.initial_state,
            "final_state": execution.final_state,
            "state_diff": execution.state_diff,
            "candidate_answer": execution.answer,
        })
        prompt = build_task_solution_review_prompt()
        (review_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            agent.run(prompt, working_directory=review_dir)
            review = read_json(review_dir / "review.json")
        except Exception as error:
            shutil.copytree(review_dir, audit_dir)
            return {}, [f"语义审查失败：{type(error).__name__}: {error}"]
        shutil.copytree(review_dir, audit_dir)
    check_names = {
        "research_grounded",
        "requirements_preserved",
        "no_implementation_leak",
        "execution_aligned",
        "output_schema_complete",
        "environment_supported",
    }
    checks = review.get("checks") if isinstance(review, dict) else None
    if (
        not isinstance(review, dict)
        or type(review.get("accepted")) is not bool
        or not isinstance(checks, dict)
        or set(checks) != check_names
        or any(type(value) is not bool for value in checks.values())
        or not isinstance(review.get("issues"), list)
    ):
        return {}, ["review.json 不符合固定审查结构"]
    if review["accepted"] is not True or not all(checks.values()):
        issues = [str(item) for item in review.get("issues", [])]
        return review, issues or ["独立审查未通过"]
    return review, []


def run_step3(
    *,
    step1_path: Path,
    step2_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    generation_agent: TaskSolutionAgent | None,
    review_agent: TaskSolutionReviewAgent | None,
    candidates_path: Path | None = None,
) -> Step3Result:
    """生成任务，执行/修复 Solution，并固化稳定 Ground Truth。"""
    policy.validate()
    package = load_frozen_package(step1_path)
    research = read_json(step2_path.resolve())
    archetypes = {
        str(item["archetype_id"]): item
        for item in research.get("task_archetypes", [])
        if isinstance(item, dict)
        and item.get("environment_support", {}).get("generatable") is True
    }
    if not archetypes:
        raise RuntimeError("Step 2 没有可生成的任务原型")
    schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "candidate.schema.json")
    output_dir = output_dir.resolve()
    output_path = output_dir / "step3_task_solution.jsonl"
    validation_path = output_dir / "step3_validation.json"
    accepted: list[dict[str, Any]] = []
    accepted_texts: set[str] = set()
    rejections: list[dict[str, Any]] = []
    rounds = 1 if candidates_path is not None else policy.task_generation_attempts

    for round_index in range(1, rounds + 1):
        if len(accepted) >= policy.task_count:
            break
        if candidates_path is not None:
            payload = read_json(candidates_path.resolve())
        else:
            if generation_agent is None:
                raise ValueError("没有提供 TaskSolutionAgent")
            with tempfile.TemporaryDirectory(prefix="agent-world-program-step3-") as temporary:
                authoring = Path(temporary)
                shutil.copytree(package.state_root, authoring / "state")
                before = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                write_json(authoring / "environment.public.json", package.public_environment())
                write_json(authoring / "task_research.json", research)
                write_json(authoring / "candidate.schema.json", schema)
                write_json(authoring / "generation_request.json", {
                    **policy.to_dict(),
                    "remaining_tasks": policy.task_count - len(accepted),
                    "candidate_count": (
                        policy.task_count - len(accepted)
                    ) * policy.candidate_multiplier,
                })
                write_json(authoring / "validation_feedback.json", {
                    "accepted_task_public": [item[TaskFields.TASK_PUBLIC] for item in accepted],
                    "previous_rejections": rejections[-12:],
                })
                prompt = build_task_solution_prompt(round_index, policy)
                (authoring / "prompt.txt").write_text(prompt, encoding="utf-8")
                try:
                    generation_agent.run(prompt, working_directory=authoring)
                    payload = read_json(authoring / "candidates.json")
                except Exception as error:
                    rejections.append({
                        "round": round_index,
                        "candidate": None,
                        "reasons": [f"生成 Agent 失败：{type(error).__name__}: {error}"],
                    })
                    continue
                after = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                if after != before:
                    rejections.append({
                        "round": round_index,
                        "candidate": None,
                        "reasons": ["生成 Agent 修改了只读 state/"],
                    })
                    continue

        structural_errors = _schema_errors(payload, schema)
        if structural_errors:
            rejections.append({
                "round": round_index,
                "candidate": None,
                "reasons": structural_errors,
            })
            continue
        for candidate_index, raw in enumerate(payload["candidates"]):
            candidate = ProgramTaskCandidate.from_dict(raw)
            normalized_text = re.sub(r"\s+", " ", candidate.task_public.casefold()).strip()
            if normalized_text in accepted_texts:
                continue
            errors = _candidate_errors(package, candidate, set(archetypes))
            if errors:
                rejections.append({
                    "round": round_index,
                    "candidate": candidate_index,
                    "reasons": errors,
                })
                continue
            code, execution, debug_history = _execute_with_repairs(
                package=package,
                candidate=candidate,
                policy=policy,
                agent=generation_agent,
                repair_root=output_dir / "step3_repairs",
            )
            if not execution.success:
                errors.append(f"Solution 执行失败：{execution.error_type}: {execution.error}")
            distinct_tools = {item.get("tool") for item in execution.trace}
            if len(execution.trace) < policy.min_tool_calls:
                errors.append(
                    f"实际工具调用 {len(execution.trace)} 次，少于 {policy.min_tool_calls}"
                )
            if len(distinct_tools) < policy.min_distinct_tools:
                errors.append(
                    f"实际不同工具 {len(distinct_tools)} 个，少于 {policy.min_distinct_tools}"
                )
            if policy.require_state_change and not _state_changed(execution):
                errors.append("任务要求状态变化，但 Solution 没有产生状态变化")
            replays: list[ProgramExecutionResult] = []
            if not errors:
                replays, replay_errors = _replay_solution(
                    package=package,
                    code=code,
                    schema=candidate.output_schema,
                    policy=policy,
                    first=execution,
                )
                errors.extend(replay_errors)
            review: dict[str, Any] = {}
            if not errors:
                review, review_errors = _review_task_solution(
                    agent=review_agent,
                    package=package,
                    archetype=archetypes[candidate.archetype_id],
                    candidate=candidate,
                    execution=execution,
                    review_dir=(
                        output_dir / "step3_reviews" /
                        f"round_{round_index:02d}_candidate_{candidate_index:02d}"
                    ),
                )
                errors.extend(review_errors)
            if errors:
                rejections.append({
                    "round": round_index,
                    "candidate": candidate_index,
                    "archetype_id": candidate.archetype_id,
                    "task_public": candidate.task_public,
                    "reasons": errors,
                    "debug_history": debug_history,
                })
                continue
            accepted.append({
                TaskFields.ENV_ID: str(package.environment["environment_id"]),
                TaskFields.ENV_CLASS_NAME: str(package.environment["name"]),
                TaskFields.ENVIRONMENT_PACKAGE: "baseline_environment",
                TaskFields.TASK_ID: (
                    f"{package.environment['environment_id']}_program_{len(accepted):03d}"
                ),
                TaskFields.ARCHETYPE_ID: candidate.archetype_id,
                "task_archetype": deepcopy(archetypes[candidate.archetype_id]),
                TaskFields.TASK_INTERNAL: candidate.task_internal,
                TaskFields.TASK_PUBLIC: candidate.task_public,
                TaskFields.OUTPUT_SCHEMA: deepcopy(candidate.output_schema),
                TaskFields.SOLUTION_CODE_ORIGINAL: candidate.solution_code,
                TaskFields.SOLUTION_CODE: code,
                TaskFields.SOLUTION_CODE_FIXED: code,
                TaskFields.SOLUTION_TRACE: deepcopy(execution.trace),
                TaskFields.GROUND_TRUTH: {
                    "candidate_answer": deepcopy(execution.answer),
                    "init_state": deepcopy(execution.initial_state),
                    "final_state": deepcopy(execution.final_state),
                    "state_diff": deepcopy(execution.state_diff),
                },
                "solution_validation": {
                    "success": True,
                    "debug_history": debug_history,
                    "clean_replay_count": len(replays),
                    "tool_call_count": len(execution.trace),
                    "distinct_tools": sorted(str(item) for item in distinct_tools),
                    "state_changed": _state_changed(execution),
                    "semantic_review": review,
                },
            })
            accepted_texts.add(normalized_text)
            if len(accepted) >= policy.task_count:
                break

    write_jsonl(output_path, accepted)
    write_json(validation_path, {
        "status": "passed" if len(accepted) == policy.task_count else "failed",
        "requested": policy.task_count,
        "accepted": len(accepted),
        "rejected": len(rejections),
        "rejections": rejections,
    })
    if len(accepted) != policy.task_count:
        raise RuntimeError(f"Step 3 只接受了 {len(accepted)}/{policy.task_count} 条任务")
    return Step3Result(
        output_path, validation_path, policy.task_count, len(accepted), len(rejections)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--step2-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--task-count", type=int, default=1)
    parser.add_argument("--generation-attempts", type=int, default=3)
    parser.add_argument("--max-repair-rounds", type=int, default=10)
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient

    agent = CodexAgentClient(
        model=arguments.model,
        sandbox="workspace-write",
        network_access=False,
        reasoning_effort="high",
    )
    result = run_step3(
        step1_path=arguments.step1_path,
        step2_path=arguments.step2_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            task_count=arguments.task_count,
            task_generation_attempts=arguments.generation_attempts,
            max_repair_rounds=arguments.max_repair_rounds,
        ),
        generation_agent=None if arguments.candidates else agent,
        review_agent=agent,
        candidates_path=arguments.candidates,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
