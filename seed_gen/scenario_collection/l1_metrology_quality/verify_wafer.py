"""Manufactured wafer map, spatial patterns and upstream association fixtures."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import struct
import numpy as np
import pandas as pd
import networkx as nx
from scipy.stats import binned_statistic,fisher_exact
from sklearn.cluster import DBSCAN
from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import confusion_matrix
from sklearn.metrics.pairwise import cosine_similarity
from numpy.testing import assert_allclose,assert_array_equal
BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/wafer')
def result(a,b,oracle,fixture):return dict(tasks=[dict(id=a[0],status='passed',**a[1]),dict(id=b[0],status='passed',**b[1])],oracle=oracle,fixture=fixture)

def binmap():
    from Semi_ATE import STDF
    from wafermap import WaferMap
    def cn(s):b=s.encode();return bytes([len(b)])+b
    def rec(typ,sub,body):return struct.pack('<HBB',len(body),typ,sub)+body
    source=[('D1',0,0,1,1),('D2',1,0,2,1),('D3',0,1,2,1),('D2',1,0,1,2)]
    data=rec(0,10,bytes([2,4]))+rec(2,10,struct.pack('<BBI',1,1,0)+cn('W1'))
    for partid,x,y,bin_,attempt in source:
        body=struct.pack('<BBBHHHhhI',1,1,0 if bin_==1 else 8,1,bin_,bin_,x,y,10)+cn(partid)+cn(str(attempt))+b'\x00'
        data+=rec(5,20,body)
    path=BASE/'binmap.stdf';path.write_bytes(data)
    frame=pd.DataFrame([r.to_dict() for r in STDF.records_from_file(str(path)) if r.id=='PRR']);frame['attempt']=frame['PART_TXT'].astype(int)
    final=frame.sort_values('attempt').drop_duplicates(['PART_ID'],keep='last')
    bins={(int(r.X_COORD),int(r.Y_COORD)):int(r.HARD_BIN) for r in final.itertuples()}
    assert bins=={(0,0):1,(1,0):1,(0,1):2}
    wm=WaferMap(wafer_radius=40,cell_size=(10,10),cell_margin=(0,0),grid_offset=(0,0),cell_origin=(0,0),edge_exclusion=0,coverage='full')
    for (x,y),bin_ in bins.items():wm.style_cell((x,y),{'fillColor':'#00aa00' if bin_==1 else '#aa0000','fillOpacity':.8});wm.add_label(label_text=f'{x},{y}:bin{bin_}',cell=(x,y))
    p0=wm.cell_to_wafer_coordinates((0,0));px=wm.cell_to_wafer_coordinates((1,0));py=wm.cell_to_wafer_coordinates((0,1))
    assert_allclose(np.subtract(px,p0),[10,0]);assert_allclose(np.subtract(py,p0),[0,10])
    out=BASE/'wafer.html';wm.save_html(str(out));assert out.exists() and 'bin2' in out.read_text(encoding='utf-8')
    wrong=frame.drop_duplicates(['PART_ID'],keep='first');assert int((wrong.HARD_BIN==1).sum())==1
    assert int((final.HARD_BIN==1).sum())==2
    return result(('decode_and_render_binmap',dict(dies=3,passed=2,failed=1,xy_pitch_mm=[10,10],html_written=True)),
        ('repair_retest_bin_selection',dict(wrong_first_pass=1,repaired_final_pass=2,retest_die='D2')),
        'IndependentlyframedSTDF4PRR; D2retestoverridesfirstfail,finalbins(0,0)pass(1,0)pass(0,1)fail; waferXYpitch10mm.',
        'Manufacturedthree-die wafer; HTML outputverified forlabels/coordinategeometry, browserpixelsandPNGnotverified.')

def spatial():
    cloud=np.array([[0,0],[.2,0],[0,.2],[-.2,0],[0,-.2]])
    points=np.vstack([cloud+[-3,1],cloud+[4,-2],[[0,8]]])
    labels=DBSCAN(eps=.5,min_samples=3).fit_predict(points)
    def clusters(l):return sorted([tuple(np.where(l==v)[0]) for v in set(l) if v!=-1])
    assert clusters(labels)==[tuple(range(5)),tuple(range(5,10))] and labels[10]==-1
    # Independent geometricoracle: allwithincloudpairs<=.4, crosscloud>=6, outlier>6.
    assert max(math.dist(a,b) for a in points[:5] for b in points[:5])<=.4+1e-12
    wrong=DBSCAN(eps=.5,min_samples=3).fit_predict(points*1000);assert all(wrong==-1)
    fixed=DBSCAN(eps=.5,min_samples=3).fit_predict(points*1000/1000);assert clusters(fixed)==clusters(labels)
    return result(('cluster_failed_dies',dict(clusters=[[int(i) for i in c] for c in clusters(labels)],noise=[10],centroids=[points[:5].mean(0).tolist(),points[5:10].mean(0).tolist()])),
        ('repair_spatial_units',dict(wrong_noise_count=11,fixed_noise_count=1)),
        'Knowninjectedgroups0..4 and5..9 andsingleton10; directEuclideandistances boundclustersindependentofDBSCANlabelIDs.',
        'ManufacturedfailureXYinmm, twoequal-densitygroups; notvalidationofrealwaferdefectdensityorclusteralgorithmchoice.')

def pattern_masks():
    y,x=np.mgrid[-20:21,-20:21];r=np.hypot(x,y)/20;valid=r<=1
    masks={'ring':(r>.65)&(r<.85)&valid,'center':(r<.3)&valid,'scratch':(abs(y)<=1)&valid}
    return x,y,r,valid,masks
def features(mask,r,valid):return binned_statistic(r[valid],mask[valid].astype(float),statistic='mean',bins=[0,.2,.4,.6,.8,1.0001]).statistic
def recognition():
    x,y,r,valid,masks=pattern_masks();names=['center','ring','scratch'];train=[];target=[];test=[]
    for k,name in enumerate(names):
        base=masks[name];train.append(features(base&((x+2*y)%7!=0),r,valid));target.append(name)
        train.append(features(base&((2*x+y)%9!=0),r,valid));target.append(name)
        # Disjointmanufacturedmeasurements: differentmissingdiepattern, noarrayoverlap.
        test.append(features(base&((3*x+y)%11!=0),r,valid))
    classifier=KNeighborsClassifier(n_neighbors=1,algorithm='brute').fit(train,target)
    prediction=classifier.predict(test);assert list(prediction)==names
    cm=confusion_matrix(names,prediction,labels=names);assert_array_equal(cm,np.eye(3,dtype=int))
    wrong=np.asarray(test)[:,::-1];bad=classifier.predict(wrong);assert list(bad)!=names
    fixed=classifier.predict(wrong[:,::-1]);assert list(fixed)==names
    return result(('recognize_wafer_shapes',dict(labels=names,predictions=prediction.tolist(),confusion_matrix=cm.tolist(),radial_features=np.asarray(test).tolist())),
        ('repair_radial_feature_order',dict(wrong_predictions=bad.tolist(),fixed_predictions=fixed.tolist())),
        'Manufacturedgeometricdefinitions r<.3center,.65<r<.85ring,|y|<=1scratch; heldoutomitted-diepatternindependentoftrainingarrays.',
        'Sixtrain/threeheldoutmanufacturedmaps, radialbinfeatures; onlyseparabilityoffixedshapes, notlearnedindustrialaccuracy.')

def recurrence():
    # CommonphysicalXYgrid; unmeasuredcornerexcludedforbothwafers.
    a=np.zeros((5,5));a[1,1]=a[2,1]=a[3,1]=1
    b=a.copy();b[3,2]=1;c=np.zeros((5,5));c[1,3]=c[2,3]=c[3,3]=1
    measured=np.ones((5,5),bool);measured[0,0]=False
    arrays=np.array([v[measured] for v in [a,b,c]])
    scores=cosine_similarity(arrays);expected=3/math.sqrt(3*4)
    assert_allclose(scores[0,1],expected,atol=1e-12);assert scores[0,2]==0
    wrong=np.fliplr(b);wrong_score=float(cosine_similarity([a[measured]],[wrong[measured]])[0,0]);assert wrong_score==0
    fixed=np.fliplr(wrong);assert_allclose(cosine_similarity([a[measured]],[fixed[measured]])[0,0],expected,atol=1e-12)
    return result(('compare_lot_pattern_recurrence',dict(wafer_ids=['L1-W1','L1-W2','L2-W1'],similarity=scores.tolist(),common_measured_dies=int(measured.sum()))),
        ('repair_wafer_orientation_alignment',dict(wrong_similarity=wrong_score,fixed_similarity=expected)),
        'Binarysupportdotproducts: shared3/(sqrt3*sqrt4)=sqrt3/2 forA/B; A/Cdisjoint; knownmirrorinjectiondestroysphysicalalignment.',
        'ManufacturedmapswithstablephysicalXYkeysandcommonmeasuredmask; similaritynotcausalprooforunknownmissing-dieimputation.')

def equipment():
    graph=nx.DiGraph();rows=[]
    for chamber,count in [('A',8),('B',2)]:
        for i in range(10):
            wafer=f'{chamber}-{i}';pattern='ring' if i<count else 'none';rows.append(dict(wafer=wafer,chamber=chamber,ring=pattern=='ring',pattern_artifact=f'{wafer}.json'))
            graph.add_edge('chamber:'+chamber,'process:'+wafer);graph.add_edge('process:'+wafer,'wafer:'+wafer);graph.add_edge('wafer:'+wafer,'pattern:'+wafer)
    frame=pd.DataFrame(rows);tab=pd.crosstab(frame.chamber,frame.ring);stat=fisher_exact(tab.values)
    n,r,s=20,10,10
    def probability(k):return math.comb(s,k)*math.comb(n-s,r-k)/math.comb(n,r)
    exact=sum(probability(k) for k in range(11) if probability(k)<=probability(2)+1e-14)
    assert_allclose(stat.statistic,1/16,atol=1e-12);assert_allclose(stat.pvalue,exact,atol=1e-12)
    assert 'chamber:A' in nx.ancestors(graph,'pattern:A-0') and 'chamber:B' not in nx.ancestors(graph,'pattern:A-0')
    wrong=graph.copy();wrong.add_edge('chamber:B','process:A-0');assert 'chamber:B' in nx.ancestors(wrong,'pattern:A-0')
    wrong.remove_edge('chamber:B','process:A-0');assert nx.ancestors(wrong,'pattern:A-0')==nx.ancestors(graph,'pattern:A-0')
    restored=nx.node_link_graph(json.loads(json.dumps(nx.node_link_data(wrong))));assert nx.is_directed_acyclic_graph(restored)
    return result(('associate_patterns_with_upstream_equipment',dict(ring_counts=[8,2],wafer_counts=[10,10],pvalue=float(stat.pvalue),ring_odds_ratio=16.,candidate_only=True)),
        ('repair_pattern_genealogy',dict(spurious_chamber_B_rejected=True,source_chain_restored=True,json_roundtrip=True)),
        'Manufactured20waferrecords8/10vs2/10ring; independenthypergeometriccombinationsandknownchamber→process→wafer→artifactedges.',
        'Manufacturedpatternartifactsandprocessledger; associationcandidateonly, actualmapclassifierconfidenceandcausalityremainseparate.')

FUNCTIONS={'07.07.01':binmap,'07.07.02':spatial,'07.07.03':recognition,'07.07.04':recurrence,'07.07.05':equipment}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);a=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    for sid,fn in FUNCTIONS.items():
        if a.scene and a.scene!=sid:continue
        value=fn();value.update(scenario_id=sid,status='passed',versions={n:importlib.metadata.version(n) for n in ['wafermap','Semi-ATE-STDF','numpy','scipy','scikit-learn','pandas','networkx']},executed_script=Path(__file__).as_posix(),stdf_release='0.1.33 commit5bbcbe76b522bb899fcc8d9d966d34b125f6516f; metadata0.0.0')
        (BASE/f'{sid}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');print(sid,value['tasks'],flush=True)
if __name__=='__main__':main()
