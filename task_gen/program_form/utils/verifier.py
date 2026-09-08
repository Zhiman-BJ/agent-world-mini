"""Verifier 代码的受限执行与确定性正负例测试工具。"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from typing import Any


_SAFE_BUILTINS = {
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
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


def execute_verifier_code(
    code: str,
    candidate_answer: dict[str, Any],
    ground_truth_answer: dict[str, Any],
) -> float:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN):
            raise ValueError(f"Verifier 禁止使用 {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("Verifier 禁止访问双下划线名称")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Verifier 禁止访问内部属性")
    namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(tree, "<verifier>", "exec"), namespace, namespace)
    verify = namespace.get("verify")
    if not callable(verify):
        raise ValueError("Verifier 必须定义 verify(candidate_answer, ground_truth_answer)")
    value = verify(deepcopy(candidate_answer), deepcopy(ground_truth_answer))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Verifier 返回值必须是 0..1 的数字")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("Verifier 返回值必须位于 0..1")
    return score


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
