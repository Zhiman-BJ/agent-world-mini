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
from .proposal_sources import EXPLORATION_TOOLS


TOOL_NAME = "validate_solution_calls"
TOOL = {
    "name": TOOL_NAME,
    "description": (
        "从任务干净初态顺序执行参考工具调用列表，返回每步真实结果、失败位置和最终状态变化。"
        "首次执行使用干净初态副本；相同列表复用实测结果，作者可修改完整方案后再次校验。"
        "calls 只记录公开业务MCP工具短名和实际参数；Read/Grep/Glob只用于作者探索，不进入参考列表。"
        "执行直接复用正式MCP业务接口，无需启动新的Kimi会话。"
        "通过表示列表执行成功，业务结论由作者根据返回结果核对。"
        "每次同时提交完整proposal，检查题文、参数来源和答案证据；反馈source_check_passed及具体错误。"
        "收到错误后在本会话修改并重验，通过后提交同一proposal和validation_id。"
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["calls", "proposal"],
        "properties": {"proposal": {"type": "object", "description": "待提交的新题与完整参考解JSON，validation_id可省略。"}, "calls": {
            "type": "array", "minItems": 1, "maxItems": 65,
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


class SolutionValidator:
    def __init__(self, config: dict, *, defer_baseline: bool = False):
        self.config = config
        self.options = config["solution_validation"]
        self.tools = {tool["name"]: tool for tool in config["tools"]}
        if TOOL_NAME in self.tools:
            raise ValueError("solution validation tool name collides with a business tool")
        self.storage = tempfile.TemporaryDirectory(prefix="task-solution-validation-")
        self.root = Path(self.storage.name)
        self.baseline = self.root / "baseline"
        self._baseline_ready = False
        self._baseline_lock = threading.Lock()
        self.attempts = 0
        self.executions = {}
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
            if [{"tool": call["tool"], "arguments": call["arguments"]}
                    for call in proposal["solution_calls"]] != arguments["calls"]:
                raise ValueError("proposal call list differs from the executed validation request")
            if not result["execution_passed"]:
                raise ValueError("source checking requires the complete successfully executed list")
            original = {"task_text": options["original_task_text"],
                        "reference": {"tool_calls": [None] * options["original_call_count"]}}
            _, checked = check_solution(proposal, original, [], options["protocol"],
                {name: tool["inputSchema"] for name, tool in self.tools.items()},
                validation={"request": arguments, "result": result}, public_files=self.public_files())
            result.update(source_check_passed=True, source_check=checked)
        except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
            errors = getattr(error, "errors", None)
            result["source_check_errors"].extend(errors if errors else [f"{type(error).__name__}: {error}"])
        evidence = Path(self.config["trace"]).with_name("solution_validations.jsonl")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        with evidence.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"request": arguments, "result": result}, ensure_ascii=False, allow_nan=False) + "\n")
        return result

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
        passed = len(records) == len(calls) and all(
            not record.get("error") and not (isinstance(record.get("result"), dict)
                                             and record["result"].get("success") is False) for record in records)
        return {"validation_id": validation_id, "execution_passed": passed,
                  "execution_validation_id": validation_id, "execution_reused": False,
                  "semantic_correctness_proved": False, "call_count": len(records),
                  "failed_call": None if passed else len(records), "calls": records,
                  "state": {"changed_files": changed, "changed": bool(changed)},
                  "backend": "shared_mcp_executor", "model_inference": False}
    def public_files(self) -> dict[str, Path]:
        self.ensure_baseline()
        root = self.baseline / "workspace"
        return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}
