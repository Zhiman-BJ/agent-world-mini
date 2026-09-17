"""Step 2：根据真实任务调研生成任务和 Solution，并固化可重放的标准结果。

生成 Agent 可以读取冻结状态副本来选择真实对象，但任务是否通过完全由 Python 的
真实 Runtime 决定。失败的 Solution 会收到真实错误并由 Agent 修复；修复成功后还要
经过多次干净重放和独立语义审查，才会写入 ``step2_task_solution.jsonl``，并发布
外部 ``task_eval`` 可以直接消费的 ``tasks.json`` 与参考状态目录。
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

from .utils.schema import Draft202012Validator

from .utils.contracts import ProgramGenerationPolicy, TaskFields
from .utils.environment import CompleteEnvironmentPackage, load_frozen_package
from .utils.io import read_json, read_records, write_json, write_jsonl
from .utils.schema_docs import STEP2_SCHEMA_DOCS, copy_schema_docs
from .utils.tool_runtime import (
    CompleteEnvironmentRuntime,
    compact_state_snapshot,
    snapshot_state,
    workspace_diff,
)


class TaskSolutionAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


class TaskSolutionReviewAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


def _run_agent_until_json(
    agent: TaskSolutionAgent | TaskSolutionReviewAgent,
    prompt: str,
    *,
    working_directory: Path,
    filename: str,
) -> str:
    """Stop Codex as soon as its JSON handoff is complete.

    Test doubles and other Agent implementations may expose only ``run``; those
    retain the original behavior.
    """
    method = getattr(agent, "run_until_json_file", None)
    if callable(method):
        return method(
            prompt,
            working_directory=working_directory,
            required_path=working_directory / filename,
        )
    return agent.run(prompt, working_directory=working_directory)


@dataclass
class ProgramTaskCandidate:
    archetype_id: str
    task_internal: str
    task_public: str
    output_schema: dict[str, Any]
    task_resources: dict[str, Any]
    solution_code: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            archetype_id=str(value["archetype_id"]).strip(),
            task_internal=str(value["task_internal"]).strip(),
            task_public=str(value["task_public"]).strip(),
            output_schema=deepcopy(value["output_schema"]),
            task_resources=deepcopy(value["task_resources"]),
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


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for item in target.elts
            for name in _assigned_names(item)
        }
    return set()


def _uses_tainted_value(node: ast.AST | None, tainted: set[str]) -> bool:
    return node is not None and any(
        isinstance(item, ast.Name) and item.id in tainted
        for item in ast.walk(node)
    )


def _contains_tool_call(node: ast.AST | None) -> bool:
    return node is not None and any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "call_tool"
        for item in ast.walk(node)
    )


def _solution_dataflow_errors(tree: ast.Module) -> list[str]:
    """Require a real data dependency from observations to actions and final answer."""
    tainted: set[str] = set()
    dependent_tool_calls = 0
    final_answer_depends_on_tool = False

    def count_dependent_calls(node: ast.AST | None) -> None:
        nonlocal dependent_tool_calls
        if node is None:
            return
        for item in ast.walk(node):
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == "call_tool"
                and any(
                    _uses_tainted_value(argument, tainted)
                    for argument in [*item.args, *(keyword.value for keyword in item.keywords)]
                )
            ):
                dependent_tool_calls += 1

    def process(statements: list[ast.stmt]) -> None:
        nonlocal final_answer_depends_on_tool
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                count_dependent_calls(value)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                names = {
                    name
                    for target in targets
                    for name in _assigned_names(target)
                }
                derived = _contains_tool_call(value) or _uses_tainted_value(value, tainted)
                if "final_answer" in names:
                    final_answer_depends_on_tool = derived
                if derived:
                    tainted.update(names)
                else:
                    tainted.difference_update(names)
                continue
            if isinstance(statement, ast.For):
                count_dependent_calls(statement.iter)
                if _contains_tool_call(statement.iter) or _uses_tainted_value(
                    statement.iter, tainted
                ):
                    tainted.update(_assigned_names(statement.target))
                process(statement.body)
                process(statement.orelse)
                continue
            if isinstance(statement, ast.If):
                count_dependent_calls(statement.test)
                process(statement.body)
                process(statement.orelse)
                continue
            if isinstance(statement, ast.Try):
                process(statement.body)
                for handler in statement.handlers:
                    process(handler.body)
                process(statement.orelse)
                process(statement.finalbody)
                continue
            count_dependent_calls(statement)
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
                and any(
                    _contains_tool_call(argument)
                    or _uses_tainted_value(argument, tainted)
                    for argument in statement.value.args
                )
            ):
                tainted.add(statement.value.func.value.id)

    process(tree.body)
    errors: list[str] = []
    if dependent_tool_calls == 0:
        errors.append("至少一个后续工具调用的参数必须依赖前序工具结果")
    if not final_answer_depends_on_tool:
        errors.append("final_answer 必须由真实工具结果推导，不能写死")
    return errors


@dataclass(frozen=True)
class SolutionComplexityProfile:
    """Structural evidence that difficulty comes from runtime data dependencies."""

    tool_calls: int
    distinct_tools: int
    dependent_tool_calls: int
    max_dependency_depth: int
    business_decisions: int
    loops_over_tool_results: int


def _solution_complexity_profile(source: str) -> SolutionComplexityProfile:
    """Approximate the observation -> decision -> action depth of a Solution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return SolutionComplexityProfile(0, 0, 0, 0, 0, 0)

    depths: dict[str, int] = {}
    tool_calls = 0
    tool_names: set[str] = set()
    dependent_tool_calls = 0
    max_dependency_depth = 0
    business_decisions = 0
    loops_over_tool_results = 0

    def expression_depth(node: ast.AST | None) -> int:
        nonlocal tool_calls, dependent_tool_calls, max_dependency_depth
        if node is None:
            return 0
        if isinstance(node, ast.Name):
            return depths.get(node.id, 0)
        if isinstance(node, ast.Call):
            argument_depth = max(
                [
                    expression_depth(argument)
                    for argument in [
                        *node.args,
                        *(keyword.value for keyword in node.keywords),
                    ]
                ]
                or [0]
            )
            if isinstance(node.func, ast.Name) and node.func.id == "call_tool":
                tool_calls += 1
                if node.args and isinstance(node.args[0], ast.Constant):
                    tool_names.add(str(node.args[0].value))
                if argument_depth > 0:
                    dependent_tool_calls += 1
                result_depth = max(1, argument_depth + 1)
                max_dependency_depth = max(max_dependency_depth, result_depth)
                return result_depth
            return max(
                argument_depth,
                expression_depth(node.func),
            )
        return max(
            [expression_depth(child) for child in ast.iter_child_nodes(node)] or [0]
        )

    def is_business_decision(node: ast.AST, node_depth: int) -> bool:
        if node_depth == 0:
            return False
        return not any(
            isinstance(item, ast.Constant) and item.value == "success"
            for item in ast.walk(node)
        )

    def process(statements: list[ast.stmt]) -> None:
        nonlocal business_decisions, loops_over_tool_results
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                value_depth = expression_depth(value)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    for name in _assigned_names(target):
                        depths[name] = value_depth
                if (
                    value_depth > 0
                    and isinstance(value, (ast.BoolOp, ast.Compare, ast.IfExp))
                    and is_business_decision(value, value_depth)
                ):
                    business_decisions += 1
                continue
            if isinstance(statement, ast.For):
                iterator_depth = expression_depth(statement.iter)
                if iterator_depth > 0:
                    loops_over_tool_results += 1
                for name in _assigned_names(statement.target):
                    depths[name] = iterator_depth
                process(statement.body)
                process(statement.orelse)
                continue
            if isinstance(statement, ast.If):
                test_depth = expression_depth(statement.test)
                if is_business_decision(statement.test, test_depth):
                    business_decisions += 1
                process(statement.body)
                process(statement.orelse)
                continue
            if isinstance(statement, ast.Try):
                process(statement.body)
                for handler in statement.handlers:
                    process(handler.body)
                process(statement.orelse)
                process(statement.finalbody)
                continue
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
            ):
                container = statement.value.func.value.id
                value_depth = expression_depth(statement.value)
                depths[container] = max(depths.get(container, 0), value_depth)
                continue
            expression_depth(statement)

    process(tree.body)
    return SolutionComplexityProfile(
        tool_calls=tool_calls,
        distinct_tools=len(tool_names),
        dependent_tool_calls=dependent_tool_calls,
        max_dependency_depth=max_dependency_depth,
        business_decisions=business_decisions,
        loops_over_tool_results=loops_over_tool_results,
    )


def _solution_complexity_errors(source: str) -> list[str]:
    """Reject linear workflows even when every individual call is valid."""
    profile = _solution_complexity_profile(source)
    errors: list[str] = []
    if profile.max_dependency_depth < 3 or profile.dependent_tool_calls < 2:
        errors.append(
            "任务依赖链过浅：至少需要两段连续依赖，使一次工具结果影响后续调用，"
            "后续结果再影响更后面的调用或业务决定"
        )
    if profile.business_decisions == 0 and profile.loops_over_tool_results == 0:
        errors.append(
            "任务缺少基于真实工具结果的筛选、比较、分类、条件判断或逐项处理"
        )
    return errors


def _candidate_complexity_sort_key(raw: dict[str, Any]) -> tuple[int, ...]:
    profile = _solution_complexity_profile(str(raw.get("solution_code") or ""))
    return (
        profile.max_dependency_depth,
        profile.business_decisions + profile.loops_over_tool_results,
        profile.dependent_tool_calls,
        profile.distinct_tools,
        profile.tool_calls,
    )


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
    errors.extend(_solution_dataflow_errors(tree))
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
        # Tool handlers and state snapshots have their own timeout controls.
        # Counting their implementation lines makes the Solution budget depend
        # on environment size instead of the submitted reference program.
        if event == "line" and _frame.f_code.co_filename == "<solution-code>":
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
    final_state_output: Path | None = None,
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
            if final_state_output is not None:
                destination = final_state_output.resolve()
                if destination.exists():
                    shutil.rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(runtime.state_root, destination)
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
class Step2Result:
    output_path: Path
    validation_path: Path
    tasks_path: Path
    bundle_path: Path
    requested: int
    accepted: int
    rejected: int


def build_task_solution_prompt(round_index: int, policy: ProgramGenerationPolicy) -> str:
    mutation_rule = (
        "每个任务都必须产生由业务目标要求的状态变化。"
        if policy.require_state_change
        else "任务可以只读，也可以修改状态；由所选现实工作类型决定，不要强行写入。"
    )
    return f"""# 任务：根据一个真实离线环境编写高难度任务及可执行参考程序

## 背景

系统正在为一个已经固定初始状态的离线环境制作任务数据。未来会有一个执行者只看到任务
正文、公开环境说明和公开工具，然后从相同初始状态独立完成任务。为了证明任务确实可做，
你需要同时提供一段隐藏的参考程序；系统会真实执行这段程序并保存正确答案和执行后的状态。

你没有本项目此前的对话上下文。不要假设读者知道任何内部步骤、缩写或目录约定。工作目录
中的文件是当前任务的完整输入，下面的字段定义和约束是完整要求。

这是第 {round_index} 轮生成。`validation_feedback.json` 中如果有此前失败原因，本轮必须针对
这些真实错误重新设计或修正，不能只换一种说法后重复提交。

## 输入文件

1. `environment.public.json`

   这是执行者可见的环境说明。顶层 `environment` 描述环境和可访问内容，`tools` 描述可
   调用工具。环境内容分为：

   - `record_sets`：数据库中的业务记录集合；
   - `relationships`：记录集合之间的关联方式；
   - `filesystem_scopes`：由 `scope_id` 命名的文件区域；
   - `tools`：公开工具。每个工具的 `inputSchema` 是参数格式，`outputSchema` 是返回格式，
     `usageConditions` 说明调用前提、目标对象和副作用。

2. `task_research.json`

   这是经过外部来源核实的现实工作调研。`task_archetypes` 的每一项表示一种现实工作类型，
   其中 `environment_support.generatable=true` 表示当前环境能够支持其核心工作。选择一个可
   生成原型作为任务的主要现实依据，也可以吸收其他调研项中与同一业务闭环直接相关的异常
   处理或复核要求。`environment_support.tools` 是调研时确认的主要能力映射，不是工具白名单。

3. `state/`

   这是本次环境初始状态的只读副本，也是生成任务时唯一需要查看的真实数据。数据库记录
   位于 `state/records.sqlite`；文件只位于 `state/filesystem_scopes/<scope_id>/`。你可以
   读取这些内容来选择真实存在的对象、条件、文件和冲突，但不得修改任何内容，也不要读取
   工作目录中的其他路径。未来执行者不会看到你在这里做的调查过程。

4. `generation_request.json`

   这里给出需要补充的任务数量、候选数量、重复执行次数等质量要求。尽量提交其中
   `candidate_count` 指定数量的有效候选。

5. `validation_feedback.json`

   `accepted_task_public` 是已经接受的任务正文，不能生成语义重复项；
   `previous_rejections` 是此前候选被程序拒绝的真实原因。

6. `candidate.schema.json`

   这是 `candidates.json` 的唯一合法结构。所有字段必须符合该 Schema，不能增加自定义字段。

7. `references/环境契约-v2.0.md`、`references/工具契约-v1.0.md` 和
   `references/任务契约-v1.0.md`

   这些文档分别解释环境状态、公开工具和最终任务包的边界。它们是理解 JSON 输入的
   参考，不是额外数据来源；对象、字段和路径只能使用前面 JSON 文件中真实存在的值。

## 要生成的内容

文件 `candidates.json` 中的 `candidates` 数组，每一项都是一条完整候选。可以把它理解为
“一份交给执行者的任务说明，加上一份只供系统验证的参考程序”，而不是六个互相独立的
字段。下面只说明这些内容之间的职责边界。

### 交给未来执行者的任务

`task_public` 是唯一会交给未来执行者的任务正文。它必须从现实使用者的角度提出一个
具体、可完成的业务请求；`output_schema` 定义执行者最终返回的业务结果结构；
`task_resources` 列出这项工作实际涉及的最小记录集合、关系、文件和工具范围，供系统检查
任务是否越界。这三者必须互相一致：正文说明目标和公开文件位置，答案结构只包含用户需要
的结果，资源范围只列完成该目标确实需要的内容。

### 只供系统验证的参考信息

`archetype_id` 指向 `task_research.json` 中一个 `generatable=true` 的现实工作类型。
`task_internal` 用一段话记录本题如何把该现实工作落到初始状态、必须满足哪些业务规则、
预期发生什么状态变化，便于独立审查；它不能直接泄露最终答案。`solution_code` 是隐藏的
参考程序，系统会真实执行它来得到参考答案和最终状态，未来执行者看不到它。

### 任务正文的具体要求

未来执行者看到的任务正文。请站在现实使用者的角度提出工作请求，而不是解释如何制作
测试题。正文必须让一个没有上下文的执行者准确知道：

- 为什么现在要做这项工作；
- 要处理哪些对象或文件；
- 筛选、比较、计算或修改时必须满足哪些规则；
- 哪些内容必须保持不变；
- 最终需要返回哪些业务结果；
- 涉及文件时，输入和输出分别位于哪个公开 `scope_id` 及该区域内的哪个相对路径。

正文不得出现公开工具的 `name`、工具调用顺序、Python、`call_tool`、`solution_code`、
`final_answer`、数据库物理路径、隐藏答案或“先调用 A 再调用 B”式实现提示。不要把
`output_schema` 原样复述到正文，也不要假设执行者知道未写出的范围、规则或文件位置。

### 最终答案结构

定义最终结构化答案。它必须是合法的 JSON Schema Draft 2020-12 封闭对象：

- 根节点 `type` 为 `object`；
- `properties` 非空，每个字段都表示使用者实际需要的业务结果；
- `required` 恰好列出全部 `properties`；
- `additionalProperties` 为 `false`。

不要把工具调用过程、调试信息或内部状态快照当作答案字段。

### 最小资源范围

这是完成该任务所需的最小环境范围：

- `record_sets`：实际使用的记录集合标识；
- `relationships`：实际使用的记录关系标识；
- `files`：实际使用或生成的文件；
- `allowed_tools`：隐藏参考程序实际允许调用的工具标识。

这些值只能使用 `environment.public.json` 中真实存在的标识，而且必须是当前具体任务实际
需要的最小范围。可以使用所选原型未列出、但确实服务于同一现实工作闭环的公开能力；不能
为了增加难度扩大范围。
`files` 中的 `path` 是对应 `scope_id` 区域内的相对路径，不能带
`filesystem_scopes/` 物理前缀。输入可以使用确有匹配文件的 glob；固定输出必须给出完整
相对路径、设置 `role="output"` 和 `path_is_exact=true`，而且输出目录必须已经存在。
每个文件的 `scope_id` 和 `path` 都必须逐字出现在 `task_public` 中。

### 隐藏参考程序

这是系统内部执行的隐藏参考程序，不会展示给未来执行者。它是普通 Python 语句序列，
唯一允许访问环境的接口是：

```python
result = call_tool("公开工具名", {{"参数名": "参数值"}})
```

工具返回以下两种结构之一，具体 `data` 字段必须以该工具的 `outputSchema` 为准：

```json
{{"success": true, "data": {{}}}}
```

```json
{{"success": false, "error": {{"code": "...", "path": "...", "message": "...", "retryable": false}}}}
```

参考程序必须：

1. 严格按 `inputSchema` 构造参数，按 `outputSchema` 读取返回值；
2. 从工具返回结果中取得对象 ID、文件信息、判断证据和后续动作参数；
3. 至少有一次后续工具调用的参数依赖前序工具结果；
4. 让最终答案真实依赖工具结果，不能先做若干无关调用后写死答案；
5. 不直接读取 `state/`、数据库或文件，不导入模块，也不访问未提供的环境对象；
6. 最后一条语句必须给 `final_answer` 赋一个 JSON object，字段与 `output_schema` 完全一致。

参考程序不得使用 `import`、函数或类定义、`with`、`while`、`raise`、异步语法、生成器、
`global`、`nonlocal`、`delete`、反射、动态执行或文件接口。
工具预计成功时直接读取其 `data`；若需要让业务失败停止程序，可使用 `assert result["success"]`
而不是 `raise`。

### 每个工具参数都必须有来源

`solution_code` 中每一次实际工具调用的每一个输入参数，都只能来自以下两类来源：

1. `task_public` 已经明确给出的对象、路径、范围、阈值、格式、开关、排序或处理规则；
2. 更早一次成功工具调用返回的值，或按照 `task_public` 的明确规则对这些返回值计算出的值。

生成时查看过的隐藏初始状态、常识、工具名称、参数名称、Schema 示例、可选参数默认值，都
不能单独成为业务取值的来源。若某个值只能在生成时从 `state/` 看到，就必须把执行者需要的
业务信息自然地写进 `task_public`；若某个可选参数没有任务依据，就省略它，不得随意选择。
工具契约只能帮助把任务语言转换成合法参数格式，不能替任务补充一个没有提出的决定。

## 真实性和难度要求

任务必须是所选现实工作类型在当前真实初始状态中的一个具体实例。不能把环境主题相关但
现实中不会一起发生的目标拼成一条任务，也不能为了使用更多工具增加与业务结果无关的动作。

先比较所有 `generatable=true` 的现实工作原型及其当前初态实例，选择能够形成最丰富业务
闭环的方向。不要因为某个方向容易写通就优先选择它；如果环境同时支持简单格式转换和需要
多轮取证、判断、处理、复核的工作，应选择后者。

一条合格的高难度任务必须在同一目标下形成完整且有真实因果依赖的业务闭环。根据当前环境
选择最自然的一种结构，而不是机械套用同一种模板：

- 决策型：建立多个候选，收集不同证据，按硬条件排除，再按明确偏好比较或排序，执行决定
  并复查结果；
- 产物型：核验输入，根据查询结果确定转换或处理参数，生成产物，重新读取并与基线比较，
  发现异常时执行任务允许的修正或回退；
- 批处理型：枚举范围内对象，逐项关联其他记录或文件，分类处理失败与业务排除，汇总结果，
  再核对汇总与最终状态；
- 诊断型：从总体异常逐层缩小范围，交叉核对多类证据，定位唯一原因或处理集合，采取动作后
  用新的观测确认问题确实消失且没有引入相关回归。

无论选择哪种结构，都必须同时满足：

1. 至少有两段连续依赖：一次工具结果决定后续调用的对象或参数，该后续结果还要继续影响
   更后的调用或业务决定；只把写出路径从一次调用复制给下一次不算充分的业务判断。
2. 至少有一个执行前无法写死的业务判断，例如从多个对象中筛选、比较证据、分类异常、根据
   观测选择处理集合或计算动作参数。仅在固定操作完成后读取一个 `identical`/`success` 布尔值
   不足以构成高难度任务。
3. 对修改或新产物进行独立复查，并让复查结果影响最终结论；不能只相信写操作返回成功。
4. 多个证据或处理环节必须各自影响最终决定。若一个聚合工具已经直接返回全部答案，不得在
   前后增加无关查询把它包装成复杂任务。

删除或调换一个关键环节应当会改变最终决定、参数或结果。调用次数和工具种类没有固定最低值，
不能重复查询、拆碎一次操作或拼接无关目标来伪造复杂度。所有调用必须服务于
`task_public` 的同一个工作目标。
{mutation_rule}

不同候选必须具有不同的业务目标、触发条件或决策结构，不能只替换对象名称、文件名或数字。

## 程序验收方式

提交后，系统会从干净初始状态真实执行参考程序，并检查：工具参数和返回结构、业务失败、
只读边界、参数来源、最终答案格式和实际状态变化。执行失败时，错误会
写入后续修复请求。通过后还会从干净初始状态重复执行，并由另一次独立检查确认任务正文、
参考程序、工具结果、最终状态和资源范围一致。你在文字中声称“已验证”不能替代这些检查。

## 输出

最终只创建或更新工作目录中的 `candidates.json`。写完后按照 `candidate.schema.json` 逐字段
检查。不要修改输入文件或 `state/`，不要创建其他交付文件，也不要在最终回复中粘贴 JSON。
"""


def build_solution_repair_prompt(round_index: int) -> str:
    return f"""# 任务：修复一段执行失败的参考程序

## 背景和输入

你没有此前对话上下文。系统正在验证一条离线环境任务的隐藏参考程序，这是第
{round_index} 次修复。

完整读取：

- `repair_request.json`：固定不允许改变的任务正文、答案结构、资源范围、当前完整程序、
  真实执行错误和已经成功的工具调用记录；
- `environment.public.json`：可用工具及其参数 Schema、返回 Schema、调用前提和副作用；
- `references/环境契约-v2.0.md`、`references/工具契约-v1.0.md`：解释状态和工具契约的
  语义边界。

真实错误来自程序在干净环境副本中的实际执行，不是建议。先确定失败属于 Python 语法、
工具参数、返回字段读取、数据依赖、分支控制或最终答案构造中的哪一类，再修复完整程序。

## 修复边界

- 保持 `task_internal`、`task_public`、`output_schema` 和 `task_resources` 表达的目标不变；
- 只能使用 `call_tool(name, arguments)` 访问环境；
- 工具名和参数结构必须来自 `environment.public.json`；每个参数的业务取值必须能追溯到
  `task_public` 或更早的成功工具结果；
- 后续工具参数应尽量从前序结果动态取得，最终答案必须由真实结果推导；
- 不得读取状态目录、导入模块、访问环境对象或硬编码对象 ID 和最终答案；
- 不得使用函数或类定义、`with`、`while`、`raise`、异步语法、生成器、反射、
  动态执行或文件接口；需要检查工具成功时使用 `assert result["success"]`；
- 不得用无关调用或相同查询伪造复杂度；
- 修复后最后一条语句仍须给 `final_answer` 赋值，并符合 `output_schema`。

## 输出

最终只创建 `repair.json`，结构必须恰好为：

```json
{{
  "solution_code": "修复后的完整 Python 程序",
  "modification": "指出原始错误、具体修改和修改为何能解决真实执行失败"
}}
```

不要修改输入文件，不要改变任务要求，也不要在最终回复中粘贴 JSON。
"""


def build_task_solution_review_prompt() -> str:
    """给独立审查 Agent 的简短说明。

    复杂度、Schema 和资源范围由 Python 自动检查；审查 Agent 只核对用户要求是否真的被
    执行，以及工具参数是否有明确出处，避免把内部流水线术语传给模型。
    """
    return """# 最后验收：程序有没有把用户的事情做完

打开 `review_request.json`。把 `task_public` 当作用户发来的完整原始要求，不要把它当作内部字段；只根据下面这些真实证据判断：

- `used_tool_contracts`：本次实际使用工具的公开用途、输入和输出定义；
- `solution_trace`：程序实际调用了什么、每次得到什么结果；
- `state_diff`、`final_state`：记录或文件实际发生了什么变化；
- `candidate_answer`：程序最后返回了什么；

逐句检查任务中的对象、文件、筛选/比较规则、修改动作、复查动作和最终结果。每一项都要
能在调用结果或最终状态中找到证据；漏做、做错、改了不该改的内容、调用失败，或答案与
结果不符，都必须拒绝。不要根据代码“看起来应该能工作”来猜测，也不要用 `task_internal`
补充用户没有说过的条件。

任务复杂度、答案格式、资源标识和文件路径已经由程序自动检查，不要评价这些内部概念。

还要逐一说明每个工具输入值的出处，防止凭空编造 ID、路径或阈值：`task` 表示值直接写在
任务中，`previous_tool_result` 表示值来自更早调用，`task_and_previous_tool_result` 表示
任务给出规则而更早结果提供具体值；无法确定就写 `unresolved` 并拒绝。`parameter_audit`
必须覆盖每次调用及其每个参数叶子值。调用序号严格从 `0` 开始：第一条调用是 `0`，第二条
是 `1`；`source_call_indices` 也使用同一套从 `0` 开始的序号，并且只能引用更早调用。
`evidence` 用普通话指出任务原句或更早调用的返回字段。

对于 `export_analysis_artifact` 的 `data`，它是一份由多个结果组装出的完整报告，只用一条
`path="/data"` 审查整份报告的来源，不要展开其中的每个字段；该调用的其他参数仍逐叶列出。

最终只创建 `review.json`，结构必须恰好为：

必须先在上下文中完成全部要求核对和参数来源核对，再一次性创建完整文件。禁止先创建 `{}`、
空数组或缺少字段的草稿；运行器会把首次出现且稳定的 JSON 当作最终交付。

```json
{
  "accepted": true,
  "reason": "逐项说明用户要求，以及每项要求在真实执行结果中的证据",
  "parameter_audit": [
    {"call_index": 0, "tool": "实际工具名", "parameters": [
      {"path": "/参数路径", "source": "task", "source_call_indices": [], "evidence": "任务中的具体原句"}
    ]}
  ],
  "issues": []
}
```

`accepted=true` 只允许在用户的全部要求都完成、所有参数都有明确来源且 `issues` 为空时使用。
不要修改其他文件，也不要在最终回复中粘贴 JSON。
"""


def _schema_errors(payload: Any, schema: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]


def _candidate_errors(
    package: CompleteEnvironmentPackage,
    candidate: ProgramTaskCandidate,
    archetypes: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if candidate.archetype_id not in archetypes:
        errors.append(f"archetype_id 不属于可生成原型：{candidate.archetype_id}")
        return errors
    public_lower = candidate.task_public.casefold()
    leaked_tools = [name for name in package.tool_names if name.casefold() in public_lower]
    if leaked_tools:
        errors.append(f"task_public 泄露工具名：{', '.join(leaked_tools)}")
    for word in ("call_tool", "solution_code", "final_answer", "output_schema"):
        if word in public_lower:
            errors.append(f"task_public 泄露内部概念：{word}")
    errors.extend(_solution_complexity_errors(candidate.solution_code))
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
    resources = candidate.task_resources
    environment = package.environment
    known_resources = {
        "record_sets": {
            str(item.get("record_set_id"))
            for item in environment.get("record_sets", [])
            if isinstance(item, dict)
        },
        "relationships": {
            str(item.get("relationship_id"))
            for item in environment.get("relationships", [])
            if isinstance(item, dict)
        },
        "allowed_tools": set(package.tool_names),
    }
    for field, known in known_resources.items():
        unknown = sorted(set(resources.get(field, [])) - known)
        if unknown:
            errors.append(f"task_resources.{field} 引用环境中不存在的标识：{unknown}")
    known_scopes = {
        str(item.get("scope_id"))
        for item in environment.get("filesystem_scopes", [])
        if isinstance(item, dict)
    }
    seen_files: set[tuple[str, str, str]] = set()
    for index, item in enumerate(resources.get("files", [])):
        scope_id = str(item.get("scope_id") or "")
        if scope_id not in known_scopes:
            errors.append(
                f"task_resources.files[{index}].scope_id 在环境中不存在"
            )
        path = str(item.get("path") or "")
        identity = (scope_id, path, str(item.get("role") or ""))
        if identity in seen_files:
            errors.append(f"task_resources.files[{index}] 重复")
        seen_files.add(identity)
        if path.startswith("filesystem_scopes/"):
            errors.append(
                f"task_resources.files[{index}].path 不得包含内部物理前缀"
            )
        if "\\" in path:
            errors.append(f"task_resources.files[{index}].path 必须使用 POSIX /")
        if item.get("role") == "output" and item.get("path_is_exact") is not True:
            errors.append(
                f"task_resources.files[{index}] 输出文件必须指定精确路径"
            )
        if scope_id in known_scopes and path:
            scope_root = package.state_root / "filesystem_scopes" / scope_id
            if item.get("role") == "input":
                matches = (
                    [scope_root / path]
                    if item.get("path_is_exact") is True
                    else list(scope_root.glob(path))
                )
                if not any(candidate.is_file() for candidate in matches):
                    errors.append(
                        f"task_resources.files[{index}] 输入路径在初态中没有匹配文件"
                    )
            elif any(token in path for token in ("*", "?", "[")):
                errors.append(
                    f"task_resources.files[{index}] 输出路径不能包含 glob"
                )
            elif not (scope_root / path).parent.is_dir():
                errors.append(
                    f"task_resources.files[{index}] 输出目录在初态中不存在"
                )
        if scope_id not in candidate.task_public or path not in candidate.task_public:
            errors.append(
                f"task_resources.files[{index}] 的 scope_id/path 未在 task_public 中明确说明"
            )
    return errors


def _state_changed(execution: ProgramExecutionResult) -> bool:
    return bool(execution.state_diff.get("changed_assets")) or any(
        execution.state_diff.get(field) for field in ("created", "modified", "deleted")
    )


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _argument_leaf_paths(value: Any, prefix: str = "") -> set[str]:
    """Return JSON Pointer paths for every concrete argument leaf value."""
    if isinstance(value, dict):
        if not value:
            return {prefix} if prefix else set()
        return {
            path
            for key, child in value.items()
            for path in _argument_leaf_paths(
                child,
                f"{prefix}/{_pointer_escape(str(key))}",
            )
        }
    if isinstance(value, list):
        if not value:
            return {prefix} if prefix else set()
        return {
            path
            for index, child in enumerate(value)
            for path in _argument_leaf_paths(child, f"{prefix}/{index}")
        }
    return {prefix} if prefix else set()


def _argument_audit_paths(tool: str, arguments: Any) -> set[str]:
    """Use one provenance entry for a complete exported report payload."""
    if (
        tool == "export_analysis_artifact"
        and isinstance(arguments, dict)
        and "data" in arguments
    ):
        control_arguments = {
            key: value for key, value in arguments.items() if key != "data"
        }
        return _argument_leaf_paths(control_arguments) | {"/data"}
    return _argument_leaf_paths(arguments)


def _parameter_audit_errors(
    review: dict[str, Any],
    trace: list[dict[str, Any]],
) -> list[str]:
    """Mechanically require the reviewer to account for every runtime argument."""
    audit = review.get("parameter_audit")
    if not isinstance(audit, list):
        return ["review.json.parameter_audit 必须是数组"]
    errors: list[str] = []
    entries: dict[int, dict[str, Any]] = {}
    for item in audit:
        if not isinstance(item, dict) or type(item.get("call_index")) is not int:
            errors.append("parameter_audit 每项必须包含整数 call_index")
            continue
        call_index = item["call_index"]
        if call_index in entries:
            errors.append(f"parameter_audit.call_index 重复：{call_index}")
        entries[call_index] = item

    expected_indices = set(range(len(trace)))
    if set(entries) != expected_indices:
        errors.append(
            "parameter_audit 必须恰好覆盖全部调用序号；"
            f"期望 {sorted(expected_indices)}，实际 {sorted(entries)}"
        )
    allowed_sources = {
        "task",
        "previous_tool_result",
        "task_and_previous_tool_result",
        "unresolved",
    }
    for call_index in sorted(expected_indices & set(entries)):
        item = entries[call_index]
        record = trace[call_index]
        if item.get("tool") != record.get("tool"):
            errors.append(f"parameter_audit[{call_index}].tool 与执行轨迹不一致")
        parameters = item.get("parameters")
        if not isinstance(parameters, list):
            errors.append(f"parameter_audit[{call_index}].parameters 必须是数组")
            continue
        paths: list[str] = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                errors.append(f"parameter_audit[{call_index}] 含非对象参数项")
                continue
            path = parameter.get("path")
            if not isinstance(path, str):
                errors.append(f"parameter_audit[{call_index}] 含非法 path")
                continue
            paths.append(path)
            source = parameter.get("source")
            if source not in allowed_sources:
                errors.append(
                    f"parameter_audit[{call_index}]{path} 的 source 非法：{source!r}"
                )
            elif source == "unresolved":
                errors.append(f"parameter_audit[{call_index}]{path} 的参数来源无法确定")
            source_indices = parameter.get("source_call_indices")
            if not isinstance(source_indices, list) or any(
                type(index) is not int for index in source_indices
            ):
                errors.append(
                    f"parameter_audit[{call_index}]{path} 的 source_call_indices 非法"
                )
                source_indices = []
            if source == "task" and source_indices:
                errors.append(
                    f"parameter_audit[{call_index}]{path} 来源仅为 task，"
                    "不应声明 source_call_indices"
                )
            if source in {"previous_tool_result", "task_and_previous_tool_result"}:
                if not source_indices or any(
                    index < 0 or index >= call_index for index in source_indices
                ):
                    errors.append(
                        f"parameter_audit[{call_index}]{path} 必须引用更早的调用序号"
                    )
            if not isinstance(parameter.get("evidence"), str) or len(
                parameter["evidence"].strip()
            ) < 8:
                errors.append(f"parameter_audit[{call_index}]{path} 缺少具体 evidence")
        expected_paths = _argument_audit_paths(
            str(record.get("tool") or ""),
            record.get("arguments", {}),
        )
        if set(paths) != expected_paths or len(paths) != len(set(paths)):
            errors.append(
                f"parameter_audit[{call_index}] 必须恰好覆盖参数叶子；"
                f"期望 {sorted(expected_paths)}，实际 {sorted(paths)}"
            )
    return errors


def _effective_tool_call_count(trace: list[dict[str, Any]]) -> int:
    """Count state-changing calls and distinct read observations.

    Repeating the same read tool with the same arguments is one observation, not
    additional task difficulty.
    """
    observations: set[str] = set()
    state_changing = 0
    for item in trace:
        change = item.get("state_diff")
        if isinstance(change, dict) and (
            change.get("changed_assets")
            or any(change.get(field) for field in ("created", "modified", "deleted"))
        ):
            state_changing += 1
            continue
        observations.add(json.dumps(
            {
                "tool": item.get("tool"),
                "arguments": item.get("arguments"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
    return state_changing + len(observations)


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
            "task_resources": candidate.task_resources,
            "solution_code": code,
            "error_type": execution.error_type,
            "error": execution.error,
            "tool_trace": execution.trace,
        })
        copy_schema_docs(repair_dir, ("环境契约-v2.0.md", "工具契约-v1.0.md"))
        prompt = build_solution_repair_prompt(round_index)
        (repair_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            _run_agent_until_json(
                agent,
                prompt,
                working_directory=repair_dir,
                filename="repair.json",
            )
            payload = read_json(repair_dir / "repair.json")
        finally:
            shutil.copytree(repair_dir, audit_dir)
    if not isinstance(payload, dict) or not str(payload.get("solution_code") or "").strip():
        raise ValueError("repair.json 缺少非空 solution_code")
    return str(payload["solution_code"]).strip(), str(payload.get("modification") or "")


def _is_infrastructure_execution_failure(execution: ProgramExecutionResult) -> bool:
    """Return true when changing the reference program cannot fix the failure."""
    message = execution.error or ""
    return execution.error_type == "RuntimeError" and any(
        marker in message
        for marker in (
            "在 Profile 中执行失败",
            "tool implementation failed",
            "tool runtime failed",
        )
    )


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
        if _is_infrastructure_execution_failure(execution):
            history.append({
                "round": round_index,
                "error": f"{execution.error_type}: {execution.error}",
                "repair_skipped": "工具基础设施执行失败，修改 Solution 无法修复",
            })
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


def _replay_comparable_state(value: Any, *, layout_file: bool = False) -> Any:
    """Ignore container timestamps embedded by GDSII/OASIS writers.

    Semantic equivalence is still established by the reference program's
    independent round-trip/diff calls and stable answer. All paths, sizes,
    non-layout files, database records and other state remain exact.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            child_is_layout = layout_file or (
                isinstance(key, str)
                and Path(key).suffix.casefold() in {
                    ".gds", ".gdsii", ".oas", ".oasis"
                }
            )
            if layout_file and key == "sha256":
                continue
            result[key] = _replay_comparable_state(
                child,
                layout_file=child_is_layout,
            )
        return result
    if isinstance(value, list):
        return [
            _replay_comparable_state(item, layout_file=layout_file)
            for item in value
        ]
    return value


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
            if _replay_comparable_state(run.final_state) != _replay_comparable_state(
                reference.final_state
            ):
                errors.append(f"clean replay {index} 的 final_state 不稳定")
            if _replay_comparable_state(run.state_diff) != _replay_comparable_state(
                reference.state_diff
            ):
                errors.append(f"clean replay {index} 的 state_diff 不稳定")
    return runs, errors


def _review_task_solution(
    *,
    agent: TaskSolutionReviewAgent | None,
    candidate: ProgramTaskCandidate,
    execution: ProgramExecutionResult,
    public_tools: list[dict[str, Any]],
    review_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    if agent is None:
        return {}, ["没有提供独立 TaskSolutionReviewAgent"]
    audit_dir = review_dir
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    with tempfile.TemporaryDirectory(prefix="agent-world-program-review-") as temporary:
        review_dir = Path(temporary)
        used_tool_names = {str(item.get("tool")) for item in execution.trace}
        write_json(review_dir / "review_request.json", {
            "task_public": candidate.task_public,
            "used_tool_contracts": [
                deepcopy(tool)
                for tool in public_tools
                if str(tool.get("name")) in used_tool_names
            ],
            "solution_trace": execution.trace,
            "final_state": execution.final_state,
            "state_diff": execution.state_diff,
            "candidate_answer": execution.answer,
        })
        prompt = build_task_solution_review_prompt()
        (review_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            _run_agent_until_json(
                agent,
                prompt,
                working_directory=review_dir,
                filename="review.json",
            )
            review = read_json(review_dir / "review.json")
        except Exception as error:
            shutil.copytree(review_dir, audit_dir)
            return {}, [f"语义审查失败：{type(error).__name__}: {error}"]
        shutil.copytree(review_dir, audit_dir)
    review_names = {
        "accepted",
        "reason",
        "parameter_audit",
        "issues",
    }
    if (
        not isinstance(review, dict)
        or set(review) != review_names
        or type(review.get("accepted")) is not bool
        or not isinstance(review.get("reason"), str)
        or len(review["reason"].strip()) < 20
        or not isinstance(review.get("issues"), list)
        or any(not isinstance(issue, str) for issue in review.get("issues", []))
    ):
        return {}, ["review.json 不符合固定审查结构"]
    parameter_errors = _parameter_audit_errors(review, execution.trace)
    if (
        review["accepted"] is not True
        or review["issues"]
        or parameter_errors
    ):
        issues = [str(item) for item in review.get("issues", [])]
        return review, [*issues, *parameter_errors] or ["独立审查未通过"]
    return review, []


def run_step2(
    *,
    step0_path: Path,
    step1_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    generation_agent: TaskSolutionAgent | None,
    review_agent: TaskSolutionReviewAgent | None,
    candidates_path: Path | None = None,
) -> Step2Result:
    """生成任务，执行/修复 Solution，并固化稳定 Ground Truth。"""
    policy.validate()
    package = load_frozen_package(step0_path)
    research = read_json(step1_path.resolve())
    archetypes = {
        str(item["archetype_id"]): item
        for item in research.get("task_archetypes", [])
        if isinstance(item, dict)
        and item.get("environment_support", {}).get("generatable") is True
    }
    if not archetypes:
        raise RuntimeError("Step 1 没有 environment_support.generatable=true 的任务原型")
    schema = read_json(Path(__file__).resolve().parent / "schemas" / "candidate.schema.json")
    output_dir = output_dir.resolve()
    output_path = output_dir / "step2_task_solution.jsonl"
    validation_path = output_dir / "step2_validation.json"
    tasks_path = output_dir / "tasks.json"
    bundle_path = output_dir / "intermediate" / "step_5_bundle.json"
    accepted: list[dict[str, Any]] = []
    formal_tasks: list[dict[str, Any]] = []
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
            with tempfile.TemporaryDirectory(prefix="agent-world-program-step2-") as temporary:
                authoring = Path(temporary)
                shutil.copytree(package.state_root, authoring / "state")
                before = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                write_json(authoring / "environment.public.json", package.public_environment())
                write_json(authoring / "task_research.json", research)
                write_json(authoring / "candidate.schema.json", schema)
                copy_schema_docs(authoring, STEP2_SCHEMA_DOCS)
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
                    _run_agent_until_json(
                        generation_agent,
                        prompt,
                        working_directory=authoring,
                        filename="candidates.json",
                    )
                    payload = read_json(authoring / "candidates.json")
                    write_json(
                        output_dir / "step2_candidates" / f"round_{round_index:02d}.json",
                        payload,
                    )
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
        ranked_candidates = sorted(
            enumerate(payload["candidates"]),
            key=lambda item: _candidate_complexity_sort_key(item[1]),
            reverse=True,
        )
        for candidate_index, raw in ranked_candidates:
            candidate = ProgramTaskCandidate.from_dict(raw)
            normalized_text = re.sub(r"\s+", " ", candidate.task_public.casefold()).strip()
            if normalized_text in accepted_texts:
                continue
            errors = _candidate_errors(package, candidate, archetypes)
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
                repair_root=output_dir / "step2_repairs",
            )
            if not execution.success:
                errors.append(f"Solution 执行失败：{execution.error_type}: {execution.error}")
            distinct_tools = {item.get("tool") for item in execution.trace}
            effective_tool_calls = _effective_tool_call_count(execution.trace)
            undeclared_tools = sorted(
                str(name)
                for name in distinct_tools
                if name not in set(candidate.task_resources["allowed_tools"])
            )
            if undeclared_tools:
                errors.append(
                    "Solution 使用了 task_resources.allowed_tools 之外的工具："
                    + ", ".join(undeclared_tools)
                )
            unused_declared_tools = sorted(
                set(candidate.task_resources["allowed_tools"])
                - {str(name) for name in distinct_tools}
            )
            if unused_declared_tools:
                errors.append(
                    "task_resources.allowed_tools 不是最小实际范围，包含未使用工具："
                    + ", ".join(unused_declared_tools)
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
                    candidate=candidate,
                    execution=execution,
                    public_tools=package.public_environment()["tools"],
                    review_dir=(
                        output_dir / "step2_reviews" /
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
            task_id = f"{package.environment['environment_id']}_program_{len(accepted):03d}"
            task_root = output_dir / "tasks" / task_id
            if task_root.exists():
                shutil.rmtree(task_root)
            published_execution = execute_solution_code(
                package,
                code,
                candidate.output_schema,
                timeout_seconds=policy.execution_timeout_seconds,
                final_state_output=task_root / "final",
            )
            publication_mismatch = (
                not published_execution.success
                or published_execution.answer != execution.answer
                or _replay_comparable_state(published_execution.final_state)
                != _replay_comparable_state(execution.final_state)
                or _replay_comparable_state(published_execution.state_diff)
                != _replay_comparable_state(execution.state_diff)
            )
            if publication_mismatch:
                if task_root.exists():
                    shutil.rmtree(task_root)
                rejections.append({
                    "round": round_index,
                    "candidate": candidate_index,
                    "archetype_id": candidate.archetype_id,
                    "task_public": candidate.task_public,
                    "reasons": ["发布重放与已验证 Ground Truth 不一致"],
                    "debug_history": debug_history,
                })
                continue
            shutil.copytree(package.state_root, task_root / "initial")
            task_resources = deepcopy(candidate.task_resources)
            public_calls = [
                {"tool": item["tool"], "arguments": deepcopy(item["arguments"])}
                for item in published_execution.trace
            ]
            formal_task = {
                "schema_version": "1.0",
                "task_id": task_id,
                "environment_id": str(package.environment["environment_id"]),
                "task_text": candidate.task_public,
                "output_schema": deepcopy(candidate.output_schema),
                "task_resources": task_resources,
                "difficulty": {"tool_calls": len(public_calls)},
                "initial_state": f"tasks/{task_id}/initial",
                "available_tools": package.public_environment()["tools"],
                "reference": {
                    "tool_calls": public_calls,
                    "answer": json.dumps(
                        published_execution.answer,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "final_state": f"tasks/{task_id}/final",
                },
            }
            formal_tasks.append(formal_task)
            accepted.append({
                TaskFields.ENV_ID: str(package.environment["environment_id"]),
                TaskFields.ENV_CLASS_NAME: str(package.environment["name"]),
                TaskFields.ENVIRONMENT_PACKAGE: "baseline_environment",
                TaskFields.TASK_ID: task_id,
                TaskFields.ARCHETYPE_ID: candidate.archetype_id,
                "task_archetype": deepcopy(archetypes[candidate.archetype_id]),
                TaskFields.TASK_INTERNAL: candidate.task_internal,
                TaskFields.TASK_PUBLIC: candidate.task_public,
                TaskFields.OUTPUT_SCHEMA: deepcopy(candidate.output_schema),
                TaskFields.SOLUTION_CODE_ORIGINAL: candidate.solution_code,
                TaskFields.SOLUTION_CODE: code,
                TaskFields.SOLUTION_CODE_FIXED: code,
                TaskFields.SOLUTION_TRACE: deepcopy(published_execution.trace),
                TaskFields.GROUND_TRUTH: {
                    "candidate_answer": deepcopy(published_execution.answer),
                    "init_state": deepcopy(published_execution.initial_state),
                    "final_state": deepcopy(published_execution.final_state),
                    "state_diff": deepcopy(published_execution.state_diff),
                },
                "solution_validation": {
                    "success": True,
                    "debug_history": debug_history,
                    "clean_replay_count": len(replays),
                    "tool_call_count": len(execution.trace),
                    "effective_tool_call_count": effective_tool_calls,
                    "distinct_tools": sorted(str(item) for item in distinct_tools),
                    "state_changed": _state_changed(execution),
                    "semantic_review": review,
                },
                "task_resources": task_resources,
                "external_task": deepcopy(formal_task),
            })
            accepted_texts.add(normalized_text)
            if len(accepted) >= policy.task_count:
                break

    write_jsonl(output_path, accepted)
    write_json(tasks_path, formal_tasks)
    write_json(output_dir / "rejected.json", rejections)
    write_json(bundle_path, {
        "schema_version": "1.0",
        "source": "program_form_step_2",
        "environment": package.executable_environment,
        "toolgen_delivery": deepcopy(package.delivery),
        "tasks": [
            {
                "task_id": task["task_id"],
                "execution": {
                    "success": True,
                    "tool_calls": deepcopy(task["reference"]["tool_calls"]),
                    "initial_state": task["initial_state"],
                    "final_state": task["reference"]["final_state"],
                },
            }
            for task in formal_tasks
        ],
    })
    write_json(validation_path, {
        "status": "passed" if len(accepted) == policy.task_count else "failed",
        "requested": policy.task_count,
        "accepted": len(accepted),
        "rejected": len(rejections),
        "rejections": rejections,
    })
    if len(accepted) != policy.task_count:
        raise RuntimeError(f"Step 2 只接受了 {len(accepted)}/{policy.task_count} 条任务")
    return Step2Result(
        output_path,
        validation_path,
        tasks_path,
        bundle_path,
        policy.task_count,
        len(accepted),
        len(rejections),
    )


def generate_solutions(
    input: dict[str, Any],
    *,
    generation_agent: Any = None,
    review_agent: Any = None,
) -> dict[str, Any]:
    """Pipeline adapter for Step 2."""
    config = input["config"]
    output = run_step2(
        step0_path=Path(input["step0_path"]),
        step1_path=Path(input["step1_path"]),
        output_dir=Path(input["run_dir"]),
        policy=config.policy,
        generation_agent=generation_agent,
        review_agent=review_agent or generation_agent,
        candidates_path=config.candidates_path,
    )
    return {
        "tasks": read_json(output.tasks_path),
        "generated_tasks": read_records(output.output_path),
        "rejected": read_json(Path(input["run_dir"]) / "rejected.json"),
        "step2_path": str(output.output_path),
        "external_bundle_path": str(output.bundle_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step0-path", type=Path, required=True)
    parser.add_argument("--step1-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--model", default="gpt-5.6-sol")
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
    result = run_step2(
        step0_path=arguments.step0_path,
        step1_path=arguments.step1_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            task_count=arguments.task_count,
            task_generation_attempts=arguments.generation_attempts,
            max_repair_rounds=arguments.max_repair_rounds,
        ),
        generation_agent=agent,
        review_agent=agent,
        candidates_path=arguments.candidates,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
