import sqlite3
import sys
from pathlib import Path

import pytest

from harness.sandbox import _workspace_usage, run_tool_direct, run_tool_in_sandbox
from harness.execution import call_environment_tool


def test_workspace_usage_uses_one_non_following_stat_per_entry(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'sample.bin').write_bytes(b'1234')
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'empty.txt').write_text('')
    original_stat = Path.stat
    observed = []

    def record_stat(path, *arguments, **options):
        observed.append((path.relative_to(workspace).as_posix(), options))
        return original_stat(path, *arguments, **options)

    monkeypatch.setattr(Path, 'stat', record_stat)
    assert _workspace_usage(workspace) == (4, 3, None)
    assert sorted(observed) == [
        ('nested', {'follow_symlinks': False}),
        ('nested/empty.txt', {'follow_symlinks': False}),
        ('sample.bin', {'follow_symlinks': False}),
    ]


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime isolation')
@pytest.mark.parametrize('runner', [run_tool_direct, run_tool_in_sandbox])
def test_read_only_runtime_supports_scratch_cache_and_posix_queue(tmp_path, runner):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    code = '''def run(arguments, context):
    import multiprocessing
    import os
    from pathlib import Path
    q = multiprocessing.SimpleQueue()
    q.put(17)
    value = q.get()
    q.close()
    Path('library-cache').write_text('cache')
    with open('/dev/null', 'w') as stream:
        stream.write('log')
    try:
        (context.workspace_root / 'business.txt').write_text('changed')
    except (OSError, PermissionError):
        blocked = True
    else:
        blocked = False
    return {'value': value, 'blocked': blocked, 'cache': Path('library-cache').read_text()}
'''
    outcome = runner(code, {}, workspace, 30, 1024**3, 1024**2, read_only=True)
    assert outcome['error'] is None, outcome
    assert outcome['result'] == {'value': 17, 'blocked': True, 'cache': 'cache'}
    assert list(workspace.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime isolation')
@pytest.mark.parametrize('runner', [run_tool_direct, run_tool_in_sandbox])
def test_scientific_results_serialize_nested_numpy_values(tmp_path, runner):
    pytest.importorskip('numpy')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    code = '''def run(arguments, context):
    import numpy
    return {'success': True, 'data': {
        'matrix': numpy.array([[1, 2], [3, 4]], dtype=numpy.int64),
        'scalar': numpy.float32(1.5),
        'nested': (numpy.bool_(True), {'count': numpy.int64(7)}),
        'empty': numpy.array([]), 'zero_dimensional': numpy.array(9)}}
'''
    outcome = runner(code, {}, workspace, 30, 1024**3, 1024**2, read_only=True)
    assert outcome['error'] is None, outcome
    assert outcome['result'] == {'success': True, 'data': {
        'matrix': [[1, 2], [3, 4]], 'scalar': 1.5,
        'nested': [True, {'count': 7}], 'empty': [], 'zero_dimensional': 9}}
    assert list(workspace.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime isolation')
@pytest.mark.parametrize('runner', [run_tool_direct, run_tool_in_sandbox])
@pytest.mark.parametrize('expression', ["float('nan')", "float('inf')", 'object()'])
def test_runtime_reports_non_json_results_without_corrupting_the_wire(tmp_path, runner, expression):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    code = 'def run(arguments, context):\n    return {"value": ' + expression + '}\n'
    outcome = runner(code, {}, workspace, 30, 1024**3, 1024**2, read_only=True)
    assert outcome['kind'] == 'exception'
    assert outcome['result'] is None
    assert outcome['error'].startswith(('ValueError:', 'TypeError:')), outcome
    assert 'JSON' in outcome['error']


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime isolation')
@pytest.mark.parametrize('runner', [run_tool_direct, run_tool_in_sandbox])
def test_numpy_extended_precision_does_not_recurse_or_silently_round(tmp_path, runner):
    numpy = pytest.importorskip('numpy')
    if not isinstance(numpy.longdouble('1.25').item(), numpy.generic):
        pytest.skip('This platform represents longdouble as a native scalar')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    code = '''def run(arguments, context):
    import numpy
    return {'value': numpy.longdouble('1.25')}
'''
    outcome = runner(code, {}, workspace, 30, 1024**3, 1024**2, read_only=True)
    assert outcome['kind'] == 'exception'
    assert outcome['error'] == 'TypeError: NumPy scalar has no lossless JSON representation'


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime isolation')
@pytest.mark.parametrize('workspace_mode', [False, True])
@pytest.mark.parametrize('item_type', ['integer', 'string'])
def test_numpy_normalization_preserves_public_output_schema_checks(tmp_path, workspace_mode, item_type):
    pytest.importorskip('numpy')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    database = workspace / 'records.sqlite'
    with sqlite3.connect(database):
        pass
    tool = {
        'name': 'scientific_values',
        'inputSchema': {'type': 'object'},
        'outputSchema': {'type': 'object', 'properties': {
            'success': {'const': True},
            'data': {'type': 'array', 'items': {'type': item_type}},
        }, 'required': ['success', 'data']},
        'usageConditions': {'targetResources': [], 'sideEffects': []},
        'internal': {'code': '''def run(arguments, context):
    import numpy
    return {'success': True, 'data': numpy.array([1, 2], dtype=numpy.int64)}
'''},
    }
    outcome = call_environment_tool(
        'scientific_values', {}, {'scientific_values': tool}, workspace,
        environment={'schema_version': '2.0', 'record_sets': [], 'filesystem_scopes': []},
        timeout=30, memory_limit=1024**3, write_limit=1024**2,
        **({'record_database': database, 'scope_mounts': {}} if workspace_mode else {}),
    )
    assert outcome['result'] == {'success': True, 'data': [1, 2]}
    if item_type == 'integer':
        assert outcome['error'] is None, outcome
    else:
        assert outcome['error'] is not None, outcome
        assert 'string' in outcome['error']
