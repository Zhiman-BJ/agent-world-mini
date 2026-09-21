"""Summarize the approved-prompt trial from saved evidence, without model calls."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from report_cost_optimized_ten import measure


def recover_usage(call, sessions, cache):
    sid = call.get('agent_log', {}).get('session_id')
    if not sid:
        return None
    if sid not in cache:
        files = list(sessions.glob(f'*/*/*/*{sid}*.jsonl'))
        points = []
        for path in files:
            for line in path.open():
                try:
                    row = json.loads(line)
                    payload = row.get('payload', {})
                    if row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                        usage = (payload.get('info') or {}).get('total_token_usage')
                        if usage:
                            points.append((datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')), usage))
                except (ValueError, KeyError):
                    continue
        cache[sid] = (files, sorted(points, key=lambda p: p[0]))
    files, points = cache[sid]
    start = datetime.fromisoformat(call['started_at'])
    end = start + timedelta(seconds=call.get('duration_seconds', 0))
    before = [u for t, u in points if t < start]
    inside = [(t, u) for t, u in points if start <= t <= end]
    if not inside:
        return None
    previous = before[-1] if before else {}
    timestamp, final = inside[-1]
    usage = {k: v - previous.get(k, 0) for k, v in final.items()
             if isinstance(v, (int, float)) and v >= previous.get(k, 0)}
    return {'usage': usage, 'sources': [str(p) for p in files], 'last_event_at': timestamp.isoformat(),
            'scope': 'observed cumulative delta inside call interval; unfinished response may be unmetered'}


def summarize(batch_file):
    batch = json.loads(batch_file.read_text())
    root = Path(batch['output_root'])
    rows, texts = [], ['# 十个新环境任务文本', '']
    recovered, cache = {}, {}
    sessions = Path('~/.codex-task-eval/sessions').expanduser()
    for name in batch['environments']:
        paths = sorted((root / name).glob('*/run.json'))
        if not paths:
            rows.append({'environment': name, 'status': 'pending'})
            continue
        run = paths[-1].parent
        row = {'environment': name, **measure(run)}
        phases = defaultdict(lambda: dict(calls=0, input=0, cached=0, output=0, errors=0))
        errors = []
        log = run / 'llm_calls.jsonl'
        if log.exists():
            for line in log.open():
                try:
                    call = json.loads(line)
                except ValueError:
                    continue
                if call.get('error'):
                    errors.append({'step': call.get('step'), 'call_id': call.get('call_id'), 'error': call['error']})
                usage = call.get('usage') or {}
                if not usage and call.get('backend') == 'codex-execution':
                    recovery = recover_usage(call, sessions, cache)
                    if recovery:
                        recovered[call['call_id']] = recovery
                        usage = recovery['usage']
                        stage = row['stages'][call['step']]
                        for key, field in [('input', 'input_tokens'), ('cached', 'cached_input_tokens'), ('output', 'output_tokens')]:
                            amount = usage.get(field, 0)
                            stage[key] += amount
                            row['total'][key] += amount
                        stage['missing_usage'] -= 1
                        stage['recovered_usage'] += 1
                        row['total']['missing_usage'] -= 1
                        row['total']['recovered_usage'] += 1
                if call.get('step') != 'step_3_chain_execute':
                    continue
                phase = phases[call.get('agent_log', {}).get('phase', 'unknown')]
                phase['calls'] += 1
                phase['input'] += usage.get('input_tokens', 0)
                phase['cached'] += usage.get('cached_input_tokens', 0)
                phase['output'] += usage.get('output_tokens', 0)
                phase['errors'] += bool(call.get('error'))
        row['step3_phases'] = dict(phases)
        row['llm_errors'] = errors
        details = []
        for path in sorted((run / 'tasks').glob('*/agent_result.json')):
            task = json.loads(path.read_text())
            ex = task['execution']
            attempts = ex.get('attempts', [])
            drafts, reviews = [], []
            for p in sorted(path.parent.glob('rounds/*/attempt_*_draft.json')):
                drafts.append(json.loads(p.read_text()))
            for p in sorted(path.parent.glob('rounds/*/attempt_*_review.json')):
                reviews.append(json.loads(p.read_text()))
            details.append({'task': task['task_id'], 'success': ex['success'], 'error': ex.get('error'),
                'raw_calls': len(ex['raw_tool_calls']), 'selected_calls': len(ex['tool_calls']),
                'call_indices': ex.get('call_indices'), 'chain_changes': ex.get('chain_changes'),
                'attempts': len(attempts), 'drafts': len(drafts), 'reviews': len(reviews),
                'draft_completed': [d['completed'] for d in drafts],
                'review_completed': [r['completed'] for r in reviews]})
        row['step3_details'] = details
        rejected = run / 'rejected.json'
        row['step5_rejections'] = [
            {'task': t['task_id'], 'errors': t.get('validation', {}).get('errors', [])}
            for t in json.loads(rejected.read_text()) if t.get('execution', {}).get('success')
        ] if rejected.exists() else []
        row['review_recovered'] = sum(any(not d and r for d, r in zip(t['draft_completed'], t['review_completed'])) for t in details)
        row['review_rejected'] = sum(any(d and not r for d, r in zip(t['draft_completed'], t['review_completed'])) for t in details)
        rows.append(row)
        for t in row['task_texts']:
            texts.extend([f"## {name} / {t['id']}", '', t['text'], '', f"最终确认调用数：{t['calls']}", ''])
    root.mkdir(parents=True, exist_ok=True)
    (root / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    (root / 'usage_recovery.json').write_text(json.dumps(recovered, ensure_ascii=False, indent=2))
    (root / 'task_texts.md').write_text('\n'.join(texts) + '\n')
    lines = ['# 审核版十个新环境测试', '',
        '从建图运行至Step5；不运行独立评测。环境不同，不能与旧十环境视为配对消融。输入包含缓存；超时用量从同一会话调用时间区间内的token_count累计差恢复，来源见usage_recovery.json。未完成响应仍可能未计量，恢复值不是服务商账单。', '',
        '| 环境 | 状态 | Step3尝试/成功 | 最终任务 | 输入M | 输出M | 复核挽回/拒绝 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        if 'total' not in r:
            lines.append(f"| {r['environment']} | pending | — | — | — | — | — |")
            continue
        lines.append(f"| {r['environment']} | {r['status']} | {r['step3_attempted']}/{r['step3_accepted']} | {r['tasks']} | {r['total']['input']/1e6:.3f} | {r['total']['output']/1e6:.3f} | {r['review_recovered']}/{r['review_rejected']} |")
    lines += ['', '## Step3调用阶段成本', '', '| 环境 | 阶段 | 调用 | 输入 | 缓存输入 | 输出 | 错误 |', '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        for phase, u in r.get('step3_phases', {}).items():
            lines.append(f"| {r['environment']} | {phase} | {u['calls']} | {u['input']} | {u['cached']} | {u['output']} | {u['errors']} |")
    lines += ['', '## 失败候选与调用错误', '']
    for r in rows:
        lines += [f"### {r['environment']}", '']
        if r.get('error'):
            lines += [str(r['error']), '']
        for t in r.get('step3_details', []):
            if not t['success']:
                lines += [f"- {t['task']}：{t['error']}"]
        for e in r.get('llm_errors', []):
            lines += [f"- 模型调用 {e['step']} / {e['call_id']}：{e['error']}"]
        for t in r.get('step5_rejections', []):
            lines += [f"- Step5 {t['task']}：" + '；'.join(t['errors'])]
        lines.append('')
    (root / 'summary.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'states': dict(Counter(r['status'] for r in rows)),
        'tasks': sum(r.get('tasks', 0) for r in rows),
        'input': sum(r.get('total', {}).get('input', 0) for r in rows)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('batch_file', type=Path)
    summarize(parser.parse_args().batch_file)
