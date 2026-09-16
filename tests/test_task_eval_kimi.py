"""Run the real Kimi SDK and MCP gateway against a deterministic HTTP model."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


@pytest.fixture
def model_server():
    requests, replies = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            delta = replies.pop(0) if replies else {'content': 'unexpected extra request'}
            chunk = {
                'id': f'chat-{len(requests)}', 'object': 'chat.completion.chunk',
                'created': 0, 'model': 'test-model',
                'choices': [{'index': 0, 'delta': {'role': 'assistant', **delta},
                             'finish_reason': None}],
            }
            finish = {**chunk, 'choices': [{'index': 0, 'delta': {},
                       'finish_reason': 'tool_calls' if 'tool_calls' in delta else 'stop'}],
                      'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
            data = ''.join('data: ' + json.dumps(c) + '\n\n' for c in [chunk, finish]) + 'data: [DONE]\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(data.encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', requests, replies
    server.shutdown()
    server.server_close()
    thread.join()


def tool_call(name, arguments, ident):
    return {'tool_calls': [{'index': 0, 'id': ident, 'type': 'function',
                           'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


def setup_case(tmp_path, url):
    sdk = os.environ.get('KIMI_CODE_SDK')
    if not sdk:
        pytest.skip('Set KIMI_CODE_SDK to the built official SDK index.mjs')
    workspace = tmp_path / 'state'
    workspace.mkdir()
    (workspace / 'hidden.txt').write_text('HIDDEN_INITIAL_STATE')
    tool = {
        'name': 'inspect', 'description': 'Inspect the public sample.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'outputSchema': {'type': 'object'},
        'usageConditions': {'preconditions': ['SPECIAL_PUBLIC_CONDITION'], 'sideEffects': []},
        'internal': {'code': "def run(arguments, context):\n    return {'success': True, 'value': 'observed-731'}\n"},
    }
    trace = tmp_path / 'calls.jsonl'
    server = tmp_path / 'server.json'
    server.write_text(json.dumps({
        'tools': [tool], 'environment': {}, 'workspace': str(workspace), 'trace': str(trace),
        'max_tool_calls': 1, 'timeout': 10, 'memory_limit': 2147483648, 'write_limit': 268435456,
    }))
    options = {'agent_backend': 'kimi', 'model': 'test-model', 'base_url': url,
               'api_key_env': 'KIMI_TEST_KEY', 'max_tokens': 1024,
               'kimi': {'sdk_path': sdk, 'max_context_size': 131072, 'timeout_seconds': 90,
                        'max_steps_per_turn': 6, 'max_attempts_per_step': 1}}
    return workspace, server, trace, options


def test_kimi_rejects_builtin_preserves_public_contract_and_observation(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-secret-not-for-logs')
    replies.extend([
        tool_call('Read', {'path': str(workspace / 'hidden.txt')}, 'forbidden'),
        tool_call('mcp__agent_world_eval__inspect', {}, 'allowed'),
        {'content': 'The observed value is observed-731.'},
    ])
    assert _run_agent('Inspect the sample and report its value.', workspace, server, trace, options) == 'The observed value is observed-731.'
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [r['tool'] for r in records] == ['inspect']
    assert records[0]['result']['value'] == 'observed-731'
    assert len(requests) == 3
    for request in requests:
        assert [t['function']['name'] for t in request['tools']] == ['mcp__agent_world_eval__inspect']
    visible = json.dumps(requests)
    assert 'HIDDEN_INITIAL_STATE' not in visible
    assert 'def run(arguments' not in visible
    assert 'SPECIAL_PUBLIC_CONDITION' in visible
    assert 'observed-731' in json.dumps(requests[-1]['messages'])
    logs = tmp_path / 'state.agent'
    result = json.loads((logs / 'result.json').read_text())
    assert result['usage']['total']['output'] == 30
    assert 'test-secret-not-for-logs' not in ''.join(p.read_text() for p in logs.rglob('*.json*'))


def test_kimi_step_limit_does_not_accept_intermediate_text(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi']['max_steps_per_turn'] = 1
    replies.append({'content': 'I will inspect it.', **tool_call('mcp__agent_world_eval__inspect', {}, 'one')})
    with pytest.raises(RuntimeError):
        _run_agent('Inspect the sample.', workspace, server, trace, options)
    assert len(requests) == 1
    assert len(trace.read_text().splitlines()) == 1
    assert (tmp_path / 'state.agent/tool_calls.jsonl').is_file()


def test_unknown_agent_backend_is_rejected(tmp_path):
    from task_gen.task_eval import _run_agent
    with pytest.raises(ValueError, match='agent_backend'):
        _run_agent('Task', tmp_path, tmp_path / 'config', tmp_path / 'trace', {'agent_backend': 'typo'})


def test_multiple_calls_return_both_results_before_next_model_step(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    config = json.loads(server.read_text())
    config['max_tool_calls'] = 2
    config['tools'][0]['inputSchema'] = {
        'type': 'object', 'properties': {'tag': {'type': 'integer'}}, 'required': ['tag'],
        'additionalProperties': False,
    }
    config['tools'][0]['internal']['code'] = "def run(arguments, context):\n return {'success': True, 'tag': arguments['tag']}"
    server.write_text(json.dumps(config))
    first = tool_call('mcp__agent_world_eval__inspect', {'tag': 1}, 'first')['tool_calls'][0]
    second = tool_call('mcp__agent_world_eval__inspect', {'tag': 2}, 'second')['tool_calls'][0]
    second['index'] = 1
    replies.extend([{'tool_calls': [first, second]}, {'content': 'Observed tags 1 and 2.'}])
    assert '1 and 2' in _run_agent('Inspect twice.', workspace, server, trace, options)
    assert len(requests) == 2
    messages = [m for m in requests[1]['messages'] if m['role'] == 'tool']
    assert [m['tool_call_id'] for m in messages] == ['first', 'second']
    assert '"tag": 1' in messages[0]['content']
    assert '"tag": 2' in messages[1]['content']
    assert len(trace.read_text().splitlines()) == 2
