import importlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def design(root):
    root.mkdir(parents=True)
    rubric = dict(id='R1', goal='Return value.', task_quotes=['Return value.'], weight=1,
                  scale='binary', criteria={'0':'Missing or wrong value.', '1':'Correct value.'},
                  method='codex', inputs=['actual answer'], static_tools=[], required_tools=[],
                  codex_work='Compare value with source.', evidence_plan='Read answer and source.',
                  missing_policy='Missing answer scores zero.', error_policy='Unreliable inspection is an error.',
                  method_reason='No prebuilt tools in this method.')
    for name, value in {'rubrics.json':{'rubrics':[rubric]}, 'tools.json':{'tools':[]},
                        'coverage.json':{'requirements':[{'task_quote':'Return value.', 'rubric_ids':['R1']}]} }.items():
        (root/name).write_text(json.dumps(value))
    (root/'instructions.md').write_text('Independently inspect the source and answer; follow fixed criteria.')


@pytest.mark.parametrize('method', ['codex_rubric_verifier', 'direct_codex_rubric'])
def test_each_method_generates_from_task_without_source_verifier(tmp_path, method):
    generation = importlib.import_module('task_gen.test_verifier.'+method+'.generation')
    evidence = tmp_path/'evidence'; evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    calls = []
    def model_call(prompt, *, stage, output_dir):
        calls.append(stage)
        assert 'Return value.' in prompt
        if method == 'direct_codex_rubric':
            assert 'task_id、outcome、summary 和 rubrics 都是必需字段' in prompt
            assert '最终仅输出 {"outcome"' not in prompt
        else:
            assert '最终仅输出 {"outcome"' in prompt
        workspace = tmp_path/stage
        design(workspace)
        (workspace/'README.md').write_text('Fixed criteria and application contract.')
        if stage == 'final':
            assert not (output_dir/'frozen_manifest.json').exists()
            assert (output_dir/'generation/staged_result.json').is_file()
            (workspace/'instructions.md').write_text('Reviewed fixed criteria; inspect answer independently.')
        return workspace
    output = tmp_path/'output'
    result = generation.generate(evidence, output, {}, model_call=model_call)
    assert result['status'] == 'generated', result
    assert result['generation_source'] == 'original_task'
    assert result['method'] == method and result['tool_count'] == 0
    assert (output/'verifier/README.md').is_file()
    assert calls == ['design', 'final']
    assert result['final_review']['status'] == 'completed'
    from task_gen.test_verifier.program_verifier.generation.model import snapshot
    assert json.loads((output/'frozen_manifest.json').read_text()) == snapshot(output/'verifier')
    assert 'Reviewed' in (output/'verifier/instructions.md').read_text()
    assert 'Independently' in (output/'generation/before_final/instructions.md').read_text()
    original = (output/'verifier/rubrics.json').read_bytes()
    provenance = (output/'generation/result.json').read_bytes()
    again = generation.generate(evidence, output, {}, model_call=lambda *a, **k:pytest.fail('must not overwrite'))
    assert again['status'] == 'generation_failed'
    assert (output/'verifier/rubrics.json').read_bytes() == original
    assert (output/'generation/result.json').read_bytes() == provenance


@pytest.mark.parametrize('method', ['codex_rubric_verifier', 'direct_codex_rubric'])
@pytest.mark.parametrize('design_fails', [False, True])
def test_final_review_failure_never_freezes_and_can_recover_design(tmp_path, method, design_fails):
    generation = importlib.import_module('task_gen.test_verifier.'+method+'.generation')
    evidence = tmp_path/'evidence'; evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    calls = []
    def model_call(prompt, *, stage, output_dir):
        calls.append(stage)
        if stage == 'design' and design_fails:
            raise TimeoutError('designer timed out')
        if stage == 'final' and not design_fails:
            raise RuntimeError('review failed')
        workspace = tmp_path/stage
        design(workspace)
        return workspace
    output = tmp_path/'output'
    result = generation.generate(evidence, output, {}, model_call=model_call)
    assert calls == ['design', 'final']
    if design_fails:
        assert result['status'] == 'generated', result
        assert json.loads((output/'generation/staged_result.json').read_text())['stage'] == 'design'
    else:
        assert result['status'] == 'generation_failed' and result['stage'] == 'final_review'
        assert not (output/'frozen_manifest.json').exists()
        assert json.loads((output/'generation/final_result.json').read_text())['status'] == 'error'


@pytest.mark.parametrize('method', ['codex_rubric_verifier', 'direct_codex_rubric'])
def test_production_review_uses_a_separate_model_session(tmp_path, monkeypatch, method):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.codex_rubric_verifier import generation
    evidence = tmp_path/'evidence'; evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    sessions = []
    class Designer:
        def __init__(self, *args):
            sessions.append(self)
        def __call__(self, prompt, *, stage, output_dir):
            assert stage == 'design'
            workspace = tmp_path/'design'; design(workspace)
            return workspace
    class Reviewer:
        def __init__(self, evidence_dir, config, logs, final_method):
            assert final_method == method
            sessions.append(self)
        def __call__(self, prompt, *, stage, output_dir):
            assert stage == 'final'
            workspace = tmp_path/'final'
            shutil.copytree(output_dir/'verifier', workspace)
            return workspace
    monkeypatch.setattr(generation, 'GenerationModel', Designer)
    monkeypatch.setattr(final_review, 'FinalReviewModel', Reviewer)
    result = generation.generate_for(method, evidence, tmp_path/'output')
    assert result['status'] == 'generated', result
    assert len(sessions) == 2 and sessions[0] is not sessions[1]


def test_rubric_only_contract_rejects_static_tool_or_static_method(tmp_path):
    from task_gen.test_verifier.codex_rubric_verifier.generation import validate_package
    root = tmp_path/'verifier'; design(root)
    document = json.loads((root/'rubrics.json').read_text())
    document['rubrics'][0]['method'] = 'static'
    (root/'rubrics.json').write_text(json.dumps(document))
    with pytest.raises(ValueError, match='codex'):
        validate_package(root, 'Return value.')


@pytest.mark.parametrize('method,mode', [('codex_rubric_verifier','codex_rubric_only'),
                                       ('direct_codex_rubric','direct_codex_rubric')])
def test_batch_dispatches_independent_generator_once_then_two_applications(tmp_path, monkeypatch, method, mode):
    from task_gen.test_verifier.final_review_experiment import execute
    generation = importlib.import_module('task_gen.test_verifier.'+method+'.generation')
    application = importlib.import_module('task_gen.test_verifier.'+method+'.application')
    calls = []
    def generate(evidence, output, config):
        calls.append('generate')
        design(output/'verifier')
        return {'status':'generated', 'generation_source':'original_task'}
    def apply(verifier, evidence, output, config):
        calls.append(output.name); output.mkdir()
        return dict(status='completed', mode=mode, score=1, rubrics=[{'rubric_id':'R1','score':1}])
    monkeypatch.setattr(generation, 'generate', generate)
    monkeypatch.setattr(application, 'apply', apply)
    result = execute(method, tmp_path/'evidence', tmp_path/'output', {})
    assert calls == ['generate','run_01','run_02']
    assert result['both_native'] and result['same_rubric_scores']


def test_batch_rejects_unknown_method(tmp_path):
    from task_gen.test_verifier.final_review_experiment import execute
    with pytest.raises(ValueError, match='Unknown method'):
        execute('typo', tmp_path, tmp_path/'out', {})


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
@pytest.mark.parametrize('stage', ['design', 'final'])
def test_generation_sandbox_hides_other_runs_but_reopens_own_evidence_and_call(tmp_path, stage):
    from task_gen.task_eval_verifier import sandbox_command
    from task_gen.test_verifier.codex_rubric_verifier.generation import GenerationModel
    source = tmp_path/'repo/task_gen/test_verifier'
    other = source/'codex_tool_verifier/runs/old/verifier/rubrics.json'
    other.parent.mkdir(parents=True); other.write_text('secret old rubric')
    legacy = source/'runs/old/rubrics.json'
    legacy.parent.mkdir(parents=True); legacy.write_text('legacy rubric')
    own = source/'direct_codex_rubric/runs/current'
    evidence = own/'evidence/P01'; evidence.mkdir(parents=True)
    (evidence/'request.json').write_text('original task')
    call = own/'direct_codex_rubric/P01/generation/01_design'; call.mkdir(parents=True)
    (call/'evidence').mkdir(); (call/'scratch').mkdir()
    (call/'request.json').write_text('request'); (call/'prompt.txt').write_text('prompt')
    auth = tmp_path/'auth'; auth.mkdir()
    repo = source.parents[1]
    prefix = list(sandbox_command(call, auth, runtime_paths=[repo]))
    if stage == 'final':
        from task_gen.test_verifier.final_review import FinalReviewModel
        model = FinalReviewModel(evidence, {}, call, 'direct_codex_rubric')
    else:
        model = GenerationModel(evidence, {}, call)
    prefix = model.isolate_prefix(prefix, repo, call)
    script = f'''from pathlib import Path
assert not Path({str(other)!r}).exists()
assert not Path({str(legacy)!r}).exists()
assert Path({str(evidence/'request.json')!r}).read_text() == 'original task'
for name in ({str(evidence/'request.json')!r}, 'request.json', 'prompt.txt'):
    try: Path(name).write_text('changed')
    except OSError: pass
    else: raise AssertionError('input writable')
Path('scratch/check.txt').write_text('ok')
'''
    result = subprocess.run([*prefix, '/usr/bin/python3', '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (call/'scratch/check.txt').read_text() == 'ok'


@pytest.mark.parametrize('method,mode', [('codex_rubric_verifier','codex_rubric_only'),
                                       ('direct_codex_rubric','direct_codex_rubric')])
def test_report_counts_each_rubric_runtime_as_native(tmp_path, method, mode):
    from task_gen.test_verifier.final_review_report import write_report
    from task_gen.test_verifier.program_verifier.generation.pipeline import write_json
    write_json(tmp_path/'selection.json', {'methods':[method], 'selected':[{'sample_id':'P01','task_id':'t'}]})
    write_json(tmp_path/method/'P01/row.json', dict(status='finished', generation={'status':'generated'},
               applications=[dict(status='completed',mode=mode,score=1,rubrics=[]) for _ in range(2)],
               both_native=True,same_total_score=True,same_rubric_scores=True))
    stats = write_report(tmp_path)[method]
    assert stats['native_completed'] == stats['full_score'] == 2
