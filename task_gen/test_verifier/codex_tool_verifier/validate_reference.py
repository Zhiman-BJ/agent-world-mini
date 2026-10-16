"""Four selected reference runs, with unmodified failures, scores and costs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from ..program_verifier.data import load_selected, prepare_evidence
from ..program_verifier.generation.pipeline import write_json
from ..program_verifier.validate_reference import usage_totals
from .application import apply
from .generation import generate

DEFAULT_CASES = ['S21', 'S24', 'S91', 'S81']


def cached_application(result_file, config):
    result = json.loads(result_file.read_text())
    requested = config.get('static_tools_enabled', True)
    enabled = result.get('static_tools_enabled', result.get('mode') != 'codex_rubric_only')
    expected = 'codex_with_tools' if requested else 'codex_rubric_only'
    if (type(requested) is not bool or type(enabled) is not bool or enabled != requested
            or result.get('mode') not in (expected, 'fallback_codex')):
        raise ValueError(f'Cached application mode mismatch at {result_file}; use a new output directory')
    return result


def summarize(rows, total=None):
    apps = [r['application'] for r in rows if r.get('application') is not None]
    completed = [a for a in apps if a['mode'] in ('codex_with_tools', 'codex_rubric_only') and a['status'] == 'completed']
    attempted = [r for r in rows if r['generation'].get('stage') != 'evidence_preparation']
    return {'total': total if total is not None else len(rows), 'finished': len(rows),
            'preparation_failed': len(rows)-len(attempted), 'generation_attempted': len(attempted),
            'generated': sum(r['generation']['status'] == 'generated' for r in rows),
            'generation_failed': sum(r['generation']['status'] != 'generated' for r in attempted),
            'main_started': sum(a.get('main_started', True) for a in apps),
            'main_completed': len(completed), 'reference_full_score': sum(a['score'] == 1 for a in completed),
            'fallback_count': sum(a['mode'] == 'fallback_codex' for a in apps),
            'verification_errors': sum(a['status'] == 'verification_error' for a in apps),
            'static_tools': sum(len(r.get('tools', [])) for r in rows),
            'tool_calls': sum(len(a.get('tool_calls', [])) for a in apps),
            'zero_tool_tasks': sum(r['generation']['status'] == 'generated' and not r.get('tools') for r in rows)}


def write_report(batch, rows, names):
    stats = summarize(rows, len(names))
    write_json(batch/'summary.json', {'selected_cases': names, 'statistics': stats, 'cases': rows})
    lines = ['# Codex + 静态工具：首批参考验收', '',
             f"已完成记录 {stats['finished']}/{stats['total']}；证据准备失败 {stats['preparation_failed']}；生成成功 {stats['generated']}/{stats['generation_attempted']}；主流程完成 {stats['main_completed']}；参考满分 {stats['reference_full_score']}；回退 {stats['fallback_count']}。", '',
             '| 案例 | 生成 | 执行 | 分数/判定 | 工具数/调用数 | 生成秒 | 应用秒 |',
             '|---|---|---|---|---|---|---|']
    for row in rows:
        g, a = row['generation'], row.get('application') or {}
        score = a.get('score') if a.get('score') is not None else a.get('outcome', '—')
        lines.append(f"| {row['case']} | {g['status']} | {a.get('mode', '未启动')} / {a.get('status', '—')} | {score} | {len(row.get('tools', []))}/{len(a.get('tool_calls', []))} | {g.get('seconds', 0):.1f} | {a.get('seconds', 0):.1f} |")
    for row in rows:
        g, a = row['generation'], row.get('application') or {}
        lines += ['', f"## {row['case']}", '', f"目录：`{row['directory']}`"]
        if g['status'] != 'generated':
            phase = '证据准备失败，未开始生成' if g.get('stage') == 'evidence_preparation' else '生成失败'
            lines += ['', f"{phase}（{g.get('stage')}）：{g.get('error')}"]
        for key in ('fallback_reason', 'fallback_error', 'error'):
            if a.get(key):
                lines += ['', f'{key}：{json.dumps(a[key], ensure_ascii=False)}']
        for result in a.get('rubrics', []):
            lines += ['', f"- {result['rubric_id']}：{result['score']}；{result['reason']}"]
        for label, usage in [('生成', row['generation_usage']), ('应用', usage_totals(a.get('cost', {}).get('calls', []))),
                             ('回退', usage_totals(a.get('cost', {}).get('fallback', {})))]:
            lines += ['', f'{label} token：`{json.dumps(usage or "unknown", ensure_ascii=False)}`。']
        calls = a.get('tool_calls', [])
        cited = {ref for r in a.get('rubrics', []) for ref in r.get('tool_call_ids', [])}
        lines += ['', f"真实工具调用 {len(calls)} 次，其中被评分引用 {len(cited)} 次；工具耗时合计 {sum(c.get('seconds',0) for c in calls):.2f}s。"]
    lines += ['', '参考运行暂视为正确；非满分和失败原样保留，不自动修改评分标准或生成代码迎合参考。',
              '未测任务负例，不能据此评价错误识别准确率。未配置美元单价；cached_input_tokens 是 input_tokens 的子集，不能重复相加。未知 token 不当作零。', '']
    (batch/'RESULTS.md').write_text('\n'.join(lines))


def run_case(name, case, batch, config):
    task_id = case.task['task_id']
    out = batch/task_id
    out.mkdir(exist_ok=True)
    evidence = out/'source_evidence'
    generation_result = out/'generation/result.json'
    if not (evidence/'program_request.json').exists() and not generation_result.exists():
        try:
            prepare_evidence(case, evidence)
        except Exception as error:
            write_json(generation_result, {'task_id':task_id, 'status':'generation_failed',
                       'stage':'evidence_preparation', 'error':f'{type(error).__name__}: {error}'})
    if generation_result.exists():
        g = json.loads(generation_result.read_text())
    elif (out/'verifier').exists():
        g = {'task_id':task_id, 'status':'generation_failed', 'stage':'interrupted',
             'error':'Previous incomplete generation retained; no automatic repair'}
        write_json(generation_result, g)
    else:
        print(f'{name}: generating', flush=True)
        g = generate(evidence, out, config)
    a, design, tools = None, {}, []
    if g['status'] == 'generated':
        design = json.loads((out/'verifier/rubrics.json').read_text())
        tools = json.loads((out/'verifier/tools.json').read_text())['tools']
        result_file = out/'reference_validation/result.json'
        if result_file.exists():
            a = cached_application(result_file, config)
        elif result_file.parent.exists():
            enabled = config.get('static_tools_enabled', True)
            a = {'mode':'codex_with_tools' if enabled else 'codex_rubric_only',
                 'static_tools_enabled':enabled, 'status':'verification_error', 'score':None, 'main_started':False,
                 'error':'Interrupted application retained; use a new output directory for explicit rerun'}
        else:
            print(f'{name}: applying', flush=True)
            a = apply(out/'verifier', evidence, out/'reference_validation', config)
    usage = [json.loads(p.read_text()).get('usage') for p in (out/'generation').glob('*/call.json')]
    row = {'case':name, 'task_id':task_id, 'directory':str(out), 'generation':g, 'application':a,
           'design':design, 'tools':tools, 'generation_usage':usage_totals(usage)}
    print(f'{name}: {g["status"]}, mode={(a or {}).get("mode")}, score={(a or {}).get("score")}', flush=True)
    return row


def run_batch(data_root, batch, names, config):
    batch = Path(batch).resolve()
    batch.mkdir(parents=True, exist_ok=True)
    if len(names) != len(set(names)):
        raise ValueError('case names must be unique')
    cases = load_selected(data_root, names)
    # Reject mixed modes before overwriting reports or launching any new model calls.
    for _, case in cases:
        result_file = batch/case.task['task_id']/'reference_validation/result.json'
        if result_file.exists():
            cached_application(result_file, config)
    rows = []
    write_report(batch, rows, names)
    with ThreadPoolExecutor(max_workers=int(config.get('case_concurrency', 2))) as executor:
        futures = [executor.submit(run_case, name, case, batch, config) for name, case in cases]
        for future in as_completed(futures):
            rows.append(future.result())
            rows.sort(key=lambda row: names.index(row['case']))
            write_report(batch, rows, names)
    return {'statistics':summarize(rows), 'results':str(batch/'RESULTS.md')}
