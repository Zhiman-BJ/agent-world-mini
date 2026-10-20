"""Run the existing whole-task Codex verifier once on the fixed program sample."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import time
import tomllib

from task_gen.task_eval_verifier import verify_execution
from task_gen.test_verifier.program_verifier.metering import MeteringRelay, now, save
from task_gen.test_verifier.program_verifier.repeat_sample import api_totals, metered_config, read


def evidence_path(source, item):
    return source / 'attempts' / f"{item['case']}_R1_a01" / 'source_evidence/fallback_input.json'


def preflight(source):
    selected = read(source / 'sampling.json')['selected']
    assert len(selected) == 20
    assert len({item['case_name'] for item in selected}) == 20
    assert {item['case'] for item in selected} == {f'P{i:02d}' for i in range(1, 21)}
    for item in selected:
        evidence = read(evidence_path(source, item))
        assert evidence['task']['task_id'] == item['task_id']
        assert evidence['task']['task_text'] == item['task_text']
        for key in ('initial_state', 'actual_state', 'reference_state', 'reference_initial_state'):
            if evidence.get(key) is not None:
                assert Path(evidence[key]).is_dir(), (item['case'], key)
    return selected


def run_case(source, batch, item, config, upstream):
    started = time.monotonic()
    path = evidence_path(source, item)
    row = dict(case=item['case'], task_id=item['task_id'], environment=item['environment'],
               status='running', started_at=now(), outcome=None,
               model=config['codex']['model'], reasoning_effort=config['codex']['reasoning_effort'],
               evidence_source=str(path), evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    output = batch / 'cases' / item['case']
    record = batch / 'records' / (item['case'] + '.json')
    save(record, row)
    evidence = read(path)
    for key in ('initial_state', 'actual_state', 'reference_state', 'reference_initial_state'):
        if evidence.get(key) is not None:
            evidence[key] = Path(evidence[key])
    try:
        with MeteringRelay(upstream, batch / 'api' / item['case'], lambda: 'direct_codex') as relay:
            with metered_config(config, relay) as cfg:
                verdict = verify_execution(run_dir=output, config=dict(cfg['codex'], method='codex'), **evidence)
        row.update(status='completed', outcome=verdict['outcome'], summary=verdict['summary'],
                   failure_causes=verdict['failure_causes'])
    except Exception as error:
        row.update(status='verification_error', error=f'{type(error).__name__}: {error}')
    row.update(finished_at=now(), seconds=time.monotonic() - started,
               attempts=read(output / 'logs/attempts.json', []))
    save(record, row)
    print(item['case'], row['status'], row['outcome'], round(row['seconds'], 1), flush=True)
    return row


def report(batch):
    rows, all_calls = [], []
    for item in read(batch / 'sampling.json')['selected']:
        row = read(batch / 'records' / (item['case'] + '.json'),
                   dict(case=item['case'], task_id=item['task_id'], status='pending', outcome=None))
        calls = [read(p) for p in sorted((batch / 'api' / item['case']).glob('*.json'))]
        row['api'] = api_totals(calls)
        row['request_settings_valid'] = all(c['model'] == 'gpt-6.1-sol' and c['reasoning_effort'] == 'high' for c in calls)
        rows.append(row)
        all_calls.extend(calls)
    stats = {key: sum(r.get('outcome') == key for r in rows) for key in ('pass', 'fail')}
    stats.update(finished=sum(r['status'] in ('completed', 'verification_error') for r in rows),
                 verification_error=sum(r['status'] == 'verification_error' for r in rows), planned=len(rows))
    summary = dict(updated_at=now(), statistics=stats, api=api_totals(all_calls), rows=rows,
                   request_settings_valid=all(r['request_settings_valid'] for r in rows))
    save(batch / 'summary.json', summary)
    columns = ['任务', 'task_id', '状态', '判定', '总耗时秒', '模型调查秒', '提交次数', '输入token',
               '其中缓存token', '输出token', '用量已知/请求', 'API错误', '传输错误', '取消请求', '金额', '问题/结论']
    table = []
    for r in rows:
        a = r['api']
        attempts = r.get('attempts', [])
        notes = [x['error'] for x in attempts if x.get('error')]
        notes.append(r.get('error') or r.get('summary', ''))
        table.append([r['case'], r['task_id'], r['status'], r.get('outcome') or '—',
                      round(r['seconds'], 2) if 'seconds' in r else '—',
                      round(sum(x['seconds'] for x in attempts), 2), len(attempts),
                      a['known_input_tokens'], a['known_cached_input_tokens'], a['known_output_tokens'],
                      f"{a['requests_with_usage']}/{a['requests']}", a['api_errors'], a['transport_errors'],
                      a['client_cancellations'], json.dumps(a['known_provider_cost']) if a['known_provider_cost'] else '未知',
                      '\n'.join(notes)])
    with (batch / 'results.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(table)
    body = []
    for row in table:
        cells = ''.join('<td>' + escape(str(v)) + '</td>' for v in row[:-1])
        case = row[0]
        body.append('<tr>' + cells + '<td><details><summary>查看</summary><pre>' + escape(row[-1]) +
                    f'</pre><a href="cases/{case}/verdict.json">原始判定</a> · '
                    f'<a href="cases/{case}/scratch/investigation.md">调查记录</a></details></td></tr>')
    html = '<!doctype html><meta charset="utf-8"><title>直接 Codex 验证 · 20任务</title>'
    html += '<style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}th{position:sticky;top:0;background:#eee}pre{white-space:pre-wrap;min-width:500px;max-width:900px}td{vertical-align:top}</style>'
    html += '<h1>原始 Codex verifier · 20任务 × 1轮</h1><p>gpt-6.1-sol / high；复用上一批相同候选终态。直接调用原验证入口，最多3次提交重试，每次3600秒。</p>'
    html += '<p>总耗时含证据准备与排队；模型调查秒包含内部重试。输入包含缓存。token仅统计已返回用量的请求；无供应商价格，金额未知。FAIL是任务未通过，verification_error是验证器未完成。</p>'
    html += '<p>' + escape(json.dumps(stats, ensure_ascii=False)) + '</p><a href="results.csv">下载 CSV</a><table><thead><tr>'
    html += ''.join('<th>' + escape(c) + '</th>' for c in columns) + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table>'
    (batch / 'RESULTS.html').write_text(html)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    source, batch = args.source.resolve(), args.output.resolve()
    if args.report_only:
        report(batch)
        return
    selected = preflight(source)
    config = read(source / 'run_config.json')
    assert config['codex']['model'] == 'gpt-6.1-sol'
    assert config['codex']['reasoning_effort'] == 'high'
    if args.check_only:
        print('20 unique cases, exact task texts, evidence paths and model settings verified')
        return
    batch.mkdir(parents=True, exist_ok=False)
    for folder in ('cases', 'records', 'api'):
        (batch / folder).mkdir()
    for name in ('sampling.json', 'run_config.json', 'task_ids.tsv'):
        shutil.copyfile(source / name, batch / name)
    shutil.copyfile(__file__, batch / 'runner_snapshot.py')
    shutil.copyfile(Path(__file__).parents[1] / 'task_eval_verifier.py', batch / 'verifier_snapshot.py')
    started = time.monotonic()
    status = dict(started_at=now(), source=str(source), workers=args.workers, mode='direct_codex')
    save(batch / 'run_status.json', status)
    home = Path(config['codex'].get('codex_home', Path.home() / '.codex'))
    settings = tomllib.loads((home / 'config.toml').read_text())
    upstream = settings['model_providers'][settings['model_provider']]['base_url']
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, source, batch, item, config, upstream) for item in selected]
        for future in as_completed(futures):
            future.result()
            report(batch)
    summary = report(batch)
    assert summary['statistics']['finished'] == 20
    assert summary['request_settings_valid'], 'Unexpected model or effort in API requests'
    status.update(finished_at=now(), seconds=time.monotonic() - started, status='finished')
    save(batch / 'run_status.json', status)


if __name__ == '__main__':
    main()
