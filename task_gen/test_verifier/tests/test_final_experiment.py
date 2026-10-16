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


def test_report_separates_generation_and_reused_application_costs(tmp_path):
    from task_gen.test_verifier.final_review_report import write_report
    from task_gen.test_verifier.program_verifier.generation.pipeline import write_json
    write_json(tmp_path/'selection.json', {'methods':['program'],
               'selected':[{'sample_id':'P01','task_id':'task'}]})
    root = tmp_path/'program/P01'
    write_json(root/'row.json', {'status':'finished',
               'generation':{'status':'generated','seconds':12},
               'applications':[{'status':'completed','mode':'program','score':1,
                                'seconds':3,'rubrics':[]} for _ in range(2)],
               'both_native':True,'same_total_score':True,'same_rubric_scores':True})
    write_json(root/'generation/final_result.json', {'seconds':5})
    for i,phase in enumerate(('generation','final_review','application_1','application_2'),1):
        write_json(root/f'api/{i}.json', {'phase':phase,'status':'completed',
                   'usage':{'input_tokens':100*i,'output_tokens':10*i,
                            'input_tokens_details':{'cached_tokens':50*i}}})
    write_report(tmp_path)
    stats = json.loads((tmp_path/'summary.json').read_text())['statistics']['program']
    assert stats['generation_seconds']['sum'] == 12
    assert stats['final_review_seconds']['sum'] == 5
    assert stats['application_seconds']['sum'] == 6
    assert stats['application_seconds']['median'] == 3
    assert stats['generation_api']['known_input_tokens'] == 300
    assert stats['application_api']['known_input_tokens'] == 700
    assert stats['generation_api']['estimated_cost'] is None
    assert '费用 unknown' in (tmp_path/'RESULTS.html').read_text()
