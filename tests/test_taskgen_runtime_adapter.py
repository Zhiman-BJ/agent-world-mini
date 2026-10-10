import json
import subprocess

from task_gen.tool_runtime import call_environment_tool


def test_mcp_records_bad_resource_argument_and_accepts_corrected_call(tmp_path):
    from task_gen.task_eval_mcp import TaskEvalMcpServer
    from tests.test_kimi_mcp import KimiMcpTests
    binding = KimiMcpTests()._make_delivery(tmp_path, with_report_tool=True)
    server = TaskEvalMcpServer({
        'binding_path': str(binding), 'state_root': str(tmp_path / 'support/state'),
        'trace': str(tmp_path / 'trace.jsonl'), 'max_tool_calls': 5,
        'timeout': 10, 'memory_limit': 2 * 1024**3, 'write_limit': 1024**2,
    })
    response = server.handle({'method': 'tools/call', 'params': {
        'name': 'get_ticket', 'arguments': {'ticket_id': 'aw://reports/daily.json'}}})
    assert response['isError']
    record = json.loads((tmp_path / 'trace.jsonl').read_text())
    assert record['failure_kind'] == 'invalid_arguments'
    corrected = server.handle({'method': 'tools/call', 'params': {'name': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
    assert corrected['structuredContent']['data']['status'] == 'open'


def test_docker_reuses_upstream_worker_and_rolls_back_failure(tmp_path, monkeypatch):
    from task_gen.program.utils import tool_runtime
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'value').write_text('original')
    calls = []
    def worker(package, code, arguments, candidate, *args, **kwargs):
        calls.append((package.delivery, kwargs))
        (candidate / 'value').write_text('changed')
        return {'kind': 'exception', 'result': None, 'error': 'failed'}
    monkeypatch.setattr(tool_runtime, '_docker_call_tool', worker)
    tool = {'inputSchema': {'type': 'object'}, 'outputSchema': {'type': 'object'}, 'internal': {'code': 'unused'}}
    result = call_environment_tool('write', {}, {'write': tool}, state,
        runtime={'backend': 'docker', 'software_root': '/opt/tool-software'},
        binding_path=tmp_path / 'binding.json', timeout=10, memory_limit=1024**3, write_limit=1024**2)
    assert calls[0][0]['binding_path'] == str(tmp_path / 'binding.json')
    assert str(calls[0][1]['software_root']) == '/opt/tool-software'
    assert result['error'] == 'failed'
    assert (state / 'value').read_text() == 'original'


def test_docker_timeout_becomes_retryable_observation(tmp_path, monkeypatch):
    from task_gen.program.utils import tool_runtime
    def worker(*args, **kwargs):
        raise subprocess.TimeoutExpired('docker', 10)
    monkeypatch.setattr(tool_runtime, '_docker_call_tool', worker)
    state = tmp_path / 'state'
    state.mkdir()
    tool = {'inputSchema': {'type': 'object'}, 'outputSchema': {'type': 'object'}, 'internal': {'code': 'unused'}}
    record = call_environment_tool('read', {}, {'read': tool}, state,
        runtime={'backend': 'docker', 'software_root': '/opt/tool-software'}, binding_path=tmp_path / 'binding.json',
        timeout=10, memory_limit=1024**3, write_limit=1024**2)
    assert record['failure_kind'] == 'timeout'
