import json


def test_one_generation_two_applications_share_frozen_package(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review_experiment as batch
    from task_gen.test_verifier.program_verifier.generation import pipeline
    from task_gen.test_verifier.program_verifier.application import runner
    (tmp_path/'verifier').mkdir()
    (tmp_path/'verifier/main.py').write_text('original')
    calls = []
    def generate(evidence, output, config):
        calls.append('generate')
        return {'status':'generated','seconds':1}
    def apply(verifier, evidence, output, config):
        calls.append(output.name)
        output.mkdir(parents=True)
        assert (verifier/'main.py').read_text() == 'original'
        return {'mode':'program','status':'completed','score':0.5,'seconds':2,
                'rubrics':[{'rubric_id':'R1','score':1}]}
    monkeypatch.setattr(pipeline, 'generate', generate)
    monkeypatch.setattr(runner, 'apply', apply)
    result = batch.execute('program', tmp_path/'source', tmp_path, {})
    assert calls == ['generate','run_01','run_02']
    assert len(result['applications']) == 2
    assert result['same_rubric_scores'] is True
    assert json.loads((tmp_path/'frozen_manifest.json').read_text())['main.py']


def test_modified_package_is_not_applied_again(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review_experiment as batch
    from task_gen.test_verifier.program_verifier.generation import pipeline
    from task_gen.test_verifier.program_verifier.application import runner
    root = tmp_path/'verifier'
    root.mkdir(); (root/'main.py').write_text('original')
    monkeypatch.setattr(pipeline, 'generate', lambda *a: {'status':'generated','seconds':0})
    def apply(verifier, evidence, output, config):
        output.mkdir(parents=True)
        (verifier/'main.py').write_text('changed')
        return {'mode':'program','status':'completed','score':1,'seconds':0,'rubrics':[]}
    monkeypatch.setattr(runner, 'apply', apply)
    value = batch.execute('program', tmp_path/'source', tmp_path, {})
    assert value['status'] == 'error'
    assert 'changed' in value['error']
    assert not (tmp_path/'applications/run_02').exists()
