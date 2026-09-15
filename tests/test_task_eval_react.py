import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from task_gen.task_eval_react import run_react_agent


def test_resume_replays_failed_request_and_preserves_spent_budget(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    config = tmp_path / 'server.json'
    config.write_text(json.dumps({'tools': [], 'max_tool_calls': 1}))
    trace = tmp_path / 'trace.jsonl'
    trace.write_text('{"tool":"previous"}\n')
    requests = []

    class Client:
        model = 'test'
        client = True

        def complete_messages(self, messages, **kwargs):
            requests.append(messages)
            return ('{"action":{"name":"extra","params":{}}}' if len(requests) == 1
                    else '{"finish":true,"final_answer":"Done"}'), {}

    resume = {'system_prompt': 'saved system', 'history': [
        {'role': 'user', 'content': 'original task'}], 'prompt': 'last observation'}
    with patch.object(llm, '_client', lambda _: Client()):
        assert run_react_agent('', state, config, trace, {}, resume_request=resume) == 'Done'
    assert requests[0] == [{'role': 'system', 'content': 'saved system'},
                           *resume['history'], {'role': 'user', 'content': 'last observation'}]
    assert 'budget exhausted' in requests[1][-1]['content']
    assert trace.read_text() == '{"tool":"previous"}\n'

from task_gen.task_eval import _run_agent
from task_gen.tool_graph import llm


def test_eval_agent_uses_only_public_tools_and_keeps_observations_and_usage(tmp_path):
    workspace = tmp_path / "state"
    workspace.mkdir()
    (workspace / "hidden.txt").write_text("PRIVATE_STATE_CONTENT")
    config = tmp_path / "server.json"
    config.write_text(json.dumps({
        "tools": [{
            "name": "inspect", "description": "Return a document.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {"type": "object"},
            "usageConditions": {"sideEffects": []},
            "internal": {"code": "def run(arguments, context):\n    return {'success': True, 'document': 'x' * 1500}\n"},
        }],
        "environment": {}, "max_tool_calls": 3, "timeout": 10,
        "memory_limit": 2147483648, "write_limit": 268435456,
    }))
    requests = []
    replies = iter([
        '{"action":{"name":"exec_command","params":{"cmd":"cat hidden.txt"}}}',
        '{"action":{"name":"inspect","params":{}}}',
        '{"finish":true,"final_answer":"Document retrieved."}',
    ])

    class Client:
        model = "test"
        client = True

        def complete_messages(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            return next(replies), {"input_tokens": 10, "output_tokens": 5}

    api_config = []

    def client_factory(options):
        api_config.append(dict(options))
        return Client()

    trace = tmp_path / "calls.jsonl"
    with patch.object(llm, "_client", client_factory), patch.object(
        llm.CodexAgentClient, "run", side_effect=AssertionError("Must not launch Codex")
    ):
        answer = _run_agent("Read the document.", workspace, config, trace, {
            "backend": "codex", "timeout_seconds": 321,
        })

    assert answer == "Document retrieved."
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert records[0]["error"] == "未知工具"
    assert records[1]["error"] is None
    assert records[1]["result"]["document"] == "x" * 1500
    visible = json.dumps(requests, ensure_ascii=False)
    assert "PRIVATE_STATE_CONTENT" not in visible
    assert "def run(arguments" not in visible
    assert "x" * 1500 in visible
    assert all(item["backend"] == "api" and item["timeout_seconds"] == 321 for item in api_config)
    logs = list(tmp_path.glob("state.agent/llm_calls.jsonl"))
    assert len(logs) == 1
    calls = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert len(calls) == 3
    assert calls[-1]["answer"] == '{"finish":true,"final_answer":"Document retrieved."}'
    assert sum(call["usage"]["input_tokens"] for call in calls) == 30


def test_eval_agent_bounds_invalid_output_and_preserves_failed_run_logs(tmp_path):
    workspace = tmp_path / "state"
    workspace.mkdir()
    config = tmp_path / "server.json"
    config.write_text(json.dumps({"tools": [], "max_tool_calls": 1}))

    class Client:
        model = "test"
        client = True

        def complete_messages(self, messages, **kwargs):
            return '{"action":{"name":"shell","params":{}}}', {}

    with patch.object(llm, "_client", lambda _: Client()), patch.object(
        llm.CodexAgentClient, "run", side_effect=AssertionError("Must not launch Codex")
    ):
        with pytest.raises(RuntimeError, match="未提交最终答案"):
            _run_agent("Task", workspace, config, tmp_path / "calls.jsonl", {})
    records = (tmp_path / "calls.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert (tmp_path / "state.agent/tool_calls.jsonl").read_text().splitlines() == records
