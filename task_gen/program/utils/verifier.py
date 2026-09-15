"""Verifier 代码的受限执行与确定性正负例测试工具。"""

from __future__ import annotations

import ast
import json
import sys
import time
from copy import deepcopy
from typing import Any


_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}
_FORBIDDEN = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Global,
    ast.Nonlocal,
)


class _VerifierBudget:
    def __init__(self, timeout_seconds: float, max_lines: int):
        self.deadline = time.monotonic() + timeout_seconds
        self.max_lines = max_lines
        self.lines = 0

    def trace(self, _frame: Any, event: str, _arg: Any) -> Any:
        if event == "line":
            self.lines += 1
            if self.lines > self.max_lines:
                raise TimeoutError("Verifier 超过最大执行行数")
            if time.monotonic() > self.deadline:
                raise TimeoutError("Verifier 执行超时")
        return self.trace


def _execute_restricted_function(
    *,
    code: str,
    function_name: str,
    arguments: tuple[dict[str, Any], ...],
    signature: str,
    label: str,
    timeout_seconds: float = 1.0,
    max_executed_lines: int = 100_000,
) -> float:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN):
            raise ValueError(f"{label} 禁止使用 {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"{label} 禁止访问双下划线名称")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"{label} 禁止访问内部属性")
    namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    copied_arguments = tuple(deepcopy(argument) for argument in arguments)
    budget = _VerifierBudget(timeout_seconds, max_executed_lines)
    previous_trace = sys.gettrace()
    try:
        sys.settrace(budget.trace)
        exec(compile(tree, f"<{function_name}>", "exec"), namespace, namespace)
        function = namespace.get(function_name)
        if not callable(function):
            raise ValueError(f"{label} 必须定义 {signature}")
        value = function(*copied_arguments)
    finally:
        sys.settrace(previous_trace)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 返回值必须是 0..1 的数字")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} 返回值必须位于 0..1")
    return score


def execute_verifier_code(
    code: str,
    candidate_answer: dict[str, Any],
    ground_truth_answer: dict[str, Any],
) -> float:
    return _execute_restricted_function(
        code=code,
        function_name="verify",
        arguments=(candidate_answer, ground_truth_answer),
        signature="verify(candidate_answer, ground_truth_answer)",
        label="Verifier",
    )


def execute_state_verifier_code(
    code: str,
    candidate_state: dict[str, Any],
    ground_truth_state: dict[str, Any],
    initial_state: dict[str, Any],
) -> float:
    """在与答案 Verifier 相同的沙箱中执行三参数状态验证器。"""
    return _execute_restricted_function(
        code=code,
        function_name="verify_state",
        arguments=(candidate_state, ground_truth_state, initial_state),
        signature=(
            "verify_state(candidate_state, ground_truth_state, initial_state)"
        ),
        label="状态 Verifier",
    )


def build_deterministic_verifier(ground_truth_answer: dict[str, Any]) -> str:
    weights = {
        field: 3 if isinstance(value, (dict, list)) else 2
        for field, value in ground_truth_answer.items()
    }
    return f'''def verify(candidate_answer, ground_truth_answer):
    if not isinstance(candidate_answer, dict) or not isinstance(ground_truth_answer, dict):
        return 0.0
    if set(candidate_answer) != set(ground_truth_answer):
        return 0.0
    weights = {json.dumps(weights, ensure_ascii=False, sort_keys=True)}
    earned = 0
    total = sum(weights.values())
    for field, weight in weights.items():
        candidate = candidate_answer[field]
        expected = ground_truth_answer[field]
        if type(candidate) is type(expected) and candidate == expected:
            earned += weight
    return earned / total if total else 0.0
'''.strip()


def build_deterministic_state_verifier() -> str:
    return '''def verify_state(candidate_state, ground_truth_state, initial_state):
    if not isinstance(candidate_state, dict) or not isinstance(ground_truth_state, dict):
        return 0.0
    return 1.0 if candidate_state == ground_truth_state else 0.0
'''.strip()


def verifier_smoke_test(
    code: str,
    ground_truth_answer: dict[str, Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        positive = execute_verifier_code(code, ground_truth_answer, ground_truth_answer)
        if positive != 1.0:
            errors.append(f"标准答案正例得分不是 1.0：{positive}")
    except Exception as error:
        return False, [f"Verifier 执行异常：{type(error).__name__}: {error}"]

    extra = deepcopy(ground_truth_answer)
    extra["__unexpected__"] = True
    if execute_verifier_code(code, extra, ground_truth_answer) == 1.0:
        errors.append("增加 Schema 外字段后仍得到满分")
    for field, expected in ground_truth_answer.items():
        missing = deepcopy(ground_truth_answer)
        del missing[field]
        if execute_verifier_code(code, missing, ground_truth_answer) == 1.0:
            errors.append(f"删除字段 {field} 后仍得到满分")
        wrong = deepcopy(ground_truth_answer)
        wrong[field] = _different_value(expected)
        if execute_verifier_code(code, wrong, ground_truth_answer) == 1.0:
            errors.append(f"修改字段 {field} 后仍得到满分")
        wrong_type = deepcopy(ground_truth_answer)
        wrong_type[field] = _different_type(expected)
        if execute_verifier_code(code, wrong_type, ground_truth_answer) == 1.0:
            errors.append(f"改变字段 {field} 类型后仍得到满分")
    return not errors, errors


def state_verifier_smoke_test(
    code: str,
    initial_state: dict[str, Any],
    ground_truth_state: dict[str, Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        positive = execute_state_verifier_code(
            code, ground_truth_state, ground_truth_state, initial_state
        )
        if positive != 1.0:
            errors.append(f"Ground Truth 状态正例得分不是 1.0：{positive}")
        wrong = _different_value(ground_truth_state)
        if execute_state_verifier_code(
            code, wrong, ground_truth_state, initial_state
        ) == 1.0:
            errors.append("修改 Ground Truth 状态后仍得到满分")
        if initial_state != ground_truth_state and execute_state_verifier_code(
            code, initial_state, ground_truth_state, initial_state
        ) == 1.0:
            errors.append("状态变化任务未执行时仍得到满分")
    except Exception as error:
        return False, [f"状态 Verifier 执行异常：{type(error).__name__}: {error}"]
    return not errors, errors


def _different_value(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "__wrong__"
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, list):
        return value + [None]
    if isinstance(value, dict):
        return value | {"__wrong__": True}
    return "__wrong__"


def _different_type(value: Any) -> Any:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return {"items": value}
    if isinstance(value, dict):
        return list(value.items())
    return str(value)
