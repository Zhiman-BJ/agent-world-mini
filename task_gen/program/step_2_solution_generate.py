"""Step 2：根据真实任务调研生成任务和 Solution，并固化可重放的标准结果。

生成 Agent 可以读取冻结状态副本来选择真实对象，但任务是否通过完全由 Python 的
统一质检服务决定。同一个生成 Agent 反复提交草稿、读取真实执行/重放/语义审查反馈
并修复；只有统一质检通过后才允许创建 ``candidates.json``。外层只核验质检回执、
固化 Ground Truth 和发布外部 ``task_eval`` 可以直接消费的产物，不再启动修复 Agent。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
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


class _AuthoringInfrastructureFailure(RuntimeError):
    """The author Agent proved that the environment runtime cannot execute tools."""


SEMANTIC_REVIEW_MAX_ATTEMPTS = 3
QUALITY_SERVICE_STOP_TIMEOUT_SECONDS = 30.0


def _terminal_quality_infrastructure_reason(report: Any) -> str | None:
    if not isinstance(report, dict) or report.get("terminal") is not True:
        return None
    stage = str(report.get("stage") or "")
    errors = report.get("errors")
    codes = {
        str(item.get("code") or "")
        for item in errors or []
        if isinstance(item, dict)
    }
    if stage != "infrastructure" and "tool_runtime_unavailable" not in codes:
        return None
    messages = [
        str(item.get("message") or "")
        for item in errors or []
        if isinstance(item, dict) and str(item.get("message") or "").strip()
    ]
    return " | ".join(messages) or "父进程统一质检确认运行基础设施不可用"


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
    tool_names: set[str] | None = None,
) -> None:
    """Give the authoring Agent a safe, disposable view of the real tools.

    The Agent may inspect internal implementations and probe a tool against a
    fresh copy of the frozen state. Probe calls never share state with the
    eventual reference-program execution and are not part of the task output.
    """
    probe_package = working_directory / "probe_package"
    shutil.copytree(package.package_root, probe_package)
    visible_tools = [
        deepcopy(tool)
        for tool in package.tools
        if tool_names is None or str(tool.get("name")) in tool_names
    ]
    write_json(
        working_directory / "tools.internal.json",
        {
            "environment_id": package.environment["environment_id"],
            "tools": visible_tools,
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

    checker_source = '''#!/usr/bin/env python3
\"\"\"Request one complete quality check from the parent Step 2 process.\"\"\"
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, __PROJECT_ROOT__)
from task_gen.program.step_2_solution_generate import _canonical_payload_hash

ROOT = Path(__file__).resolve().parent


def write_atomic(path, payload):
    temporary = path.with_suffix(path.suffix + \".tmp\")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding=\"utf-8\"
    )
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(\"--candidate\", default=\"candidate.draft.json\")
    parser.add_argument(\"--minimum-effective-tool-calls\", type=int)
    parser.add_argument(\"--clean-replays\", type=int, default=2)
    parser.add_argument(\"--execution-timeout-seconds\", type=float, default=300.0)
    parser.add_argument(\"--require-state-change\", action=\"store_true\")
    parser.add_argument(\"--result\", default=\"candidate_check_result.json\")
    parser.add_argument(\"--receipt\", default=\"candidate_preflight_receipt.json\")
    parser.add_argument(\"--history\", default=\"candidate_check_history.jsonl\")
    parser.add_argument(\"--request\", default=\"candidate_check_request.json\")
    parser.add_argument(\"--quality-timeout-seconds\", type=float, default=2400.0)
    args = parser.parse_args()

    candidate_path = (ROOT / args.candidate).resolve()
    result_path = (ROOT / args.result).resolve()
    receipt_path = (ROOT / args.receipt).resolve()
    history_path = (ROOT / args.history).resolve()
    request_path = (ROOT / args.request).resolve()
    try:
        payload = json.loads(candidate_path.read_text(encoding=\"utf-8\"))
        candidate_sha256 = _canonical_payload_hash(payload)
        request_id = f\"{candidate_sha256}:{time.time_ns()}\"
        if result_path.exists():
            result_path.unlink()
        write_atomic(request_path, {
            \"request_id\": request_id,
            \"candidate\": args.candidate,
            \"candidate_sha256\": candidate_sha256,
            \"minimum_effective_tool_calls\": args.minimum_effective_tool_calls,
            \"require_state_change\": args.require_state_change,
            \"clean_replays\": args.clean_replays,
            \"execution_timeout_seconds\": args.execution_timeout_seconds,
        })
        deadline = time.monotonic() + args.quality_timeout_seconds
        report = None
        while time.monotonic() < deadline:
            if result_path.is_file():
                possible = json.loads(result_path.read_text(encoding=\"utf-8\"))
                if (
                    possible.get(\"request_id\") == request_id
                    and possible.get(\"candidate_sha256\") == candidate_sha256
                ):
                    report = possible
                    break
            time.sleep(0.2)
        if report is None:
            raise TimeoutError(\"父进程质检服务未在时限内返回结果\")
    except Exception as error:
        report = {
            \"passed\": False,
            \"stage\": \"checker\",
            \"errors\": [{
                \"code\": \"checker_failure\",
                \"location\": \"$\",
                \"message\": f\"{type(error).__name__}: {error}\",
            }],
            \"metrics\": {},
        }

    with history_path.open(\"a\", encoding=\"utf-8\") as stream:
        stream.write(json.dumps(report, ensure_ascii=False) + \"\\n\")
    if report.get(\"passed\") is True:
        receipt = {
            \"passed\": True,
            \"candidate_sha256\": report[\"candidate_sha256\"],
            \"metrics\": report.get(\"metrics\", {}),
            \"review\": report.get(\"review\", {}),
            \"clean_replay_count\": report.get(\"clean_replay_count\", 0),
        }
        write_atomic(receipt_path, receipt)
    elif receipt_path.exists():
        receipt_path.unlink()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get(\"passed\") is True else 2)


if __name__ == \"__main__\":
    main()
'''.replace("__PROJECT_ROOT__", repr(str(project_root)))
    checker_path = working_directory / "candidate_check.py"
    checker_path.write_text(checker_source, encoding="utf-8")
    checker_path.chmod(0o755)


@dataclass
class ProgramTaskCandidate:
    archetype_id: str
    task_internal: str
    workspace_brief: str
    task_summary: str
    task_public: str
    task_resources: dict[str, Any]
    solution_code: str
    # Accepted only for compatibility with old intermediate candidates. It is
    # deliberately ignored and is never published in the v1.0 task.
    output_schema: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            archetype_id=str(value["archetype_id"]).strip(),
            task_internal=str(value["task_internal"]).strip(),
            workspace_brief=str(value["workspace_brief"]).strip(),
            task_summary=str(value["task_summary"]).strip(),
            task_public=str(value["task_public"]).strip(),
            task_resources=deepcopy(value["task_resources"]),
            solution_code=str(value["solution_code"]).strip(),
            output_schema=deepcopy(value.get("output_schema") or {}),
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


def _execution_error_message(
    error: Exception,
    trace: list[dict[str, Any]],
) -> str:
    """Retain failed-tool diagnostics when Solution code raises assert.

    Generated reference programs commonly assert result success. A bare
    AssertionError otherwise erases the actionable facts that the authoring
    Agent needs to abandon an unavailable tool path.
    """
    failed_tools: list[str] = []
    for record in trace:
        result = record.get("result")
        if not isinstance(result, dict) or result.get("success") is True:
            continue
        detail = result.get("error")
        if not isinstance(detail, dict):
            failed_tools.append(
                f"{record.get('tool') or '<unknown>'}: {detail!r}"
            )
            continue
        code = str(detail.get("code") or "tool_error")
        message = str(detail.get("message") or detail)
        retryable = detail.get("retryable")
        retry_label = (
            f", retryable={str(retryable).lower()}"
            if isinstance(retryable, bool)
            else ""
        )
        failed_tools.append(
            f"{record.get('tool') or '<unknown>'} [{code}{retry_label}]: {message}"
        )
    original = str(error).strip()
    if not failed_tools:
        return original
    diagnosis = "；".join(failed_tools[-3:])
    if original:
        return f"{original}；失败工具详情：{diagnosis}"
    return f"失败工具详情：{diagnosis}"


def _answer_schema_errors(schema: dict[str, Any], answer: Any) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(answer)
    ]


def execute_solution_code(
    package: CompleteEnvironmentPackage,
    source: str,
    _legacy_output_schema: dict[str, Any] | None = None,
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
            if _legacy_output_schema is not None:
                try:
                    answer = json.loads(json.dumps(answer, ensure_ascii=False, allow_nan=False))
                except (TypeError, ValueError) as error:
                    raise ValueError(f"final_answer 不是严格 JSON-native 数据：{error}") from error
                schema_errors = _answer_schema_errors(_legacy_output_schema, answer)
                if schema_errors:
                    raise ValueError("final_answer 不符合旧 output_schema：" + " | ".join(schema_errors))
            else:
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("final_answer 必须是非空的用户可读文本")
                answer = answer.strip()
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
            trace = [record.to_dict() for record in runtime.trace]
            return ProgramExecutionResult(
                False,
                None,
                trace,
                compact_state_snapshot(initial_state),
                compact_state_snapshot(final_state),
                workspace_diff(initial_state, final_state),
                type(error).__name__,
                _execution_error_message(error, trace),
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


def _generation_archetype_rank(
    item: dict[str, Any],
    tool_side_effects: dict[str, bool],
) -> tuple[int, int, int, int, int]:
    """Prefer grounded workflows with broader environment coverage."""
    support = item.get("environment_support", {})
    tools = {str(value) for value in support.get("tools", []) if value}
    stateful_tools = sum(tool_side_effects.get(name, False) for name in tools)
    resources = sum(
        len({str(value) for value in support.get(field, []) if value})
        for field in ("record_sets", "relationships", "filesystem_scopes")
    )
    requirements = len(item.get("requirements", []))
    return (
        int(stateful_tools > 0),
        stateful_tools,
        len(tools),
        resources,
        requirements + len(str(item.get("description") or "")) // 200,
    )


def _rank_generation_archetypes(
    archetypes: dict[str, dict[str, Any]],
    tool_side_effects: dict[str, bool],
) -> list[str]:
    return sorted(
        archetypes,
        key=lambda identifier: (
            _generation_archetype_rank(
                archetypes[identifier], tool_side_effects
            ),
            identifier,
        ),
        reverse=True,
    )


def _authoring_call_target(
    policy: ProgramGenerationPolicy,
    environment_id: str,
    task_index: int,
) -> int | None:
    """Give the author a varied buffer above the acceptance floor."""
    minimum = policy.minimum_effective_tool_calls
    if minimum is None:
        return None
    digest = hashlib.sha256(
        f"{environment_id}:{task_index}".encode("utf-8")
    ).digest()
    return minimum + 2 + digest[0] % 5


def _authoring_research(
    research: dict[str, Any],
    archetype: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(research)
    result["task_archetypes"] = [deepcopy(archetype)]
    return result


def _authoring_public_environment(
    package: CompleteEnvironmentPackage,
    tool_names: set[str],
) -> dict[str, Any]:
    result = deepcopy(package.public_environment())
    tools = result.get("tools")
    if isinstance(tools, list):
        result["tools"] = [
            tool for tool in tools
            if isinstance(tool, dict) and str(tool.get("name")) in tool_names
        ]
    return result


def build_task_solution_prompt(round_index: int, policy: ProgramGenerationPolicy) -> str:
    mutation_rule = (
        "每个任务都必须产生由业务目标要求的状态变化。"
        if policy.require_state_change
        else "任务可以只读，也可以修改状态；由所选现实工作类型决定，不要强行写入。"
    )
    preflight_parts = [
        "python candidate_check.py",
        "--candidate candidate.draft.json",
        f"--clean-replays {policy.clean_replays}",
        f"--execution-timeout-seconds {policy.execution_timeout_seconds}",
        "--quality-timeout-seconds 2400",
    ]
    if policy.minimum_effective_tool_calls is not None:
        preflight_parts.append(
            f"--minimum-effective-tool-calls {policy.minimum_effective_tool_calls}"
        )
    if policy.require_state_change:
        preflight_parts.append("--require-state-change")
    preflight_command = " ".join(preflight_parts)
    # Keep the authoring request focused on the artifact the Agent must create.
    # The one checker command returns all execution, replay and semantic feedback.
    return f"""# 目标：生成真实、可执行且具有自然难度的一条任务及其参考程序

你没有本项目此前的对话上下文；工作目录中的文件是本轮唯一事实来源。

你正在为一个已经冻结的离线环境制作一条训练任务。先在 `candidate.draft.json` 中生成并修正
草稿，只有预检查通过后才创建最终 `candidates.json`。文件中必须只有
`generation_request.json` 中 `candidate_count=1` 指定的一条候选任务；不能生成备用候选或把
多个任务放在同一个文件中。每条候选必须是一个现实工作场景的具体实例，
并同时包含：

1. 给未来执行者看的任务正文 `task_public`；
2. 只供系统执行的隐藏参考程序 `solution_code`；
3. 说明该任务实际涉及哪些数据和工具的 `task_resources`。

不要写解释报告，也不要在预检查通过前创建 `candidates.json`。

## 你可以使用的输入

先阅读下面这些文件，再开始设计任务：

- `task_research.json`：本轮已经根据环境支持范围选定的现实工作原型，文件中只保留这一项；
- `generation_request.json`：其中的 `selected_archetype_id` 是本轮必须使用的原型，
  `effective_tool_call_target` 是隐藏参考程序的内部复杂度目标，二者都不能写进任务正文；
- `environment.public.json`：公开的记录集合、关系、文件区域和工具契约；
- `state/`：当前环境真实初始数据，只读。用它确认真实存在的记录、文件和可用条件；
- `tools.internal.json`：公开工具的实现代码、输入 Schema、输出 Schema 和使用条件；
- `candidate.schema.json`：`candidates.json` 的字段格式；
- `candidate_check.py`：请求父进程对草稿执行结构、资源、真实运行、调用次数、最终回答、
  干净重放和业务语义的统一质检；
- `generation_request.json` 和 `validation_feedback.json`：本轮数量、已接受任务和之前失败原因；
- `references/`：环境、工具和任务契约，需要理解边界时再查阅。

`state/` 和工具内部代码只用于你生成任务时调查。不要把内部数据库路径、主机路径、内部
实现代码或隐藏答案写进 `task_public`。

## 生成顺序

对这一条候选按以下顺序工作：

### 1. 选择一个真实业务目标

使用 `generation_request.json.selected_archetype_id` 指定的现实工作原型，并结合当前初始数据把它写成一个具体实例，不得换成更容易完成的其他原型。先问清楚：专业人员为什么现在要做这件事，完成后会得到什么新的领域结果、决策或可继续使用的产物。任务应围绕这个结果展开，而不是围绕“需要检查哪些东西”展开。

输入核对、来源确认、格式验证和写入后确认可以作为必要的辅助动作，但不能自动成为任务主体。如果环境能够进行实际计算、仿真、候选选择、参数确定、对象修改或产物生成，应让这些工作成为主要目标；只有现实工作本身就是合规审查时，才选择纯核查任务。

这些任务用于训练工具调用能力。任务的深度优先来自结果之间的因果关系。编写 solution 前，先在内部列出完成该现实工作所需的具体调用及每次调用的业务作用，并让去重后的有效调用达到 `generation_request.json.effective_tool_call_target`；这个数字只用于内部设计，不能出现在 `task_public`。可以对不同真实对象、条件或候选分别调用同一工具，也可以根据前序结果继续处理，但每一次调用都必须产生不可替代的信息、产物或决策。不得通过重复相同查询、无关检查或机械拆分增加长度。

所有调用必须直接服务于同一个核心业务结果。不得为了增加调用次数而附加环境依赖核查、运行条件盘点、资源清单、通用文件检查或其他与核心业务结果没有直接因果关系的工作；只有现实交付本身明确需要这些结果时才可以使用。较长的调用链应来自真实工作中的必要对象、必要计算、结果驱动的后续动作或迭代。

在把一个工具的输出交给后续工具前，先根据工具说明核对字段、数据形状、唯一性、排序和取值约束。若上下游不兼容，应调整处理方法或任务设计，不得强行拼接调用。

任务仍然只能有一个主要业务目标和一个 `archetype_id`。可以形成从输入到结果再到后续处理的自然闭环，但不要把几个独立任务硬拼在一起。如果 `validation_feedback.json` 表明上一轮方向真实且工具执行成功，只存在参数来源、答案字段或要求对齐问题，应修正局部问题，不要退化成更简单的核查任务。

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

如果真实探测已经成功启动工具，并返回 success=false 且 error.retryable=false，应把它视为
对象、参数或工具能力的真实失败，不要用 assert 掩盖，也不要围绕该失败路径设计任务。

tool_probe.py 是运行在生成 Agent 自身沙箱里的辅助探测。如果它连工具都没有启动，就因为
bwrap、Operation not permitted、嵌套沙箱或类似宿主权限问题退出，这不能证明冻结环境中的工具
不可用，也不要因此放弃候选。此时继续根据真实状态、公开契约和工具代码编写草稿，并提交给
candidate_check.py；父进程写出的 candidate_check_result.json 才是运行能力的权威结果。只有
该结果返回 tool_runtime_unavailable 且 terminal=true 时，才立即停止当前候选。

探测时不仅要看调用是否成功，还要看每个返回字段的确切含义。不得用名称相似但语义不同的字段
回答任务，例如不能把“扫描首末点变化”当成“末点相对于指定基线的变化”。如果工具没有直接返回
任务要求的量，就必须在 solution 中用返回的原始值明确计算；无法计算时不要把该要求写入任务。

### 3. 编写任务正文 `task_public`

把正文写成现实用户会直接发出的工作请求，而不是执行手册或工具清单。根据具体场景自行决定叙述方式、信息顺序和篇幅，不套用固定段落结构、句式或开场方式。任务应让执行者自然地理解为什么要做这项工作、可以使用哪些具体材料、哪些业务条件会影响结果，以及最终需要完成什么；这些信息不必按固定顺序出现，只要在当前场景中清楚且足够即可。

发布时系统会把 `workspace_brief` 作为一段自然背景接在正文前面，因此 `task_public` 不要机械
重复整段环境介绍。正文只补充当前任务特有、且无法通过公开工具发现的对象、范围、规则、
输入路径和交付要求。

不要出现工具名、Python、`call_tool`、内部字段或调用顺序，也不要要求用户阅读每一个中间检查结果。中间结果只有在它影响最终判断，或本身就是正式交付的一部分时，才写进正文。如果核心工作会产生设计、计算结果、选择决策、修改后的对象或文件，应让这项实际工作成为任务叙述的中心，不要用“审计”“复核”“签核”或“检查”重新包装。

任务必须给足执行所需的业务上下文：具体对象、阈值、范围、规则和确定的输入路径要能从正文直接取得；运行后才知道的动态 ID、路径或业务值应由前序工具结果提供。涉及文件时，正文仍须自然地写明 `scope_id` 和区域内相对路径，使 `task_resources.files` 可被精确验证。确定的交付文件使用完整相对路径和 `path_is_exact=true`；一次列出多个精确文件时，每一个文件都必须在正文中独立写出完整相对路径，不能在首个文件之后只写文件名、使用“同目录下”等省略表达。只有工具确实会动态生成多个文件时，才使用以 `/` 结尾的目录边界和 `path_is_exact=false`。只把 solution 实际会读取、处理或生成的资源列入 `task_resources`。

### 4. 编写隐藏 `solution_code`

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
- 特别不要显式填写空查询、默认排序、分页上限、分页偏移等工具默认控制项。例如任务没有
  要求分页时，不要自行传入 query=""、sort_order="asc"、limit=20、offset=0；直接省略
  这些可选参数。工具 Schema 中存在默认值不等于任务允许你把该值写入调用；
- 先在头脑中逐个核对参数来源，再写出 solution。后续调用需要使用前序结果中的对象时，
  必须通过变量传递，不能重新手写同一个 ID。

如果任务的核心目标包含迭代、优化或候选生成，后续调用必须真正使用前一次工具返回的新参数或新对象。
不得在任务正文中声称“根据结果迭代”，却在 solution 中继续使用原始常量或另一组手写参数。

最终只能用工具结果和任务明确要求的计算构造 `final_answer`。它必须是一段完整、自然、
可直接交付给用户的非空文本，清楚覆盖任务要求的结论、数值、对象和产物路径；不要返回
字典或把 JSON 当作回答。使用 Python 字面量 `True`、`False`、`None`，不要使用 JSON 的
`true`、`false`、`null`；最后一条语句必须是 `final_answer = ...`。

在提交前，用刚才的真实探测结果按 solution 逻辑构造一次完整 `final_answer`，再逐句回看
`task_public`：每一个“检查、核对、比较、计算、返回、保留、生成”要求，都必须能指向
某次实际调用的直接证据、明确计算或 `final_answer` 中的明确表述。只有在句子表面上相似，或只出现在
工具输出但没有进入最终交付，都不算完成。

### 5. 填写资源和内部说明

- `task_resources` 只列这条任务实际用到的最小 `record_sets`、`relationships`、`files` 和
  `allowed_tools`；
- `task_internal` 简要记录业务闭环、关键判断和预期结果，不要复制隐藏答案；
- `workspace_brief` 必须逐字使用 `environment.public.json` 中的环境摘要；
- `task_summary` 用一两句话描述本题业务目标，用于批量去重，不要写工具调用过程。

## 提交前自检与修复循环

逐条检查：

1. 任务目标是否像现实工作，而不是工具操作清单？
2. 任务正文中的每个最终要求是否都在 solution 的最终回答或实际交付状态中得到覆盖？
3. 每个工具参数是否来自任务正文或更早的成功工具结果？
4. 所有对象、文件和工具是否在当前环境中真实存在且已探测可用？
5. 现实工作要求的每个必要环节是否都被保留，且没有加入无关环节？
6. 这条任务是否与 `validation_feedback.json` 中已接受任务的业务目标不同，而不是只替换
   ID、文件名或数字？
7. 如果环境中有同样真实可行、但能自然覆盖更多必要工具交互的实例，当前是否误选了被单一聚合工具
   直接完成的简单实例？同时确认每一次保留的调用都不是为了增加数量而加入。

完成自检后，把当前版本写入 `candidate.draft.json`，然后运行：

```bash
{preflight_command}
```

读取 `candidate_check_result.json`。如果 `passed=false`，必须根据每个
`errors[].code`、`errors[].location` 和 `errors[].message` 定向修改同一份草稿，再次运行检查。
修复时保持当前 `archetype_id` 和主要业务目标，不得通过换成更简单的任务规避错误。整个过程中
只有你这个生成 Agent 可以修改任务和 Solution，不要启动或假设存在另一个修复 Agent。最多允许
初检加 `generation_request.json.max_repair_rounds` 轮修复；如果结果包含 `terminal=true`，说明是
基础设施错误、重复不收敛或达到修复上限，应停止本候选，不要继续猜测式修改。

只有 `passed=true` 且已经生成 `candidate_preflight_receipt.json` 后，才把通过检查的草稿原样复制为
`candidates.json`。复制后不得再修改内容。不要修改其他输入文件或 `state/`。
这是第 {round_index} 轮；请吸收 `validation_feedback.json` 中的失败原因，不要重复同一错误。
"""

def build_solution_repair_prompt(round_index: int) -> str:
    return f"""# 任务：修复一段执行失败的参考程序

## 背景和输入

你没有此前对话上下文。系统正在验证一条离线环境任务的隐藏参考程序，这是第
{round_index} 次修复。

完整读取：

- `repair_request.json`：固定不允许改变的任务正文、资源范围、当前完整程序、
  真实执行错误和已经成功的工具调用记录；
- `environment.public.json`：可用工具及其参数 Schema、返回 Schema、调用前提和副作用；
- `references/环境契约-v2.0.md`、`references/工具契约-v1.0.md`：解释状态和工具契约的
  语义边界。

真实错误来自程序在干净环境副本中的实际执行，不是建议。先确定失败属于 Python 语法、
工具参数、返回字段读取、数据依赖、分支控制或最终答案构造中的哪一类，再修复完整程序。

## 修复边界

- 保持 `task_internal`、`workspace_brief`、`task_summary`、`task_public` 和
  `task_resources` 表达的目标不变；
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
- 修复后最后一条语句仍须给 `final_answer` 赋值，值必须是完整的用户可读文本。

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
必须覆盖每次调用的全部实际参数。字符串、数字、布尔值等标量参数必须逐项列出；如果某个
字典或数组是任务直接给出的完整对象，或完整来自某次更早工具调用，可以只用该对象的父
路径审查整棵结构，例如 `path="/derived_network"` 或 `path="/data"`，不要把矩阵、表格
或报告展开成数百个叶子。父路径必须是工具实际参数中存在的对象，不能用 `/` 一次覆盖
所有参数，也不能用父路径掩盖同级的标量控制参数。调用序号严格从 `0` 开始：第一条调用
是 `0`，第二条是 `1`；`source_call_indices` 也使用同一套从 `0` 开始的序号，并且只能引用
更早调用。`evidence` 用普通话指出任务原句或更早调用的返回字段。

还要判断任务本身是否像现实工作：主要产出应是可使用的领域结果、决策或新产物，而不是一份检查清单；必要的检查应服务于核心目标。若环境和调研原型明显支持设计、计算、仿真、选择、修改或产物生成，但候选退化成通用核查、证据整理或文件质检，应拒绝。`task_public` 应像自然请求，而不是按工具顺序编写的执行说明；solution 应推进到可交付深度，同时不得用重复调用或无关检查制造复杂度。

不要根据某个公开的调用次数数字评价候选，调用深度由后台单独验证。

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


def _published_task_text(candidate: ProgramTaskCandidate) -> str:
    """Combine stable workspace context with the task without a schema-like wrapper."""
    brief = candidate.workspace_brief.strip()
    body = candidate.task_public.strip()
    if not brief or brief.casefold() in body.casefold():
        return body
    return f"{brief.rstrip()}\n\n{body}"


def _reference_answer_text(answer: Any) -> str:
    """Keep the v1.0 reference answer textual, including legacy candidates."""
    if isinstance(answer, str):
        return answer.strip()
    return json.dumps(answer, ensure_ascii=False, sort_keys=True)


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
        unique_paths = set(paths)
        invalid_paths = sorted(
            path
            for path in unique_paths
            if path in {"", "/"}
            or not any(
                leaf == path or leaf.startswith(f"{path}/")
                for leaf in expected_paths
            )
        )
        uncovered_paths = sorted(
            leaf
            for leaf in expected_paths
            if not any(
                leaf == path or leaf.startswith(f"{path}/")
                for path in unique_paths
            )
        )
        overlapping_paths = sorted(
            leaf
            for leaf in expected_paths
            if sum(
                leaf == path or leaf.startswith(f"{path}/")
                for path in unique_paths
            ) > 1
        )
        if (
            len(paths) != len(unique_paths)
            or invalid_paths
            or uncovered_paths
            or overlapping_paths
        ):
            errors.append(
                f"parameter_audit[{call_index}] 必须完整且无重叠地覆盖实际参数；"
                "标量参数逐项列出，结构化对象可用其真实父路径整体覆盖；"
                f"未覆盖 {uncovered_paths}，非法 {invalid_paths}，"
                f"重叠 {overlapping_paths}，实际 {sorted(paths)}"
            )
    return errors


def _compact_failed_review(review: dict[str, Any]) -> dict[str, Any]:
    """Keep failed semantic feedback small while retaining actionable items.

    The complete review remains archived under step2_reviews. The live
    authoring Agent only needs the business reason, issues, and unresolved
    parameter entries; echoing every already-grounded leaf across a long tool
    chain can consume most of the repair context and cause timeouts.
    """
    compact_audit: list[dict[str, Any]] = []
    unresolved_count = 0
    audit = review.get("parameter_audit")
    if isinstance(audit, list):
        for item in audit:
            if not isinstance(item, dict):
                continue
            parameters = item.get("parameters")
            if not isinstance(parameters, list):
                continue
            unresolved = [
                deepcopy(parameter)
                for parameter in parameters
                if isinstance(parameter, dict)
                and (
                    parameter.get("source") == "unresolved"
                    or not str(parameter.get("evidence") or "").strip()
                )
            ]
            if not unresolved:
                continue
            unresolved_count += len(unresolved)
            compact_audit.append({
                "call_index": item.get("call_index"),
                "tool": item.get("tool"),
                "parameters": unresolved,
            })
    reason = str(review.get("reason") or "")
    return {
        "accepted": review.get("accepted") is True,
        "reason": reason[:3000],
        "issues": deepcopy(review.get("issues") or []),
        "parameter_audit": compact_audit,
        "parameter_audit_summary": {
            "reviewed_call_count": len(audit) if isinstance(audit, list) else 0,
            "unresolved_parameter_count": unresolved_count,
            "note": "完整 parameter_audit 已归档在 step2_reviews；这里只返回需要修复的条目",
        },
    }


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
            "task_public": _published_task_text(candidate),
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
    messages = [execution.error or ""]
    unavailable_codes = {
        "backend_unavailable",
        "devsim_unavailable",
        "ghostscript_unavailable",
        "dependency_unavailable",
        "runtime_unavailable",
        "tool_runtime_unavailable",
    }
    for call in execution.trace:
        result = call.get("result") if isinstance(call, dict) else None
        error = result.get("error") if isinstance(result, dict) else None
        if not isinstance(error, dict):
            continue
        code = str(error.get("code") or "").strip().lower()
        if code in unavailable_codes or code.endswith("_runtime_unavailable"):
            return True
        messages.append(str(error.get("message") or ""))
    message = chr(10).join(messages)
    return any(
        marker in message
        for marker in (
            "运行库不可用",
            "backend crashed",
            "runtime unavailable",
            "runtime is unavailable",
            "executable not found",
            "No module named",
            "not installed",
            "工具沙箱异常退出",
            "libGLU.so",
            "libtorch_cpu.so",
            "cannot open shared object file",
            "failed to map segment from shared object",
            "bwrap:",
            "Operation not permitted",
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
        package, code, candidate.output_schema or None,
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
            package, code, candidate.output_schema or None,
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


_REPLAY_MISSING = object()


def _first_replay_difference(
    reference: Any,
    replay: Any,
    *,
    path: str = "$",
) -> tuple[str, Any, Any] | None:
    """Return the first deterministic JSON-style difference between two values."""
    if type(reference) is not type(replay):
        return path, reference, replay
    if isinstance(reference, dict):
        reference_keys = set(reference)
        replay_keys = set(replay)
        for key in sorted(reference_keys - replay_keys, key=str):
            return f"{path}.{key}", reference[key], _REPLAY_MISSING
        for key in sorted(replay_keys - reference_keys, key=str):
            return f"{path}.{key}", _REPLAY_MISSING, replay[key]
        for key in sorted(reference_keys, key=str):
            difference = _first_replay_difference(
                reference[key], replay[key], path=f"{path}.{key}"
            )
            if difference is not None:
                return difference
        return None
    if isinstance(reference, list):
        for index, (reference_item, replay_item) in enumerate(
            zip(reference, replay)
        ):
            difference = _first_replay_difference(
                reference_item, replay_item, path=f"{path}[{index}]"
            )
            if difference is not None:
                return difference
        if len(reference) != len(replay):
            return f"{path}.length", len(reference), len(replay)
        return None
    if reference != replay:
        return path, reference, replay
    return None


def _replay_value_summary(value: Any, *, limit: int = 240) -> str:
    if value is _REPLAY_MISSING:
        return "<missing>"
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) > limit:
        return rendered[: limit - 3] + "..."
    return rendered


def _replay_instability_error(
    *,
    index: int,
    label: str,
    reference: Any,
    replay: Any,
) -> str:
    difference = _first_replay_difference(reference, replay)
    if difference is None:
        return f"clean replay {index} 的 {label} 不稳定"
    path, reference_value, replay_value = difference
    return (
        f"clean replay {index} 的 {label} 不稳定：首个差异路径 {path}；"
        f"首次={_replay_value_summary(reference_value)}；"
        f"重放={_replay_value_summary(replay_value)}"
    )


def _replay_solution(
    *,
    package: CompleteEnvironmentPackage,
    code: str,
    legacy_output_schema: dict[str, Any] | None = None,
    policy: ProgramGenerationPolicy,
    first: ProgramExecutionResult,
    candidate: ProgramTaskCandidate,
) -> tuple[list[ProgramExecutionResult], list[str]]:

    runs = [first]
    while len(runs) < policy.clean_replays:
        runs.append(execute_solution_code(
            package, code, legacy_output_schema,
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
                errors.append(_replay_instability_error(
                    index=index,
                    label="final_answer",
                    reference=reference.answer,
                    replay=run.answer,
                ))
            reference_trace = _replay_comparable_trace(reference.trace)
            run_trace = _replay_comparable_trace(run.trace)
            if run_trace != reference_trace:
                errors.append(_replay_instability_error(
                    index=index,
                    label="工具返回结果",
                    reference=reference_trace,
                    replay=run_trace,
                ))
            run_final = _without_dynamic_output_files(
                run.final_state, dynamic_prefixes
            )
            reference_final = _without_dynamic_output_files(
                reference.final_state, dynamic_prefixes
            )
            if _replay_comparable_state(run_final) != _replay_comparable_state(
                reference_final
            ):
                errors.append(_replay_instability_error(
                    index=index,
                    label="final_state",
                    reference=_replay_comparable_state(reference_final),
                    replay=_replay_comparable_state(run_final),
                ))
            run_diff = _without_dynamic_output_files(
                run.state_diff, dynamic_prefixes
            )
            reference_diff = _without_dynamic_output_files(
                reference.state_diff, dynamic_prefixes
            )
            if _replay_comparable_state(run_diff) != _replay_comparable_state(
                reference_diff
            ):
                errors.append(_replay_instability_error(
                    index=index,
                    label="state_diff",
                    reference=_replay_comparable_state(reference_diff),
                    replay=_replay_comparable_state(run_diff),
                ))
    return runs, errors


def _review_issue_messages(raw_issues: Any) -> list[str] | None:
    """Keep actionable semantic-review feedback even when it is structured."""
    if not isinstance(raw_issues, list):
        return None

    messages: list[str] = []
    for issue in raw_issues:
        if isinstance(issue, str):
            message = issue.strip()
            if not message:
                return None
            messages.append(message)
            continue

        if not isinstance(issue, dict):
            return None
        message = issue.get("message")
        if not isinstance(message, str) or not message.strip():
            return None
        context = [
            f"{key}={issue[key]}"
            for key in ("type", "call_index", "tool", "path")
            if issue.get(key) is not None and issue.get(key) != ""
        ]
        suffix = f" ({', '.join(context)})" if context else ""
        messages.append(f"{message.strip()}{suffix}")
    return messages


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
    audit_dir.mkdir(parents=True)
    used_tool_names = {str(item.get("tool")) for item in execution.trace}
    request = {
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
    }
    previous_errors: list[str] = []
    review_names = {"accepted", "reason", "parameter_audit", "issues"}

    for attempt_index in range(1, SEMANTIC_REVIEW_MAX_ATTEMPTS + 1):
        with tempfile.TemporaryDirectory(
            prefix="agent-world-program-review-"
        ) as temporary:
            attempt_dir = Path(temporary)
            write_json(attempt_dir / "review_request.json", request)
            prompt = build_task_solution_review_prompt()
            if previous_errors:
                prompt += (
                    "\n\n## 上一次审阅输出无效，必须重新审阅\n\n"
                    "上一次不是候选任务未通过，而是 `review.json` 自身不符合交付约定。"
                    "请重新阅读全部真实证据并覆盖写入完整文件。具体问题：\n- "
                    + "\n- ".join(previous_errors)
                    + "\n\n如果拒绝候选，`issues` 必须逐项写出可执行的具体问题；"
                    "不得返回空拒绝或无法读取的问题对象。"
                )
            (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

            try:
                _run_agent_until_json(
                    agent,
                    prompt,
                    working_directory=attempt_dir,
                    filename="review.json",
                )
                review = read_json(attempt_dir / "review.json")
                attempt_errors: list[str] = []
            except Exception as error:
                review = {}
                attempt_errors = [
                    f"语义审查执行失败：{type(error).__name__}: {error}"
                ]

            if not attempt_errors and (
                not isinstance(review, dict)
                or set(review) != review_names
                or type(review.get("accepted")) is not bool
                or not isinstance(review.get("reason"), str)
                or len(review["reason"].strip()) < 20
                or not isinstance(review.get("issues"), list)
            ):
                attempt_errors = ["review.json 不符合固定审查结构"]

            issues: list[str] | None = None
            if not attempt_errors:
                issues = _review_issue_messages(review["issues"])
                if issues is None:
                    attempt_errors = [
                        "review.json 的 issues 必须包含可读的问题说明"
                    ]
                elif review["accepted"] is False and not issues:
                    attempt_errors = [
                        "review.json 拒绝候选时必须在 issues 中给出具体问题"
                    ]

            attempt_audit = audit_dir / f"attempt_{attempt_index:02d}"
            shutil.copytree(attempt_dir, attempt_audit)
            for filename in ("review_request.json", "prompt.txt", "review.json"):
                source = attempt_dir / filename
                if source.is_file():
                    shutil.copy2(source, audit_dir / filename)

        if attempt_errors:
            previous_errors = attempt_errors
            continue

        assert issues is not None
        review["issues"] = issues
        parameter_errors = _parameter_audit_errors(review, execution.trace)
        unresolved_parameter_errors = [
            error
            for error in parameter_errors
            if "参数来源无法确定" in error
        ]
        if (
            review["accepted"] is True
            and not review["issues"]
            and parameter_errors
            and not unresolved_parameter_errors
        ):
            previous_errors = [
                "review.json 的 parameter_audit 自身不符合审阅契约，"
                "请重新审阅；不要要求候选修改任务来适配错误的审计表",
                *parameter_errors,
            ]
            continue
        if (
            review["accepted"] is not True
            or review["issues"]
            or parameter_errors
        ):
            return review, [*issues, *parameter_errors] or ["独立审查未通过"]
        return review, []

    return {}, [
        f"语义审查连续 {SEMANTIC_REVIEW_MAX_ATTEMPTS} 次未产生有效 review.json："
        + "；".join(previous_errors)
    ]


def _canonical_payload_hash(payload: Any) -> str:
    """Hash a candidate payload independently of JSON whitespace/order."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preflight_error(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


@dataclass
class _PreflightEvidence:
    """Execution evidence retained for later semantic review and replay."""

    candidate: ProgramTaskCandidate | None = None
    execution: ProgramExecutionResult | None = None


def _preflight_candidate_payload(
    *,
    package: CompleteEnvironmentPackage,
    payload: Any,
    research: dict[str, Any],
    schema: dict[str, Any],
    minimum_effective_tool_calls: int | None,
    require_state_change: bool,
    clean_replays: int,
    execution_timeout_seconds: float,
    run_clean_replays: bool = True,
    evidence: _PreflightEvidence | None = None,
) -> dict[str, Any]:
    """Run deterministic checks inside the authoring Agent's own session.

    This deliberately excludes the independent semantic-review Agent. Passing
    preflight means the draft is mechanically executable, not publishable.
    """
    metrics: dict[str, Any] = {}
    errors = [
        _preflight_error("candidate_schema", "$", message)
        for message in _schema_errors(payload, schema)
    ]
    if errors:
        return {"passed": False, "stage": "schema", "errors": errors, "metrics": metrics}
    candidates = payload.get("candidates", [])
    if len(candidates) != 1:
        return {
            "passed": False,
            "stage": "schema",
            "errors": [_preflight_error(
                "candidate_count", "$.candidates",
                "预检查每次只接受一条候选任务",
            )],
            "metrics": metrics,
        }

    archetypes = {
        str(item["archetype_id"]): item
        for item in research.get("task_archetypes", [])
        if isinstance(item, dict)
        and item.get("environment_support", {}).get("generatable") is True
    }
    candidate = ProgramTaskCandidate.from_dict(candidates[0])
    if evidence is not None:
        evidence.candidate = candidate
    errors = [
        _preflight_error("candidate_contract", "$.candidates[0]", message)
        for message in _candidate_errors(package, candidate, archetypes)
    ]
    if errors:
        return {
            "passed": False,
            "stage": "candidate_contract",
            "errors": errors,
            "metrics": metrics,
        }

    execution = execute_solution_code(
        package,
        candidate.solution_code,
        candidate.output_schema or None,
        timeout_seconds=execution_timeout_seconds,
    )
    if evidence is not None:
        evidence.execution = execution
    metrics.update({
        "tool_calls": len(execution.trace),
        "effective_tool_calls": _effective_tool_call_count(execution.trace),
        "distinct_tools": sorted({str(item.get("tool")) for item in execution.trace}),
        "state_changed": _state_changed(execution),
    })
    if not execution.success:
        unavailable = _is_infrastructure_execution_failure(execution)
        execution_errors = [_preflight_error(
            "solution_execution",
            "$.candidates[0].solution_code",
            f"{execution.error_type}: {execution.error}",
        )]
        if unavailable:
            execution_errors.append(_preflight_error(
                "tool_runtime_unavailable",
                "$.candidates[0].solution_code",
                "必要工具在当前运行环境中不可用，修改 Python 逻辑无法修复。"
                "不要重试该调用或用 assert 掩盖；停止当前候选，下一次生成改用"
                "已经真实探测成功的工具路径。",
            ))
        return {
            "passed": False,
            "stage": "execution",
            "errors": execution_errors,
            "metrics": metrics,
            "trace_tail": execution.trace[-3:],
            "terminal": unavailable,
        }

    errors = []
    if minimum_effective_tool_calls is not None:
        if len(execution.trace) < minimum_effective_tool_calls:
            errors.append(_preflight_error(
                "raw_tool_calls_below_minimum",
                "$.candidates[0].solution_code",
                f"实际调用 {len(execution.trace)} 次，至少需要 {minimum_effective_tool_calls} 次",
            ))
        if metrics["effective_tool_calls"] < minimum_effective_tool_calls:
            errors.append(_preflight_error(
                "effective_tool_calls_below_minimum",
                "$.candidates[0].solution_code",
                "去重后的有效调用不足；相同工具和相同参数的重复只读调用不计数",
            ))
    used_tools = set(metrics["distinct_tools"])
    declared_tools = set(candidate.task_resources["allowed_tools"])
    undeclared = sorted(used_tools - declared_tools)
    unused = sorted(declared_tools - used_tools)
    if undeclared:
        errors.append(_preflight_error(
            "undeclared_tools", "$.candidates[0].task_resources.allowed_tools",
            "solution 使用了未声明工具：" + ", ".join(undeclared),
        ))
    if unused:
        errors.append(_preflight_error(
            "unused_declared_tools", "$.candidates[0].task_resources.allowed_tools",
            "allowed_tools 含未实际使用工具：" + ", ".join(unused),
        ))
    if require_state_change and not metrics["state_changed"]:
        errors.append(_preflight_error(
            "state_change_required", "$.candidates[0].solution_code",
            "当前策略要求状态变化，但 solution 没有修改环境",
        ))
    errors.extend(
        _preflight_error("dynamic_output_missing", "$.candidates[0].task_resources.files", message)
        for message in _dynamic_output_errors(candidate, execution)
    )
    if errors:
        return {
            "passed": False,
            "stage": "runtime_contract",
            "errors": errors,
            "metrics": metrics,
        }

    if run_clean_replays and clean_replays > 1:
        replay_policy = ProgramGenerationPolicy(
            clean_replays=clean_replays,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        _, replay_errors = _replay_solution(
            package=package,
            code=candidate.solution_code,
            legacy_output_schema=candidate.output_schema or None,
            policy=replay_policy,
            first=execution,
            candidate=candidate,
        )
        if replay_errors:
            return {
                "passed": False,
                "stage": "replay",
                "errors": [
                    _preflight_error("clean_replay", "$.candidates[0].solution_code", message)
                    for message in replay_errors
                ],
                "metrics": metrics,
            }

    return {"passed": True, "stage": "passed", "errors": [], "metrics": metrics}


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Publish a checker response without exposing a partially written JSON file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, payload)
    temporary.replace(path)


def _quality_error_signature(report: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(item.get("code") or ""),
            str(item.get("location") or ""),
            str(item.get("message") or ""),
        )
        for item in report.get("errors", [])
        if isinstance(item, dict)
    )


def _quality_report_is_infrastructure_failure(report: dict[str, Any]) -> bool:
    markers = (
        "backend_unavailable",
        "dependency_unavailable",
        "runtime_unavailable",
        "tool_runtime_unavailable",
        "运行库不可用",
        "backend crashed",
        "runtime unavailable",
        "runtime is unavailable",
        "executable not found",
        "No module named",
        "not installed",
        "工具沙箱异常退出",
        "libGLU.so",
        "libtorch_cpu.so",
        "cannot open shared object file",
        "failed to map segment from shared object",
        "bwrap:",
        "Operation not permitted",
    )
    return any(
        any(marker in str(item.get("message") or "") for marker in markers)
        for item in report.get("errors", [])
        if isinstance(item, dict)
    )


def _run_candidate_quality_request(
    *,
    authoring: Path,
    package: CompleteEnvironmentPackage,
    research: dict[str, Any],
    schema: dict[str, Any],
    review_agent: TaskSolutionReviewAgent | None,
    request: dict[str, Any],
    review_dir: Path,
) -> dict[str, Any]:
    """Run all publish checks and return feedback to the still-running author Agent."""
    request_id = str(request.get("request_id") or "")
    expected_hash = str(request.get("candidate_sha256") or "")
    candidate_name = str(request.get("candidate") or "candidate.draft.json")
    candidate_path = (authoring / candidate_name).resolve()
    if candidate_path.parent != authoring.resolve():
        raise ValueError("candidate 必须位于生成工作目录根目录")
    payload = read_json(candidate_path)
    actual_hash = _canonical_payload_hash(payload)
    if not request_id or actual_hash != expected_hash:
        raise ValueError("质检请求与当前 candidate 草稿哈希不一致")

    clean_replays = int(request.get("clean_replays") or 2)
    execution_timeout = float(request.get("execution_timeout_seconds") or 300.0)
    evidence = _PreflightEvidence()
    report = _preflight_candidate_payload(
        package=package,
        payload=payload,
        research=research,
        schema=schema,
        minimum_effective_tool_calls=request.get("minimum_effective_tool_calls"),
        require_state_change=bool(request.get("require_state_change")),
        clean_replays=clean_replays,
        execution_timeout_seconds=execution_timeout,
        run_clean_replays=False,
        evidence=evidence,
    )
    report = {
        "request_id": request_id,
        "candidate_sha256": actual_hash,
        **report,
    }
    if report.get("passed") is not True:
        if _quality_report_is_infrastructure_failure(report):
            report["terminal"] = True
            report["stage"] = "infrastructure"
        return report

    candidate = evidence.candidate
    execution = evidence.execution
    if candidate is None or execution is None or not execution.success:
        raise RuntimeError("预检查通过但没有保留可用的执行证据")

    review, review_errors = _review_task_solution(
        agent=review_agent,
        candidate=candidate,
        execution=execution,
        public_tools=package.public_environment()["tools"],
        review_dir=review_dir,
    )
    if review_errors:
        return {
            "request_id": request_id,
            "candidate_sha256": actual_hash,
            "passed": False,
            "stage": "semantic_review",
            "errors": [
                _preflight_error(
                    "semantic_review",
                    "$.candidates[0]",
                    message,
                )
                for message in review_errors
            ],
            "metrics": report.get("metrics", {}),
            "review": _compact_failed_review(review),
        }

    if clean_replays > 1:
        replay_policy = ProgramGenerationPolicy(
            clean_replays=clean_replays,
            execution_timeout_seconds=execution_timeout,
        )
        _, replay_errors = _replay_solution(
            package=package,
            code=candidate.solution_code,
            legacy_output_schema=candidate.output_schema or None,
            policy=replay_policy,
            first=execution,
            candidate=candidate,
        )
        if replay_errors:
            return {
                "request_id": request_id,
                "candidate_sha256": actual_hash,
                "passed": False,
                "stage": "replay",
                "errors": [
                    _preflight_error(
                        "clean_replay",
                        "$.candidates[0].solution_code",
                        message,
                    )
                    for message in replay_errors
                ],
                "metrics": report.get("metrics", {}),
                "review": review,
            }
    report["review"] = review
    report["clean_replay_count"] = clean_replays
    return report


class _CandidateQualityService:
    """Serve checker requests while one authoring Codex session stays alive."""

    def __init__(
        self,
        *,
        authoring: Path,
        package: CompleteEnvironmentPackage,
        research: dict[str, Any],
        schema: dict[str, Any],
        review_agent: TaskSolutionReviewAgent | None,
        max_repair_rounds: int,
    ) -> None:
        self.authoring = authoring
        self.package = package
        self.research = research
        self.schema = schema
        self.review_agent = review_agent
        self.max_checks = max_repair_rounds + 1
        self.request_path = authoring / "candidate_check_request.json"
        self.result_path = authoring / "candidate_check_result.json"
        self.review_root = authoring / "candidate_reviews"
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.failure: BaseException | None = None
        self.check_count = 0
        self.last_request_id: str | None = None
        self.last_failure_signature: tuple[tuple[str, str, str], ...] | None = None
        self.last_failure_candidate_hash: str | None = None
        self.repeated_failure_count = 0

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=QUALITY_SERVICE_STOP_TIMEOUT_SECONDS)
        if self.thread.is_alive():
            raise TimeoutError(
                "候选质检服务在停止请求后仍未结束；"
                "生成 Agent 已无法继续接收本轮反馈，终止当前候选"
            )
        if self.failure is not None:
            raise RuntimeError(f"候选质检服务失败：{type(self.failure).__name__}: {self.failure}")

    def _serve(self) -> None:
        try:
            while not self.stop_event.is_set():
                if not self.request_path.is_file():
                    self.stop_event.wait(0.1)
                    continue
                try:
                    request = read_json(self.request_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    self.stop_event.wait(0.1)
                    continue
                request_id = str(request.get("request_id") or "")
                if not request_id or request_id == self.last_request_id:
                    self.stop_event.wait(0.1)
                    continue
                self.last_request_id = request_id
                self.check_count += 1
                if self.check_count > self.max_checks:
                    report = {
                        "request_id": request_id,
                        "candidate_sha256": request.get("candidate_sha256"),
                        "passed": False,
                        "terminal": True,
                        "stage": "repair_limit",
                        "errors": [_preflight_error(
                            "repair_limit_reached",
                            "$.candidates[0]",
                            f"已经完成 {self.max_checks} 次统一质检，达到修复上限",
                        )],
                        "metrics": {},
                    }
                else:
                    try:
                        report = _run_candidate_quality_request(
                            authoring=self.authoring,
                            package=self.package,
                            research=self.research,
                            schema=self.schema,
                            review_agent=self.review_agent,
                            request=request,
                            review_dir=(
                                self.review_root / f"check_{self.check_count:02d}"
                            ),
                        )
                    except Exception as error:
                        report = {
                            "request_id": request_id,
                            "candidate_sha256": request.get("candidate_sha256"),
                            "passed": False,
                            "stage": "checker",
                            "errors": [_preflight_error(
                                "checker_failure",
                                "$",
                                f"{type(error).__name__}: {error}",
                            )],
                            "metrics": {},
                        }
                signature = _quality_error_signature(report)
                candidate_hash = str(report.get("candidate_sha256") or "")
                if report.get("passed") is True:
                    self.last_failure_signature = None
                    self.last_failure_candidate_hash = None
                    self.repeated_failure_count = 0
                else:
                    same_signature = bool(
                        signature and signature == self.last_failure_signature
                    )
                    same_candidate = bool(
                        candidate_hash
                        and candidate_hash == self.last_failure_candidate_hash
                    )
                    self.repeated_failure_count = (
                        self.repeated_failure_count + 1 if same_signature else 1
                    )
                    should_stop = (
                        report.get("terminal") is not True
                        and same_signature
                        and (same_candidate or self.repeated_failure_count >= 3)
                    )
                    if should_stop:
                        report["terminal"] = True
                        detail = (
                            "候选内容未改变且连续返回相同错误"
                            if same_candidate
                            else "候选虽有修改，但连续三次返回相同错误"
                        )
                        report["errors"] = [
                            *report.get("errors", []),
                            _preflight_error(
                                "non_converging_repair",
                                "$.candidates[0]",
                                detail + "，停止无效修复",
                            ),
                        ]
                    self.last_failure_signature = signature
                    self.last_failure_candidate_hash = candidate_hash
                _write_json_atomic(self.result_path, report)
        except BaseException as error:
            self.failure = error


def _archive_authoring_quality_artifacts(
    *,
    authoring: Path,
    output_dir: Path,
    round_index: int,
    review_root: Path,
) -> None:
    """Keep checker feedback even when the Agent never publishes candidates.json."""
    receipt_path = authoring / "candidate_preflight_receipt.json"
    result_path = authoring / "candidate_check_result.json"
    history_path = authoring / "candidate_check_history.jsonl"
    write_json(
        output_dir / "step2_preflight" / f"round_{round_index:02d}.json",
        {
            "receipt": read_json(receipt_path) if receipt_path.is_file() else None,
            "last_result": read_json(result_path) if result_path.is_file() else None,
            "history": read_records(history_path) if history_path.is_file() else [],
        },
    )
    if review_root.is_dir():
        review_audit = (
            output_dir / "step2_reviews" / f"round_{round_index:02d}_authoring"
        )
        if review_audit.exists():
            shutil.rmtree(review_audit)
        review_audit.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(review_root, review_audit)


def _authoring_failure_reasons(error: Exception, authoring: Path) -> list[str]:
    """Preserve the last checker diagnosis for the next outer retry."""
    reasons = [f"生成 Agent 失败：{type(error).__name__}: {error}"]
    result_path = authoring / "candidate_check_result.json"
    if not result_path.is_file():
        return reasons
    try:
        report = read_json(result_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return reasons
    stage = str(report.get("stage") or "unknown")
    errors = report.get("errors")
    if not isinstance(errors, list):
        return reasons
    actionable = []
    for item in errors[:12]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "quality_error")
        location = str(item.get("location") or "$")
        message = str(item.get("message") or "")
        actionable.append(f"[{stage}/{code}] {location}: {message}")
    if actionable:
        reasons.append("最后一次统一质检反馈：" + " | ".join(actionable))
    return reasons



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
    """让一个生成 Agent 自检修复任务，并固化稳定 Ground Truth。"""
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
    tool_side_effects = {
        str(tool.get("name")): bool(
            tool.get("usageConditions", {}).get("sideEffects")
        )
        for tool in package.tools
        if isinstance(tool, dict) and tool.get("name")
    }
    ranked_archetype_ids = _rank_generation_archetypes(
        archetypes, tool_side_effects
    )
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
    infrastructure_failure: list[str] | None = None
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
        archetype_offset = (
            len(accepted) + (attempts_for_current_task - 1) // 3
        ) % len(ranked_archetype_ids)
        selected_archetype_id = ranked_archetype_ids[archetype_offset]
        selected_archetype = archetypes[selected_archetype_id]
        selected_tool_names = {
            str(value)
            for value in selected_archetype.get("environment_support", {}).get("tools", [])
            if value
        }
        authoring_call_target = _authoring_call_target(
            policy,
            str(package.environment["environment_id"]),
            len(accepted),
        )
        receipt: dict[str, Any] = {}
        if candidates_path is not None:
            payload = read_json(candidates_path.resolve())
        else:
            if generation_agent is None:
                raise ValueError("没有提供 TaskSolutionAgent")
            with tempfile.TemporaryDirectory(prefix="agent-world-program-step2-") as temporary:
                authoring = Path(temporary)
                shutil.copytree(package.state_root, authoring / "state")
                _write_generation_tool_access(
                    authoring, package, selected_tool_names
                )
                before = snapshot_state(
                    authoring / "state", package.environment,
                    package_format=package.package_format,
                )
                write_json(
                    authoring / "environment.public.json",
                    _authoring_public_environment(package, selected_tool_names),
                )
                write_json(
                    authoring / "task_research.json",
                    _authoring_research(research, selected_archetype),
                )
                write_json(authoring / "candidate.schema.json", schema)
                copy_schema_docs(authoring, STEP2_SCHEMA_DOCS)
                write_json(authoring / "generation_request.json", {
                    **{key: value for key, value in policy.to_dict().items() if key != "minimum_effective_tool_calls"},
                    "workspace_brief": _environment_workspace_brief(package.environment),
                    "selected_archetype_id": selected_archetype_id,
                    "effective_tool_call_target": authoring_call_target,
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
                quality_service = _CandidateQualityService(
                    authoring=authoring,
                    package=package,
                    research=_authoring_research(research, selected_archetype),
                    schema=schema,
                    review_agent=review_agent,
                    max_repair_rounds=policy.max_repair_rounds,
                )
                try:
                    quality_service.start()
                    try:
                        _run_agent_until_json(
                            generation_agent,
                            prompt,
                            working_directory=authoring,
                            filename="candidates.json",
                        )
                    finally:
                        try:
                            quality_service.stop()
                        finally:
                            _archive_authoring_quality_artifacts(
                                authoring=authoring,
                                output_dir=output_dir,
                                round_index=round_index,
                                review_root=quality_service.review_root,
                            )
                    candidates_output = authoring / "candidates.json"
                    if not candidates_output.is_file():
                        quality_result_path = (
                            authoring / "candidate_check_result.json"
                        )
                        quality_result = (
                            read_json(quality_result_path)
                            if quality_result_path.is_file()
                            else None
                        )
                        infrastructure_reason = (
                            _terminal_quality_infrastructure_reason(
                                quality_result
                            )
                        )
                        if infrastructure_reason is not None:
                            raise _AuthoringInfrastructureFailure(
                                infrastructure_reason
                            )
                    payload = read_json(candidates_output)
                    receipt_path = authoring / "candidate_preflight_receipt.json"
                    if not receipt_path.is_file():
                        raise ValueError(
                            "生成 Agent 未在提交 candidates.json 前通过 candidate_check.py"
                        )
                    receipt = read_json(receipt_path)
                    expected_hash = _canonical_payload_hash(payload)
                    if (
                        receipt.get("passed") is not True
                        or receipt.get("candidate_sha256") != expected_hash
                    ):
                        raise ValueError(
                            "candidates.json 与通过预检查的草稿不一致，请重新检查后原样提交"
                        )
                    write_json(
                        output_dir / "step2_candidates" / f"round_{round_index:02d}.json",
                        payload,
                    )
                except Exception as error:
                    terminal_infrastructure = isinstance(
                        error, _AuthoringInfrastructureFailure
                    )
                    reasons = _authoring_failure_reasons(error, authoring)
                    rejections.append({
                        "round": round_index,
                        "candidate": None,
                        "stage": (
                            "infrastructure"
                            if terminal_infrastructure
                            else "authoring"
                        ),
                        "terminal": terminal_infrastructure,
                        "reasons": reasons,
                    })
                    if terminal_infrastructure:
                        infrastructure_failure = reasons
                        break
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
            if candidates_path is None:
                code = candidate.solution_code
                execution = execute_solution_code(
                    package,
                    code,
                    candidate.output_schema or None,
                    timeout_seconds=policy.execution_timeout_seconds,
                )
                debug_history: list[dict[str, Any]] = []
            else:
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
            minimum_effective_tool_calls = policy.minimum_effective_tool_calls
            if execution.success and minimum_effective_tool_calls is not None:
                raw_call_count = len(execution.trace)
                if raw_call_count < minimum_effective_tool_calls:
                    errors.append(
                        "参考程序实际工具调用数不足长链门槛："
                        f"{raw_call_count} < {minimum_effective_tool_calls}；"
                        "必须扩展同一现实工作中不可替代的业务交互，不能重复查询凑数"
                    )
                if effective_tool_calls < minimum_effective_tool_calls:
                    errors.append(
                        "参考程序去重后的有效工具调用数不足长链门槛："
                        f"{effective_tool_calls} < {minimum_effective_tool_calls}；"
                        "相同工具和相同参数的重复只读调用不计入有效调用"
                    )
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
            if not errors and candidates_path is not None:
                replays, replay_errors = _replay_solution(
                    package=package,
                    code=code,
                    legacy_output_schema=candidate.output_schema or None,
                    policy=policy,
                    first=execution,
                    candidate=candidate,
                )
                errors.extend(replay_errors)
            review: dict[str, Any] = deepcopy(receipt.get("review", {}))
            if not errors and candidates_path is not None:
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
                candidate.output_schema or None,
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
                "task_text": _published_task_text(candidate),
                "difficulty": {
                    "tool_calls": len(public_calls),
                },
                "initial_state": f"tasks/{task_id}/initial",
                "available_tools": package.public_environment()["tools"],
                "reference": {
                    "tool_calls": public_calls,
                    "answer": _reference_answer_text(published_execution.answer),
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
                TaskFields.TASK_PUBLIC: _published_task_text(candidate),
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
                    "clean_replay_count": (
                        len(replays)
                        if candidates_path is not None
                        else int(receipt.get("clean_replay_count") or 0)
                    ),
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
    validation_status = (
        "passed"
        if len(accepted) == policy.task_count
        else "blocked_infrastructure"
        if infrastructure_failure is not None
        else "failed"
    )
    write_json(validation_path, {
        "status": validation_status,
        "requested": policy.task_count,
        "accepted": len(accepted),
        "rejected": len(rejections),
        "infrastructure_failure": infrastructure_failure,
        "rejections": rejections,
    })
    if len(accepted) != policy.task_count:
        if infrastructure_failure is not None:
            raise RuntimeError(
                "Step 2 因运行基础设施不可用而停止："
                + " | ".join(infrastructure_failure)
            )
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
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--task-count", type=int, default=1)
    parser.add_argument("--generation-attempts", type=int, default=8)
    parser.add_argument("--max-repair-rounds", type=int, default=6)
    parser.add_argument("--minimum-effective-tool-calls", type=int, default=18)
    parser.add_argument("--execution-timeout-seconds", type=float, default=1000.0)
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
            execution_timeout_seconds=arguments.execution_timeout_seconds,
            minimum_effective_tool_calls=(
                arguments.minimum_effective_tool_calls
                if arguments.minimum_effective_tool_calls > 0
                else None
            ),
        ),
        generation_agent=agent,
        review_agent=agent,
        candidates_path=arguments.candidates,
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
