import json
import os
import re
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import tomllib
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def test_state_copy_waits_until_io_pressure_recovers(monkeypatch):
    from distill import runner

    readings = iter([35.0, 12.0, 9.0])
    sleeps = []
    monkeypatch.setattr(runner, "_io_full_pressure_avg10", lambda: next(readings))
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)

    runner._wait_for_state_copy_capacity()

    assert sleeps == [runner.KIMI_STATE_COPY_IO_POLL_SECONDS] * 2


def test_state_copy_pressure_limit_can_be_overridden_for_low_priority_runs(monkeypatch):
    from distill import runner

    readings = iter([46.0, 44.0])
    sleeps = []
    monkeypatch.setenv("KIMI_STATE_COPY_IO_PRESSURE_LIMIT", "45")
    monkeypatch.setattr(runner, "_io_full_pressure_avg10", lambda: next(readings))
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)

    runner._wait_for_state_copy_capacity()

    assert sleeps == [runner.KIMI_STATE_COPY_IO_POLL_SECONDS]


def test_cli_tool_call_counter_includes_attachment_reads_incrementally(tmp_path):
    from distill.runner import _CliToolCallCounter

    stream = tmp_path / "cli_stream.jsonl"
    counter = _CliToolCallCounter(stream)

    stream.write_text(
        json.dumps({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "mcp_1",
                    "function": {"name": "mcp__environment__inspect"},
                },
                {
                    "id": "read_1",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "kimi-file://result/1"}),
                    },
                },
            ],
        }) + "\n",
        encoding="utf-8",
    )

    assert counter.snapshot() == {
        "observed_count": 2,
        "native_file_tool_calls": 1,
        "environment_mcp_tool_calls": 1,
        "other_tool_calls": 0,
    }

    with stream.open("a", encoding="utf-8") as output:
        output.write(json.dumps({
            "role": "assistant",
            "tool_calls": [
                {"id": "read_1", "function": {"name": "Read"}},
                {"id": "glob_1", "function": {"name": "Glob"}},
            ],
        }) + "\n")

    assert counter.snapshot() == {
        "observed_count": 3,
        "native_file_tool_calls": 2,
        "environment_mcp_tool_calls": 1,
        "other_tool_calls": 0,
    }


def test_runner_stops_before_executing_tool_call_above_budget(tmp_path, monkeypatch):
    from distill import runner

    class Process:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

    process = Process()
    stream = tmp_path / "cli_stream.jsonl"
    stream.write_text(json.dumps({
        "role": "assistant",
        "tool_calls": [
            {
                "id": f"call_{index}",
                "function": {"name": "Read" if index == 65 else "mcp__env__inspect"},
            }
            for index in range(66)
        ],
    }) + "\n", encoding="utf-8")
    marker = tmp_path / "tool_budget_stop.json"

    monkeypatch.setattr(
        runner,
        "_stop_process_group",
        lambda observed_process, _signal: setattr(observed_process, "stopped", True),
    )
    runner._monitor_environment_errors(
        process,
        tmp_path / "environment_tool_calls.jsonl",
        None,
        tmp_path / "environment_stop.json",
        tool_budget_marker=marker,
        cli_tool_counter=runner._CliToolCallCounter(stream),
        tool_call_limit=65,
    )

    assert process.stopped is True
    assert json.loads(marker.read_text()) == {
        "reason": "total_tool_call_limit_exceeded",
        "limit": 65,
        "observed_count": 66,
        "native_file_tool_calls": 1,
        "environment_mcp_tool_calls": 65,
        "other_tool_calls": 0,
        "count_source": "kimi_cli_stream",
    }


def test_runner_stops_after_fatal_upstream_stream_error(tmp_path, monkeypatch):
    from distill import runner

    class Process:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

    process = Process()
    marker = tmp_path / "upstream_stop.json"
    marker.write_text('{"reason":"fatal_upstream_stream_error"}\n', encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_stop_process_group",
        lambda observed_process, _signal: setattr(observed_process, "stopped", True),
    )

    runner._monitor_environment_errors(
        process,
        tmp_path / "environment_tool_calls.jsonl",
        None,
        tmp_path / "environment_stop.json",
        upstream_stop_marker=marker,
    )

    assert process.stopped is True


def test_kimi_tool_schema_splits_mixed_enum_without_mutating_source():
    from task_gen.task_eval_kimi_mcp import _normalize_kimi_tool_schema

    source = {
        "type": "object",
        "properties": {
            "aggregation": {
                "type": ["string", "null"],
                "enum": ["mean", "min", "max", None],
                "default": None,
            },
            "mode": {"type": "string", "enum": ["fast", "exact"]},
        },
    }

    normalized = _normalize_kimi_tool_schema(source)

    assert source["properties"]["aggregation"]["enum"][-1] is None
    aggregation = normalized["properties"]["aggregation"]
    assert "enum" not in aggregation
    assert "type" not in aggregation
    assert aggregation["default"] is None
    assert aggregation["anyOf"] == [
        {"type": "string", "enum": ["mean", "min", "max"]},
        {"type": "null", "enum": [None]},
    ]
    assert normalized["properties"]["mode"] == source["properties"]["mode"]


def test_kimi_launcher_loads_prompt_after_execve(tmp_path):
    from distill.runner import _write_kimi_launcher

    if shutil.which("node") is None:
        pytest.skip("node is required by the official Kimi CLI")
    launcher = tmp_path / "launch-kimi.mjs"
    _write_kimi_launcher(launcher)
    prompt = "x" * 200_000
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    output = tmp_path / "argv.json"
    fake_cli = tmp_path / "fake-cli.mjs"
    fake_cli.write_text(
        "import {writeFileSync} from 'node:fs';"
        "writeFileSync(process.env.ARGV_OUTPUT, JSON.stringify(process.argv));",
        encoding="utf-8",
    )

    subprocess.run(
        [str(launcher), str(fake_cli), str(prompt_path), "--model", "test"],
        env={**os.environ, "ARGV_OUTPUT": str(output)},
        check=True,
    )

    argv = json.loads(output.read_text(encoding="utf-8"))
    assert argv[1:] == [str(fake_cli), "--prompt", prompt, "--model", "test"]


def test_kimi_config_declares_official_k3_metadata(tmp_path):
    from distill.runner import KIMI_K3_CONTEXT_SIZE, KIMI_K3_OUTPUT_SIZE, _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(
        path,
        "http://127.0.0.1:1234/v1",
        "evaluation",
        "kimi-k3",
        KIMI_K3_CONTEXT_SIZE,
        "kimi",
        "python /private/kimi_file_policy.py",
    )

    config = tomllib.loads(path.read_text(encoding="utf-8"))
    provider = config["providers"]["agent_world_distill"]
    model = config["models"]["evaluation"]
    assert provider["type"] == "kimi"
    assert model["max_context_size"] == 1_048_576
    assert model["max_output_size"] == KIMI_K3_OUTPUT_SIZE == 131_072
    assert model["capabilities"] == ["thinking", "always_thinking", "tool_use"]
    assert model["support_efforts"] == ["low", "high", "max"]
    assert model["default_effort"] == "max"


def test_kimi_config_can_select_max_reasoning_effort(tmp_path):
    from distill.runner import KIMI_K3_CONTEXT_SIZE, _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(
        path,
        "http://127.0.0.1:1234/v1",
        "evaluation",
        "kimi-k3",
        KIMI_K3_CONTEXT_SIZE,
        "kimi",
        "python /private/kimi_file_policy.py",
        "max",
    )

    assert tomllib.loads(path.read_text(encoding="utf-8"))["models"]["evaluation"][
        "default_effort"
    ] == "max"


def test_kimi_config_preserves_explicit_provider_headers(tmp_path):
    from distill.runner import _write_kimi_config

    path = tmp_path / "config.toml"
    headers = {"User-Agent": "codex_cli_rs/0.115.0", "originator": "codex_cli_rs",
               "OpenAI-Beta": "responses=experimental"}
    _write_kimi_config(path, "http://127.0.0.1:1234/v1", "evaluation", "test-model", 262144,
                       "openai_responses", "python /private/hook.py", provider_headers=headers)
    provider = tomllib.loads(path.read_text())["providers"]["agent_world_distill"]
    assert provider["custom_headers"] == headers
    assert provider["api_key"] == "relay-only-not-an-upstream-credential"


def test_provider_header_configuration_rejects_invalid_names_and_values():
    import pytest
    from distill.runner import _validate_provider_headers

    for headers in ([], {"bad name": "value"}, {"valid": "line\r\nbreak"}, {"valid": 1}):
        with pytest.raises(ValueError):
            _validate_provider_headers(headers)


def test_early_compaction_preserves_the_declared_context(tmp_path):
    from distill.runner import _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(path, "http://127.0.0.1:1234/v1", "evaluation", "kimi-k3",
                       262144, "kimi", "python /private/hook.py",
                       compaction_trigger_ratio=0.5)
    config = tomllib.loads(path.read_text())
    assert config["models"]["evaluation"]["max_context_size"] == 262144
    assert config["models"]["evaluation"]["max_output_size"] == 131072
    assert config["loop_control"]["compaction_trigger_ratio"] == 0.5
    assert config["loop_control"]["reserved_context_size"] == 131072


def test_read_limits_are_explicit_in_official_cli_config(tmp_path):
    from distill.runner import _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(path, "http://127.0.0.1:1234/v1", "evaluation", "kimi-k3",
                       262144, "kimi", "python /private/hook.py")
    assert tomllib.loads(path.read_text())["read"] == {
        "default_max_chars": 80000, "max_chars": 128000,
    }
    _write_kimi_config(path, "http://127.0.0.1:1234/v1", "evaluation", "kimi-k3",
                       262144, "kimi", "python /private/hook.py",
                       read_default_max_chars=25000, read_max_chars=50000)
    assert tomllib.loads(path.read_text())["read"] == {
        "default_max_chars": 25000, "max_chars": 50000,
    }


def test_invalid_read_limits_are_rejected():
    import pytest
    from distill.runner import _validate_read_limits

    for default, maximum in ((0, 128000), (80000, -1), (128001, 128000), (True, 128000)):
        with pytest.raises(ValueError):
            _validate_read_limits(default, maximum)


def test_mcp_config_uses_extended_startup_timeout(tmp_path):
    from distill.runner import (
        KIMI_MCP_CLIENT_STARTUP_TIMEOUT_SECONDS,
        _write_mcp_config,
    )
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    path = tmp_path / "mcp.json"
    _write_mcp_config(
        path,
        StdioLaunch(Path("python"), ("server.py",), {}),
        tmp_path,
        300,
    )

    server = json.loads(path.read_text())["mcpServers"]["agent_world_distill"]
    assert server["startupTimeoutMs"] == KIMI_MCP_CLIENT_STARTUP_TIMEOUT_SECONDS * 1000
    assert server["toolTimeoutMs"] == 300_000


def test_workspace_namespace_matches_mcp_registration_and_agent_allowlist(tmp_path):
    from distill.runner import _write_agent, _write_mcp_config
    from env_gen.tool_gen.runtime_launch import StdioLaunch
    from harness.mcp_tools import expected_mcp_tool_names

    agent = tmp_path / "agent.md"
    mcp = tmp_path / "mcp.json"
    _write_agent(agent, "workspace")
    _write_mcp_config(mcp, StdioLaunch(Path("python"), ("server.py",), {}), tmp_path, 300, "workspace")
    assert set(json.loads(mcp.read_text())["mcpServers"]) == {"workspace"}
    assert "mcp__workspace__*" in agent.read_text()
    assert "mcp__agent_world_distill__*" not in agent.read_text()
    assert expected_mcp_tool_names([{"name": "inspect"}], {}, server_name="workspace") == {
        "mcp__workspace__inspect"}


@pytest.mark.parametrize("server_name", ["", "../workspace", "name with spaces", "x" * 33])
def test_runner_rejects_invalid_mcp_namespace_before_startup(tmp_path, server_name):
    from distill.runner import run_k3_distillation

    with pytest.raises(ValueError, match="mcp_server_name"):
        run_k3_distillation("Inspect.", tmp_path, {}, tmp_path / "run", kimi_bin="unused",
                            base_url="http://unused", api_key="unused", max_context_size=262144,
                            mcp_server_name=server_name)


def test_mcp_preflight_validates_initialize_and_tool_contract(tmp_path):
    from distill.runner import _run_mcp_preflight
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    server = tmp_path / "fake_mcp.py"
    server.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        " request = json.loads(line)\n"
        " if request.get('id') == 1:\n"
        "  print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': "
        "{'protocolVersion': '2025-06-18', 'capabilities': {}, "
        "'serverInfo': {'name': 'fake', 'version': '1'}}}), flush=True)\n"
        " if request.get('id') == 2:\n"
        "  print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': "
        "{'tools': [{'name': 'inspect', 'inputSchema': {'type': 'object'}}]}}), flush=True)\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw"

    evidence = _run_mcp_preflight(
        StdioLaunch(Path(sys.executable), (str(server),), {}),
        tmp_path,
        raw,
        {"inspect"},
        timeout_seconds=5,
    )

    assert evidence["status"] == "passed"
    assert evidence["failure_stage"] is None
    assert evidence["response_ids"] == [1, 2]
    assert evidence["observed_tool_names"] == ["inspect"]
    assert (raw / "mcp_preflight.stdout.jsonl").is_file()
    assert json.loads((raw / "mcp_preflight.json").read_text())["status"] == "passed"


def test_mcp_preflight_records_process_failure_and_stderr(tmp_path):
    from distill.runner import _run_mcp_preflight
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    server = tmp_path / "broken_mcp.py"
    server.write_text(
        "import sys\n"
        "sys.stderr.write('docker daemon unavailable\\n')\n"
        "raise SystemExit(23)\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw"

    evidence = _run_mcp_preflight(
        StdioLaunch(Path(sys.executable), (str(server),), {}),
        tmp_path,
        raw,
        {"inspect"},
        timeout_seconds=5,
    )

    assert evidence["status"] == "failed"
    assert evidence["failure_stage"] == "process_exit"
    assert evidence["returncode"] == 23
    assert evidence["missing_tool_names"] == ["inspect"]
    assert "docker daemon unavailable" in (
        raw / "mcp_preflight.stderr.txt"
    ).read_text()


def test_mcp_preflight_records_spawn_failure(tmp_path):
    from distill.runner import _run_mcp_preflight
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    raw = tmp_path / "raw"
    evidence = _run_mcp_preflight(
        StdioLaunch(tmp_path / "missing-mcp-command", (), {}),
        tmp_path,
        raw,
        {"inspect"},
        timeout_seconds=5,
    )

    assert evidence["status"] == "failed"
    assert evidence["failure_stage"] == "process_spawn"
    assert evidence["returncode"] is None
    assert "FileNotFoundError" in evidence["error"]
    assert "FileNotFoundError" in (raw / "mcp_preflight.stderr.txt").read_text()


def test_mcp_preflight_rejects_wrong_tool_set(tmp_path):
    from distill.runner import _run_mcp_preflight
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    server = tmp_path / "wrong_tools_mcp.py"
    server.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        " request = json.loads(line)\n"
        " if request.get('id') == 1:\n"
        "  print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}), flush=True)\n"
        " if request.get('id') == 2:\n"
        "  print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': "
        "{'tools': [{'name': 'other'}]}}), flush=True)\n",
        encoding="utf-8",
    )

    evidence = _run_mcp_preflight(
        StdioLaunch(Path(sys.executable), (str(server),), {}),
        tmp_path,
        tmp_path / "raw",
        {"inspect"},
        timeout_seconds=5,
    )

    assert evidence["status"] == "failed"
    assert evidence["failure_stage"] == "tool_contract"
    assert evidence["missing_tool_names"] == ["inspect"]
    assert evidence["unexpected_tool_names"] == ["other"]


def test_kimi_config_registers_native_file_access_hook(tmp_path):
    from distill.runner import KIMI_K3_CONTEXT_SIZE, _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(
        path,
        "http://127.0.0.1:1234/v1",
        "evaluation",
        "kimi-k3",
        KIMI_K3_CONTEXT_SIZE,
        "kimi",
        "python /private/kimi_file_policy.py",
    )

    config = tomllib.loads(path.read_text(encoding="utf-8"))
    assert config["hooks"] == [{
        "event": "PreToolUse",
        "matcher": "^(Read|Grep|Glob)$",
        "command": "python /private/kimi_file_policy.py",
        "timeout": 5,
    }]


def test_atomic_tool_budget_is_shared_across_callers(tmp_path):
    from harness.tool_budget import claim_tool_call

    counter = tmp_path / "counter.json"
    marker = tmp_path / "stop.json"

    assert claim_tool_call(counter, marker, 2, "inspect")["allowed"] is True
    assert claim_tool_call(counter, marker, 2, "Read")["allowed"] is True
    rejected = claim_tool_call(counter, marker, 2, "Grep")

    assert rejected == {
        "allowed": False,
        "reason": "total_tool_call_limit_exceeded",
        "limit": 2,
        "observed_count": 2,
        "attempted_tool": "Grep",
    }
    assert json.loads(marker.read_text()) == rejected


def test_kimi_k3_request_normalization_uses_public_chat_contract():
    from distill.relay import _normalize_kimi_k3_chat_request

    request, changes = _normalize_kimi_k3_chat_request({
        "model": "kimi-k3",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "effort": "high", "keep": "all"},
        "max_tokens": 131072,
        "stream": True,
    })

    assert request["reasoning_effort"] == "high"
    assert request["max_completion_tokens"] == 131072
    assert "thinking" not in request
    assert "max_tokens" not in request
    assert request["parallel_tool_calls"] is True
    assert changes == [
        "drop_thinking",
        "thinking_effort_to_reasoning_effort",
        "max_tokens_to_max_completion_tokens",
        "parallel_tool_calls=true",
    ]


def test_kimi_k3_request_normalization_rejects_unsupported_effort():
    from distill.relay import _normalize_kimi_k3_chat_request

    with pytest.raises(ValueError, match="unsupported kimi-k3 reasoning effort"):
        _normalize_kimi_k3_chat_request({
            "model": "kimi-k3",
            "thinking": {"type": "enabled", "effort": "medium"},
        })


def test_relay_retries_only_internal_maas_400():
    from distill.relay import StreamingRelay

    internal = json.dumps({
        "error": {
            "message": "The request is invalid: the request was rejected by an internal MaaS component."
        }
    }).encode()
    ordinary = json.dumps({
        "error": {"message": "invalid request body"}
    }).encode()

    assert StreamingRelay._retryable_response(400, internal)
    assert not StreamingRelay._retryable_response(400, ordinary)
    assert StreamingRelay._retryable_response(503, b"temporary")
    assert not StreamingRelay._retryable_response(401, b"unauthorized")


def test_relay_detects_error_inside_successful_sse_response(tmp_path):
    from distill.relay import _stream_error

    response = tmp_path / "response.sse"
    response.write_text(
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        '{"error":{"type":"timeout_error","code":"504001",'
        '"message":"upstream timed out","source":"upstream"}}\n'
        'event: error\n'
        'data: {"error":{"type":"upstream_error",'
        '"code":"upstream_stream_truncated"}}\n\n',
        encoding="utf-8",
    )

    assert _stream_error(response) == {
        "type": "timeout_error",
        "code": "504001",
        "message": "upstream timed out",
        "source": "upstream",
    }


def test_relay_ignores_error_text_inside_normal_sse_content(tmp_path):
    from distill.relay import _stream_error

    response = tmp_path / "response.sse"
    response.write_text(
        'data: {"choices":[{"delta":{"content":"the error is fixed"}}]}\n\n'
        'data: [DONE]\n\n',
        encoding="utf-8",
    )

    assert _stream_error(response) is None


def test_relay_retry_delay_is_exponential_and_bounded(tmp_path):
    from distill.relay import StreamingRelay

    with StreamingRelay(
        "http://127.0.0.1:1/v1",
        "test-key-never-written",
        tmp_path / "raw",
        retry_base_seconds=1,
        retry_max_seconds=3,
        retry_jitter_seconds=0,
    ) as relay:
        assert [relay._retry_delay(index) for index in range(1, 5)] == [1, 2, 3, 3]


def test_relay_blocks_missing_mcp_tools_before_upstream(tmp_path, streaming_model):
    from distill.relay import StreamingRelay

    base_url, requests = streaming_model
    raw = tmp_path / "raw"
    expected = {"Read", "mcp__agent_world_distill__inspect"}
    harness_request = {
        "model": "kimi-k3",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{
            "type": "function",
            "function": {"name": "Read", "parameters": {"type": "object"}},
        }],
        "stream": True,
    }

    with StreamingRelay(
        base_url,
        "test-key-never-written",
        raw,
        first_request_tool_validator=lambda names: names == expected,
        expected_tool_names=expected,
    ) as relay:
        request = Request(
            relay.base_url + "/chat/completions",
            data=json.dumps(harness_request).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as captured:
            urlopen(request, timeout=5)
        assert captured.value.code == 503
        assert relay.first_model_request_event.is_set()
        assert relay.first_request_tool_failure is not None

    assert requests == []
    failure = json.loads((raw / "mcp_startup_failure.json").read_text())
    assert failure["reason"] == "mcp_tools_missing_from_first_model_request"
    assert failure["expected_tool_names"] == sorted(expected)
    assert failure["observed_tool_names"] == ["Read"]
    response = json.loads((raw / "model_responses.jsonl").read_text())
    assert response["origin"] == "local_harness"


def test_streaming_relay_forwards_public_k3_contract_and_keeps_both_requests(
    tmp_path, streaming_model
):
    from distill.relay import StreamingRelay

    base_url, requests = streaming_model
    raw = tmp_path / "raw"
    harness_request = {
        "model": "kimi-k3",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "effort": "high", "keep": "all"},
        "max_tokens": 131072,
        "stream": True,
    }

    with StreamingRelay(base_url, "test-key-never-written", raw) as relay:
        request = Request(
            relay.base_url + "/chat/completions",
            data=json.dumps(harness_request).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert "data: [DONE]" in response.read().decode()

    assert len(requests) == 1
    upstream_request = requests[0]
    assert upstream_request["reasoning_effort"] == "high"
    assert upstream_request["max_completion_tokens"] == 131072
    assert upstream_request["parallel_tool_calls"] is True
    assert "thinking" not in upstream_request
    assert "max_tokens" not in upstream_request
    assert json.loads((raw / "model_io/000001.request.json").read_text()) == upstream_request
    assert json.loads((raw / "model_io/000001.harness-request.json").read_text()) == harness_request
    record = json.loads((raw / "model_requests.jsonl").read_text())
    assert record["request"] == upstream_request
    assert record["harness_request"] == harness_request


def test_streaming_relay_maps_backend_effort_and_records_original_request(tmp_path, streaming_model):
    from distill.relay import StreamingRelay

    base_url, requests = streaming_model
    raw = tmp_path / "raw"
    harness_request = {
        "model": "kimi-k3", "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "effort": "max"}, "max_tokens": 131072, "stream": True,
    }
    with StreamingRelay(base_url, "test-key", raw, reasoning_effort_map={"max": "xhigh"}) as relay:
        request = Request(relay.base_url + "/chat/completions", data=json.dumps(harness_request).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            assert "data: [DONE]" in response.read().decode()

    assert requests[0]["reasoning_effort"] == "xhigh"
    assert requests[0]["messages"] == harness_request["messages"]
    assert harness_request["thinking"]["effort"] == "max"
    record = json.loads((raw / "model_requests.jsonl").read_text())
    assert record["harness_request"] == harness_request
    assert record["request"] == requests[0]
    assert "reasoning_effort:max->xhigh" in record["normalizations"]


@pytest.mark.parametrize("mapping", [[], {"max": ""}, {"": "xhigh"}, {"max": None}])
def test_streaming_relay_rejects_invalid_effort_map(tmp_path, mapping):
    from distill.relay import StreamingRelay

    with pytest.raises(ValueError, match="reasoning_effort_map"):
        StreamingRelay("http://127.0.0.1:1/v1", "test-key", tmp_path / "raw", reasoning_effort_map=mapping)


def test_server_config_uses_scientific_runtime_defaults():
    from distill.runner import make_server_config

    tool = {
        "name": "inspect",
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
        "internal": {"code": "def run(arguments, context): return {'success': True}"},
    }
    config = make_server_config({"tools": [tool]})

    assert config["memory_limit"] == 128 * 1024 * 1024 * 1024
    assert config["process_limit"] == 1024


def test_host_mcp_launch_preserves_virtual_environment_interpreter(tmp_path, monkeypatch):
    from distill.runner import _task_mcp_launch

    base = tmp_path / "base-python"
    base.touch()
    interpreter = tmp_path / "venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(base)
    monkeypatch.setattr("distill.runner.sys.executable", str(interpreter))
    launch = _task_mcp_launch({}, tmp_path / "server.json", None)
    assert launch.command == interpreter
    assert launch.command != interpreter.resolve()


@pytest.mark.parametrize("author_validation", [False, True])
def test_task_mcp_launch_uses_delivery_runtime(tmp_path, monkeypatch, author_validation):
    from types import SimpleNamespace

    from distill.runner import _task_mcp_launch
    from env_gen.tool_gen.runtime_launch import StdioLaunch

    state = tmp_path / "execution-state"
    state.mkdir()
    trace = tmp_path / "raw/environment_tool_calls.jsonl"
    trace.parent.mkdir()
    private_server = tmp_path / "server.json"
    private_server.write_text("{}", encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text("{}", encoding="utf-8")
    delivery = SimpleNamespace(
        binding_path=binding,
        runtime={
            "schema_version": "1.0",
            "backend": "docker",
            "image": "agentworld/test:latest",
            "software_root": "/opt/tool-software",
        },
    )
    monkeypatch.setattr("distill.runner.load_delivery", lambda _path: delivery)
    monkeypatch.setattr(
        "distill.runner.stdio_launch",
        lambda *args, **kwargs: StdioLaunch(
            Path("docker"), tuple(kwargs["arguments"]), {}
        ),
    )
    private_state = tmp_path / "private-state"
    private_state.mkdir()
    record_database = private_state / "records.sqlite"
    record_database.touch()
    path_map = private_state / "path-map.json"
    path_map.write_text(json.dumps({
        "workspace_root": str(state),
        "record_database": str(record_database),
        "scope_mounts": {},
    }))
    paths = SimpleNamespace(
        model_workspace=state,
        private_state=private_state,
        record_database=record_database,
        path_map=path_map,
        raw=trace.parent,
    )
    launch = _task_mcp_launch(
        {
            "binding_path": str(binding),
            "max_tool_calls": 4,
            "timeout": 30,
            "memory_limit": 1024,
            "write_limit": 1024,
            "process_limit": 4,
            **({"solution_validation": {"max_attempts": 3}} if author_validation else {}),
        },
        private_server,
        paths,
    )
    assert launch.command == Path("docker")
    assert "--binding" in launch.arguments
    assert str(state) in launch.arguments
    assert ("--solution-validation" in launch.arguments) is author_validation
    if author_validation:
        assert json.loads((private_state / "solution_validation.json").read_text()) == {"max_attempts": 3}


def test_tool_policy_accepts_kimi_code_shortened_long_name():
    from distill.runner import _tool_names_match

    long_name = "mcp__agent_world_distill__generate_solution_driven_background_mesh"
    shortened = "mcp__agent_world_distill__generate_solution_driven_back_280aead6"

    assert _tool_names_match(
        {"mcp__agent_world_distill__inspect", shortened},
        {"mcp__agent_world_distill__inspect", long_name},
    )

    hyphen_shortened = "mcp__agent_world_distill__generate_solution_driven_back-280aead6"
    assert _tool_names_match(
        {"mcp__agent_world_distill__inspect", hyphen_shortened},
        {"mcp__agent_world_distill__inspect", long_name},
    )

    assert _tool_names_match(
        {"mcp__agent_world_distill__solve_equilibrium_and_quench_-3ec75202"},
        {"mcp__agent_world_distill__solve_equilibrium_and_quenched_concentrations"},
    )

    assert _tool_names_match(
        {"mcp__agent_world_distill__update_simulated_event_report_332a6acd"},
        {"mcp__agent_world_distill__update_simulated_event_report_definition"},
    )

    assert _tool_names_match(
        {"mcp__agent_world_distill__update_simulated_remote_comma_-8194698"},
        {"mcp__agent_world_distill__update_simulated_remote_command_definition"},
    )


def test_tool_policy_accepts_multiple_shortened_names_with_same_prefix():
    from distill.runner import _tool_names_match

    expected = {
        "Read",
        "mcp__agent_world_distill__find_control_job_process_job_to_control_job",
        "mcp__agent_world_distill__find_control_job_process_job_to_process_job",
    }
    observed = {
        "Read",
        "mcp__agent_world_distill__find_control_job_process_job_-319b09d6",
        "mcp__agent_world_distill__find_control_job_process_job_-4453156a",
    }

    assert _tool_names_match(observed, expected)


@pytest.mark.parametrize("observed", [
    "mcp__agent_world_distill__generate_solution_driven_back_notahash",
    "mcp__agent_world_distill__generate_solution_driven_back_280aead7_extra",
    "mcp__agent_world_distill__unapproved_tool",
])
def test_tool_policy_rejects_invalid_or_unapproved_short_name(observed):
    from distill.runner import _tool_names_match

    expected = {
        "mcp__agent_world_distill__inspect",
        "mcp__agent_world_distill__generate_solution_driven_background_mesh",
    }
    assert not _tool_names_match({"mcp__agent_world_distill__inspect", observed}, expected)


@pytest.fixture
def streaming_model():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            body = json.dumps({
                "object": "list",
                "data": [{"id": "kimi-k3", "object": "model", "owned_by": "test"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            serialized = json.dumps(request)
            early_blocking = "EARLY_BLOCKING_COMPACTION_PROBE" in serialized
            if early_blocking and request.get("messages", [{}])[-1].get("content", "").startswith("You are about to run out of context."):
                time.sleep(0.25)
            if "CONTEXT_OVERFLOW_PROBE" in serialized and len(requests) == 2:
                body = json.dumps({"error": {"message": "Input exceeds the maximum context length",
                                             "type": "invalid_request_error",
                                             "code": "context_length_exceeded"}}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            forbidden_probe = re.search(r"FORBIDDEN_READ_PROBE:(/[^\s\"\\]+)", serialized)
            if forbidden_probe is not None and len(requests) == 1:
                delta = {
                    "role": "assistant",
                    "reasoning_content": "I will try the requested native read.",
                    "tool_calls": [{
                        "index": 0,
                        "id": "forbidden-read-1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": json.dumps({"path": forbidden_probe.group(1)}),
                        },
                    }],
                }
                finish_reason = "tool_calls"
            elif forbidden_probe is not None:
                delta = {
                    "role": "assistant",
                    "reasoning_content": "The native read was denied by the harness.",
                    "content": "Access was denied.",
                }
                finish_reason = "stop"
            elif len(requests) == 1:
                delta = {
                    "role": "assistant",
                    "reasoning_content": "I should inspect the environment.",
                    "tool_calls": [{
                        "index": 0,
                        "id": "inspect-1",
                        "type": "function",
                        "function": {
                            "name": next((tool["function"]["name"] for tool in request.get("tools", [])
                                          if tool["function"]["name"].endswith("__inspect")),
                                         "mcp__agent_world_distill__inspect"),
                            "arguments": "{}",
                        },
                    }],
                }
                finish_reason = "tool_calls"
            else:
                output_path = re.search(r"output_path: (/[^\s\"\\]+)", serialized)
                long_sample = "long sample" in serialized
                if long_sample and output_path is not None and "MIDDLE_EVIDENCE_731" not in serialized:
                    delta = {
                        "role": "assistant",
                        "reasoning_content": "I need to read the persisted middle section.",
                        "tool_calls": [{
                            "index": 0,
                            "id": "read-long-result-1",
                            "type": "function",
                            "function": {
                                "name": "Read",
                                "arguments": json.dumps({
                                    "path": output_path.group(1),
                                    "line_offset": 3,
                                    "column_offset": 299900,
                                    "max_chars": 1000,
                                }),
                            },
                        }],
                    }
                    finish_reason = "tool_calls"
                else:
                    delta = {
                        "role": "assistant",
                        "reasoning_content": "The observation is sufficient.",
                        "content": (
                            "MIDDLE_EVIDENCE_731"
                            if long_sample
                            else "The value is 17."
                        ),
                    }
                    finish_reason = "stop"
            input_tokens = 140000 if early_blocking and len(requests) == 1 else 100
            chunks = [
                {
                    "id": f"test-{len(requests)}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "kimi-k3",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": f"test-{len(requests)}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "kimi-k3",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": input_tokens, "completion_tokens": 10, "total_tokens": input_tokens + 10},
                },
            ]
            body = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("compaction_probe", [None, "CONTEXT_OVERFLOW_PROBE", "EARLY_BLOCKING_COMPACTION_PROBE"])
@pytest.mark.parametrize("server_name", ["agent_world_distill", "workspace"])
def test_official_cli_streaming_mcp_and_evidence(tmp_path, streaming_model, compaction_probe, server_name):
    from distill.runner import run_k3_distillation

    kimi_bin = os.environ.get("KIMI_CODE_BIN")
    if not kimi_bin:
        pytest.skip("Set KIMI_CODE_BIN to the official Kimi CLI")
    base_url, requests = streaming_model
    initial = tmp_path / "initial"
    initial.mkdir()
    tool = {
        "name": "inspect",
        "description": "Inspect a deterministic value.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"success": {"const": True}, "value": {"type": "integer"}},
            "required": ["success", "value"],
            "additionalProperties": False,
        },
        "internal": {"code": "def run(arguments, context):\n return {'success': True, 'value': 17}"},
    }
    output = tmp_path / "run"
    trajectory = run_k3_distillation(
        "Inspect the environment and report the value." + (" " + compaction_probe if compaction_probe else ""),
        initial,
        {
            "workspace_root": ".",
            "trace": "calls.jsonl",
            "max_tool_calls": 10,
            "timeout": 30,
            "memory_limit": 2 * 1024 * 1024 * 1024,
            "write_limit": 256 * 1024 * 1024,
            "tools": [tool],
            "environment": {"environment_id": "cli-fixture", "schema_version": "1.0"},
        },
        output,
        kimi_bin=Path(kimi_bin),
        base_url=base_url,
        api_key="test-key-never-written",
        max_context_size=262144 if compaction_probe else 1_048_576,
        compaction_trigger_ratio=0.5 if compaction_probe else None,
        mcp_server_name=server_name,
    )

    assert trajectory["outcome"]["final_answer"] == "The value is 17."
    assert "I should inspect" in trajectory["steps"][0]["reasoning"]["text"]
    call = trajectory["steps"][0]["tool_calls"][0]
    assert call["tool_kind"] == "environment_mcp"
    assert call["environment_record"]["result"]["value"] == 17
    if compaction_probe:
        assert len(requests) >= (4 if compaction_probe == "CONTEXT_OVERFLOW_PROBE" else 3)
        assert len(trajectory["compactions"]) >= 1
        assert trajectory["model"]["max_context_size"] == 262144
        policy = json.loads((output / "raw/harness_policy.json").read_text())
        assert policy["compaction_trigger_ratio"] == 0.5
        assert policy["compaction_reserved_context_size"] == 131072
        if compaction_probe == "EARLY_BLOCKING_COMPACTION_PROBE":
            assert requests[1]["messages"][-1]["content"].startswith("You are about to run out of context.")
    else:
        assert len(requests) == 2
    expected = {
        f"mcp__{server_name}__inspect",
        "Read", "Grep", "Glob",
    }
    for request in requests:
        assert request["stream"] is True
        assert request["reasoning_effort"] == "max"
        assert request["max_completion_tokens"] > 0
        assert "thinking" not in request
        assert "max_tokens" not in request
        assert {tool["function"]["name"] for tool in request["tools"]} == expected
    assert (output / "raw/kimi_session.zip").is_file()
    policy = json.loads((output / "raw/harness_policy.json").read_text())
    assert policy["mcp_server_name"] == server_name
    assert set(json.loads((output / "raw/mcp.json").read_text())["mcpServers"]) == {server_name}
    assert len(list((output / "raw/model_io").glob("*.response.sse"))) == len(requests)
    assert "test-key-never-written" not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (output / "raw").rglob("*") if path.is_file() and path.suffix != ".zip"
    )


def test_official_cli_denies_native_read_of_execution_state(tmp_path, streaming_model):
    from distill.runner import run_k3_distillation

    kimi_bin = os.environ.get("KIMI_CODE_BIN")
    if not kimi_bin:
        pytest.skip("Set KIMI_CODE_BIN to the official Kimi CLI")
    base_url, requests = streaming_model
    initial = tmp_path / "initial"
    initial.mkdir()
    secret_value = "EXECUTION_STATE_SECRET_928"
    (initial / "secret.txt").write_text(secret_value, encoding="utf-8")
    output = tmp_path / "run"
    forbidden_path = output.resolve() / "execution-state/secret.txt"

    trajectory = run_k3_distillation(
        f"FORBIDDEN_READ_PROBE:{forbidden_path}",
        initial,
        {
            "workspace_root": ".", "trace": "calls.jsonl", "max_tool_calls": 1,
            "timeout": 30, "memory_limit": 2 * 1024 * 1024 * 1024,
            "write_limit": 256 * 1024 * 1024, "tools": [],
            "environment": {"environment_id": "read-boundary", "schema_version": "1.0"},
        },
        output,
        kimi_bin=Path(kimi_bin),
        base_url=base_url,
        api_key="test-key-never-written",
        max_context_size=1_048_576,
    )

    assert trajectory["outcome"]["final_answer"] == "Access was denied."
    assert secret_value not in json.dumps(requests)
    audit = [
        json.loads(line)
        for line in (output / "raw/native_file_access.jsonl").read_text().splitlines()
    ]
    assert len(audit) == 1
    assert audit[0]["tool"] == "Read"
    assert audit[0]["requested_path"] == str(forbidden_path)
    assert audit[0]["allowed"] is False
    assert audit[0]["reason"] == "path_outside_session_allowlist"


def test_official_cli_reads_long_result_with_native_read(tmp_path, streaming_model):
    from distill.runner import run_k3_distillation

    kimi_bin = os.environ.get("KIMI_CODE_BIN")
    if not kimi_bin:
        pytest.skip("Set KIMI_CODE_BIN to the official Kimi CLI")
    base_url, requests = streaming_model
    initial = tmp_path / "initial"
    initial.mkdir()
    (initial / "hidden.txt").write_text("SHOULD_NOT_REACH_MODEL", encoding="utf-8")
    tool = {
        "name": "inspect",
        "description": "Inspect a long deterministic sample.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"success": {"const": True}, "data": {"type": "string"}},
            "required": ["success", "data"],
            "additionalProperties": False,
        },
        "internal": {
            "code": "def run(arguments, context):\n return {'success': True, 'data': 'x' * 300000 + 'MIDDLE_EVIDENCE_731' + 'y' * 300000}"
        },
    }
    output = tmp_path / "run"
    trajectory = run_k3_distillation(
        "Inspect the long sample and report the evidence in its middle.",
        initial,
        {
            "workspace_root": ".", "trace": "calls.jsonl", "max_tool_calls": 2,
            "timeout": 30, "memory_limit": 2 * 1024 * 1024 * 1024,
            "write_limit": 256 * 1024 * 1024, "tools": [tool],
            "environment": {"environment_id": "long-cli-fixture", "schema_version": "1.0"},
        },
        output,
        kimi_bin=Path(kimi_bin),
        base_url=base_url,
        api_key="test-key-never-written",
        max_context_size=1_048_576,
    )

    assert trajectory["outcome"]["final_answer"] == "MIDDLE_EVIDENCE_731"
    calls = [call for step in trajectory["steps"] for call in step["tool_calls"]]
    assert [call["tool_kind"] for call in calls] == ["environment_mcp", "native_file_tool"]
    assert len((output / "raw/environment_tool_calls.jsonl").read_text().splitlines()) == 1
    audit = [
        json.loads(line)
        for line in (output / "raw/native_file_access.jsonl").read_text().splitlines()
    ]
    assert len(audit) == 1
    assert audit[0]["tool"] == "Read"
    assert audit[0]["allowed"] is True
    assert audit[0]["reason"] == "session_tool_results"
    serialized_requests = json.dumps(requests)
    assert "SHOULD_NOT_REACH_MODEL" not in serialized_requests
    assert '"Read"' in serialized_requests
