"""Fixed manufactured reliability fixtures; no field lifetime/qualification claim."""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
os.environ['MPLBACKEND']='Agg'
import numpy as np
import pandas as pd
from numpy.testing import assert_allclose

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/reliability')

def weibull_mle(times):
    # Independent scalar score equation, no library fitter or scipy optimizer.
    logs=np.log(times)
    def score(beta):
        weights=np.exp(beta*(logs-logs.max()))
        return 1/beta+logs.mean()-(weights*logs).sum()/weights.sum()
    lo,hi=.05,20.
    assert score(lo)>0 and score(hi)<0
    for _ in range(100):
        mid=(lo+hi)/2
        if score(mid)>0: lo=mid
        else: hi=mid
    beta=(lo+hi)/2
    alpha=np.exp(logs.max()+np.log(np.exp(beta*(logs-logs.max())).mean())/beta)
    return float(alpha),float(beta)

def lifetime():
    from reliability.Fitters import Fit_Weibull_2P
    times=np.array([58,75,36,52,63,65,22,17,28,64,23,40,73,45,52,36,52,60,13,55,82,55,34,57,23,42,66,35,34,25.])
    expected=weibull_mle(times)
    def fit(t): return Fit_Weibull_2P(failures=t,show_probability_plot=False,print_results=False)
    model=fit(times)
    assert_allclose([model.alpha,model.beta],expected,rtol=2e-4)
    t=np.array([20.,50.,80.]); sf=np.exp(-(t/expected[0])**expected[1])
    assert_allclose(model.distribution.SF(xvals=t,show_plot=False),sf,rtol=2e-4)
    b10=expected[0]*(-math.log(.9))**(1/expected[1])
    assert_allclose(model.distribution.quantile(.1),b10,rtol=2e-4)
    wrong=fit(times*1000)
    assert abs(wrong.alpha/expected[0]-1)>100
    repaired=fit(times*1000/1000)
    assert_allclose([repaired.alpha,repaired.beta],expected,rtol=2e-4)
    assert_allclose(repaired.distribution.SF(xvals=t,show_plot=False),sf,rtol=2e-4)
    return {'tasks':[{'id':'fit_lifetime','status':'passed','alpha_beta':expected,'b10_hours':float(b10)},
                     {'id':'repair_time_units','status':'passed','wrong_alpha':float(wrong.alpha),'repaired_alpha':float(repaired.alpha)}],
            'oracle':'Weibull MLE scalar score solved by bisection independently of reliability; analytic SF/B10.',
            'fixture':'30 fixed demonstration failure hours; manufactured/documented sample, not semiconductor field observations.'}

def censoring():
    from lifelines import KaplanMeierFitter, ExponentialFitter
    t=np.array([2.,3.,4.,5.,6.,8.]); event=np.array([1,0,1,0,1,0])
    km=KaplanMeierFitter().fit(t,event_observed=event)
    expected=np.array([5/6,5/8,5/16])
    assert_allclose(km.survival_function_at_times([2,4,6]).values,expected,atol=1e-12)
    wrong=KaplanMeierFitter().fit(t,event_observed=np.ones(6))
    assert abs(float(wrong.survival_function_at_times(6).iloc[0])-expected[-1])>.1
    repaired=KaplanMeierFitter().fit(t,event_observed=event)
    assert_allclose(repaired.survival_function_at_times([2,4,6]).values,expected,atol=1e-12)
    exponential=ExponentialFitter().fit(t,event_observed=event)
    target=t.sum()/event.sum()
    assert_allclose(exponential.lambda_,target,rtol=1e-4)
    assert_allclose(exponential.params_.loc['lambda_'],target,rtol=1e-4)
    wrong_fit=ExponentialFitter().fit(t,event_observed=np.ones(6))
    assert abs(wrong_fit.lambda_/target-1)>.4
    return {'tasks':[{'id':'censor_km','status':'passed','survival':expected.tolist(),'events':3,'censored':3},
                     {'id':'repair_censor_exposure','status':'passed','expected_mean_hours':float(target),'fitted_mean_hours':float(exponential.lambda_),'wrong_mean_hours':float(wrong_fit.lambda_)}],
            'oracle':'Exact risk sets: 5/6, (5/6)*(3/4)=5/8, (5/8)*(1/2)=5/16; exponential MLE=sum exposure/events.',
            'fixture':'Six manufactured fixed unit endpoints, event flags supplied by fixture ledger.'}

def regression():
    from lifelines import WeibullAFTFitter
    # Matched quantiles give an exactly known twofold lifetime ratio.
    p=(np.arange(1,301)-.5)/300
    baseline=100*np.sqrt(-np.log1p(-p))
    df=pd.DataFrame({'hours':np.r_[baseline,baseline*2], 'event':1, 'low_stress':np.repeat([0,1],300)})
    model=WeibullAFTFitter().fit(df,'hours','event',formula='low_stress')
    query=pd.DataFrame({'low_stress':[0,1]})
    med=model.predict_median(query).values
    assert_allclose(med[1]/med[0],2,rtol=2e-4)
    coefficient=float(model.params_.loc[('lambda_','low_stress')])
    assert_allclose(coefficient,math.log(2),atol=2e-4)
    survival=model.predict_survival_function(query,times=[100]).values[0]
    assert survival[1]>survival[0]
    # Wrong upstream mapping of physical groups to encoded covariate reverses risk.
    wrong_query=pd.DataFrame({'low_stress':[1,0]})
    wrong=model.predict_median(wrong_query).values
    assert wrong[1]/wrong[0]<.6
    repaired=model.predict_median(query).values
    assert_allclose(repaired[1]/repaired[0],2,rtol=2e-4)
    repaired_sf=model.predict_survival_function(query,times=[100]).values[0]
    assert repaired_sf[1]>repaired_sf[0]
    return {'tasks':[{'id':'fit_aft_covariates','status':'passed','log_time_ratio':coefficient,'median_hours':med.tolist()},
                     {'id':'repair_covariate_encoding','status':'passed','wrong_ratio':float(wrong[1]/wrong[0]),'repaired_ratio':float(repaired[1]/repaired[0])}],
            'oracle':'Matched Weibull quantiles multiplied by2 imply coefficient ln2 and every conditional lifetime quantile ratio2, independently of fitting.',
            'fixture':'600 deterministic quantile samples in two manufactured stress groups; this checks AFT math, not causal effect or real HTOL population.'}

def accelerated():
    from reliability.ALT_fitters import Fit_Exponential_Exponential
    unit=-np.log1p(-(np.arange(1,61)-.5)/60); unit=unit/unit.mean()
    stress=np.repeat([400.,450.,500.],len(unit))
    times=np.tile(unit,3)*np.exp(3000/stress)
    def fit(s,use):
        return Fit_Exponential_Exponential(failures=times,failure_stress=s,use_level_stress=use,
            show_probability_plot=False,show_life_stress_plot=False,print_results=False)
    good=fit(stress,350.)
    expected=math.exp(3000/350)
    assert good.success
    assert_allclose([good.a,good.b],[3000,1],rtol=3e-3)
    assert_allclose(good.mean_life,expected,rtol=3e-3)
    assert_allclose(good.distribution_at_use_stress.SF(xvals=[1000.,5000.],show_plot=False),
                    np.exp(-np.array([1000.,5000.])/expected),rtol=3e-3)
    wrong=fit(stress-273.15,350.-273.15)
    assert abs(wrong.mean_life/expected-1)>.2
    repaired=fit((stress-273.15)+273.15,350.)
    assert_allclose(repaired.mean_life,expected,rtol=3e-3)
    return {'tasks':[{'id':'fit_arrhenius_alt','status':'passed','a':float(good.a),'b':float(good.b),'mean_at_350K':float(good.mean_life)},
                     {'id':'repair_kelvin_stress','status':'passed','wrong_mean':float(wrong.mean_life),'repaired_mean':float(repaired.mean_life)}],
            'oracle':'Each stress group has exact sample mean exp(3000/T); exponential MLE attains these three group means at a3000,b1; use350K mean exp(3000/350).',
            'fixture':'Manufactured180 failure hours at400/450/500K. No failure-mechanism transition or real device extrapolation verified.'}

def repairable():
    from reliability.Repairable_systems import MCF_nonparametric
    from surpyval.recurrent import HPP
    # Each system has two repairs and administrative retirement at10h.
    ledger=[[2.,6.,10.],[3.,8.,10.]]
    mcf=MCF_nonparametric(data=ledger,print_results=False,show_plot=False)
    assert_allclose(np.asarray(mcf.MCF,dtype=float),[.5,1.,1.5,2.],atol=1e-12)
    x=np.array([2.,6.,10.,3.,8.,10.]); i=np.array([0,0,0,1,1,1]); c=np.array([0,0,1,0,0,1])
    hpp=HPP.fit(x,i=i,c=c)
    rate=4/20
    assert_allclose(hpp.mcf([5.,10.]),[rate*5,rate*10],atol=1e-10)
    wrong=MCF_nonparametric(data=[[2.,6.],[3.,8.]],print_results=False,show_plot=False)
    assert abs(wrong.MCF[-1]-2)>.5
    restored=MCF_nonparametric(data=ledger,print_results=False,show_plot=False)
    assert_allclose(restored.MCF[-1],2,atol=1e-12)
    return {'tasks':[{'id':'repair_counts_and_exposure','status':'passed','mcf':list(map(float,mcf.MCF)),'hpp_rate':rate,'total_exposure_hours':20},
                     {'id':'repair_retirement_endpoints','status':'passed','wrong_final_mcf':float(wrong.MCF[-1]),'repaired_final_mcf':float(restored.MCF[-1])}],
            'oracle':'Four repairs over20 system-hours imply HPP rate.2/h; while both systems at risk each repair adds1/2 to nonparametricMCF.',
            'fixture':'Two manufactured equipment repair ledgers; final endpoint is administrative censor, not a repair. reliability nested lists -> SurPyval x/i/c checked.'}

def degradation():
    from surpyval.degradation import DegradationAnalysis, DegradationModel
    time=np.tile([100.,200.,300.,400.],4)
    unit=np.repeat([1,2,3,4],4); slopes=np.repeat([.31,.28,.44,.37],4)
    value=10+slopes*time; threshold=150.
    model=DegradationAnalysis.fit(time,value,unit,threshold=threshold,path='linear')
    expected=(threshold-10)/np.array([.31,.28,.44,.37])
    assert_allclose(model.pseudo_failure_times,expected,rtol=1e-10)
    pred=model.predict_failure_time([100,200,300],[45,80,115])
    rul=model.predict_remaining_life([100,200,300],[45,80,115])
    assert_allclose([pred,rul],[400,100],atol=1e-8)
    wrong=DegradationAnalysis.fit(time,value,unit,threshold=15.,path='linear')
    assert np.max(np.abs(wrong.pseudo_failure_times/expected-1))>.9
    repaired=DegradationAnalysis.fit(time,value,unit,threshold=threshold,path='linear')
    restored=DegradationModel.from_dict(json.loads(json.dumps(repaired.to_dict(),allow_nan=False)))
    assert_allclose(restored.pseudo_failure_times,expected,rtol=1e-10)
    assert_allclose(restored.sf([300,400,500]),repaired.sf([300,400,500]),atol=1e-12)
    return {'tasks':[{'id':'fit_degradation_paths','status':'passed','pseudo_failure_hours':expected.tolist(),'new_unit_failure_hours':float(pred),'new_unit_rul_hours':float(rul)},
                     {'id':'repair_degradation_threshold','status':'passed','wrong_pseudo_failure_hours':wrong.pseudo_failure_times.tolist(),'roundtrip':True}],
            'oracle':'Known linear path10+b*t crosses150 at140/b; unseen trajectory10+.35t fails at400h with100h remaining from300h.',
            'fixture':'Four deterministic noiseless resistance-drift tracks. Pseudo failure times are extrapolations, not observed failures; no interval calibration claim.'}

FUNCTIONS={'08.01.01':lifetime,'08.01.02':censoring,'08.01.03':regression,'08.01.04':accelerated,'08.01.05':repairable,'08.01.06':degradation}

def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);args=p.parse_args()
    BASE.mkdir(parents=True,exist_ok=True)
    versions={n:importlib.metadata.version(n) for n in ['reliability','surpyval','lifelines','numpy','scipy','pandas']}
    for sid,fn in FUNCTIONS.items():
        if args.scene and sid!=args.scene:continue
        result=fn();result.update(scenario_id=sid,status='passed',versions=versions,
            executed_script=Path(__file__).as_posix(),boundaries=['Fixed fixtures only; no real semiconductor reliability, complete API coverage or agent environment implemented.'])
        path=BASE/f'{sid}.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(sid,result['tasks'],flush=True)

if __name__=='__main__':main()
