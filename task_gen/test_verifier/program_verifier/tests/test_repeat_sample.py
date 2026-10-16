from task_gen.test_verifier.program_verifier.repeat_sample import api_totals, slots


def test_two_independent_rounds_keep_sample_ids():
    selected = [{'case':f'P{i:02d}', 'case_name':f'env__task{i}'} for i in range(1,21)]
    rows = slots(selected)
    assert len(rows) == len({r['slot'] for r in rows}) == 40
    assert rows[0]['slot'] == 'P01_R1' and rows[20]['slot'] == 'P01_R2'
    assert [r['case_name'] for r in rows[:20]] == [r['case_name'] for r in rows[20:]]


def test_cost_counts_cached_input_once_and_reports_missing():
    rows = [{'phase':'generation','usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':60}},'status':'completed','seconds':2},
            {'phase':'application','usage':{'prompt_tokens':20,'completion_tokens':5,'prompt_tokens_details':{'cached_tokens':0}},'status':'completed','seconds':3}]
    prices = {'currency':'USD','input_per_million':2,'cached_input_per_million':.5,'output_per_million':8}
    total = api_totals(rows, prices)
    assert total['known_input_tokens'] == 120 and total['known_cached_input_tokens'] == 60
    assert abs(total['estimated_cost'] - .00027) < 1e-12
    assert total['usage_complete'] and total['api_seconds_sum'] == 5
    assert api_totals(rows, None)['estimated_cost'] is None
    missing = api_totals(rows+[{'phase':'generation','usage':None,'status':'error','seconds':1}], prices)
    assert not missing['usage_complete'] and missing['estimated_cost'] is None
    assert missing['known_estimated_cost'] == total['estimated_cost']


def test_cancelled_request_is_separate_from_api_failure_and_keeps_unknown_usage():
    cancelled = {'status':'error','client_disconnected':True,'usage':None,'seconds':2}
    totals = api_totals([cancelled])
    assert totals['client_cancellations']==1 and totals['api_errors']==0
    assert not totals['usage_complete']


def test_unattributed_transport_error_is_not_reported_as_provider_error():
    totals=api_totals([{'status':'error','client_disconnected':False,'usage':None,
                       'error':'BrokenPipeError: [Errno 32] Broken pipe','seconds':2}])
    assert totals['api_errors']==0 and totals['transport_errors']==1
    assert totals['client_cancellations']==0 and not totals['usage_complete']


def test_report_keeps_failed_attempt_cost_and_separates_score_from_completion(tmp_path):
    from task_gen.test_verifier.program_verifier.metering import save
    from task_gen.test_verifier.program_verifier.repeat_sample import report, read
    save(tmp_path/'sampling.json',{'selected':[{'case':'P01','case_name':'env__task','task_id':'task'}]})
    for folder in ('attempt_records','attempts','api'):(tmp_path/folder).mkdir()
    for number, bad in [(1,True),(2,False)]:
        attempt=f'P01_R1_a{number:02d}'
        (tmp_path/'attempts'/attempt).mkdir();(tmp_path/'api'/attempt).mkdir()
        save(tmp_path/'attempt_records'/(attempt+'.json'),{'slot':'P01_R1','attempt':attempt,'status':'finished','seconds':12,'started_at':'2026-10-16T00:00:00+00:00',**({'reused_verifier_from':'P01_R1_a01'} if number==2 else {})})
        save(tmp_path/'attempts'/attempt/'row.json',{'generation':{'status':'generated','seconds':8},'seconds':12,'application':{'mode':'fallback_codex' if bad else 'program','status':'verification_error' if bad else 'completed','seconds':3,'score':None if bad else .8,**({'fallback_reason':{'message':'interface error'},'fallback_error':{'message':'all retries invalid'}} if bad else {})}})
        save(tmp_path/'api'/attempt/'a.json',{'phase':'generation','status':'completed','usage':{'input_tokens':100,'output_tokens':10}})
    stats=report(tmp_path)
    rows=read(tmp_path/'summary.json')['rows']
    assert stats['finished']==1 and stats['program_completed']==1 and stats['reference_full_score']==0
    assert rows[0]['attempts']==2 and rows[0]['total_seconds']==24 and rows[0]['api']['known_input_tokens']==200
    assert rows[0]['cumulative_generation_seconds']==16 and rows[0]['cumulative_application_seconds']==6
    assert rows[0]['issues'][0]['error']['message']=='interface error'
    assert 'all retries invalid' in rows[0]['issues'][1]['error']['message']
    assert '应用复验' in rows[0]['issues'][2]['error']
    assert rows[1]['status']=='pending'


def test_report_shows_fallback_verdict_and_partial_score_reason(tmp_path):
    import csv
    from task_gen.test_verifier.program_verifier.metering import save
    from task_gen.test_verifier.program_verifier.repeat_sample import report
    save(tmp_path/'sampling.json',{'selected':[{'case':'P01','case_name':'env__task','task_id':'task'}]})
    (tmp_path/'attempt_records').mkdir()
    for repeat, app in [(1, {'mode':'program','status':'completed','score':.5,
                             'rubrics':[{'rubric_id':'R1','score':0,'reason':'required output absent'}]}),
                        (2, {'mode':'fallback_codex','status':'completed','score':None,'outcome':'pass'})]:
        attempt=f'P01_R{repeat}_a01'
        (tmp_path/'attempts'/attempt).mkdir(parents=True)
        save(tmp_path/'attempt_records'/(attempt+'.json'),{'slot':f'P01_R{repeat}','attempt':attempt,
             'status':'finished','seconds':10,'started_at':'2026-10-16T00:00:00+00:00',
             'diagnosis':'设计缺少可区分缺项的判断字段',
             'reused_partial_from':'P01_R1_previous','repair_note':'仅补全缺失函数'})
        save(tmp_path/'attempts'/attempt/'row.json',{'generation':{'status':'generated','seconds':5},
             'seconds':10,'design':{'rubrics':[{'id':'R1','scale':'binary'}]},'application':dict(app,seconds=5)})
    report(tmp_path)
    with (tmp_path/'results.csv').open() as f: rows=list(csv.DictReader(f))
    assert 'required output absent' in rows[0]['问题']
    assert '设计缺少可区分缺项的判断字段' in rows[0]['问题']
    assert '补全生成' in rows[0]['问题'] and '仅补全缺失函数' in rows[0]['问题']
    assert rows[1]['回退判定']=='pass'
