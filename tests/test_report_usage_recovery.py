import json
from pathlib import Path


def test_usage_recovery_excludes_other_calls(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from report_approved_prompt_ten import recover_usage

    folder = tmp_path / '2026' / '09' / '22'
    folder.mkdir(parents=True)
    records = [
        {'timestamp': f'2026-09-22T00:00:{second:02d}+00:00',
         'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
             'total_token_usage': {'input_tokens': tokens}}}}
        for second, tokens in [(0, 100), (5, 130), (10, 180), (15, 240)]
    ]
    (folder / 'rollout-session-example.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in records))
    call = {'agent_log': {'session_id': 'session-example'},
            'started_at': '2026-09-22T00:00:01+00:00', 'duration_seconds': 10}
    result = recover_usage(call, tmp_path, {})
    assert result['usage']['input_tokens'] == 80
    call['started_at'] = '2026-09-22T00:01:00+00:00'
    assert recover_usage(call, tmp_path, {}) is None
