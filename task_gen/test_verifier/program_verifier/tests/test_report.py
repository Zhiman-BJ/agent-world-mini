from task_gen.test_verifier.program_verifier.validate_reference import summarize


def test_statistics_distinguish_generation_failure_fallback_and_nonfull_score():
    rows=[
      {'case':'a','generation':{'status':'generated'},'application':{'mode':'program','status':'completed','score':1,'rubrics':[]}},
      {'case':'b','generation':{'status':'generated'},'application':{'mode':'program','status':'completed','score':.8,'rubrics':[]}},
      {'case':'c','generation':{'status':'generated'},'application':{'mode':'fallback_codex','status':'completed','score':None,'outcome':'pass'}},
      {'case':'d','generation':{'status':'generation_failed','error':'invalid JSON'},'application':None},
    ]
    result=summarize(rows)
    assert result['generated']==3
    assert result['program_completed']==2
    assert result['reference_full_score']==1
    assert result['fallback_count']==1
    assert result['program_started']==3
    assert result['not_started']==1
    assert result['total']==4


def test_evidence_preparation_failure_is_reported_without_retry(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from task_gen.test_verifier.program_verifier import validate_reference as module
    case=SimpleNamespace(task={'task_id':'t'})
    monkeypatch.setattr(module,'load_selected',lambda *args:[('S1',case)])
    calls=[]
    def prepare(*args):
        calls.append(1)
        raise PermissionError('reference is unreadable')
    monkeypatch.setattr(module,'prepare_evidence',prepare)
    module.run_batch(tmp_path,tmp_path/'batch',['S1'],{})
    module.run_batch(tmp_path,tmp_path/'batch',['S1'],{})
    assert calls==[1]
    row=json.loads((tmp_path/'batch/summary.json').read_text())['cases'][0]
    assert row['generation']['stage']=='evidence_preparation'
    assert row['application'] is None
