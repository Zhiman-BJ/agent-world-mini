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

from .tool_graph.llm import InferenceResult, infer, parse_json_object
from .tool_graph.step_3_chain_execute import _bounded_calls, _call_tool, _workspace_signature


_STATUSES = {"pass", "fail", "indeterminate"}
_CHANNELS = {"answer", "tool_trace", "workspace"}
_CONTEXT_METHODS = {
    "answer", "calls", "changed_paths", "files", "file", "read_text", "read_json",
    "call_tool", "verifier_calls",
    "pass_requirement", "fail_requirement", "indeterminate_requirement", "semantic_requirement",
}
_BANNED_CALLS = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "getattr", "globals", "input",
    "locals", "open", "setattr", "type", "vars", "__import__",
}
_BANNED_NODES = (
    ast.AsyncFunctionDef, ast.Await, ast.ClassDef, ast.Delete, ast.Global, ast.Import,
    ast.ImportFrom, ast.Nonlocal, ast.Raise, ast.With, ast.AsyncWith,
)
InferFn = Callable[..., InferenceResult | list[InferenceResult]]


class VerifierPreparationError(ValueError):
    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        super().__init__(json.dumps({"message": "verifier 生成校准失败", "attempts": attempts}, ensure_ascii=False))


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
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

class VerifierContext:
    def __init__(self, arguments):
        self._evidence = arguments["evidence"]
        self._tools = {tool["name"]: tool for tool in arguments.get("tools", [])}
        self._max_tool_calls = arguments.get("max_tool_calls", 50)
        self._verifier_calls = []
        self._results = []

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
        record = {"tool": name, "arguments": deepcopy(arguments), "result": result, "error": error}
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
'''


def _runner_source(source: str) -> str:
    return _RUNTIME + "\n" + source + r'''

def run(arguments, context):
    ctx = VerifierContext(arguments)
    verify(ctx)
    return {"results": ctx._results, "verifier_calls": ctx._verifier_calls}
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
    if (not isinstance(payload, dict) or set(payload) != {"results", "verifier_calls"}
            or not isinstance(payload["results"], list) or not isinstance(payload["verifier_calls"], list)):
        raise ValueError("verifier 返回结构非法")
    evidence["verifier_calls"] = payload["verifier_calls"]
    requirements = {item["id"] for item in package["requirements"]}
    seen: set[str] = set()
    for result in payload["results"]:
        if not isinstance(result, dict) or set(result) != {"requirement_id", "status", "reason", "evidence_refs"}:
            raise ValueError("verifier requirement 结果结构非法")
        requirement_id = result["requirement_id"]
        if requirement_id not in requirements or requirement_id in seen:
            raise ValueError("verifier 返回了未知或重复的 requirement_id")
        seen.add(requirement_id)
        if result["status"] not in _STATUSES | {"semantic"}:
            raise ValueError("verifier 返回了非法状态")
        if not isinstance(result["reason"], str) or not result["reason"].strip():
            raise ValueError("verifier reason 必须是非空字符串")
        if not isinstance(result["evidence_refs"], list) or any(
            not isinstance(ref, str) or not _valid_evidence_ref(ref, evidence) for ref in result["evidence_refs"]
        ):
            raise ValueError("verifier 返回了无效 evidence_refs")
        result["evidence_refs"] = list(dict.fromkeys(result["evidence_refs"]))
    if seen != requirements:
        raise ValueError("verifier 未返回每一项 requirement 的结果")
    results = payload["results"]
    semantic_results = [item for item in results if item["status"] == "semantic"]
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
    return results


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


def generate_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    infer_fn: InferFn = infer,
    previous_failure: str | None = None,
) -> dict[str, Any]:
    """Ask the model for a constrained verifier package, then validate it."""
    request = {
            "role": "你为一次真实任务执行生成审核 verifier。只根据任务文本定义成功条件。",
            "judging_principles": [
                "原任务文本是成功标准的唯一权威；参考回答、参考调用链和参考最终状态只是一条已知可行证据路径，不是必须复现的标准答案。",
                "判断任务要求的结果和状态变化，不把发现信息、选择工具、调用顺序或调用次数等内部过程另立为成功条件，除非原任务明确要求该过程。",
                "按任务动词区分创建、修改、删除、保持、查询、计算和呈现；需要持久化的结果必须由最终状态证明，纯查询、计算或呈现可以由工具结果与最终回答共同证明。",
                "证据必须直接支持要求，并绑定到同一业务对象、交付物、时间范围和字段；不得用其他对象上的同名信息拼接证明。",
                "业务身份独立于技术表示。任务未明确指定时，不把动态 ID、内部路径、序列化格式、字段承载方式、标识顺序或参考措辞当作通过条件。",
                "限定条件只作用于它直接修饰的对象或动作，不得传播到其他并列目标。",
                "路径、哈希、ID、数量、枚举、布尔值、精确引文及结构化字段等确定性事实用代码严格核对；自然语言的含义、质量和等价表达才交给 semantic_requirement。",
                "任务要求交付内容时必须存在实质内容；空字段、占位内容或仅声称已完成不能通过，除非任务明确允许无内容并要求说明该状态。",
                "所有实际状态变化都必须能由任务目标或完成目标所必需的工具语义解释；无关、破坏性或相互冲突的副作用不能通过。",
                "证据不足、截断或冲突时返回 indeterminate；证据明确反驳要求时返回 fail。reason 只能陈述 evidence_refs 实际证明的事实。",
            ],
            "construction_requirements": [
                "先完整拆出任务的全部必需原子要求：所有要求的并集覆盖整个任务，每项只表达一个可独立判断的目标，并具有唯一 id。",
                "除任务原子要求外，增加一项 required 的执行完整性要求，检查实际状态变化是否都与任务目标相关且没有破坏无关状态。",
                "为每项给出 claim、evidence_channels、pass_condition 和 fail_condition。",
                "source 只能定义 verify(ctx)，只能调用 VerifierContext 的公开方法；不得导入模块、直接访问路径、启动进程或写文件。",
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
        raise ValueError("参考执行语义二次确认未通过：" + json.dumps(
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
        "status": "calibrated",
    }


def prepare_verifier(
    task: dict[str, Any],
    environment: dict[str, Any],
    reference_evidence: dict[str, Any],
    empty_evidence: dict[str, Any],
    llm_config: dict[str, Any],
    *,
    attempts: int = 3,
    infer_fn: InferFn = infer,
    initial_state: Path | None = None,
    final_state: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Generate and calibrate, retaining each failed attempt for diagnostics."""
    if attempts < 1:
        raise ValueError("attempts 必须大于 0")
    history: list[dict[str, Any]] = []
    previous_failure: str | None = None
    for index in range(attempts):
        package: dict[str, Any] | None = None
        try:
            package = generate_verifier(
                task, environment, reference_evidence, llm_config, infer_fn,
                previous_failure=previous_failure,
            )
            calibration = calibrate_verifier(
                package, reference_evidence, empty_evidence,
                llm_config=llm_config, infer_fn=infer_fn,
                task_text=str(task.get("task_text") or ""),
                confirm_all=True,
                initial_state=initial_state,
                final_state=final_state,
                tools=tools,
            )
        except Exception as error:
            previous_failure = f"{type(error).__name__}: {error}"[:4000]
            history.append({
                "attempt": index + 1,
                "error": previous_failure,
                "verifier": package,
            })
            continue
        history.append({"attempt": index + 1, "error": None, "verifier": package})
        return package, calibration, history
    raise VerifierPreparationError(history)
