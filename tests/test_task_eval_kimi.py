"""Run the real Kimi SDK and MCP gateway against a deterministic HTTP model."""
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import time

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
            if callable(delta):
                delta = delta(body)
            if 'http_error' in delta:
                self.send_response(delta['http_error'])
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"temporary unavailable","type":"server_error"}}')
                return
            time.sleep(delta.pop('_delay_seconds', 0))
            disconnect = delta.pop('_disconnect', False)
            stream_error = delta.pop('_stream_error', None)
            chunk = {
                'id': f'chat-{len(requests)}', 'object': 'chat.completion.chunk',
                'created': 0, 'model': 'test-model',
                'choices': [{'index': 0, 'delta': {'role': 'assistant', **delta},
                             'finish_reason': None}],
            }
            finish = {**chunk, 'choices': [{'index': 0, 'delta': {},
                       'finish_reason': 'tool_calls' if 'tool_calls' in delta else 'stop'}],
                      'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
            data = ('data: ' + json.dumps(chunk) + '\n\nevent: error\ndata: ' +
                    json.dumps({'error': {'message': stream_error, 'type': 'upstream_error'}}) + '\n\n'
                    if stream_error else
                    ''.join('data: ' + json.dumps(c) + '\n\n' for c in [chunk, finish]) + 'data: [DONE]\n\n')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            if disconnect:
                self.send_header('Content-Length', str(len(data.encode()) + 100))
            self.end_headers()
            try:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode() if disconnect else data.encode())
                self.wfile.flush()
                if disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Cancellation test intentionally closes the response.

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
        'outputSchema': {'oneOf': [{'type': 'object', 'properties': {'success': {'const': True}}},
                                 {'type': 'object', 'properties': {'success': {'const': False}}}]},
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
    options['temperature'] = 0.2
    options['kimi'].update(system_prompt='CUSTOM_EVALUATION_INSTRUCTIONS',
                           reserved_context_size=8192, compaction_trigger_ratio=0.8, compaction_max_attempts=2,
                           max_output_size=1024)
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
        assert [t['function']['name'] for t in request['tools']] == [
            'mcp__agent_world_eval__inspect', 'mcp__agent_world_eval__read_tool_result']
        assert 'parallel_tool_calls' not in request
        assert request['temperature'] == 0.2
        assert request.get('max_tokens', request.get('max_completion_tokens')) == 1024
        assert request['messages'][0]['content'].startswith('CUSTOM_EVALUATION_INSTRUCTIONS')
    visible = json.dumps(requests)
    assert 'HIDDEN_INITIAL_STATE' not in visible
    assert 'def run(arguments' not in visible
    assert 'SPECIAL_PUBLIC_CONDITION' in visible
    assert 'observed-731' in json.dumps(requests[-1]['messages'])
    logs = tmp_path / 'state.agent'
    result = json.loads((logs / 'result.json').read_text())
    assert result['usage']['total']['output'] == 30
    effective = json.loads((logs / 'effective_config.json').read_text())
    assert effective['loopControl']['compactionMaxAttempts'] == 2
    assert effective['models']['evaluation']['maxContextSize'] == 131072
    responses = [json.loads(line) for line in (logs / 'llm_responses.jsonl').read_text().splitlines()]
    assert sorted(r['request_id'] for r in responses) == [1, 2, 3]
    assert all(r['status'] == 200 and 'data:' in r['body'] for r in responses)
    assert 'test-secret-not-for-logs' not in ''.join(p.read_text() for p in logs.rglob('*.json*'))


def test_default_prompt_uses_official_template_with_restricted_tools(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    replies.extend([
        tool_call('Read', {'path': str(workspace / 'hidden.txt')}, 'forbidden'),
        tool_call('mcp__agent_world_eval__inspect', {}, 'allowed'),
        {'content': 'observed-731'},
    ])
    assert _run_agent('Inspect.', workspace, server, trace, options) == 'observed-731'
    prompt = requests[0]['messages'][0]['content']
    assert 'Your primary goal is to help users with software engineering tasks.' in prompt
    assert '# Context management' in prompt
    assert '${base_prompt}' not in prompt
    assert 'Long results are returned as pages' not in prompt
    assert 'At most 1 environment tool calls' not in prompt
    assert 'HIDDEN_INITIAL_STATE' not in json.dumps(requests)
    assert [json.loads(line)['tool'] for line in trace.read_text().splitlines()] == ['inspect']
    assert {t['function']['name'] for t in requests[0]['tools']} == {
        'mcp__agent_world_eval__inspect', 'mcp__agent_world_eval__read_tool_result'}


def test_default_loop_uses_sdk_policy_without_inherited_timeout(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    for key in ('max_steps_per_turn', 'max_attempts_per_step', 'timeout_seconds'):
        options['kimi'].pop(key)
    options['timeout_seconds'] = 0.001
    options['kimi']['max_context_size'] = 1000000
    replies.append({'content': 'Done.'})
    assert _run_agent('Inspect.', workspace, server, trace, options) == 'Done.'
    effective = json.loads((tmp_path / 'state.agent/effective_config.json').read_text())
    assert 'maxStepsPerTurn' not in effective['loopControl']
    assert 'maxAttemptsPerStep' not in effective['loopControl']
    assert effective['loopControl'] == {}
    assert effective['models']['evaluation']['maxContextSize'] == 1000000
    assert 'maxOutputSize' not in effective['models']['evaluation']
    # Official OpenAI-compatible provider caps its default output at 128K.
    assert requests[0].get('max_tokens', requests[0].get('max_completion_tokens')) == 131072


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


@pytest.mark.parametrize('setting,value', [('max_steps_per_turn', 0), ('parallel_tool_calls', 'false'),
                                         ('compaction_trigger_ratio', 2), ('system_prompt', ''), ('typo', 1),
                                         ('tool_result_page_chars', 50000)])
def test_invalid_kimi_configuration_is_rejected(tmp_path, monkeypatch, setting, value):
    from task_gen.task_eval import _run_agent
    workspace, server, trace, options = setup_case(tmp_path, 'http://unused.invalid/v1')
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi'][setting] = value
    with pytest.raises(ValueError, match=setting):
        _run_agent('Task', workspace, server, trace, options)


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
    assert 'parallel_tool_calls' not in requests[0]


def test_api_retry_does_not_reexecute_tool(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi']['max_attempts_per_step'] = 2
    replies.extend([tool_call('mcp__agent_world_eval__inspect', {}, 'read'),
                    {'http_error': 503}, {'content': 'The observed value is observed-731.'}])
    assert 'observed-731' in _run_agent('Inspect.', workspace, server, trace, options)
    assert len(trace.read_text().splitlines()) == 1
    assert len(requests) == 3
    logs = [json.loads(line) for line in (tmp_path / 'state.agent/llm_responses.jsonl').read_text().splitlines()]
    assert [row['status'] for row in sorted(logs, key=lambda r: r['request_id'])] == [200, 503, 200]


def test_stream_retry_joins_final_answer_without_tool_commentary(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi']['max_attempts_per_step'] = 2
    replies.extend([
        {'content': 'I will inspect first. ',
         **tool_call('mcp__agent_world_eval__inspect', {}, 'read')},
        {'content': 'The complete answer starts here', '_stream_error': 'Upstream request failed'},
        {'content': ' and ends here.'},
    ])
    assert _run_agent('Inspect.', workspace, server, trace, options) == (
        'The complete answer starts here and ends here.')
    assert len(requests) == 3
    assert requests[-1]['messages'][-1]['content'] == 'The complete answer starts here'


def test_timeout_preserves_completed_tool_and_partial_context(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi']['timeout_seconds'] = 7
    replies.extend([tool_call('mcp__agent_world_eval__inspect', {}, 'read'),
                    {'content': 'Late answer', '_delay_seconds': 20}])
    with pytest.raises(RuntimeError, match='超时'):
        _run_agent('Inspect.', workspace, server, trace, options)
    logs = tmp_path / 'state.agent'
    assert len(trace.read_text().splitlines()) == 1
    assert 'observed-731' in (logs / 'context.json').read_text()
    assert json.loads((logs / 'result.json').read_text())['reason'] == 'cancelled'
    assert json.loads((logs / 'timing.json').read_text())['timed_out'] is True


def test_empty_final_answer_is_not_success(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    replies.append({'content': ''})
    with pytest.raises(RuntimeError):
        _run_agent('Answer.', workspace, server, trace, options)


@pytest.mark.parametrize('overrides,expected', [([], (12, 2)), (['--max-tool-calls', '8', '--max-concurrency', '3'], (8, 3))])
def test_eval_cli_config_precedence(tmp_path, monkeypatch, overrides, expected):
    import task_gen.task_eval as evaluation
    config = tmp_path / 'config.yaml'
    config.write_text('llm: {agent_backend: kimi}\nexecution: {evaluation_max_tool_calls: 12, evaluation_max_concurrency: 2}\n')
    received = {}
    def run(*args, **kwargs):
        received.update(kwargs)
        return tmp_path
    monkeypatch.setattr(evaluation, 'run_evaluation', run)
    monkeypatch.setattr('sys.argv', ['task-eval', '--config', str(config), *overrides])
    evaluation.main()
    assert (received['max_tool_calls'], received['max_concurrency']) == expected


def test_small_context_triggers_compaction_without_tool_allowlist_error(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    options['kimi'].update(max_context_size=4096, reserved_context_size=1024, compaction_trigger_ratio=0.5,
                           tool_result_page_chars=12000)
    config = json.loads(server.read_text())
    config['tools'][0]['internal']['code'] = "def run(arguments, context):\n return {'success': True, 'value': 'observed-731', 'details': 'evidence ' * 5000}"
    server.write_text(json.dumps(config))
    replies.extend([tool_call('mcp__agent_world_eval__inspect', {}, 'read'),
                    {'content': 'Summary: the inspect tool returned observed-731. Report that value.'},
                    {'content': 'The observed value is observed-731.'}])
    assert 'observed-731' in _run_agent('Inspect and report.', workspace, server, trace, options)
    events = [json.loads(line) for line in (tmp_path / 'state.agent/events.jsonl').read_text().splitlines()]
    assert any(event['type'] == 'compaction.completed' for event in events)
    assert any('handoff summary' in str(request['messages']) for request in requests)


def test_concurrent_sessions_keep_tools_and_logs_separate(tmp_path, model_server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    cases = []
    for name in ('first', 'second'):
        root = tmp_path / name
        root.mkdir()
        cases.append(setup_case(root, url))
    replies.extend([{'content': 'Done.'}, {'content': 'Done.'}])
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_run_agent, 'Answer briefly.', *case) for case in cases]
        assert [future.result() for future in futures] == ['Done.', 'Done.']
    results = [json.loads((case[0].parent / 'state.agent/result.json').read_text()) for case in cases]
    assert results[0]['session_id'] != results[1]['session_id']
    for case in cases:
        logs = case[0].parent / 'state.agent'
        assert len((logs / 'llm_requests.jsonl').read_text().splitlines()) == 1


def test_broken_response_preserves_received_bytes(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    replies.append({'content': 'PARTIAL_RESPONSE_EVIDENCE', '_disconnect': True})
    with pytest.raises(RuntimeError):
        _run_agent('Answer.', workspace, server, trace, options)
    records = [json.loads(line) for line in (tmp_path / 'state.agent/llm_responses.jsonl').read_text().splitlines()]
    assert any('PARTIAL_RESPONSE_EVIDENCE' in record['body'] and record.get('error') for record in records)


def test_long_result_read_is_scoped_and_does_not_spend_business_budget(tmp_path, model_server, monkeypatch):
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    config = json.loads(server.read_text())
    config['tools'][0]['internal']['code'] = "def run(arguments, context):\n return {'success': True, 'data': 'x' * 300000 + 'MIDDLE_EVIDENCE_731' + 'y' * 300000}"
    server.write_text(json.dumps(config))
    options['kimi']['max_steps_per_turn'] = 8
    def read_middle(request):
        message = next(m for m in request['messages'] if m.get('tool_call_id') == 'inspect')
        page = json.loads(message['content'])
        assert page['has_more'] is True and page['total_chars'] > 600000
        assert len(page['content']) <= 12000
        assert 'MIDDLE_EVIDENCE_731' not in message['content']
        return tool_call('mcp__agent_world_eval__read_tool_result',
                         {'result_id': page['result_id'], 'offset': 300000, 'length': 100}, 'middle')
    replies.extend([
        tool_call('mcp__agent_world_eval__inspect', {}, 'inspect'),
        read_middle,
        tool_call('mcp__agent_world_eval__read_tool_result',
                  {'result_id': str(workspace / 'hidden.txt'), 'offset': 0, 'length': 100}, 'forbidden'),
        {'content': 'MIDDLE_EVIDENCE_731'},
    ])
    assert _run_agent('Report the evidence in the middle of the sample.', workspace, server, trace, options) == 'MIDDLE_EVIDENCE_731'
    assert len(trace.read_text().splitlines()) == 1
    assert len(json.loads(trace.read_text())['result']['data']) > 600000
    messages = requests[-1]['messages']
    assert 'MIDDLE_EVIDENCE_731' in next(m['content'] for m in messages if m.get('tool_call_id') == 'middle')
    assert 'Unknown result_id' in next(m['content'] for m in messages if m.get('tool_call_id') == 'forbidden')
    assert 'HIDDEN_INITIAL_STATE' not in json.dumps(requests)
    reads = [json.loads(line) for line in (tmp_path / 'state.agent/result_reads.jsonl').read_text().splitlines()]
    assert len(reads) == 2 and reads[0]['error'] is None and reads[1]['error']


def test_kimi_binding_uses_task_state_and_public_delivery_tools(tmp_path, model_server, monkeypatch):
    import shutil
    import sqlite3
    from tests.test_kimi_mcp import KimiMcpTests
    from env_gen.tool_gen.kimi_mcp import load_delivery
    from task_gen.task_eval import _run_agent
    url, requests, replies = model_server
    workspace, server, trace, options = setup_case(tmp_path, url)
    monkeypatch.setenv('KIMI_TEST_KEY', 'test-key')
    binding = KimiMcpTests()._make_delivery(tmp_path)
    delivery = load_delivery(binding)
    shutil.copytree(delivery.package.package_root / 'state', workspace, dirs_exist_ok=True)
    with sqlite3.connect(workspace / 'records.sqlite') as db:
        db.execute("UPDATE tickets SET ticket_id='task-only'")
    config = json.loads(server.read_text())
    config.update(tools=delivery.package.tools, environment=delivery.package.environment)
    server.write_text(json.dumps(config))
    options['kimi']['binding_path'] = str(binding)
    replies.extend([tool_call('mcp__agent_world_eval__resolve_ticket', {'ticket_id': 'task-only'}, 'resolve'),
                    {'content': 'Resolved task-only.'}])
    assert _run_agent('Resolve task-only.', workspace, server, trace, options) == 'Resolved task-only.'
    assert json.loads(trace.read_text())['result']['data']['status'] == 'resolved'
    assert 'Target objects:' in json.dumps(requests[0]['tools'])
    with sqlite3.connect(delivery.package.package_root / 'state/records.sqlite') as db:
        assert db.execute('SELECT status FROM tickets').fetchone()[0] == 'open'
