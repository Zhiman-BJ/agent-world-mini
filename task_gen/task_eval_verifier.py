"""Generate and run frozen, task-specific execution verifiers."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable

from .tool_graph.llm import BatchInferenceError, InferenceResult, infer, parse_json_object
from .tool_graph.step_3_chain_execute import _bounded_calls, _call_tool, _workspace_signature


_STATUSES = {"pass", "fail", "indeterminate"}
_CHANNELS = {"answer", "tool_trace", "workspace"}
_EVIDENCE_PROVENANCE = {"task", "environment_contract", "reference_observation"}
_EVIDENCE_USES = {"criterion", "locator", "example"}
_OUTCOME_TYPES = {
    "persistent_state", "query", "computation", "presentation", "preservation",
    "execution_integrity",
}
_CONTEXT_METHODS = {
    "answer", "calls", "changed_paths", "files", "file", "read_text", "read_json",
    "call_tool", "verifier_calls",
    "candidate_group",
    "pass_requirement", "fail_requirement", "indeterminate_requirement", "semantic_requirement",
}
_BANNED_CALLS = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "getattr", "globals", "input",
    "locals", "open", "setattr", "type", "vars", "__import__",
}
_BANNED_NODES = (
    ast.AsyncFunctionDef, ast.Await, ast.ClassDef, ast.Global, ast.Import,
    ast.ImportFrom, ast.Nonlocal, ast.With, ast.AsyncWith,
)
InferFn = Callable[..., InferenceResult | list[InferenceResult]]


class VerifierPreparationError(ValueError):
    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        super().__init__(json.dumps({"message": "verifier 生成校准失败", "attempts": attempts}, ensure_ascii=False))


class ReferenceCalibrationError(ValueError):
    """The frozen requirements are not proven by the known-good reference evidence."""


class TaskReferenceConflictError(VerifierPreparationError):
    """The task authority and reference evidence are independently confirmed incompatible."""

    def __init__(self, attempts: list[dict[str, Any]], assessment: dict[str, Any]):
        self.attempts = attempts
        self.assessment = assessment
        ValueError.__init__(self, json.dumps({
            "message": "task_reference_conflict",
            "assessment": assessment,
            "attempts": attempts,
        }, ensure_ascii=False))


def _workspace_changes(
    before: tuple[tuple[str, int, str], ...],
    after: tuple[tuple[str, int, str], ...],
) -> list[dict[str, str]]:
    old = {path: (mode, digest) for path, mode, digest in before}
    new = {path: (mode, digest) for path, mode, digest in after}
    return [
        {"path": path, "change": "added" if path not in old else "deleted" if path not in new else "modified"}
        for path in sorted(old.keys() | new.keys())
        if old.get(path) != new.get(path)
    ]


def _files(root: Path, content_budget: int, priority_paths: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    readable: list[tuple[int, bytes, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"workspace 不得包含符号链接：{path}")
        if not path.is_file():
            continue
        raw = path.read_bytes()
        record: dict[str, Any] = {
            "path": str(path.relative_to(root)),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            readable.append((len(records), raw, text))
        records.append(record)

    remaining = max(0, content_budget)
    for index, raw, text in sorted(
        readable,
        key=lambda item: (
            records[item[0]]["path"] not in priority_paths,
            not records[item[0]]["path"].lower().endswith(".json"),
            len(item[1]),
        ),
    ):
        if remaining:
            encoded = text.encode("utf-8")
            kept = encoded[:remaining].decode("utf-8", errors="ignore")
            record = records[index]
            record["text"] = kept
            record["truncated"] = len(kept.encode("utf-8")) < len(encoded)
            remaining -= len(kept.encode("utf-8"))
            if not record["truncated"]:
                try:
                    record["json"] = json.loads(text)
                except json.JSONDecodeError:
                    pass
    return records


def build_evidence(
    initial: Path,
    final: Path,
    calls: list[dict[str, Any]],
    answer: str,
    result_limit: int,
) -> dict[str, Any]:
    """Build a bounded, path-free evidence package for one execution."""
    initial = initial.resolve()
    final = final.resolve()
    if not initial.is_dir() or not final.is_dir():
        raise ValueError("initial 和 final 必须是存在的目录")
    if result_limit < 1:
        raise ValueError("result_limit 必须大于 0")
    initial_signature = _workspace_signature(initial)
    final_signature = _workspace_signature(final)
    changes = _workspace_changes(initial_signature, final_signature)
    changed_paths = {item["path"] for item in changes}
    return {
        "answer": answer,
        "calls": _bounded_calls(calls, result_limit),
        "changed_paths": changes,
        "initial_files": _files(initial, result_limit, changed_paths),
        "final_files": _files(final, result_limit, changed_paths),
    }


def validate_verifier(package: dict[str, Any]) -> None:
    """Validate the generated package and reject unsafe Python syntax."""
    if not isinstance(package, dict) or set(package) != {"schema_version", "requirements", "source"}:
        keys = sorted(package) if isinstance(package, dict) else type(package).__name__
        raise ValueError(f"verifier 必须只包含 schema_version、requirements、source；实际字段：{keys}")
    if package["schema_version"] != "1":
        raise ValueError("verifier.schema_version 必须为 1")
    requirements = package["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("verifier.requirements 必须是非空数组")
    ids: set[str] = set()
    required_keys = {"id", "claim", "required", "evidence_channels", "pass_condition", "fail_condition"}
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != required_keys:
            keys = sorted(requirement) if isinstance(requirement, dict) else type(requirement).__name__
            raise ValueError(f"requirement 必须包含 {sorted(required_keys)}；实际字段：{keys}")
        requirement_id = requirement["id"]
        if not isinstance(requirement_id, str) or not requirement_id or requirement_id in ids:
            raise ValueError("requirement.id 必须是唯一的非空字符串")
        ids.add(requirement_id)
        if not isinstance(requirement["claim"], str) or not requirement["claim"].strip():
            raise ValueError("requirement.claim 必须是非空字符串")
        if not isinstance(requirement["required"], bool):
            raise ValueError("requirement.required 必须是 boolean")
        channels = requirement["evidence_channels"]
        aliases = {"files": "workspace", "tool_calls": "tool_trace", "final_answer": "answer"}
        if isinstance(channels, list):
            requirement["evidence_channels"] = list(dict.fromkeys(aliases.get(channel, channel) for channel in channels))
            channels = requirement["evidence_channels"]
        if not isinstance(channels, list) or any(channel not in _CHANNELS for channel in channels):
            raise ValueError("requirement.evidence_channels 非法")
        if any(not isinstance(requirement[key], str) or not requirement[key].strip()
               for key in ("pass_condition", "fail_condition")):
            raise ValueError("requirement 的通过和失败条件必须是非空字符串")

    source = package["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("verifier.source 必须是非空字符串")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"verifier.source 语法错误：{error}") from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("verifier.source 只能定义 verify(ctx)")
    function = tree.body[0]
    if function.name != "verify" or function.decorator_list or len(function.args.args) != 1:
        raise ValueError("verifier.source 必须定义 verify(ctx)")
    if function.args.args[0].arg != "ctx" or function.args.vararg or function.args.kwarg:
        raise ValueError("verify 只能接收 ctx")
    for node in ast.walk(tree):
        if isinstance(node, _BANNED_NODES):
            raise ValueError(f"verifier.source 不允许 {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("verifier.source 不允许访问私有名称")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("verifier.source 不允许访问私有属性")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctx":
            if node.attr not in _CONTEXT_METHODS:
                raise ValueError(f"未知 VerifierContext 属性：{node.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED_CALLS:
            raise ValueError(f"verifier.source 不允许调用 {node.func.id}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "ctx"
                and node.func.attr not in _CONTEXT_METHODS):
            raise ValueError(f"未知 VerifierContext 方法：{node.func.attr}")


_RUNTIME = r'''
import contextlib
from copy import deepcopy
import html
import io
import json
import math
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

class VerifierContext:
    def __init__(self, arguments):
        self._evidence = arguments["evidence"]
        self._tools = {tool["name"]: tool for tool in arguments.get("tools", [])}
        self._max_tool_calls = arguments.get("max_tool_calls", 50)
        self._verifier_calls = []
        self._results = []
        self._candidate_groups = []

    def answer(self):
        return self._evidence.get("answer", "")

    def calls(self):
        return self._evidence.get("calls", [])

    def changed_paths(self):
        return self._evidence.get("changed_paths", [])

    def files(self, state="final"):
        return self._evidence.get(state + "_files", [])

    def file(self, path, state="final"):
        for item in self.files(state):
            if item.get("path") == path:
                return item
        return None

    def read_text(self, path, state="final"):
        target = self._state_path(path, state)
        if target is not None and target.is_file():
            try:
                return target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        item = self.file(path, state)
        value = None if item is None else item.get("text")
        if value is None and state == "final":
            initial = self.file(path, "initial")
            if initial is not None and item is not None and initial.get("sha256") == item.get("sha256"):
                value = initial.get("text")
        return value

    def read_json(self, path, state="final"):
        text = self.read_text(path, state)
        if text is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None
        item = self.file(path, state)
        value = None if item is None else item.get("json")
        if value is None and state == "final":
            initial = self.file(path, "initial")
            if initial is not None and item is not None and initial.get("sha256") == item.get("sha256"):
                value = initial.get("json")
        return value

    def _state_path(self, path, state):
        if state not in ("initial", "final") or not isinstance(path, str):
            return None
        relative = Path(path)
        if relative.is_absolute():
            return None
        root = (Path("/workspace") / state).resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target

    def call_tool(self, name, arguments):
        if len(self._verifier_calls) >= self._max_tool_calls:
            raise ValueError("verifier 工具调用次数已达到上限")
        tool = self._tools.get(name)
        if tool is None or not isinstance(arguments, dict):
            raise ValueError("未知工具或工具参数不是 object")
        index = len(self._verifier_calls)
        workspace = Path("/workspace/.verifier_calls") / str(index)
        shutil.copytree(Path("/workspace/final"), workspace)
        namespace = {"json": json}
        result = None
        error = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exec(tool["internal"]["code"], namespace)
                runner = namespace.get("run")
                if not callable(runner):
                    raise ValueError("工具没有定义 run(arguments, context)")
                result = runner(deepcopy(arguments), SimpleNamespace(workspace_root=workspace))
        except BaseException as exception:
            error = f"{type(exception).__name__}: {exception}"
        record = {
            "tool": name,
            "arguments": deepcopy(arguments),
            "result": result,
            "error": error,
            "evidence_ref": f"verifier_call:{index}",
        }
        self._verifier_calls.append(record)
        return deepcopy(record)

    def verifier_calls(self):
        return deepcopy(self._verifier_calls)

    def _record(self, requirement_id, status, reason, evidence_refs):
        self._results.append({
            "requirement_id": requirement_id,
            "status": status,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
        })

    def pass_requirement(self, requirement_id, reason, evidence_refs):
        self._record(requirement_id, "pass", reason, evidence_refs)

    def fail_requirement(self, requirement_id, reason, evidence_refs):
        self._record(requirement_id, "fail", reason, evidence_refs)

    def indeterminate_requirement(self, requirement_id, reason, evidence_refs=()):
        self._record(requirement_id, "indeterminate", reason, evidence_refs)

    def semantic_requirement(self, requirement_id, claim, evidence_refs):
        self._record(requirement_id, "semantic", claim, evidence_refs)

    def candidate_group(self, group_id, requirement_ids, complete, reason, evidence_refs, rows):
        self._candidate_groups.append({
            "group_id": group_id,
            "requirement_ids": list(requirement_ids),
            "complete": complete,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
            "rows": rows,
        })
'''


def _runner_source(source: str) -> str:
    return _RUNTIME + "\n" + source + r'''

def run(arguments, context):
    ctx = VerifierContext(arguments)
    verify(ctx)
    return {"results": ctx._results, "candidate_groups": ctx._candidate_groups, "verifier_calls": ctx._verifier_calls}
'''


def _valid_evidence_ref(reference: str, evidence: dict[str, Any]) -> bool:
    if reference == "answer":
        return True
    if reference.startswith("tool_call:"):
        try:
            index = int(reference.split(":", 1)[1])
        except ValueError:
            return False
        return 0 <= index < len(evidence.get("calls", []))
    if reference.startswith("verifier_call:"):
        try:
            index = int(reference.split(":", 1)[1])
        except ValueError:
            return False
        return 0 <= index < len(evidence.get("verifier_calls", []))
    for prefix, key in (("initial:", "initial_files"), ("final:", "final_files")):
        if reference.startswith(prefix):
            path = reference[len(prefix):]
            return any(item.get("path") == path for item in evidence.get(key, []))
    if reference.startswith("workspace_change:"):
        path = reference[len("workspace_change:"):]
        return any(item.get("path") == path for item in evidence.get("changed_paths", []))
    return False


def _select_witness_group(group: dict[str, Any]) -> list[dict[str, Any]]:
    """Return results from one assignment that satisfies an existential group."""
    rows = group["rows"]
    for row in rows:
        if all(result["status"] == "pass" for result in row["results"]):
            return row["results"]
    if not group["complete"]:
        return [{
            "requirement_id": requirement_id,
            "status": "indeterminate",
            "reason": group["reason"],
            "evidence_refs": group["evidence_refs"],
        } for requirement_id in group["requirement_ids"]]
    if not rows:
        return [{
            "requirement_id": requirement_id,
            "status": "fail",
            "reason": group["reason"],
            "evidence_refs": group["evidence_refs"],
        } for requirement_id in group["requirement_ids"]]
    return min(
        rows,
        key=lambda row: (
            sum(result["status"] == "fail" for result in row["results"]),
            sum(result["status"] == "indeterminate" for result in row["results"]),
        ),
    )["results"]


def _validate_runtime_result(
    result: Any,
    allowed_ids: set[str],
    evidence: dict[str, Any],
) -> str:
    if not isinstance(result, dict) or set(result) != {
        "requirement_id", "status", "reason", "evidence_refs",
    }:
        raise ValueError("verifier requirement 结果结构非法")
    requirement_id = result["requirement_id"]
    if requirement_id not in allowed_ids:
        raise ValueError("verifier 返回了未知的 requirement_id")
    if result["status"] not in _STATUSES | {"semantic"}:
        raise ValueError("verifier 返回了非法状态")
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        raise ValueError("verifier reason 必须是非空字符串")
    if not isinstance(result["evidence_refs"], list) or any(
        not isinstance(ref, str) or not _valid_evidence_ref(ref, evidence)
        for ref in result["evidence_refs"]
    ):
        raise ValueError("verifier 返回了无效 evidence_refs")
    result["evidence_refs"] = list(dict.fromkeys(result["evidence_refs"]))
    return requirement_id


def run_verifier(
    package: dict[str, Any],
    evidence: dict[str, Any],
    *,
    timeout: int = 30,
    memory_limit: int = 512 * 1024 * 1024,
    semantic_infer_fn: InferFn | None = None,
    llm_config: dict[str, Any] | None = None,
    task_text: str | None = None,
    initial_state: Path | None = None,
    final_state: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tool_calls: int = 50,
) -> list[dict[str, Any]]:
    """Execute generated verifier code in the existing bubblewrap sandbox."""
    validate_verifier(package)
    with tempfile.TemporaryDirectory(prefix="task-verifier-") as temporary:
        runtime = Path(temporary) / "runtime"
        runtime.mkdir()
        for name, state in (("initial", initial_state), ("final", final_state)):
            target = runtime / name
            if state is None:
                target.mkdir()
            else:
                shutil.copytree(state.resolve(), target)
        outcome = _call_tool(
            _runner_source(package["source"]),
            {"evidence": evidence, "tools": tools or [], "max_tool_calls": max_tool_calls},
            runtime,
            timeout,
            memory_limit,
            256 * 1024 * 1024,
        )
    if outcome.get("kind") is not None:
        raise ValueError(f"verifier 执行失败：{outcome.get('error')}")
    payload = outcome.get("result")
    if (not isinstance(payload, dict)
            or set(payload) not in ({"results", "verifier_calls"}, {
                "results", "candidate_groups", "verifier_calls",
            })
            or not isinstance(payload["results"], list)
            or not isinstance(payload["verifier_calls"], list)):
        raise ValueError("verifier 返回结构非法")
    evidence["verifier_calls"] = payload["verifier_calls"]
    requirement_order = [item["id"] for item in package["requirements"]]
    requirements = set(requirement_order)
    seen: set[str] = set()
    for result in payload["results"]:
        requirement_id = _validate_runtime_result(result, requirements, evidence)
        if requirement_id in seen:
            raise ValueError("verifier 返回了重复的 requirement_id")
        seen.add(requirement_id)

    candidate_groups = payload.get("candidate_groups", [])
    if not isinstance(candidate_groups, list):
        raise ValueError("verifier candidate_groups 必须是数组")
    all_results = list(payload["results"])
    for group in candidate_groups:
        if not isinstance(group, dict) or set(group) != {
            "group_id", "requirement_ids", "complete", "reason", "evidence_refs", "rows",
        }:
            raise ValueError("verifier candidate group 结构非法")
        group_ids = group["requirement_ids"]
        if (not isinstance(group["group_id"], str) or not group["group_id"]
                or not isinstance(group_ids, list) or not group_ids
                or any(not isinstance(item, str) for item in group_ids)
                or len(set(group_ids)) != len(group_ids)
                or not set(group_ids) <= requirements
                or set(group_ids) & seen):
            raise ValueError("verifier candidate group requirement_ids 非法")
        if not isinstance(group["complete"], bool):
            raise ValueError("verifier candidate group complete 必须是 boolean")
        if not isinstance(group["reason"], str) or not group["reason"].strip():
            raise ValueError("verifier candidate group reason 必须非空")
        if not isinstance(group["evidence_refs"], list) or any(
            not isinstance(ref, str) or not _valid_evidence_ref(ref, evidence)
            for ref in group["evidence_refs"]
        ):
            raise ValueError("verifier candidate group evidence_refs 非法")
        if not isinstance(group["rows"], list):
            raise ValueError("verifier candidate group rows 必须是数组")
        for row in group["rows"]:
            if (not isinstance(row, dict) or set(row) != {"assignment_id", "results"}
                    or not isinstance(row["assignment_id"], str)
                    or not isinstance(row["results"], list)):
                raise ValueError("verifier candidate row 结构非法")
            row_ids = [_validate_runtime_result(item, set(group_ids), evidence) for item in row["results"]]
            if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(group_ids):
                raise ValueError("verifier candidate row 必须恰好覆盖分组 requirements")
            all_results.extend(row["results"])
        seen.update(group_ids)
    if seen != requirements:
        raise ValueError("verifier 未返回每一项 requirement 的结果")

    semantic_results = [item for item in all_results if item["status"] == "semantic"]
    if semantic_results and semantic_infer_fn is None:
        raise ValueError("verifier 包含 semantic_requirement，但未提供语义评审器")
    if semantic_infer_fn is not None:
        requirements_by_id = {item["id"]: item for item in package["requirements"]}
        prompts = [
            _semantic_prompt(item, evidence, requirements_by_id[item["requirement_id"]], task_text)
            for item in semantic_results
        ]
        responses = semantic_infer_fn(
            prompts if len(prompts) > 1 else prompts[0],
            llm_config=llm_config or {},
        ) if prompts else []
        if isinstance(responses, InferenceResult):
            responses = [responses]
        if not isinstance(responses, list) or len(responses) != len(semantic_results):
            raise ValueError("语义评审返回数量与请求不一致")
        for item, response in zip(semantic_results, responses):
            semantic = _parse_semantic_result(response.text)
            if set(semantic) != {"status", "reason"} or semantic["status"] not in _STATUSES:
                raise ValueError("语义评审结果结构非法")
            if not isinstance(semantic["reason"], str) or not semantic["reason"].strip():
                raise ValueError("语义评审 reason 必须是非空字符串")
            item["status"] = semantic["status"]
            item["reason"] = semantic["reason"]
    results = list(payload["results"])
    for group in candidate_groups:
        results.extend(_select_witness_group(group))
    by_id = {item["requirement_id"]: item for item in results}
    return [by_id[requirement_id] for requirement_id in requirement_order]


def _parse_semantic_result(text: str) -> dict[str, str]:
    """Parse strict JSON, plus the CLI's unambiguous one-line fallback."""
    try:
        value = parse_json_object(text)
    except ValueError:
        chinese = re.fullmatch(r"\s*(通过|失败|无法判断|不确定)\s*[：:]\s*(.+?)\s*", text, re.S)
        if chinese:
            status = {"通过": "pass", "失败": "fail", "无法判断": "indeterminate", "不确定": "indeterminate"}
            return {"status": status[chinese.group(1)], "reason": chinese.group(2).strip()}
        match = re.fullmatch(r"\s*status\s*:\s*(pass|fail|indeterminate)\s+reason\s*:\s*(.+?)\s*", text, re.I | re.S)
        if not match:
            match = re.fullmatch(r"\s*(?:status|状态)\s*[：:]\s*(pass|fail|indeterminate)\s+(?:reason|理由)\s*[：:]\s*(.+?)\s*", text, re.I | re.S)
        if not match:
            match = re.fullmatch(r"\s*(pass|fail|indeterminate)\s*[：:]\s*(.+?)\s*", text, re.I | re.S)
        if not match:
            match = re.fullmatch(r"\s*(pass|fail|indeterminate)\s+(?:reason|理由)\s*[：:]\s*(.+?)\s*", text, re.I | re.S)
        if not match:
            match = re.fullmatch(r"\s*(pass|fail|indeterminate)\s+(.+?)\s*", text, re.I | re.S)
        if not match:
            raise
        value = {"status": match.group(1).lower(), "reason": match.group(2).strip()}
    if set(value) != {"status", "reason"}:
        raise ValueError("语义评审结果结构非法")
    return value


def _semantic_prompt(
    result: dict[str, Any],
    evidence: dict[str, Any],
    requirement: dict[str, Any],
    task_text: str | None = None,
) -> str:
    refs = result["evidence_refs"]
    selected: dict[str, Any] = {
        "answer": evidence.get("answer", "") if "answer" in refs else "",
        "calls": [],
        "verifier_calls": [],
        "changed_paths": [],
        "initial_files": [],
        "final_files": [],
    }
    for reference in refs:
        if reference.startswith("tool_call:"):
            index = int(reference.split(":", 1)[1])
            selected["calls"].append(evidence["calls"][index])
        elif reference.startswith("verifier_call:"):
            index = int(reference.split(":", 1)[1])
            selected["verifier_calls"].append(evidence["verifier_calls"][index])
        elif reference.startswith("workspace_change:"):
            path = reference.split(":", 1)[1]
            selected["changed_paths"].extend(
                item for item in evidence.get("changed_paths", []) if item.get("path") == path
            )
        elif reference.startswith(("initial:", "final:")):
            state, path = reference.split(":", 1)
            key = state + "_files"
            item = next((item for item in evidence.get(key, []) if item.get("path") == path), None)
            if item is not None:
                item = dict(item)
                if state == "final" and "text" not in item and "json" not in item:
                    initial = next((candidate for candidate in evidence.get("initial_files", [])
                                    if candidate.get("path") == path), None)
                    if initial is not None and initial.get("sha256") == item.get("sha256"):
                        item.update({field: initial[field] for field in ("text", "json", "truncated")
                                     if field in initial})
                selected[key].append(item)
    role = (
        "你是单项任务要求的语义核验器，只判断给定要求，不新增要求。"
        "只有引用证据直接、完整地证明要求时才通过；证据缺失、截断或相互冲突时返回 indeterminate，"
        "证据明确反驳要求时返回 fail。理由只能陈述引用证据实际显示的事实，且事实必须属于同一业务对象、"
        "交付物、时间范围和字段，不能借用其他对象上的同名信息。"
    )
    if task_text is not None:
        role += (
            "原任务是成功标准的唯一权威；参考执行、原子子项和生成代码只是核验辅助。"
            "当前原子子项不包含其他任务目标是正常的，不得因遗漏无关目标而失败。"
            "若原子子项或代码加入原任务对应分句未要求的限定、格式、载体或操作方式，忽略这些加码。"
            "限定条件只作用于它直接修饰的对象或动作，不得传播给其他目标。"
        )
    role += (
        "按任务动词要求的状态变化判断结果；要求写入或改变持久状态时，最终回答不能补足状态证据，"
        "只要求查询、计算或呈现时，工具结果和最终回答可以共同证明。"
        "技术 ID、路径、序列化格式和措辞不是业务结果，除非原任务明确指定。"
        "精确值、计数、路径、哈希、枚举和布尔值等结构化事实应与证据严格一致；自然语言仅判断含义是否满足。"
        "要求交付内容时，空字段、占位内容或仅声称完成不算交付。"
    )
    return json.dumps({
        "role": role,
        "task": task_text,
        "requirement_id": result["requirement_id"],
        "claim": result["reason"],
        "requirement": requirement,
        "evidence_refs": refs,
        "evidence": selected,
        "response_contract": {
            "status": "pass、fail 或 indeterminate",
            "reason": "引用证据的简短具体理由",
        },
    }, ensure_ascii=False)


def aggregate_results(requirements: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate required criteria without allowing compensation."""
    by_id = {result["requirement_id"]: result for result in results}
    required = [by_id[item["id"]]["status"] for item in requirements if item["required"]]
    if "fail" in required:
        outcome = "fail"
    elif "indeterminate" in required or "semantic" in required:
        outcome = "indeterminate"
    else:
        outcome = "pass"
    attribution = "agent" if outcome == "fail" else "verifier" if outcome == "indeterminate" else None
    return {
        "outcome": outcome,
        "passed": outcome == "pass",
        "attribution": attribution,
        "requirements": results,
    }


def validate_verification_spec(specification: dict[str, Any]) -> None:
    """Validate task coverage before reference evidence can influence implementation."""
    if not isinstance(specification, dict) or set(specification) != {
        "schema_version", "task_clauses", "requirements",
    }:
        raise ValueError("verification spec 必须只包含 schema_version、task_clauses、requirements")
    if specification["schema_version"] != "1":
        raise ValueError("verification spec schema_version 必须为 1")
    clauses = specification["task_clauses"]
    if not isinstance(clauses, list) or not clauses:
        raise ValueError("task_clauses 必须是非空数组")
    clause_ids: set[str] = set()
    for clause in clauses:
        if not isinstance(clause, dict) or set(clause) != {"id", "text"}:
            raise ValueError("task clause 必须只包含 id 和 text")
        if (not isinstance(clause["id"], str) or not clause["id"]
                or clause["id"] in clause_ids):
            raise ValueError("task clause id 必须是唯一的非空字符串")
        if not isinstance(clause["text"], str) or not clause["text"].strip():
            raise ValueError("task clause text 必须是非空字符串")
        clause_ids.add(clause["id"])

    requirements = specification["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("verification spec requirements 必须是非空数组")
    required_keys = {
        "id", "claim", "required", "task_clause_ids", "outcome_type", "evidence_channels",
        "pass_condition", "fail_condition", "indeterminate_condition",
    }
    requirement_ids: set[str] = set()
    coverage: list[str] = []
    integrity_count = 0
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != required_keys:
            raise ValueError(f"verification requirement 必须包含 {sorted(required_keys)}")
        requirement_id = requirement["id"]
        if (not isinstance(requirement_id, str) or not requirement_id
                or requirement_id in requirement_ids):
            raise ValueError("verification requirement id 必须是唯一的非空字符串")
        requirement_ids.add(requirement_id)
        if not isinstance(requirement["claim"], str) or not requirement["claim"].strip():
            raise ValueError("verification requirement claim 必须是非空字符串")
        if requirement["required"] is not True:
            raise ValueError("verification spec 中所有 requirement 都必须 required=true")
        outcome_type = requirement["outcome_type"]
        if outcome_type not in _OUTCOME_TYPES:
            raise ValueError(f"非法 outcome_type：{outcome_type}")
        channels = requirement["evidence_channels"]
        if (not isinstance(channels, list) or not channels
                or len(channels) != len(set(channels))
                or any(channel not in _CHANNELS for channel in channels)):
            raise ValueError("verification requirement evidence_channels 非法")
        if outcome_type in {"persistent_state", "preservation", "execution_integrity"} and "workspace" not in channels:
            raise ValueError(f"{outcome_type} requirement 必须包含 workspace 证据")
        mapped = requirement["task_clause_ids"]
        if not isinstance(mapped, list) or any(item not in clause_ids for item in mapped):
            raise ValueError("verification requirement task_clause_ids 非法")
        if outcome_type == "execution_integrity":
            integrity_count += 1
            if mapped:
                raise ValueError("execution_integrity 不得占用 task clause")
        elif not mapped:
            raise ValueError(f"{requirement_id} 必须覆盖至少一个 task clause")
        coverage.extend(mapped)
        for key in ("pass_condition", "fail_condition", "indeterminate_condition"):
            if not isinstance(requirement[key], str) or not requirement[key].strip():
                raise ValueError(f"{requirement_id}.{key} 必须是非空字符串")
    if integrity_count > 1:
        raise ValueError("verification spec 最多包含一个 execution_integrity requirement")
    missing = sorted(clause_ids - set(coverage))
    duplicated = sorted(item for item in clause_ids if coverage.count(item) != 1)
    if missing or duplicated:
        affected = sorted(set(missing + duplicated))
        raise ValueError("task clause 必须被恰好覆盖一次：" + ", ".join(affected))


def generate_verification_spec(
    task: dict[str, Any],
    environment: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
    previous_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive auditable success conditions without seeing a reference execution."""
    request: dict[str, Any] = {
        "role": (
            "你负责把任务文本转成验证规格。先把每个结果写成可被证据证明、反驳或暂时无法判断的"
            "业务 claim，再定义对应证据边界；不要把执行方法当成结果。任务文本是要求的唯一权威，"
            "环境只提供可观察状态、可用工具和业务约束。不要指定任务未要求的工具、调用顺序、"
            "内部 ID、文件路径、字段承载方式、数量或措辞。"
        ),
        "principles": [
            "先完整拆出任务中的原子结果；每个结果只表达一个可独立失败的 claim，所有 claim 的并集覆盖任务，且每个 task clause 恰好覆盖一次。",
            "同一句中的并列结果仍分别建模；共同对象、范围和关系作为上下文绑定，不能用绑定掩盖独立失败。",
            "每个 requirement 必须同时给出充分通过、明确失败和证据不足三种边界；没有明确反证或完整权威缺失时不得判 fail。",
            "当任务要求从明确的源集合生成、导出、转换或保留内容时，完整性（源集合中的每个要求项都被覆盖）是该结果的语义组成部分，必须单独或在同一原子 requirement 中明确表达；不要只检查产物中任意一个条目。若任务没有表达保留、全部或完整覆盖，则不得擅自增加源集合完整性要求。",
            "创建、修改、删除和保持必须由 workspace 初末状态证明；查询、计算和呈现可由工具结果与最终回答证明。",
            "evidence_channels 只说明可接受的证据来源，不规定工具、顺序、数量、路径或表示方式。",
            "不得把新生成、初态不存在、唯一文件、固定数量、舍入容差、编码表示或特定更新方式写成通过条件，除非任务或环境契约明确要求；已有同样有效的结果可以被更新、覆盖或复用。",
            "仅当任务和环境表明存在具体、风险较大的破坏性或冲突性副作用时，才可单独增加一个 "
            "execution_integrity requirement，并明确检查的风险；没有此类风险则不添加。"
            "任务相关的正常状态变化不算违规，不要求最小执行路径。",
        ],
        "task": task.get("task_text"),
        "environment": environment,
        "response_contract": {
            "schema_version": "1",
            "task_clauses": [{"id": "C1", "text": "任务原文中的一个最小可检验要求"}],
            "requirements": [{
                "id": "R1",
                "claim": "一个可独立判断的业务结果",
                "required": True,
                "task_clause_ids": ["C1"],
                "outcome_type": "persistent_state|query|computation|presentation|preservation|execution_integrity",
                "evidence_channels": ["workspace", "tool_trace", "answer"],
                "pass_condition": "足以证明结果成立的证据",
                "fail_condition": "足以证明结果不成立的证据",
                "indeterminate_condition": "无法可靠判断的证据状态",
            }],
        },
    }
    if previous_issues:
        request["previous_issues"] = previous_issues
        request["revision_instruction"] = (
            "重新生成完整规格，逐项解决 previous_issues，且不得重新引入其中任何问题。"
            "保留已经正确覆盖的任务要求；只修改导致 issue 的 requirement。"
            "再次检查：保留/导出/转换任务是否覆盖完整源集合；是否误加初态不存在、新生成、唯一性、"
            "数量或表示容差等任务未声明条件。"
        )
    specification = parse_json_object(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text)
    if set(specification) == {"response_contract"} and isinstance(specification["response_contract"], dict):
        specification = specification["response_contract"]
    validate_verification_spec(specification)
    return specification


def validate_evidence_plan(plan: dict[str, Any], specification: dict[str, Any]) -> None:
    """Validate the small handoff between task decomposition and code generation."""
    if not isinstance(plan, dict) or not {"schema_version", "requirements"} <= set(plan):
        raise ValueError("evidence plan 必须包含 schema_version 和 requirements")
    if plan["schema_version"] != "1" or not isinstance(plan["requirements"], list):
        raise ValueError("evidence plan 结构非法")
    expected = {item["id"] for item in specification["requirements"]}
    seen: set[str] = set()
    for item in plan["requirements"]:
        if not isinstance(item, dict) or not {
            "requirement_id", "sources", "strategy", "alternatives",
        } <= set(item):
            raise ValueError("evidence plan requirement 结构非法")
        requirement_id = item["requirement_id"]
        if requirement_id not in expected or requirement_id in seen:
            raise ValueError("evidence plan requirement_id 未知或重复")
        seen.add(requirement_id)
        if (not isinstance(item["sources"], list) or not item["sources"]
                or any(source not in _CHANNELS for source in item["sources"])):
            raise ValueError("evidence plan sources 非法")
        if not isinstance(item["strategy"], str) or not item["strategy"].strip():
            raise ValueError("evidence plan strategy 必须是非空字符串")
        if (not isinstance(item["alternatives"], list)
                or any(not isinstance(value, str) or not value.strip() for value in item["alternatives"])):
            raise ValueError("evidence plan alternatives 必须是字符串数组")
    if seen != expected:
        raise ValueError("evidence plan 必须恰好覆盖全部 requirements")


def _unwrap_contract(value: dict[str, Any], required: set[str]) -> dict[str, Any]:
    if required <= set(value):
        return value
    candidates = [
        child for child in value.values()
        if isinstance(child, dict) and required <= set(child)
    ]
    return candidates[0] if len(candidates) == 1 else value


def _parse_contract_response(text: str, required: set[str]) -> dict[str, Any]:
    """Prefer the last complete JSON object that actually satisfies a contract."""
    decoder = json.JSONDecoder()
    matches: list[dict[str, Any]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidate = _unwrap_contract(value, required)
            if required <= set(candidate):
                matches.append(candidate)
    if matches:
        return matches[-1]
    return _unwrap_contract(parse_json_object(text), required)


def generate_evidence_plan(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Plan where a future verifier should look, without judging an execution."""
    request = {
        "role": (
            "你负责为已经拆分好的任务要求规划证据。此时尚未执行待测 Agent；请说明未来 verifier "
            "可以去哪里观察事实、如何保持对象和关系一致，以及哪些不同实现也能证明同一结果。"
            "不要写验证代码，不要判断通过失败。"
        ),
        "principles": [
            "每个 requirement 只写一项简洁策略，并且恰好出现一次。",
            "参考执行只用于发现可能的证据位置和数据形态，不是白名单，也不是唯一正确路径。",
            "允许任何满足任务结果的工具、顺序、文件或等价表达，除非任务明确限定。",
            "需要组合多项事实时，明确它们必须属于同一业务对象、交付物或集合关系，不能跨对象拼接。",
            "缺失只有在证据来源完整且权威时才能证明失败；否则 verifier 应保留 indeterminate。",
        ],
        "task": task.get("task_text"),
        "environment": environment,
        "specification": specification,
        "reference_evidence": _generation_evidence(reference_evidence),
        "output_instruction": "只输出 response_contract 对应的 JSON object，不要解释，不要 Markdown。",
        "response_contract": {
            "schema_version": "1",
            "requirements": [{
                "requirement_id": "R1",
                "sources": ["workspace|tool_trace|answer 中 specification 允许的渠道"],
                "strategy": "未来执行后应从哪里、按什么关系寻找充分证据",
                "alternatives": ["同样可以证明该结果的其他实现或证据路径"],
            }],
        },
    }
    plan = _parse_contract_response(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text, {"schema_version", "requirements"})
    validate_evidence_plan(plan, specification)
    return plan


def _generated_source(response: InferenceResult, *, stage: str) -> str:
    try:
        value = _parse_contract_response(response.text, {"source"})
    except ValueError:
        fenced = re.fullmatch(r"\s*```(?:python)?\s*(.*?)\s*```\s*", response.text, re.I | re.S)
        if fenced:
            source = fenced.group(1).strip()
            if source:
                return source
        raise
    if not isinstance(value, dict) or "source" not in value:
        raise ValueError(f"{stage} 必须返回 source")
    source = value["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{stage}.source 必须是非空字符串")
    return source


def generate_planned_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    evidence_plan: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Generate one complete verifier from the frozen subtasks and evidence plan."""
    validate_evidence_plan(evidence_plan, specification)
    request = {
        "role": (
            "你负责把冻结的任务子项和证据规划实现成一个完整 verifier。一次完成 verify(ctx)；"
            "可以在 verify 内定义小函数，但不要把不同 requirement 分给其他模型。"
        ),
        "principles": [
            "任务是唯一验收标准。任务未要求的操作或产物，即使参考执行做了，也禁止加入验收条件。"
            "参考执行中的参数、路径和中间结果同样不能自行成为要求；凡是满足任务的实现，"
            "都不能因与参考执行不同而判失败。",
            "specification 和 evidence_plan 是辅助中间产物，不能增加或覆盖原始任务的要求。",
            "副作用检查是按风险启用的例外：若 specification 包含 execution_integrity，"
            "仅核验其中指出的具体高风险破坏性或冲突性副作用；不得扩大为禁止正常关联变化或所有额外操作。",
            "每项 pass 必须有充分证据，fail 必须有明确反证；信息不完整、不可读或冲突时使用 indeterminate。",
            "组合证据必须属于同一对象、交付物和关系；集合任务检查任务明确要求的整体范围。",
            "结构化事实由代码判断；确实需要理解自然语言含义时使用 semantic_requirement。",
            "可以读取完整初态、终态、实际调用及回答，也可以用 call_tool 只读核验终态；不能替 Agent 补做任务。",
            "必须为 specification.requirements 中的全部 ID 在每条执行路径恰好记录一次结果，"
            "包括已列出的 execution_integrity，并引用真实 evidence_ref；未列出时无需添加副作用检查。",
        ],
        "task": task.get("task_text"),
        "environment": environment,
        "specification": specification,
        "evidence_plan": evidence_plan,
        "reference_evidence": _generation_evidence(reference_evidence),
        "verifier_context_api": {
            "answer() / calls() / changed_paths()": "读取回答、调用链和状态变化",
            "files(state) / file(path, state) / read_text(path, state) / read_json(path, state)": "只读初态或终态",
            "call_tool(name, arguments)": (
                "在终态副本中补充核验，返回 {tool, arguments, result, error, evidence_ref}；"
                "工具自己的 success/data/error 位于外层记录的 result 字段内"
            ),
            "verifier_calls()": "返回 verifier 已执行的补充核验记录",
            "pass_requirement(id, reason, refs) / fail_requirement(...) / indeterminate_requirement(...)": "记录确定结果",
            "semantic_requirement(id, claim, refs)": "让运行期模型判断引用证据中的语义命题",
        },
        "evidence_reference_formats": [
            "answer", "tool_call:N", "initial:relative/path", "final:relative/path",
            "workspace_change:relative/path", "verifier_call:N",
        ],
        "output_instruction": "只输出 response_contract 对应的 JSON object，不要解释，不要 Markdown。",
        "response_contract": {"source": "Python source defining verify(ctx)"},
    }
    response = infer_fn(json.dumps(request, ensure_ascii=False), llm_config=llm_config)
    if not isinstance(response, InferenceResult):
        raise ValueError("verifier generation 必须返回单条结果")
    return {
        "schema_version": "1",
        "requirements": _planned_requirements(specification, evidence_plan),
        "source": _generated_source(response, stage="verifier generation"),
    }


def review_and_revise_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    evidence_plan: dict[str, Any],
    package: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Review once and return the corrected verifier instead of issue feedback."""
    _ = task, environment, reference_evidence
    validate_evidence_plan(evidence_plan, specification)
    try:
        validate_verifier(package)
    except ValueError as error:
        draft_validation_error = str(error)
    else:
        draft_validation_error = None
    request = {
        "role": (
            "你只负责把当前 verifier 转换成符合 code_constraints 的等价 Python 实现。业务判断已经冻结；"
            "逐项检查全部限制，若已合规则原样返回，若不合规则只替换违规语法。不得删除判断，也不得修改"
            "条件、常量、requirement ID、证据引用、ctx API 参数或 pass/fail/indeterminate 结果。"
            "不要重新设计 verifier，不要输出问题清单。"
        ),
        "current_verifier": package,
        "draft_validation_error": draft_validation_error,
        "code_constraints": {
            "required_structure": (
                "source 顶层只能定义一个无装饰器的 verify(ctx)；只能有一个名为 ctx 的位置参数，"
                "不得使用 *args 或 **kwargs；辅助函数只能定义在 verify 内。"
            ),
            "forbidden_syntax_nodes": sorted(node.__name__ for node in _BANNED_NODES),
            "forbidden_calls": sorted(_BANNED_CALLS),
            "forbidden_names": "不得使用以双下划线开头的名称，也不得访问任何以下划线开头的属性。",
            "allowed_context_methods": sorted(_CONTEXT_METHODS),
            "available_runtime_values": ["json", "re", "html", "math", "deepcopy", "Path"],
            "behavior": [
                "不得写文件、启动进程或访问 verifier 上下文的私有状态。",
                "每个 requirement 在每条控制流恰好记录一次。",
            ],
        },
        "output_instruction": "只输出 response_contract 对应的 JSON object，不要解释，不要 Markdown。",
        "response_contract": {"source": "审查并直接修正后的完整 Python verify(ctx) 源码"},
    }
    response = infer_fn(json.dumps(request, ensure_ascii=False), llm_config=llm_config)
    if not isinstance(response, InferenceResult):
        raise ValueError("verifier review 必须返回单条结果")
    reviewed = {
        "schema_version": "1",
        "requirements": _planned_requirements(specification, evidence_plan),
        "source": _generated_source(response, stage="verifier review"),
    }
    validate_verifier(reviewed)
    return reviewed


def _validate_review(review: dict[str, Any], *, kind: str) -> None:
    if not isinstance(review, dict) or set(review) != {"approved", "issues"}:
        raise ValueError(f"{kind} review 必须只包含 approved 和 issues")
    if not isinstance(review["approved"], bool) or not isinstance(review["issues"], list):
        raise ValueError(f"{kind} review 结构非法")
    issue_keys = {"code", "task_clause_ids", "requirement_ids", "message"}
    for issue in review["issues"]:
        if (not isinstance(issue, dict) or set(issue) != issue_keys
                or any(not isinstance(issue[key], list) for key in ("task_clause_ids", "requirement_ids"))
                or any(not isinstance(item, str) for key in ("task_clause_ids", "requirement_ids") for item in issue[key])
                or not isinstance(issue["code"], str) or not issue["code"]
                or not isinstance(issue["message"], str) or not issue["message"].strip()):
            raise ValueError(f"{kind} review issue 结构非法")
    if review["approved"] != (not review["issues"]):
        raise ValueError(f"{kind} review 的 approved 与 issues 矛盾")


def review_verification_spec(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Independently reject missing, merged, invented, or unprovable requirements."""
    validate_verification_spec(specification)
    request = {
        "role": (
            "你独立审核验证规格，不重写它。把任务当作 claim 集合逐项核对：结果是否完整、原子、"
            "可证伪且只使用声明的证据；是否偷偷加入工具、路径、数量、顺序或表示限制。"
            "唯一不来自任务文本的 execution_integrity 只能作为系统级可观察副作用门禁，不能成为实现路径要求。"
            "任何会把合法替代实现判错的实质问题都必须拒绝。"
        ),
        "review_dimensions": ["coverage", "atomicity", "fidelity", "verifiability", "evidence_classification"],
        "task": task.get("task_text"),
        "environment": environment,
        "specification": specification,
        "response_contract": {
            "approved": "boolean；仅当 issues 为空时为 true",
            "issues": [{
                "code": "missing_requirement|non_atomic|added_constraint|unverifiable|wrong_evidence_classification|other",
                "task_clause_ids": ["C1"],
                "requirement_ids": ["R1"],
                "message": "具体说明规格为何偏离任务或无法可靠验证",
            }],
        },
    }
    review = parse_json_object(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text)
    _validate_review(review, kind="specification")
    return review


def _validate_plan_sources(
    sources: Any,
    *,
    allowed_channels: set[str] | None = None,
) -> None:
    keys = {
        "channel", "locator", "provenance", "use", "completeness",
        "absence_is_conclusive", "basis",
    }
    if not isinstance(sources, list) or not sources:
        raise ValueError("proof plan evidence_sources 必须是非空数组")
    for source in sources:
        if not isinstance(source, dict) or set(source) != keys:
            raise ValueError("proof plan evidence source 结构非法")
        channel = source["channel"]
        if channel not in _CHANNELS or allowed_channels is not None and channel not in allowed_channels:
            raise ValueError("proof plan evidence source channel 非法")
        if source["provenance"] not in _EVIDENCE_PROVENANCE:
            raise ValueError("proof plan evidence source provenance 非法")
        if source["use"] not in _EVIDENCE_USES:
            raise ValueError("proof plan evidence source use 非法")
        if source["completeness"] not in {"complete", "partial"}:
            raise ValueError("proof plan evidence source completeness 非法")
        if not isinstance(source["absence_is_conclusive"], bool):
            raise ValueError("proof plan absence_is_conclusive 必须是 boolean")
        if source["absence_is_conclusive"] and source["completeness"] == "partial":
            raise ValueError("partial evidence 不能让 absence_is_conclusive=true")
        if source["provenance"] == "reference_observation" and source["use"] == "criterion":
            raise ValueError("reference_observation 不能作为 criterion")
        if any(not isinstance(source[key], str) or not source[key].strip() for key in ("locator", "basis")):
            raise ValueError("proof plan evidence source 文本字段不能为空")


def _normalize_proof_plan_channels(value: Any) -> None:
    if isinstance(value, dict):
        channel_aliases = {
            "call_tool", "tool_call", "tool_calls", "verifier_call", "verifier_calls",
            "verifier_tool_call", "verifier_tool_calls",
        }
        channel = value.get("channel")
        if isinstance(channel, str) and channel.strip().lower() in channel_aliases:
            value["channel"] = "tool_trace"
        provenance = value.get("provenance")
        if isinstance(provenance, str) and provenance.strip().lower() in {
            "tool_observation", "verifier_observation",
        }:
            value["provenance"] = "environment_contract"
        for child in value.values():
            _normalize_proof_plan_channels(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_proof_plan_channels(child)


def _proof_component(text: str, fields: set[str]) -> dict[str, Any]:
    value = parse_json_object(text)
    if set(value) == {"response_contract"} and isinstance(value["response_contract"], dict):
        value = value["response_contract"]
    if not fields <= set(value):
        raise ValueError(f"proof plan component 缺少字段：{sorted(fields - set(value))}")
    return {field: value[field] for field in fields}


def _validate_bindings(bindings: Any) -> set[str]:
    if not isinstance(bindings, list):
        raise ValueError("proof plan bindings 必须是数组")
    binding_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {
            "id", "business_identity", "identity_evidence", "excluded_properties",
        }:
            raise ValueError("proof plan binding 结构非法")
        binding_id = binding["id"]
        if not isinstance(binding_id, str) or not binding_id or binding_id in binding_ids:
            raise ValueError("proof plan binding id 必须唯一且非空")
        binding_ids.add(binding_id)
        if not isinstance(binding["business_identity"], str) or not binding["business_identity"].strip():
            raise ValueError("proof plan business_identity 不能为空")
        _validate_plan_sources(binding["identity_evidence"])
        excluded = binding["excluded_properties"]
        if not isinstance(excluded, list) or any(not isinstance(item, str) or not item for item in excluded):
            raise ValueError("proof plan excluded_properties 非法")
    return binding_ids


def _validate_requirement_proof_component(
    value: dict[str, Any], requirement: dict[str, Any], binding_ids: set[str],
) -> None:
    used_bindings = value["binding_ids"]
    if not isinstance(used_bindings, list) or set(used_bindings) - binding_ids:
        raise ValueError("binding_ids 必须只引用 binding design 中的 ID")
    _validate_plan_sources(
        value["evidence_sources"], allowed_channels=set(requirement["evidence_channels"]),
    )
    if any(not isinstance(value[key], str) or not value[key].strip()
           for key in ("proof", "disproof", "indeterminate")):
        raise ValueError("proof、disproof 和 indeterminate 必须是非空字符串")


def _validate_integrity_proof_component(value: dict[str, Any], requirement: dict[str, Any]) -> None:
    _validate_plan_sources(
        value["evidence_sources"], allowed_channels=set(requirement["evidence_channels"]),
    )
    if any(not isinstance(value[key], str) or not value[key].strip()
           for key in ("observable_scope", "proof", "indeterminate")):
        raise ValueError("integrity 文本字段必须是非空字符串")
    violations = value["explicit_violations"]
    if not isinstance(violations, list) or not violations or any(
        not isinstance(item, str) or not item.strip() for item in violations
    ):
        raise ValueError("explicit_violations 必须是非空字符串数组")


def validate_proof_plan(plan: dict[str, Any], specification: dict[str, Any]) -> None:
    """Validate a code-free, reference-aware proof plan against the frozen specification."""
    validate_verification_spec(specification)
    if not isinstance(plan, dict) or set(plan) != {
        "schema_version", "bindings", "requirements", "integrity",
    }:
        raise ValueError("proof plan 顶层结构非法")
    if plan["schema_version"] != "1":
        raise ValueError("proof plan schema_version 必须为 1")

    binding_ids = _validate_bindings(plan["bindings"])

    spec_requirements = {item["id"]: item for item in specification["requirements"]}
    integrity_ids = {
        item["id"] for item in specification["requirements"]
        if item["outcome_type"] == "execution_integrity"
    }
    expected = set(spec_requirements) - integrity_ids
    planned: set[str] = set()
    requirement_keys = {
        "requirement_id", "binding_ids", "evidence_sources", "proof", "disproof", "indeterminate",
    }
    requirements = plan["requirements"]
    if not isinstance(requirements, list):
        raise ValueError("proof plan requirements 必须是数组")
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != requirement_keys:
            raise ValueError("proof plan requirement 结构非法")
        requirement_id = requirement["requirement_id"]
        if requirement_id not in expected or requirement_id in planned:
            raise ValueError("proof plan requirement_id 非法或重复")
        planned.add(requirement_id)
        used_bindings = requirement["binding_ids"]
        if not isinstance(used_bindings, list) or set(used_bindings) - binding_ids:
            raise ValueError("proof plan binding_ids 非法")
        _validate_plan_sources(
            requirement["evidence_sources"],
            allowed_channels=set(spec_requirements[requirement_id]["evidence_channels"]),
        )
        if any(not isinstance(requirement[key], str) or not requirement[key].strip()
               for key in ("proof", "disproof", "indeterminate")):
            raise ValueError("proof plan requirement 判定字段不能为空")
    if planned != expected:
        raise ValueError("proof plan 必须恰好覆盖全部非完整性 requirement")

    integrity = plan["integrity"]
    integrity_keys = {
        "requirement_id", "observable_scope", "evidence_sources", "proof",
        "explicit_violations", "indeterminate",
    }
    if not isinstance(integrity, dict) or set(integrity) != integrity_keys:
        raise ValueError("proof plan integrity 结构非法")
    integrity_id = integrity["requirement_id"]
    if integrity_id not in integrity_ids:
        raise ValueError("proof plan integrity requirement_id 非法")
    _validate_plan_sources(
        integrity["evidence_sources"],
        allowed_channels=set(spec_requirements[integrity_id]["evidence_channels"]),
    )
    if any(not isinstance(integrity[key], str) or not integrity[key].strip()
           for key in ("observable_scope", "proof", "indeterminate")):
        raise ValueError("proof plan integrity 文本字段不能为空")
    violations = integrity["explicit_violations"]
    if not isinstance(violations, list) or not violations or any(
        not isinstance(item, str) or not item.strip() for item in violations
    ):
        raise ValueError("proof plan explicit_violations 必须是非空字符串数组")


def generate_proof_plan(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
    previous_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Design proof obligations before source generation sees any reference execution."""
    validate_verification_spec(specification)
    source_contract = {
        "channel": "workspace|tool_trace|answer；call_tool 和 verifier_call 取得的核验证据也统一写作 tool_trace",
        "locator": "如何取得证据；它不是成功条件",
        "provenance": "task|environment_contract|reference_observation",
        "use": "criterion|locator|example",
        "completeness": "complete|partial",
        "absence_is_conclusive": False,
        "basis": "为什么该证据充分、部分或具有完整性",
    }
    principles = [
        "参考执行只提供定位和例子；没有复现参考工具、顺序、ID、路径、字段或表示，不等于任务失败。",
        "binding 是待赋值的逻辑变量：候选域由业务类型和可观察范围界定，不能用正在审核的属性预先筛出正确答案。",
        "binding 的业务身份必须包含任务明确给出的名称、类型、归属关系或其他稳定标识；不得遗漏这些身份条件，也不得用任务未要求的属性补强身份关联。",
        "binding 只用于需要在候选域中独立选择的业务对象。若 requirement 要求父对象的完整成员集合满足全部、每个或覆盖条件，不要把每个集合成员建成存在性 binding；在父对象 assignment 内把完整集合交给该 requirement 整体判断。",
        "同一 binding 出现在多个 requirement 时，这些 requirement 必须由同一个候选对象同时满足，不能跨对象拼接证据。",
        "任务以单数指代的每个业务实体、交付物或关联实体都应有独立 binding；不能把评论、附件、关联等集合藏在父对象中，再让不同 requirement 各自选择不同成员。",
        "候选域可以有任意多个成员；选择一个见证赋值不代表环境中只允许存在一个该类对象。",
        "任务未明确限制数量时不得增加数量上限；候选域的每个成员分别形成 assignment，不能合并字段共同证明单个对象。",
        "证明是寻找一组一致的见证赋值：共享 binding 的 requirement 共同约束同一对象；没有共享 binding 的并列结果可以分别寻找见证。",
        "pass 需要充分直接证据；fail 需要明确反证或完整权威来源中的决定性缺失；partial、不可读和冲突证据只能 indeterminate。",
        "task 定义标准，environment_contract 定义可使用的路径、字段、枚举和稳定标识；reference_observation 只能作 locator 或 example。",
        "优先使用 Schema 完整的只读工具或明确数据契约，不猜测环境未声明的容器、字段或格式。",
        "证据 channel 只能使用 workspace、tool_trace、answer；核验工具结果统一归入 tool_trace。",
        "完整性检查只寻找明确的无关、破坏性或冲突副作用，不把参考 diff 或预期写入集合当作变化白名单。",
    ]
    common: dict[str, Any] = {
        "principles": principles,
        "task": task.get("task_text"),
        "environment": environment,
        "reference_evidence": _generation_evidence(reference_evidence),
        "output_instruction": "只输出 response_contract 描述的一个 JSON object，不要解释、markdown 或额外文字。",
    }
    non_integrity = [
        item for item in specification["requirements"] if item["outcome_type"] != "execution_integrity"
    ]
    integrity = next(
        item for item in specification["requirements"] if item["outcome_type"] == "execution_integrity"
    )
    binding_request = {
        **common,
        "component": "binding_design",
        "role": (
            "只设计证明计划中的逻辑变量和候选域，不写各 requirement 的判定。"
            "同时一次性决定每项非完整性 requirement 使用哪些 binding；共享业务对象必须共享 binding，"
            "同一条子实体承载多项要求时也必须共享其 binding。动态 ID 可以标识枚举出的候选，"
            "但不能要求 Agent 必须通过参考工具返回该 ID。集合成员只有在任务要求独立选择某个成员时才建 binding；"
            "全部、每个或覆盖等集合条件保留为父对象 assignment 内的整体谓词。"
        ),
        "specification": specification,
        "response_contract": {
            "bindings": [{
                "id": "B1",
                "business_identity": "候选业务类型、可观察范围和身份关联；不包含被审核属性",
                "identity_evidence": [source_contract],
                "excluded_properties": ["不能用于预筛候选的被审核属性"],
            }],
            "requirement_bindings": {
                item["id"]: ["B1"] for item in non_integrity
            },
        },
    }
    if previous_issues:
        binding_issues = [
            issue for issue in previous_issues
            if not isinstance(issue, dict) or issue.get("code") in {"identity_depends_on_result", "wrong_binding"}
            or not issue.get("requirement_ids")
        ]
        if binding_issues:
            binding_request["previous_issues"] = binding_issues
    binding_error: Exception | None = None
    for _ in range(3):
        try:
            binding_response = infer_fn(json.dumps(binding_request, ensure_ascii=False), llm_config=llm_config)
            if not isinstance(binding_response, InferenceResult):
                raise ValueError("proof plan binding design 必须返回单条结果")
            binding_value = _proof_component(
                binding_response.text, {"bindings", "requirement_bindings"},
            )
            bindings = binding_value["bindings"]
            _normalize_proof_plan_channels(bindings)
            binding_ids = _validate_bindings(bindings)
            requirement_bindings = binding_value["requirement_bindings"]
            expected_requirement_ids = {item["id"] for item in non_integrity}
            if (not isinstance(requirement_bindings, dict)
                    or set(requirement_bindings) != expected_requirement_ids
                    or any(not isinstance(ids, list) or set(ids) - binding_ids
                           for ids in requirement_bindings.values())):
                raise ValueError("requirement_bindings 必须恰好覆盖非完整性 requirements 并只引用已有 binding")
            break
        except Exception as error:
            binding_error = error
            binding_request["format_correction"] = str(error)
    else:
        raise ValueError(f"proof plan binding design 结构非法：{binding_error}")

    requests: list[str] = []
    requirement_contract = {
        "evidence_sources": [source_contract],
        "proof": "充分证明", "disproof": "明确反证", "indeterminate": "证据不足边界",
    }
    for requirement in non_integrity:
        request = {
            **common,
            "component": "requirement_proof",
            "role": (
                "只为给定的一项冻结 requirement 制定证明义务。binding_ids 已由全局 binding design 固定，"
                "不要返回或改变它们；不要返回 requirement_id。proof 和 disproof 都只描述当前一个 assignment 的局部谓词，"
                "候选域完整性和不存在共同满足 assignment 的全局结论由运行时代码处理。只验证本 requirement 的属性，"
                "不要重复、强化或借用同组其他 requirement 负责的属性。所有合法的环境声明字段都可作为证据载体。"
            ),
            "bindings": bindings,
            "binding_ids": requirement_bindings[requirement["id"]],
            "requirement": requirement,
            "response_contract": requirement_contract,
        }
        if previous_issues:
            relevant = [
                issue for issue in previous_issues
                if not isinstance(issue, dict) or not issue.get("requirement_ids")
                or requirement["id"] in issue["requirement_ids"]
            ]
            if relevant:
                request["previous_issues"] = relevant
        requests.append(json.dumps(request, ensure_ascii=False))
    integrity_request = {
        **common,
        "component": "integrity_proof",
        "role": (
            "只制定执行完整性证明。通过表示在完整可观察范围内未发现明确违规；"
            "失败必须指出实际可观察的无关、破坏性或冲突副作用，不能建立允许变化白名单。"
            "不要返回 requirement_id，它由代码固定。"
        ),
        "requirement": integrity,
        "response_contract": {
            "observable_scope": "实际可观察范围", "evidence_sources": [source_contract],
            "proof": "完整范围内没有明确违规",
            "explicit_violations": ["明确无关、破坏性或冲突的变化"],
            "indeterminate": "无法归因或观察不完整的边界",
        },
    }
    if previous_issues:
        relevant = [
            issue for issue in previous_issues
            if not isinstance(issue, dict) or not issue.get("requirement_ids")
            or integrity["id"] in issue["requirement_ids"]
        ]
        if relevant:
            integrity_request["previous_issues"] = relevant
    requests.append(json.dumps(integrity_request, ensure_ascii=False))
    requirement_fields = {"evidence_sources", "proof", "disproof", "indeterminate"}
    integrity_fields = {
        "observable_scope", "evidence_sources", "proof", "explicit_violations", "indeterminate",
    }
    expected_fields = [requirement_fields] * len(non_integrity) + [integrity_fields]
    objects: list[dict[str, Any] | None] = [None] * len(requests)
    pending = list(range(len(requests)))
    errors: dict[int, Exception] = {}
    for _ in range(3):
        if not pending:
            break
        responses = _infer_component_batch([requests[index] for index in pending], llm_config, infer_fn)
        retry: list[int] = []
        for index, response in zip(pending, responses):
            try:
                value = _proof_component(response.text, expected_fields[index])
                _normalize_proof_plan_channels(value)
                if index < len(non_integrity):
                    value["binding_ids"] = requirement_bindings[non_integrity[index]["id"]]
                    _validate_requirement_proof_component(
                        value, non_integrity[index], {item["id"] for item in bindings},
                    )
                else:
                    _validate_integrity_proof_component(value, integrity)
                objects[index] = value
                errors.pop(index, None)
            except (ValueError, TypeError) as error:
                errors[index] = error
                request = json.loads(requests[index])
                request["format_correction"] = str(error)
                requests[index] = json.dumps(request, ensure_ascii=False)
                retry.append(index)
        pending = retry
    if pending:
        detail = "; ".join(f"{index + 1}: {errors[index]}" for index in pending)
        raise ValueError(f"proof plan components 结构非法：{detail}")
    completed = [value for value in objects if value is not None]
    plan = {
        "schema_version": "1",
        "bindings": bindings,
        "requirements": [
            {"requirement_id": requirement["id"], **value}
            for requirement, value in zip(non_integrity, completed[:-1])
        ],
        "integrity": {"requirement_id": integrity["id"], **completed[-1]},
    }
    _normalize_proof_plan_channels(plan)
    validate_proof_plan(plan, specification)
    return plan


def review_proof_plan(
    task: dict[str, Any],
    environment: dict[str, Any],
    specification: dict[str, Any],
    plan: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Reject proof logic that confuses a reference path with task completion."""
    validate_proof_plan(plan, specification)
    request = {
        "role": (
            "你独立审核证明计划，不修改它。对每个 fail 条件追问：它是否有完整、直接、"
            "决定性的反证，还是只说明 Agent 没按参考方式执行；后者必须拒绝。检查 binding 是否"
            "保持同一业务身份、共享 binding 的要求是否必须由同一 assignment 满足、是否把 partial 缺失误当失败、是否引入数量上限，"
            "以及 integrity 是否把参考变化误当白名单。环境契约明确声明的事实是可用约束，不属于 reference_overfit；"
            "任何会把正确替代实现判错的路径都必须拒绝。运行时会枚举候选域的 assignment，并接受其中任一同时满足"
            "整个 witness group 的赋值；因此不要要求 proof plan 预先从参考工具调用锁定唯一动态 ID，也不要把一个候选"
            "不满足误解为所有候选都不满足。每项 disproof 只淘汰当前 assignment；只有完整候选域不存在共同满足项时"
            "才会全局失败。同一条评论、附件或关联若承载多个要求，必须有自己的 binding，不能在父对象集合内拼接。"
            "binding 必须保留任务明示的业务身份，但不得用任务未要求的属性建立或补强身份。单项 proof/disproof 不能"
            "重复或强化同组其他 requirement 负责的属性。不要把完整集合的每个成员误建成存在性 binding；集合全称条件"
            "应在父对象 assignment 内基于完整成员集合判断。"
        ),
        "task": task.get("task_text"),
        "environment": environment,
        "specification": specification,
        "proof_plan": plan,
        "witness_groups": _witness_groups(plan),
        "reference_evidence": _generation_evidence(reference_evidence),
        "response_contract": {
            "approved": "boolean；仅当 issues 为空时为 true",
            "issues": [{
                "code": "identity_depends_on_result|inconclusive_failure|reference_path_required|reference_as_criterion|closed_world_integrity|wrong_binding|other",
                "task_clause_ids": ["C1"], "requirement_ids": ["R1"],
                "message": "具体说明会误判哪种正确实现或证据边界",
            }],
        },
    }
    review = parse_json_object(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text)
    _validate_review(review, kind="proof plan")
    return review


def _frozen_requirements(specification: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "id": item["id"],
        "claim": item["claim"],
        "required": item["required"],
        "evidence_channels": item["evidence_channels"],
        "pass_condition": item["pass_condition"],
        "fail_condition": item["fail_condition"],
    } for item in specification["requirements"]]


def _planned_requirements(
    specification: dict[str, Any], evidence_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    planned_sources = {
        item["requirement_id"]: item["sources"] for item in evidence_plan["requirements"]
    }
    requirements = _frozen_requirements(specification)
    for requirement in requirements:
        requirement["evidence_channels"] = list(dict.fromkeys([
            *requirement["evidence_channels"], *planned_sources[requirement["id"]],
        ]))
    return requirements


def _witness_groups(proof_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive existential groups from requirements that share binding variables."""
    groups: list[dict[str, Any]] = []
    for requirement in proof_plan.get("requirements", []):
        binding_ids = set(requirement.get("binding_ids", []))
        if not binding_ids:
            continue
        matches = [index for index, group in enumerate(groups) if binding_ids & set(group["binding_ids"])]
        if not matches:
            groups.append({
                "group_id": "",
                "requirement_ids": [requirement["requirement_id"]],
                "binding_ids": sorted(binding_ids),
            })
            continue
        target = groups[matches[0]]
        target["requirement_ids"].append(requirement["requirement_id"])
        target["binding_ids"] = sorted(set(target["binding_ids"]) | binding_ids)
        for index in reversed(matches[1:]):
            merged = groups.pop(index)
            target["requirement_ids"].extend(merged["requirement_ids"])
            target["binding_ids"] = sorted(set(target["binding_ids"]) | set(merged["binding_ids"]))
    for index, group in enumerate(groups, start=1):
        group["group_id"] = f"G{index}"
    return groups


def _component_function(response: InferenceResult, name: str, arguments: tuple[str, ...]) -> ast.FunctionDef:
    source = response.text.strip()
    lines = source.splitlines()
    if len(lines) >= 3 and lines[0].strip() in {"```", "```py", "```python"} \
            and lines[-1].strip() == "```":
        source = "\n".join(lines[1:-1]).strip()
    if not source.startswith(f"def {name}("):
        try:
            module = ast.parse(source)
        except SyntaxError:
            module = None
        if module is not None:
            imports = [node for node in module.body if isinstance(node, ast.Import)]
            functions = [node for node in module.body if isinstance(node, ast.FunctionDef)]
            allowed_modules = {"html", "json", "math", "re"}
            if (len(functions) == 1 and len(imports) + len(functions) == len(module.body)
                    and all(
                        isinstance(node, ast.Import)
                        and all(alias.name in allowed_modules and alias.asname is None for alias in node.names)
                        for node in imports
                    )):
                source = ast.unparse(functions[0]).strip()
        if not source.startswith(f"def {name}("):
            generated = parse_json_object(response.text)
            if set(generated) == {"response_contract"} and isinstance(generated["response_contract"], dict):
                generated = generated["response_contract"]
            if not isinstance(generated.get("source"), str):
                raise ValueError(f"verifier {name} component 必须返回 source")
            source = generated["source"].strip()
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"verifier {name} component 语法错误：{error}") from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError(f"verifier {name} component 只能定义一个函数")
    function = tree.body[0]
    actual_arguments = tuple(item.arg for item in function.args.args)
    if (function.name != name or actual_arguments != arguments or function.decorator_list
            or function.args.posonlyargs or function.args.kwonlyargs or function.args.vararg
            or function.args.kwarg or function.args.defaults or function.args.kw_defaults):
        raise ValueError(f"verifier {name} component 必须定义 {name}({', '.join(arguments)})")
    reporters = {
        "pass_requirement", "fail_requirement", "indeterminate_requirement", "semantic_requirement",
        "candidate_group",
    }
    if any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "ctx"
        and node.func.attr in reporters
        for node in ast.walk(function)
    ):
        raise ValueError(f"verifier {name} component 不得直接记录 requirement")
    if name == "check" and any(isinstance(node, ast.Name) and node.id == "ctx" for node in ast.walk(function)):
        raise ValueError("verifier check component 不得访问 ctx，只能使用 shared observations")
    return function


def _infer_component_batch(
    requests: list[str], llm_config: dict[str, Any], infer_fn: InferFn, attempts: int = 3,
) -> list[InferenceResult]:
    responses: list[InferenceResult | None] = [None] * len(requests)
    pending = list(range(len(requests)))
    errors: dict[int, Exception] = {}
    for _ in range(attempts):
        if not pending:
            break
        try:
            outcomes = infer_fn([requests[index] for index in pending], llm_config=llm_config)
            if not isinstance(outcomes, list) or len(outcomes) != len(pending):
                raise ValueError("verifier requirement checks 返回数量不正确")
        except BatchInferenceError as error:
            if len(error.outcomes) != len(pending):
                raise ValueError("verifier requirement checks 返回数量不正确") from error
            outcomes = list(error.outcomes)

        retry: list[int] = []
        for index, outcome in zip(pending, outcomes):
            if isinstance(outcome, InferenceResult):
                responses[index] = outcome
                errors.pop(index, None)
            elif isinstance(outcome, Exception):
                errors[index] = outcome
                retry.append(index)
            else:
                raise ValueError("verifier requirement check 返回类型不正确")
        pending = retry

    if pending:
        detail = "; ".join(
            f"{index + 1}: {type(errors[index]).__name__}: {errors[index]}" for index in pending
        )
        raise RuntimeError(f"verifier requirement checks 重试失败：{detail}")
    return [response for response in responses if response is not None]


def _assemble_verifier(prepare: ast.FunctionDef, checks: list[ast.FunctionDef], requirement_ids: list[str]) -> str:
    body: list[ast.stmt] = [prepare, *checks, ast.parse("shared = prepare(ctx)").body[0]]
    for index, requirement_id in enumerate(requirement_ids, start=1):
        result = f"result_{index}"
        body.extend(ast.parse(
            f"try:\n"
            f"    {result} = check_{index}(deepcopy(shared))\n"
            f"except Exception as error:\n"
            f"    {result} = {{'status': 'indeterminate', 'reason': 'checker execution failed: ' + str(error), 'evidence_refs': []}}\n"
            f"if not isinstance({result}, dict) or set({result}) != {{'status', 'reason', 'evidence_refs'}}:\n"
            f"    ctx.indeterminate_requirement({requirement_id!r}, 'checker returned an invalid result', [])\n"
            f"elif {result}['status'] == 'pass':\n"
            f"    ctx.pass_requirement({requirement_id!r}, {result}['reason'], {result}['evidence_refs'])\n"
            f"elif {result}['status'] == 'fail':\n"
            f"    ctx.fail_requirement({requirement_id!r}, {result}['reason'], {result}['evidence_refs'])\n"
            f"elif {result}['status'] == 'semantic':\n"
            f"    ctx.semantic_requirement({requirement_id!r}, {result}['reason'], {result}['evidence_refs'])\n"
            f"else:\n"
            f"    ctx.indeterminate_requirement({requirement_id!r}, {result}['reason'], {result}['evidence_refs'])\n"
        ).body)
    module = ast.Module(body=[ast.FunctionDef(
        name="verify",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="ctx")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body,
        decorator_list=[],
    )], type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(module))


def _assemble_witness_verifier(
    prepare: ast.FunctionDef,
    checks: list[ast.FunctionDef],
    groups: list[dict[str, Any]],
    direct_requirement_ids: str | list[str],
    requirement_ids: list[str] | None = None,
) -> str:
    """Assemble checks so one assignment must satisfy every requirement in its group."""
    direct_ids = [direct_requirement_ids] if isinstance(direct_requirement_ids, str) else direct_requirement_ids
    ordered_ids = requirement_ids or [
        requirement_id for group in groups for requirement_id in group["requirement_ids"]
    ] + direct_ids
    check_names = {requirement_id: f"check_{index}" for index, requirement_id in enumerate(ordered_ids, start=1)}
    group_source = "[" + ", ".join(
        "(" + repr(group["group_id"]) + ", " + repr(group["binding_ids"]) + ", [" + ", ".join(
            f"({requirement_id!r}, {check_names[requirement_id]})"
            for requirement_id in group["requirement_ids"]
        ) + "])" for group in groups
    ) + "]"
    body: list[ast.stmt] = [prepare, *checks, *ast.parse(
        "shared = prepare(ctx)\n"
        "prepared_groups = shared.get('groups', {}) if isinstance(shared, dict) else {}\n"
        f"for group_id, binding_ids, requirement_checks in {group_source}:\n"
        "    group = prepared_groups.get(group_id, {})\n"
        "    assignments = group.get('assignments', []) if isinstance(group, dict) else []\n"
        "    complete = group.get('complete') is True if isinstance(group, dict) else False\n"
        "    reason = group.get('reason', 'candidate assignment domain is unavailable') if isinstance(group, dict) else 'candidate assignment domain is unavailable'\n"
        "    evidence_refs = group.get('evidence_refs', []) if isinstance(group, dict) else []\n"
        "    if not isinstance(assignments, list):\n"
        "        assignments = []\n"
        "        complete = False\n"
        "    rows = []\n"
        "    for assignment_index, assignment in enumerate(assignments):\n"
        "        if not isinstance(assignment, dict) or any(binding_id not in assignment for binding_id in binding_ids):\n"
        "            complete = False\n"
        "            continue\n"
        "        row_results = []\n"
        "        for requirement_id, checker in requirement_checks:\n"
        "            try:\n"
        "                result = checker(deepcopy(shared), deepcopy(assignment))\n"
        "            except Exception as error:\n"
        "                result = {'status': 'indeterminate', 'reason': 'checker execution failed: ' + str(error), 'evidence_refs': []}\n"
        "            if not isinstance(result, dict) or set(result) != {'status', 'reason', 'evidence_refs'}:\n"
        "                result = {'status': 'indeterminate', 'reason': 'checker returned an invalid result', 'evidence_refs': []}\n"
        "            elif result['status'] not in ('pass', 'fail', 'indeterminate', 'semantic'):\n"
        "                result = {'status': 'indeterminate', 'reason': 'checker returned an invalid status', 'evidence_refs': []}\n"
        "            row_results.append({'requirement_id': requirement_id, **result})\n"
        "        rows.append({'assignment_id': str(assignment_index), 'results': row_results})\n"
        "    ctx.candidate_group(group_id, [item[0] for item in requirement_checks], complete, reason, evidence_refs, rows)\n"
    ).body]
    for requirement_id in direct_ids:
        check_name = check_names[requirement_id]
        body.extend(ast.parse(
            f"try:\n"
            f"    result = {check_name}(deepcopy(shared), {{}})\n"
            f"except Exception as error:\n"
            f"    result = {{'status': 'indeterminate', 'reason': 'checker execution failed: ' + str(error), 'evidence_refs': []}}\n"
            f"if not isinstance(result, dict) or set(result) != {{'status', 'reason', 'evidence_refs'}}:\n"
            f"    ctx.indeterminate_requirement({requirement_id!r}, 'checker returned an invalid result', [])\n"
            f"elif result['status'] == 'pass':\n"
            f"    ctx.pass_requirement({requirement_id!r}, result['reason'], result['evidence_refs'])\n"
            f"elif result['status'] == 'fail':\n"
            f"    ctx.fail_requirement({requirement_id!r}, result['reason'], result['evidence_refs'])\n"
            f"elif result['status'] == 'semantic':\n"
            f"    ctx.semantic_requirement({requirement_id!r}, result['reason'], result['evidence_refs'])\n"
            f"else:\n"
            f"    ctx.indeterminate_requirement({requirement_id!r}, result['reason'], result['evidence_refs'])\n"
        ).body)
    module = ast.Module(body=[ast.FunctionDef(
        name="verify",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="ctx")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body,
        decorator_list=[],
    )], type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(module))


def generate_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    infer_fn: InferFn = infer,
    previous_failure: str | list[dict[str, Any]] | None = None,
    *,
    specification: dict[str, Any] | None = None,
    proof_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the model for a constrained verifier package, then validate it."""
    if specification is not None:
        validate_verification_spec(specification)
        if proof_plan is None:
            raise ValueError("冻结规格必须提供 proof_plan")
        validate_proof_plan(proof_plan, specification)
        witness_groups = _witness_groups(proof_plan)
        grouped_ids = {
            requirement_id for group in witness_groups for requirement_id in group["requirement_ids"]
        }
        requirement_ids = [item["id"] for item in specification["requirements"]]
        direct_ids = [requirement_id for requirement_id in requirement_ids if requirement_id not in grouped_ids]
        principles = [
            "先把冻结规格和 proof_plan 当作唯一判定契约：prepare 只收集、解析和标注观察，check 只对一项 claim 判定。",
            "确定性结构、数值、ID、数量和状态由代码比较；自然语言含义才返回 semantic。",
            "任何 pass 都必须引用 shared 中直接证明 claim 的证据；reason 只能陈述 evidence_refs 实际显示的事实。",
            "持久状态要求必须使用初态和终态；最终回答不能替代落盘结果。",
            "call_tool 只能核验已有状态，不能替 Agent 补做任务，也不能单独证明 Agent 已交付。",
            "参考执行只用于定位证据；不得把参考 ID、路径、调用顺序、工具选择、表示方式或措辞变成条件。",
            "环境契约明确声明的路径、字段、枚举和稳定标识可以直接使用；不得猜测未声明的容器、字段或格式。",
            "Schema 完整的只读工具或明确数据契约优先；不得猜测环境未声明的容器、字段或格式。",
            "proof_plan 中的 binding 是逻辑变量；先枚举业务类型范围内所有可观察候选，再形成 assignment，不得用被审核属性预筛正确答案。",
            "任务以单数指代的评论、附件、关联或其他子实体必须分别绑定；assignment 要保留实体关系，不能在父对象的集合字段中跨成员拼接要求。",
            "同一 witness group 的所有 requirement 必须针对同一个 assignment 判断；不能让不同候选分别满足其中一部分要求。",
            "选择一个通过的 assignment 只是存在性见证，不限制环境中同类对象的总数。",
            "完整证据明确反驳要求时必须 fail；缺失、不可读、partial 或冲突时才 indeterminate。",
            "执行完整性必须保留集合数量和重复项，并判断每个额外副作用是否与任务相关、无关、破坏性或冲突。",
            "prepare 可以使用 ctx 读取证据和调用核验工具，但不得记录 requirement；每次核验调用都必须保留稳定 evidence_ref。",
            "check 的签名必须是 check(shared, assignment)，只能判断传入的一个 assignment，不能搜索或改选其他候选，也不能访问 ctx、调用工具、读取文件、写文件或使用私有状态。",
            "组件源码不要包含 import；运行时已提供 json、re、html、math、deepcopy 和 Path。",
            "check 必须返回一个结果对象，不得自行记录 requirement；运行时会隔离每个 check，异常只影响该项并转为 indeterminate。",
        ]
        context_api = {
            "answer()": "实际最终回答",
            "calls()": "实际工具调用及结果",
            "changed_paths()": "初末 workspace 变化",
            "files(state='final') / file(path, state='final')": "文件证据元数据",
            "read_text(path, state='final') / read_json(path, state='final')": "完整初末 workspace 只读访问",
            "call_tool(name, arguments) / verifier_calls()": "在终态副本补充核验",
        }
        shared_request: dict[str, Any] = {
            "component": "shared_preparation",
            "role": (
                "实现 verifier 的共享证据准备函数。只读取证据、解析业务对象和 proof_plan bindings，"
                "返回供各项检查共同使用的 observations、候选集合、绑定、完整性和 evidence_refs；"
                "不得选择唯一见证、判断或记录任何 requirement。"
            ),
            "implementation_principles": [
                *principles,
                "业务对象身份不得依赖正在审核的属性；共享结果必须保留候选集合、无法区分、明确缺失、读取失败和冲突状态。",
                "返回 dict 必须包含 groups；每个固定 group_id 对应 assignments、complete、reason、evidence_refs，assignments 不得静默截断。",
                "每个 assignment 必须为该 group 的每个 binding_id 提供一个候选值，并只保留环境证据支持的实体关系组合。",
                "complete 只有在候选域被完整观察时才为 true；读取失败、分页未穷尽或来源 partial 时必须为 false。",
                "source 只能定义 prepare(ctx)，并返回一个 dict；不得调用任何 requirement 记录方法。",
            ],
            "environment": environment,
            "specification": specification,
            "proof_plan": proof_plan,
            "witness_groups": witness_groups,
            "verifier_context_api": context_api,
            "response_contract": {"source": "Python source defining prepare(ctx)"},
        }
        if previous_failure:
            shared_request["previous_issues"] = previous_failure
        shared_response = infer_fn(
            json.dumps(shared_request, ensure_ascii=False), llm_config=llm_config,
        )
        if not isinstance(shared_response, InferenceResult):
            raise ValueError("verifier shared preparation 必须返回单条结果")
        prepare = _component_function(shared_response, "prepare", ("ctx",))

        plans = {item["requirement_id"]: item for item in proof_plan.get("requirements", [])}
        integrity_plan = proof_plan.get("integrity", {})
        if "requirement_id" in integrity_plan:
            plans[integrity_plan["requirement_id"]] = integrity_plan
        check_requests: list[str] = []
        for requirement in specification["requirements"]:
            request: dict[str, Any] = {
                "component": "requirement_check",
                "role": (
                    "只实现给定的一项冻结 requirement。使用共享准备结果判断这一项，不得检查或记录其他项。"
                    "返回 status、reason、evidence_refs；status 只能是 pass、fail、indeterminate 或 semantic。"
                ),
                "implementation_principles": principles,
                "environment": environment,
                "requirement": requirement,
                "proof_plan": plans.get(requirement["id"], {}),
                "witness_group": next((
                    group for group in witness_groups if requirement["id"] in group["requirement_ids"]
                ), None),
                "shared_source": ast.unparse(prepare),
                "shared_contract": {
                    "observations": "已读取或核验的原始事实及其 complete|partial|unreadable|conflict 状态",
                    "groups": "按固定 group_id 保存 assignments、complete、reason、evidence_refs",
                    "assignment": "运行时单独传给 check 的 binding_id 到一个候选对象的映射",
                    "evidence": "每个观察的来源、完整性和稳定 evidence_ref",
                    "errors": "读取或核验失败，不得静默当作空结果",
                },
                "evidence_reference_formats": [
                    "answer", "tool_call:N", "initial:relative/path", "final:relative/path",
                    "workspace_change:relative/path", "verifier_call:N",
                ],
                "response_contract": {
                    "source": (
                        "Python source defining check(shared, assignment), returning exactly "
                        "{'status': 'pass|fail|indeterminate|semantic', 'reason': str, 'evidence_refs': list[str]}"
                    ),
                },
            }
            if previous_failure:
                relevant = [
                    issue for issue in previous_failure if not isinstance(issue, dict)
                    or not issue.get("requirement_ids") or requirement["id"] in issue["requirement_ids"]
                ] if isinstance(previous_failure, list) else previous_failure
                if relevant:
                    request["previous_issues"] = relevant
            check_requests.append(json.dumps(request, ensure_ascii=False))
        check_responses = _infer_component_batch(check_requests, llm_config, infer_fn)
        checks: list[ast.FunctionDef] = []
        for index, response in enumerate(check_responses, start=1):
            if not isinstance(response, InferenceResult):
                raise ValueError("verifier requirement check 返回类型不正确")
            function = _component_function(response, "check", ("shared", "assignment"))
            function.name = f"check_{index}"
            checks.append(function)
        package = {
            "schema_version": "1",
            "requirements": _frozen_requirements(specification),
            "source": _assemble_witness_verifier(
                prepare, checks, witness_groups, direct_ids, requirement_ids,
            ),
        }
        validate_verifier(package)
        return package

    request = {
            "role": "你为一次真实任务执行生成审核 verifier。先把任务拆成业务 claims，再为每个 claim 定义可审计证据和边界；不要把执行方法当成成功条件。",
            "judging_principles": [
                "原任务文本是唯一成功标准；参考回答、调用链和终态只是一条可行证据路径，不是标准答案。",
                "只判断任务要求的结果和状态变化；工具、顺序、次数和内部表示只有在任务明确要求时才是条件。",
                "按任务动词区分创建、修改、删除、保持、查询、计算和呈现；持久结果看最终状态，查询和呈现可结合工具结果与回答。",
                "每个 claim 的证据必须绑定到同一业务对象、交付物、范围和字段，不能拼接其他对象的同名信息。",
                "业务身份独立于技术表示；任务未指定时不要求动态 ID、内部路径、序列化格式、字段承载方式、顺序或参考措辞。",
                "确定性事实用代码严格核对；自然语言含义和等价表达才使用 semantic_requirement。",
                "任务要求实质交付时，空字段、占位内容或只声称完成不能通过，除非任务明确允许。",
                "证据不足、截断或冲突时 indeterminate；明确反驳时 fail；reason 只能陈述引用证据显示的事实。",
            ],
            "construction_requirements": [
                "先完整拆出全部原子 claim：并集覆盖任务，每项只表达一个可独立判断的目标，并具有唯一 id。",
                "除任务原子要求外，增加一项 required 的执行完整性要求，检查实际状态变化是否都与任务目标相关且没有破坏无关状态。",
                "为每项给出 claim、evidence_channels、pass_condition 和 fail_condition。",
                "source 只能定义 verify(ctx)，只能调用 VerifierContext 的公开方法；不得导入模块、启动进程、写文件或访问私有状态。",
                "不得硬编码参考执行中的偶然值。路径和数据结构只能使用 reference_evidence 明确提供的事实，不得猜测。",
                "凡把相对初态新增、删除、变化或保持作为条件，必须同时引用相关 initial 和 final 证据。",
                "自然语言含义使用 semantic_requirement；确定性事实直接用 source 判断，不得用关键词或参考措辞子串替代语义判断。",
                "semantic_requirement 会直接记录该 requirement 的结果请求且没有返回值；调用后不得再用 pass/fail/indeterminate 重复记录同一 id，也不得把它放进布尔表达式。",
                "call_tool 只能补充核验已有状态或核对 Agent 已提交的结果，不能单独证明 Agent 执行过任务或交付了任务要求的结果；查询、计算或呈现型要求仍须同时引用 Agent 的原始调用、最终回答或实际状态证据。",
                "source 每条执行路径必须恰好记录每项 requirement 一次；evidence_refs 必须使用规定格式，并指向实际引用的证据。",
                "source 保持紧凑；空回答、无工具调用且 workspace 无变化时，除保持不变的单项要求外，任务不得整体通过。",
                "只输出符合 schema 的 JSON，不要 markdown。",
            ],
            "runtime_capabilities": [
                "verifier 可以通过 read_text/read_json 只读访问完整的 initial 和 final workspace；路径必须是 workspace 内的相对路径。",
                "verifier 可以通过 call_tool 在 final workspace 的一次性副本中调用环境工具；每次调用使用独立副本，任何写入都不会改变被审核终态。审核调用只能核验，不能替 Agent 补做任务或单独证明 Agent 已交付结果。",
                "工具调用是可引用证据而不是标准答案；任务未要求时，不能强制特定工具、顺序或次数。",
                "最终回答不能替代任务明确要求写入文件或持久业务状态的证据。",
            ],
            "task": task.get("task_text"),
            "environment": environment,
            "reference_evidence": _generation_evidence(reference_evidence),
            "verifier_context_api": {
                "answer()": "返回实际最终回答字符串",
                "calls()": "返回实际工具调用数组",
                "changed_paths()": "返回 workspace 变化数组",
                "files(state='final')": "返回 initial 或 final 文件元数据数组",
                "file(path, state='final')": "按相对路径返回文件元数据或 None",
                "read_text(path, state='final')": "返回有界 UTF-8 文本或 None",
                "read_json(path, state='final')": "从完整 initial/final workspace 读取 JSON，失败时返回 None",
                "call_tool(name, arguments)": "在 final 的独立副本调用环境工具，返回 tool/arguments/result/error 记录",
                "verifier_calls()": "返回当前 verifier 已执行的补充工具调用记录",
                "pass_requirement(id, reason, refs)": "记录确定通过",
                "fail_requirement(id, reason, refs)": "记录确定失败",
                "indeterminate_requirement(id, reason, refs=[])": "记录证据不足",
                "semantic_requirement(id, claim, refs)": "请求模型对一个语义命题判断",
            },
            "evidence_reference_formats": [
                "answer", "tool_call:N", "initial:relative/path", "final:relative/path",
                "workspace_change:relative/path", "verifier_call:N",
            ],
            "response_contract": {
                "schema_version": "1",
                "requirements": [{
                    "id": "R1",
                    "claim": "一个可独立判断的任务要求",
                    "required": True,
                    "evidence_channels": ["answer", "tool_trace", "workspace"],
                    "pass_condition": "充分证明该要求成立的条件",
                    "fail_condition": "充分证明该要求未成立的条件",
                }],
                "source": "Python source defining verify(ctx)",
            },
        }
    if previous_failure:
        request["previous_failure"] = previous_failure
    response = infer_fn(
        json.dumps(request, ensure_ascii=False),
        llm_config=llm_config,
    )
    package = parse_json_object(response.text)
    if set(package) == {"response_contract"} and isinstance(package["response_contract"], dict):
        package = package["response_contract"]
    if set(package) == {"requirements", "source"}:
        package = {"schema_version": "1", **package}
    validate_verifier(package)
    return package


def review_verifier_implementation(
    specification: dict[str, Any],
    package: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
    proof_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review whether source faithfully and sufficiently implements the frozen spec."""
    validate_verification_spec(specification)
    validate_verifier(package)
    if package["requirements"] != _frozen_requirements(specification):
        raise ValueError("verifier requirements 与冻结规格不一致")
    if proof_plan is not None:
        validate_proof_plan(proof_plan, specification)
    request = {
        "role": (
            "你独立审核 verifier 实现，不修改代码或规格。逐项检查所有控制流：代码是否忠实实现冻结"
            "条件、是否可能漏报或重复报告、是否在证据不足时误 pass、是否把参考执行的偶然细节"
            "变成硬条件。只有没有实质缺陷时才能 approved=true。"
        ),
        "review_dimensions": [
            "Each requirement is reported exactly once on every reachable path.",
            "Every pass path cites evidence sufficient for the frozen pass condition.",
            "Fail and indeterminate paths follow the frozen evidence boundaries.",
            "The runtime contract is part of the implementation: prepare(ctx) enumerates assignments, each check(shared, assignment) evaluates exactly one assignment, and checker exceptions become indeterminate without aborting later checks.",
            "Shared preparation preserves candidate multiplicity, identity evidence, completeness states, errors, and stable evidence references; it does not choose a witness or produce a verdict.",
            "A check(shared, assignment) cannot access ctx, call tools, read or write files, search other candidates, or use private runtime state; it returns only one result object for its assigned requirement.",
            "No reference-specific tool, order, generated identifier, path, representation, or wording is required.",
            "Paths, fields, enums, and stable identifiers declared by the current environment contract are required specialization, not reference overfitting.",
            "Prefer schema-defined tools or explicit data contracts over guessed file representations; reject code that assumes undeclared containers or fields.",
            "Distinguish complete evidence that disproves a requirement from missing or unreadable evidence; do not collapse both into indeterminate.",
            "Integrity checks preserve multiplicity and classify every extra side effect as task-related, unrelated, destructive, or conflicting without requiring a minimal execution path.",
            "Semantic judgments are not replaced by substring heuristics; deterministic facts are not delegated unnecessarily.",
        ],
        "runtime_contract": {
            "prelude": "Generated verifier source runs with json, re, html, math, deepcopy, and Path already loaded; component imports are removed before assembly.",
            "stages": "verify(ctx) calls prepare(ctx) once, evaluates every requirement against each assignment in its witness group, then selects one consistent assignment mechanically.",
            "isolation": "A checker exception or malformed result becomes indeterminate for that requirement only; later requirements still run.",
            "evidence": "Verifier tool records expose stable verifier_call:N evidence references.",
        },
        "specification": specification,
        "proof_plan": proof_plan,
        "verifier": package,
        "environment": environment,
        "reference_evidence": _generation_evidence(reference_evidence),
        "response_contract": {
            "approved": "boolean；仅当 issues 为空时为 true",
            "issues": [{
                "code": "missing_result_path|duplicate_result_path|unsupported_pass|wrong_failure_boundary|reference_overfit|wrong_semantic_boundary|other",
                "task_clause_ids": ["C1"],
                "requirement_ids": ["R1"],
                "message": "具体说明哪条控制流或判断不忠实，以及为什么",
            }],
        },
    }
    review = parse_json_object(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text)
    _validate_review(review, kind="implementation")
    clause_ids = {item["id"] for item in specification["task_clauses"]}
    requirement_ids = {item["id"] for item in specification["requirements"]}
    for issue in review["issues"]:
        if (set(issue["task_clause_ids"]) - clause_ids
                or set(issue["requirement_ids"]) - requirement_ids):
            raise ValueError("implementation review 引用了未知 clause 或 requirement")
    return review


def _generation_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep all file metadata but only changed-file contents in the generation prompt."""
    changed = {item.get("path") for item in evidence.get("changed_paths", [])}
    compact = {key: value for key, value in evidence.items() if key not in {"initial_files", "final_files"}}
    for key in ("initial_files", "final_files"):
        compact[key] = [
            item if item.get("path") in changed else {
                field: value for field, value in item.items() if field not in {"text", "json", "truncated"}
            }
            for item in evidence.get(key, [])
        ]
    return compact


def _counterfactual_evidence(evidence: dict[str, Any], task_text: str) -> dict[str, Any]:
    """Change incidental identifiers and reference formatting without changing business state."""
    variant = json.loads(json.dumps(evidence, ensure_ascii=False))
    stable_ids: set[str] = set()
    initial_objects: set[str] = set()

    def collect_ids(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            initial_objects.add(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            for item_key, item in value.items():
                collect_ids(item, item_key)
        elif isinstance(value, list):
            for item in value:
                collect_ids(item, key)
        elif key == "id" or key.endswith("_id") or key.endswith("_ids"):
            stable_ids.add(str(value))

    for record in variant.get("initial_files", []):
        if "json" in record:
            collect_ids(record["json"])

    def changed_id(value: Any) -> Any:
        if str(value) in stable_ids or str(value) in task_text:
            return value
        if isinstance(value, str):
            return "counterfactual_" + value
        if type(value) is int:
            return value + 1_000_000_000
        return value

    def rewrite(value: Any, key: str = "", move_reference: bool = False) -> Any:
        if isinstance(value, dict):
            original = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            rewritten = {
                item_key: rewrite(item, item_key, move_reference)
                for item_key, item in value.items()
            }
            if (move_reference and original not in initial_objects
                    and isinstance(value.get("source_reference"), str)
                    and isinstance(value.get("description"), str)):
                reference = value["source_reference"]
                rewritten["source_reference"] = "counterfactual_reference"
                rewritten["description"] = f"{value['description']} [references: {reference}]"
            return rewritten
        if isinstance(value, list):
            if key.endswith("_ids"):
                return [changed_id(item) for item in value]
            return [rewrite(item, key, move_reference) for item in value]
        if key == "source_reference" and isinstance(value, str):
            return f"trace[{value}]"
        if (key == "id" or key.endswith("_id")) and isinstance(value, (str, int)) and not isinstance(value, bool):
            return changed_id(value)
        return value

    variant["calls"] = rewrite(variant.get("calls", []), move_reference=True)
    for state in ("initial_files", "final_files"):
        for record in variant.get(state, []):
            if "json" in record:
                record["json"] = rewrite(record["json"], move_reference=state == "final_files")
                record["text"] = json.dumps(record["json"], ensure_ascii=False)
                record["truncated"] = False
    return variant


def _confirm_results_semantically(
    package: dict[str, Any],
    results: list[dict[str, Any]],
    evidence: dict[str, Any],
    infer_fn: InferFn,
    llm_config: dict[str, Any] | None,
    task_text: str,
) -> list[dict[str, Any]]:
    requirements = {item["id"]: item for item in package["requirements"]}
    prompts = [
        _semantic_prompt(
            {**result, "reason": requirements[result["requirement_id"]]["claim"]},
            evidence,
            requirements[result["requirement_id"]],
            task_text,
        )
        for result in results
    ]
    responses = infer_fn(prompts if len(prompts) > 1 else prompts[0], llm_config=llm_config or {})
    if isinstance(responses, InferenceResult):
        responses = [responses]
    if not isinstance(responses, list) or len(responses) != len(results):
        raise ValueError("语义二次确认返回数量与请求不一致")
    confirmed = []
    for result, response in zip(results, responses):
        semantic = _parse_semantic_result(response.text)
        if semantic["status"] not in _STATUSES or not semantic["reason"].strip():
            raise ValueError("语义二次确认结果结构非法")
        confirmed.append({**result, **semantic})
    return confirmed


def _evidence_channel(reference: str) -> str | None:
    if reference == "answer":
        return "answer"
    if reference.startswith(("tool_call:", "verifier_call:")):
        return "tool_trace"
    if reference.startswith(("initial:", "final:", "workspace_change:")):
        return "workspace"
    return None


def _ablate_evidence(evidence: dict[str, Any], channel: str) -> dict[str, Any]:
    ablated = json.loads(json.dumps(evidence, ensure_ascii=False))
    ablated.pop("verifier_calls", None)
    if channel == "answer":
        ablated["answer"] = ""
    elif channel == "tool_trace":
        ablated["calls"] = []
    elif channel == "workspace":
        ablated["changed_paths"] = []
        ablated["initial_files"] = []
        ablated["final_files"] = []
    else:
        raise ValueError(f"未知证据渠道：{channel}")
    return ablated


def calibrate_verifier(
    package: dict[str, Any],
    reference_evidence: dict[str, Any],
    empty_evidence: dict[str, Any],
    *,
    llm_config: dict[str, Any] | None = None,
    infer_fn: InferFn = infer,
    task_text: str | None = None,
    confirm_all: bool = False,
    initial_state: Path | None = None,
    final_state: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
    specification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require the reference to pass and empty evidence not to pass required criteria."""
    reference_results = run_verifier(
        package, reference_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
        task_text=task_text,
        initial_state=initial_state, final_state=final_state, tools=tools,
    )
    reference_raw = aggregate_results(package["requirements"], reference_results)
    confirmation = None
    if confirm_all:
        confirmation_results = _confirm_results_semantically(
            package, reference_results, reference_evidence, infer_fn, llm_config, task_text or "",
        )
        confirmation = aggregate_results(package["requirements"], confirmation_results)
        reference = confirmation
    elif "semantic_requirement" in package["source"]:
        confirmation_results = run_verifier(
            package, reference_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
            task_text=task_text, initial_state=initial_state, final_state=final_state, tools=tools,
        )
        confirmation = aggregate_results(package["requirements"], confirmation_results)
        reference = confirmation
    else:
        reference = reference_raw
    if reference["outcome"] != "pass":
        raise ReferenceCalibrationError("参考执行语义二次确认未通过：" + json.dumps(
            confirmation_results if confirmation is not None else reference_results, ensure_ascii=False,
        ))
    counterfactual = None
    if task_text is not None:
        counterfactual_evidence = _counterfactual_evidence(reference_evidence, task_text)
        counterfactual_results = run_verifier(
            package, counterfactual_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
            task_text=task_text,
            initial_state=initial_state, final_state=final_state, tools=tools,
        )
        counterfactual = aggregate_results(package["requirements"], counterfactual_results)
        if counterfactual["outcome"] != "pass":
            raise ValueError("参考执行反事实校准未通过：" + json.dumps(counterfactual_results, ensure_ascii=False))
    empty_results = run_verifier(
        package, empty_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
        task_text=task_text,
        initial_state=initial_state, final_state=initial_state, tools=tools,
    )
    empty = aggregate_results(package["requirements"], empty_results)
    required = {item["id"] for item in package["requirements"] if item["required"]}
    files = {
        state: {item.get("path"): item.get("sha256") for item in empty_evidence.get(f"{state}_files", [])}
        for state in ("initial", "final")
    }

    def proves_preservation(result: dict[str, Any]) -> bool:
        refs = result["evidence_refs"]
        if not refs or any(not ref.startswith(("initial:", "final:")) for ref in refs):
            return False
        initial = {ref[8:] for ref in refs if ref.startswith("initial:")}
        final = {ref[6:] for ref in refs if ref.startswith("final:")}
        return bool(initial) and initial == final and all(
            files["initial"].get(path) == files["final"].get(path) is not None for path in initial
        )

    invalid_empty = [
        item["requirement_id"] for item in empty_results
        if item["requirement_id"] in required and item["status"] == "pass" and not proves_preservation(item)
    ]
    if invalid_empty:
        raise ValueError("空证据错误通过非保留型 required 要求：" + ", ".join(invalid_empty))
    if empty["outcome"] == "pass":
        raise ValueError("空证据错误通过了整个任务")
    ablations: list[dict[str, Any]] = []
    if specification is not None:
        validate_verification_spec(specification)
        spec_requirements = {item["id"]: item for item in specification["requirements"]}
        if package["requirements"] != _frozen_requirements(specification):
            raise ValueError("verifier requirements 与消融规格不一致")
        channels = sorted({
            channel
            for requirement in specification["requirements"]
            if requirement["outcome_type"] != "preservation"
            for channel in requirement["evidence_channels"]
        })
        for channel in channels:
            ablated_evidence = _ablate_evidence(reference_evidence, channel)
            try:
                ablated_results = run_verifier(
                    package,
                    ablated_evidence,
                    semantic_infer_fn=infer_fn,
                    llm_config=llm_config,
                    task_text=task_text,
                    initial_state=None if channel == "workspace" else initial_state,
                    final_state=None if channel == "workspace" else final_state,
                    tools=[] if channel == "tool_trace" else tools,
                )
            except Exception as error:
                raise ValueError(f"证据消融 {channel} 执行失败：{error}") from error
            affected = {
                item["id"] for item in specification["requirements"]
                if item["outcome_type"] != "preservation" and channel in item["evidence_channels"]
            }
            survivors = [
                item for item in ablated_results
                if item["requirement_id"] in affected and item["status"] == "pass"
            ]
            unsupported: list[str] = []
            semantically_reviewable: list[dict[str, Any]] = []
            for result in survivors:
                remaining = set(spec_requirements[result["requirement_id"]]["evidence_channels"]) - {channel}
                cited = {_evidence_channel(ref) for ref in result["evidence_refs"]}
                if not result["evidence_refs"] or None in cited or not cited <= remaining:
                    unsupported.append(result["requirement_id"])
                else:
                    semantically_reviewable.append(result)
            if semantically_reviewable:
                confirmed = _confirm_results_semantically(
                    package, semantically_reviewable, ablated_evidence,
                    infer_fn, llm_config, task_text or "",
                )
                unsupported.extend(
                    item["requirement_id"] for item in confirmed if item["status"] != "pass"
                )
            if unsupported:
                raise ValueError(
                    f"证据消融 {channel} 后要求仍无充分证据通过：" + ", ".join(sorted(set(unsupported)))
                )
            ablations.append({
                "channel": channel,
                "results": ablated_results,
                "independently_supported": [
                    item["requirement_id"] for item in semantically_reviewable
                ],
            })
    return {
        "reference_raw": reference_raw,
        "reference": reference,
        "reference_confirmation": confirmation,
        "reference_verifier_calls": reference_evidence.get("verifier_calls", []),
        "counterfactual": counterfactual,
        "counterfactual_verifier_calls": (
            counterfactual_evidence.get("verifier_calls", []) if task_text is not None else []
        ),
        "empty": empty,
        "empty_verifier_calls": empty_evidence.get("verifier_calls", []),
        "ablations": ablations,
        "status": "calibrated",
    }


def assess_task_reference_conflict(
    task: dict[str, Any],
    specification: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    infer_fn: InferFn = infer,
) -> dict[str, Any]:
    """Determine whether task authority and known-good evidence are themselves incompatible."""
    validate_verification_spec(specification)
    request = {
        "role": (
            "你判断任务文本与参考证据本身是否冲突。忽略任何 verifier 实现及其判断，只逐项检查"
            "任务明确要求的业务结果是否被参考证据直接反驳，或参考证据是否根本无法证明该结果。"
            "只有能指出具体任务条款和具体证据时才能报告冲突；证据只是采用了不同工具、顺序、"
            "动态 ID、存储表示或等价措辞，不构成冲突。"
        ),
        "task": task.get("task_text"),
        "specification": specification,
        "reference_evidence": _generation_evidence(reference_evidence),
        "response_contract": {
            "conflict": "boolean；仅当 issues 非空时为 true",
            "issues": [{
                "requirement_id": "R1",
                "task_clause_ids": ["C1"],
                "claim": "发生冲突的任务要求",
                "evidence_refs": ["final:relative/path 或 tool_call:N 或 answer"],
                "message": "参考证据如何明确反驳或无法证明该要求",
            }],
        },
    }
    assessment = parse_json_object(infer_fn(
        json.dumps(request, ensure_ascii=False), llm_config=llm_config,
    ).text)
    if not isinstance(assessment, dict) or set(assessment) != {"conflict", "issues"}:
        raise ValueError("task-reference conflict assessment 结构非法")
    if not isinstance(assessment["conflict"], bool) or not isinstance(assessment["issues"], list):
        raise ValueError("task-reference conflict assessment 字段非法")
    if assessment["conflict"] != bool(assessment["issues"]):
        raise ValueError("task-reference conflict assessment 的 conflict 与 issues 矛盾")
    requirements = {item["id"]: item for item in specification["requirements"]}
    for issue in assessment["issues"]:
        if not isinstance(issue, dict) or set(issue) != {
            "requirement_id", "task_clause_ids", "claim", "evidence_refs", "message",
        }:
            raise ValueError("task-reference conflict issue 结构非法")
        requirement = requirements.get(issue["requirement_id"])
        if (requirement is None or not isinstance(issue["task_clause_ids"], list)
                or set(issue["task_clause_ids"]) - set(requirement["task_clause_ids"])
                or not isinstance(issue["claim"], str) or not issue["claim"].strip()
                or not isinstance(issue["message"], str) or not issue["message"].strip()
                or not isinstance(issue["evidence_refs"], list) or not issue["evidence_refs"]
                or any(not isinstance(ref, str) or not _valid_evidence_ref(ref, reference_evidence)
                       for ref in issue["evidence_refs"])):
            raise ValueError("task-reference conflict issue 内容非法")
    return assessment


def _prepare_verifier_legacy(
    task: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    empty_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    attempts: int = 5,
    infer_fn: InferFn = infer,
    initial_state: Path | None = None,
    final_state: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Freeze a reviewed specification, then generate and calibrate its implementation."""
    if attempts < 1:
        raise ValueError("attempts 必须大于 0")
    # 规格和证明只判断任务语义与可观察证据；完整工具 Schema 只在生成可执行
    # verifier 时需要。把两者分开可避免把大量无关参数定义重复塞进每次审查请求。
    design_environment = {
        key: value for key, value in environment.items() if key != "tools"
    }
    history: list[dict[str, Any]] = []
    previous_spec_issues: list[dict[str, Any]] | None = None
    specification: dict[str, Any] | None = None
    specification_review: dict[str, Any] | None = None
    for index in range(attempts):
        candidate: dict[str, Any] | None = None
        try:
            candidate = generate_verification_spec(
                task, design_environment, llm_config,
                infer_fn=infer_fn,
                previous_issues=previous_spec_issues,
            )
            review = review_verification_spec(
                task, design_environment, candidate, llm_config, infer_fn=infer_fn,
            )
        except Exception as error:
            issue = {
                "code": "invalid_specification",
                "task_clause_ids": [],
                "requirement_ids": [],
                "message": f"{type(error).__name__}: {error}"[:4000],
            }
            previous_spec_issues = [issue]
            history.append({
                "stage": "specification",
                "attempt": index + 1,
                "error": issue["message"],
                "specification": candidate,
                "review": None,
            })
            continue
        history.append({
            "stage": "specification",
            "attempt": index + 1,
            "error": None if review["approved"] else json.dumps(review["issues"], ensure_ascii=False),
            "specification": candidate,
            "review": review,
        })
        if review["approved"]:
            specification = candidate
            specification_review = review
            break
        previous_spec_issues = review["issues"]
    if specification is None or specification_review is None:
        raise VerifierPreparationError(history)

    previous_plan_issues: list[dict[str, Any]] | None = None
    proof_plan: dict[str, Any] | None = None
    proof_plan_review: dict[str, Any] | None = None
    for index in range(attempts):
        candidate_plan: dict[str, Any] | None = None
        try:
            candidate_plan = generate_proof_plan(
                task, design_environment, specification, reference_evidence, llm_config,
                infer_fn=infer_fn, previous_issues=previous_plan_issues,
            )
            review = review_proof_plan(
                task, design_environment, specification, candidate_plan, reference_evidence,
                llm_config, infer_fn=infer_fn,
            )
        except Exception as error:
            issue = {
                "code": "invalid_proof_plan",
                "task_clause_ids": [],
                "requirement_ids": [],
                "message": f"{type(error).__name__}: {error}"[:4000],
            }
            previous_plan_issues = [*(previous_plan_issues or []), issue]
            history.append({
                "stage": "proof_plan", "attempt": index + 1,
                "error": issue["message"], "proof_plan": candidate_plan, "review": None,
            })
            continue
        history.append({
            "stage": "proof_plan", "attempt": index + 1,
            "error": None if review["approved"] else json.dumps(review["issues"], ensure_ascii=False),
            "proof_plan": candidate_plan, "review": review,
        })
        if review["approved"]:
            proof_plan = candidate_plan
            proof_plan_review = review
            break
        previous_plan_issues = [*(previous_plan_issues or []), *review["issues"]]
    if proof_plan is None or proof_plan_review is None:
        raise VerifierPreparationError(history)

    previous_impl_issues: list[dict[str, Any]] | None = None
    for index in range(attempts):
        package: dict[str, Any] | None = None
        implementation_review: dict[str, Any] | None = None
        try:
            package = generate_verifier(
                task, environment, reference_evidence, llm_config, infer_fn,
                previous_failure=previous_impl_issues,
                specification=specification,
                proof_plan=proof_plan,
            )
            implementation_review = review_verifier_implementation(
                specification, package, environment, reference_evidence,
                llm_config, infer_fn=infer_fn, proof_plan=proof_plan,
            )
            if not implementation_review["approved"]:
                previous_impl_issues = implementation_review["issues"]
                history.append({
                    "stage": "implementation",
                    "attempt": index + 1,
                    "error": json.dumps(previous_impl_issues, ensure_ascii=False),
                    "verifier": package,
                    "review": implementation_review,
                })
                continue
            calibration = calibrate_verifier(
                package, reference_evidence, empty_evidence,
                llm_config=llm_config, infer_fn=infer_fn,
                task_text=str(task.get("task_text") or ""),
                confirm_all=True,
                initial_state=initial_state,
                final_state=final_state,
                tools=tools,
                specification=specification,
            )
        except ReferenceCalibrationError as error:
            assessment: dict[str, Any] | None = None
            assessment_error: str | None = None
            try:
                assessment = assess_task_reference_conflict(
                    task, specification, reference_evidence, llm_config, infer_fn=infer_fn,
                )
            except Exception as conflict_error:
                assessment_error = f"{type(conflict_error).__name__}: {conflict_error}"[:4000]
            issue = {
                "code": "reference_calibration_failure",
                "task_clause_ids": [],
                "requirement_ids": [],
                "message": str(error)[:4000],
            }
            history.append({
                "stage": "implementation",
                "attempt": index + 1,
                "error": issue["message"],
                "verifier": package,
                "review": implementation_review,
                "conflict_assessment": assessment,
                "conflict_assessment_error": assessment_error,
            })
            if assessment is not None and assessment["conflict"]:
                raise TaskReferenceConflictError(history, assessment) from error
            previous_impl_issues = [issue]
            continue
        except Exception as error:
            issue = {
                "code": "verifier_generation_error",
                "task_clause_ids": [],
                "requirement_ids": [],
                "message": f"{type(error).__name__}: {error}"[:4000],
            }
            previous_impl_issues = [issue]
            history.append({
                "stage": "implementation",
                "attempt": index + 1,
                "error": issue["message"],
                "verifier": package,
                "review": implementation_review,
            })
            continue
        calibration = {
            **calibration,
            "specification": specification,
            "specification_review": specification_review,
            "proof_plan": proof_plan,
            "proof_plan_review": proof_plan_review,
            "implementation_review": implementation_review,
        }
        history.append({
            "stage": "implementation",
            "attempt": index + 1,
            "error": None,
            "verifier": package,
            "review": implementation_review,
        })
        return package, calibration, history
    raise VerifierPreparationError(history)


def prepare_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    empty_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    attempts: int = 1,
    infer_fn: InferFn = infer,
    initial_state: Path | None = None,
    final_state: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build one verifier through four single-pass stages before Agent execution."""
    if attempts < 1:
        raise ValueError("attempts 必须大于 0")
    # Retain the established call signature while deliberately removing retry and calibration loops.
    _ = empty_evidence, initial_state, final_state, tools
    history: list[dict[str, Any]] = []

    def run_stage(stage: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            result = operation()
        except Exception as error:
            history.append({
                "stage": stage,
                "attempt": 1,
                "error": f"{type(error).__name__}: {error}"[:4000],
            })
            raise VerifierPreparationError(history) from error
        history.append({"stage": stage, "attempt": 1, "error": None, stage: result})
        return result

    specification = run_stage("subtask_plan", lambda: generate_verification_spec(
        task, environment, llm_config, infer_fn=infer_fn,
    ))
    evidence_plan = run_stage("evidence_plan", lambda: generate_evidence_plan(
        task, environment, specification, reference_evidence, llm_config, infer_fn=infer_fn,
    ))
    generated = run_stage("implementation", lambda: generate_planned_verifier(
        task, environment, specification, evidence_plan, reference_evidence,
        llm_config, infer_fn=infer_fn,
    ))
    reviewed = run_stage("implementation_review", lambda: review_and_revise_verifier(
        task, environment, specification, evidence_plan, generated, reference_evidence,
        llm_config, infer_fn=infer_fn,
    ))
    validate_verifier(reviewed)
    metadata = {
        "status": "reviewed",
        "specification": specification,
        "evidence_plan": evidence_plan,
    }
    return reviewed, metadata, history
