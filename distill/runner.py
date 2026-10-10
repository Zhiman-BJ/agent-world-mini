"""Official Kimi Code CLI orchestration for K3 trajectory distillation."""

from __future__ import annotations

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
from env_gen.tool_gen.runtime_launch import StdioLaunch, stdio_launch
from task_gen.task_eval_mcp import bind_delivery
from task_gen.tool_graph.step_3_chain_execute import _public_environment

from .export import export_trajectory, validate_trajectory
from .environment_errors import environment_errors
from .relay import StreamingRelay


KIMI_K3_CONTEXT_SIZE = 1_048_576
# A smaller cap is useful for controlled reruns without changing the default
# official K3 window. The CLI still receives the selected cap as its model
# metadata and the relay preserves it in the recorded trajectory.
KIMI_K3_CONTEXT_SIZES = (262_144, KIMI_K3_CONTEXT_SIZE)
KIMI_K3_EFFORTS = ("low", "high", "max")
KIMI_K3_DEFAULT_EFFORT = "high"
KIMI_UPSTREAM_MAX_RETRIES = 4
KIMI_UPSTREAM_RETRY_BASE_SECONDS = 1.0
KIMI_UPSTREAM_RETRY_MAX_SECONDS = 30.0
KIMI_UPSTREAM_RETRY_JITTER_SECONDS = 0.25
KIMI_MCP_STARTUP_TIMEOUT_SECONDS = 40.0
KIMI_DOCKER_MCP_STARTUP_CONCURRENCY = 4
PROVIDER_TYPES = {"kimi"}


_DOCKER_MCP_STARTUP_SEMAPHORE = threading.BoundedSemaphore(
    KIMI_DOCKER_MCP_STARTUP_CONCURRENCY
)


class DistillRunError(RuntimeError):
    """The raw run was retained but Kimi did not complete the task."""


DistillPaths = TaskRunLayout


def _prepare_paths(initial_state: Path, output_dir: Path) -> DistillPaths:
    return create_task_run_layout(initial_state, output_dir)


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
) -> None:
    if not isinstance(file_hook_command, str) or not file_hook_command.strip():
        raise ValueError("file_hook_command must be non-empty")
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
        'display_name = "Kimi K3 Distillation"\n'
        f"max_context_size = {max_context_size}\n"
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

Native `Read`, `Grep`, and `Glob` are only for files in the empty model workspace,
the current session's saved long tool results, and the current session's wire log.
Do not use them to search the environment Scope, `execution-state`, delivery
directories, host paths, or another task. Use the environment MCP tools to inspect
and modify business state; a long MCP result can be recovered from the path Kimi
provides in the current session.

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
                "startupTimeoutMs": 30000,
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
    arguments = [
        str(server_path),
        "--binding", str(delivery.binding_path),
        "--state-root", str(paths.execution_state),
        "--trace", str(trace),
        "--max-tool-calls", str(config["max_tool_calls"]),
        "--timeout", str(config["timeout"]),
        "--memory-limit", str(config["memory_limit"]),
        "--write-limit", str(config["write_limit"]),
        "--process-limit", str(config["process_limit"]),
    ]
    writable_paths = [paths.execution_state, trace]
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
            "display_name": "Kimi K3",
            "provider": provider_type,
            "max_context_size": max_context_size,
            "max_output_size": None,
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
    unmatched = set(expected)
    for name in observed:
        if name in unmatched:
            unmatched.remove(name)
            continue
        suffix = re.search(
            r"(?:_[0-9a-f]{8}|-[0-9a-f]{8}|_-[0-9a-f]{7,8})$", name
        )
        candidates = [
            candidate for candidate in unmatched
            if len(candidate) > 64
            and len(name) == 64
            # Kimi Code shortens at different safe boundaries across versions.
            # Match the preserved prefix rather than a fixed cut position.
            and suffix is not None
            and candidate.startswith(name[:suffix.start()])
        ]
        if len(candidates) != 1:
            return False
        unmatched.remove(candidates[0])
    return not unmatched


def _drain(stream: Any, target: Path) -> None:
    read = getattr(stream, "read1", stream.read)
    with target.open("wb") as output:
        while True:
            chunk = read(65536)
            if not chunk:
                break
            output.write(chunk)
            output.flush()


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


def _monitor_environment_errors(
    process: subprocess.Popen[bytes],
    trace: Path,
    limit: int,
    marker: Path,
    poll_seconds: float = 0.1,
) -> None:
    while process.poll() is None:
        errors = environment_errors(trace)
        if len(errors) >= limit:
            marker.write_text(json.dumps({
                "reason": "environment_error_limit_reached",
                "limit": limit,
                "observed_count": len(errors),
                "errors": errors,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    max_upstream_retries: int = KIMI_UPSTREAM_MAX_RETRIES,
    max_environment_errors: int | None = None,
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
    if max_environment_errors is not None and (
        type(max_environment_errors) is not int or max_environment_errors < 1
    ):
        raise ValueError("max_environment_errors must be a positive integer")
    if model_id == "kimi-k3" and max_context_size not in KIMI_K3_CONTEXT_SIZES:
        raise ValueError(
            "kimi-k3 max_context_size must be one of the supported caps: "
            + ", ".join(str(value) for value in KIMI_K3_CONTEXT_SIZES)
        )
    executable = _resolve_kimi_bin(kimi_bin)
    paths = _prepare_paths(initial_state, output_dir)
    config = _server_config(server_config)
    environment_trace = paths.raw / "environment_tool_calls.jsonl"
    environment_trace.touch()
    config.update({
        "state_root": str(paths.execution_state),
        "workspace": str(paths.execution_state),
        "trace": str(environment_trace),
    })
    tool_names = [tool["name"] for tool in config["tools"]]
    expected_names = expected_mcp_tool_names(
        config["tools"],
        config.get("environment"),
        server_name="agent_world_distill",
    ) | {"Read", "Grep", "Glob"}
    if "review_choice_seed" in config:
        from task_gen.tool_graph.review_choices import TOOL
        expected_names.add(f"mcp__agent_world_distill__{TOOL['name']}")
    (paths.raw / "harness_policy.json").write_text(json.dumps({
        "harness": "official_kimi_code_cli",
        "streaming_required": True,
        "system_prompt": "kimi_code_default",
        "tools_enabled": ["mcp__agent_world_distill__*", "Read", "Grep", "Glob"],
        "file_tools": ["Read", "Grep", "Glob"],
        "file_access_policy": "pre_tool_use_realpath_allowlist",
        "file_access_allowed": [
            "model-workspace/**",
            "current-session/agents/main/tool-results/**",
            "current-session/agents/main/wire.jsonl",
            "current-session kimi-file:// attachments",
        ],
        "tools_disabled": ["Write", "Edit", "Bash", "Agent", "AgentSwarm", "WebSearch", "FetchURL", "AskUserQuestion", "select_tools"],
        "result_reader": "native_kimi_tool_results",
        "mcp_runtime_backend": config.get("delivery_runtime", {}).get(
            "backend", "host_python"
        ),
        "provider_type": provider_type,
        "declared_model_context_size": max_context_size,
        "reasoning_effort": reasoning_effort,
        "relay_normalizations": [
            "thinking.effort -> reasoning_effort",
            "max_tokens -> max_completion_tokens",
            "parallel_tool_calls=true when omitted",
        ],
        "upstream_retry": {
            "max_retries": max_upstream_retries,
            "base_seconds": KIMI_UPSTREAM_RETRY_BASE_SECONDS,
            "max_seconds": KIMI_UPSTREAM_RETRY_MAX_SECONDS,
            "jitter_seconds": KIMI_UPSTREAM_RETRY_JITTER_SECONDS,
            "retryable_400": "internal MaaS component rejection only",
        },
        "model_parameters_overridden": ["reasoning_effort"],
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
        )

        with StreamingRelay(
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
        ) as relay:
            _write_kimi_config(
                kimi_home / "config.toml", relay.base_url, model_alias, model_id,
                max_context_size, provider_type, file_hook_command, reasoning_effort,
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
            child_env = dict(os.environ)
            for name, value in list(child_env.items()):
                if value == api_key or name in {"OPENAI_API_KEY", "KIMI_API_KEY"}:
                    child_env.pop(name, None)
            child_env.update({
                "KIMI_CODE_HOME": str(kimi_home),
                "KIMI_CODE_NO_AUTO_UPDATE": "1",
                "KIMI_CLI_NO_AUTO_UPDATE": "1",
            })
            command = [
                str(launcher), str(executable), str(prompt_file),
                "--output-format", "stream-json",
                "--agent-file", str(agent_file),
                "--skills-dir", str(skills_dir),
                "--model", model_alias,
            ]
            docker_startup_slot = (
                _DOCKER_MCP_STARTUP_SEMAPHORE
                if config.get("delivery_runtime", {}).get("backend") == "docker"
                else None
            )
            if docker_startup_slot is not None:
                docker_startup_slot.acquire()
            try:
                process = subprocess.Popen(
                    command,
                    cwd=paths.model_workspace,
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
                assert process.stdout is not None and process.stderr is not None
                stderr_thread = threading.Thread(
                    target=_drain,
                    args=(process.stderr, paths.raw / "cli_stderr.txt"),
                    daemon=True,
                )
                stderr_thread.start()
                monitor_thread = None
                if max_environment_errors is not None:
                    monitor_thread = threading.Thread(
                        target=_monitor_environment_errors,
                        args=(
                            process,
                            environment_trace,
                            max_environment_errors,
                            paths.raw / "environment_stop.json",
                        ),
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
                        "runtime_backend": config.get("delivery_runtime", {}).get(
                            "backend", "host_python"
                        ),
                    }
                    (paths.raw / "mcp_startup_failure.json").write_text(
                        json.dumps(startup_failure, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    errors.append("Kimi made no model request before MCP startup deadline")
                    _stop_process_group(process, signal.SIGTERM)
            finally:
                if docker_startup_slot is not None:
                    docker_startup_slot.release()
            _drain(process.stdout, paths.raw / "cli_stream.jsonl")
            process_returncode = process.wait()
            stderr_thread.join()
            if monitor_thread is not None:
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
            paths.raw, model_alias, model_id, max_context_size, provider_type, reasoning_effort
        )
    else:
        result = {
            "reason": "failed", "answer": "", "usage": None,
            "model": {
                "alias": model_alias,
                "upstream_id": model_id,
                "provider": provider_type,
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
    max_tool_calls: int = 100,
    timeout: int = 300,
    memory_limit: int = 128 * 1024 * 1024 * 1024,
    write_limit: int = 256 * 1024 * 1024,
    process_limit: int = 1024,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the task-scoped MCP configuration used by task-generation bundles."""
    from task_gen.tool_graph.step_3_chain_execute import _tools

    runtime = runtime or {}
    return {
        "state_root": ".",
        "trace": "calls.jsonl",
        "max_tool_calls": max_tool_calls,
        "timeout": timeout,
        "memory_limit": memory_limit,
        "write_limit": write_limit,
        "process_limit": process_limit,
        "tools": list(_tools(environment).values()),
        "environment": environment,
        **({"binding_path": runtime["binding_path"]} if runtime.get("binding_path") else {}),
        **({"software": runtime["software"]} if runtime.get("software") else {}),
    }
