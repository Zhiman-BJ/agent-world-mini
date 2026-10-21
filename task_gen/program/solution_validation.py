"""Author-only, clean-state execution of a proposed public tool-call list."""

from __future__ import annotations

import hashlib
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from harness.execution import _schema_error, call_environment_tool
from .author_submission import check_proposal_fields
from .proposal_sources import EXPLORATION_TOOLS


TOOL_NAME = "validate_solution_calls"
SUBMISSION_TOOL_NAME = "submit_validated_solution"
TOOL = {
    "name": TOOL_NAME,
    "description": (
        "从任务干净初态顺序执行参考工具调用列表，返回每步真实结果、失败位置和最终状态变化。"
        "首次执行使用干净初态副本；相同列表复用实测结果，作者可修改完整方案后再次校验。"
        "calls 只记录公开业务MCP工具短名和实际参数；Read/Grep/Glob只用于作者探索，不进入参考列表。"
        "执行直接复用正式MCP业务接口，无需启动新的Kimi会话。"
        "通过表示列表执行成功，业务结论由作者根据返回结果核对。"
        "state.public_workspace为本次重放产物的只读目录，可用Read回读；私有数据库不对作者开放。"
        "每次提交完整proposal，以proposal.solution_calls为执行列表，检查题文、参数来源和答案证据；"
        "反馈source_check_passed及具体错误。旧请求可另传calls，但必须与完整方案的工具和参数一致。"
        "收到错误后在本会话修改并重验，通过后最终仅提交validation_id，引用该记录中的完整proposal。"
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["proposal"],
        "properties": {"proposal": {"type": "object", "description": "待提交的新题与完整参考解JSON，validation_id可省略。"}, "calls": {
            "type": "array", "minItems": 1, "maxItems": 65,
            "description": "可省略，默认执行proposal.solution_calls；提供时须与方案中的工具和参数完全一致。",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["tool", "arguments"],
                "properties": {"tool": {"type": "string", "minLength": 1},
                               "arguments": {"type": "object"}},
            },
        }},
    },
    "outputSchema": {"type": "object"},
}
SUBMISSION_TOOL = {
    "name": SUBMISSION_TOOL_NAME,
    "description": (
        "提交作者方案的成功校验回执。只有 execution_passed 和 source_check_passed 同时为 true 的校验记录才会接受；"
        "失败时返回具体错误，继续在当前会话修改完整方案并重新校验。接受后最终答复仍只提交 validation_id。"
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["validation_id"],
        "properties": {"validation_id": {"type": "string", "minLength": 1}},
    },
    "outputSchema": {"type": "object"},
}


def _snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("validation workspace contains a symbolic link")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def _proposal_calls(proposal: dict) -> list[dict]:
    if not isinstance(proposal.get("solution_calls"), list):
        raise ValueError("proposal.solution_calls must be a list of tool calls")
    calls = []
    for index, call in enumerate(proposal["solution_calls"], 1):
        if not isinstance(call, dict) or not isinstance(call.get("tool"), str) or not isinstance(call.get("arguments"), dict):
            raise ValueError(f"proposal.solution_calls[{index - 1}] requires tool and arguments fields")
        calls.append({"tool": call["tool"], "arguments": call["arguments"]})
    return calls


class SolutionValidator:
    def __init__(self, config: dict, *, defer_baseline: bool = False):
        self.config = config
        self.options = config["solution_validation"]
        self.tools = {tool["name"]: tool for tool in config["tools"]}
        if {TOOL_NAME, SUBMISSION_TOOL_NAME} & self.tools.keys():
            raise ValueError("solution validation tool name collides with a business tool")
        self.storage = tempfile.TemporaryDirectory(prefix="task-solution-validation-")
        self.root = Path(self.storage.name)
        self.baseline = self.root / "baseline"
        self.artifacts = Path(self.options.get("artifact_root", self.root / "artifacts"))
        self.private_artifacts = Path(self.options.get("private_artifact_root", self.root / "private-artifacts"))
        self._baseline_ready = False
        self._baseline_lock = threading.Lock()
        self.attempts = 0
        self.executions = {}
        self.validations = {}
        if not defer_baseline:
            self.ensure_baseline()

    def ensure_baseline(self) -> None:
        with self._baseline_lock:
            if self._baseline_ready:
                return
            try:
                shutil.copytree(self.config["workspace_root"], self.baseline / "workspace")
                database = self.config.get("record_database")
                if database and Path(database).is_file():
                    shutil.copy2(database, self.baseline / "records.sqlite")
            except BaseException:
                shutil.rmtree(self.baseline, ignore_errors=True)
                raise
            self._baseline_ready = True

    def validate(self, arguments: dict) -> dict:
        error = _schema_error(TOOL["inputSchema"], arguments)
        if error:
            raise ValueError(error)
        if "calls" not in arguments:
            check_proposal_fields({**arguments["proposal"], "validation_id": ""})
            arguments = {**arguments, "calls": _proposal_calls(arguments["proposal"])}
            error = _schema_error(TOOL["inputSchema"], arguments)
            if error:
                raise ValueError(error)
        if len(arguments["calls"]) > self.config["max_tool_calls"]:
            raise ValueError("solution exceeds the frozen tool-call budget")
        if any(call["tool"] in EXPLORATION_TOOLS for call in arguments["calls"]):
            raise ValueError("reference lists contain business MCP tools only; Read/Grep/Glob are exploration-only")
        self.ensure_baseline()
        self.attempts += 1
        validation_id = uuid.uuid4().hex
        execution_key = json.dumps(arguments["calls"], sort_keys=True, allow_nan=False)
        previous = self.executions.get(execution_key)
        if previous is not None:
            result = {**deepcopy(previous), "validation_id": validation_id, "execution_reused": True}
        else:
            result = self.execute(arguments["calls"], validation_id)
            if result["execution_passed"]:
                self.executions[execution_key] = deepcopy(result)
        result.update(source_check_passed=False, source_check_errors=[])
        try:
            from .proposal_sources import check_solution

            options = self.options["source_check"]
            proposal = {**arguments["proposal"], "validation_id": validation_id}
            check_proposal_fields(proposal)
            declared_calls = _proposal_calls(proposal)
            if declared_calls != arguments["calls"]:
                requested_calls = arguments["calls"]
                mismatches = [index + 1 for index in range(max(len(declared_calls), len(requested_calls)))
                    if index >= len(declared_calls) or index >= len(requested_calls)
                    or declared_calls[index] != requested_calls[index]]
                raise ValueError("calls and proposal.solution_calls require identical tools and arguments in the same order; "
                                 f"mismatched 1-based call indices={mismatches}; "
                                 f"list lengths={len(requested_calls)}/{len(declared_calls)}")
            if not result["execution_passed"]:
                raise ValueError("source checking requires the complete successfully executed list")
            original = {"task_text": options["original_task_text"],
                        "reference": {"tool_calls": [None] * options["original_call_count"]}}
            _, checked = check_solution(proposal, original, [], options["protocol"],
                {name: tool["inputSchema"] for name, tool in self.tools.items()},
                validation={"request": arguments, "result": result}, public_files=self.public_files(),
                minimum_reference_calls=options.get("minimum_reference_calls", 1))
            if "task_history" in self.options:
                from .task_history import check_task_history, review_novelty

                review_options = self.options.get("task_novelty_review")
                reviewer = ((lambda candidate, history: review_novelty(candidate, history, review_options))
                            if review_options else None)
                novelty = check_task_history(proposal, self.options["task_history"], reviewer=reviewer)
                result["task_history_check"] = novelty
                if novelty["passed"] is not True:
                    raise ValueError("任务与历史或本批前题重复，请在本会话重新设计并验证："
                                     + json.dumps(novelty, ensure_ascii=False))
            result.update(source_check_passed=True, source_check=checked)
        except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            errors = getattr(error, "errors", None)
            result["source_check_errors"].extend(errors if errors else [f"{type(error).__name__}: {error}"])
        self.validations[validation_id] = {
            "result": deepcopy(result),
            "proposal": deepcopy(arguments.get("proposal")),
        }
        evidence = Path(self.config["trace"]).with_name("solution_validations.jsonl")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        with evidence.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"request": arguments, "result": result}, ensure_ascii=False, allow_nan=False) + "\n")
        return result

    def submit(self, arguments: dict) -> dict:
        error = _schema_error(SUBMISSION_TOOL["inputSchema"], arguments)
        if error:
            raise ValueError(error)
        validation_id = arguments["validation_id"]
        record = self.validations.get(validation_id)
        if record is None:
            raise ValueError("validation_id does not refer to a validation in this author session")
        result = record["result"]
        if result.get("execution_passed") is not True:
            raise ValueError("submit requires execution_passed=true; repair the call list and validate again")
        if result.get("source_check_passed") is not True:
            errors = result.get("source_check_errors") or ["source checking did not pass"]
            raise ValueError("submit requires source_check_passed=true: " + "; ".join(str(item) for item in errors))
        accepted = {"accepted": True, "validation_id": validation_id}
        evidence = Path(self.config["trace"]).with_name("solution_submissions.jsonl")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        with evidence.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(accepted, ensure_ascii=False) + "\n")
        return accepted

    def execute(self, calls: list[dict], validation_id: str) -> dict:
        records = []
        with tempfile.TemporaryDirectory(dir=self.root, prefix="attempt-") as temporary:
            state = Path(temporary) / "state"
            shutil.copytree(self.baseline, state)
            workspace, database = state / "workspace", state / "records.sqlite"
            before = _snapshot(state)
            for index, call in enumerate(calls, 1):
                name = call["tool"]
                try:
                    if name in self.tools:
                        record = call_environment_tool(name, call["arguments"], self.tools, workspace,
                            timeout=self.config["timeout"], memory_limit=self.config["memory_limit"],
                            write_limit=self.config["write_limit"], process_limit=self.config.get("process_limit", 1024),
                            environment=self.config.get("environment", {}),
                            record_database=database if self.config.get("record_database") else None,
                            scope_mounts=self.config.get("scope_mounts") or None,
                            public_workspace_root=Path(self.config.get("public_workspace_root", self.config["workspace_root"])),
                            **{key: self.config[key] for key in ("software", "software_root") if self.config.get(key)})
                    else:
                        raise ValueError(f"unknown public solution tool: {name}")
                except (ValueError, OSError, NotImplementedError) as caught:
                    record = {**call, "result": None, "error": str(caught)}
                records.append({**record, "index": index})
                if record.get("error") or (isinstance(record.get("result"), dict)
                                           and record["result"].get("success") is False):
                    break
            after = _snapshot(state)
            changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
            public_workspace = self.artifacts / validation_id
            public_workspace.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(workspace), public_workspace)
            private_state = self.private_artifacts / validation_id
            private_state.mkdir(parents=True, exist_ok=False)
            database_sha256 = None
            if database.is_file():
                shutil.copy2(database, private_state / "records.sqlite")
                database_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
        passed = len(records) == len(calls) and all(
            not record.get("error") and not (isinstance(record.get("result"), dict)
                                             and record["result"].get("success") is False) for record in records)
        return {"validation_id": validation_id, "execution_passed": passed,
                  "execution_validation_id": validation_id, "execution_reused": False,
                  "semantic_correctness_proved": False, "call_count": len(records),
                  "failed_call": None if passed else len(records), "calls": records,
                  "state": {"changed_files": changed, "changed": bool(changed),
                            "public_workspace": str(public_workspace), "database_sha256": database_sha256},
                  "backend": "shared_mcp_executor", "model_inference": False}
    def public_files(self) -> dict[str, Path]:
        self.ensure_baseline()
        root = self.baseline / "workspace"
        return {path.relative_to(root).as_posix(): path for path in root.rglob("*")
                if path.is_file() or path.is_dir()}
