"""Run existing reference executions once, preserving failures and original scores."""
from __future__ import annotations

import json
from pathlib import Path

from .application.runner import apply
from .data import load_selected, prepare_evidence, CASES
from .generation.pipeline import generate, write_json


def summarize(rows):
    apps=[r['application'] for r in rows if r.get('application') is not None]
    return {'total':len(rows),'generated':sum(r['generation']['status']=='generated' for r in rows),
        'program_started':len(apps),'not_started':len(rows)-len(apps),
        'generation_failed':sum(r['generation']['status']!='generated' for r in rows),
        'program_completed':sum(a['mode']=='program' and a['status']=='completed' for a in apps),
        'reference_full_score':sum(a['mode']=='program' and a['status']=='completed' and abs(a['score']-1)<1e-12 for a in apps),
        'fallback_count':sum(a['mode']=='fallback_codex' for a in apps),
        'verification_errors':sum(a['status']=='verification_error' for a in apps)}


def usage_totals(value):
    if isinstance(value,dict):
        out={}
        for k,v in value.items():
            if k in ('input_tokens','output_tokens','cached_input_tokens','prompt_tokens','completion_tokens','total_tokens') and type(v) is int:
                out[k]=out.get(k,0)+v
            elif isinstance(v,(dict,list)):
                for a,b in usage_totals(v).items(): out[a]=out.get(a,0)+b
        return out
    if isinstance(value,list):
        out={}
        for v in value:
            for a,b in usage_totals(v).items(): out[a]=out.get(a,0)+b
        return out
    return {}


def write_report(batch: Path, rows: list):
    stats=summarize(rows)
    write_json(batch/'summary.json',{'statistics':stats,'cases':rows})
    lines=['# Program verifier 首版实跑结果','',
        f"六案例选样统计（当前完成记录 {stats['total']}）：生成成功 {stats['generated']}/{stats['total']}；program 完成 {stats['program_completed']}/{stats['total']}；参考满分 {stats['reference_full_score']}/{stats['total']}；回退 {stats['fallback_count']}/{stats['program_started']} 次 program 启动。",
        '', '参考运行暂视为正确。原分数完整保留；没有为了使参考通过而自动修复生成代码或调整标准。', '',
        '| 案例 | 生成 | 执行路径 | 分数/判定 | rubric 方法 | 生成秒 | 应用秒 |',
        '|---|---|---|---|---|---|---|']
    for r in rows:
        g=r['generation'];a=r.get('application') or {}
        rubrics=r.get('design',{}).get('rubrics',[])
        methods=', '.join(f"{x['id']}:{x['method']}" for x in rubrics)
        score=a.get('score');verdict=f'{score:.4f}'.rstrip('0').rstrip('.') if score is not None else a.get('outcome','—')
        lines.append(f"| {r['case']} | {g['status']} | {a.get('mode','未启动')} / {a.get('status','—')} | {verdict} | {methods} | {g.get('seconds',0):.1f} | {a.get('seconds',0):.1f} |")
    for r in rows:
        lines+=['',f"## {r['case']}：{r['task_id']}", '',f"目录：`{r['directory']}`"]
        a=r.get('application') or {};g=r['generation']
        if g['status']!='generated': lines+=['',f"生成失败（{g.get('stage')}）：{g.get('error')}"]
        if a.get('fallback_reason'): lines+=['',f"回退原因：{json.dumps(a['fallback_reason'],ensure_ascii=False)}"]
        if a.get('fallback_error'): lines+=['',f"回退失败：{json.dumps(a['fallback_error'],ensure_ascii=False)}"]
        for result in a.get('rubrics',[]):
            lines+=['',f"- {result['rubric_id']}：{result['score']}；{result['reason']}"]
        lines+=['',f"生成调用 token：`{json.dumps(r.get('generation_usage',{}),ensure_ascii=False)}`；应用调用 token：`{json.dumps(usage_totals([c.get('usage') for c in a.get('cost',{}).get('calls',[])]),ensure_ascii=False)}`。"]
        if a.get('mode')=='fallback_codex':
            fallback_cost=a.get('cost',{}).get('fallback',{})
            lines+=['', f"整任务回退 token：`{json.dumps(usage_totals(fallback_cost),ensure_ascii=False)}`；原始调用与判定保存在 `reference_validation/fallback/logs`；不会计入 program 成功。"]
    lines+=['', '未运行任务负例或训练；美元成本未配置模型单价，报告记录可获得的 token、耗时及原始 usage；供应方未报告的 token 记 unknown，不能按零处理。', '']
    (batch/'RESULTS.md').write_text('\n'.join(lines))


def run_batch(data_root: Path, batch: Path, names: list[str], config: dict) -> dict:
    batch=batch.resolve();batch.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name,case in load_selected(data_root,names):
        task_id=case.task['task_id'];out=batch/task_id
        out.mkdir(exist_ok=True)
        evidence=out/'source_evidence'
        generation_result=out/'generation/result.json'
        if not (evidence/'program_request.json').exists() and not generation_result.exists():
            try:
                prepare_evidence(case,evidence)
            except Exception as error:
                write_json(generation_result,{'task_id':task_id,'status':'generation_failed','stage':'evidence_preparation','error':f'{type(error).__name__}: {error}'})
        if generation_result.exists():
            g=json.loads(generation_result.read_text())
        elif (out/'verifier').exists():
            g={'task_id':task_id,'status':'generation_failed','stage':'interrupted','error':'Previous generation has no completion record; retained without automatic repair'}
            write_json(generation_result,g)
        else:
            print(f'{name}: generating',flush=True)
            g=generate(evidence,out,config)
        a=None;design={}
        if g['status']=='generated':
            design=json.loads((out/'verifier/rubrics.json').read_text())
            result_file=out/'reference_validation/result.json'
            if result_file.exists(): a=json.loads(result_file.read_text())
            elif (out/'reference_validation').exists():
                a={'mode':'program','status':'verification_error','score':None,'error':'Previous application interrupted; use a new application directory for explicit rerun'}
            else:
                print(f'{name}: applying',flush=True)
                a=apply(out/'verifier',evidence,out/'reference_validation',config)
        generation_usage=[]
        for p in (out/'generation').glob('*/call.json'):
            generation_usage.append(json.loads(p.read_text()).get('usage'))
        row={'case':name,'task_id':task_id,'directory':str(out),'generation':g,'application':a,'design':design,'generation_usage':usage_totals(generation_usage)}
        rows.append(row);write_report(batch,rows)
        print(f'{name}: {g["status"]}, application={a.get("mode") if a else None}, score={a.get("score") if a else None}',flush=True)
    return {'statistics':summarize(rows),'results':str(batch/'RESULTS.md')}
