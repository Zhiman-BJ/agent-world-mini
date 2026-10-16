"""Keep numeric API usage before the private Codex home is removed."""
import json
from pathlib import Path


def events(path):
    if not path.is_file():
        return
    with path.open(errors='replace') as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def collect_usage(logs, codex_home):
    threads, completed = set(), []
    for event in events(Path(logs)/'stdout.log'):
        if event.get('type') == 'thread.started':
            threads.add(event.get('thread_id'))
        if event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
            completed.append(event['usage'])
    if completed:
        totals = {}
        for item in completed:
            for key, value in item.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
        return {'usage': totals, 'source': 'turn.completed', 'turn_completed': True}
    totals = None
    for path in Path(codex_home).glob('sessions/**/*.jsonl'):
        if not any(thread and path.stem.endswith(thread) for thread in threads):
            continue
        for event in events(path):
            payload = event.get('payload', {})
            if event.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                current = (payload.get('info') or {}).get('total_token_usage')
                if isinstance(current, dict):
                    totals = {k:v for k,v in current.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
    return {'usage': totals, 'source': 'session_token_count' if totals is not None else 'unknown',
            'turn_completed': False}
