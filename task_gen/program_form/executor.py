from __future__ import annotations

import ast
import json
import sys
import time
import traceback
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from .loader import CompleteEnvironmentPackage
from .runtime import CompleteEnvironmentRuntime, workspace_diff


FORBIDDEN_NODES = (
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
FORBIDDEN_CALLS = {
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
SAFE_BUILTINS = {
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


def validate_reference_program(source: str) -> list[str]:
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
        if not any(isinstance(target, ast.Name) and target.id == "final_answer" for target in targets):
            errors.append("最后一条语句必须给 final_answer 赋值")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            errors.append(f"第 {getattr(node, 'lineno', '?')} 行禁止使用 {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"第 {node.lineno} 行禁止访问双下划线名称 {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(f"第 {node.lineno} 行禁止访问内部属性 {node.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
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


def execute_reference_program(
    package: CompleteEnvironmentPackage,
    source: str,
    output_schema: dict[str, Any],
    *,
    timeout_seconds: float = 15.0,
    max_executed_lines: int = 1_000_000,
) -> ProgramExecutionResult:
    source_errors = validate_reference_program(source)
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
            "__builtins__": SAFE_BUILTINS,
            "call_tool": call_tool,
        }
        budget = _ExecutionBudget(timeout_seconds, max_executed_lines)
        previous_trace = sys.gettrace()
        try:
            compiled = compile(source, "<reference-program>", "exec")
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
                raise ValueError(f"final_answer 不符合 output_schema：{' | '.join(schema_errors)}")
            failed_calls = [record for record in runtime.trace if record.result.get("success") is not True]
            if failed_calls:
                names = ", ".join(record.tool for record in failed_calls)
                raise ValueError(f"参考程序包含业务失败的工具调用：{names}")
            final_state = runtime.snapshot()
            return ProgramExecutionResult(
                True,
                answer,
                [record.to_dict() for record in runtime.trace],
                initial_state,
                final_state,
                workspace_diff(initial_state, final_state),
            )
        except Exception as error:
            final_state = runtime.snapshot()
            return ProgramExecutionResult(
                False,
                None,
                [record.to_dict() for record in runtime.trace],
                initial_state,
                final_state,
                workspace_diff(initial_state, final_state),
                type(error).__name__,
                str(error),
                traceback.format_exc(limit=12),
            )
        finally:
            sys.settrace(previous_trace)
