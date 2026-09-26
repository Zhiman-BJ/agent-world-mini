"""Public protocol must stay independent of Kimi's long-result presentation."""
import io
import json
import sqlite3
from pathlib import Path
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


def test_copied_venv_runs_with_its_own_import_paths(tmp_path):
    import subprocess
    import sys
    from task_gen.task_eval_mcp import call_environment_tool
    root = tmp_path / 'software'
    launcher = root / 'venv/bin/python'
    subprocess.run([sys.executable, '-m', 'venv', '--copies', '--without-pip',
                    str(root / 'venv')], check=True)
    # Install compatible fixture dependencies into the venv, not via host search paths.
    import shutil
    import jsonschema
    import typing_extensions
    purelib = subprocess.check_output([str(launcher), '-I', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    packages = Path(jsonschema.__file__).parent.parent
    for name in ('jsonschema', 'jsonschema_specifications', 'referencing', 'rpds', 'attr', 'attrs'):
        shutil.copytree(packages / name, Path(purelib) / name)
    shutil.copyfile(Path(typing_extensions.__file__), Path(purelib) / 'typing_extensions.py')
    state = tmp_path / 'state'
    state.mkdir()
    tool = {'name': 'inspect', 'inputSchema': {'type': 'object'}, 'outputSchema': {'type': 'object'},
            'internal': {'code': "def run(arguments, context):\n import sys, jsonschema\n return {'success': True, 'prefix': sys.prefix, 'paths': sys.path}"}}
    result = call_environment_tool('inspect', {}, {'inspect': tool}, state, timeout=20,
        memory_limit=2147483648, write_limit=268435456,
        software={'root': str(root), 'python': str(launcher)})
    assert result['error'] is None, result
    assert result['result']['prefix'] == str(root / 'venv')
    assert '/dependencies' not in result['result']['paths']


def test_pipeline_loads_binding_and_keeps_runtime_out_of_public_inputs(tmp_path):
    from tests.test_kimi_mcp import KimiMcpTests
    from task_gen.tool_graph.contracts import Config
    from task_gen.tool_graph.step_0_environment_load import load_environment
    from task_gen.tool_graph.run_io import to_build_graph_input, to_execute_chains_input
    binding = KimiMcpTests()._make_delivery(tmp_path)
    config = Config(environment_dir=binding.parent)
    output = load_environment({'config': config})
    assert output['environment']['tools'][0]['name'] == 'get_ticket'
    assert Path(output['runtime']['initial_state']).is_dir()
    assert output['runtime']['binding_path'] == str(binding)
    assert 'runtime' not in to_build_graph_input(output, config)
    assert 'runtime' not in output['environment']
    execution = to_execute_chains_input({**output, 'tasks': []}, config, tmp_path / 'run')
    assert execution['runtime'] == output['runtime']


def test_merged_execution_uses_delivery_state_and_profile(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import sys
    import jsonschema
    import typing_extensions
    from tests.test_kimi_mcp import KimiMcpTests
    from task_gen.tool_graph.contracts import Config
    from task_gen.tool_graph.step_0_environment_load import load_environment
    from task_gen.tool_graph.run_io import to_execute_chains_input
    from task_gen.tool_graph.execution_agent import execute_candidates
    from task_gen.task_eval_mcp import TaskEvalMcpServer
    binding = KimiMcpTests()._make_delivery(tmp_path)
    config = Config(environment_dir=binding.parent,
                    execution={'min_tool_calls': 1, 'target_tool_calls': 1})
    bundle = load_environment({'config': config})
    software = tmp_path / 'software'
    launcher = software / 'venv/bin/python'
    subprocess.run([sys.executable, '-m', 'venv', '--copies', '--without-pip',
                    str(software / 'venv')], check=True)
    purelib = Path(subprocess.check_output([str(launcher), '-I', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    packages = Path(jsonschema.__file__).parent.parent
    for name in ('jsonschema', 'jsonschema_specifications', 'referencing', 'rpds', 'attr', 'attrs'):
        shutil.copytree(packages / name, purelib / name)
    shutil.copyfile(Path(typing_extensions.__file__), purelib / 'typing_extensions.py')
    (software / 'marker').write_text('correct profile')
    bundle['runtime']['software'] = {'root': str(software), 'python': str(launcher)}
    tool = bundle['environment']['tools'][0]
    tool['internal']['code'] = tool['internal']['code'].replace(
        'record = context.records.get',
        "assert (context.software_root / 'marker').read_text() == 'correct profile'\n    record = context.records.get")
    bundle['tasks'] = [{'task_id': 'task1', 'chain': ['get_ticket'], 'objective': 'Read ticket', 'score': 1}]

    preparation_calls = 0

    def run(client, prompt, working_directory):
        nonlocal preparation_calls
        client.session_id = client.session_id or f'test-{Path(client.server_config).parent.name}'
        if 'preparation' in Path(client.server_config).parts:
            preparation_calls += 1
            action = 'execute' if preparation_calls == 1 else 'accept'
            return json.dumps({'action': action, 'objective': 'Read ticket',
                               'reason': 'Use the delivery state and profile.',
                               'feedback': '', 'score': 0 if action == 'execute' else 4})
        server = TaskEvalMcpServer(json.loads(client.server_config.read_text()))
        response = server.handle({'method': 'tools/call', 'params': {
            'name': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
        assert response['structuredContent']['data']['status'] == 'open', response
        return json.dumps({'reason': 'Read actual ticket', 'completed': True, 'answer': 'open'})

    monkeypatch.setattr('task_gen.tool_graph.execution_agent._ReviewClient.run', run)
    result = execute_candidates(to_execute_chains_input(bundle, config, tmp_path / 'run'))
    assert result['tasks'][0]['execution']['success'] is True
    assert result['tasks'][0]['execution']['tool_calls'][0]['result']['data']['status'] == 'open'


def test_profile_missing_validation_dependencies_does_not_borrow_host_packages(tmp_path):
    import subprocess
    import sys
    from task_gen.tool_graph.step_3_chain_execute import _run_tool
    root = tmp_path / 'software'
    subprocess.run([sys.executable, '-m', 'venv', '--copies', '--without-pip', str(root)], check=True)
    state = tmp_path / 'state'
    state.mkdir()
    result = _run_tool('def run(arguments, context):\n import jsonschema\n return {}', {}, state, 10, 2147483648,
                       268435456, software={'root': str(root), 'python': str(root / 'bin/python')})
    assert result['kind'] == 'exception'
    assert "No module named 'jsonschema'" in result['error']


def test_library_caches_are_temporary_not_task_state(tmp_path):
    import sys
    from task_gen.tool_graph.step_3_chain_execute import _call_tool
    software = tmp_path / 'software'
    software.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    code = '''def run(arguments, context):
 import os
 from pathlib import Path
 for variable, fallback in [('XDG_CACHE_HOME', '.cache'), ('XDG_CONFIG_HOME', '.config')]:
  folder = Path(os.environ.get(variable, str(Path.home() / fallback))) / 'library'
  folder.mkdir(parents=True, exist_ok=True)
  (folder / 'cache').write_text('temporary')
 return {'success': True}
'''
    result = _call_tool(code, {}, state, 10, 2147483648, 268435456,
        {'schema_version': '2.0', 'record_sets': [], 'filesystem_scopes': []},
        software={'root': str(software), 'python': sys.executable})
    assert result['error'] is None, result
    assert list(state.iterdir()) == []


def test_profile_does_not_execute_site_hooks_or_import_external_packages(tmp_path):
    import subprocess
    import sys
    from task_gen.tool_graph.step_3_chain_execute import _run_tool
    root = tmp_path / 'profile'
    subprocess.run([sys.executable, '-m', 'venv', '--copies', '--without-pip', str(root)], check=True)
    launcher = root / 'bin/python'
    purelib = subprocess.check_output([str(launcher), '-I', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    foreign = tmp_path / 'vendor'
    foreign.mkdir(parents=True)
    (foreign / 'foreign_module.py').write_text('value = 1')
    marker = tmp_path / 'host_was_modified'
    (Path(purelib) / 'foreign.pth').write_text(str(foreign) + '\n' +
        f'import pathlib; pathlib.Path({str(marker)!r}).write_text("bad")\n')
    state = tmp_path / 'state'
    state.mkdir()
    outcome = _run_tool('def run(arguments, context):\n import importlib.util\n return {"visible": importlib.util.find_spec("foreign_module") is not None}', {}, state, 10, 2147483648,
        268435456, software={'root': str(root), 'python': str(launcher)})
    assert not marker.exists()
    assert outcome['error'] is None, outcome
    assert outcome['result']['visible'] is False


def test_scientific_library_tls_initialization_has_public_ca_bundle(tmp_path):
    import ssl
    import sys
    from task_gen.tool_graph.step_3_chain_execute import _run_tool
    if not (ssl.get_default_verify_paths().cafile or Path('/etc/ssl/certs/ca-certificates.crt').is_file()):
        pytest.skip('Host has no public CA bundle')
    software = tmp_path / 'software'
    software.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    result = _run_tool('def run(arguments, context):\n import ssl, getpass\n return {**ssl.create_default_context().cert_store_stats(), "user": getpass.getuser()}',
        {}, state, 10, 2147483648, 268435456, software={'root': str(software), 'python': sys.executable})
    assert result['error'] is None, result
    assert result['result']['x509_ca'] > 0
    assert result['result']['user']


def test_native_tool_stdout_does_not_corrupt_result_protocol(tmp_path):
    from task_gen.tool_graph.step_3_chain_execute import _run_tool
    result = _run_tool('def run(arguments, context):\n import os, ctypes\n os.write(1, b"native log\\n")\n ctypes.CDLL(None).printf(b"buffered log\\n")\n return {"success": True}',
        {}, tmp_path, 10, 2147483648, 268435456)
    assert result['error'] is None, result
    assert result['result'] == {'success': True}
