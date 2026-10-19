import json
from task_gen.test_verifier.program_verifier.data import load_selected, prepare_evidence


def test_prepared_candidate_is_separate_from_reference_and_uses_full_trace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    initial=tmp_path/'initial';initial.mkdir();(initial/'x.csv').write_text('x\n1\n')
    final=tmp_path/'final';final.mkdir();(final/'x.csv').write_text('x\n2\n')
    source=tmp_path/'source';source.mkdir()
    task={'task_id':'x','task_text':'改变数字','reference':{'answer':'完成','tool_calls':[]}}
    full=[{'index':1,'tool':'foo','arguments':{},'result':{'value':2}}]
    (source/'step2_task_solution.jsonl').write_text(json.dumps({'item':{'task_id':'x','solution_trace':full}})+'\n')
    case=SimpleNamespace(task=task,environment={'tools':[]},initial_state=initial,reference_state=final,reference_calls=[],source_run=source)
    out=prepare_evidence(case,tmp_path/'bundle')
    req=json.loads((out/'program_request.json').read_text())
    assert req['actual']['final_state'] != req['reference']['final_state']
    assert json.loads((out/req['actual']['calls']).read_text())['result']['value']==2
    assert (out/'fallback_input.json').exists()
    fallback=json.loads((out/'fallback_input.json').read_text())
    assert fallback['actual_state']=='evidence/actual/final'
    assert fallback['initial_state']=='evidence/initial'
