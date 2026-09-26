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
from collections import Counter
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


def _write_generation_tool_access(
    working_directory: Path,
    package: CompleteEnvironmentPackage,
) -> None:
    """Give the authoring Agent a safe, disposable view of the real tools.

    The Agent may inspect internal implementations and probe a tool against a
    fresh copy of the frozen state. Probe calls never share state with the
    eventual reference-program execution and are not part of the task output.
    """
    probe_package = working_directory / "probe_package"
    shutil.copytree(package.package_root, probe_package)
    write_json(
        working_directory / "tools.internal.json",
        {
            "environment_id": package.environment["environment_id"],
            "tools": [deepcopy(tool) for tool in package.tools],
            "warning": "仅供任务生成阶段分析；不得把 internal.code 写入 task_public。",
        },
    )
    project_root = Path(__file__).resolve().parents[2]
    probe_source = f'''#!/usr/bin/env python3
"""Inspect or probe one ToolGen tool against a disposable state copy."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, {str(project_root)!r})
from task_gen.program.utils.environment import CompleteEnvironmentPackage
from task_gen.program.utils.tool_runtime import CompleteEnvironmentRuntime

ROOT = Path(__file__).resolve().parent
TOOLS_PATH = ROOT / "tools.internal.json"
PACKAGE_PATH = ROOT / "probe_package"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--show-code")
    parser.add_argument("--tool")
    parser.add_argument("--arguments-json", default="{{}}")
    args = parser.parse_args()
    document = json.loads(TOOLS_PATH.read_text(encoding="utf-8"))
    tools = {{str(item["name"]): item for item in document["tools"]}}
    if args.list:
        print(json.dumps({{"tools": [
            {{"name": name, "description": item["description"],
              "usageConditions": item.get("usageConditions")}}
            for name, item in tools.items()
        ]}}, ensure_ascii=False, indent=2))
        return
    if args.show_code:
        if args.show_code not in tools:
            raise SystemExit(f"unknown tool: {{args.show_code}}")
        print(tools[args.show_code]["internal"]["code"])
        return
    if not args.tool:
        raise SystemExit("provide --list, --show-code TOOL, or --tool TOOL")
    if args.tool not in tools:
        raise SystemExit(f"unknown tool: {{args.tool}}")
    arguments = json.loads(args.arguments_json)
    if not isinstance(arguments, dict):
        raise SystemExit("--arguments-json must encode a JSON object")
    package = CompleteEnvironmentPackage.load(PACKAGE_PATH)
    with CompleteEnvironmentRuntime(package) as runtime:
        try:
            result = runtime.call(args.tool, arguments)
            state_diff = runtime.trace[-1].state_diff if runtime.trace else {{}}
            print(json.dumps({{"tool": args.tool, "result": result,
                              "state_diff": state_diff}},
                             ensure_ascii=False, indent=2))
        except Exception as error:
            print(json.dumps({{"tool": args.tool, "error_type": type(error).__name__,
                              "error": str(error)}},
                             ensure_ascii=False, indent=2))
            raise SystemExit(2)


if __name__ == "__main__":
    main()
'''
    probe_path = working_directory / "tool_probe.py"
    probe_path.write_text(probe_source, encoding="utf-8")
    probe_path.chmod(0o755)


@dataclass
class ProgramTaskCandidate:
    archetype_id: str
    task_internal: str
    workspace_brief: str
    task_summary: str
    task_public: str
    output_schema: dict[str, Any]
    task_resources: dict[str, Any]
    solution_code: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            archetype_id=str(value["archetype_id"]).strip(),
            task_internal=str(value["task_internal"]).strip(),
            workspace_brief=str(value["workspace_brief"]).strip(),
            task_summary=str(value["task_summary"]).strip(),
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
    ast.Try,
    ast.While,
    ast.Delete,
    ast.Lambda,
    ast.Match,
    ast.AsyncFor,
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

    def process(statements: list[ast.stmt], *, control_tainted: bool = False) -> None:
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
                derived = (
                    control_tainted
                    or _contains_tool_call(value)
                    or _uses_tainted_value(value, tainted)
                )
                if "final_answer" in names:
                    final_answer_depends_on_tool = derived
                if derived:
                    tainted.update(names)
                else:
                    tainted.difference_update(names)
                continue
            if isinstance(statement, ast.For):
                count_dependent_calls(statement.iter)
                iterator_tainted = _contains_tool_call(
                    statement.iter
                ) or _uses_tainted_value(
                    statement.iter, tainted
                )
                if iterator_tainted:
                    tainted.update(_assigned_names(statement.target))
                process(
                    statement.body,
                    control_tainted=control_tainted or iterator_tainted,
                )
                process(statement.orelse, control_tainted=control_tainted)
                continue
            if isinstance(statement, ast.If):
                count_dependent_calls(statement.test)
                test_tainted = _contains_tool_call(
                    statement.test
                ) or _uses_tainted_value(statement.test, tainted)
                process(
                    statement.body,
                    control_tainted=control_tainted or test_tainted,
                )
                process(
                    statement.orelse,
                    control_tainted=control_tainted or test_tainted,
                )
                continue
            if isinstance(statement, ast.Try):
                process(statement.body, control_tainted=control_tainted)
                for handler in statement.handlers:
                    process(handler.body, control_tainted=control_tainted)
                process(statement.orelse, control_tainted=control_tainted)
                process(statement.finalbody, control_tainted=control_tainted)
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
    per_item_tool_calls: int
    conditional_tool_calls: int
    result_calculations: int


def _solution_complexity_profile(source: str) -> SolutionComplexityProfile:
    """Approximate the observation -> decision -> action depth of a Solution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return SolutionComplexityProfile(0, 0, 0, 0, 0, 0, 0, 0, 0)

    depths: dict[str, int] = {}
    tool_calls = 0
    tool_names: set[str] = set()
    dependent_tool_calls = 0
    max_dependency_depth = 0
    business_decisions = 0
    loops_over_tool_results = 0
    per_item_tool_calls = 0
    conditional_tool_calls = 0
    result_calculations = 0

    def count_tool_call_sites(node: ast.AST) -> int:
        return sum(
            1
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "call_tool"
        )

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

    def inspect_comprehensions(node: ast.AST) -> None:
        """Count result-dependent filtering inside list/set/dict comprehensions.

        A common valid solution selects records with a comprehension, for
        example ``[point for point in result if point["axis"] == target]``.
        The previous statement-only scan missed that business decision even
        though the iterator and predicate were derived from a tool result.
        """
        nonlocal business_decisions, loops_over_tool_results
        for item in ast.walk(node):
            if not isinstance(
                item, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                continue
            for generator in item.generators:
                iterator_depth = expression_depth(generator.iter)
                if iterator_depth <= 0:
                    continue
                loops_over_tool_results += 1
                for predicate in generator.ifs:
                    if is_business_decision(predicate, iterator_depth):
                        business_decisions += 1

    def process(statements: list[ast.stmt]) -> None:
        nonlocal business_decisions, loops_over_tool_results
        nonlocal per_item_tool_calls, conditional_tool_calls, result_calculations
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                value_depth = expression_depth(value)
                inspect_comprehensions(value)
                if value_depth > 0 and any(
                    isinstance(item, ast.BinOp) for item in ast.walk(value)
                ):
                    result_calculations += 1
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
                    per_item_tool_calls += sum(
                        count_tool_call_sites(item) for item in statement.body
                    )
                for name in _assigned_names(statement.target):
                    depths[name] = iterator_depth
                process(statement.body)
                process(statement.orelse)
                continue
            if isinstance(statement, ast.If):
                test_depth = expression_depth(statement.test)
                if is_business_decision(statement.test, test_depth):
                    business_decisions += 1
                    conditional_tool_calls += sum(
                        count_tool_call_sites(item)
                        for item in [*statement.body, *statement.orelse]
                    )
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
        per_item_tool_calls=per_item_tool_calls,
        conditional_tool_calls=conditional_tool_calls,
        result_calculations=result_calculations,
    )


def _solution_difficulty_forms(
    profile: SolutionComplexityProfile,
) -> frozenset[str]:
    """Describe how a solution is difficult without prescribing one workflow."""
    forms: set[str] = set()
    if profile.max_dependency_depth >= 3 and profile.dependent_tool_calls >= 2:
        forms.add("result_chain")
    if profile.loops_over_tool_results >= 1:
        forms.add("result_batch")
    if profile.per_item_tool_calls >= 1:
        forms.add("per_item_tool_work")
    if profile.business_decisions >= 2:
        forms.add("multiple_runtime_decisions")
    if profile.conditional_tool_calls >= 1:
        forms.add("conditional_tool_path")
    if profile.result_calculations >= 1:
        forms.add("result_calculation")
    if profile.distinct_tools >= 4:
        forms.add("multiple_evidence_sources")
    return frozenset(forms)


def _solution_complexity_errors(source: str) -> list[str]:
    """Require real runtime reasoning without imposing one workflow shape."""
    profile = _solution_complexity_profile(source)
    errors: list[str] = []
    has_deep_dependency = (
        profile.max_dependency_depth >= 3
        and profile.dependent_tool_calls >= 2
    )
    has_batch_reasoning = (
        profile.loops_over_tool_results >= 1
        and profile.tool_calls >= 3
    )
    has_multi_decision_reasoning = (
        profile.business_decisions >= 2
        and profile.distinct_tools >= 3
    )
    has_multi_evidence_reasoning = (
        profile.business_decisions >= 1
        and profile.distinct_tools >= 4
        and profile.tool_calls >= 4
    )
    if not (
        has_deep_dependency
        or has_batch_reasoning
        or has_multi_decision_reasoning
        or has_multi_evidence_reasoning
    ):
        errors.append(
            "任务缺少足够的运行时推理：应自然具备深结果依赖、基于工具结果的批处理，"
            "或多个会改变后续操作/最终结论的业务判断之一"
        )
    if profile.business_decisions == 0 and profile.loops_over_tool_results == 0:
        errors.append(
            "任务缺少基于真实工具结果的筛选、比较、分类、条件判断或逐项处理"
        )
    return errors


def _candidate_complexity_sort_key(raw: dict[str, Any]) -> tuple[int, ...]:
    profile = _solution_complexity_profile(str(raw.get("solution_code") or ""))
    difficulty_forms = _solution_difficulty_forms(profile)
    balanced_score = (
        min(profile.max_dependency_depth, 5) * 3
        + min(profile.business_decisions, 5) * 2
        + min(profile.loops_over_tool_results, 3) * 3
        + min(profile.per_item_tool_calls, 4) * 4
        + min(profile.conditional_tool_calls, 4) * 3
        + min(profile.result_calculations, 4) * 2
        + min(profile.dependent_tool_calls, 6) * 2
        + min(profile.distinct_tools, 6)
        + min(profile.tool_calls, 10)
    )
    return (
        len(difficulty_forms),
        balanced_score,
        profile.per_item_tool_calls + profile.conditional_tool_calls,
        profile.business_decisions + profile.loops_over_tool_results,
        profile.max_dependency_depth,
        profile.dependent_tool_calls,
        profile.distinct_tools,
        profile.tool_calls,
    )


def _rank_candidates_for_diversity(
    candidates: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Greedily prefer strong candidates that add a different reasoning shape.

    This is a portfolio preference, not a per-task requirement. A candidate is
    still allowed to use any natural workflow; when several are available, the
    batch avoids selecting the same structural pattern repeatedly.
    """
    form_counts: Counter[str] = Counter()
    signature_counts: Counter[tuple[str, ...]] = Counter()
    archetype_counts: Counter[str] = Counter()
    for item in accepted:
        profile = _solution_complexity_profile(
            str(item.get(TaskFields.SOLUTION_CODE) or "")
        )
        forms = _solution_difficulty_forms(profile)
        form_counts.update(forms)
        signature_counts.update([tuple(sorted(forms))])
        archetype_counts.update([str(item.get(TaskFields.ARCHETYPE_ID) or "")])

    remaining = list(enumerate(candidates))
    ranked: list[tuple[int, dict[str, Any]]] = []
    while remaining:
        def selection_key(item: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
            _, raw = item
            profile = _solution_complexity_profile(
                str(raw.get("solution_code") or "")
            )
            forms = _solution_difficulty_forms(profile)
            signature = tuple(sorted(forms))
            archetype_id = str(raw.get("archetype_id") or "")
            new_forms = sum(form_counts[form] == 0 for form in forms)
            underused_forms = sum(
                1.0 / (1 + form_counts[form]) for form in forms
            )
            return (
                new_forms,
                signature_counts[signature] == 0,
                archetype_counts[archetype_id] == 0,
                underused_forms,
                _candidate_complexity_sort_key(raw),
            )

        chosen = max(remaining, key=selection_key)
        remaining.remove(chosen)
        ranked.append(chosen)
        _, raw = chosen
        forms = _solution_difficulty_forms(
            _solution_complexity_profile(str(raw.get("solution_code") or ""))
        )
        form_counts.update(forms)
        signature_counts.update([tuple(sorted(forms))])
        archetype_counts.update([str(raw.get("archetype_id") or "")])
    return ranked


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
        if isinstance(node, ast.Name) and node.id in {"true", "false", "null"}:
            replacement = {"true": "True", "false": "False", "null": "None"}[node.id]
            errors.append(
                f"第 {node.lineno} 行使用了 JSON 字面量 {node.id}；"
                f"solution_code 是 Python，必须写 {replacement}"
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
    # Keep the authoring request focused on the artifact the Agent must create.
    # Runtime validation and semantic review happen in Python after this call;
    # repeating their implementation details here distracts from task design.
    return f"""# 目标：生成真实、可执行且具有自然难度的一条任务及其参考程序

你正在为一个已经冻结的离线环境制作一条训练任务。请生成 `candidates.json`，其中必须只有
`generation_request.json` 中 `candidate_count=1` 指定的一条候选任务；不能生成备用候选或把
多个任务放在同一个文件中。每条候选必须是一个现实工作场景的具体实例，
并同时包含：

1. 给未来执行者看的任务正文 `task_public`；
2. 描述最终答案结构的 `output_schema`；
3. 只供系统执行的隐藏参考程序 `solution_code`；
4. 说明该任务实际涉及哪些数据和工具的 `task_resources`。

不要写解释报告，完成后直接在工作目录创建或覆盖 `candidates.json`。

## 你可以使用的输入

先阅读下面这些文件，再开始设计任务：

- `task_research.json`：现实工作调研。只选择其中 `environment_support.generatable=true` 的
  任务类型作为业务依据。候选的 `archetype_id` 必须逐字复制其中一个现有 ID，不能创建新 ID、
  改名或把多个 ID 合成新名称；复杂任务仍应归入其最主要的现实工作类型；
- `environment.public.json`：公开的记录集合、关系、文件区域和工具契约；
- `state/`：当前环境真实初始数据，只读。用它确认真实存在的记录、文件和可用条件；
- `tools.internal.json`：公开工具的实现代码、输入 Schema、输出 Schema 和使用条件；
- `candidate.schema.json`：`candidates.json` 的字段格式；
- `generation_request.json` 和 `validation_feedback.json`：本轮数量、已接受任务和之前失败原因；
- `references/`：环境、工具和任务契约，需要理解边界时再查阅。

`state/` 和工具内部代码只用于你生成任务时调查。不要把内部数据库路径、主机路径、内部
实现代码或隐藏答案写进 `task_public`。

## 生成顺序

对这一条候选按以下顺序工作：

### 1. 选择一个真实业务目标

从 `task_research.json` 中选择一项 `environment_support.generatable=true` 的现实工作，结合
当前初始数据把它写成一条具体任务。任务应忠于调研描述，具有明确目标和交付结果，并在当前
环境能够支撑的范围内尽量有一定难度。不要套用固定任务类型、固定工作流或固定调用次数。
先保证它是一项聚焦的现实工作，再保留这项工作本身需要的分析、比较和判断。不要为了显得
更完整或更困难，额外加入源码检查、关系核验、来源追溯、文件交付或其他旁支。只有它们是完成
该现实工作不可缺少的一部分，且 solution 会真实调用工具完成并在最终答案中交付证据时，才能写入
`task_public`。

这些任务将用于训练 Agent 的工具调用能力。在多个方向同样真实、同样有可靠调研依据且都能正确执行时，
优先选择需要更多必要工具交互才能完成的具体任务，不要优先选择一次聚合调用就直接返回全部答案的简单实例。
同一工具可以针对不同对象或条件多次调用，前提是每次返回都为最终判断提供不可替代的证据。不设固定最低调用次数；
删除任何一次调用都应会导致某项任务要求无法完成、某个后续参数无法确定，或最终结论缺少必要证据。

选择前先在内部比较至少三个 `generatable=true` 的具体实例：根据真实工具的输入输出，估算完成各自全部
必要业务要求会产生多少次有效交互，以及是否覆盖工具选择、前序结果传参、多对象处理和跨结果汇总。
在真实性、证据可靠性和可执行性不降低的前提下，选择交互更丰富的实例。这个内部比较不要写入 `task_public`。

上述比较必须采用两级顺序，不能直接按调用次数排序：

1. 先比较是否真正推进了领域工作。当环境能够实际运行仿真或分析、产生新结果、比较候选、优化参数、
   生成交付产物或支持具体业务决策时，优先从这些工作中选择。配置就绪检查、来源追溯、文件完整性检查、
   历史结果复核和回归签核仍是合法现实工作，但它们不能因为容易拆成较多查询就压过可执行的领域任务。
2. 只在同一领域价值层级内，再比较多少次必要工具交互。一条只读审计任务即使需要八次查询，也不应因此
   取代一条会真实运行仿真、评估设计并作出选择的任务。

任务的深度优先来自结果之间的因果关系，而不是更长的步骤描述。如果调研和环境同时支持两种工作：
一种只判断已有方案是否合格，另一种能从初始方案出发产生新方案，再把新方案交给后续工具在更全面的
条件下复验，应优先后者。前一步产生的参数、对象 ID、文件路径或候选集应真实成为后续调用的输入，
最终答案同时说明产生了什么、与起点相比发生了什么变化、以及扩大条件后是否仍然成立。

这是同一个业务目标中的闭环，不是把多个任务硬拼在一起。候选任务仍只属于一个主要 `archetype_id`，
但可以包含为达成同一交付结果而必不可少的上游方案产生和下游验证。如果后续工具无法消费前一结果，或来源没有支持
这种业务闭环，就不要为了增加深度而编造参数调整规则。

如果 `validation_feedback.json` 中的上一个候选已经是真实、工具交互丰富且所有工具都成功执行的任务，仅因个别参数
来源、答案字段或要求对齐问题被拒绝，本轮应保留同一业务目标和必要交互，只修正反馈指出的局部问题。
不得为了更容易通过而改选能被单个聚合工具直接完成的简单任务。只有上一个业务目标本身不真实、环境不支持，
或必须依赖无关调用才能显得复杂时，才改选方向。

### 2. 先确认数据和工具真的可用

阅读相关工具的 `internal.code`，确认它实际读取的记录、文件和软件依赖。对不确定的调用可以
使用下面的方式做真实探测：

```bash
python tool_probe.py --show-code TOOL_NAME
python tool_probe.py --tool TOOL_NAME --arguments-json '{{"参数": "值"}}'
```

探测必须使用当前环境中真实存在的对象和参数。不要把已知会失败的调用写进 solution_code。
本次只生成一条任务，并避免与 `validation_feedback.json` 中已接受任务重复。任务的具体难度、
步骤和工具使用方式由所选现实工作与当前数据决定；solution 必须完成任务正文要求的工作，
最终答案必须由真实工具结果构造。

探测时不仅要看调用是否成功，还要看每个返回字段的确切含义。不得用名称相似但语义不同的字段
回答任务，例如不能把“扫描首末点变化”当成“末点相对于指定基线的变化”。如果工具没有直接返回
任务要求的量，就必须在 solution 中用返回的原始值明确计算；无法计算时不要把该要求写入任务。

### 3. 编写任务正文 `task_public`

正文要像真实用户提出的工作请求，不要描述测试题制作过程，也不要出现工具名、Python、
`call_tool` 或调用顺序。也不要让用户替程序填写 `direction=...`、`comparison_mode=...`
之类工具参数；要用领域工作语言说清楚意图，例如“从该案例确认其所属结构”。正文必须明确写出：

- 当前要解决的业务问题和目标对象；
- 执行者需要使用的公开记录、关系或文件位置；
- 现实工作要求遵守的全部规则和限制；
- 最终必须返回的每一项业务结果；
- 哪些内容不能修改，以及允许的输出位置。

如果核心工作是产生新的设计、计算结果或交付物，正文开头应直接说明要完成的设计或决策，
不要用“审计”、“复核”、“签核”或“检查”把整个任务包装成只读工作。必要的输入校验只是执行约束，
不应取代真正的业务目标。

任务中的对象、路径、阈值、范围、开关和排序规则必须来自真实数据或现实工作要求，不能凭空
添加。对象 ID、文件路径、数值、布尔选择和排序方式应在正文中明确写出；仅用于把业务意图
序列化的工具枚举值，可以由正文的自然语言要求唯一确定，无需把参数名和枚举字符串暴露给用户。
若任务要求返回某个信息，必须在后面的 `output_schema` 中为它设置字段。
如果任务涉及文件，正文必须逐字写出文件区域的 `scope_id` 和该区域内的相对路径，例如
`scope_id=mobility_models`、`source/mobility.py`；`task_resources.files` 中的每一项都必须
能在正文中找到完全相同的两段文字。只有 solution 实际通过工具读取、检查或生成的文件才可
列入 `task_resources.files`；如果 solution 不会使用该文件，就不要把它列为任务资源。
某个计算工具内部使用了源码或数据文件，不等于 solution 已经“检查”了该文件；除非真的有工具读取并
返回检查证据，否则不得在正文中要求检查它，也不得把它列入 `task_resources.files`。关系核验同理：
直接读取两端记录不等于已经通过指定关系完成核验。
如果任务指定一个确定的交付文件，使用完整相对路径并设置 `path_is_exact=true`。
如果工具合同明确会在某个目录下自行产生多个文件，而具体文件名只能在运行后知道，
可以声明以 `/` 结尾的目录边界并设置 `path_is_exact=false`；不得用这种方式模糊一个本来
就能在任务中确定的文件位置。

### 4. 从任务正文反推 `output_schema`

先列一份“任务要求的最终结果清单”，再设计 JSON Schema。Schema 必须是封闭的 object：

- 每个用户要求的结果都有对应字段；
- 每个字段都能由 solution 的工具结果计算得到；
- `required` 恰好包含全部 properties；
- `additionalProperties` 必须为 `false`。

不要把工具调用日志、内部状态快照、调试信息或工具名当作用户答案字段。

### 5. 编写隐藏 `solution_code`

这是系统用来产生标准答案的普通 Python 代码，未来执行者看不到。它只能通过
`call_tool(name, arguments)` 使用环境，不能直接读 `state/`、数据库或文件。

代码只能使用一组很小的 Python 语句：变量赋值、`call_tool` 调用、`assert`、`if/elif/else`、
`for`，以及这些语句需要的字典/列表、索引、比较、布尔和算术表达式。不要使用其他控制
结构或运行时能力，例如 `import`、函数/类定义、`try/except`、`while`、`lambda`、模式匹配、
文件接口、动态执行或主机路径。{mutation_rule}

每一次工具调用的每一个输入参数都必须单独满足以下来源规则：

- 业务对象 ID、文件路径、数值、阈值、范围、开关和排序规则，必须由 `task_public`
  明确给出；工具内部的方向或模式枚举可以由正文的业务说法唯一映射，但不得仅依赖工具默认值或猜测；
- 动态 ID、路径和业务值，必须来自更早一次成功工具调用的返回结果，或按照 `task_public`
  明确写出的规则由这些结果计算得到；
- 不能把生成时从 `state/` 查到的值、工具 Schema 示例、工具默认值、常识或内部代码中的
  示例值直接写入调用参数；
- 如果一个可选参数没有任务依据，就省略它；不能为了调用合法或增加难度自行补参数；
- 先在头脑中逐个核对参数来源，再写出 solution。后续调用需要使用前序结果中的对象时，
  必须通过变量传递，不能重新手写同一个 ID。

如果任务的核心目标包含迭代、优化或候选生成，后续调用必须真正使用前一次工具返回的新参数或新对象。
不得在任务正文中声称“根据结果迭代”，却在 solution 中继续使用原始常量或另一组手写参数。

最终只能用工具结果和任务明确要求的计算构造 `final_answer`，且每个字段都必须出现在
`output_schema` 中；使用 Python 字面量 `True`、`False`、`None`，不要使用 JSON 的
`true`、`false`、`null`；最后一条语句必须是 `final_answer = ...`。

在提交前，用刚才的真实探测结果按 solution 逻辑构造一次完整 `final_answer`，再逐句回看
`task_public`：每一个“检查、核对、比较、计算、返回、保留、生成”要求，都必须能指向
某次实际调用的直接证据、明确计算或 `final_answer` 字段。只有在句子表面上相似，或只出现在
工具输出但没有进入最终交付，都不算完成。

### 6. 填写资源和内部说明

- `task_resources` 只列这条任务实际用到的最小 `record_sets`、`relationships`、`files` 和
  `allowed_tools`；
- `task_internal` 简要记录业务闭环、关键判断和预期结果，不要复制隐藏答案；
- `workspace_brief` 必须逐字使用 `environment.public.json` 中的环境摘要；
- `task_summary` 用一两句话描述本题业务目标，用于批量去重，不要写工具调用过程。

## 提交前自检

逐条检查：

1. 任务目标是否像现实工作，而不是工具操作清单？
2. 任务正文中的每个最终要求是否都有 Schema 字段和 solution 赋值？
3. 每个工具参数是否来自任务正文或更早的成功工具结果？
4. 所有对象、文件和工具是否在当前环境中真实存在且已探测可用？
5. 现实工作要求的每个必要环节是否都被保留，且没有加入无关环节？
6. 这条任务是否与 `validation_feedback.json` 中已接受任务的业务目标不同，而不是只替换
   ID、文件名或数字？
7. 如果环境中有同样真实可行、但能自然覆盖更多必要工具交互的实例，当前是否误选了被单一聚合工具
   直接完成的简单实例？同时确认每一次保留的调用都不是为了增加数量而加入。

只提交符合 `candidate.schema.json` 的 `candidates.json`，不要修改输入文件或 `state/`。
这是第 {round_index} 轮；请吸收 `validation_feedback.json` 中的失败原因，不要重复同一错误。
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

- 保持 `task_internal`、`workspace_brief`、`task_summary`、`task_public`、`output_schema`
  和 `task_resources` 表达的目标不变；
- 只能使用 `call_tool(name, arguments)` 访问环境；
- 工具名和参数结构必须来自 `environment.public.json`；每个参数的业务取值必须能追溯到
  `task_public` 或更早的成功工具结果；
- 当后续对象由前序结果选择时，参数必须动态取得；并列收集独立证据时，参数可以直接来自
  任务正文，不必人为制造串行依赖；最终答案必须由真实结果推导；
- 不得读取状态目录、导入模块、访问环境对象或硬编码对象 ID 和最终答案；
- 不得使用函数或类定义、`with`、`while`、`raise`、异步语法、生成器、反射、
  动态执行或文件接口；需要检查工具成功时使用 `assert result["success"]`；
- 不得用无关调用或相同查询伪造复杂度；
- `solution_code` 是 Python 源代码，必须全文使用 `True`、`False`、`None`，不得残留
  JSON 的 `true`、`false`、`null`；修复一种字面量错误时要检查完整程序中的所有同类位置；
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
    expected_brief = _environment_workspace_brief(package.environment)
    if candidate.workspace_brief != expected_brief:
        errors.append(
            "workspace_brief 必须逐字等于当前环境的 environment.summary，"
            "同一环境不能为不同任务编写不同环境介绍"
        )
    if len(re.sub(r"\s+", " ", candidate.task_summary).strip()) < 40:
        errors.append("task_summary 必须具体记录本题的业务目标，不能是泛化标题")
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
            elif item.get("path_is_exact") is not True and not path.endswith("/"):
                errors.append(
                    f"task_resources.files[{index}] 动态输出目录必须以 / 结尾"
                )
            elif (scope_root / path).exists() and (
                item.get("path_is_exact") is True
                and (scope_root / path).is_dir()
            ):
                errors.append(
                    f"task_resources.files[{index}] 精确输出路径不能是已有目录"
                )
            elif (scope_root / path).exists() and (
                item.get("path_is_exact") is not True
                and not (scope_root / path).is_dir()
            ):
                errors.append(
                    f"task_resources.files[{index}] 动态输出路径不能是已有文件"
                )
        if scope_id not in candidate.task_public or path not in candidate.task_public:
            errors.append(
                f"task_resources.files[{index}] 的 scope_id/path 未在 task_public 中明确说明"
            )
    return errors


def _environment_workspace_brief(environment: dict[str, Any]) -> str:
    """Return the stable environment-level context used by every task.

    v2 environments provide ``summary``. The description fallback keeps the
    task authoring path usable for legacy v1 packages during migration.
    """
    return str(
        environment.get("summary")
        or environment.get("description")
        or environment.get("name")
        or ""
    ).strip()


def _task_descriptor_key(value: str) -> str:
    """Normalize a task summary for deterministic within-run de-duplication.

    IDs and numeric settings identify an instance, but they do not make a new
    business task. Removing them catches the common "same task, new project ID"
    variant while retaining the actual business wording for comparison.
    """
    text = value.casefold()
    text = re.sub(r"\b[a-z][a-z0-9_-]*\d[a-z0-9_-]*\b", " ", text)
    text = re.sub(r"\d+(?:\.\d+)?", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


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
            "workspace_brief": candidate.workspace_brief,
            "task_summary": candidate.task_summary,
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


def _dynamic_output_prefixes(
    candidate: ProgramTaskCandidate,
) -> dict[str, tuple[str, ...]]:
    """Return declared directories whose concrete files are tool-generated.

    Some real tools deterministically choose an artifact directory but use
    process IDs or random suffixes for scratch files inside it.  The directory
    is the public resource boundary; those private filenames are not part of
    the task contract.
    """
    prefixes: dict[str, list[str]] = {}
    for item in candidate.task_resources.get("files", []):
        if item.get("role") != "output" or item.get("path_is_exact") is True:
            continue
        scope_id = str(item.get("scope_id") or "")
        path = str(item.get("path") or "").strip("/")
        if scope_id and path:
            prefixes.setdefault(scope_id, []).append(path)
    return {
        scope_id: tuple(sorted(set(paths)))
        for scope_id, paths in prefixes.items()
    }


def _path_has_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = path.strip("/")
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in prefixes
    )


def _without_dynamic_output_files(
    value: dict[str, Any],
    prefixes_by_scope: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Remove only files hidden behind declared dynamic output directories."""
    normalized = deepcopy(value)
    scopes = normalized.get("filesystem_scopes")
    if not isinstance(scopes, dict):
        return normalized
    emptied_scopes: set[str] = set()
    for scope_id, prefixes in prefixes_by_scope.items():
        scope = scopes.get(scope_id)
        if not isinstance(scope, dict):
            continue
        files = scope.get("files")
        if isinstance(files, dict):
            scope["files"] = {
                path: metadata
                for path, metadata in files.items()
                if not _path_has_prefix(str(path), prefixes)
            }
        for field in ("created", "modified", "deleted"):
            paths = scope.get(field)
            if isinstance(paths, list):
                scope[field] = [
                    path
                    for path in paths
                    if not _path_has_prefix(str(path), prefixes)
                ]
        changes = scope.get("changes")
        if isinstance(changes, dict):
            scope["changes"] = {
                path: change
                for path, change in changes.items()
                if not _path_has_prefix(str(path), prefixes)
            }
        if not any(scope.get(field) for field in ("created", "modified", "deleted")):
            emptied_scopes.add(scope_id)
    changed_assets = normalized.get("changed_assets")
    if isinstance(changed_assets, list):
        normalized["changed_assets"] = [
            asset for asset in changed_assets if asset not in emptied_scopes
        ]
    return normalized


def _dynamic_output_errors(
    candidate: ProgramTaskCandidate,
    execution: ProgramExecutionResult,
) -> list[str]:
    """Confirm that every declared dynamic output directory produced a file."""
    errors: list[str] = []
    scopes = execution.final_state.get("filesystem_scopes", {})
    for scope_id, prefixes in _dynamic_output_prefixes(candidate).items():
        scope = scopes.get(scope_id, {}) if isinstance(scopes, dict) else {}
        files = scope.get("files", {}) if isinstance(scope, dict) else {}
        for prefix in prefixes:
            if not any(
                _path_has_prefix(str(path), (prefix,))
                for path in files
            ):
                errors.append(
                    f"动态输出目录 {scope_id}:{prefix}/ 没有产生任何文件"
                )
    return errors


def _replay_comparable_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare public calls and results, excluding per-call filesystem diffs."""
    return [
        {key: deepcopy(value) for key, value in item.items() if key != "state_diff"}
        for item in trace
    ]


def _replay_solution(
    *,
    package: CompleteEnvironmentPackage,
    code: str,
    schema: dict[str, Any],
    policy: ProgramGenerationPolicy,
    first: ProgramExecutionResult,
    candidate: ProgramTaskCandidate,
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
        dynamic_prefixes = _dynamic_output_prefixes(candidate)
        for index, run in enumerate(runs[1:], start=1):
            if run.answer != reference.answer:
                errors.append(f"clean replay {index} 的 final_answer 不稳定")
            if _replay_comparable_trace(run.trace) != _replay_comparable_trace(
                reference.trace
            ):
                errors.append(f"clean replay {index} 的工具返回结果不稳定")
            run_final = _without_dynamic_output_files(
                run.final_state, dynamic_prefixes
            )
            reference_final = _without_dynamic_output_files(
                reference.final_state, dynamic_prefixes
            )
            if _replay_comparable_state(run_final) != _replay_comparable_state(
                reference_final
            ):
                errors.append(f"clean replay {index} 的 final_state 不稳定")
            run_diff = _without_dynamic_output_files(
                run.state_diff, dynamic_prefixes
            )
            reference_diff = _without_dynamic_output_files(
                reference.state_diff, dynamic_prefixes
            )
            if _replay_comparable_state(run_diff) != _replay_comparable_state(
                reference_diff
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
    accepted_summaries: set[str] = set()
    rejections: list[dict[str, Any]] = []
    # One Agent call produces one task. A failed task consumes one retry for
    # the current slot; a successful task advances the slot to the next task.
    rounds = (
        1
        if candidates_path is not None
        else policy.task_count * policy.task_generation_attempts
    )
    attempts_for_current_task = 0

    for round_index in range(1, rounds + 1):
        if len(accepted) >= policy.task_count:
            break
        if candidates_path is None:
            if attempts_for_current_task >= policy.task_generation_attempts:
                break
            attempts_for_current_task += 1
        accepted_before_round = len(accepted)
        if candidates_path is not None:
            payload = read_json(candidates_path.resolve())
        else:
            if generation_agent is None:
                raise ValueError("没有提供 TaskSolutionAgent")
            with tempfile.TemporaryDirectory(prefix="agent-world-program-step2-") as temporary:
                authoring = Path(temporary)
                shutil.copytree(package.state_root, authoring / "state")
                _write_generation_tool_access(authoring, package)
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
                    "workspace_brief": _environment_workspace_brief(package.environment),
                    "remaining_tasks": 1,
                    "candidate_count": 1,
                })
                write_json(authoring / "validation_feedback.json", {
                    "accepted_task_public": [item[TaskFields.TASK_PUBLIC] for item in accepted],
                    "accepted_task_summaries": [
                        item[TaskFields.TASK_SUMMARY] for item in accepted
                    ],
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
        if candidates_path is None and len(payload.get("candidates", [])) != 1:
            rejections.append({
                "round": round_index,
                "candidate": None,
                "reasons": [
                    "单任务生成轮次必须恰好返回 1 条候选，不能通过一次 Agent 调用批量生成任务"
                ],
            })
            continue
        ranked_candidates = (
            _rank_candidates_for_diversity(payload["candidates"], accepted)
            if candidates_path is not None
            else [(0, payload["candidates"][0])]
        )
        for candidate_index, raw in ranked_candidates:
            candidate = ProgramTaskCandidate.from_dict(raw)
            normalized_text = re.sub(r"\s+", " ", candidate.task_public.casefold()).strip()
            normalized_summary = _task_descriptor_key(candidate.task_summary)
            if normalized_text in accepted_texts or normalized_summary in accepted_summaries:
                rejections.append({
                    "round": round_index,
                    "candidate": candidate_index,
                    "archetype_id": candidate.archetype_id,
                    "task_summary": candidate.task_summary,
                    "reasons": ["任务正文或任务业务摘要与本轮已接受任务重复"],
                })
                continue
            errors = _candidate_errors(package, candidate, archetypes)
            if errors:
                rejections.append({
                    "round": round_index,
                    "candidate": candidate_index,
                    "archetype_id": candidate.archetype_id,
                    "task_summary": candidate.task_summary,
                    "task_public": candidate.task_public,
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
            if execution.success:
                errors.extend(_dynamic_output_errors(candidate, execution))
            replays: list[ProgramExecutionResult] = []
            if not errors:
                replays, replay_errors = _replay_solution(
                    package=package,
                    code=code,
                    schema=candidate.output_schema,
                    policy=policy,
                    first=execution,
                    candidate=candidate,
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
            dynamic_prefixes = _dynamic_output_prefixes(candidate)
            published_final = _without_dynamic_output_files(
                published_execution.final_state, dynamic_prefixes
            )
            reference_final = _without_dynamic_output_files(
                execution.final_state, dynamic_prefixes
            )
            published_diff = _without_dynamic_output_files(
                published_execution.state_diff, dynamic_prefixes
            )
            reference_diff = _without_dynamic_output_files(
                execution.state_diff, dynamic_prefixes
            )
            publication_mismatch = (
                not published_execution.success
                or published_execution.answer != execution.answer
                or _replay_comparable_trace(published_execution.trace)
                != _replay_comparable_trace(execution.trace)
                or _replay_comparable_state(published_final)
                != _replay_comparable_state(reference_final)
                or _replay_comparable_state(published_diff)
                != _replay_comparable_state(reference_diff)
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
                "workspace_brief": candidate.workspace_brief,
                "task_summary": candidate.task_summary,
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
                TaskFields.WORKSPACE_BRIEF: candidate.workspace_brief,
                TaskFields.TASK_SUMMARY: candidate.task_summary,
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
            accepted_summaries.add(normalized_summary)
            if len(accepted) >= policy.task_count:
                break

        if len(accepted) > accepted_before_round:
            attempts_for_current_task = 0

    write_jsonl(output_path, accepted)
    write_json(tasks_path, formal_tasks)
    write_json(output_dir / "task_catalog.json", {
        "schema_version": "1.0",
        "environment_id": str(package.environment["environment_id"]),
        "workspace_brief": _environment_workspace_brief(package.environment),
        "tasks": [
            {
                "task_id": item["task_id"],
                "task_summary": item[TaskFields.TASK_SUMMARY],
                "task_public": item[TaskFields.TASK_PUBLIC],
                "archetype_id": item[TaskFields.ARCHETYPE_ID],
            }
            for item in accepted
        ],
    })
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
        "task_catalog_path": str(Path(input["run_dir"]) / "task_catalog.json"),
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
