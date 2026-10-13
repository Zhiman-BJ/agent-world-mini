import sys

import pytest

from harness.sandbox import run_tool_direct, run_tool_in_sandbox


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
