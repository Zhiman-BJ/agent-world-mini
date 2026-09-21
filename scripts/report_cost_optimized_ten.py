"""Read-only measurement of paired runs; no model calls or estimated missing usage."""
from collections import defaultdict
import json
from pathlib import Path
from run_cost_optimized_ten import ROOT, OUT, NAMES

OLD = ROOT.parent.parent / 'runs/semiconductor_160_20260919_step3_514331a'
RECOVERED = json.loads((ROOT / 'reports/cost_optimized_ten_usage_recovery.json').read_text())


def measure(run):
    meta = json.loads((run / 'run.json').read_text())
    stages = defaultdict(lambda: dict(input=0, cached=0, output=0, calls=0, missing_usage=0,
                                     cache_unknown=0, failed_calls=0, recovered_usage=0, seconds=0))
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
                if not u and row.get('call_id') in RECOVERED:
                    u = RECOVERED[row['call_id']]['usage']
                    stage['recovered_usage'] += 1
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
        'created_at': meta.get('created_at'), 'completed_at': meta.get('completed_at'),
        'tasks': len(tasks), 'step3_attempted': len(results),
        'step3_accepted': sum(t['execution']['success'] for t in results),
        'stages': dict(stages), 'stage_seconds': meta.get('stage_timings_seconds', {}),
        'total': {k: sum(s[k] for s in stages.values()) for k in
                  ('input', 'cached', 'output', 'missing_usage', 'cache_unknown', 'recovered_usage')},
        'task_texts': [{'id': t['task_id'], 'text': t['task_text'],
                        'calls': len(t['reference']['tool_calls'])} for t in tasks]}


def main():
    for recovery in RECOVERED.values():
        with (ROOT / recovery['source']).open() as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        usage = next(e['usage'] for e in events if e.get('type') == recovery['event'])
        assert all(usage[k] == v for k, v in recovery['usage'].items()), '恢复用量与原始事件不一致'
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
    lines += ['', '## 阶段与缓存明细', '',
              '时间为 run.json 的阶段墙钟时间；LLM 调用时长相加会重叠，不当作运行耗时。未知缓存量计为 0 是统计缺口，不代表实际没有命中。', '',
              '| 环境 | 版本 | 阶段 | 输入 | 已知缓存输入 | 输出 | 阶段秒数 | 缺失usage调用 |',
              '|---|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        for label in ('old', 'new'):
            run = r[label]
            if not run:
                continue
            for stage, s in run['stages'].items():
                seconds = run['stage_seconds'].get(stage)
                lines.append(f"| {r['environment']} | {label} | {stage} | {s['input']:,} | {s['cached']:,} | {s['output']:,} | {seconds if seconds is not None else '进行中'} | {s['missing_usage']} |")
    complete = [r for r in rows if r['new'] and r['new']['status'] == 'completed']
    lines += ['', '## 已完成环境的汇总', '']
    for label in ('old', 'new'):
        runs = [r[label] for r in complete]
        totals = {k: sum(r['total'][k] for r in runs) for k in ('input', 'cached', 'output', 'missing_usage', 'cache_unknown')}
        count = sum(r['tasks'] for r in runs)
        lines += [f"- {label}：{len(runs)} 个环境，{count} 条最终任务；输入 {totals['input']:,}，已知缓存输入 {totals['cached']:,}，输出 {totals['output']:,}；缺失usage {totals['missing_usage']} 次，缓存字段缺失 {totals['cache_unknown']} 次。",
                  f"  以每百万 token 的未缓存输入价 P、缓存输入价 C、输出价 O 计，已记录费用为 {(totals['input']-totals['cached'])/1e6:.6f}P + {totals['cached']/1e6:.6f}C + {totals['output']/1e6:.6f}O。"]
    lines += ['', 'DRC task20 首次空正文的用量已由原始 turn.completed 恢复（3008836 输入、2896000 缓存输入、6709 输出）；后续重试只计增量，未重复累计。恢复依据见 reports/cost_optimized_ten_usage_recovery.json。', '', '代理服务未提供可核实的 gpt-5.6-sol 计费映射，本报告不将别的型号官网价格当作实际扣费。缺失usage及未知缓存会影响上述估算；旧新版产量不同，应同时比较每条最终任务成本。']
    (OUT / 'cost_comparison.md').write_text('\n'.join(lines)+'\n')
    texts = ['# 新版任务文本', '']
    for r in rows:
        for t in (r['new'] or {}).get('task_texts', []):
            texts += [f"## {r['environment']} / {t['id']}", '', t['text'], '', f"实际调用数：{t['calls']}", '']
    (OUT / 'task_texts.md').write_text('\n'.join(texts))
    print([(r['environment'], (r['new'] or {}).get('status')) for r in rows])


if __name__ == '__main__':
    main()
