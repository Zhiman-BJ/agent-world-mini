"""Two independent runs of a fixed sample, with wire-level API accounting."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib

from .application.runner import _isolated_auth
from .metering import MeteringRelay, now, save, token_counts
from .random_sample import load_selection, run_case


def slots(selected):
    return [dict(item, slot=f'{item["case"]}_R{repeat}', repeat=repeat)
            for repeat in (1, 2) for item in selected]


def read(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError): return default


def api_totals(records, prices=None):
    counts = [token_counts(r.get('usage')) for r in records]
    out = {'requests':len(records), 'requests_with_usage':sum(c['input'] is not None and c['output'] is not None for c in counts),
           'api_errors':sum(r.get('status')=='http_error' or bool(r.get('api_error')) for r in records),
           'transport_errors':sum(r.get('status')=='error' and not r.get('client_disconnected') and not r.get('api_error') for r in records),
           'client_cancellations':sum(bool(r.get('client_disconnected')) for r in records),
           'api_seconds_sum':sum(r.get('seconds',0) for r in records),
           'known_input_tokens':sum(c['input'] or 0 for c in counts),
           'known_cached_input_tokens':sum(c['cached_input'] or 0 for c in counts),
           'known_output_tokens':sum(c['output'] or 0 for c in counts),
           'usage_complete':all(c['input'] is not None and c['output'] is not None for c in counts),
           'estimated_cost':None, 'known_estimated_cost':None, 'currency':(prices or {}).get('currency')}
    if prices:
        values = []
        for c in counts:
            if all(v is not None for v in c.values()):
                if not 0 <= c['cached_input'] <= c['input']: raise ValueError('Invalid cached token count')
                values.append(((c['input']-c['cached_input'])*prices['input_per_million']
                               + c['cached_input']*prices['cached_input_per_million']
                               + c['output']*prices['output_per_million'])/1e6)
        out['known_estimated_cost'] = sum(values)
        if len(values) == len(counts): out['estimated_cost'] = sum(values)
    # Keep provider-reported amounts separate from estimates; currency must be explicit.
    actual = {}
    for r in records:
        fields = r.get('cost_fields') or {}
        amount, currency = fields.get('cost_usd'), 'USD'
        if amount is None: amount, currency = fields.get('cost'), fields.get('currency')
        if type(amount) in (float,int) and currency:
            actual[currency] = actual.get(currency,0)+amount
    out['known_provider_cost'] = actual
    return out


def source_hashes():
    root = Path(__file__).parent
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in ('.py','.json','.md') and '__pycache__' not in p.parts}


@contextmanager
def metered_config(config, relay):
    with tempfile.TemporaryDirectory(prefix='verifier-metered-auth-') as temporary:
        auth = Path(temporary)
        _isolated_auth(config, auth)
        text = (auth/'config.toml').read_text()
        settings = tomllib.loads(text)
        provider = settings['model_providers'][settings['model_provider']]
        text = text.replace(json.dumps(provider['base_url']), json.dumps(relay.base_url))
        (auth/'config.toml').write_text(text)
        result = json.loads(json.dumps(config))
        result['codex']['codex_home'] = str(auth)
        result['codex'].pop('base_url', None)
        result['llm']['base_url'] = relay.base_url.rstrip('/')+'/v1'
        # Existing fallback uses the same isolated home and therefore the same meter.
        yield result


def run_attempt(batch, item, config, data_root):
    attempts = batch/'attempts'
    previous = list(attempts.glob(item['slot']+'_a*'))
    number = max([int(p.name.rsplit('_a',1)[1]) for p in previous] or [0])+1
    attempt = f'{item["slot"]}_a{number:02d}'
    root = attempts/attempt
    def phase():
        if (root/'reference_validation/program_error.json').exists(): return 'fallback'
        if (root/'generation/result.json').exists(): return 'application'
        return 'generation'
    started = time.monotonic()
    meta = dict(slot=item['slot'], case=item['case'], repeat=item['repeat'], task_id=item['task_id'],
                case_name=item['case_name'], attempt=attempt, status='running', started_at=now(),
                source_sha256=source_hashes(), model='gpt-6.1-sol', reasoning_effort='high')
    meta_path = batch/'attempt_records'/(attempt+'.json')
    save(meta_path, meta)
    home = Path(config['codex'].get('codex_home',Path.home()/'.codex'))
    settings = tomllib.loads((home/'config.toml').read_text())
    upstream = settings['model_providers'][settings['model_provider']]['base_url']
    try:
        with MeteringRelay(upstream, batch/'api'/attempt, phase) as relay:
            with metered_config(config, relay) as cfg:
                spec = {'item':dict(item,case=attempt),'data_root':str(data_root),'batch':str(attempts),'config':cfg}
                with tempfile.TemporaryDirectory(prefix='verifier-run-spec-') as temporary:
                    spec_path = Path(temporary)/'spec.json';save(spec_path,spec)
                    with (batch/'logs'/(attempt+'.log')).open('w') as log:
                        child = subprocess.Popen([sys.executable,'-m',__spec__.name,'--worker',str(spec_path)], stdout=log,stderr=subprocess.STDOUT)
                        meta['pid']=child.pid;save(meta_path,meta)
                        meta['exit_code']=child.wait()
                meta['status']='finished' if (root/'row.json').exists() else 'worker_failed'
    except Exception as error:
        meta.update(status='worker_failed',error=f'{type(error).__name__}: {error}')
    finally:
        meta.update(finished_at=now(),seconds=time.monotonic()-started)
        save(meta_path,meta)
    print(attempt,meta['status'],flush=True)
    return meta


def report(batch):
    selection=read(batch/'sampling.json');prices=read(batch/'pricing.json')
    records=[read(p) for p in sorted((batch/'attempt_records').glob('*.json'))]
    records=[r for r in records if r]
    rows=[]
    for item in slots(selection['selected']):
        trials=[r for r in records if r['slot']==item['slot']]
        row=dict(case=item['case'],repeat=item['repeat'],slot=item['slot'],task_id=item['task_id'],
                 case_name=item['case_name'],attempts=len(trials),status='pending',issues=[],score=None,
                 program_completed=False,reference_full_score=False,fallback=False,generation_seconds=None,
                 application_seconds=None,preparation_seconds=None,fallback_seconds=None,total_seconds=None,
                 cumulative_generation_seconds=0,cumulative_application_seconds=0,cumulative_fallback_seconds=0,
                 api={},phases={})
        calls=[]
        for trial in trials:
            calls += [v for p in sorted((batch/'api'/trial['attempt']).glob('*.json')) if (v:=read(p))]
            value=read(batch/'attempts'/trial['attempt']/'row.json',{})
            gen=value.get('generation',{});app=value.get('application') or {}
            row['cumulative_generation_seconds']+=gen.get('seconds',0)
            row['cumulative_application_seconds']+=app.get('seconds',0)
            fallback_calls=read(batch/'attempts'/trial['attempt']/'reference_validation/fallback/logs/attempts.json',[])
            row['cumulative_fallback_seconds']+=sum(call['seconds'] for call in fallback_calls)
            error=gen.get('error') or app.get('fallback_reason') or app.get('error') or trial.get('error')
            if error:row['issues'].append({'attempt':trial['attempt'],'error':error})
            if trial.get('diagnosis'):
                row['issues'].append({'attempt':trial['attempt'],'error':trial['diagnosis']})
            if trial.get('reused_partial_from'):
                row['issues'].append({'attempt':trial['attempt'],'error':
                    f"补全生成：基于 {trial['reused_partial_from']}，{trial['repair_note']}"})
            if app.get('fallback_error'):
                row['issues'].append({'attempt':trial['attempt'],'error':{'message':
                    'Codex回退也失败：'+app['fallback_error']['message']}})
            if trial.get('reused_verifier_from'):
                note=trial.get('repair_note','试验共享SDK的调用传输')
                row['issues'].append({'attempt':trial['attempt'],'error':
                    f"应用复验：沿用 {trial['reused_verifier_from']} 的生成代码，{note}；本尝试生成耗时为0。"})
            scales={r['id']:1 if r['scale']=='binary' else 5 for r in value.get('design',{}).get('rubrics',[])}
            if app.get('mode')=='program' and app.get('status')=='completed':
                for result in app.get('rubrics',[]):
                    maximum=scales.get(result['rubric_id'])
                    if maximum is not None and result['score']<maximum:
                        row['issues'].append({'attempt':trial['attempt'],'error':
                            f"参考未满分 {result['rubric_id']}={result['score']}/{maximum}: {result['reason']}"})
        if trials:
            latest=trials[-1];root=batch/'attempts'/latest['attempt']
            value=read(root/'row.json',{})
            g=value.get('generation',{});a=value.get('application') or {}
            row.update(status=latest['status'],started_at=trials[0]['started_at'],finished_at=latest.get('finished_at'),
                       generation_status=g.get('status'),application_status=a.get('status'),mode=a.get('mode'),
                       score=a.get('score'),fallback=a.get('mode')=='fallback_codex',fallback_outcome=a.get('outcome'),
                       program_completed=a.get('mode')=='program' and a.get('status')=='completed',
                       reference_full_score=a.get('mode')=='program' and a.get('status')=='completed' and abs((a.get('score') or 0)-1)<1e-12,
                       generation_seconds=g.get('seconds'),application_seconds=a.get('seconds'),
                       latest_attempt=latest['attempt'],total_seconds=sum(t.get('seconds',0) for t in trials) if all('seconds' in t for t in trials) else None)
            if value and g.get('status')=='generated' and a.get('seconds') is not None:
                row['preparation_seconds']=max(0,value['seconds']-g['seconds']-a['seconds'])
            fallback_attempts=read(root/'reference_validation/fallback/logs/attempts.json',[])
            row['fallback_seconds']=sum(x['seconds'] for x in fallback_attempts) if row['fallback'] else 0
        row['api']=api_totals(calls,prices)
        row['phases']={phase:api_totals([c for c in calls if c['phase']==phase],prices) for phase in ('generation','application','fallback')}
        rows.append(row)
    stats={'planned':len(rows),'finished':sum(r['status'] in ('finished','worker_failed') for r in rows),
           'program_completed':sum(r['program_completed'] for r in rows),'reference_full_score':sum(r['reference_full_score'] for r in rows),
           'fallback':sum(r['fallback'] for r in rows),'generation_failed':sum(r.get('generation_status')=='generation_failed' for r in rows)}
    save(batch/'summary.json',{'updated_at':now(),'statistics':stats,'pricing':prices,'rows':rows})
    def fmt(value):return '—' if value is None else f'{value:.3f}' if isinstance(value,float) else str(value)
    columns=['任务','轮次','尝试','状态','累计生成秒','累计应用秒(含回退)','累计回退模型秒','累计总秒','输入token','其中缓存','输出token','用量已知/请求','估算金额','程序完成','参考满分','回退','回退判定','得分','问题']
    table=[]
    for r in rows:
        u=r['api'];cost=fmt(u['estimated_cost'])+((' '+u['currency']) if u['currency'] else '')
        issue='；'.join(x['error'].get('message',str(x['error'])) if isinstance(x['error'],dict) else str(x['error']) for x in r['issues'])
        table.append([r['case'],r['repeat'],r['attempts'],r['status'],fmt(r['cumulative_generation_seconds']),fmt(r['cumulative_application_seconds']),fmt(r['cumulative_fallback_seconds']),fmt(r['total_seconds']),
                      u['known_input_tokens'],u['known_cached_input_tokens'],u['known_output_tokens'],f"{u['requests_with_usage']}/{u['requests']}",cost,
                      '是' if r['program_completed'] else '否','是' if r['reference_full_score'] else '否','是' if r['fallback'] else '否',r.get('fallback_outcome') or '—',fmt(r['score']),issue])
    with (batch/'results.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(columns);w.writerows(table)
    lines=['# 固定20任务 × 两轮：Program verifier','',f'更新：{now()}；{json.dumps(stats,ensure_ascii=False)}','',
           '全部gpt-6.1-sol/high。程序完成表示未回退且完成评分；参考满分表示score=1，不设置额外通过阈值。',
           '每轮独立生成。表内生成/应用/回退/总秒和用量均累计该槽位所有尝试，包含修复前失败；仅应用复验不重复计生成时间。分数展示最近一次结果，原始记录不覆盖。未完成槽位的阶段累计值仅为已结束阶段。',
           '输入含缓存；API耗时总和不是并行任务墙钟耗时。token为已知部分，用量完整性看已知/请求。金额缺失为—；估算金额不是服务商账单。服务端原始费用字段保存在api记录，分阶段用量及费用在summary.json。','',
           '| '+' | '.join(columns)+' |','|'+'|'.join(['---']*len(columns))+'|']
    lines += ['| '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in row)+' |' for row in table]
    (batch/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    return stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--sampling-manifest',type=Path)
    parser.add_argument('--config',type=Path,default=Path(__file__).with_name('run_config.json'))
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--retry',nargs='*',default=[])
    args=parser.parse_args()
    if args.worker:
        spec=read(args.worker)
        # Eager import avoids concurrent lazy imports in the generation workers.
        from .generation.model import GenerationModel
        run_case(spec['item'],Path(spec['data_root']),Path(spec['batch']),spec['config'])
        return
    if not args.output or args.workers<1:parser.error('output and positive workers required')
    batch=args.output.resolve()
    if args.report_only:report(batch);return
    if not args.resume:
        if not args.sampling_manifest:parser.error('sampling-manifest required')
        selection=load_selection(args.sampling_manifest,20)
        batch.mkdir(parents=True,exist_ok=False)
        for folder in ('attempts','attempt_records','api','logs'):(batch/folder).mkdir()
        config=read(args.config)
        assert config['codex']['model']==config['llm']['model']=='gpt-6.1-sol'
        assert config['codex']['reasoning_effort']==config['llm']['reasoning_effort']=='high'
        save(batch/'sampling.json',selection);save(batch/'run_config.json',config)
    selection=read(batch/'sampling.json');config=read(batch/'run_config.json')
    all_slots=slots(selection['selected'])
    if set(args.retry)-{s['slot'] for s in all_slots}:parser.error('unknown retry slot')
    existing={read(p)['slot'] for p in (batch/'attempt_records').glob('*.json')}
    selected=[s for s in all_slots if s['slot'] not in existing or s['slot'] in args.retry]
    save(batch/'run_status.json',{'pid':os.getpid(),'started_at':now(),'status':'running','scheduled':[s['slot'] for s in selected]})
    report(batch)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(run_attempt,batch,item,config,Path(selection['data_root'])) for item in selected}
        while pending:
            done,pending=wait(pending,timeout=30,return_when=FIRST_COMPLETED)
            for future in done:future.result()
            report(batch)
    stats=report(batch)
    save(batch/'run_status.json',{'pid':os.getpid(),'finished_at':now(),'status':'finished','statistics':stats})


if __name__=='__main__':main()
