import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from harness.execution import call_environment_tool


@pytest.fixture(params=['python_profile', 'docker'])
def runtime(request):
    if request.param == 'docker':
        image = os.environ.get('TOOLGEN_DOCKER_TEST_IMAGE')
        if not image:
            pytest.skip('配置真实测试镜像后运行')
        return {'backend': 'docker', 'image': image, 'python_command': 'python',
                'software_root': '/opt/tool-software'}
    if shutil.which('bwrap') is None:
        pytest.skip('配置 bubblewrap 后运行')
    return None


@pytest.fixture
def package(tmp_path):
    state = tmp_path / 'state'
    files = state / 'filesystem_scopes/reports'
    files.mkdir(parents=True)
    (files / 'original.txt').write_text('original')
    sqlite3.connect(state / 'records.sqlite').close()
    env = {'schema_version': '2.0', 'record_sets': [],
           'filesystem_scopes': [{'scope_id': 'reports', 'access': 'copy_on_write'}]}
    return state, files, env


def invoke(package, runtime, code, *, write=False, timeout=15):
    state, _, environment = package
    tool = {'name': 'inspect', 'inputSchema': {'type': 'object'},
            'outputSchema': {'type': 'object'},
            'usageConditions': {'targetResources': ['reports'],
                                'sideEffects': ['Updates reports'] if write else []},
            'internal': {'code': code}}
    return call_environment_tool('inspect', {}, {'inspect': tool}, state,
        environment=environment, runtime=runtime, timeout=timeout,
        memory_limit=2 * 1024**3, write_limit=1024**2)


def test_generated_tool_can_resolve_and_inspect_resources(package, runtime):
    result = invoke(package, runtime, '''def run(arguments, context):
    path = context.resolve_resource('aw://reports/original.txt', must_exist=True)
    preview = context.resources.inspect('aw://reports/original.txt', preview_chars=4)
    return {'success': True, 'data': {'text': path.read_text(), 'preview': preview['text_preview']}}
''')
    assert result['error'] is None, result
    assert result['result']['data'] == {'text': 'original', 'preview': 'orig'}


def test_timeout_keeps_original_data(package, runtime):
    result = invoke(package, runtime, '''def run(arguments, context):
    import time
    context.scope_root('reports').joinpath('original.txt').write_text('changed')
    time.sleep(10)
    return {'success': True}
''', write=True, timeout=2)
    assert result['failure_kind'] == 'timeout', result
    assert package[1].joinpath('original.txt').read_text() == 'original'


def test_read_only_call_cannot_write_resource(package, runtime):
    result = invoke(package, runtime, '''def run(arguments, context):
    try:
        context.scope_root('reports').joinpath('original.txt').write_text('changed')
    except OSError:
        return {'success': True, 'write_blocked': True}
    return {'success': True, 'write_blocked': False}
''')
    assert result['error'] is None, result
    assert result['result']['write_blocked'] is True
    assert package[1].joinpath('original.txt').read_text() == 'original'
