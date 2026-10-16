"""Report generation costs once and both applications of each frozen verifier."""
from collections import defaultdict
import html
import json
from pathlib import Path
from statistics import mean, median

from .program_verifier.repeat_sample import api_totals, read
from .program_verifier.generation.pipeline import write_json


def write_report(batch):
    batch = Path(batch)
    manifest = read(batch/'selection.json')
    pricing = read(batch/'pricing.json', {})
    prices = pricing if all(isinstance(pricing.get(k),(int,float)) for k in
                           ('input_per_million','cached_input_per_million','output_per_million')) else None
    rows = []
    statistics = {}
    def timing(values):
        return {'count':len(values),'sum':sum(values) if values else None,
                'mean':mean(values) if values else None,'median':median(values) if values else None}
    for method in manifest['methods']:
        current = []
        method_records = []
        for item in manifest['selected']:
            root = batch/method/item['sample_id']
            row = read(root/'row.json', {'status':'running' if (root/'started.json').exists() else 'pending',
                                       'generation':read(root/'generation/result.json'), 'applications':[]})
            row.update(method=method, case=item['sample_id'], task_id=item['task_id'])
            grouped = defaultdict(list)
            for p in (root/'api').glob('*.json'):
                record = read(p)
                if record:
                    grouped[record['phase']].append(record)
                    method_records.append(record)
            row['api'] = {name:api_totals(values, prices) for name,values in grouped.items()}
            row['generation_api'] = api_totals(grouped['generation']+grouped['final_review'], prices)
            row['final_review'] = read(root/'generation/final_result.json', {})
            row['directory'] = str(root)
            current.append(row)
        native = 'program' if method == 'program' else 'codex_with_tools'
        apps = [a for r in current for a in r.get('applications',[])]
        pairs = [r for r in current if r.get('both_native')]
        statistics[method] = {
            'tasks':len(current), 'finished_tasks':sum(r['status'] not in ('pending','running') for r in current),
            'generated':sum((r.get('generation') or {}).get('status') == 'generated' for r in current),
            'generation_failed':sum(r['status'] == 'generation_failed' for r in current),
            'applications':len(apps), 'native_completed':sum(a.get('mode') == native and a.get('status') == 'completed' for a in apps),
            'application_errors':sum(a.get('status') != 'completed' for a in apps),
            'full_score':sum(a.get('mode') == native and a.get('score') == 1 for a in apps),
            'fallback':sum(a.get('mode') == 'fallback_codex' for a in apps),
            'both_native_pairs':len(pairs), 'same_total_pairs':sum(r['same_total_score'] for r in pairs),
            'same_rubric_pairs':sum(r['same_rubric_scores'] for r in pairs),
            'generation_seconds':timing([r['generation']['seconds'] for r in current
                                        if isinstance((r.get('generation') or {}).get('seconds'),(int,float))]),
            'final_review_seconds':timing([r['final_review']['seconds'] for r in current
                                          if isinstance(r['final_review'].get('seconds'),(int,float))]),
            'application_seconds':timing([a['seconds'] for a in apps if isinstance(a.get('seconds'),(int,float))]),
            'generation_api':api_totals([r for r in method_records if r['phase'] in ('generation','final_review')], prices),
            'application_api':api_totals([r for r in method_records if r['phase'].startswith('application_')], prices)}
        rows.extend(current)
    write_json(batch/'summary.json', {'statistics':statistics,'pricing':pricing,'records':rows})
    esc = lambda v: html.escape(str(v))
    def seconds(v):
        return f'{v:.1f}' if isinstance(v,(int,float)) else '-'
    def score(v):
        return f'{v:.6g}' if isinstance(v,(int,float)) else v
    def api_text(v):
        if not v or not v['requests']:
            return '-'
        tokens = f"{v['known_input_tokens']:,}/{v['known_cached_input_tokens']:,}/{v['known_output_tokens']:,}"
        money = v.get('estimated_cost')
        actual = v.get('known_provider_cost',{})
        cost = '/'.join(f'已知金额 {k} {n:.4f}' for k,n in actual.items()) or (
            f"估算 {v['currency']} {money:.4f}" if money is not None else '费用 unknown')
        errors = f"API错误 {v['api_errors']} / 传输错误 {v['transport_errors']} / 取消 {v['client_cancellations']}"
        return esc(f"{tokens}; {v['requests_with_usage']}/{v['requests']}请求有usage; {cost}; {errors}")
    lines = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>最终 Codex 修复实验</title><style>body{font:14px/1.6 system-ui;margin:24px;color:#222;background:#fff}h1{font-size:24px}h2{font-size:18px;margin-top:28px}table{border-collapse:collapse;width:100%}th,td{padding:8px;border:1px solid #ddd;text-align:left;vertical-align:top}th{background:#f3f4f5;white-space:nowrap}.scroll{overflow:auto}details{max-width:800px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#087b67}</style>',
             '<h1>生成一次，冻结后应用两次</h1>',
             '<p>6.1sol / high。最终修复与验收试跑全部计入生成费用；两次正式应用单独计费。满分是参考通过信号，不等于错误识别准确率。token依次为输入/缓存/输出，缓存是输入子集。</p>',
             '<p>本批使用任务已有的一次运行结果，没有错误答案集，无法计算完整准确率。未满分须对照任务原文判断；生成失败不伪造两次应用，回退通过不计作本方法原生通过。</p>',
             '<p>费用优先展示 API 返回值；未确认供应方价格、缺少usage或价格时保留 unknown，不填零。重复一致性仅在两次本方法均完成的任务对上计算，回退另计。</p>',
             '<h2>关键指标</h2><div class="scroll"><table><tr><th>方法</th><th>完成任务</th><th>生成成功/失败</th><th>本方法完成/应用</th><th>满分/原生完成</th><th>回退/最终应用错误</th><th>总分一致/可比对</th><th>逐项一致/可比对</th></tr>']
    for method,s in statistics.items():
        vals = [method,f"{s['finished_tasks']}/{s['tasks']}",f"{s['generated']}/{s['generation_failed']}",f"{s['native_completed']}/{s['applications']}",
                f"{s['full_score']}/{s['native_completed']}",f"{s['fallback']}/{s['application_errors']}",
                f"{s['same_total_pairs']}/{s['both_native_pairs']}",f"{s['same_rubric_pairs']}/{s['both_native_pairs']}"]
        lines.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in vals)+'</tr>')
    lines += ['</table></div><h2>耗时与 API 汇总</h2>',
              '<p>时间为各任务耗时之和，不是并行批次的墙钟时间。最终修复已包含在生成时间和生成 API 中，不重复相加；两次正式应用分别计入应用总量。未完成部分不估算。</p>',
              '<div class="scroll"><table><tr><th>方法</th><th>生成累计/中位数秒</th><th>其中最终修复累计秒</th><th>应用累计/中位数秒</th><th>全部生成API</th><th>全部应用API（含回退）</th></tr>']
    for method,s in statistics.items():
        vals = [method,seconds(s['generation_seconds']['sum'])+' / '+seconds(s['generation_seconds']['median']),
                seconds(s['final_review_seconds']['sum']),
                seconds(s['application_seconds']['sum'])+' / '+seconds(s['application_seconds']['median'])]
        lines.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in vals)+'<td>'+api_text(s['generation_api'])+
                     '</td><td>'+api_text(s['application_api'])+'</td></tr>')
    lines += ['</table></div><h2>生成与最终修复</h2><div class="scroll"><table><tr><th>任务</th><th>方法</th><th>状态</th><th>生成秒</th><th>其中最终修复秒</th><th>修改文件数</th><th>全部生成API</th><th>问题/修改</th></tr>']
    for r in rows:
        g,f = r.get('generation') or {},r['final_review']
        issue = g.get('error') or r.get('error') or f.get('changed_files',[])
        vals = [r['case'],r['method'],g.get('status',r['status']),seconds(g.get('seconds')),seconds(f.get('seconds')),len(f.get('changed_files',[]))]
        lines.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in vals)+'<td>'+api_text(r['generation_api'])+'</td><td><details><summary>详情</summary><pre>'+esc(json.dumps(issue,ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
    lines += ['</table></div><h2>两次正式应用</h2><div class="scroll"><table><tr><th>任务</th><th>方法</th><th>次数</th><th>执行路径</th><th>得分/判定</th><th>应用总秒</th><th>API（含回退）</th><th>问题与扣分</th></tr>']
    for r in rows:
        for i,a in enumerate(r.get('applications',[]),1):
            root = Path(r['directory'])
            records = [read(p) for p in (root/'api').glob('*.json')]
            totals = api_totals([c for c in records if c and c['phase'].startswith(f'application_{i}')], prices)
            design = read(root/'verifier/rubrics.json',{}).get('rubrics',[])
            maxima = {x['id']:1 if x['scale'] == 'binary' else 5 for x in design}
            issues = {k:a[k] for k in ('error','fallback_reason','fallback_error') if a.get(k)}
            issues['deductions'] = [x for x in a.get('rubrics',[]) if x['score'] < maxima.get(x['rubric_id'],5)]
            vals = [r['case'],r['method'],i,a.get('mode'),score(a.get('score')) if a.get('score') is not None else a.get('outcome',a.get('status')),seconds(a.get('seconds'))]
            lines.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in vals)+'<td>'+api_text(totals)+'</td><td><details><summary>详情</summary><pre>'+esc(json.dumps(issues,ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
    lines += ['</table></div><p>原始材料：summary.json、各任务 generation/、api/、applications/。生成与应用代码快照保存在 source_versions/。</p>']
    if (batch/'ISSUES.html').exists():
        lines.append('<p><a href="ISSUES.html">已核实的问题、原任务和候选回答</a></p>')
    lines.append('</html>')
    (batch/'RESULTS.html').write_text('\n'.join(lines))
    return statistics
