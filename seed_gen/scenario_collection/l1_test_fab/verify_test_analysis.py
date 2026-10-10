"""Manufactured ATE, wafer map, characterization and yield fixtures."""
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import struct
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import DBSCAN
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import confusion_matrix
from Semi_ATE import STDF
import wfmap

BASE=Path('seed_gen/scenario_collection/l1_test_fab');OUT=BASE/'runtime/test_analysis'
def task(identifier,**kw):return dict(id=identifier,status='passed',**kw)
def cn(s):b=s.encode('ascii');return bytes([len(b)])+b
def record(typ,sub,body,endian='<'):return struct.pack(endian+'HBB',len(body),typ,sub)+body

def write_stdf(path,endian='<'):
    data=record(0,10,bytes([2 if endian=='<' else 1,4]),endian)
    data+=record(2,10,struct.pack(endian+'BBI',1,1,0)+cn('W01'),endian)
    # D2 initially fails and later passes: first-pass and final yield differ.
    for partid,x,y,hard,soft,attempt in [('D1',0,0,1,1,1),('D2',1,0,2,10,1),('D3',0,1,2,20,1),('D2',1,0,1,1,2)]:
        body=struct.pack(endian+'BBBHHHhhI',1,1,0 if hard==1 else 8,1,hard,soft,x,y,10)+cn(partid)+cn(str(attempt))+b'\x00'
        data+=record(5,20,body,endian)
    path.write_bytes(data)
    return data

def parse(path):return list(STDF.records_from_file(str(path)))
def framing_valid(data,endian='<'):
    offset=0
    while offset<len(data):
        if len(data)-offset<4:return False
        n=struct.unpack(endian+'H',data[offset:offset+2])[0];offset+=4+n
        if offset>len(data):return False
    return offset==len(data)

def stdf_decode():
    path=OUT/'little.stdf';data=write_stdf(path)
    records=parse(path); rows=[r.to_dict() for r in records if r.id=='PRR']
    assert [r.id for r in records]==['FAR','WIR','PRR','PRR','PRR','PRR']
    assert [(r['PART_ID'],r['HARD_BIN'],r['X_COORD'],r['Y_COORD']) for r in rows]==[('D1',1,0,0),('D2',2,1,0),('D3',2,0,1),('D2',1,1,0)]
    assert not framing_valid(data,'>') and framing_valid(data,'<')
    big=OUT/'big.stdf';write_stdf(big,'>');bigrows=[r.to_dict() for r in parse(big) if r.id=='PRR']
    assert bigrows==rows
    truncated=data[:-2];assert not framing_valid(truncated) and framing_valid(data)
    # Independent record framing rejects truncation even if a parser stops silently.
    assert len(rows)==4
    return [task('repair_stdf_endian',wrong_endian_framing=False,repaired_records=6,parts=rows,big_endian_equal=True),
            task('reject_truncated_stdf',truncated_bytes=len(truncated),valid_bytes=len(data),truncation_rejected=True,oracle_records=6)]

def ate_table():
    path=OUT/'canonical.stdf';write_stdf(path)
    return pd.DataFrame([r.to_dict() for r in parse(path) if r.id=='PRR']).assign(lot_id='L01',wafer_id='W01')

def canonical():
    frame=ate_table(); key=['lot_id','wafer_id','PART_ID'];frame=frame.assign(attempt=frame['PART_TXT'].astype(int))
    assert frame.duplicated(key).any() and not frame.duplicated(key+['attempt']).any()
    final=frame.sort_values('attempt').drop_duplicates(key,keep='last')
    assert len(final)==3 and final.set_index('PART_ID').loc['D2','HARD_BIN']==1
    payload=final[['lot_id','wafer_id','PART_ID','X_COORD','Y_COORD','HARD_BIN','attempt']].to_dict(orient='records')
    path=OUT/'canonical.json';final.to_json(path,orient='records');restored=pd.read_json(path)
    assert restored[key].to_dict(orient='records')==final[key].to_dict(orient='records')
    wrong=frame.assign(lot_id='L02');mixed=pd.concat([frame,wrong],ignore_index=True)
    assert len(mixed.drop_duplicates(['PART_ID','attempt']))==4 and len(mixed.drop_duplicates(key+['attempt']))==8
    return [task('repair_retest_key',raw_records=4,final_dies=payload,json_rows=len(restored)),
            task('repair_cross_lot_identity',wrong_unique=4,repaired_unique=8,canonical_key=key+['attempt'])]

def bin_yield():
    frame=ate_table().assign(attempt=lambda x:x.PART_TXT.astype(int));key=['lot_id','wafer_id','PART_ID']
    first=frame.sort_values('attempt').drop_duplicates(key,keep='first');final=frame.sort_values('attempt').drop_duplicates(key,keep='last')
    wrong=float((frame.HARD_BIN==1).mean());first_y=float((first.HARD_BIN==1).mean());last_y=float((final.HARD_BIN==1).mean())
    assert wrong==.5 and first_y==1/3 and last_y==2/3
    hard=final.HARD_BIN.value_counts().to_dict();soft=final.SOFT_BIN.value_counts().to_dict()
    assert hard=={1:2,2:1} and soft=={1:2,20:1}
    assert 10 not in soft and 20 not in hard
    return [task('repair_yield_denominator',wrong_record_yield=wrong,first_pass_yield=first_y,final_yield=last_y,die_count=3),
            task('separate_hard_soft_bins',hard_bin_counts=hard,soft_bin_counts=soft,oracle_total=3,wrong_soft10_from_retest_removed=True)]

class WfFrame(pd.DataFrame):
    """Instance-local compatibility bridge: wfmap1 calls pandas pivot positionally."""
    @property
    def _constructor(self):return WfFrame
    def pivot(self,*args,**kwargs):
        if args:
            if kwargs or len(args)!=3:raise TypeError('Expected wfmap (row,col,value)')
            kwargs=dict(zip(('index','columns','values'),args))
        # pandas3 preserves object dtype after categorical replace; plotted
        # matrix contains only numeric codes/values, so require float explicitly.
        return super().pivot(**kwargs).astype(float)

def wafer_map():
    base=pd.DataFrame({'MAP_ROW':[0,0,1,1],'MAP_COL':[0,1,0,1],'value':[1.,2.,3.,4.],'bin':['P','P','F','P']})
    import_failed=False
    try:wfmap.num_heatmap(base,'value',vlim=(1,4))
    except TypeError:import_failed=True
    assert import_failed;plt.close('all')
    # A bad coordinate map transposes the independent expected matrix.
    wrong=base.rename(columns={'MAP_ROW':'TMP','MAP_COL':'MAP_ROW'}).rename(columns={'TMP':'MAP_COL'})
    axbad=wfmap.num_heatmap(WfFrame(wrong),'value',vlim=(1,4));wrongmatrix=np.asarray(axbad.collections[0].get_array()).reshape(2,2).tolist();plt.close('all')
    ax=wfmap.num_heatmap(WfFrame(base),'value',vlim=(1,4));matrix=np.asarray(ax.collections[0].get_array()).reshape(2,2).tolist()
    assert wrongmatrix==[[1.,3.],[2.,4.]] and matrix==[[1.,2.],[3.,4.]]
    image=OUT/'wafer_numeric.png';ax.figure.savefig(image,dpi=120);plt.close('all');assert image.stat().st_size>1000
    frame=WfFrame(base.copy());ax,counts,labels,codes=wfmap.cat_heatmap(frame,'bin',verbose=True,qty_limit=10)
    assert counts.to_dict()=={'P':3,'F':1} and codes['P']!=codes['F']
    ax.figure.savefig(OUT/'wafer_bins.png',dpi=120);plt.close('all')
    return [task('repair_wafer_coordinates',wrong=wrongmatrix,repaired=matrix,plain_pandas3_rejected=True,compatibility='WfFrame.pivot maps three positional args to pandas keyword-only API and numeric plotting matrix to float; package source unchanged'),
            task('verify_bin_map_legend',counts=counts.to_dict(),codes=codes,labels=labels,oracle_pass=3,oracle_fail=1)]

def spatial_cluster():
    points=np.array([[0,0],[0,1],[1,0],[1,1],[5,5],[5,6],[6,5],[6,6],[10,0]],float)
    wrong=DBSCAN(eps=20,min_samples=3).fit_predict(points);correct=DBSCAN(eps=1.1,min_samples=3).fit_predict(points)
    assert len(set(wrong))==1 and correct.tolist()==[0,0,0,0,1,1,1,1,-1]
    wrong_unit=DBSCAN(eps=1.1,min_samples=3).fit_predict(points*1000)
    assert all(wrong_unit==-1)
    repaired=DBSCAN(eps=1.1,min_samples=3).fit_predict(points*1000/1000)
    assert repaired.tolist()==correct.tolist()
    return [task('repair_spatial_radius',wrong=wrong.tolist(),repaired=correct.tolist(),oracle_components=[[0,1,2,3],[4,5,6,7]],oracle_noise=[8]),
            task('repair_die_coordinate_units',wrong=wrong_unit.tolist(),repaired=repaired.tolist(),unit='die_pitch')]

def pattern_classification():
    def feature(kind,offset=0):
        grid=np.zeros((7,7)); coords={'center':[(3,3),(3,2),(2,3)],'edge':[(0,2),(0,3),(0,4)],'scratch':[(i,i) for i in range(7)]}[kind]
        for y,x in coords:grid[y,x]=1
        # Explicit geometric summaries, not hidden trained image embedding.
        return [grid[2:5,2:5].sum()/grid.sum(), (grid[0].sum()+grid[-1].sum()+grid[:,0].sum()+grid[:,-1].sum())/grid.sum(),np.trace(grid)/grid.sum(),grid.sum()+offset]
    kinds=['center','edge','scratch'];train=np.array([feature(k,off) for k in kinds for off in (0,.1,.2)]);labels=np.repeat(kinds,3);test=np.array([feature(k,.15) for k in kinds])
    wrong=DecisionTreeClassifier(max_depth=3,random_state=4).fit(train,np.roll(labels,3)).predict(test).tolist()
    model=DecisionTreeClassifier(max_depth=3,random_state=4).fit(train,labels);correct=model.predict(test).tolist()
    assert wrong!=kinds and correct==kinds
    wrongscale=test.copy();wrongscale[:,0]=1-wrongscale[:,0];bad=model.predict(wrongscale).tolist()
    assert bad!=kinds
    matrix=confusion_matrix(kinds,correct,labels=kinds).tolist();assert matrix==[[1,0,0],[0,1,0],[0,0,1]]
    return [task('repair_pattern_labels',wrong=wrong,repaired=correct,oracle_labels=kinds,features='center fraction, edge fraction, diagonal fraction, fail count'),
            task('repair_pattern_feature_definition',wrong=bad,repaired=correct,confusion_matrix=matrix,boundary='Artificial 7x7 maps; no WM-811K training or measured benchmark accuracy')]

def shmoo():
    rows=[(v,f,f<=100*v) for v in (1.,1.1,1.2) for f in (90.,100.,110.,120.)]
    frame=pd.DataFrame(rows,columns=['voltage','frequency','passed']);grid=frame.pivot(index='voltage',columns='frequency',values='passed')
    expected=[[True,True,False,False],[True,True,True,False],[True,True,True,True]]
    assert grid.to_numpy().tolist()==expected
    wrong=frame.assign(frequency=frame.frequency/1000);assert wrong[wrong.passed].groupby('voltage').frequency.max().tolist()==[.1,.11,.12]
    safe=frame[frame.passed].groupby('voltage').frequency.max().to_dict();assert safe=={1.:100.,1.1:110.,1.2:120.}
    duplicates=pd.concat([frame,frame.iloc[[0]]]);rejected=False
    try:duplicates.pivot(index='voltage',columns='frequency',values='passed')
    except ValueError:rejected=True
    assert rejected
    return [task('repair_shmoo_units',wrong_max=[.1,.11,.12],repaired_max=safe,oracle_grid=expected,units=['V','MHz']),
            task('reject_duplicate_shmoo_cell',duplicate_cell_rejected=rejected,repaired_shape=list(grid.shape),oracle_unique_cells=12)]

def limits():
    values=np.array([9.7,9.8,9.9,10.,10.1,10.2,10.3]);lower,upper=9.5,10.5
    median=float(np.median(values));mad=float(stats.median_abs_deviation(values,scale=1))
    assert math.isclose(median,10) and math.isclose(mad,.2,abs_tol=1e-12)
    wrong_lower,wrong_upper=10.5,9.5;wrong_pass=(values>=wrong_lower)&(values<=wrong_upper);assert not wrong_pass.any()
    good=(values>=lower)&(values<=upper);assert good.all()
    guarded=(values>=lower+.3)&(values<=upper-.3);assert guarded.tolist()==[False,True,True,True,True,True,False]
    return [task('repair_limit_order',wrong_yield=0.,repaired_yield=float(good.mean()),oracle_accepted=7,median=median,mad=mad),
            task('apply_limit_guardband',accepted=guarded.tolist(),oracle_count=5,reject_ids=[0,6],guardband=.3)]

def pat():
    values=np.array([9.,10.,11.,10.,9.,11.,10.,50.]);baseline=values[:-1]
    wrong=stats.zscore(values);correct=(values-np.median(baseline))/stats.median_abs_deviation(baseline,scale='normal')
    assert not any(abs(wrong)>3) and np.flatnonzero(abs(correct)>3).tolist()==[7]
    # Site offset must be kept separate from within-site outlier.
    frame=pd.DataFrame({'site':['A']*7+['B']*7,'value':list(baseline)+list(baseline+100)})
    center=frame.groupby('site').value.transform('median');residual=frame.value-center
    assert residual.abs().max()==1 and frame.groupby('site').value.median().to_dict()=={'A':10.,'B':110.}
    return [task('repair_pat_baseline',wrong_z=wrong.tolist(),repaired_flags=np.flatnonzero(abs(correct)>3).tolist(),oracle_outlier=[7]),
            task('separate_site_offset',site_centers={'A':10.,'B':110.},within_site_max_abs=1.,oracle_relative_outliers=0)]

def lot_comparison():
    a=np.array([0,0,1,1,2,2],float);b=a+2
    res=stats.ttest_ind(a,b,equal_var=False)
    # Welch t oracle from independent sums of squares, no test routine.
    va=sum((x-sum(a)/6)**2 for x in a)/5;vb=sum((x-sum(b)/6)**2 for x in b)/5
    expected=(sum(a)/6-sum(b)/6)/math.sqrt(va/6+vb/6)
    assert math.isclose(res.statistic,expected,rel_tol=1e-12) and res.pvalue<.01
    wrong=stats.ttest_ind(a,b/1000,equal_var=False);assert not math.isclose(wrong.statistic,expected,rel_tol=1e-2)
    table=np.array([[8,2],[2,8]]);fisher=stats.fisher_exact(table)
    # Hypergeometric margins enumerate two-sided probability independently.
    probs=[math.comb(10,k)*math.comb(10,10-k)/math.comb(20,10) for k in range(11)]
    p=sum(p for p in probs if p<=probs[8]+1e-15)
    assert math.isclose(fisher.statistic,16,rel_tol=1e-12) and math.isclose(fisher.pvalue,p,rel_tol=1e-12)
    return [task('repair_lot_measurement_unit',wrong_t=float(wrong.statistic),repaired_t=float(res.statistic),oracle_t=expected,pvalue=float(res.pvalue)),
            task('compare_split_lot_yield',table=table.tolist(),odds_ratio=float(fisher.statistic),pvalue=float(fisher.pvalue),independent_exact_p=p)]

def cross_domain():
    tests=pd.DataFrame({'wafer':['A','B','C','D'],'yield':[.9,.8,.5,.4]});process=pd.DataFrame({'wafer':['D','C','B','A'],'pressure':[4.,3.,2.,1.]})
    bad=stats.pearsonr(tests['yield'],process.pressure).statistic
    joined=tests.merge(process,on='wafer',validate='one_to_one');good=stats.pearsonr(joined['yield'],joined.pressure).statistic
    assert bad>0 and good<-.97
    x=joined.pressure.to_numpy();y=joined['yield'].to_numpy();oracle=sum((x-x.mean())*(y-y.mean()))/math.sqrt(sum((x-x.mean())**2)*sum((y-y.mean())**2))
    assert math.isclose(good,oracle,rel_tol=1e-12)
    duplicate=pd.concat([process,process.iloc[[0]]]);rejected=False
    try:tests.merge(duplicate,on='wafer',validate='one_to_one')
    except pd.errors.MergeError:rejected=True
    assert rejected
    return [task('repair_cross_domain_join',wrong_correlation=float(bad),repaired_correlation=float(good),independent_oracle=float(oracle),joined=joined.to_dict(orient='records')),
            task('reject_rca_duplicate_key',duplicate_rejected=rejected,repaired_rows=4,boundary='Association only; four artificial wafers cannot prove pressure caused yield loss')]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    funcs={'05.01.01':stdf_decode,'05.01.02':canonical,'05.01.03':bin_yield,'05.02.01':wafer_map,'05.02.02':spatial_cluster,'05.02.03':pattern_classification,
           '05.03.01':shmoo,'05.03.02':limits,'05.04.01':pat,'05.05.01':lot_comparison,'05.05.02':cross_domain}
    for sid,fn in funcs.items():
        first,second=fn(),fn();assert first==second
        report=dict(scenario_id=sid,status='passed',tasks=first,repeat_runs=2,repeat_equal=True,
                    versions={p:importlib.metadata.version(p) for p in ['Semi-ATE-STDF','wfmap','pandas','scipy','scikit-learn']},
                    boundaries=['Artificial ATE records/maps/measurements; no production yield claim.','STDF source version identifies official tag0.1.33 despite upstream distribution metadata0.0.0.','wfmap1.0.3 requires explicit instance-local DataFrame pivot compatibility bridge on pandas3; unmodified release source.'])
        (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(sid,'passed',len(first),flush=True)

if __name__=='__main__':main()
