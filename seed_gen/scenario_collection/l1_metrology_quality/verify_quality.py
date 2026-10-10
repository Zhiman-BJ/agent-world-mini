"""Manufactured fab evidence ledgers; explicit independent checks, no causal claim."""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import networkx as nx
import numpy as np
import pandas as pd
import ruptures as rpt
from scipy.stats import ttest_ind,fisher_exact
from numpy.testing import assert_allclose

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/quality')

def timeline():
    rows=[{'event_id':'test','wafer':'W1','kind':'test','time':'2026-09-01T01:20:00Z','source':'test.csv'},
          {'event_id':'etch','wafer':'W1','kind':'process','time':'2026-09-01T09:05:00+08:00','source':'mes.csv'},
          {'event_id':'load','wafer':'W1','kind':'load','time':'2026-09-01T01:00:00Z','source':'mes.csv'}]
    df=pd.DataFrame(rows);df['utc']=pd.to_datetime(df['time'],utc=True)
    ordered=df.sort_values('utc');ids=ordered['event_id'].tolist()
    assert ids==['load','etch','test']
    graph=nx.DiGraph();graph.add_nodes_from((r['event_id'],r) for r in ordered.to_dict('records'))
    graph.add_edges_from(zip(ids[:-1],ids[1:]));assert nx.is_directed_acyclic_graph(graph)
    assert list(nx.topological_sort(graph))==ids
    delta=(ordered['utc'].iloc[-1]-ordered['utc'].iloc[0]).total_seconds();assert delta==1200
    wrong=df.copy();wrong.loc[wrong['event_id']=='etch','time']='2026-09-01T09:05:00Z'
    wrong['utc']=pd.to_datetime(wrong['time'],utc=True)
    assert wrong.sort_values('utc')['event_id'].tolist()!=ids
    repaired=pd.DataFrame(rows);repaired['utc']=pd.to_datetime(repaired['time'],utc=True)
    assert repaired.sort_values('utc')['event_id'].tolist()==ids
    graph.add_edge('test','load');assert not nx.is_directed_acyclic_graph(graph)
    graph.remove_edge('test','load');assert nx.is_directed_acyclic_graph(graph)
    saved=nx.node_link_data(graph)
    # Datetime persistence kept explicit to avoid serialization magically changing timezone.
    for node in saved['nodes']:node['utc']=node['utc'].isoformat()
    restored=nx.node_link_graph(json.loads(json.dumps(saved)))
    assert set(restored.edges)=={('load','etch'),('etch','test')}
    return dict(tasks=[dict(id='reconstruct_event_timeline',status='passed',ordered_ids=ids,elapsed_seconds=delta),
                       dict(id='repair_timezone_and_cycle',status='passed',wrong_order=wrong.sort_values('utc')['event_id'].tolist(),restored_edges=sorted(restored.edges))],
        oracle='Fixture chronology is00min load,05min etch,20min test inUTC; required directed edges load→etch→test and no cycle.',
        fixture='Three manufactured waferMES/test events from two source labels, explicit timezone offsets; no externalMES write.')

def lot_comparison():
    rows=[]
    # Product mix produces a Simpson reversal: B improves each stratum by2,
    # but its overall mean is worse because it contains more low-yield product.
    for lot,product,n,mean in [('A','easy',18,98),('A','hard',2,80),('B','easy',2,100),('B','hard',18,82)]:
        for j in range(n):rows.append(dict(lot=lot,product=product,yield_pct=mean+(-.5 if j%2 else .5),wafer=f'{lot}-{product}-{j}'))
    df=pd.DataFrame(rows)
    stratified=df.groupby(['lot','product'])['yield_pct'].mean().unstack('lot')
    differences=(stratified['B']-stratified['A']).values
    assert_allclose(differences,[2,2],atol=1e-12)
    naive=df.groupby('lot')['yield_pct'].mean()
    naive_delta=float(naive['B']-naive['A']);assert naive_delta<0
    corrected=float(np.mean(differences));assert_allclose(corrected,2,atol=1e-12)
    x=df.query("lot=='A' and product=='hard'")['yield_pct'].values
    y=df.query("lot=='B' and product=='hard'")['yield_pct'].values
    stat=ttest_ind(x,y,equal_var=False)
    expected=(sum(x)/len(x)-sum(y)/len(y))/math.sqrt(sum((v-x.mean())**2 for v in x)/(len(x)-1)/len(x)+sum((v-y.mean())**2 for v in y)/(len(y)-1)/len(y))
    assert_allclose(stat.statistic,expected,atol=1e-12)
    # Re-aggregation with fixed equal product weights is the stated estimand.
    return dict(tasks=[dict(id='compare_matched_lots',status='passed',within_product_delta=differences.tolist(),welch_t=float(stat.statistic)),
                       dict(id='repair_product_mix_bias',status='passed',wrong_unstratified_delta=naive_delta,standardized_delta=corrected)],
        oracle='Manufactured means Aeasy98/Ahard80/Beasy100/Bhard82 imply+2 within each product and equal-weight target+2; independent Welch formula.',
        fixture='Forty manufactured wafer yield summaries with deliberate unequal product mix. Association only; not randomization or causal process improvement.')

def changepoint():
    signal=np.r_[np.tile([-.2,.2],18),0.,np.tile([2.8,3.2],21),3.]
    assert len(signal)==80
    def sse(b):return float(np.sum((signal[:b]-signal[:b].mean())**2)+np.sum((signal[b:]-signal[b:].mean())**2))
    candidates=list(range(5,len(signal)-4));best=min(candidates,key=sse);assert best==37
    good=rpt.Dynp(model='l2',min_size=5,jump=1).fit(signal).predict(n_bkps=1)
    assert good==[37,80]
    wrong=rpt.Dynp(model='l2',min_size=5,jump=20).fit(signal).predict(n_bkps=1)
    assert wrong[0]!=37
    fixed=rpt.Dynp(model='l2',min_size=5,jump=1).fit(signal).predict(n_bkps=1)
    assert fixed==[37,80]
    before,after=signal[:37],signal[37:]
    contrast=ttest_ind(before,after,equal_var=False)
    expected=(before.mean()-after.mean())/math.sqrt(before.var(ddof=1)/len(before)+after.var(ddof=1)/len(after))
    assert_allclose(contrast.statistic,expected,atol=1e-12)
    return dict(tasks=[dict(id='detect_process_changepoint',status='passed',breakpoints=good,independent_sse_best=best,mean_shift=float(after.mean()-before.mean())),
                       dict(id='repair_detection_resolution',status='passed',wrong_breakpoints=wrong,repaired_breakpoints=fixed,welch_t=float(contrast.statistic))],
        oracle='Exhaustive independent two-segment SSE over5..75 uniquely chooses37; before/after Welch statistic computed from sums/variances.',
        fixture='Eighty manufactured process measurements with step at index37. A change point does not identify a cause or prove a process change impact.')

def fisher_two_sided(table):
    a,b=table[0];c,d=table[1];n=a+b+c+d;r=a+b;s=a+c
    def p(x):return math.comb(s,x)*math.comb(n-s,r-x)/math.comb(n,r)
    observed=p(a)
    return sum(p(x) for x in range(max(0,r-(n-s)),min(r,s)+1) if p(x)<=observed+1e-14)

def ranking():
    graph=nx.DiGraph()
    records=[]
    for chamber,nfail in [('A',8),('B',2)]:
        for i in range(10):
            wafer=f'{chamber}{i}';failed=i<nfail
            graph.add_edge('chamber:'+chamber,'wafer:'+wafer,relation='processed')
            graph.add_edge('wafer:'+wafer,'test:'+wafer,relation='measured')
            records.append(dict(wafer=wafer,chamber=chamber,failed=failed))
    graph.add_node('chamber:C',role='unconnected_alternative')
    df=pd.DataFrame(records);cross=pd.crosstab(df.chamber,df.failed)
    table=[[int(cross.loc['A',True]),int(cross.loc['A',False])],[int(cross.loc['B',True]),int(cross.loc['B',False])]]
    result=fisher_exact(table)
    assert_allclose(result.statistic,16,atol=1e-12)
    assert_allclose(result.pvalue,fisher_two_sided(table),atol=1e-12)
    assert 'chamber:A' in nx.ancestors(graph,'test:A0')
    assert 'chamber:C' not in nx.ancestors(graph,'test:A0')
    assert df.query("chamber=='A' and failed==False").shape[0]==2
    # Wrong genealogy assignment destroys the fixture association.
    wrong=df.copy();wrong['chamber']=['A' if j%2==0 else 'B' for j in range(20)]
    wrong_table=pd.crosstab(wrong.chamber,wrong.failed)
    wrong_or=float(fisher_exact([[int(wrong_table.loc['A',True]),int(wrong_table.loc['A',False])],[int(wrong_table.loc['B',True]),int(wrong_table.loc['B',False])]]).statistic)
    assert wrong_or==1
    repaired=df.copy();correct=pd.crosstab(repaired.chamber,repaired.failed)
    assert int(correct.loc['A',True])==8 and int(correct.loc['B',True])==2
    return dict(tasks=[dict(id='rank_supported_root_cause_candidates',status='passed',candidate='chamber:A',odds_ratio=float(result.statistic),pvalue=float(result.pvalue),counterevidence_A_pass=2,excluded_unconnected='chamber:C'),
                       dict(id='repair_genealogy_join',status='passed',wrong_odds_ratio=wrong_or,repaired_failure_counts=[8,2])],
        oracle='Known injection8/10 failures inA vs2/10 inB, OR16; two-sided hypergeometric sum independently computed with integer combinations. Only ancestors admitted.',
        fixture='Manufactured20-wafer process genealogy and fail/pass ledger with counterexamples. Candidate association ranking, expressly not causal diagnosis.')

def capa():
    graph=nx.DiGraph()
    graph.add_node('issue',kind='quality_issue',source='issue_001.json',revision=1)
    graph.add_node('cause',kind='hypothesis',confirmed=False,source='analysis_001.json')
    graph.add_node('action',kind='corrective_action',status='implemented',source='maintenance_001.json')
    graph.add_node('verification',kind='verification',passed=True,source='test_001.json',sha256=hashlib.sha256(b'fixed test artifact').hexdigest())
    graph.add_node('followup',kind='effectiveness_followup',passed=True,source='audit_001.json',window_days=30)
    graph.add_edges_from([('issue','cause'),('cause','action'),('action','verification'),('verification','followup')])
    required={'issue','cause','action','verification','followup'}
    def complete(g):
        if not required<=set(g):return False
        return (nx.is_directed_acyclic_graph(g) and nx.has_path(g,'issue','followup') and
                all(g.nodes[n].get('source') for n in required) and g.nodes['verification'].get('passed') is True and
                g.nodes['followup'].get('passed') is True and g.nodes['followup'].get('window_days',0)>=30)
    assert complete(graph)
    rows=pd.DataFrame([dict(evidence_id=n,**attrs) for n,attrs in graph.nodes(data=True)])
    assert set(rows['evidence_id'])==required
    saved=nx.node_link_data(graph);restored=nx.node_link_graph(json.loads(json.dumps(saved)))
    assert complete(restored) and restored.nodes['cause']['confirmed'] is False
    wrong=graph.copy();wrong.remove_node('followup');assert not complete(wrong)
    wrong.add_node('followup',**graph.nodes['followup']);wrong.add_edge('verification','followup');assert complete(wrong)
    return dict(tasks=[dict(id='assemble_capa_evidence',status='passed',required_evidence=sorted(required),unconfirmed_cause_preserved=True,json_roundtrip=True),
                       dict(id='repair_missing_effectiveness_evidence',status='passed',missing_followup_rejected=True,restored_from_fixture=True)],
        oracle='Fixed evidence contract: five named evidence nodes, source refs, acyclic issue→followup chain, passing verification and30day effectiveness; assertion independent of graph traversal implementation.',
        fixture='Manufactured local CAPA evidence with explicit hypothesis not confirmed. Repair attaches existing fixture evidence, never fabricates a test or changes externalQMS status.')

FUNCTIONS={'08.02.01':timeline,'08.02.02':lot_comparison,'08.02.03':changepoint,'08.02.04':ranking,'08.02.05':capa}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);a=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    for sid,fn in FUNCTIONS.items():
        if a.scene and a.scene!=sid:continue
        data=fn();data.update(scenario_id=sid,status='passed',versions={n:importlib.metadata.version(n) for n in ['networkx','pandas','scipy','ruptures','numpy']},executed_script=Path(__file__).as_posix())
        (BASE/f'{sid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(sid,data['tasks'],flush=True)
if __name__=='__main__':main()
