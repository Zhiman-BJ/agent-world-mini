import json
from pathlib import Path
import subprocess
import sys

import pytest

from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer


def invoke(server, inputs, tool='T1'):
    path = server.scratch/'input.json'
    path.write_text(json.dumps(inputs))
    process = subprocess.run([sys.executable, '-m', 'task_gen.test_verifier.codex_tool_verifier.tool_runner',
                              '--session', str(server.session_path), '--tool', tool, '--input', str(path)],
                             capture_output=True, text=True, timeout=10)
    return process, json.loads(process.stdout)


def test_real_tool_cli_and_missing_state(bundle, tmp_path):
    root, evidence = bundle
    with ToolServer(root, evidence, tmp_path/'run', {}) as server:
        process, value = invoke(server, {'path': str(evidence/'final/data.json')})
        assert process.returncode == 0 and value['result'] == {'status': 'ok', 'value': 3}
        assert server.records()[0]['call_id'] == value['call_id']
        _, missing = invoke(server, {'path': str(evidence/'final/absent.json')})
        assert missing['result']['status'] == 'missing' and not server.failed.is_set()


@pytest.mark.parametrize('code', [
    'open(inputs["path"], "w").write("destroyed")',
    'return {"status":"ok", "value":"not integer"}',
    'import time; time.sleep(10)',
])
def test_tool_error_stops_session_and_protects_evidence(bundle, tmp_path, code):
    root, evidence = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    '+code+'\n')
    before = (evidence/'final/data.json').read_bytes()
    with ToolServer(root, evidence, tmp_path/'run', {'tool_timeout_seconds': .3}) as server:
        _, value = invoke(server, {'path': str(evidence/'final/data.json')})
        assert value['result']['status'] == 'verification_error'
        assert server.failed.is_set() and server.records()[0]['status'] == 'error'
        _, following = invoke(server, {'path': str(evidence/'final/data.json')})
        assert following['result']['status'] == 'verification_error'
    assert (evidence/'final/data.json').read_bytes() == before


def test_invalid_input_is_recorded_as_error(bundle, tmp_path):
    root, evidence = bundle
    with ToolServer(root, evidence, tmp_path/'run', {}) as server:
        _, value = invoke(server, {'wrong_field': True})
        assert value['result']['status'] == 'verification_error'
        assert server.failed.is_set() and 'schema' in server.records()[0]['error'].lower()


def test_invalid_json_input_notifies_host(bundle, tmp_path):
    root, evidence = bundle
    with ToolServer(root, evidence, tmp_path/'run', {}) as server:
        path = server.scratch/'broken.json'
        path.write_text('{broken')
        process = subprocess.run([sys.executable, '-m', 'task_gen.test_verifier.codex_tool_verifier.tool_runner',
                                  '--session', str(server.session_path), '--tool', 'T1', '--input', str(path)],
                                 capture_output=True, text=True, timeout=10)
        assert process.returncode == 1
        assert server.failed.is_set()
        assert server.records()[0]['status'] == 'error'


def test_tool_result_exposes_the_actual_per_call_scratch(bundle, tmp_path):
    root, evidence = bundle
    registry=json.loads((root/'tools.json').read_text())
    schema=registry['tools'][0]['output_schema']
    schema['properties']['path']={'type':'string'}
    schema['required'].append('path')
    (root/'tools.json').write_text(json.dumps(registry))
    (root/'tools/T1.py').write_text('''import json
def tool(context, inputs):
    (context.scratch_dir/'facts.json').write_text(json.dumps({'value':3}))
    return {'status':'ok','path':'facts.json'}
''')
    with ToolServer(root,evidence,tmp_path/'run',{}) as server:
        directories=[]
        for _ in range(2):
            process,value=invoke(server, {'path':str(evidence/'final/data.json')})
            assert process.returncode==0
            scratch=Path(value['scratch_dir'])
            assert scratch==server.scratch/value['call_id']
            assert json.loads((scratch/value['result']['path']).read_text())=={'value':3}
            directories.append(scratch)
        assert directories[0]!=directories[1]
        assert {r['scratch_dir'] for r in server.records()}=={str(p) for p in directories}
