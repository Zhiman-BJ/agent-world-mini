import json
from pathlib import Path
import subprocess
import sys

import pytest

from task_gen import task_eval_verifier as base
from task_gen.test_verifier.direct_codex_rubric import application as direct


@pytest.fixture
def bundle(tmp_path):
    state = tmp_path/'state'
    state.mkdir()
    (state/'data.json').write_text('{"value":8}')
    inputs = dict(task={'task_id':'test','task_text':'Return value.'}, environment={},
                  initial_state=state, actual_state=state, calls=[], answer='8', execution={})
    evidence = base.prepare_verification_workspace(tmp_path/'source', **inputs)
    (evidence/'fallback_input.json').write_text(json.dumps(inputs, default=str))
    verifier = tmp_path/'verifier'
    verifier.mkdir()
    doc = {'shared_definitions':{'rounding':'Allow half a last digit.'}, 'rubrics':[
        {'id':'R1','weight':1,'scale':'ordinal_5','goal':'Return value.',
         'criteria':{str(i):f'level {i}' for i in range(6)},
         'task_quotes':['Return value.'], 'missing_policy':'Missing answer scores zero.',
         'evidence_plan':'Error <= 1e-7.', 'codex_work':'Inspect answer.',
         'method':'static','static_tools':['T1'],'required_tools':['T1']}]}
    (verifier/'rubrics.json').write_text(json.dumps(doc))
    (verifier/'instructions.md').write_text('Tolerance is 1e-7. Use T1.')
    (verifier/'tools').mkdir()
    (verifier/'tools/T1.py').write_text('raise RuntimeError("must not run")')
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('model="test"')
    config={'codex':{'codex_home':str(home),'attempts':1,'timeout_seconds':15}}
    return verifier,evidence,config


def response(score=5, outcome="pass"):
    return {'task_id':'test','outcome':outcome,'summary':'Checked answer against data.', 'rubrics':[
        {'rubric_id':'R1','score':score,'reason':'Checked the fixed criterion.',
         'evidence':[{'path':'evidence/actual/answer.txt','locator':'line 1','finding':'8'}]}]}


@pytest.mark.parametrize('score,outcome',[(0,'fail'),(3,'pass'),(5,'fail')])
def test_direct_base_investigation_scores_fixed_rubrics_and_isolates_tools(bundle,tmp_path,monkeypatch,score,outcome):
    verifier,evidence,config=bundle
    original=(verifier/'rubrics.json').read_bytes()
    def run(client,prompt,*,working_directory):
        assert '1e-7' in prompt and 'half a last digit' in prompt
        script = f'''
from pathlib import Path
assert not Path({str(verifier/'tools/T1.py')!r}).exists()
assert Path('evidence/actual/answer.txt').read_text()=='8'
for p in (Path('prompt.txt'),Path('evidence/actual/answer.txt')):
    try:p.write_text('changed')
    except OSError:pass
    else:raise AssertionError('writable protected input')
Path('scratch/investigation.md').write_text('R1: checked data.json and answer: 8.')
'''
        result=subprocess.run([*client.command_prefix,'/usr/bin/python3','-c',script],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        return json.dumps(response(score,outcome))
    monkeypatch.setattr(base._VerifierClient,'run',run)
    monkeypatch.setattr(direct,'verify_execution',lambda **kw: pytest.fail('valid score must not fallback'))
    result=direct.apply(verifier,evidence,tmp_path/'output',config)
    assert result['mode']=='direct_codex_rubric',result
    assert result['score']==score/5 and result['status']=='completed'
    assert result['outcome']==outcome and result['summary']=='Checked answer against data.'
    assert result['static_tools_enabled'] is False and result['tool_calls']==[]
    assert (verifier/'rubrics.json').read_bytes()==original
    assert json.loads((tmp_path/'output/investigation/logs/attempts.json').read_text())[0]['error'] is None


@pytest.mark.parametrize('problem',['missing_rubric','invalid_score','bad_path','no_investigation','explicit_error','missing_outcome','invalid_outcome'])
def test_invalid_fixed_rubric_verdict_falls_back_whole_task(bundle,tmp_path,monkeypatch,problem):
    verifier,evidence,config=bundle
    def run(client,prompt,*,working_directory):
        if problem!='no_investigation':
            (working_directory/'scratch/investigation.md').write_text('R1 checked answer.')
        value=response()
        if problem=='missing_rubric':value['rubrics']=[]
        if problem=='invalid_score':value['rubrics'][0]['score']=6
        if problem=='bad_path':value['rubrics'][0]['evidence'][0]['path']='../verifier/rubrics.json'
        if problem=='missing_outcome':value.pop('outcome')
        if problem=='invalid_outcome':value['outcome']='unknown'
        if problem=='explicit_error':value={'verification_error':'Unable to establish facts.'}
        return json.dumps(value)
    monkeypatch.setattr(base._VerifierClient,'run',run)
    monkeypatch.setattr(direct,'verify_execution',lambda **kw:{'outcome':'pass'})
    result=direct.apply(verifier,evidence,tmp_path/'output',config)
    assert result['mode']=='fallback_codex' and result['score'] is None
    assert result['outcome']=='pass' and result['fallback_reason']


def test_fixed_prompt_preserves_standards_and_replaces_decomposition(bundle):
    verifier,_,_=bundle
    prompt=direct.build_prompt(json.loads((verifier/'rubrics.json').read_text()),(verifier/'instructions.md').read_text())
    assert '1e-7' in prompt and 'half a last digit' in prompt
    assert 'scratch/investigation.md' in prompt
    assert '分配 R1、R2' not in prompt
    assert '不得增删' in prompt
    assert prompt.endswith('task_id、outcome、summary 和 rubrics 都是必需字段。')


def test_directory_evidence_is_valid_but_paths_outside_evidence_are_not(bundle, tmp_path):
    verifier, evidence, _ = bundle
    root = tmp_path/'investigation'
    import shutil
    shutil.copytree(evidence, root)
    (root/'scratch').mkdir(exist_ok=True)
    (root/'scratch/investigation.md').write_text('R1: searched actual state directory.')
    value = response()
    value['rubrics'][0]['evidence'][0]['path'] = 'evidence/actual'
    document = json.loads((verifier/'rubrics.json').read_text())
    assert direct.validate_result(value, root, document)['score'] == 1
    value['rubrics'][0]['evidence'][0]['path'] = 'request.json'
    with pytest.raises(ValueError, match='relative path'):
        direct.validate_result(value, root, document)


def test_generation_acceptance_never_uses_fallback(bundle, tmp_path, monkeypatch):
    verifier, evidence, config = bundle
    monkeypatch.setattr(direct, 'run_verification_agent', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('host error')))
    monkeypatch.setattr(direct, 'verify_execution', lambda **k: pytest.fail('acceptance must not fallback'))
    result = direct.apply(verifier, evidence, tmp_path/'output', dict(config, fallback_enabled=False))
    assert result['mode'] == 'direct_codex_rubric' and result['status'] == 'verification_error'
    assert result['error']['message'] == 'host error'


def test_moved_relative_fallback_inputs_and_runtime_view(bundle, tmp_path, monkeypatch):
    verifier, evidence, config = bundle
    inputs = json.loads((evidence/'fallback_input.json').read_text())
    inputs.update(initial_state='evidence/initial_state', actual_state='evidence/actual/final_state')
    (evidence/'fallback_input.json').write_text(json.dumps(inputs))
    import shutil
    relocated = tmp_path/'relocated'
    shutil.copytree(evidence, relocated)
    def run(root, cfg, **kwargs):
        assert relocated in [Path(p) for p in cfg['runtime_paths']]
        assert (root/'scratch/evidence_bundle').resolve() == relocated
        assert 'cd scratch' in (root/'prompt.txt').read_text()
        raise RuntimeError('controlled failure')
    def fallback(**kwargs):
        assert kwargs['initial_state'] == relocated/'evidence/initial_state'
        assert kwargs['actual_state'] == relocated/'evidence/actual/final_state'
        return {'outcome':'pass'}
    monkeypatch.setattr(direct, 'run_verification_agent', run)
    monkeypatch.setattr(direct, 'verify_execution', fallback)
    result = direct.apply(verifier, relocated, tmp_path/'output', config)
    assert result['mode'] == 'fallback_codex' and result['outcome'] == 'pass'


def test_original_runtime_cli_is_importable_in_direct_sandbox(bundle, tmp_path, monkeypatch):
    verifier, evidence, config = bundle
    (evidence/'program_request.json').write_text(json.dumps({
        'initial_state':'evidence/initial', 'actual':{'final_state':'evidence/actual/final'},
        'reference':{}, 'environment':'evidence/environment.json'}))
    def run(client, prompt, *, working_directory):
        completed = subprocess.run([*client.command_prefix, 'scratch/environment_tool', '--help'],
                                   capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        assert '--evidence' in completed.stdout
        completed = subprocess.run([*client.command_prefix, sys.executable, '-c',
            "import json; from pathlib import Path; import sys; "
            f"sys.path.insert(0, {str(Path(direct.__file__).resolve().parents[3])!r}); "
            "from env_gen.tool_gen.runtime import SCHEMA_ROOT; "
            "assert json.loads((SCHEMA_ROOT/'toolgen_runtime.schema.json').read_text())"],
            capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        (working_directory/'scratch/helper.py').write_text("def run(arguments, context):\n    return {'success':True}\n")
        completed = subprocess.run([*client.command_prefix, '/bin/sh', '-c',
            'cd scratch && ./environment_tool --evidence evidence_bundle --scratch . '
            '--config runtime_config.json --code helper.py --arguments \'{}\''], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == {'success':True}
        (working_directory/'scratch/investigation.md').write_text('R1: inspected answer.')
        return json.dumps(response())
    monkeypatch.setattr(base._VerifierClient, 'run', run)
    result = direct.apply(verifier, evidence, tmp_path/'output', dict(config, fallback_enabled=False))
    assert result['mode'] == 'direct_codex_rubric' and result['status'] == 'completed', result
