"""Official Kimi Code CLI orchestration for K3 trajectory distillation."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from harness.kimi_file_policy import build_kimi_file_hook_command
from harness.delivery import load_delivery
from harness.layout import TaskRunLayout, create_task_run_layout
from harness.mcp_tools import expected_mcp_tool_names
from harness.mcp_protocol import PROTOCOL_VERSION
from env_gen.tool_gen.runtime_launch import StdioLaunch, stdio_launch
from task_gen.task_eval_mcp import bind_delivery
from task_gen.tool_graph.step_3_chain_execute import _public_environment

from .export import export_trajectory, validate_trajectory
from .environment_errors import environment_errors
from .relay import StreamingRelay


KIMI_K3_CONTEXT_SIZE = 1_048_576
# Keep the full K3 context window while reserving a bounded completion budget.
# Sending the full context size as max_completion_tokens makes requests with a
# non-empty prompt exceed the upstream context window and receive HTTP 400.
KIMI_K3_OUTPUT_SIZE = 131_072
# A smaller cap is useful for controlled reruns without changing the default
# official K3 window. The CLI still receives the selected cap as its model
# metadata and the relay preserves it in the recorded trajectory.
KIMI_K3_CONTEXT_SIZES = (262_144, KIMI_K3_CONTEXT_SIZE)
KIMI_K3_EFFORTS = ("low", "high", "max")
KIMI_K3_DEFAULT_EFFORT = "max"
KIMI_DEFAULT_MAX_TOOL_CALLS = 65
KIMI_MAX_TOOL_CALLS = 75
KIMI_UPSTREAM_MAX_RETRIES = 4
KIMI_UPSTREAM_RETRY_BASE_SECONDS = 1.0
KIMI_UPSTREAM_RETRY_MAX_SECONDS = 30.0
KIMI_UPSTREAM_RETRY_JITTER_SECONDS = 0.25
KIMI_MCP_CLIENT_STARTUP_TIMEOUT_SECONDS = 120
KIMI_MCP_STARTUP_TIMEOUT_SECONDS = 130.0
KIMI_MCP_PREFLIGHT_TIMEOUT_SECONDS = 60.0
KIMI_DOCKER_MCP_STARTUP_CONCURRENCY = 1
KIMI_DOCKER_MCP_SESSION_CONCURRENCY = 4
# /data1 is a single ext4 volume. Keep the conservative default, but let large
# batches raise preparation parallelism while the I/O pressure gate remains in
# force. The value is read once when each runner process starts.
KIMI_STATE_COPY_CONCURRENCY = int(os.environ.get("KIMI_STATE_COPY_CONCURRENCY", "1"))
if KIMI_STATE_COPY_CONCURRENCY < 1:
    raise ValueError("KIMI_STATE_COPY_CONCURRENCY must be a positive integer")
KIMI_STATE_COPY_IO_PRESSURE_LIMIT = 10.0
KIMI_STATE_COPY_IO_POLL_SECONDS = 5.0
PROVIDER_TYPES = {"kimi", "openai_responses"}


_DOCKER_MCP_STARTUP_SEMAPHORE = threading.BoundedSemaphore(
    KIMI_DOCKER_MCP_STARTUP_CONCURRENCY
)
_DOCKER_MCP_SESSION_SEMAPHORE = threading.BoundedSemaphore(
    KIMI_DOCKER_MCP_SESSION_CONCURRENCY
)
_STATE_COPY_SEMAPHORE = threading.BoundedSemaphore(KIMI_STATE_COPY_CONCURRENCY)


def _io_full_pressure_avg10() -> float | None:
    try:
        for line in Path("/proc/pressure/io").read_text(encoding="utf-8").splitlines():
            if line.startswith("full "):
                values = dict(field.split("=", 1) for field in line.split()[1:])
                return float(values["avg10"])
    except (OSError, ValueError, KeyError):
        return None
    return None


def _wait_for_state_copy_capacity() -> None:
    """Do not start another full-state copy while the shared volume is saturated."""
    pressure_limit = float(os.environ.get(
        "KIMI_STATE_COPY_IO_PRESSURE_LIMIT",
        KIMI_STATE_COPY_IO_PRESSURE_LIMIT,
    ))
    if pressure_limit < 0:
        raise ValueError("KIMI_STATE_COPY_IO_PRESSURE_LIMIT must be non-negative")
    while True:
        pressure = _io_full_pressure_avg10()
        if pressure is None or pressure < pressure_limit:
            return
        time.sleep(KIMI_STATE_COPY_IO_POLL_SECONDS)


class DistillRunError(RuntimeError):
    """The raw run was retained but Kimi did not complete the task."""


class EnvironmentErrorCircuitBreaker:
    """Share a live environment-error budget across concurrent task runs."""

    def __init__(self, limit: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("environment error circuit-breaker limit must be positive")
        self.limit = limit
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._errors: dict[tuple[str, int], dict[str, Any]] = {}

    def observe(self, trace: Path, errors: list[dict[str, Any]]) -> bool:
        with self._lock:
            for error in errors:
                key = (str(trace.resolve()), int(error["line"]))
                self._errors.setdefault(key, {"trace": str(trace), **error})
            if len(self._errors) >= self.limit:
                self.event.set()
            return self.event.is_set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "limit": self.limit,
                "observed_count": len(self._errors),
                "errors": list(self._errors.values()),
            }


DistillPaths = TaskRunLayout


def _prepare_paths(
    initial_state: Path,
    output_dir: Path,
    *,
    environment: dict[str, Any] | None = None,
    workspace_name: str | None = None,
) -> DistillPaths:
    return create_task_run_layout(
        initial_state,
        output_dir,
        environment=environment,
        workspace_name=workspace_name,
    )


def _task_workspace_name(
    environment: dict[str, Any], task: dict[str, Any] | None, output_dir: Path,
) -> str:
    environment_id = str(environment.get("environment_id") or output_dir.name)
    task_id = task.get("task_id") if isinstance(task, dict) else None
    return f"{environment_id}_{task_id}" if isinstance(task_id, str) and task_id else environment_id


def _server_config(value: Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, Path):
        loaded = json.loads(value.expanduser().read_text(encoding="utf-8"))
    else:
        loaded = dict(value)
    if not isinstance(loaded.get("tools"), list):
        raise ValueError("server_config.tools must be an array")
    loaded.setdefault("process_limit", 1024)
    for name in ("max_tool_calls", "timeout", "memory_limit", "write_limit", "process_limit"):
        if type(loaded.get(name)) is not int or loaded[name] < 1:
            raise ValueError(f"server_config.{name} must be a positive integer")
    return bind_delivery(loaded)


def _resolve_kimi_bin(value: Path | str) -> Path:
    text = str(value)
    candidate = Path(text).expanduser()
    if "/" not in text:
        found = shutil.which(text)
        if found:
            candidate = Path(found)
    candidate = candidate.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError(f"official Kimi CLI is not executable: {candidate}")
    return candidate


def _write_kimi_config(
    path: Path,
    relay_url: str,
    model_alias: str,
    model_id: str,
    max_context_size: int,
    provider_type: str,
    file_hook_command: str,
    reasoning_effort: str = KIMI_K3_DEFAULT_EFFORT,
    max_output_size: int = KIMI_K3_OUTPUT_SIZE,
) -> None:
    if not isinstance(file_hook_command, str) or not file_hook_command.strip():
        raise ValueError("file_hook_command must be non-empty")
    if type(max_output_size) is not int or max_output_size < 1:
        raise ValueError("max_output_size must be a positive integer")
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    hook = (
        "\n[[hooks]]\n"
        'event = "PreToolUse"\n'
        'matcher = "^(Read|Grep|Glob)$"\n'
        f"command = {quote(file_hook_command)}\n"
        "timeout = 5\n"
    )
    path.write_text(
        f"default_model = {quote(model_alias)}\n\n"
        "[providers.agent_world_distill]\n"
        f"type = {quote(provider_type)}\n"
        f"base_url = {quote(relay_url)}\n"
        'api_key = "relay-only-not-an-upstream-credential"\n\n'
        f"[models.{quote(model_alias)}]\n"
        'provider = "agent_world_distill"\n'
        f"model = {quote(model_id)}\n"
        f'display_name = {quote(model_id)}\n'
        f"max_context_size = {max_context_size}\n"
        f"max_output_size = {max_output_size}\n"
        'capabilities = ["thinking", "always_thinking", "tool_use"]\n'
        f"support_efforts = {json.dumps(list(KIMI_K3_EFFORTS))}\n"
        f"default_effort = {quote(reasoning_effort)}\n"
        f"{hook}",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_agent(path: Path) -> None:
    path.write_text("""\
---
name: agent-world-distill
description: Agent World Kimi K3 trajectory distillation
tools:
  - mcp__agent_world_distill__*
  - Read
  - Grep
  - Glob
disallowedTools:
  - Write
  - Edit
  - Bash
  - Agent
  - AgentSwarm
  - WebSearch
  - FetchURL
  - AskUserQuestion
  - select_tools
subagents: []
---

${base_prompt}
""", encoding="utf-8")


def _write_kimi_launcher(path: Path) -> None:
    """Load a prompt after execve so large tasks do not exceed argv limits."""
    path.write_text("""\
#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const [cliPath, promptPath, ...args] = process.argv.slice(2);
process.argv = [process.argv[0], cliPath, '--prompt', readFileSync(promptPath, 'utf8'), ...args];
await import(pathToFileURL(cliPath).href);
""", encoding="utf-8")
    path.chmod(0o700)


def _write_mcp_config(path: Path, launch: StdioLaunch, workspace: Path, timeout: int) -> None:
    payload = {
        "mcpServers": {
            "agent_world_distill": {
                "command": str(launch.command),
                "args": list(launch.arguments),
                "env": launch.environment,
                "cwd": str(workspace),
                "enabled": True,
                "deferred": False,
                "startupTimeoutMs": KIMI_MCP_CLIENT_STARTUP_TIMEOUT_SECONDS * 1000,
                "toolTimeoutMs": timeout * 1000,
            }
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _task_mcp_launch(
    config: dict[str, Any],
    private_server: Path,
    paths: DistillPaths,
) -> StdioLaunch:
    repository = Path(__file__).resolve().parents[1]
    server_path = repository / "task_gen/task_eval_kimi_mcp.py"
    binding_path = config.get("binding_path")
    if not isinstance(binding_path, str) or not binding_path:
        return StdioLaunch(
            Path(sys.executable).resolve(),
            (str(server_path), str(private_server)),
            {},
        )

    delivery = load_delivery(Path(binding_path))
    trace = paths.raw / "environment_tool_calls.jsonl"
    budget_stop_marker = paths.raw / "tool_budget_stop.json"
    tool_budget_counter = paths.raw / "tool_budget_counter.json"
    workspace = paths.model_workspace
    record_database = paths.record_database
    path_map = paths.path_map
    arguments = [
        str(server_path),
        "--binding", str(delivery.binding_path),
        "--workspace-root", str(workspace),
        "--record-database", str(record_database),
        "--path-map", str(path_map),
        "--trace", str(trace),
        "--budget-stop-marker", str(budget_stop_marker),
        "--tool-budget-counter", str(tool_budget_counter),
        "--max-tool-calls", str(config["max_tool_calls"]),
        "--timeout", str(config["timeout"]),
        "--memory-limit", str(config["memory_limit"]),
        "--write-limit", str(config["write_limit"]),
        "--process-limit", str(config["process_limit"]),
    ]
    tools_path = config.get("tools_path")
    if isinstance(tools_path, str) and tools_path:
        arguments.extend(["--tools-path", str(Path(tools_path).resolve())])
    writable_paths = [
        workspace, record_database, path_map, trace, budget_stop_marker,
        tool_budget_counter,
    ]
    if "review_choice_seed" in config:
        seed_path = private_server.with_name("review_choice_seed.json")
        seed_path.write_text(
            json.dumps(config["review_choice_seed"], ensure_ascii=False),
            encoding="utf-8",
        )
        seed_path.chmod(0o600)
        arguments.extend(["--review-choice-seed", str(seed_path)])
        writable_paths.append(seed_path)
    return stdio_launch(
        delivery,
        server_path=server_path,
        arguments=arguments,
        writable_paths=tuple(writable_paths),
        project_root=repository,
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
    return records


def _session_record(kimi_home: Path, workspace: Path) -> dict[str, Any]:
    records = _load_jsonl(kimi_home / "session_index.jsonl")
    matching = [record for record in records if Path(record.get("workDir", "")).resolve() == workspace.resolve()]
    if not matching:
        raise DistillRunError("Kimi CLI did not create a session for this workspace")
    return matching[-1]


def _copy_session_evidence(record: dict[str, Any], kimi_home: Path, raw: Path) -> str:
    session_id = record.get("sessionId")
    session_dir = Path(record.get("sessionDir", "")).resolve()
    if not isinstance(session_id, str) or not session_id:
        raise DistillRunError("Kimi session index is missing sessionId")
    if not session_dir.is_relative_to(kimi_home.resolve()) or not session_dir.is_dir():
        raise DistillRunError("Kimi session directory escaped the isolated home")
    wire = session_dir / "agents/main/wire.jsonl"
    if not wire.is_file():
        raise DistillRunError("Kimi session is missing agents/main/wire.jsonl")
    shutil.copy2(wire, raw / "wire.jsonl")
    state = session_dir / "state.json"
    if state.is_file():
        shutil.copy2(state, raw / "session_state.json")
    return session_id


def _turn_step(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, str) or "." not in value:
        return None, None
    turn, step = value.split(".", 1)
    return (int(turn), int(step)) if turn.isdigit() and step.isdigit() else (None, None)


def _enrich_model_requests(raw: Path) -> list[str]:
    requests = _load_jsonl(raw / "model_requests.jsonl")
    wire_requests = [
        (line, record) for line, record in enumerate(_load_jsonl(raw / "wire.jsonl"), 1)
        if record.get("type") == "llm.request"
    ]
    errors = []
    for index, request in enumerate(requests):
        wire_line, wire = wire_requests[index] if index < len(wire_requests) else (None, {})
        turn_id, step = _turn_step(wire.get("turnStep"))
        wire_kind = wire.get("kind")
        request.update({
            "kind": "compaction" if wire_kind == "compaction" else "agent_step",
            "turn_id": turn_id,
            "step": step,
            "wire_line": wire_line,
            "wire_request": wire or None,
        })
        body = request.get("request")
        tools = body.get("tools", []) if isinstance(body, dict) else []
        names = []
        for tool in tools:
            if isinstance(tool, dict):
                function = tool.get("function")
                name = function.get("name") if isinstance(function, dict) else tool.get("name")
                if isinstance(name, str):
                    names.append(name)
        request["tool_names"] = names
    if len(wire_requests) != len(requests):
        errors.append(f"wire/model request count mismatch: {len(wire_requests)} != {len(requests)}")
    with (raw / "model_requests.jsonl").open("w", encoding="utf-8") as stream:
        for request in requests:
            stream.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
    return errors


def _wire_summary(
    raw: Path,
    model_alias: str,
    model_id: str,
    max_context_size: int,
    provider_type: str,
    reasoning_effort: str,
    max_output_size: int | None = KIMI_K3_OUTPUT_SIZE,
) -> dict[str, Any]:
    records = _load_jsonl(raw / "wire.jsonl")
    final_answer = ""
    reason = "failed"
    usage = {"inputOther": 0, "output": 0, "inputCacheRead": 0, "inputCacheCreation": 0}
    for record in records:
        if record.get("type") == "agent.message.appended":
            message = record.get("message", {}).get("message", {})
            if message.get("role") == "assistant" and not message.get("toolCalls"):
                text = "".join(
                    part.get("text", "") for part in message.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "text"
                )
                if text.strip():
                    final_answer = text
        elif record.get("type") == "turn.ended":
            reason = record.get("reason", reason)
        elif record.get("type") == "usage.record" and isinstance(record.get("usage"), dict):
            for key in usage:
                value = record["usage"].get(key, 0)
                if isinstance(value, (int, float)):
                    usage[key] += value
    return {
        "reason": reason,
        "answer": final_answer,
        "usage": {"total": usage},
        "model": {
            "alias": model_alias,
            "upstream_id": model_id,
            "display_name": model_id,
            "provider": provider_type,
            "max_context_size": max_context_size,
            "max_output_size": max_output_size,
            "reasoning_effort": reasoning_effort,
        },
    }


def _tool_policy_errors(raw: Path, expected_names: set[str]) -> list[str]:
    errors = []
    requests = _load_jsonl(raw / "model_requests.jsonl")
    if not requests:
        return ["Kimi CLI made no model request"]
    for request in requests:
        names = set(request.get("tool_names", []))
        if request.get("kind") == "compaction" and not names:
            continue
        if not _tool_names_match(names, expected_names):
            errors.append(
                f"request {request.get('request_id')} tool allowlist mismatch: "
                f"expected {sorted(expected_names)}, got {sorted(names)}"
            )
    return errors


def _tool_names_match(observed: set[str], expected: set[str]) -> bool:
    """Accept Kimi Code's collision-safe shortening of names over 64 characters."""
    if observed == expected or len(observed) != len(expected):
        return observed == expected
    unmatched_expected = expected - observed
    unmatched_observed = observed - expected
    candidates_by_alias: dict[str, list[str]] = {}
    for name in unmatched_observed:
        suffix = re.search(
            r"(?:_[0-9a-f]{8}|-[0-9a-f]{8}|_-[0-9a-f]{7,8})$", name
        )
        candidates_by_alias[name] = [
            candidate for candidate in unmatched_expected
            if len(candidate) > 64
            and len(name) == 64
            # Kimi Code shortens at different safe boundaries across versions.
            # Match the preserved prefix rather than a fixed cut position.
            and suffix is not None
            and candidate.startswith(name[:suffix.start()])
        ]
        if not candidates_by_alias[name]:
            return False

    # Multiple long names may share the same retained prefix. Match the two
    # sets as a bipartite graph instead of requiring every alias to have one
    # locally unique candidate.
    matched_alias: dict[str, str] = {}

    def assign(alias: str, seen: set[str]) -> bool:
        for candidate in candidates_by_alias[alias]:
            if candidate in seen:
                continue
            seen.add(candidate)
            previous = matched_alias.get(candidate)
            if previous is None or assign(previous, seen):
                matched_alias[candidate] = alias
                return True
        return False

    return all(assign(alias, set()) for alias in unmatched_observed)


def _drain(stream: Any, target: Path) -> None:
    read = getattr(stream, "read1", stream.read)
    with target.open("wb") as output:
        while True:
            chunk = read(65536)
            if not chunk:
                break
            output.write(chunk)
            output.flush()


class _CliToolCallCounter:
    """Incrementally count unique tool calls emitted by Kimi's JSONL stream."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.pending = b""
        self.seen_ids: set[str] = set()
        self.observed_count = 0
        self.native_file_tool_calls = 0
        self.environment_mcp_tool_calls = 0
        self.other_tool_calls = 0
        self.anonymous_call_sequence = 0

    def _observe_row(self, row: Any) -> None:
        if not isinstance(row, dict) or row.get("role") != "assistant":
            return
        calls = row.get("tool_calls")
        if not isinstance(calls, list):
            return
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                identity = call_id
            else:
                # Stream-json normally supplies an id. Keep malformed fixtures
                # countable without merging distinct calls in the same row.
                identity = f"anonymous:{self.anonymous_call_sequence}:index:{index}"
                self.anonymous_call_sequence += 1
            if identity in self.seen_ids:
                continue
            self.seen_ids.add(identity)
            function = call.get("function")
            name = function.get("name") if isinstance(function, dict) else call.get("name")
            self.observed_count += 1
            if name in {"Read", "Grep", "Glob"}:
                self.native_file_tool_calls += 1
            elif isinstance(name, str) and name.startswith("mcp__"):
                self.environment_mcp_tool_calls += 1
            else:
                self.other_tool_calls += 1

    def snapshot(self) -> dict[str, int]:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if size < self.offset:
            # The runner creates a fresh stream file for every task, but handle
            # replacement defensively so stale offsets cannot hide new calls.
            self.offset = 0
            self.pending = b""
            self.seen_ids.clear()
            self.observed_count = 0
            self.native_file_tool_calls = 0
            self.environment_mcp_tool_calls = 0
            self.other_tool_calls = 0
            self.anonymous_call_sequence = 0
        if size > self.offset:
            try:
                with self.path.open("rb") as stream:
                    stream.seek(self.offset)
                    chunk = stream.read()
            except OSError:
                chunk = b""
            self.offset += len(chunk)
            lines = (self.pending + chunk).split(b"\n")
            self.pending = lines.pop()
            for raw_line in lines:
                if not raw_line.strip():
                    continue
                try:
                    row = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                self._observe_row(row)
        return {
            "observed_count": self.observed_count,
            "native_file_tool_calls": self.native_file_tool_calls,
            "environment_mcp_tool_calls": self.environment_mcp_tool_calls,
            "other_tool_calls": self.other_tool_calls,
        }


def _stop_process_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _run_mcp_preflight(
    launch: StdioLaunch,
    workspace: Path,
    raw: Path,
    expected_tool_names: set[str],
    *,
    timeout_seconds: float = KIMI_MCP_PREFLIGHT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Start the exact task MCP command and verify its protocol before Kimi runs."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "agent-world-distill-preflight",
                    "version": "1.0",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    request_bytes = b"".join(
        (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        for request in requests
    )
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    raw.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen[bytes] | None = None
    spawn_error: OSError | None = None
    timed_out = False
    try:
        process = subprocess.Popen(
            [str(launch.command), *launch.arguments],
            cwd=workspace,
            env={**os.environ, **launch.environment},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = process.communicate(
                input=request_bytes,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop_process_group(process, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _stop_process_group(process, signal.SIGKILL)
                stdout, stderr = process.communicate()
    except OSError as caught:
        spawn_error = caught
        stdout = b""
        stderr = f"{type(caught).__name__}: {caught}\n".encode("utf-8")

    stdout_path = raw / "mcp_preflight.stdout.jsonl"
    stderr_path = raw / "mcp_preflight.stderr.txt"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)

    parsed: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for line_number, line in enumerate(stdout.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            parse_errors.append(f"line {line_number}: {error}")
            continue
        if not isinstance(value, dict):
            parse_errors.append(f"line {line_number}: response is not an object")
            continue
        parsed.append(value)

    responses = {response.get("id"): response for response in parsed if "id" in response}
    initialize = responses.get(1)
    tools_list = responses.get(2)
    observed_tool_names: set[str] = set()
    if isinstance(tools_list, dict):
        result = tools_list.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if isinstance(tools, list):
            observed_tool_names = {
                tool["name"]
                for tool in tools
                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
            }

    failure_stage = None
    error = None
    if spawn_error is not None:
        failure_stage = "process_spawn"
        error = f"MCP process could not start: {type(spawn_error).__name__}: {spawn_error}"
    elif timed_out:
        failure_stage = "startup_timeout"
        error = f"MCP preflight exceeded {timeout_seconds:g} seconds"
    elif process is not None and process.returncode != 0:
        failure_stage = "process_exit"
        error = f"MCP process exited with code {process.returncode}"
    elif parse_errors:
        failure_stage = "protocol_parse"
        error = "; ".join(parse_errors)
    elif initialize is None:
        failure_stage = "initialize"
        error = "MCP server returned no initialize response"
    elif "error" in initialize:
        failure_stage = "initialize"
        error = f"MCP initialize failed: {initialize['error']}"
    elif tools_list is None:
        failure_stage = "tools_list"
        error = "MCP server returned no tools/list response"
    elif "error" in tools_list:
        failure_stage = "tools_list"
        error = f"MCP tools/list failed: {tools_list['error']}"
    elif observed_tool_names != expected_tool_names:
        failure_stage = "tool_contract"
        error = "MCP tools/list did not match the task tool contract"

    finished_at = datetime.now(timezone.utc)
    evidence = {
        "schema_version": "1.0",
        "status": "passed" if failure_stage is None else "failed",
        "failure_stage": failure_stage,
        "error": error,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "timeout_seconds": timeout_seconds,
        "command": str(launch.command),
        "arguments": list(launch.arguments),
        "environment": launch.environment,
        "cwd": str(workspace),
        "returncode": process.returncode if process is not None else None,
        "protocol_version": PROTOCOL_VERSION,
        "requests": requests,
        "response_ids": sorted(
            response_id for response_id in responses if isinstance(response_id, int)
        ),
        "stdout_line_count": len(stdout.decode("utf-8", errors="replace").splitlines()),
        "stderr_bytes": len(stderr),
        "parse_errors": parse_errors,
        "expected_tool_names": sorted(expected_tool_names),
        "observed_tool_names": sorted(observed_tool_names),
        "missing_tool_names": sorted(expected_tool_names - observed_tool_names),
        "unexpected_tool_names": sorted(observed_tool_names - expected_tool_names),
        "stdout_path": stdout_path.name,
        "stderr_path": stderr_path.name,
    }
    (raw / "mcp_preflight.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def _monitor_environment_errors(
    process: subprocess.Popen[bytes],
    trace: Path,
    limit: int | None,
    marker: Path,
    poll_seconds: float = 0.1,
    circuit_breaker: EnvironmentErrorCircuitBreaker | None = None,
    tool_budget_marker: Path | None = None,
    cli_tool_counter: _CliToolCallCounter | None = None,
    tool_call_limit: int | None = None,
    upstream_stop_marker: Path | None = None,
) -> None:
    while process.poll() is None:
        if upstream_stop_marker is not None and upstream_stop_marker.is_file():
            _stop_process_group(process, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(poll_seconds)
            if process.poll() is None:
                _stop_process_group(process, signal.SIGKILL)
            return
        if cli_tool_counter is not None and tool_call_limit is not None:
            tool_stats = cli_tool_counter.snapshot()
            if (
                tool_stats["observed_count"] > tool_call_limit
                and tool_budget_marker is not None
                and not tool_budget_marker.is_file()
            ):
                tool_budget_marker.write_text(
                    json.dumps({
                        "reason": "total_tool_call_limit_exceeded",
                        "limit": tool_call_limit,
                        **tool_stats,
                        "count_source": "kimi_cli_stream",
                    }, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        if tool_budget_marker is not None and tool_budget_marker.is_file():
            _stop_process_group(process, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(poll_seconds)
            if process.poll() is None:
                _stop_process_group(process, signal.SIGKILL)
            return
        errors = environment_errors(trace)
        batch_tripped = bool(
            circuit_breaker and circuit_breaker.observe(trace, errors)
        )
        task_tripped = limit is not None and len(errors) >= limit
        if batch_tripped or task_tripped:
            payload = {
                "reason": (
                    "batch_environment_error_limit_reached"
                    if batch_tripped else "environment_error_limit_reached"
                ),
                "limit": circuit_breaker.limit if batch_tripped else limit,
                "observed_count": (
                    circuit_breaker.snapshot()["observed_count"]
                    if batch_tripped else len(errors)
                ),
                "errors": errors,
            }
            if batch_tripped:
                payload["batch"] = circuit_breaker.snapshot()
            marker.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _stop_process_group(process, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(poll_seconds)
            if process.poll() is None:
                _stop_process_group(process, signal.SIGKILL)
            return
        time.sleep(poll_seconds)


def run_k3_distillation(
    prompt: str,
    initial_state: Path,
    server_config: Path | dict[str, Any],
    output_dir: Path,
    *,
    kimi_bin: Path | str,
    base_url: str,
    api_key: str,
    max_context_size: int,
    model_id: str = "kimi-k3",
    model_alias: str = "evaluation",
    provider_type: str = "kimi",
    reasoning_effort: str = KIMI_K3_DEFAULT_EFFORT,
    max_output_size: int = KIMI_K3_OUTPUT_SIZE,
    max_upstream_retries: int = KIMI_UPSTREAM_MAX_RETRIES,
    max_environment_errors: int | None = None,
    environment_error_circuit_breaker: EnvironmentErrorCircuitBreaker | None = None,
    task: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one task through the official streaming Kimi CLI and export its trajectory."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must be non-empty")
    if not isinstance(model_alias, str) or not model_alias.strip():
        raise ValueError("model_alias must be non-empty")
    if provider_type not in PROVIDER_TYPES:
        raise ValueError(f"provider_type must be one of: {', '.join(sorted(PROVIDER_TYPES))}")
    if reasoning_effort not in KIMI_K3_EFFORTS:
        raise ValueError(
            f"reasoning_effort must be one of: {', '.join(KIMI_K3_EFFORTS)}"
        )
    if type(max_upstream_retries) is not int or max_upstream_retries < 0:
        raise ValueError("max_upstream_retries must be a non-negative integer")
    if type(max_context_size) is not int or max_context_size < 1:
        raise ValueError("max_context_size must be a positive integer supported by the upstream model")
    if type(max_output_size) is not int or max_output_size < 1:
        raise ValueError("max_output_size must be a positive integer")
    if max_environment_errors is not None and (
        type(max_environment_errors) is not int or max_environment_errors < 1
    ):
        raise ValueError("max_environment_errors must be a positive integer")
    if model_id == "kimi-k3" and max_context_size not in KIMI_K3_CONTEXT_SIZES:
        raise ValueError(
            "kimi-k3 max_context_size must be one of the supported caps: "
            + ", ".join(str(value) for value in KIMI_K3_CONTEXT_SIZES)
        )
    if model_id == "kimi-k3" and max_output_size >= max_context_size:
        raise ValueError("kimi-k3 max_output_size must be smaller than max_context_size")
    config = _server_config(server_config)
    if config["max_tool_calls"] > KIMI_MAX_TOOL_CALLS:
        raise ValueError(
            f"max_tool_calls cannot exceed the Kimi hard limit of {KIMI_MAX_TOOL_CALLS}"
        )
    executable = _resolve_kimi_bin(kimi_bin)
    # Copying many large initial states concurrently saturates /data1 before
    # model work starts.  Limit only the preparation phase; once a workspace
    # is ready, Kimi and the MCP runtime still use the requested batch
    # concurrency.
    with _STATE_COPY_SEMAPHORE:
        _wait_for_state_copy_capacity()
        paths = _prepare_paths(
            initial_state,
            output_dir,
            environment=config.get("environment", {}),
            workspace_name=_task_workspace_name(config.get("environment", {}), task, output_dir),
        )
    environment_trace = paths.raw / "environment_tool_calls.jsonl"
    config["budget_stop_marker"] = str(paths.raw / "tool_budget_stop.json")
    config["tool_budget_counter"] = str(paths.raw / "tool_budget_counter.json")
    environment_trace.touch()
    config.update({
        "workspace_root": str(paths.model_workspace),
        "record_database": str(paths.record_database),
        "scope_mounts": paths.scope_mounts,
        "trace": str(environment_trace),
    })
    tool_names = [tool["name"] for tool in config["tools"]]
    preflight_tool_names = set(tool_names)
    expected_names = expected_mcp_tool_names(
        config["tools"],
        config.get("environment"),
        server_name="agent_world_distill",
    ) | {"Read", "Grep", "Glob"}
    if "review_choice_seed" in config:
        from task_gen.tool_graph.review_choices import TOOL
        preflight_tool_names.add(TOOL["name"])
        expected_names.add(f"mcp__agent_world_distill__{TOOL['name']}")
    (paths.raw / "harness_policy.json").write_text(json.dumps({
        "harness": "official_kimi_code_cli",
        "streaming_required": True,
        "system_prompt": "kimi_code_default",
        "tools_enabled": ["mcp__agent_world_distill__*", "Read", "Grep", "Glob"],
        "file_tools": ["Read", "Grep", "Glob"],
        "file_access_policy": "pre_tool_use_realpath_allowlist",
        "file_access_allowed": [
            f"{paths.model_workspace.name}/**",
            "current-session/agents/main/tool-results/**",
            "current-session/agents/main/wire.jsonl",
            "current-session kimi-file:// attachments",
        ],
        "tools_disabled": ["Write", "Edit", "Bash", "Agent", "AgentSwarm", "WebSearch", "FetchURL", "AskUserQuestion", "select_tools"],
        "result_reader": "native_kimi_tool_results",
        "tool_call_budget": {
            "limit": config["max_tool_calls"],
            "scope": "environment_mcp_plus_native_file_tools",
        },
        "mcp_runtime_backend": config.get("delivery_runtime", {}).get(
            "backend", "host_python"
        ),
        "mcp_preflight": {
            "enabled": True,
            "timeout_seconds": KIMI_MCP_PREFLIGHT_TIMEOUT_SECONDS,
            "protocol_steps": ["initialize", "notifications/initialized", "tools/list"],
        },
        "docker_mcp_session_concurrency": KIMI_DOCKER_MCP_SESSION_CONCURRENCY,
        "provider_type": provider_type,
        "declared_model_context_size": max_context_size,
        "declared_model_output_size": max_output_size,
        "reasoning_effort": reasoning_effort,
        "relay_normalizations": (
            [
                "thinking.effort -> reasoning_effort",
                "max_tokens -> max_completion_tokens",
                "parallel_tool_calls=true when omitted",
            ]
            if model_id == "kimi-k3" and provider_type == "kimi"
            else []
        ),
        "upstream_retry": {
            "max_retries": max_upstream_retries,
            "base_seconds": KIMI_UPSTREAM_RETRY_BASE_SECONDS,
            "max_seconds": KIMI_UPSTREAM_RETRY_MAX_SECONDS,
            "jitter_seconds": KIMI_UPSTREAM_RETRY_JITTER_SECONDS,
            "retryable_400": "internal MaaS component rejection only",
        },
        "model_parameters_overridden": ["reasoning_effort", "max_output_size"],
    }, indent=2) + "\n", encoding="utf-8")

    process_returncode = 1
    session_id = None
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent-world-kimi-home-") as temporary:
        kimi_home = Path(temporary).resolve()
        private_server = kimi_home / "server.json"
        private_server.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        private_server.chmod(0o600)
        mcp_launch = _task_mcp_launch(config, private_server, paths)
        agent_file = kimi_home / "agent.md"
        _write_agent(agent_file)
        prompt_file = kimi_home / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        prompt_file.chmod(0o600)
        launcher = kimi_home / "launch-kimi.mjs"
        _write_kimi_launcher(launcher)
        skills_dir = kimi_home / "skills"
        skills_dir.mkdir()
        native_file_audit = paths.raw / "native_file_access.jsonl"
        native_file_audit.touch(mode=0o600)
        file_hook_command = build_kimi_file_hook_command(
            paths.model_workspace,
            kimi_home,
            native_file_audit,
            budget_counter=paths.raw / "tool_budget_counter.json",
            budget_stop_marker=paths.raw / "tool_budget_stop.json",
            max_tool_calls=config["max_tool_calls"],
        )

        runtime_backend = config.get("delivery_runtime", {}).get(
            "backend", "host_python"
        )
        mcp_session_guard = (
            _DOCKER_MCP_SESSION_SEMAPHORE
            if runtime_backend == "docker"
            else nullcontext()
        )
        with mcp_session_guard, StreamingRelay(
            base_url,
            api_key,
            paths.raw,
            max_retries=max_upstream_retries,
            retry_base_seconds=KIMI_UPSTREAM_RETRY_BASE_SECONDS,
            retry_max_seconds=KIMI_UPSTREAM_RETRY_MAX_SECONDS,
            retry_jitter_seconds=KIMI_UPSTREAM_RETRY_JITTER_SECONDS,
            first_request_tool_validator=lambda names: _tool_names_match(
                names, expected_names
            ),
            expected_tool_names=expected_names,
            fatal_stream_error_codes={"504001", "upstream_stream_truncated"},
        ) as relay:
            _write_kimi_config(
                kimi_home / "config.toml", relay.base_url, model_alias, model_id,
                max_context_size, provider_type, file_hook_command, reasoning_effort,
                max_output_size,
            )
            mcp_config_path = kimi_home / "mcp.json"
            _write_mcp_config(
                mcp_config_path,
                mcp_launch,
                paths.model_workspace,
                int(config["timeout"]),
            )
            shutil.copy2(mcp_config_path, paths.raw / "mcp.json")
            (paths.raw / "mcp_launch.json").write_text(
                json.dumps({
                    "command": str(mcp_launch.command),
                    "arguments": list(mcp_launch.arguments),
                    "environment": mcp_launch.environment,
                    "cwd": str(paths.model_workspace),
                    "runtime_backend": config.get("delivery_runtime", {}).get(
                        "backend", "host_python"
                    ),
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if runtime_backend == "docker":
                with _DOCKER_MCP_STARTUP_SEMAPHORE:
                    preflight = _run_mcp_preflight(
                        mcp_launch,
                        paths.model_workspace,
                        paths.raw,
                        preflight_tool_names,
                    )
            else:
                preflight = _run_mcp_preflight(
                    mcp_launch,
                    paths.model_workspace,
                    paths.raw,
                    preflight_tool_names,
                )
            if preflight["status"] != "passed":
                startup_failure = {
                    "reason": "mcp_preflight_failed",
                    "runtime_backend": runtime_backend,
                    "failure_stage": preflight["failure_stage"],
                    "error": preflight["error"],
                    "evidence": "mcp_preflight.json",
                }
                (paths.raw / "mcp_startup_failure.json").write_text(
                    json.dumps(startup_failure, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                raise DistillRunError(
                    "MCP preflight failed before Kimi startup; "
                    f"evidence retained at {paths.raw}: {preflight['error']}"
                )
            child_env = dict(os.environ)
            for name, value in list(child_env.items()):
                if value == api_key or name in {"OPENAI_API_KEY", "KIMI_API_KEY"}:
                    child_env.pop(name, None)
            child_env.update({
                "KIMI_CODE_HOME": str(kimi_home),
                "KIMI_CODE_NO_AUTO_UPDATE": "1",
                "KIMI_CLI_NO_AUTO_UPDATE": "1",
                # Shared evaluation hosts can exhaust the per-user inotify
                # instance quota before this batch starts. Kimi Code's
                # Chokidar watcher supports polling without changing session
                # or tool semantics.
                "CHOKIDAR_USEPOLLING": "1",
                "CHOKIDAR_INTERVAL": "1000",
            })
            command = [
                str(launcher), str(executable), str(prompt_file),
                "--output-format", "stream-json",
                "--agent-file", str(agent_file),
                "--skills-dir", str(skills_dir),
                "--model", model_alias,
            ]
            if runtime_backend == "docker":
                _DOCKER_MCP_STARTUP_SEMAPHORE.acquire()
            try:
                process = subprocess.Popen(
                    command,
                    cwd=paths.model_workspace,
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
            except BaseException:
                if runtime_backend == "docker":
                    _DOCKER_MCP_STARTUP_SEMAPHORE.release()
                raise
            assert process.stdout is not None and process.stderr is not None
            stderr_thread = threading.Thread(
                target=_drain,
                args=(process.stderr, paths.raw / "cli_stderr.txt"),
                daemon=True,
            )
            stderr_thread.start()
            monitor_thread = threading.Thread(
                target=_monitor_environment_errors,
                args=(
                    process,
                    environment_trace,
                    max_environment_errors,
                    paths.raw / "environment_stop.json",
                ),
                kwargs={
                    "circuit_breaker": environment_error_circuit_breaker,
                    "tool_budget_marker": paths.raw / "tool_budget_stop.json",
                    "cli_tool_counter": _CliToolCallCounter(
                        paths.raw / "cli_stream.jsonl"
                    ),
                    "tool_call_limit": config["max_tool_calls"],
                    "upstream_stop_marker": paths.raw / "upstream_stop.json",
                },
                daemon=True,
            )
            monitor_thread.start()
            startup_deadline = time.monotonic() + KIMI_MCP_STARTUP_TIMEOUT_SECONDS
            while (
                process.poll() is None
                and not relay.first_model_request_event.wait(0.1)
                and time.monotonic() < startup_deadline
            ):
                pass
            if relay.first_request_tool_failure is not None:
                errors.append("Kimi first model request omitted environment MCP tools")
                _stop_process_group(process, signal.SIGTERM)
            elif not relay.first_model_request_event.is_set():
                startup_failure = {
                    "reason": "first_model_request_startup_timeout",
                    "timeout_seconds": KIMI_MCP_STARTUP_TIMEOUT_SECONDS,
                    "runtime_backend": runtime_backend,
                }
                (paths.raw / "mcp_startup_failure.json").write_text(
                    json.dumps(startup_failure, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                errors.append("Kimi made no model request before MCP startup deadline")
                _stop_process_group(process, signal.SIGTERM)
            if runtime_backend == "docker":
                _DOCKER_MCP_STARTUP_SEMAPHORE.release()
            _drain(process.stdout, paths.raw / "cli_stream.jsonl")
            process_returncode = process.wait()
            stderr_thread.join()
            monitor_thread.join()

        try:
            session = _session_record(kimi_home, paths.model_workspace)
            session_id = _copy_session_evidence(session, kimi_home, paths.raw)
            export = subprocess.run(
                [
                    str(executable), "export", session_id,
                    "--output", str(paths.raw / "kimi_session.zip"),
                    "--yes", "--no-include-global-log",
                ],
                cwd=paths.model_workspace,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            (paths.raw / "export_stdout.txt").write_bytes(export.stdout)
            (paths.raw / "export_stderr.txt").write_bytes(export.stderr)
            if export.returncode != 0:
                errors.append(f"kimi export failed with exit code {export.returncode}")
        except DistillRunError as error:
            errors.append(str(error))

    if (paths.raw / "wire.jsonl").is_file():
        errors.extend(_enrich_model_requests(paths.raw))
        errors.extend(_tool_policy_errors(paths.raw, expected_names))
        result = _wire_summary(
            paths.raw, model_alias, model_id, max_context_size, provider_type, reasoning_effort,
            max_output_size,
        )
    else:
        result = {
            "reason": "failed", "answer": "", "usage": None,
            "model": {
                "alias": model_alias,
                "upstream_id": model_id,
                "provider": provider_type,
                "max_context_size": max_context_size,
                "max_output_size": max_output_size,
                "reasoning_effort": reasoning_effort,
            },
        }
    result.update({
        "session_id": session_id,
        "cli_returncode": process_returncode,
        "environment_stop": (
            json.loads((paths.raw / "environment_stop.json").read_text(encoding="utf-8"))
            if (paths.raw / "environment_stop.json").is_file() else None
        ),
        "tool_budget_stop": (
            json.loads((paths.raw / "tool_budget_stop.json").read_text(encoding="utf-8"))
            if (paths.raw / "tool_budget_stop.json").is_file() else None
        ),
        "upstream_stop": (
            json.loads((paths.raw / "upstream_stop.json").read_text(encoding="utf-8"))
            if (paths.raw / "upstream_stop.json").is_file() else None
        ),
        **({"error": "; ".join(errors)} if errors else {}),
    })
    (paths.raw / "run_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    task_record = dict(task or {})
    task_record["prompt"] = prompt
    environment = _public_environment(config.get("environment", {}))
    environment["tool_names"] = sorted(tool_names)
    trajectory = export_trajectory(
        paths.raw,
        paths.trajectory,
        task=task_record,
        environment=environment,
        evaluation=evaluation,
    )
    validate_trajectory(trajectory)
    outcome = trajectory["outcome"]
    if (
        process_returncode != 0
        or errors
        or outcome["status"] != "completed"
        or not outcome["final_answer"].strip()
    ):
        raise DistillRunError(
            f"Kimi K3 run failed; raw evidence and partial trajectory retained at {paths.output}: "
            f"{outcome.get('error') or outcome['status']}"
        )
    return trajectory


def make_server_config(
    environment: dict[str, Any],
    *,
    max_tool_calls: int = KIMI_DEFAULT_MAX_TOOL_CALLS,
    timeout: int = 300,
    memory_limit: int = 128 * 1024 * 1024 * 1024,
    write_limit: int = 256 * 1024 * 1024,
    process_limit: int = 1024,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the task-scoped MCP configuration used by task-generation bundles."""
    if type(max_tool_calls) is not int or not 1 <= max_tool_calls <= KIMI_MAX_TOOL_CALLS:
        raise ValueError(f"max_tool_calls must be between 1 and {KIMI_MAX_TOOL_CALLS}")
    from task_gen.tool_graph.step_3_chain_execute import _tools

    runtime = runtime or {}
    return {
        "workspace_root": ".",
        "trace": "calls.jsonl",
        "max_tool_calls": max_tool_calls,
        "timeout": timeout,
        "memory_limit": memory_limit,
        "write_limit": write_limit,
        "process_limit": process_limit,
        "tools": list(_tools(environment).values()),
        "environment": environment,
        **({"binding_path": runtime["binding_path"]} if runtime.get("binding_path") else {}),
        **({"tools_path": runtime["tools_path"]} if runtime.get("tools_path") else {}),
        **({"software": runtime["software"]} if runtime.get("software") else {}),
    }
