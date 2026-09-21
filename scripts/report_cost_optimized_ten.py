"""Read-only measurement of paired runs; no model calls or estimated missing usage."""
from collections import defaultdict
import json
from pathlib import Path
from run_cost_optimized_ten import ROOT, OUT, NAMES

OLD = ROOT.parent.parent / 'runs/semiconductor_160_20260919_step3_514331a'


def measure(run):
    meta = json.loads((run / 'run.json').read_text())
    stages = defaultdict(lambda: dict(input=0, cached=0, output=0, calls=0, missing_usage=0,
                                     cache_unknown=0, failed_calls=0, seconds=0))
    log = run / 'llm_calls.jsonl'
    if log.exists():
        with log.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                stage = stages[row.get('step', 'unknown')]
                u = row.get('usage') or {}
                inp = u.get('input_tokens', u.get('prompt_tokens'))
                out = u.get('output_tokens', u.get('completion_tokens'))
                cache = u.get('cached_input_tokens', (u.get('prompt_tokens_details') or {}).get('cached_tokens'))
                stage['calls'] += 1
                stage['failed_calls'] += row.get('error') is not None or row.get('status') == 'failed'
                stage['seconds'] += row.get('duration_seconds') or 0
                stage['missing_usage'] += inp is None or out is None
                stage['cache_unknown'] += inp is not None and cache is None
                stage['input'] += inp or 0
                stage['cached'] += cache or 0
                stage['output'] += out or 0
    tasks = json.loads((run / 'tasks.json').read_text()) if (run / 'tasks.json').exists() else []
    results = [json.loads(p.read_text()) for p in (run / 'tasks').glob('*/agent_result.json')]
    return {'run': str(run), 'status': meta['status'], 'error': meta.get('error'),
        'tasks': len(tasks), 'step3_attempted': len(results),
        'step3_accepted': sum(t['execution']['success'] for t in results),
        'stages': dict(stages), 'stage_seconds': meta.get('stage_timings_seconds', {}),
        'total': {k: sum(s[k] for s in stages.values()) for k in
                  ('input', 'cached', 'output', 'missing_usage', 'cache_unknown')},
        'task_texts': [{'id': t['task_id'], 'text': t['task_text'],
                        'calls': len(t['reference']['tool_calls'])} for t in tasks]}


def main():
    rows = []
    for name in NAMES:
        row = {'environment': name}
        for label, root in [('old', OLD), ('new', OUT)]:
            paths = sorted((root / name).glob('*/run.json'))
            row[label] = measure(paths[-1].parent) if paths else None
        rows.append(row)
    (OUT / 'cost_comparison.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    lines = ['# 十环境成本对照', '', '仅计已记录usage；缺失不是零。缓存输入包含在总输入内。旧版为全候选执行，新版为缺口补跑；目标、链路和最终产量可能不同。', '',
             '| 环境 | 新状态 | 旧/新任务数 | 旧/新Step3候选数 | 旧/新输入token | 新缺失usage调用 |',
             '|---|---|---:|---:|---:|---:|']
    for r in rows:
        o, n = r['old'], r['new']
        if n:
            lines.append(f"| {r['environment']} | {n['status']} | {o['tasks']}/{n['tasks']} | {o['step3_attempted']}/{n['step3_attempted']} | {o['total']['input']:,}/{n['total']['input']:,} | {n['total']['missing_usage']} |")
    (OUT / 'cost_comparison.md').write_text('\n'.join(lines)+'\n')
    texts = ['# 新版任务文本', '']
    for r in rows:
        for t in (r['new'] or {}).get('task_texts', []):
            texts += [f"## {r['environment']} / {t['id']}", '', t['text'], '', f"实际调用数：{t['calls']}", '']
    (OUT / 'task_texts.md').write_text('\n'.join(texts))
    print([(r['environment'], (r['new'] or {}).get('status')) for r in rows])


if __name__ == '__main__':
    main()
