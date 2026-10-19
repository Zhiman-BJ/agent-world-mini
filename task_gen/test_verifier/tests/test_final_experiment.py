import json
import pytest


def test_parallel_worker_cannot_take_a_completed_or_unclaimed_job(tmp_path):
    from task_gen.test_verifier.final_review_experiment import run_job
    from task_gen.test_verifier.program_verifier.generation.pipeline import write_json
    item = {'sample_id':'P01'}
    root = tmp_path/'program/P01'
    write_json(root/'row.json', {'status':'finished'})
    with pytest.raises(ValueError, match='claim'):
        run_job('program', item, tmp_path/'evidence', tmp_path, {}, 'revision', claim_token='token')
    write_json(root/'row.json', {'status':'running','claim_token':'someone_else'})
    with pytest.raises(ValueError, match='claim'):
        run_job('program', item, tmp_path/'evidence', tmp_path, {}, 'revision', claim_token='token')
    assert run_job('program', item, tmp_path/'evidence', tmp_path, {}, 'revision')['claim_token'] == 'someone_else'


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
    assert stats['generation_failed'] == 0
    assert stats['application_errors'] == 0
    assert '费用 unknown' in (tmp_path/'RESULTS.html').read_text()


def test_report_does_not_count_failed_or_fallback_results_as_native_accuracy(tmp_path):
    from task_gen.test_verifier.final_review_report import write_report
    from task_gen.test_verifier.program_verifier.generation.pipeline import write_json
    write_json(tmp_path/'selection.json', {'methods':['program'], 'selected':[
        {'sample_id':case,'task_id':case} for case in ('P01','P02')]})
    write_json(tmp_path/'program/P01/row.json', {'status':'generation_failed',
               'generation':{'status':'generation_failed','seconds':2},'applications':[]})
    write_json(tmp_path/'program/P02/row.json', {'status':'finished',
               'generation':{'status':'generated','seconds':3}, 'applications':[
                   {'mode':'fallback_codex','status':'completed','outcome':'pass','seconds':1},
                   {'mode':'fallback_codex','status':'verification_error','seconds':2}],
               'both_native':False})
    stats = write_report(tmp_path)['program']
    assert stats['generation_failed'] == 1
    assert stats['application_errors'] == 1
    assert stats['native_completed'] == stats['full_score'] == stats['both_native_pairs'] == 0


def test_report_shows_verdict_and_score_without_inventing_missing_values(tmp_path):
    from task_gen.test_verifier.final_review_report import write_report
    from task_gen.test_verifier.program_verifier.generation.pipeline import write_json
    write_json(tmp_path/'selection.json', {'methods':['direct_codex_rubric'],
               'selected':[{'sample_id':'P01','task_id':'task'}]})
    write_json(tmp_path/'direct_codex_rubric/P01/row.json', {
        'status':'finished','generation':{'status':'generated'},'both_native':False,
        'applications':[
            {'mode':'direct_codex_rubric','status':'completed','outcome':'pass','score':0.86},
            {'mode':'direct_codex_rubric','status':'completed','score':0.98},
            {'mode':'fallback_codex','status':'completed','outcome':'fail','score':None},
            {'mode':'fallback_codex','status':'verification_error','score':None}]})
    write_report(tmp_path)
    report = (tmp_path/'RESULTS.html').read_text()
    assert '<th>PASS/FAIL</th><th>分数（0–1）</th>' in report
    assert '<td>PASS</td><td>0.86</td>' in report
    assert '<td>未记录</td><td>0.98</td>' in report
    assert '<td>FAIL</td><td>未评分</td>' in report
    assert '<td>执行错误</td><td>未评分</td>' in report
