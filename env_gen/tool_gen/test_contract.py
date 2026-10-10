"""Validation and preservation of executable ToolGen acceptance tests."""

from __future__ import annotations

import math
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.io import write_json


TEST_FIELDS = {
    "calls", "expect_success", "expect_changed", "expected_data", "expected_records",
    "expected_error", "expected_error_code", "data_checks", "expected_files",
    "description", "reference",
}
ASSERTION_FIELDS = TEST_FIELDS - {"calls", "description", "reference"}
TEST_GUIDANCE = """验收测试使用 calls=[{tool,arguments}]、expect_success、expect_changed、expected_data、expected_records 和 expected_error；expected_error 可核对 code/path/retryable 等字段。
数值容差或范围使用 data_checks=[{path:"/结果字段",op:"close",value:参考数值,atol:绝对容差,rtol:相对容差}] 或 {path:"/结果字段",op:"range",min:下界,max:上界}，path 是从 data 开始的 JSON Pointer。普通 expected_data 保持精确的已声明字段比较。
文件内容使用 expected_files=[{scope_id:真实Scope,path:Scope内相对路径,expected_json:需要核对的JSON内容}] 或 contains_text:非空文本；文件必须实际存在并能读取。写操作用 expected_records 核对关键修改。
每个动作在 action_plan 的 verification_tests 中提前写至少一个实际正常案例，包含 reference 说明原始记录、官方案例或独立计算的具体依据，并验证体现动作用途的结果；数值动作不能只检查编号或算法名。此列表由执行器独立加入验收，草稿 tests 可追加其他情况。
verification_baselines.json 是执行器保存的验收基线；保持原调用参数与断言，需要修改原验收标准时记录依据并交人工审查。"""


def normalize_tests(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("tests_must_be_array_of_objects")
    tests = deepcopy(value)
    if tests and all("tool" in item and "calls" not in item for item in tests):
        for item in tests[:-1]:
            if ASSERTION_FIELDS & item.keys():
                raise ValueError("flat_intermediate_assertion_requires_explicit_test")
        for item in tests:
            unknown = item.keys() - (TEST_FIELDS - {"calls"} | {"tool", "arguments"})
            if unknown:
                raise ValueError("unsupported_test_fields:" + ",".join(sorted(unknown)))
        final = tests[-1]
        tests = [{
            **{key: val for key, val in final.items() if key not in {"tool", "arguments"}},
            "calls": [{"tool": item["tool"], "arguments": item.get("arguments", {})} for item in tests],
        }]
    for test in tests:
        if "expected_error_code" in test:
            error = test.setdefault("expected_error", {})
            if not isinstance(error, dict) or error.get("code", test["expected_error_code"]) != test["expected_error_code"]:
                raise ValueError("conflicting_expected_error_code")
            error["code"] = test.pop("expected_error_code")
    return tests


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def pointer(value: Any, path: str) -> Any:
    """Read an RFC 6901 JSON pointer, including list indices."""
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("data_check_path_must_be_json_pointer")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
                raise ValueError("invalid_array_index")
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise ValueError("data_check_path_not_found")
    return value


def validate_tests(tests: Any) -> list[str]:
    if not isinstance(tests, list) or any(not isinstance(t, dict) for t in tests):
        return ["tests_must_be_array_of_objects"]
    errors = []
    for index, test in enumerate(tests):
        reasons = []
        unknown = test.keys() - TEST_FIELDS
        if unknown:
            reasons.append("unsupported_test_fields:" + ",".join(sorted(unknown)))
        for field in ("expect_success", "expect_changed"):
            if field in test and type(test[field]) is not bool:
                reasons.append(field + "_must_be_boolean")
        calls = test.get("calls")
        if not isinstance(calls, list) or not calls:
            reasons.append("calls_must_be_nonempty_array")
        else:
            for call in calls:
                if (not isinstance(call, dict) or set(call) - {"tool", "arguments"}
                        or not isinstance(call.get("tool"), str) or not call["tool"]
                        or not isinstance(call.get("arguments", {}), dict)):
                    reasons.append("invalid_call")
        for field in ("expected_data", "expected_error"):
            nullable_data = field == "expected_data" and test.get(field) is None
            if field in test and not nullable_data and not isinstance(test[field], dict):
                reasons.append(field + "_must_be_object")
        error = test.get("expected_error", {})
        if isinstance(error, dict) and error:
            if (set(error) - {"code", "path", "message", "retryable"}
                    or any(not isinstance(v, str) for k, v in error.items() if k != "retryable")
                    or ("retryable" in error and type(error["retryable"]) is not bool)):
                reasons.append("invalid_expected_error")
            if test.get("expect_success", True):
                reasons.append("expected_error_requires_failure_test")
        records = test.get("expected_records", [])
        if not isinstance(records, list) or any(
            not isinstance(r, dict) or set(r) != {"record_set_id", "key", "values"}
            or not isinstance(r["record_set_id"], str) or not r["record_set_id"]
            or not isinstance(r["key"], dict) or not r["key"]
            or not isinstance(r["values"], dict) or not r["values"] for r in records
        ):
            reasons.append("invalid_expected_records")
        checks = test.get("data_checks", [])
        if not isinstance(checks, list):
            reasons.append("data_checks_must_be_array")
        else:
            for check in checks:
                if not isinstance(check, dict):
                    reasons.append("invalid_data_check")
                    continue
                op = check.get("op")
                fields = {"path", "op", "value", "atol", "rtol"} if op == "close" else {"path", "op", "min", "max"}
                path = check.get("path")
                valid = isinstance(path, str) and (path == "" or path.startswith("/"))
                valid = valid and not set(check) - fields
                if op == "close":
                    valid = valid and _number(check.get("value"))
                    valid = valid and all(_number(check.get(k, 0)) and check.get(k, 0) >= 0 for k in ("atol", "rtol"))
                elif op == "range":
                    valid = valid and bool({"min", "max"} & check.keys())
                    valid = valid and all(_number(check[k]) for k in ("min", "max") if k in check)
                    valid = valid and check.get("min", -math.inf) <= check.get("max", math.inf)
                else:
                    valid = False
                if not valid:
                    reasons.append("invalid_data_check")
        files = test.get("expected_files", [])
        if not isinstance(files, list) or any(
            not isinstance(f, dict) or set(f) - {"scope_id", "path", "expected_json", "contains_text"}
            or not isinstance(f.get("scope_id"), str) or not f["scope_id"]
            or not isinstance(f.get("path"), str) or not f["path"]
            or not ({"expected_json", "contains_text"} & f.keys())
            or ("contains_text" in f and (not isinstance(f["contains_text"], str) or not f["contains_text"]))
            or ("expected_json" in f and not f["expected_json"]) for f in files
        ):
            reasons.append("invalid_expected_files")
        for field in ("reference", "description"):
            if field in test and (not isinstance(test[field], str) or not test[field].strip()):
                reasons.append(field + "_must_be_nonempty_string")
        errors.extend(f"test_{index}:{reason}" for reason in reasons)
    return errors


def success_gate(tests: list[dict[str, Any]]) -> list[str]:
    if not tests:
        return ["missing_executable_test"]
    positive = [t for t in tests if t.get("expect_success", True)]
    if not positive:
        return ["missing_successful_test"]
    if not any(
        isinstance(t.get("expected_data"), dict) and t["expected_data"]
        or isinstance(t.get("expected_records"), list) and t["expected_records"]
        or isinstance(t.get("data_checks"), list) and t["data_checks"]
        or isinstance(t.get("expected_files"), list) and t["expected_files"] for t in positive
    ):
        return ["missing_success_result_assertion"]
    return []


def baseline_changes(baseline: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    """Allow appended cases; preserve every original case and its multiplicity."""
    remaining = deepcopy(current)
    for test in baseline:
        expected = {k: v for k, v in test.items() if k in ASSERTION_FIELDS | {"calls"}}
        for index, candidate in enumerate(remaining):
            actual = {k: v for k, v in candidate.items() if k in ASSERTION_FIELDS | {"calls"}}
            # Explicit defaults and omission have identical semantics.
            for item in (expected, actual):
                item.setdefault("expect_success", True)
                item.setdefault("expect_changed", False)
            if actual == expected:
                remaining.pop(index)
                break
        else:
            return True
    return False


def check_numbers(data: Any, checks: list[dict[str, Any]]) -> list[str]:
    failures = []
    for check in checks:
        try:
            actual = pointer(data, check["path"])
            valid = _number(actual)
            if check["op"] == "close":
                valid = valid and math.isclose(actual, check["value"], abs_tol=check.get("atol", 0), rel_tol=check.get("rtol", 0))
            else:
                valid = valid and check.get("min", -math.inf) <= actual <= check.get("max", math.inf)
            if not valid:
                failures.append(f"data_check_failed:{check['path']}:actual={actual!r}:expected={check!r}")
        except (KeyError, IndexError, TypeError, ValueError):
            failures.append(f"data_check_path_not_found:{check['path']}")
    return failures


class TestBaselines:
    """Capture acceptance cases once and reuse them across repair and resume."""

    __test__ = False

    def __init__(self, directory: Path) -> None:
        self.path = directory / "verification_baselines.json"
        self.entries = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        if not isinstance(self.entries, dict):
            raise ValueError("invalid_verification_baselines")
        for entry in self.entries.values():
            if not isinstance(entry, dict):
                raise ValueError("invalid_verification_baseline_entry")
            for key in ("tests", "verification_tests"):
                tests = entry.get(key, [])
                if validate_tests(tests) or (tests and success_gate(tests)):
                    raise ValueError("invalid_verification_baseline_tests")

    def capture(self, name: str, *, tests: Any = None, verification_tests: Any = None) -> None:
        entry = self.entries.setdefault(name, {})
        for key, value in (("tests", tests), ("verification_tests", verification_tests)):
            if key in entry or value is None:
                continue
            try:
                normalized = normalize_tests(value)
            except ValueError:
                continue
            if validate_tests(normalized) or success_gate(normalized):
                continue
            entry[key] = normalized
            write_json(self.path, self.entries)

    def bind(self, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for draft in drafts:
            name = str(draft["tool"]["name"])
            self.capture(name, tests=draft["tests"], verification_tests=draft["action"].get("verification_tests"))
            draft["test_baseline"] = deepcopy(self.entries.get(name, {}))
        return drafts
