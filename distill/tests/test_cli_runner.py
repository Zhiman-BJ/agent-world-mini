import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import tomllib
from urllib.request import Request, urlopen

import pytest


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
    from distill.runner import KIMI_K3_CONTEXT_SIZE, _write_kimi_config

    path = tmp_path / "config.toml"
    _write_kimi_config(
        path,
        "http://127.0.0.1:1234/v1",
        "evaluation",
        "kimi-k3",
        KIMI_K3_CONTEXT_SIZE,
        "kimi",
    )

    config = tomllib.loads(path.read_text(encoding="utf-8"))
    provider = config["providers"]["agent_world_distill"]
    model = config["models"]["evaluation"]
    assert provider["type"] == "kimi"
    assert model["max_context_size"] == 1_048_576
    assert model["capabilities"] == ["thinking", "always_thinking", "tool_use"]
    assert model["support_efforts"] == ["low", "high", "max"]
    assert model["default_effort"] == "high"


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
        "max",
    )

    assert tomllib.loads(path.read_text(encoding="utf-8"))["models"]["evaluation"][
        "default_effort"
    ] == "max"


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
    assert changes == [
        "drop_thinking",
        "thinking_effort_to_reasoning_effort",
        "max_tokens_to_max_completion_tokens",
    ]


def test_kimi_k3_request_normalization_rejects_unsupported_effort():
    from distill.relay import _normalize_kimi_k3_chat_request

    with pytest.raises(ValueError, match="unsupported kimi-k3 reasoning effort"):
        _normalize_kimi_k3_chat_request({
            "model": "kimi-k3",
            "thinking": {"type": "enabled", "effort": "medium"},
        })


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
    assert "thinking" not in upstream_request
    assert "max_tokens" not in upstream_request
    assert json.loads((raw / "model_io/000001.request.json").read_text()) == upstream_request
    assert json.loads((raw / "model_io/000001.harness-request.json").read_text()) == harness_request
    record = json.loads((raw / "model_requests.jsonl").read_text())
    assert record["request"] == upstream_request
    assert record["harness_request"] == harness_request


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
            if len(requests) == 1:
                delta = {
                    "role": "assistant",
                    "reasoning_content": "I should inspect the environment.",
                    "tool_calls": [{
                        "index": 0,
                        "id": "inspect-1",
                        "type": "function",
                        "function": {
                            "name": "mcp__agent_world_distill__inspect",
                            "arguments": "{}",
                        },
                    }],
                }
                finish_reason = "tool_calls"
            else:
                page = None
                for message in request.get("messages", []):
                    if message.get("role") != "tool" or not isinstance(message.get("content"), str):
                        continue
                    try:
                        candidate = json.loads(message["content"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and candidate.get("result_id"):
                        page = candidate
                if page and "MIDDLE_EVIDENCE_731" not in json.dumps(request):
                    delta = {
                        "role": "assistant",
                        "reasoning_content": "I need the middle page.",
                        "tool_calls": [{
                            "index": 0,
                            "id": "read-1",
                            "type": "function",
                            "function": {
                                "name": "mcp__agent_world_distill__read_tool_result",
                                "arguments": json.dumps({
                                    "result_id": page["result_id"],
                                    "offset": 300000,
                                    "length": 100,
                                }),
                            },
                        }],
                    }
                    finish_reason = "tool_calls"
                else:
                    answer = "MIDDLE_EVIDENCE_731" if "MIDDLE_EVIDENCE_731" in json.dumps(request) else "The value is 17."
                    delta = {
                        "role": "assistant",
                        "reasoning_content": "The observation is sufficient.",
                        "content": answer,
                    }
                    finish_reason = "stop"
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
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
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


def test_official_cli_streaming_mcp_and_evidence(tmp_path, streaming_model):
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
        "Inspect the environment and report the value.",
        initial,
        {
            "workspace": ".",
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
        max_context_size=1_048_576,
    )

    assert trajectory["outcome"]["final_answer"] == "The value is 17."
    assert "I should inspect" in trajectory["steps"][0]["reasoning"]["text"]
    call = trajectory["steps"][0]["tool_calls"][0]
    assert call["tool_kind"] == "environment_mcp"
    assert call["environment_record"]["result"]["value"] == 17
    assert len(requests) == 2
    expected = {
        "mcp__agent_world_distill__inspect",
        "mcp__agent_world_distill__read_tool_result",
    }
    for request in requests:
        assert request["stream"] is True
        assert request["reasoning_effort"] == "high"
        assert request["max_completion_tokens"] > 0
        assert "thinking" not in request
        assert "max_tokens" not in request
        assert {tool["function"]["name"] for tool in request["tools"]} == expected
    assert (output / "raw/kimi_session.zip").is_file()
    assert len(list((output / "raw/model_io").glob("*.response.sse"))) == 2
    assert "test-key-never-written" not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (output / "raw").rglob("*") if path.is_file() and path.suffix != ".zip"
    )


def test_official_cli_reads_long_result_by_session_id_not_path(tmp_path, streaming_model):
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
            "workspace": ".", "trace": "calls.jsonl", "max_tool_calls": 1,
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
    assert [call["tool_kind"] for call in calls] == ["environment_mcp", "harness_support"]
    assert calls[1]["arguments"].keys() == {"result_id", "offset", "length"}
    assert calls[1]["support_record"]["error"] is None
    assert len((output / "raw/environment_tool_calls.jsonl").read_text().splitlines()) == 1
    assert len((output / "raw/result_reads.jsonl").read_text().splitlines()) == 1
    serialized_requests = json.dumps(requests)
    assert "SHOULD_NOT_REACH_MODEL" not in serialized_requests
    assert '"Read"' not in serialized_requests
