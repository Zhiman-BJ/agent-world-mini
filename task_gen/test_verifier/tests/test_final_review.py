import json
from pathlib import Path
import shutil

import pytest

from task_gen.test_verifier.program_verifier.tests.test_generation import design


def program_bundle(tmp_path):
    root = tmp_path/'run/verifier'
    (root/'rubrics').mkdir(parents=True)
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'model_calls_design.json').write_text('[]')
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    (root/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    test = tmp_path/'self_test.py'
    test.write_text('assert True\n')
    return root, evidence, test


def test_final_check_accepts_zero_and_disables_fallback(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    seen = []
    def apply(verifier, source, output, config):
        seen.append(config)
        output.mkdir()
        return {'mode':'program','status':'completed','score':0,'rubrics':[]}
    monkeypatch.setattr(runner, 'apply', apply)
    report = final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert report['score'] == 0
    assert seen[0]['fallback_enabled'] is False


def test_final_check_rejects_native_error_and_preserves_report(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    def apply(*args):
        return {'mode':'program','status':'verification_error','score':None,'error':'broken interface'}
    monkeypatch.setattr(runner, 'apply', apply)
    with pytest.raises(ValueError, match='broken interface'):
        final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert json.loads((tmp_path/'native_result.json').read_text())['status'] == 'verification_error'


def test_successful_self_test_exit_cannot_skip_native_run(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    test.write_text('raise SystemExit(0)\n')
    def apply(verifier, source, output, config):
        output.mkdir()
        (output/'executed').touch()
        return {'mode':'program','status':'completed','score':0}
    monkeypatch.setattr(runner, 'apply', apply)
    value = final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert value['score'] == 0 and (tmp_path/'native/executed').is_file()


def test_final_session_can_change_main_and_rubric_together(tmp_path):
    from task_gen.test_verifier.final_review import run_final_review
    root, evidence, _ = program_bundle(tmp_path)
    def model(prompt, *, stage, output_dir):
        assert stage == 'final'
        assert '检查数据' in prompt
        workspace = tmp_path/'final_workspace'
        shutil.copytree(root, workspace)
        (workspace/'main.py').write_text('def run(context):\n    return {"fixed": True}\n')
        doc = json.loads((workspace/'rubrics.json').read_text())
        doc['rubrics'][0]['criteria']['1'] = '符合原任务的明确条件'
        (workspace/'rubrics.json').write_text(json.dumps(doc))
        return workspace
    report = run_final_review('program', evidence, root.parent, {}, model_call=model)
    assert 'fixed' in (root/'main.py').read_text()
    assert json.loads((root/'rubrics.json').read_text())['rubrics'][0]['criteria']['1'] == '符合原任务的明确条件'
    assert report['status'] == 'completed'
    assert report['after'] != report['before']
    assert (root.parent/'generation/before_final/main.py').read_text() == 'def run(context):\n    return {}\n'


def test_failed_final_session_never_replaces_deliverable(tmp_path):
    from task_gen.test_verifier.final_review import run_final_review
    root, evidence, _ = program_bundle(tmp_path)
    original = (root/'main.py').read_bytes()
    def bad(*args, **kwargs):
        raise RuntimeError('budget exhausted')
    with pytest.raises(RuntimeError, match='budget exhausted'):
        run_final_review('program', evidence, root.parent, {}, model_call=bad)
    assert (root/'main.py').read_bytes() == original
    assert json.loads((root.parent/'generation/final_result.json').read_text())['status'] == 'error'


@pytest.mark.parametrize('broken', [False, True])
def test_final_model_replays_complete_native_run_in_sandbox(tmp_path, monkeypatch, broken):
    from task_gen.test_verifier.final_review import run_final_review
    from task_gen.test_verifier.program_verifier.tests.test_runtime import bundle
    from task_gen.test_verifier.program_verifier.generation import model
    code = ('def check_R1(context, inputs):\n'
            '    return {"rubric_id":"R1","score":0,"reason":"zero is valid",'
            '"evidence":[{"path":".","locator":"directory","finding":"checked"}]}\n')
    root, evidence = bundle(tmp_path, code)
    (evidence/'request.json').write_bytes((evidence/'program_request.json').read_bytes())
    executable = tmp_path/'node/lib/codex/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.touch()
    real_which = shutil.which
    monkeypatch.setattr(model.shutil, 'which', lambda name: str(executable) if name == 'codex' else real_which(name))
    monkeypatch.setattr(model, '_isolated_auth', lambda *args: {})
    class Client:
        def __init__(self, **kwargs):
            pass
        def run_until_files(self, prompt, *, working_directory, required_paths):
            workspace = working_directory/'scratch/verifier'
            (workspace/'main.py').write_text((root/'main.py').read_text()+'\n# final repair\n')
            if broken:
                (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    raise RuntimeError("still broken")\n')
            (working_directory/'scratch/self_test.py').write_text('assert True\n')
            return 'Ready for independent host test.'
    monkeypatch.setattr(model, '_VerifierClient', Client)
    config = {'program_timeout_seconds':5, 'final_review_timeout_seconds':100}
    if broken:
        with pytest.raises(RuntimeError, match='still broken'):
            run_final_review('program', evidence, root.parent, config)
        assert 'final repair' not in (root/'main.py').read_text()
    else:
        value = run_final_review('program', evidence, root.parent, config)
        assert value['status'] == 'completed'
        assert 'final repair' in (root/'main.py').read_text()
        native = list((root.parent/'generation/01_final/scratch/native_runs').glob('*/result.json'))
        assert len(native) == 1 and json.loads(native[0].read_text())['score'] == 0


def test_pipeline_final_session_can_complete_failed_implementation(tmp_path):
    from task_gen.test_verifier.program_verifier.generation.pipeline import generate
    calls = []
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = tmp_path/f'workspace_{len(calls)}'
        if stage == 'design':
            workspace.mkdir()
            (workspace/'rubrics.json').write_text(json.dumps(design()))
            (workspace/'main.py').write_text('def run(context):\n    return {}\n')
        elif stage == 'rubrics/R1.py':
            raise RuntimeError('design interface contradiction')
        else:
            assert stage == 'final'
            shutil.copytree(output_dir/'verifier', workspace)
            (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
        return workspace
    value = generate(evidence, tmp_path/'run', model_call=model)
    assert value['status'] == 'generated'
    assert calls == ['design','rubrics/R1.py','final']
    assert json.loads((tmp_path/'run/generation/staged_result.json').read_text())['status'] == 'generation_failed'
