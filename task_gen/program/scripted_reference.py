"""Drive the official CLI with recorded calls, without model inference."""

from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Lock, Thread
import tempfile
from typing import Any

from distill.runner import _session_record, _tool_names_match, make_server_config, run_k3_distillation
from distill.taskgen_verifier import _verifier_environment
from harness.resources import ResourceCatalog
from task_gen.eval_cases import EvalCase

from .utils.io import read_json, write_json


NATIVE_TOOLS = {"Read", "Grep", "Glob"}


class ReferenceDriver(AbstractContextManager):
    def __init__(self, case: EvalCase, output_root: Path) -> None:
        self.case = case
        self.output_root = output_root
        self.calls = case.reference_calls
        self.tools = {tool["name"]: tool for tool in _verifier_environment(case)["tools"]}
        self.position = 0
        self.pending: dict[str, Any] | None = None
        self.expected_runtime_calls = deepcopy(self.calls)
        self.projections: list[dict[str, Any]] = []
        self.native_projections: list[dict[str, Any]] = []
        self.artifacts: dict[str, str] = {}
        self.lock = Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def _session_root(self, request: dict[str, Any]) -> Path:
        workspace = Path(read_json(self.output_root / "execution/private-state/path-map.json")["workspace_root"])
        matches = []
        for home in Path(tempfile.gettempdir()).glob("agent-world-kimi-home-*"):
            if not (home / "session_index.jsonl").is_file():
                continue
            try:
                record = _session_record(home, workspace)
            except RuntimeError:
                continue
            if record.get("sessionId") != request.get("prompt_cache_key"):
                continue
            session = Path(record["sessionDir"]).resolve()
            if not session.is_relative_to(home.resolve()) or not session.is_dir():
                raise ValueError("actual reference CLI session escaped its isolated home")
            matches.append(session)
        if len(matches) != 1:
            raise ValueError("cannot uniquely locate the actual reference CLI session")
        return matches[0]

    def _native_arguments(self, index: int, arguments: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        from .reference_replay import _jsonl, same_json_evidence

        old_path = arguments.get("path")
        if not isinstance(old_path, str) or not Path(old_path).is_absolute():
            return arguments
        producers = [(position, call) for position, call in enumerate(self.calls[:index])
                     if call["tool"] not in NATIVE_TOOLS and isinstance(call.get("result"), dict)
                     and call["result"].get("externalized") is True and call["result"].get("output_path") == old_path]
        if len(producers) > 1:
            raise ValueError("legacy native reference path has multiple producer calls")
        if not producers:
            original_id = self.case.task["task_id"].removesuffix("_direct_hardening")
            legacy_workspace = Path(f"/{self.case.task['environment_id']}__{original_id}/model-workspace")
            old = Path(old_path)
            legacy_root_glob = old == legacy_workspace.parent and self.calls[index]["tool"] == "Glob"
            if old.is_relative_to(legacy_workspace) or legacy_root_glob:
                mapping = read_json(self.output_root / "execution/private-state/path-map.json")
                workspace = Path(mapping["workspace_root"]).resolve()
                relocated = workspace if legacy_root_glob else (workspace / old.relative_to(legacy_workspace)).resolve()
                if not relocated.is_relative_to(workspace) or not relocated.exists():
                    raise ValueError("legacy native workspace path has no matching public runtime file")
                arguments["path"] = str(relocated)
                self.expected_runtime_calls[index]["arguments"] = deepcopy(arguments)
                self.native_projections.append({"index": index + 1, "original_path": old_path,
                                                "runtime_path": str(relocated),
                                                "source": "existing_public_workspace_root" if legacy_root_glob
                                                else "existing_public_workspace_file"})
                write_json(self.output_root / "native_argument_projection.json", self.native_projections)
            return arguments
        position, producer = producers[0]
        if old_path not in self.artifacts:
            records = _jsonl(self.output_root / "execution/raw/environment_tool_calls.jsonl")
            record_index = sum(call["tool"] not in NATIVE_TOOLS for call in self.calls[:position])
            if record_index >= len(records):
                raise ValueError("legacy reference artifact producer has not actually executed")
            observed = records[record_index]
            expected = self.expected_runtime_calls[position]
            if (observed.get("tool") != producer["tool"]
                    or not same_json_evidence(observed.get("arguments"), expected.get("arguments"))
                    or observed.get("error") != producer.get("error")):
                raise ValueError("legacy reference artifact does not match its actual producer")
            body = json.dumps({"result": observed["result"], "error": observed.get("error")},
                              ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            content = f"tool_name: {producer['tool']}\narguments: {json.dumps(observed['arguments'], ensure_ascii=False)}\n{body}\n"
            path = self._session_root(request) / "agents/main/tool-results" / f"reference-artifact-{position:04d}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.artifacts[old_path] = str(path)
        arguments["path"] = self.artifacts[old_path]
        self.expected_runtime_calls[index]["arguments"] = deepcopy(arguments)
        self.native_projections.append({"index": index + 1, "producer_index": position + 1,
                                        "original_path": old_path, "runtime_path": arguments["path"],
                                        "source": "actual_environment_tool_calls", "format": "legacy_result_wrapper_at_line_3",
                                        "sha256": hashlib.sha256(Path(arguments["path"]).read_bytes()).hexdigest()})
        write_json(self.output_root / "native_argument_projection.json", self.native_projections)
        return arguments

    def _arguments(self, index: int, request: dict[str, Any]) -> dict[str, Any]:
        from .reference_replay import reference_public_arguments

        call = self.calls[index]
        arguments = deepcopy(call.get("arguments", {}))
        if call["tool"] in NATIVE_TOOLS:
            return self._native_arguments(index, arguments, request)
        mapping = read_json(self.output_root / "execution/private-state/path-map.json")
        workspace = Path(mapping["workspace_root"])
        mounts = mapping["scope_mounts"]
        catalog = ResourceCatalog(self.case.environment, lambda scope: workspace / mounts[scope],
                                  workspace_root=workspace, scope_mounts=mounts)
        tool = self.tools[call["tool"]]
        allowed = set(mounts) & set(tool.get("usageConditions", {}).get("targetResources", []))
        public = reference_public_arguments(catalog, arguments, schema=tool["inputSchema"], allowed_scopes=allowed)
        runtime = catalog.normalize_arguments(public, schema=tool["inputSchema"], allowed_scopes=allowed)
        self.expected_runtime_calls[index]["arguments"] = runtime
        self.projections.append({"index": index + 1, "tool": call["tool"], "original_arguments": arguments,
                                 "public_arguments": public, "runtime_arguments": runtime})
        write_json(self.output_root / "reference_argument_projection.json", self.projections)
        return public

    @staticmethod
    def _name(short_name: str, request: dict[str, Any]) -> str:
        names = {tool.get("function", {}).get("name", tool.get("name")) for tool in request.get("tools", [])}
        full_name = short_name if short_name in NATIVE_TOOLS else f"mcp__agent_world_distill__{short_name}"
        candidates = [name for name in names if isinstance(name, str) and _tool_names_match({name}, {full_name})]
        if len(candidates) != 1:
            raise ValueError(f"reference tool is absent or ambiguous in actual CLI schema: {short_name}")
        return candidates[0]

    def reply(self, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
        with self.lock:
            if not request.get("tools"):
                return {"role": "assistant", "content": "Deterministic reference replay remains controlled by the local driver; no model solution is being generated."}, "stop"
            acknowledgements = {message.get("tool_call_id") for message in request.get("messages", [])
                                if message.get("role") == "tool"}
            if self.pending is not None and self.pending["id"] in acknowledgements:
                self.position += 1
                self.pending = None
            if self.position == len(self.calls):
                return {"role": "assistant", "content": self.case.task["reference"]["answer"]}, "stop"
            if self.pending is None:
                call = self.calls[self.position]
                self.pending = {"index": 0, "id": f"reference-replay-{self.position:04d}", "type": "function",
                                "function": {"name": self._name(call["tool"], request),
                                             "arguments": json.dumps(self._arguments(self.position, request), ensure_ascii=False)}}
            return {"role": "assistant", "tool_calls": [deepcopy(self.pending)]}, "tool_calls"

    def _handler(self):
        driver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                body = json.dumps({"object": "list", "data": [{"id": "kimi-k3", "object": "model", "owned_by": "scripted-reference-driver"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                try:
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    delta, finish = driver.reply(request)
                except (KeyError, ValueError, OSError) as error:
                    write_json(driver.output_root / "scripted_driver_error.json", {"error": str(error), "position": driver.position})
                    body = json.dumps({"error": {"message": str(error), "type": "invalid_request_error"}}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                chunks = [{"id": f"scripted-reference-{driver.position}", "object": "chat.completion.chunk",
                           "created": 0, "model": "kimi-k3", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": f"scripted-reference-{driver.position}", "object": "chat.completion.chunk",
                           "created": 0, "model": "kimi-k3", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
                body = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def replay_with_official_cli(case: EvalCase, output_root: Path, *, tool_timeout: int = 300) -> dict[str, Any]:
    from .reference_replay import _jsonl, check_call_results, same_json_evidence, same_native_file_evidence

    config = make_server_config(case.environment, max_tool_calls=len(case.reference_calls) + 1,
                                timeout=tool_timeout, runtime=case.runtime)
    write_json(output_root / "scripted_replay_plan.json", {"kind": "scripted_reference_replay_not_model_evaluation",
               "model_inference": False, "original_calls": case.reference_calls})
    with ReferenceDriver(case, output_root) as driver:
        trajectory = run_k3_distillation(
            case.task["task_text"], case.initial_state, config, output_root / "execution",
            kimi_bin=os.environ.get("KIMI_CODE_BIN", "kimi"), base_url=driver.base_url, api_key="local-reference-replay",
            max_context_size=262144, max_output_size=131072, max_environment_errors=None,
            max_upstream_retries=0, read_default_max_chars=80000, read_max_chars=128000,
            compaction_trigger_ratio=0.8,
            task={"environment_id": case.task["environment_id"], "task_id": case.task["task_id"],
                  "task_text": case.task["task_text"]},
            evaluation={"kind": "scripted_reference_replay", "model_inference": False},
        )
        if driver.position != len(case.reference_calls):
            raise ValueError("scripted reference stopped before all original calls were acknowledged")
        actual = [call for step in trajectory["steps"] for call in step["tool_calls"]]
        if len(actual) != len(case.reference_calls):
            raise ValueError("official reference execution contains missing or extra calls")
        native_indices = []
        native_differences = []
        for index, (expected, observed) in enumerate(zip(case.reference_calls, actual), 1):
            expected_name = expected["tool"]
            if (observed["call_id"] != f"reference-replay-{index-1:04d}"
                    or observed.get("completed_event_sequence") is None):
                raise ValueError("official reference execution reordered or left a tool call unfinished")
            if expected_name in NATIVE_TOOLS:
                if observed["name"] != expected_name or not same_json_evidence(driver.expected_runtime_calls[index-1].get("arguments", {}), observed["arguments"]):
                    raise ValueError("official reference execution changed native tool or arguments")
                expected_result = expected.get("result")
                expected_error = bool(expected.get("error") or
                                      (isinstance(expected_result, dict) and expected_result.get("isError")))
                if observed["is_error"] != expected_error:
                    raise ValueError(f"official native reference execution changed error at call {index}")
                native_indices.append(index)
                if not same_native_file_evidence(expected.get("result"), observed["output"]):
                    native_differences.append(index)
        expected_mcp = [call for call in driver.expected_runtime_calls if call["tool"] not in NATIVE_TOOLS]
        observed_mcp = _jsonl(output_root / "execution/raw/environment_tool_calls.jsonl")
        mcp = check_call_results(expected_mcp, observed_mcp)
    mcp_indices = [index for index, call in enumerate(case.reference_calls, 1) if call["tool"] not in NATIVE_TOOLS]
    differences = sorted([mcp_indices[index-1] for index in mcp["recorded_result_difference_indices"]] + native_differences)
    native_records = [{"tool": call["name"], "arguments": call["arguments"], "result": call["output"],
                      "error": None, "call_id": call["call_id"]} for call in actual if call["tool_kind"] == "native_file_tool"]
    write_json(output_root / "native_reference_calls.json", native_records)
    write_json(output_root / "actual_reference_calls.json", [
        next(record for record in native_records if record["call_id"] == call["call_id"])
        if call["tool_kind"] == "native_file_tool" else observed_mcp.pop(0) for call in actual
    ])
    result = {"call_count": len(actual), "mcp_call_count": len(mcp_indices), "native_file_call_count": len(native_indices),
              "recorded_result_difference_indices": differences, "recorded_results_identical": not differences,
              "expected_negative_result_indices": [mcp_indices[index-1] for index in mcp["expected_negative_result_indices"]],
              "backend": "official_kimi_cli_scripted_reference", "model_inference": False}
    write_json(output_root / "replay.json", result)
    return result
