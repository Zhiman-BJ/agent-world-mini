"""Exercise the original API against the new design/application boundary."""
import copy
import json
from pathlib import Path

import pytest

from eval import verifier as base
from task_gen.test_verifier.direct_codex_rubric import application, generation
from task_gen.test_verifier.program_verifier.generation.model import snapshot
from task_gen.test_verifier.tests.test_independent_rubric_generation import design


@pytest.fixture
def inputs(tmp_path):
    initial = tmp_path / 'initial'
    actual = tmp_path / 'actual'
    initial.mkdir(); actual.mkdir()
    (initial/'value.json').write_text('{"value":8}')
    (actual/'value.json').write_text('{"value":9}')
    home = tmp_path/'home'; home.mkdir()
    (home/'config.toml').write_text('model="test"')
    return dict(task={'task_id':'test', 'task_text':'Return value.', 'reference_answer':'secret'},
                environment={'tools':[]}, initial_state=initial, actual_state=actual,
                answer='candidate-secret', calls=[{'tool':'candidate-secret'}], execution={}), {
                    'codex_home':str(home), 'model':'test', 'attempts':1, 'timeout_seconds':15,
                    'submission_preflight':True}


def install_designer(monkeypatch, tmp_path):
    calls = []
    original = generation.generate

    def generate(evidence, output, config):
        request = json.loads((evidence/'request.json').read_text())
        assert request['task_text'] == 'Return value.' and request['reference'] is None
        assert 'secret' not in json.dumps(request)
        assert (evidence/'evidence/actual/answer.txt').read_text() == ''
        assert (evidence/'evidence/actual/tool_calls.jsonl').read_text() == ''
        assert (evidence/'evidence/actual/final/value.json').read_text() == '{"value":8}'
        assert not (evidence/'evidence/reference/final').exists()
        assert config['codex']['model'] == 'test'

        def model_call(prompt, *, stage, output_dir):
            calls.append(stage)
            workspace = tmp_path/f'designed_{stage}'
            design(workspace)
            return workspace
        return original(evidence, output, config, model_call=model_call)

    monkeypatch.setattr(generation, 'generate', generate)
    return calls


def model_response(root, *, outcome='pass', score=1):
    (root/'scratch/investigation.md').write_text('R1: read source and independently rechecked answer.')
    ref = {'path':'evidence/actual/answer.txt', 'locator':'line 1', 'finding':'Observed answer.'}
    return dict(task_id='test', outcome=outcome, summary='Checked source and answer.',
                requirements=[dict(id='R1', task_quote='Return value.', requirement='Return value.',
                                   analysis='Compared to original data.', evidence=[ref], passed=outcome=='pass')],
                non_blocking_issues=[], failure_causes=['Incorrect value.'] if outcome=='fail' else [],
                rubrics=[dict(rubric_id='R1', score=score, reason='Fixed criterion checked.', evidence=[ref])])


@pytest.mark.parametrize('outcome,score', [('pass', 0), ('fail', 1)])
def test_original_api_keeps_legacy_shape_without_score_threshold(inputs, tmp_path, monkeypatch, outcome, score):
    evidence, config = inputs
    calls = install_designer(monkeypatch, tmp_path)
    def run(client, prompt, *, working_directory):
        assert '原 verifier API 兼容输出' in prompt
        assert '提交前格式自检' in prompt
        assert 'task_quote 必须直接从 request.json' not in prompt  # no legacy-only preflight
        import subprocess
        protected = working_directory/'direct/verifier_source.json'
        probe = subprocess.run([*client.command_prefix, '/usr/bin/python3', '-c',
            f"from pathlib import Path; Path({str(protected)!r}).write_text('changed')"], capture_output=True)
        assert probe.returncode != 0
        return json.dumps(model_response(working_directory, outcome=outcome, score=score))
    monkeypatch.setattr(base._VerifierClient, 'run', run)
    root = tmp_path/'audit'
    result = base.verify_execution(run_dir=root, config=config, **evidence)
    assert set(result) == {'task_id','requirements','non_blocking_issues','summary','failure_causes','outcome'}
    assert result['outcome'] == outcome
    assert result == json.loads((root/'verdict.json').read_text())
    assert json.loads((root/'direct_result.json').read_text())['score'] == score
    assert (root/'logs/attempts.json').is_file() and (root/'scratch/investigation.md').is_file()
    assert calls == ['design', 'final']
    frozen = root/'direct/generation/verifier'
    before = snapshot(frozen)
    # Reuse the same frozen criteria without another model design call.
    monkeypatch.setattr(generation, 'generate', lambda *a, **k: pytest.fail('unexpected redesign'))
    again = base.verify_execution(run_dir=tmp_path/'again', config=dict(config, verifier_dir=str(frozen)), **evidence)
    assert again == result and snapshot(frozen) == before


def test_generation_error_is_not_candidate_fail(inputs, tmp_path, monkeypatch):
    evidence, config = inputs
    monkeypatch.setattr(generation, 'generate', lambda *a, **k: {'status':'generation_failed','error':'API unavailable'})
    with pytest.raises(base.VerificationError, match='API unavailable'):
        base.verify_execution(run_dir=tmp_path/'failed', config=config, **evidence)
    assert not (tmp_path/'failed/verdict.json').exists()


@pytest.mark.parametrize('changed', ['task', 'package'])
def test_reuse_rejects_different_task_and_modified_frozen_package(inputs, tmp_path, monkeypatch, changed):
    evidence, config = inputs
    install_designer(monkeypatch, tmp_path)
    monkeypatch.setattr(base._VerifierClient, 'run', lambda client, prompt, *, working_directory:
                        json.dumps(model_response(working_directory)))
    root = tmp_path/'first'
    base.verify_execution(run_dir=root, config=config, **evidence)
    frozen = root/'direct/generation/verifier'
    if changed == 'task':
        evidence = copy.deepcopy(evidence)
        evidence['task']['task_text'] = 'Return value. Changed task.'
    else:
        (frozen/'instructions.md').write_text('Changed instructions.')
    with pytest.raises(base.VerificationError, match='match this exact task|changed after generation'):
        base.verify_execution(run_dir=tmp_path/'invalid', config=dict(config, verifier_dir=str(frozen)), **evidence)
    assert not (tmp_path/'invalid/verdict.json').exists()


def test_legacy_codex_remains_explicit_and_errors_do_not_recurse(inputs, tmp_path, monkeypatch):
    evidence, config = inputs
    monkeypatch.setattr(generation, 'generate', lambda *a, **k: pytest.fail('legacy mode must not generate'))
    def run(root, config):
        result = model_response(root)
        result.pop('rubrics')
        return base.validate_verdict(result, root)
    monkeypatch.setattr(base, 'run_verification_agent', run)
    assert base.verify_execution(run_dir=tmp_path/'legacy', config=dict(config, method='codex'), **evidence)['outcome']=='pass'


def test_compatibility_output_validates_real_passed_and_exact_quotes(inputs, tmp_path):
    evidence, _ = inputs
    root = base.prepare_verification_workspace(tmp_path/'validation', **evidence)
    document = {'rubrics':[{'id':'R1','weight':1,'scale':'binary'}]}
    value = model_response(root)
    value['requirements'][0]['passed'] = False
    with pytest.raises(ValueError):
        application.validate_result(value, root, document, legacy_output=True)
    value = model_response(root)
    value['requirements'][0]['task_quote'] = 'Invented requirement'
    with pytest.raises(ValueError, match='exact task excerpt'):
        application.validate_result(value, root, document, legacy_output=True)


def test_existing_frozen_profile_keeps_original_engine(inputs, tmp_path, monkeypatch):
    from task_gen.acceptance_contract import PROFILE, text_sha256
    evidence, config = inputs
    text = evidence['task']['task_text']
    contract = dict(schema_version=1, task_id='test', task_text_sha256=text_sha256(text),
                    requirements=[dict(id='R1', task_quote=text, requirement=text,
                                       source_start=0, source_end=len(text))])
    monkeypatch.setattr(generation, 'generate', lambda *a, **k: pytest.fail('profile must stay frozen'))
    def run(root, config):
        assert json.loads((root/'request.json').read_text())['verification_profile']==PROFILE
        value = model_response(root)
        value.pop('rubrics')
        return base.validate_verdict(value, root)
    monkeypatch.setattr(base, 'run_verification_agent', run)
    config.update(verification_profile=PROFILE, acceptance_contract=contract)
    assert base.verify_execution(run_dir=tmp_path/'strict', config=config, **evidence)['outcome']=='pass'
    with pytest.raises(base.VerificationError, match='require method'):
        base.verify_execution(run_dir=tmp_path/'mixed', config=dict(config, method='direct_codex_rubric'), **evidence)


@pytest.mark.parametrize('entry', ['api', 'prepared'])
def test_both_clis_forward_method_and_frozen_package(inputs, tmp_path, monkeypatch, capsys, entry):
    from eval import existing
    evidence, _ = inputs
    request = tmp_path/'api.json'
    if entry == 'prepared':
        root = base.prepare_verification_workspace(tmp_path/'source', **evidence)
        request = root/'request.json'
        module = existing
    else:
        request.write_text(json.dumps(evidence, default=str))
        module = base
    seen = []
    monkeypatch.setattr(module, 'verify_execution', lambda **kwargs: seen.append(kwargs) or {'outcome':'pass'})
    module.main(['--request', str(request), '--output', str(tmp_path/'output'),
                 '--method', 'direct_codex_rubric', '--verifier-dir', str(tmp_path/'frozen')])
    assert seen[0]['config']['method']=='direct_codex_rubric'
    assert seen[0]['config']['verifier_dir']==str(tmp_path/'frozen')
    assert seen[0]['answer']=='candidate-secret'
