import json
from pathlib import Path
import shutil
import threading
import time

import pytest

from task_gen.test_verifier.program_verifier.contracts import RubricVerificationError, aggregate_results, validate_design
from task_gen.test_verifier.program_verifier.application.sdk import SDK, make_context
from task_gen.test_verifier.program_verifier.application import runner


def rubric(rid='R1', scale='binary', weight=1):
    return {'id': rid, 'goal': 'check data', 'task_quotes': ['check data'], 'weight': weight,
            'scale': scale, 'criteria': {str(i): f'observable level {i}' for i in range(2 if scale == 'binary' else 6)}, 'method': 'static',
            'inputs': {}, 'missing_policy': 'absent scores zero', 'error_policy': 'invalid format errors',
            'method_reason': 'structured data',
            'function_contract': {'file': f'rubrics/{rid}.py', 'name': f'check_{rid}', 'inputs': {}, 'returns': {}}}


def result(rid='R1', score=1):
    return {'rubric_id': rid, 'score': score, 'reason': 'observed', 'evidence': [{'path': '.', 'locator': 'directory', 'finding': 'observed'}]}


def test_weighted_scoring_and_invalid_results():
    rubrics = [rubric(weight=2), rubric('R2', 'ordinal_5', 3)]
    assert aggregate_results(rubrics, [result(score=0), result('R2', 5)])['score'] == .6
    for rows in ([result()], [result(), result()], [result(), result('R2', float('nan'))],
                 [result(), result('R2', 6)], [result(score=True), result('R2', 5)]):
        with pytest.raises(RubricVerificationError):
            aggregate_results(rubrics, rows)
    validate_design({'rubrics': rubrics}, 'check data')


def test_parallel_preserves_results_and_bounds_concurrency(tmp_path):
    sdk = SDK({'rubrics': [rubric()]}, tmp_path/'logs', tmp_path/'scratch', {'node_concurrency': 2})
    lock, running, maximum = threading.Lock(), 0, 0
    def work(i):
        nonlocal running, maximum
        with lock:
            running += 1
            maximum = max(maximum, running)
        time.sleep(.015)
        with lock:
            running -= 1
        return i
    assert sdk.parallel([lambda i=i: work(i) for i in range(6)]) == list(range(6))
    assert maximum == 2


def test_failure_cancels_queued_checks_and_keeps_partial_results(tmp_path):
    sdk = SDK({'rubrics': [rubric(), rubric('R2')]}, tmp_path/'logs', tmp_path/'scratch', {'node_concurrency': 1})
    context = make_context({'task_id': 'x', 'task_text': 'check data', 'initial_state': str(tmp_path),
                            'actual': {}, 'reference': {}}, tmp_path, tmp_path/'scratch', sdk)
    def check_R1(context, inputs):
        return result(score=0)
    check_R1.__code__ = check_R1.__code__.replace(co_filename=str(tmp_path/'rubrics/R1.py'))
    assert sdk.execute(check_R1, context, {})['score'] == 0
    started = []
    def fail():
        raise context.error('mock_error', 'cannot check', 'R2')
    with pytest.raises(RubricVerificationError, match='cannot check'):
        sdk.parallel([fail, lambda: started.append('should not start')])
    assert not started
    assert sdk.results == [result(score=0)]
    with pytest.raises(RubricVerificationError, match='stopped'):
        sdk.execute(check_R1, context, {})


def test_model_adapter_records_prompt_response_usage_and_context(tmp_path, monkeypatch):
    from task_gen.tool_graph import llm
    from types import SimpleNamespace
    monkeypatch.setattr(llm, 'infer', lambda prompt, **kw: SimpleNamespace(text='{"path":"artifact"}', model='mock', usage={'input_tokens': 7}))
    design = {'rubrics': [rubric()], 'model_calls': [{'file': 'model_calls/locate.py', 'method': 'llm', 'prompt': 'locate', 'output_contract': {}}]}
    sdk = SDK(design, tmp_path/'logs', tmp_path/'scratch')
    assert sdk.call_llm('locate', call_file='model_calls/locate.py', function_name='locate') == '{"path":"artifact"}'
    row = json.loads(next((tmp_path/'logs').glob('model_*.json')).read_text())
    assert row['usage'] == {'input_tokens': 7}
    assert row['function'] == 'locate' and row['rubric_id'] is None
    assert row['prompt'] == 'locate' and row['response'] and row['seconds'] >= 0


@pytest.mark.parametrize('declared_format', [None, 'json_object'])
def test_llm_uses_only_explicitly_declared_json_format(tmp_path, monkeypatch, declared_format):
    from task_gen.tool_graph import llm
    from types import SimpleNamespace
    seen = []
    def infer(prompt, *, llm_config):
        seen.append(llm_config)
        return SimpleNamespace(text='{}', model='mock', usage={})
    monkeypatch.setattr(llm, 'infer', infer)
    call = {'file':'model_calls/assess.py','method':'llm','prompt':'Return a JSON object','output_contract':{}}
    if declared_format:
        call['response_format'] = declared_format
    design = {'rubrics':[rubric()],'model_calls':[call]}
    validate_design(design)
    sdk = SDK(design,tmp_path/'logs',tmp_path/'scratch')
    sdk.call_llm(call['prompt'],call_file=call['file'])
    assert seen[0].get('response_format') == ({'type':'json_object'} if declared_format else None)


def test_invalid_model_response_format_is_rejected():
    call = {'file':'model_calls/assess.py','method':'llm','prompt':'assess','output_contract':{},'response_format':'guess'}
    with pytest.raises(RubricVerificationError,match='response_format'):
        validate_design({'rubrics':[rubric()],'model_calls':[call]})


@pytest.mark.parametrize('invalid', [None, 'file', 'function', 'rubric_id'])
def test_execute_declared_prompt_builder_without_scoring(tmp_path, invalid):
    design = {'rubrics': [rubric()], 'model_calls': [
        {'file': 'model_calls/assess.py', 'method': 'llm', 'prompt': 'assess', 'output_contract': {}}]}
    sdk = SDK(design, tmp_path/'logs', tmp_path/'scratch')
    def build_prompt(context, inputs):
        assert context is sdk
        return 'Assess ' + inputs['material']
    filename = 'other.py' if invalid == 'file' else 'assess.py'
    build_prompt.__code__ = build_prompt.__code__.replace(co_filename=str(tmp_path/'model_calls'/filename))
    if invalid == 'function':
        build_prompt.__name__ = 'undeclared'
    if invalid:
        with pytest.raises(RubricVerificationError) as error:
            sdk.execute(build_prompt, sdk, {'material': 'rows'}, rubric_id='R1' if invalid == 'rubric_id' else None)
        assert error.value.code == ('invalid_rubric_id' if invalid == 'rubric_id' else 'undeclared_function')
    else:
        assert sdk.execute(build_prompt, sdk, {'material': 'rows'}) == 'Assess rows'
        record = json.loads(next((tmp_path/'logs').glob('function_*.json')).read_text())
        assert record['status'] == 'completed' and record['rubric_id'] is None
    assert sdk.results == []


def bundle(tmp_path, code):
    v, e = tmp_path/'verifier', tmp_path/'evidence_bundle'
    v.mkdir(); (v/'rubrics').mkdir(); (v/'rubrics/__init__.py').touch()
    e.mkdir(); (e/'initial').mkdir(); (e/'final').mkdir()
    (e/'initial/value.txt').write_text('original')
    for filename in ('environment.json', 'tools.json', 'execution.json', 'calls.json', 'answer.txt'):
        (e/filename).write_text('{}')
    request = {'task_id': 'x', 'task_text': 'check data', 'initial_state': str(e/'initial'),
               'environment': str(e/'environment.json'), 'tools': str(e/'tools.json'),
               'actual': {'final_state': str(e/'final'), 'answer': str(e/'answer.txt'),
                          'calls': str(e/'calls.json'), 'execution': str(e/'execution.json')}, 'reference': {}}
    (e/'program_request.json').write_text(json.dumps(request)); (e/'fallback_input.json').write_text('{}')
    (v/'rubrics.json').write_text(json.dumps({'rubrics': [rubric()]}))
    (v/'rubrics/R1.py').write_text(code)
    (v/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    return context.sdk.aggregate([context.sdk.execute(check_R1, context, {})])\n')
    return v, e


def test_worker_error_invokes_whole_task_fallback_without_partial_score(tmp_path, monkeypatch):
    v, e = bundle(tmp_path, 'def check_R1(context, inputs):\n    raise context.error("mock", "cannot verify", "R1")\n')
    called = []
    def fallback(**kwargs):
        called.append(kwargs)
        return {'outcome': 'pass', 'summary': 'whole task'}
    monkeypatch.setattr(runner, 'verify_execution', fallback)
    value = runner.apply(v, e, tmp_path/'output', {'program_timeout_seconds': 5})
    assert value['mode'] == 'fallback_codex' and value['score'] is None
    assert value['verdict'] == {'outcome': 'pass', 'summary': 'whole task'} and len(called) == 1
    assert called[0]['run_dir'] == tmp_path/'output/fallback'


def test_unexecuted_rubric_cannot_be_forged_by_main(tmp_path, monkeypatch):
    v, e = bundle(tmp_path, 'def check_R1(context, inputs):\n    return {}\n')
    (v/'main.py').write_text('def run(context):\n    return {"score":1,"rubrics":[{"rubric_id":"R1","score":1,"reason":"claimed","evidence":[{"path":".","locator":"directory","finding":"claimed"}]}]}\n')
    calls = []
    def fallback(**kwargs):
        calls.append(kwargs)
        return {'outcome': 'pass', 'summary': 'whole task'}
    monkeypatch.setattr(runner, 'verify_execution', fallback)
    value = runner.apply(v, e, tmp_path/'output', {'program_timeout_seconds': 10})
    assert value['mode'] == 'fallback_codex' and value['score'] is None
    assert len(calls) == 1


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
def test_real_sandbox_evidence_is_readonly_and_main_score_recomputed(tmp_path):
    code = '''def check_R1(context, inputs):
    try:
        (context.initial_state / 'value.txt').write_text('changed')
    except OSError:
        pass
    else:
        raise context.error('writable_evidence', 'evidence was writable')
    (context.scratch_dir / 'computed.txt').write_text('scratch works')
    return {'rubric_id':'R1','score':0,'reason':'zero is valid','evidence':[{'path':'.','locator':'directory','finding':'observed'}]}
'''
    v, e = bundle(tmp_path, code)
    (v/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    result=context.sdk.execute(check_R1, context, {})\n    return {"score":1,"rubrics":[result]}\n')
    value = runner.apply(v, e, tmp_path/'output', {'program_timeout_seconds': 10})
    assert value['mode'] == 'program', value
    assert value['score'] == 0
    assert (e/'initial/value.txt').read_text() == 'original'
    assert (tmp_path/'output/scratch/computed.txt').is_file()


def test_helper_data_passed_to_rubric_and_helper_failure_logged(tmp_path):
    sdk = SDK({'rubrics': [rubric()], 'helpers':[{'file':'helpers/locate.py','name':'locate','inputs':{},'returns':{}}]}, tmp_path/'logs', tmp_path/'scratch')
    def locate(context, inputs):
        return {'artifact': inputs['name']}
    def check_R1(context, inputs):
        assert inputs == {'artifact': 'result.csv'}
        return result()
    locate.__code__ = locate.__code__.replace(co_filename=str(tmp_path/'helpers/locate.py'))
    check_R1.__code__ = check_R1.__code__.replace(co_filename=str(tmp_path/'rubrics/R1.py'))
    from types import SimpleNamespace
    context = SimpleNamespace(root=tmp_path, scratch_dir=tmp_path/'scratch')
    data = sdk.execute(locate, context, {'name': 'result.csv'})
    assert sdk.aggregate([sdk.execute(check_R1, context, data)])['score'] == 1
    rows = [json.loads(p.read_text()) for p in (tmp_path/'logs').glob('function*.json')]
    assert any(r['rubric_id'] is None and r['status'] == 'completed' for r in rows)


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
def test_timeout_kills_detached_child_and_preserves_fallback_failure(tmp_path, monkeypatch):
    code = '''import subprocess, sys, time
def check_R1(context, inputs):
    subprocess.Popen([sys.executable, '-c', "import time,pathlib;time.sleep(3);pathlib.Path('survived').write_text('bad')"], cwd=context.scratch_dir, start_new_session=True)
    time.sleep(20)
'''
    v, e = bundle(tmp_path, code)
    def unavailable(**kwargs):
        raise RuntimeError('fallback unavailable')
    monkeypatch.setattr(runner, 'verify_execution', unavailable)
    value = runner.apply(v, e, tmp_path/'output', {'program_timeout_seconds': 2})
    assert value['status'] == 'verification_error' and value['score'] is None
    assert value['fallback_reason']['code'] == 'timeout'
    assert value['fallback_error']['message'] == 'fallback unavailable'
    time.sleep(3)
    assert not (tmp_path/'output/scratch/survived').exists()
    rows = value['cost']['functions']
    assert rows and rows[0]['status'] == 'abandoned'


def test_design_rejects_incomplete_levels_and_undeclared_model_contracts():
    bad = rubric(scale='ordinal_5')
    del bad['criteria']['5']
    with pytest.raises(RubricVerificationError, match='each score level'):
        validate_design({'rubrics': [bad]})
    for item in ({'file':'../escape.py','method':'llm','prompt':'p','output_contract':{}},
                 {'file':'model_calls/check.py','method':'static','prompt':'p','output_contract':{}},
                 {'file':'model_calls/check.py','method':'llm','prompt':'','output_contract':{}}):
        with pytest.raises(RubricVerificationError):
            validate_design({'rubrics': [rubric()], 'model_calls': [item]})


def test_evidence_paths_allow_scratch_but_reject_missing_and_escape(tmp_path):
    from task_gen.test_verifier.program_verifier.contracts import validate_evidence
    root, scratch = tmp_path/'readonly', tmp_path/'scratch'
    root.mkdir(); scratch.mkdir(); (scratch/'computed.txt').write_text('computed')
    row = result()
    for path in ('scratch/computed.txt', str(scratch/'computed.txt'), str(root)):
        row['evidence'][0]['path'] = path
        validate_evidence([row], root, scratch)
    for path in ('missing.txt', str(tmp_path)):
        row['evidence'][0]['path'] = path
        with pytest.raises(RubricVerificationError, match='missing or outside'):
            validate_evidence([row], root, scratch)
    row['evidence'] = []
    with pytest.raises(RubricVerificationError, match='nonempty'):
        aggregate_results([rubric()], [row])


def test_failed_codex_call_keeps_default_model_and_usage(tmp_path, monkeypatch):
    import task_gen.task_eval_verifier as old
    auth = tmp_path/'auth'; auth.mkdir(); (auth/'config.toml').write_text('model="configured-model"\n')
    class FailedClient:
        def __init__(self, **kwargs):
            assert kwargs['model'] == 'configured-model'
            self.last_log_directory = kwargs['log_directory']/'run_01'
        def run(self, prompt, **kwargs):
            self.last_log_directory.mkdir(parents=True)
            (self.last_log_directory/'stdout.log').write_text('{"type":"turn.completed","usage":{"input_tokens":12}}\n')
            raise RuntimeError('transport failed')
    monkeypatch.setattr(old, '_VerifierClient', FailedClient)
    sdk = SDK({'rubrics':[rubric()], 'model_calls':[{'file':'model_calls/c.py','method':'codex','prompt':'p','output_contract':{}}]}, tmp_path/'logs', tmp_path/'scratch', {'isolated_codex_home':str(auth)})
    with pytest.raises(RuntimeError, match='transport failed'):
        sdk.call_codex('inspect', call_file='model_calls/c.py', function_name='locate')
    row = json.loads(next((tmp_path/'logs').glob('model_*.json')).read_text())
    assert row['model'] == 'configured-model' and row['usage'] == [{'input_tokens':12}]
    assert row['status'] == 'error'


def test_fallback_failure_retains_usage(tmp_path, monkeypatch):
    v, e = bundle(tmp_path, 'def check_R1(context, inputs):\n    raise context.error("mock", "cannot verify")\n')
    def fallback(**kwargs):
        logs = kwargs['run_dir']/'logs'; logs.mkdir(parents=True)
        (logs/'usage_01.json').write_text(json.dumps({'model':'mock','usage_events':[{'type':'turn.completed','usage':{'input_tokens':10}}]}))
        raise RuntimeError('fallback failed after consuming tokens')
    monkeypatch.setattr(runner, 'verify_execution', fallback)
    value = runner.apply(v, e, tmp_path/'output', {'program_timeout_seconds':10})
    assert value['status'] == 'verification_error'
    assert value['cost']['fallback']['usage'] == 'recorded'
    assert value['cost']['fallback']['calls'][0]['usage_events'][0]['usage']['input_tokens'] == 10


def test_execute_rejects_mismatched_function_file_or_rubric_id(tmp_path):
    sdk = SDK({'rubrics':[rubric()]}, tmp_path/'logs', tmp_path/'scratch')
    def check_R1(context, inputs):
        return result()
    with pytest.raises(RubricVerificationError, match='generated contract'):
        sdk.execute(check_R1, None, {})
    sdk = SDK({'rubrics':[rubric()]}, tmp_path/'logs2', tmp_path/'scratch2')
    check_R1.__code__ = check_R1.__code__.replace(co_filename=str(tmp_path/'rubrics/R1.py'))
    with pytest.raises(RubricVerificationError, match='declared for R1'):
        sdk.execute(check_R1, None, {}, rubric_id='R2')


def test_output_nested_in_evidence_rejected_before_creation(tmp_path):
    v, e = bundle(tmp_path, 'def check_R1(context, inputs):\n    pass\n')
    before = {str(p.relative_to(e)): p.read_bytes() for p in e.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='output must be separate'):
        runner.apply(v, e, e/'nested/application', {})
    assert not (e/'nested').exists()
    assert before == {str(p.relative_to(e)): p.read_bytes() for p in e.rglob('*') if p.is_file()}


def test_context_resolves_directory_evidence_without_requiring_a_file(tmp_path):
    root, scratch = tmp_path/'evidence', tmp_path/'scratch'
    root.mkdir(); scratch.mkdir(); (root/'answer.txt').write_text('answer')
    context = make_context({'task_id':'x', 'task_text':'x', 'initial_state':str(root),
                            'actual':{}, 'reference':{}}, root, scratch, None)
    assert context.resolve_evidence_path('.') == root
    assert context.resolve_evidence_path('answer.txt') == root/'answer.txt'
    assert context.resolve_evidence_path('scratch') == scratch
    for path in ('missing.txt', '..'):
        with pytest.raises(RubricVerificationError, match='missing or outside'):
            context.resolve_evidence_path(path)


def test_isolated_provider_uses_local_compaction(tmp_path):
    import tomllib
    home, isolated = tmp_path/'home', tmp_path/'isolated'
    home.mkdir(); isolated.mkdir()
    (home/'config.toml').write_text('model_provider="custom"\n[model_providers.custom]\nname="custom"\nbase_url="http://localhost/v1"\n[model_providers.custom.capabilities]\nexternal_web_access=false\nremote_compaction="v2"\n')
    runner._isolated_auth({'codex':{'codex_home':str(home)}}, isolated)
    settings = tomllib.loads((isolated/'config.toml').read_text())
    caps = settings['model_providers']['custom']['capabilities']
    assert caps == {'external_web_access':False, 'remote_compaction':'unsupported'}
