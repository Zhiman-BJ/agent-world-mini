"""Generate and run frozen, task-specific execution verifiers."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

from .tool_graph.llm import InferenceResult, infer, parse_json_object
from .tool_graph.step_3_chain_execute import _bounded_calls, _call_tool, _workspace_signature


_STATUSES = {"pass", "fail", "indeterminate"}
_CHANNELS = {"answer", "tool_trace", "workspace"}
_CONTEXT_METHODS = {
    "answer", "calls", "changed_paths", "files", "file", "read_text", "read_json",
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
class VerifierContext:
    def __init__(self, evidence):
        self._evidence = evidence
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
        item = self.file(path, state)
        value = None if item is None else item.get("text")
        if value is None and state == "final":
            initial = self.file(path, "initial")
            if initial is not None and item is not None and initial.get("sha256") == item.get("sha256"):
                value = initial.get("text")
        return value

    def read_json(self, path, state="final"):
        item = self.file(path, state)
        value = None if item is None else item.get("json")
        if value is None and state == "final":
            initial = self.file(path, "initial")
            if initial is not None and item is not None and initial.get("sha256") == item.get("sha256"):
                value = initial.get("json")
        return value

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
    ctx = VerifierContext(arguments["evidence"])
    verify(ctx)
    return {"results": ctx._results}
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
) -> list[dict[str, Any]]:
    """Execute generated verifier code in the existing bubblewrap sandbox."""
    validate_verifier(package)
    with tempfile.TemporaryDirectory(prefix="task-verifier-") as temporary:
        outcome = _call_tool(_runner_source(package["source"]), {"evidence": evidence}, Path(temporary), timeout,
                             memory_limit, 1024 * 1024)
    if outcome.get("kind") is not None:
        raise ValueError(f"verifier 执行失败：{outcome.get('error')}")
    payload = outcome.get("result")
    if not isinstance(payload, dict) or set(payload) != {"results"} or not isinstance(payload["results"], list):
        raise ValueError("verifier 返回结构非法")
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
            _semantic_prompt(item, evidence, requirements_by_id[item["requirement_id"]])
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
        "changed_paths": [],
        "initial_files": [],
        "final_files": [],
    }
    for reference in refs:
        if reference.startswith("tool_call:"):
            index = int(reference.split(":", 1)[1])
            selected["calls"].append(evidence["calls"][index])
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
    role = "你是单项任务要求的语义核验器，只判断给定要求，不新增要求。"
    if task_text is not None:
        role += (
            "依据原任务判断引用证据是否满足当前原子子项对应的任务目标，原任务是唯一权威。"
            "原子子项不包含任务中的其他目标是正常的；不得因遗漏无关目标而失败。"
            "生成的 pass_condition、fail_condition 或代码若加入原任务对应分句未要求的日期、数值、格式、载体或操作方式，"
            "忽略这些加码，按原任务实际要求裁决；不要把契约设计本身作为单独的成败条件。"
        )
    role += (
        "只有引用证据直接证明要求时才通过；不得用最终回答补足任务明确要求写入文件或持久业务状态的缺失内容；"
        "但任务只要求生成、计算并呈现结果，未明确要求落盘或持久化时，工具调用结果和最终回答可以共同作为证据。"
        "空标题、空字段或仅声称已完成都不算内容。任务要求某份简报、文件或备注包含或整理内容时，"
        "只有该载体正文能证明完成，最终回答不能替代。任务只要求提供或保留某项检查结果时，"
        "不得擅自要求该结果必须为 true、成功或通过。可回溯证据引用可以由业务标识、说话人、时间或位置等信息完成定位；"
        "任务未明确要求逐字引文时，不得要求引用中包含原文全文。"
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
            "rules": [
                "先把任务拆成全部必需的原子要求，每项必须有唯一 id。",
                "只定义用户要求的成功结果；不要把发现信息、选择工具或其他内部操作过程另立为成功条件。",
                "为每项给出 claim、evidence_channels、pass_condition 和 fail_condition。",
                "source 只能定义 verify(ctx)，只能调用 VerifierContext 的公开方法。",
                "source 不得导入模块、访问路径、启动进程、写文件或硬编码参考执行的动态 ID。",
                "不得把参考执行中偶然出现的具体 ID、时间戳或固定文本值当作通用通过条件；只能检查任务要求及证据结构。",
                "工具调用链只是可用证据，不是标准答案；任务未明确要求时，不得把调用某个工具、调用顺序或调用次数作为成功条件。",
                "只检查任务要求的最终业务状态。只有任务明确要求创建、新增或追加时才能要求相对初态新增；登记、整理、归档或设置不得自动解释为必须新建。",
                "业务名称按语义匹配，忽略不影响含义的大小写、空白、标点和下划线差异；确定性代码难以可靠归一时使用 semantic_requirement。",
                "路径和枚举值按任务语义匹配；除任务明确指定父路径或字面格式外，不得照搬参考执行的完整字符串作为唯一条件。",
                "公开工具若只返回计算结果且没有写入能力，不得虚构输出文件；任务未明确要求落盘时，工具结果、工具参数、最终回答和最终状态可以共同证明结果已生成。参考调用轨迹可能没有返回值，不能因此要求工具必须有返回值。",
                "任务只要求提供或保留某项检查结果时，不得擅自要求该结果必须为 true、成功或通过；应保留工具实际返回的结果。",
                "任务只要求可回溯的证据引用时，业务标识、说话人、时间或位置等可定位信息即可构成引用；除非任务明确要求逐字引文，不得强制引用包含原文全文。",
                "任务未指定的技术字段（例如内部 ID、source_reference、作者、文件名）不得作为通过条件；只有在该字段用于识别任务要求的业务对象时，才按其指向的对象核对。",
                "同一字段承载多个业务标识时，只能分别核对任务明确指定的标识，不得要求参考执行中的字段选择、标识顺序、完整字符串或逗号、加号等分隔格式。",
                "日期、名称、数值等限定词只约束其所在分句明确修饰的业务对象，不得传播到同段的其他动作或产物。",
                "任务要求交付物包含或整理某类内容时，空标题、空字段不能算完成；没有相应内容时，交付物必须明确说明无。",
                "凡把相对初态新增或变化作为通过条件，evidence_refs 必须同时引用相关 initial 和 final 证据，不得让语义审核凭未引用的初态推断。",
                "路径和数据结构只能使用 reference_evidence 中明确出现的事实，不得猜测。",
                "文件只有元数据而没有 text/json 时，表示正文未放进生成提示；运行时仍可按该路径读取。",
                "需要自然语言判断时调用 ctx.semantic_requirement(id, claim, evidence_refs)。",
                "备注、评论、说明、摘要等自然语言内容是否表达某个含义，必须使用 semantic_requirement；不得用固定短语、关键词或参考措辞的子串匹配直接判定成败。",
                "确定性事实优先在 source 中直接比较证据。",
                "source 的每条执行路径必须记录每项 requirement 的结果恰好一次，不能遗漏或重复记录。",
                "evidence_refs 只能使用列出的精确格式和当前证据中存在的索引或路径；工具引用必须从 ctx.calls() 的实际索引生成，不得写 tool_trace 或 workspace 等占位符。",
                "source 保持紧凑，复用局部 helper，不要在代码中重复 requirements 的长篇说明。",
                "空回答、无工具调用且 workspace 无变化时，整个任务不得通过；仅要求保持不变的子项可凭同一路径 initial/final 哈希一致单独通过。",
                "只输出符合 schema 的 JSON，不要 markdown。",
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
                "read_json(path, state='final')": "返回完整纳入证据包的 JSON 值或 None",
                "pass_requirement(id, reason, refs)": "记录确定通过",
                "fail_requirement(id, reason, refs)": "记录确定失败",
                "indeterminate_requirement(id, reason, refs=[])": "记录证据不足",
                "semantic_requirement(id, claim, refs)": "请求模型对一个语义命题判断",
            },
            "evidence_reference_formats": [
                "answer", "tool_call:N", "initial:relative/path", "final:relative/path",
                "workspace_change:relative/path",
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
) -> dict[str, Any]:
    """Require the reference to pass and empty evidence not to pass required criteria."""
    reference_results = run_verifier(
        package, reference_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
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
        def accept_semantics(prompt: str | list[str], **_: Any) -> InferenceResult | list[InferenceResult]:
            result = InferenceResult('{"status":"pass","reason":"只校准确定性分支"}', {}, "calibration")
            return [result for _ in prompt] if isinstance(prompt, list) else result

        counterfactual_results = run_verifier(
            package, counterfactual_evidence, semantic_infer_fn=accept_semantics, llm_config=llm_config,
        )
        counterfactual = aggregate_results(package["requirements"], counterfactual_results)
        if counterfactual["outcome"] != "pass":
            raise ValueError("参考执行反事实校准未通过：" + json.dumps(counterfactual_results, ensure_ascii=False))
    empty_results = run_verifier(
        package, empty_evidence, semantic_infer_fn=infer_fn, llm_config=llm_config,
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
        "counterfactual": counterfactual,
        "empty": empty,
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
