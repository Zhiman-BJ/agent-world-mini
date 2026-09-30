from harness.execution import call_environment_tool


def test_missing_output_file_keeps_original_state(tmp_path):
    state = tmp_path / 'state'
    files = state / 'filesystem_scopes/reports'
    files.mkdir(parents=True)
    (files / 'original.txt').write_text('original')
    environment = {'schema_version': '2.0', 'record_sets': [],
                   'filesystem_scopes': [{'scope_id': 'reports', 'access': 'copy_on_write'}]}
    tool = {'name': 'export', 'inputSchema': {'type': 'object'},
            'usageConditions': {'targetResources': ['reports'], 'sideEffects': ['Writes a report']},
            'outputSchema': {'type': 'object', 'properties': {'success': {'const': True}, 'path': {
                'type': 'string', 'x-resource-scope': 'reports', 'x-resource-kind': 'file'}}},
            'internal': {'code': ''}}

    def execute(code, arguments, candidate, *args, **kwargs):
        (candidate / 'filesystem_scopes/reports/original.txt').write_text('changed')
        return {'kind': None, 'error': None, 'result': {'success': True, 'path': 'missing.txt'}}

    result = call_environment_tool('export', {}, {'export': tool}, state,
        timeout=10, memory_limit=1024**2, write_limit=1024**2,
        environment=environment, call_tool_fn=execute)
    assert '输出资源不存在' in result['error']
    assert (files / 'original.txt').read_text() == 'original'
