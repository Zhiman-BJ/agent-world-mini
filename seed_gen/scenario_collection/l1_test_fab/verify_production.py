"""Manufactured, deterministic production fixtures with independent oracles.

These are local simulations, not an MES connection or measured fab performance.
Run from repo root using .venv-scenario-fab-production/Scripts/python.exe.
"""
from __future__ import annotations
import dataclasses
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import platform

import simpy
import salabim as sal
from pyjobshop import Model
from job_shop_lib import JobShopInstance, Operation
from job_shop_lib.dispatching.rules import DispatchingRuleSolver
from reliability.Fitters import Fit_Exponential_1P
from reliability.Distributions import Exponential_Distribution, Weibull_Distribution

OUT=Path('seed_gen/scenario_collection/l1_test_fab/runtime/production')
PACKAGES=['simpy','salabim','pyjobshop','job-shop-lib','reliability','ortools']

def task(identifier, **data): return dict(id=identifier,status='passed',**data)

def queue(capacity=1, order=(4,1,2)):
    env=simpy.Environment(); machine=simpy.Resource(env,capacity=capacity); rows=[]
    def lot(i,duration):
        with machine.request() as request:
            yield request
            start=env.now
            yield env.timeout(duration)
            rows.append(dict(id=i,start=start,end=env.now,duration=duration))
    for i,d in enumerate(order): env.process(lot(i,d))
    env.run()
    return rows

def fab_des():
    wrong=queue(2); fixed=queue(1)
    assert max(x['end'] for x in wrong)!=7
    assert [x['end'] for x in fixed]==[4,5,7]
    assert all(a['end']<=b['start'] for a,b in zip(fixed,fixed[1:]))
    fifo=queue(order=(4,1,2)); spt=queue(order=(1,2,4))
    # Exhaustively enumerate all three-lot orders as an independent objective oracle.
    objective=lambda order: sum(itertools.accumulate(order))/3
    optimum=min(objective(o) for o in itertools.permutations((4,1,2)))
    assert sum(r['end'] for r in spt)/3==optimum==11/3
    assert sum(r['end'] for r in fifo)/3>optimum
    return [task('repair_machine_capacity',wrong=wrong,repaired=fixed,oracle_completion=[4,5,7]),
            task('compare_fifo_spt',fifo=fifo,spt=spt,oracle_best_mean_flow=optimum)]

def sal_queue(capacity):
    env=sal.Environment(trace=False,yieldless=False); machine=sal.Resource(capacity=capacity,env=env)
    wait=sal.Monitor(name='waiting',env=env); rows=[]
    class Lot(sal.Component):
        def setup(self,duration,lot_id): self.duration,self.lot_id=duration,lot_id
        def process(self):
            arrival=env.now()
            yield self.request(machine)
            start=env.now(); wait.tally(start-arrival)
            yield self.hold(self.duration)
            rows.append(dict(id=self.lot_id,start=start,end=env.now()))
            self.release(machine)
    for i,d in enumerate((4,1,2)): Lot(duration=d,lot_id=i,env=env)
    env.run()
    return dict(rows=rows,mean_wait=wait.mean(),samples=wait.number_of_entries())

def alternative_des():
    wrong=sal_queue(2); fixed=sal_queue(1)
    assert [r['end'] for r in fixed['rows']]==[4,5,7]
    assert [r['end'] for r in wrong['rows']]!=[4,5,7]
    # Time-zero arrivals have waits 0, 4, 5. End times are not queue waits.
    wrongly_labelled_wait=sum(r['end'] for r in fixed['rows'])/3
    assert wrongly_labelled_wait!=3 and fixed['mean_wait']==3
    assert fixed['samples']==3
    return [task('repair_salabim_capacity',wrong=wrong,repaired=fixed),
            task('repair_wait_metric',wrong_mean_wait=wrongly_labelled_wait,repaired_mean_wait=fixed['mean_wait'],oracle_waits=[0,4,5])]

def dispatching():
    instance=JobShopInstance([[Operation(0,d)] for d in (4,1,2)],name='three-fixed-lots')
    outputs={}
    for rule in ('first_come_first_served','shortest_processing_time'):
        schedule=DispatchingRuleSolver(rule,ready_operations_filter=None).solve(instance)
        outputs[rule]=[dict(job=x.operation.job_id,start=x.start_time,end=x.end_time) for x in schedule.schedule[0]]
    spt=outputs['shortest_processing_time']; fifo=outputs['first_come_first_served']
    assert [r['job'] for r in spt]==[1,2,0]
    assert sum(r['end'] for r in fifo)==16 and sum(r['end'] for r in spt)==11
    # Real transfer from library schedule into SimPy timing, with identities checked.
    def replay(scale):
        env=simpy.Environment(); done=[]
        def one(row):
            yield env.timeout(row['start']*scale)
            yield env.timeout((row['end']-row['start'])*scale)
            done.append(dict(job=row['job'],end=env.now))
        for row in spt:env.process(one(row))
        env.run(); return done
    wrong=replay(60); fixed=replay(1)
    assert [x['end'] for x in wrong]!=[1,3,7]
    assert [x['end'] for x in fixed]==[1,3,7]
    return [task('choose_dispatch_rule',schedules=outputs,independent_optimum_sum_completion=11),
            task('repair_schedule_time_unit',wrong=wrong,repaired=fixed,time_unit='minute')]

def schedule_model(breaks=(),precedence=True):
    model=Model(); machine=model.add_machine(breaks=list(breaks),name='ETCH01')
    job=model.add_job(name='LOT01'); first=model.add_task(job,name='etch'); second=model.add_task(job,name='clean')
    model.add_mode(first,machine,duration=3); model.add_mode(second,machine,duration=2)
    if precedence:model.add_end_before_start(first,second)
    result=model.solve(time_limit=10,display=False,num_workers=1)
    assert str(result.status).endswith('OPTIMAL')
    return model,result,[dataclasses.asdict(x) for x in result.best.tasks]

def schedule_and_pm():
    _,wrong,wrongrows=schedule_model()
    _,fixed,rows=schedule_model(((3,5),))
    assert wrong.objective==5 and fixed.objective==7
    assert all(r['end']<=3 or r['start']>=5 for r in rows)
    assert rows[0]['start']==0 and rows[0]['end']==3 and rows[1]['start']==5 and rows[1]['end']==7
    # Replay optimized starts and processing durations on the actual DES resource.
    def replay(records):
        env=simpy.Environment(); resource=simpy.Resource(env,capacity=1); observed=[]
        def operation(i,row):
            yield env.timeout(row['start'])
            with resource.request() as req:
                yield req
                start=env.now; yield env.timeout(row['end']-row['start']); observed.append((i,start,env.now))
        for i,row in enumerate(records):env.process(operation(i,row))
        env.run();return observed
    observed=replay(rows)
    assert observed==[(0,0,3),(1,5,7)]
    wrong_overlaps=[r for r in wrongrows if r['start']<5 and r['end']>3]
    assert wrong_overlaps
    # A separate release-date bug: lot isn't released until t=2, so 0-start is invalid.
    model=Model();m=model.add_machine();j=model.add_job(release_date=2);a=model.add_task(j);b=model.add_task(j)
    model.add_mode(a,m,3);model.add_mode(b,m,2);model.add_end_before_start(a,b)
    released=model.solve(display=False,num_workers=1,time_limit=10)
    assert released.objective==7 and released.best.tasks[0].start==2
    constraints=[task('repair_resource_break',wrong_objective=wrong.objective,repaired_objective=fixed.objective,repaired=rows,oracle_optimum=7),
                 task('repair_lot_release',wrong_start=wrongrows[0]['start'],repaired=[dataclasses.asdict(x) for x in released.best.tasks],oracle_optimum=7)]
    pm=[task('repair_pm_calendar',wrong_overlaps=wrong_overlaps,repaired=rows,maintenance=[3,5],oracle_optimum=7),
        task('replay_pm_schedule',optimizer_schedule=rows,des_observed=observed,bridge='pyjobshop ScheduledTask integer minutes -> SimPy Resource timeouts')]
    return constraints,pm

def part_lifetime():
    fit=Fit_Exponential_1P(failures=[10,20,30],right_censored=[40],show_probability_plot=False,print_results=False)
    wrong=Fit_Exponential_1P(failures=[10,20,30,40],show_probability_plot=False,print_results=False)
    assert math.isclose(fit.Lambda,3/100,rel_tol=1e-7)
    assert not math.isclose(wrong.Lambda,3/100,rel_tol=1e-2)
    survival=float(fit.distribution.SF(xvals=20,show_plot=False))
    assert math.isclose(survival,math.exp(-.03*20),rel_tol=1e-7)
    age=Weibull_Distribution(alpha=100,beta=2)
    correct=float(age.SF(xvals=50,show_plot=False)); wrongunit=float(age.SF(xvals=3000,show_plot=False))
    assert math.isclose(correct,math.exp(-.25),rel_tol=1e-12) and wrongunit<.01
    t10=float(age.quantile(.1)); assert math.isclose(t10,100*math.sqrt(-math.log(.9)),rel_tol=1e-12)
    return [task('repair_censoring',wrong_rate=wrong.Lambda,repaired_rate=fit.Lambda,oracle_rate=.03,survival_at_20=survival),
            task('repair_lifetime_unit',wrong_survival=wrongunit,repaired_survival=correct,oracle=math.exp(-.25),b10=t10,unit='hour')]

def mtbf_mttr():
    env=simpy.Environment(); events=[]
    def equipment():
        for uptime,downtime in zip((8,12,10),(2,1,3)):
            yield env.timeout(uptime); events.append(dict(time=env.now,event='failure'))
            yield env.timeout(downtime); events.append(dict(time=env.now,event='repair'))
    env.process(equipment());env.run()
    ups=[];downs=[];last=0
    for failure,repair in zip(events[::2],events[1::2]):
        ups.append(failure['time']-last);downs.append(repair['time']-failure['time']);last=repair['time']
    mtbf=sum(ups)/3;mttr=sum(downs)/3;availability=sum(ups)/env.now
    assert mtbf==10 and mttr==2 and availability==5/6
    wrong_mtbf=env.now/3
    assert wrong_mtbf!=mtbf
    fitted=Fit_Exponential_1P(failures=ups,show_probability_plot=False,print_results=False)
    assert math.isclose(fitted.Lambda,.1,rel_tol=1e-8)
    distribution=Exponential_Distribution(Lambda=fitted.Lambda)
    conditional=float(distribution.mean_residual_life(5))
    assert math.isclose(conditional,10,rel_tol=1e-8)
    return [task('repair_mtbf_denominator',events=events,wrong_mtbf=wrong_mtbf,repaired_mtbf=mtbf,mttr=mttr,availability=availability),
            task('fit_renewal_uptime',uptimes=ups,rate=fitted.Lambda,mean_residual_life_at_5=conditional,oracle_exponential_rate=.1,boundary='Exponential model assumption; three fixtures do not establish empirical lifetime law')]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    def run():
        constraints,pm=schedule_and_pm()
        return {'06.05.01':fab_des(),'06.05.03':alternative_des(),'06.05.04':dispatching(),
                '06.05.05':constraints,'06.06.02':pm,'06.06.03':part_lifetime(),'06.06.04':mtbf_mttr()}
    first,second=run(),run()
    assert first==second,'Fixed fixtures must repeat exactly'
    for sid,tasks in first.items():
        record=dict(scenario_id=sid,status='passed',tasks=tasks,repeat_runs=2,repeat_equal=True,
                    python=platform.python_version(),versions={p:importlib.metadata.version(p) for p in PACKAGES},
                    boundary='Local manufactured fixtures; no live fab, no license backend, no production performance claim.')
        (OUT/f'{sid}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(sid,'passed',len(tasks),flush=True)

if __name__=='__main__':main()
