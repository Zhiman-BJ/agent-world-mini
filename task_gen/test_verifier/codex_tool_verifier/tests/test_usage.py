import json

from task_gen.test_verifier.codex_tool_verifier import usage


def test_session_usage_is_cumulative_not_sum_and_filtered_by_thread(tmp_path):
    logs = tmp_path/'logs'
    logs.mkdir()
    (logs/'stdout.log').write_text(json.dumps({'type':'thread.started', 'thread_id':'selected'})+'\n')
    sessions = tmp_path/'auth/sessions'
    sessions.mkdir(parents=True)
    def event(total):
        return {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':total}}}
    first = {'input_tokens':100,'cached_input_tokens':40,'output_tokens':10}
    last = {'input_tokens':250,'cached_input_tokens':100,'output_tokens':30}
    (sessions/'rollout-selected.jsonl').write_text('\n'.join(map(json.dumps,[event(first),event(first),event(last)])))
    (sessions/'rollout-unrelated.jsonl').write_text(json.dumps(event({'input_tokens':9999})))
    result = usage.collect_usage(logs, tmp_path/'auth')
    assert result['usage'] == last
    assert result['source'] == 'session_token_count'
    assert result['turn_completed'] is False


def test_completed_stdout_is_authoritative_and_unknown_is_not_zero(tmp_path):
    logs = tmp_path/'logs'
    logs.mkdir()
    assert usage.collect_usage(logs, tmp_path/'auth')['usage'] is None
    totals = {'input_tokens':250,'cached_input_tokens':100,'output_tokens':30}
    (logs/'stdout.log').write_text(json.dumps({'type':'turn.completed','usage':totals})+'\n')
    result = usage.collect_usage(logs, tmp_path/'auth')
    assert result['usage'] == totals and result['turn_completed']


def test_recorded_calls_never_resume_and_preserve_usage_on_failure(tmp_path, monkeypatch):
    import pytest
    from task_gen.task_eval_verifier import _VerifierClient, CodexAgentClient
    auth = tmp_path/'auth'
    (auth/'sessions').mkdir(parents=True)
    client = _VerifierClient(codex_home=auth, log_directory=tmp_path/'logs', record_usage=True,
                             model='gpt-6.1-sol', reasoning_effort='high')
    def run(instance, *args, **kwargs):
        assert instance.session_id is None
        logs = tmp_path/'logs/run_01'
        logs.mkdir(parents=True)
        instance.last_log_directory = logs
        (logs/'stdout.log').write_text(json.dumps({'type':'thread.started','thread_id':'selected'})+'\n')
        (auth/'sessions/rollout-selected.jsonl').write_text(json.dumps({'type':'event_msg','payload':{
            'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'output_tokens':3}}}}))
        raise TimeoutError('timeout')
    monkeypatch.setattr(CodexAgentClient, '_run_process', run)
    client.session_id = 'must-not-resume'
    with pytest.raises(TimeoutError):
        client._run_process('prompt', working_directory=tmp_path)
    record = json.loads((tmp_path/'logs/run_01/api_usage.json').read_text())
    assert record['usage']['input_tokens'] == 100
    assert record['reasoning_effort'] == 'high' and record['seconds'] >= 0
