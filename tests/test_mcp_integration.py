"""Public protocol must stay independent of Kimi's long-result presentation."""
import io
import json
import sqlite3
import pytest

from task_gen.task_eval_mcp import serve
from task_gen.tool_graph.step_5_task_validate import _public_tool


def test_task_and_mcp_share_public_contract_and_business_failure(tmp_path):
    tool = {'name': 'inspect', 'description': 'Inspect.',
            'usageConditions': {'preconditions': ['Must exist'], 'sideEffects': []},
            'inputSchema': {'type': 'object'},
            'outputSchema': {'oneOf': [{'type': 'object'}]},
            'internal': {'code': "def run(arguments, context):\n return {'success': False, 'error': {'code': 'not_found'}}"}}
    state = tmp_path / 'state'
    state.mkdir()
    config = tmp_path / 'server.json'
    config.write_text(json.dumps({'workspace': str(state), 'trace': str(tmp_path / 'calls'),
        'tools': [tool], 'max_tool_calls': 1, 'timeout': 10,
        'memory_limit': 2147483648, 'write_limit': 268435456}))
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                 'params': {'name': 'inspect', 'arguments': {}}}]
    output = io.StringIO()
    serve(config, io.StringIO('\n'.join(map(json.dumps, requests))), output)
    listed, called = [json.loads(line)['result'] for line in output.getvalue().splitlines()]
    assert listed['tools'] == [_public_tool(tool)]
    assert set(listed['tools'][0]) == {'name', 'description', 'inputSchema', 'outputSchema'}
    assert 'Must exist' in listed['tools'][0]['description']
    assert listed['tools'][0]['outputSchema']['type'] == 'object'
    assert called['isError'] is True
    assert called['structuredContent'] == {'success': False, 'error': {'code': 'not_found'}}


def test_binding_executes_task_initial_state_not_delivery_state(tmp_path):
    from tests.test_kimi_mcp import KimiMcpTests
    from env_gen.tool_gen.kimi_mcp import load_delivery, KimiMcpServer
    from task_gen.task_eval_mcp import TaskEvalMcpServer
    import shutil
    binding = KimiMcpTests()._make_delivery(tmp_path)
    delivery = load_delivery(binding)
    state = tmp_path / 'task_state'
    shutil.copytree(delivery.package.package_root / 'state', state)
    with sqlite3.connect(state / 'records.sqlite') as db:
        db.execute("UPDATE tickets SET ticket_id='task-only'")
    config = {'binding_path': str(binding), 'workspace': str(state), 'trace': str(tmp_path / 'calls'),
              'max_tool_calls': 3, 'timeout': 10, 'memory_limit': 2147483648, 'write_limit': 268435456}
    server = TaskEvalMcpServer(config)
    with KimiMcpServer(delivery) as original:
        assert server.handle({'method': 'tools/list'}) == original.handle({'method': 'tools/list'})
    response = server.handle({'method': 'tools/call', 'params': {
        'name': 'resolve_ticket', 'arguments': {'ticket_id': 'task-only'}}})
    assert response['structuredContent']['data']['status'] == 'resolved'
    with sqlite3.connect(state / 'records.sqlite') as db:
        assert db.execute('SELECT status FROM tickets').fetchone()[0] == 'resolved'
    with sqlite3.connect(delivery.package.package_root / 'state/records.sqlite') as db:
        assert db.execute('SELECT ticket_id, status FROM tickets').fetchone() == ('ticket-1', 'open')
    with pytest.raises(ValueError, match='工具'):
        TaskEvalMcpServer({**config, 'tools': []})


def test_bound_software_is_readable_but_not_writable_in_task_sandbox(tmp_path):
    import sys
    from task_gen.task_eval_mcp import call_environment_tool
    root = tmp_path / 'software'
    root.mkdir()
    (root / 'asset.txt').write_text('PROFILE_ASSET')
    state = tmp_path / 'state'
    state.mkdir()
    tool = {'name': 'inspect', 'inputSchema': {'type': 'object'}, 'outputSchema': {'type': 'object'},
            'internal': {'code': "def run(arguments, context):\n import os\n p = context.software_root / 'asset.txt'\n value = p.read_text()\n try:\n  p.write_text('changed')\n  return {'success': False}\n except OSError:\n  return {'success': True, 'value': value, 'blas_threads': os.environ['OPENBLAS_NUM_THREADS']}"}}
    result = call_environment_tool('inspect', {}, {'inspect': tool}, state, timeout=10,
        memory_limit=2147483648, write_limit=268435456,
        software={'root': str(root), 'python': sys.executable})
    assert result['error'] is None
    assert result['result']['value'] == 'PROFILE_ASSET'
    assert result['result']['blas_threads'] == '1'
    assert (root / 'asset.txt').read_text() == 'PROFILE_ASSET'
