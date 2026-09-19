"""Deterministic trace/SPC fixtures; all process values are manufactured."""
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from tsfresh import extract_features
import ruptures as rpt
from adtk.detector import ThresholdAD, InterQuartileRangeAD
from adtk.pipe import Pipeline as ADPipeline
from adtk.data import validate_series
from river import drift
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import confusion_matrix, accuracy_score

# Official 1.0.0 imports src.spc_lib internally: use its unchanged source namespace.
# This explicit compatibility bootstrap is required; pip import spc_lib fails.
SPC_SOURCE=Path('seed_pypi_raw/l1_test_fab/spc-lib').resolve()
sys.path.insert(0,str(SPC_SOURCE))
from src.spc_lib.charts.variables import IMRChart
from src.spc_lib.rules.western_electric import detect_violations

BASE=Path('seed_gen/scenario_collection/l1_test_fab')
OUT=BASE/'runtime/statistics'

def result(identifier,**kw):return dict(id=identifier,status='passed',**kw)
def features(frame):
    return extract_features(frame,column_id='wafer',column_sort='time',column_value='pressure',
                            default_fc_parameters={'mean':None,'maximum':None,'abs_energy':None,'variance':None},
                            n_jobs=0,disable_progressbar=True)

def traces():
    frame=pd.DataFrame({'wafer':['A']*4+['B']*4,'time':[0,1,2,3]*2,'pressure':[0,1000,2000,3000,1000,1000,1000,1000]})
    wrong=features(frame);fixedframe=frame.assign(pressure=frame.pressure/1000);fixed=features(fixedframe)
    assert wrong.loc['A','pressure__mean']==1500
    oracle={'pressure__mean':1.5,'pressure__maximum':3.,'pressure__abs_energy':14.,'pressure__variance':1.25}
    assert fixed.loc['A'].to_dict()==oracle
    merged=features(fixedframe.assign(wafer='A'))
    assert len(merged)==1 and list(fixed.index)==['A','B']
    assert fixed.loc['B','pressure__mean']==1
    return [result('repair_trace_units',wrong_mean=1500,repaired=fixed.to_dict(orient='index'),oracle=oracle),
            result('repair_trace_identity',wrong_rows=1,repaired_rows=2,expected_ids=['A','B'])]

def changepoints():
    data=np.r_[np.zeros(30),np.full(30,5.),np.zeros(30)]
    wrong=rpt.Pelt(model='l2',min_size=2,jump=1).fit(data).predict(pen=1e6)
    fixed=rpt.Pelt(model='l2',min_size=2,jump=1).fit(data).predict(pen=10)
    assert wrong==[90] and fixed==[30,60,90]
    second=np.r_[np.zeros(37),np.full(35,5.),np.zeros(28)]
    coarse=rpt.Dynp(model='l2',min_size=2,jump=5).fit(second).predict(n_bkps=2)
    fine=rpt.Dynp(model='l2',min_size=2,jump=1).fit(second).predict(n_bkps=2)
    assert coarse!=[37,72,100] and fine==[37,72,100]
    # Independent exact piecewise-constant oracle, not the detector's internal cost.
    sse=sum(float(sum((second[a:b]-sum(second[a:b])/(b-a))**2)) for a,b in zip([0]+fine[:-1],fine))
    assert sse==0
    return [result('repair_change_penalty',wrong=wrong,repaired=fixed,oracle=[30,60,90]),
            result('repair_change_grid',wrong=coarse,repaired=fine,independent_sse=sse,oracle=[37,72,100])]

def rule_anomaly():
    index=pd.date_range('2026-01-01',periods=7,freq='s')
    series=pd.Series([0.,.1,0.,4.,4.1,0.,.1],index=index)
    series=validate_series(series)
    wrong=ADPipeline([('pressure',ThresholdAD(high=10))]).detect(series)
    fixed=ADPipeline([('pressure',ThresholdAD(high=3))]).detect(series)
    expected=[False,False,False,True,True,False,False]
    assert not wrong.any() and fixed.tolist()==expected
    baseline=pd.Series([-1.,0.,1.,0.]*5,index=pd.date_range('2025-12-01',periods=20,freq='s'))
    model=ADPipeline([('range',InterQuartileRangeAD(c=3))]);model.fit(baseline)
    pred=model.detect(series)
    assert pred.tolist()==expected
    # Time index is a required data contract; malformed RangeIndex must fail.
    rejected=False
    try:ADPipeline([('pressure',ThresholdAD(high=3))]).detect(pd.Series(series.to_numpy()))
    except TypeError:rejected=True
    assert rejected
    return [result('repair_anomaly_threshold',wrong=wrong.tolist(),repaired=fixed.tolist(),oracle=expected),
            result('repair_time_index',range_index_rejected=rejected,repaired=pred.tolist(),baseline_points=20)]

def online_drift():
    values=[0.]*100+[5.]*80
    def run(threshold):
        model=drift.PageHinkley(min_instances=20,delta=.005,threshold=threshold,alpha=1.,mode='up');alarms=[]
        for i,value in enumerate(values):
            model.update(value)
            if model.drift_detected:alarms.append(i)
        return alarms
    wrong=run(1e6);fixed=run(10)
    assert not wrong and fixed and 100<=fixed[0]<=105 and not any(i<100 for i in fixed)
    model=drift.ADWIN(delta=.002,clock=1,min_window_length=5,grace_period=10);alarms=[]
    for i,value in enumerate(values):
        model.update(value)
        if model.drift_detected:alarms.append(i)
    assert alarms and 100<=alarms[0]<140
    fresh=drift.ADWIN(delta=.002,clock=1,min_window_length=5,grace_period=10)
    assert fresh.width==0 and fresh.estimation==0
    for value in [0.]*100:fresh.update(value)
    assert fresh.estimation==0 and not fresh.drift_detected
    return [result('repair_drift_threshold',wrong=wrong,repaired=fixed,known_change_index=100,allowed_delay=5),
            result('reset_drift_run',adwin_alarms=alarms,fresh_width=fresh.width,fresh_estimation=fresh.estimation,oracle_stationary_mean=0.)]

def trace_classification():
    rows=[]
    # Completely disjoint wafers; four test traces have means 0.5, 1.0, 5.5, 6.0.
    means=[0.,1.,.2,1.2,5.,6.,5.2,6.2,.5,1.,5.5,6.]
    for i,mean in enumerate(means):
        for t,v in enumerate((mean-.1,mean+.1)):rows.append((f'W{i:02}',t,v))
    frame=pd.DataFrame(rows,columns=['wafer','time','pressure']);x=features(frame)
    train=[f'W{i:02}' for i in range(8)];test=[f'W{i:02}' for i in range(8,12)]
    labels=pd.Series([0]*4+[1]*4,index=train);expected=[0,0,1,1]
    wrong=DecisionTreeClassifier(max_depth=1,random_state=17).fit(x.loc[train],1-labels).predict(x.loc[test]).tolist()
    model=DecisionTreeClassifier(max_depth=1,random_state=17).fit(x.loc[train],labels);pred=model.predict(x.loc[test]).tolist()
    assert wrong!=expected and pred==expected and accuracy_score(expected,pred)==1
    # The column schema is a real fitted-estimator precondition.
    schema_rejected=False
    try:model.predict(x.loc[test,list(reversed(x.columns))])
    except ValueError:schema_rejected=True
    assert schema_rejected
    repaired=model.predict(x.loc[test,model.feature_names_in_]).tolist()
    assert repaired==expected and not(set(train)&set(test))
    return [result('repair_trace_labels',wrong=wrong,repaired=pred,confusion_matrix=confusion_matrix(expected,pred).tolist(),train_ids=train,test_ids=test),
            result('repair_feature_schema',wrong_column_order_rejected=schema_rejected,repaired=repaired,expected_feature_columns=model.feature_names_in_.tolist())]

def control_chart():
    baseline=np.tile([-.5,.5],10);shift=np.tile([4.5,5.5],5);data=np.r_[baseline,shift].reshape(-1,1)
    goodmask=np.arange(30)<20;badmask=~goodmask
    wrong=IMRChart(data).fit(baseline_mask=badmask);fixed=IMRChart(data).fit(baseline_mask=goodmask)
    assert wrong.cl_main==5 and fixed.cl_main==0
    assert math.isclose(fixed.ucl_main,3/1.128,rel_tol=1e-12)
    assert not any(v>wrong.ucl_main for v in shift) and all(v>fixed.ucl_main for v in shift)
    rejected=False
    try:IMRChart(data[:,0]).fit(baseline_mask=goodmask)
    except np.exceptions.AxisError:rejected=True
    assert rejected
    return [result('repair_spc_baseline',wrong_center=float(wrong.cl_main),repaired_center=float(fixed.cl_main),ucl=float(fixed.ucl_main),oracle_ucl=3/1.128,alarms=list(range(20,30))),
            result('repair_subgroup_shape',wrong_1d_rejected=rejected,repaired_shape=list(data.shape),mr_sigma=1/1.128)]

def western_rules():
    data=np.array([0.,.2,3.5,.1,0.])
    wrong=detect_violations(data,center=0,sigma=2,last_n=None,rules=[1])
    fixed=detect_violations(data,center=0,sigma=1,last_n=None,rules=[1])
    assert wrong[1]==[] and fixed[1]==[2]
    sequence=np.array([2.2,2.5,-.1])
    one=detect_violations(sequence,0,1,last_n=None,rules=[1])
    two=detect_violations(sequence,0,1,last_n=None,rules=[5])
    assert one[1]==[] and sorted(two[5])==[0,1,2]
    assert sum(v>2 for v in sequence)==2
    return [result('repair_rule_sigma',wrong=wrong,repaired=fixed,oracle_indices=[2]),
            result('enable_two_of_three',wrong_rule=one,repaired_rule=two,independent_high_count=2,rule_mapping='spc-lib rule 5: two of three at >=2 sigma on same side; marks entire window')]

def capability():
    data=np.tile([-1.,1.],10).reshape(-1,1);chart=IMRChart(data).fit()
    wrong=chart.capability(usl=.006,lsl=-.006);fixed=chart.capability(usl=6,lsl=-6)
    assert math.isclose(fixed['cp'],1.128,rel_tol=1e-12) and fixed['cp']==fixed['cpk']
    assert wrong['cp']<.01
    shifted=IMRChart(data+.6).fit().capability(usl=6,lsl=-6)
    assert math.isclose(shifted['cp'],1.128,rel_tol=1e-12)
    assert math.isclose(shifted['cpk'],1.0152,rel_tol=1e-12) and shifted['cpk']<shifted['cp']
    return [result('repair_specification_unit',wrong=wrong,repaired=fixed,oracle_sigma=2/1.128),
            result('separate_cp_cpk',centered=fixed,shifted=shifted,oracle_cp=1.128,oracle_shifted_cpk=1.0152)]

def spc_fdc():
    metrics=pd.DataFrame({'wafer':[f'W{i}' for i in range(6)],'metric':[-.1,.1,-.1,.1,3.,4.]})
    chart=IMRChart(metrics[['metric']].to_numpy()).fit(baseline_mask=np.arange(6)<4)
    metrics=metrics.assign(ooc=metrics.metric>chart.ucl_main)
    trace_rows=[]
    for i,mean in enumerate((0.,0.,0.,0.,5.,6.)):
        for t in range(4):trace_rows.append((f'W{i}',t,mean))
    table=features(pd.DataFrame(trace_rows,columns=['wafer','time','pressure'])).reset_index(names='wafer')
    reversed_table=table.iloc[::-1].reset_index(drop=True)
    wrong=metrics.assign(trace_mean=reversed_table['pressure__mean'])
    fixed=metrics.merge(reversed_table[['wafer','pressure__mean']],on='wafer',validate='one_to_one')
    assert wrong.loc[wrong.ooc,'trace_mean'].tolist()==[0.,0.]
    assert fixed.loc[fixed.ooc,'wafer'].tolist()==['W4','W5'] and fixed.loc[fixed.ooc,'pressure__mean'].tolist()==[5.,6.]
    duplicate=pd.concat([table,table.iloc[[0]]],ignore_index=True);rejected=False
    try:metrics.merge(duplicate,on='wafer',validate='one_to_one')
    except pd.errors.MergeError:rejected=True
    assert rejected
    return [result('repair_wafer_trace_join',wrong_ooc_trace_means=[0.,0.],repaired=fixed.to_dict(orient='records'),oracle_ooc_ids=['W4','W5']),
            result('reject_duplicate_trace_identity',duplicate_join_rejected=rejected,repaired_rows=len(fixed),oracle_rows=6,boundary='Correlation is an association, not proof of process causality')]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    def run():return {'06.02.01':traces(),'06.02.02':changepoints(),'06.02.03':rule_anomaly(),'06.02.04':online_drift(),'06.02.05':trace_classification(),
                      '06.03.01':control_chart(),'06.03.02':western_rules(),'06.03.03':capability(),'06.03.04':spc_fdc()}
    first,second=run(),run();assert first==second,'Deterministic repeat mismatch'
    versions={p:importlib.metadata.version(p) for p in ['tsfresh','ruptures','adtk','river','scikit-learn','scipy','pandas','spc-lib']}
    for sid,tasks in first.items():
        report=dict(scenario_id=sid,status='passed',tasks=tasks,repeat_runs=2,repeat_equal=True,versions=versions,
                    spc_bootstrap='Unchanged official release at seed_pypi_raw/l1_test_fab/spc-lib added to sys.path; actual module src.spc_lib, installed spc_lib 1.0.0 import fails without it.',
                    boundaries=['Artificial traces and fixed baselines; no live equipment or measured fab accuracy.','Only listed paths were executed; not all selected APIs.'])
        (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(sid,'passed',len(tasks),flush=True)

if __name__=='__main__':main()
