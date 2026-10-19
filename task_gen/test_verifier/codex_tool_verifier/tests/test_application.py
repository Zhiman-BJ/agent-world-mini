import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from task_gen.test_verifier.codex_tool_verifier import application
from .conftest import result


def test_public_request_stays_relative_and_tool_context_serializes_paths(bundle, tmp_path):
    from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer, make_context
    root, evidence = bundle
    output = tmp_path/'output'
    with ToolServer(root, evidence, output, {}) as server:
        request = json.loads((output/'request.json').read_text())
        assert request == json.loads((evidence/'program_request.json').read_text())
        context = make_context(request, evidence, server.scratch)
        assert context.actual['final_state'] == evidence/'final'
        assert context.evidence_path(context.actual['final_state']/'data.json') == 'final/data.json'
        artifact = context.scratch_dir/'inspection.json'
        artifact.write_text('{}')
        assert context.evidence_path(artifact) == 'scratch/inspection.json'


def fake_worker(monkeypatch, *, error=False, score=1, outcome="pass", summary="Checked fixed criteria."):
    processes = []
    def launch(server, config, spec_path, stdout, stderr):
        script = '''
import json, subprocess, sys, time
from pathlib import Path
output, source = map(Path, sys.argv[1:3])
p = output/'scratch/input.json'
p.write_text(json.dumps({'path':str(source/'final/data.json')}))
call=subprocess.run([sys.executable,'-m','task_gen.test_verifier.codex_tool_verifier.tool_runner','--session',str(output/'session.json'),'--tool','T1','--input',str(p)],capture_output=True,text=True)
value=json.loads(call.stdout)
if sys.argv[3]=='error': time.sleep(20)
r=json.loads(sys.argv[4]); r['tool_call_ids']=[value['call_id']]
(output/'logs/worker_result.json').write_text(json.dumps({'status':'completed','outcome':sys.argv[5],'summary':json.loads(sys.argv[6]),'rubrics':[r]}))
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(server.output), str(server.evidence),
                                    'error' if error else 'ok', json.dumps(result(score=score)), outcome, json.dumps(summary)],
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        processes.append(process)
        return process
    monkeypatch.setattr(application, '_launch_worker', launch)
    return processes


@pytest.mark.parametrize('score', [0, 1])
def test_success_and_zero_do_not_fallback(bundle, tmp_path, monkeypatch, score):
    root, evidence = bundle
    fake_worker(monkeypatch, score=score)
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: pytest.fail('unexpected fallback'))
    value = application.apply(root, evidence, tmp_path/'run', {})
    assert value['mode'] == 'codex_with_tools' and value['score'] == score
    assert value['outcome']=='pass' and value['summary']=='Checked fixed criteria.'
    assert len(value['tool_calls']) == 1
    assert value['tool_calls'][0]['status'] == 'completed'


@pytest.mark.parametrize('fallback_fails', [False, True])
def test_tool_failure_kills_main_before_whole_task_fallback(bundle, tmp_path, monkeypatch, fallback_fails):
    root, evidence = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    raise RuntimeError("unexpected failure")\n')
    processes = fake_worker(monkeypatch, error=True)
    calls = []
    def fallback(**kwargs):
        assert processes[0].poll() is not None
        calls.append(kwargs)
        if fallback_fails:
            raise RuntimeError('fallback failed')
        return {'outcome':'pass'}
    monkeypatch.setattr(application, 'verify_execution', fallback)
    started = time.monotonic()
    value = application.apply(root, evidence, tmp_path/'run', {'application_timeout_seconds':10})
    assert time.monotonic()-started < 10 and len(calls) == 1
    assert value['mode'] == 'fallback_codex' and value['score'] is None
    assert value['status'] == ('verification_error' if fallback_fails else 'completed')
    assert 'unexpected failure' in value['fallback_reason']['message']
    assert value['tool_calls'][0]['status'] == 'error'


def test_writable_output_cannot_cover_evidence(bundle):
    root, evidence = bundle
    with pytest.raises(ValueError, match='separate'):
        application.apply(root, evidence, evidence/'run', {})


def test_generation_acceptance_does_not_fallback(bundle, tmp_path, monkeypatch):
    root, evidence = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    raise RuntimeError("broken tool")\n')
    fake_worker(monkeypatch, error=True)
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: pytest.fail('acceptance must not fallback'))
    value = application.apply(root, evidence, tmp_path/'run', {'fallback_enabled':False})
    assert value['mode'] == 'codex_with_tools' and value['status'] == 'verification_error'
    assert 'broken tool' in value['error']['message']
    assert value['score'] is None and not (tmp_path/'run/fallback').exists()


def test_main_codex_sandbox_sees_tools_but_cannot_modify_inputs(bundle, tmp_path, monkeypatch):
    root, evidence = bundle
    from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer
    class Client:
        def __init__(self, **kwargs):
            self.prefix = kwargs['command_prefix']
        def run(self, prompt, *, working_directory):
            script = '''
from pathlib import Path
import json, subprocess, sys
root, evidence, output = map(Path, sys.argv[1:])
for path in [root/'rubrics.json', root/'tools/T1.py', evidence/'final/data.json', output/'session.json', output/'logs/host_record']:
    try: path.write_text('corrupt')
    except OSError: pass
    else: raise AssertionError(str(path)+' is writable')
p = output/'scratch/input.json'
p.write_text(json.dumps({'path':str(evidence/'final/data.json')}))
value=subprocess.run([sys.executable,'-m','task_gen.test_verifier.codex_tool_verifier.tool_runner','--session',str(output/'session.json'),'--tool','T1','--input',str(p)], capture_output=True,text=True)
assert value.returncode == 0, value.stderr+value.stdout
print(value.stdout)
'''
            checked = subprocess.run([*self.prefix, sys.executable, '-c', script, str(root), str(evidence), str(output)],
                                     capture_output=True, text=True, timeout=15)
            assert checked.returncode == 0, checked.stderr
            call = json.loads(checked.stdout)
            return json.dumps({'outcome':'fail','summary':'Checked fixed criteria.','rubrics':[result([call['call_id']])]})
    monkeypatch.setattr(application, '_MainClient', Client)
    output = tmp_path/'run'
    (output/'logs').mkdir(parents=True)
    with ToolServer(root, evidence, output, {}) as server:
        (output/'prompt.txt').write_text(application.build_prompt(server))
        spec = tmp_path/'worker.json'
        spec.write_text(json.dumps({'output':str(output), 'verifier':str(root), 'evidence':str(evidence),
                                    'socket_dir':str(server.socket_dir), 'config':{}}))
        application.worker(spec)
    value = json.loads((output/'logs/worker_result.json').read_text())
    assert value['status'] == 'completed', value
    assert value['outcome']=='fail' and value['summary']=='Checked fixed criteria.'
    assert server.records()[0]['status'] == 'completed'


def test_real_client_can_write_final_message_without_writable_host_logs(bundle, tmp_path):
    root, evidence = bundle
    executable = tmp_path/'fake_cli/bin/x/y/codex'
    executable.parent.mkdir(parents=True)
    executable.write_text(f'''#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
args=sys.argv
scratch=Path(args[args.index('--cd')+1]); output=scratch.parent
try: (output/'logs/injected.json').write_text('fake')
except OSError: pass
else: raise AssertionError('host logs writable')
request=json.loads((output/'request.json').read_text())
p=scratch/'input.json'; p.write_text(json.dumps({{'path':str((scratch/'evidence_bundle'/request['actual']['final_state']/'data.json').resolve())}}))
call=subprocess.run([sys.executable,'-m','task_gen.test_verifier.codex_tool_verifier.tool_runner','--session',str(output/'session.json'),'--tool','T1','--input',str(p)], capture_output=True, text=True)
assert call.returncode==0,call.stderr+call.stdout
value=json.loads(call.stdout)
r={result()!r};r['tool_call_ids']=[value['call_id']]
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({{'outcome':'fail','summary':'Checked fixed criteria.','rubrics':[r]}}))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':17,'output_tokens':3}}}}))
''')
    executable.chmod(0o755)
    value = application.apply(root, evidence, tmp_path/'real_client_run',
                              {'codex':{'executable':str(executable)}, 'application_timeout_seconds':15})
    assert value['mode'] == 'codex_with_tools', value.get('fallback_reason')
    assert value['score'] == 1 and value['outcome']=='fail'
    assert value['cost']['calls'][0]['usage'] == [{'input_tokens':17,'output_tokens':3}]


def test_missing_cli_does_not_count_as_main_model_call(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: {'outcome':'pass'})
    value = application.apply(*bundle, tmp_path/'missing_cli', {'codex':{'executable':'/definitely/no/codex'}})
    assert value['mode'] == 'fallback_codex'
    assert not value['main_started']
    assert value['cost']['calls'] == []


def test_main_prompt_uses_supported_short_citations_and_distinguishes_tool_lists(bundle, tmp_path):
    from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer
    root, evidence = bundle
    with ToolServer(root, evidence, tmp_path/'prompt_run', {}) as server:
        prompt=application.build_prompt(server)
    assert '相对路径' in prompt and 'scratch/' in prompt
    assert 'scratch/<call_id>/' in prompt and 'relative_to' in prompt
    assert '顶层数组' in prompt and 'tools 字段' in prompt
    assert '文件/目录绝对路径' not in prompt


def test_short_citations_preserve_existence_and_containment(bundle, tmp_path):
    from task_gen.test_verifier.program_verifier.contracts import resolve_evidence_path, RubricVerificationError
    root, evidence=bundle
    scratch=tmp_path/'scratch'; scratch.mkdir()
    (scratch/'note.json').write_text('{}')
    assert resolve_evidence_path('final/data.json', evidence, scratch)==evidence/'final/data.json'
    assert resolve_evidence_path('scratch/note.json', evidence, scratch)==scratch/'note.json'
    for invalid in ('final/missing.json', '../verifier/rubrics.json', 'scratch/../verifier/rubrics.json'):
        with pytest.raises(RubricVerificationError, match='missing or outside'):
            resolve_evidence_path(invalid, evidence, scratch)


@pytest.mark.parametrize('score', [0, 1])
def test_rubric_only_keeps_scores_and_blocks_generated_tools(bundle, tmp_path, monkeypatch, score):
    root, evidence = bundle
    original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    executable = tmp_path/'rubric_cli/bin/x/y/codex'
    executable.parent.mkdir(parents=True)
    executable.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
args=sys.argv
scratch=Path(args[args.index('--cd')+1]); output=scratch.parent
view=output/'rubric_only'
rubric=json.loads((view/'rubrics.json').read_text())['rubrics'][0]
assert rubric['criteria']=={{'0':'No valid data','1':'Valid data'}}
assert rubric['weight']==2 and rubric['scale']=='binary'
assert rubric['missing_policy']=='Missing file scores zero.'
assert 'static_tools' not in rubric and 'required_tools' not in rubric
assert (view/'instructions.md').read_text()=='Use T1 to read the JSON, then judge R1.'
assert not Path({str(root/'tools/T1.py')!r}).exists()
assert not Path({str(application.REPOSITORY/'task_gen/test_verifier/codex_tool_verifier/application.py')!r}).exists()
assert not (view/'tools.json').exists() and not (view/'tools').exists()
request=json.loads((output/'request.json').read_text())
candidate=scratch/'evidence_bundle'/request['actual']['final_state']/'data.json'
assert json.loads(candidate.read_text())=={{'value':3}}
for p in [candidate,view/'rubrics.json',output/'logs/injected.json']:
    try:p.write_text('corrupt')
    except OSError:pass
    else:raise AssertionError(str(p)+' writable')
(scratch/'check.txt').write_text('independent check: value=3')
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({{'outcome':'pass','summary':'Checked fixed criteria.','rubrics':[{result(score=score)!r}]}}))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':17,'output_tokens':3}}}}))
''')
    executable.chmod(0o755)
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: pytest.fail('unexpected fallback'))
    value = application.apply(root, evidence, tmp_path/'rubric_run',
                              {'static_tools_enabled':False, 'codex':{'executable':str(executable)},
                               'application_timeout_seconds':15})
    assert value['mode'] == 'codex_rubric_only', value.get('fallback_reason')
    assert value['status'] == 'completed' and value['score'] == score
    assert value['outcome']=='pass' and value['summary']=='Checked fixed criteria.'
    assert value['tool_calls'] == [] and value['cost']['tools_seconds'] == 0
    assert value['static_tools_enabled'] is False
    from task_gen.test_verifier.codex_tool_verifier.validate_reference import summarize
    stats = summarize([{'generation':{'status':'generated'}, 'application':value}])
    assert stats['main_completed'] == 1 and stats['reference_full_score'] == score
    assert original == {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_rubric_only_errors_keep_whole_task_fallback(bundle, tmp_path, monkeypatch):
    root, evidence = bundle
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: {'outcome':'pass'})
    value = application.apply(root, evidence, tmp_path/'rubric_failure',
                              {'static_tools_enabled':False, 'codex':{'executable':'/definitely/no/codex'}})
    assert value['mode'] == 'fallback_codex' and value['outcome'] == 'pass'
    assert value['static_tools_enabled'] is False and value['tool_calls'] == []


def test_rubric_only_preserves_acceptance_details(bundle, tmp_path):
    from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer
    root, evidence = bundle
    doc = json.loads((root/'rubrics.json').read_text())
    doc['shared_definitions'] = {'rounding': 'Allow half a unit in the last reported digit.'}
    doc['rubrics'][0]['evidence_plan'] = 'Angle error <= 1e-5 degrees; reflectance error <= 1e-7.'
    doc['rubrics'][0]['codex_work'] = 'Count each incorrect field only once.'
    (root/'rubrics.json').write_text(json.dumps(doc))
    original = (root/'rubrics.json').read_bytes()
    with ToolServer(root, evidence, tmp_path/'details', {'static_tools_enabled':False}) as server:
        application.prepare_rubric_only(server)
        view = json.loads((server.verifier/'rubrics.json').read_text())
    r = view['rubrics'][0]
    assert view['shared_definitions'] == {'rounding':'Allow half a unit in the last reported digit.'}
    assert r['evidence_plan'] == 'Angle error <= 1e-5 degrees; reflectance error <= 1e-7.'
    assert r['codex_work'] == 'Count each incorrect field only once.'
    assert r['error_policy'] == 'Unreadable structured data is a verifier error.'
    assert r['inputs'] == {'source':'actual.final_state'}
    assert not {'method','static_tools','required_tools'} & r.keys()
    expected = {**doc, 'rubrics':[{k:v for k,v in r.items() if k not in
                                 ('method','static_tools','required_tools')} for r in doc['rubrics']]}
    assert view == expected
    assert (root/'rubrics.json').read_bytes() == original


@pytest.mark.parametrize('outcome', ['unknown', ''])
def test_invalid_overall_outcome_falls_back(bundle, tmp_path, monkeypatch, outcome):
    fake_worker(monkeypatch, outcome=outcome)
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: {'outcome':'pass'})
    value = application.apply(*bundle, tmp_path/'invalid_outcome', {})
    assert value['mode']=='fallback_codex' and value['score'] is None
    assert 'outcome' in value['fallback_reason']['message']


@pytest.mark.parametrize('summary', [None, ''])
def test_missing_overall_summary_falls_back(bundle, tmp_path, monkeypatch, summary):
    fake_worker(monkeypatch, summary=summary)
    monkeypatch.setattr(application, 'verify_execution', lambda **kw: {'outcome':'pass'})
    value = application.apply(*bundle, tmp_path/'invalid_summary', {})
    assert value['mode']=='fallback_codex' and value['score'] is None
    assert 'summary' in value['fallback_reason']['message']


@pytest.mark.parametrize('static_tools_enabled', [False, True])
def test_host_prompt_requires_overall_outcome_and_rubric_scores(bundle, tmp_path, static_tools_enabled):
    from task_gen.test_verifier.codex_tool_verifier.tool_runner import ToolServer
    with ToolServer(*bundle, tmp_path/'contract', {'static_tools_enabled':static_tools_enabled}) as server:
        prompt = application.build_prompt(server)
    assert '"outcome":"pass 或 fail"' in prompt and '"summary":"决定性依据"' in prompt
    assert 'outcome、summary 和 rubrics 都是必需字段' in prompt
    assert '不自行计算总分或设置总分门槛' in prompt
