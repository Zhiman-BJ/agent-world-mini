"""Released GDS/optics/optimization core: six fixed CPU task chains.

Scalar coherent, periodic 16x16 local model; no commercial resist or full-chip
accuracy claim. Direct DFT matrices and analytic grating intensity are oracles.
"""
from pathlib import Path
import hashlib,importlib.metadata,json,math
import numpy as np
import gdstk
from prysm.propagation import focus,unfocus,angular_spectrum
from scipy.optimize import minimize

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/stable'
N=16;DX=40.;WL=193.;NA=.65
axis=(np.arange(N)-N//2)*DX
yy,xx=np.meshgrid(axis,axis,indexing='ij')
freq=(np.arange(N)-N//2)/(N*DX)
fy,fx=np.meshgrid(freq,freq,indexing='ij')
PUPIL=((fx**2+fy**2)<(NA/WL)**2).astype(float)
DFT=np.exp(-2j*np.pi*np.outer(np.arange(N),np.arange(N))/N)/np.sqrt(N)

def image(mask):return np.abs(unfocus(focus(mask,Q=1)*PUPIL,Q=1))**2
def shift(a):return np.roll(a,(N//2,N//2),axis=(0,1))
def oracle(mask):
    spectrum=shift(DFT@shift(mask)@DFT.T)
    field=shift(DFT.conj().T@shift(spectrum*PUPIL)@DFT.conj())
    return np.abs(field)**2
def record(identifier,**kwargs):return dict(id=identifier,status='passed',**kwargs)
def raster(polygons,coordinate_scale=1.):
    points=np.column_stack((xx.ravel(),yy.ravel()))/coordinate_scale
    return np.asarray(gdstk.inside(points,polygons),dtype=float).reshape(N,N)
def write_layout(path,halfwidth_nm=100):
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('MASK')
    cell.add(gdstk.rectangle((-halfwidth_nm/1000,)*2,(halfwidth_nm/1000,)*2,layer=1))
    cell.add(gdstk.rectangle((-.25,-.04),(.25,.04),layer=9))
    lib.write_gds(str(path))

def forward_tasks(run):
    folder=OUT/f'run{run}';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'mask.gds';write_layout(path)
    lib=gdstk.read_gds(str(path));polygons=lib.top_level()[0].get_polygons(layer=1,datatype=0)
    bad=raster(polygons);mask=raster(polygons,lib.unit*1e9)
    assert np.count_nonzero(bad)==1 and np.count_nonzero(mask)==25
    aerial=image(mask);direct=oracle(mask)
    assert np.max(np.abs(aerial-direct))<1e-12
    assert np.max(np.abs(image(bad)-direct))>.1
    np.savez(folder/'forward.npz',mask=mask,aerial=aerial)
    with np.load(folder/'forward.npz') as stored:assert np.array_equal(stored['mask'],mask)
    first=record('repair_gds_length_units',wrong_active_pixels=int(bad.sum()),correct_active_pixels=int(mask.sum()),unit_m=lib.unit,oracle_max_error=float(np.abs(aerial-direct).max()),archive_restored=True)
    # Independent Fresnel grating formula: two sidebands acquire identical phase.
    cosine=np.cos(2*np.pi*np.arange(N)/N)[None,:]+np.zeros((N,1))
    grating=.5+.25*cosine;z_nm=100.;z_mm=z_nm*1e-6;dx_mm=DX*1e-6;wvl_um=WL/1000
    dose=.8
    result=dose*np.abs(angular_spectrum(grating,wvl=wvl_um,dx=dx_mm,z=z_mm,Q=1))**2
    phase=np.pi*(wvl_um/1000)*z_mm*(1/(N*dx_mm))**2
    expected=dose*(.25+.25*cosine*np.cos(phase)+.0625*cosine**2)
    wrong=dose*np.abs(angular_spectrum(grating,wvl=wvl_um,dx=dx_mm,z=z_nm,Q=1))**2
    assert np.max(np.abs(result-expected))<1e-12 and np.max(np.abs(wrong-expected))>1e-3
    assert math.isclose(float(result.mean()),dose*(.25+.0625/2),abs_tol=1e-12)
    second=record('repair_defocus_unit_and_dose',wrong_nm_as_mm_max_error=float(np.abs(wrong-expected).max()),correct_max_error=float(np.abs(result-expected).max()),defocus_nm=z_nm,defocus_mm=z_mm,dose=dose,mean_intensity=float(result.mean()),oracle_mean=dose*(.25+.0625/2))
    return [first,second]

def correction_tasks(run):
    folder=OUT/f'run{run}'
    target=((abs(xx)<120)&(abs(yy)<120)).astype(float)
    initial=gdstk.rectangle((-60.,-60.),(60.,60.),layer=1)
    candidates=[]
    for bias in [0,40,80]:
        polygons=gdstk.offset([initial],bias,precision=.001,layer=1)
        mask=raster(polygons);actual=image(mask)
        direct=oracle(mask)
        assert np.max(np.abs(actual-direct))<1e-12
        error=int(np.count_nonzero((actual>=.35)!=target))
        direct_error=int(np.count_nonzero((direct>=.35)!=target))
        assert error==direct_error
        candidates.append((bias,error,mask,polygons))
    best=min(candidates,key=lambda row:row[1]);assert best[0]==40 and best[1]==4 and candidates[0][1]==25
    # Serialize corrected polygons in nm user units; readback preserves area.
    lib=gdstk.Library(unit=1e-9,precision=1e-12);cell=lib.new_cell('CORRECTED');cell.add(*best[3]);lib.write_gds(str(folder/'corrected.gds'))
    loaded=gdstk.read_gds(str(folder/'corrected.gds'));restored=loaded.top_level()[0].get_polygons(layer=1,datatype=0)
    assert np.array_equal(raster(restored),best[2])
    first=record('repair_mask_bias',initial_error_pixels=25,corrected_error_pixels=4,best_outward_bias_nm=40,candidates=[dict(bias_nm=b,error_pixels=e) for b,e,_,_ in candidates],oracle='Independent direct DFT for every candidate',corrected_gds_restored=True)
    lib=gdstk.read_gds(str(folder/'mask.gds'));top=lib.top_level()[0]
    wrong_polys=top.get_polygons(layer=9,datatype=0)
    corrected_polys=top.get_polygons(layer=1,datatype=0)
    wrong_mask=raster(wrong_polys,lib.unit*1e9);mask=raster(corrected_polys,lib.unit*1e9)
    wrong_error=int(np.count_nonzero((image(wrong_mask)>=.35)!=target))
    correct_error=int(np.count_nonzero((image(mask)>=.35)!=target))
    assert correct_error==4 and wrong_error>correct_error
    # Task must preserve the target layer and its coordinate frame through export.
    assert math.isclose(sum(p.area() for p in corrected_polys)*1e6,40000,abs_tol=1e-6)
    second=record('repair_target_layer_before_opc',wrong_layer=9,correct_layer=1,wrong_error_pixels=wrong_error,correct_error_pixels=correct_error,polygon_area_nm2=40000.,target_pixels=25)
    return [first,second]

def inverse_tasks(run):
    folder=OUT/f'run{run}'
    truth=.4+.2*np.cos(2*np.pi*np.arange(N)/N)[None,:]+np.zeros((N,1))
    # Frequency 0 and +/-1 fit inside pupil, so the analytic target is truth^2.
    target=truth**2;assert np.max(np.abs(image(truth)-target))<1e-12
    initial=np.full((N,N),.3)
    def objective(v):return float(np.mean((image(v.reshape(N,N))-target)**2))
    def solve(bound,maxiter):return minimize(objective,initial.ravel(),method='L-BFGS-B',bounds=[(0,bound)]*(N*N),options=dict(maxiter=maxiter,ftol=1e-12,gtol=1e-7))
    short=solve(1,1);solved=solve(1,30)
    independent=float(np.mean((oracle(solved.x.reshape(N,N))-target)**2))
    assert short.fun>1e-4 and independent<1e-9 and solved.success
    assert np.all((solved.x>=0)&(solved.x<=1))
    np.savez(folder/'inverse.npz',mask=solved.x.reshape(N,N),target=target)
    first=record('repair_ilt_iteration_budget',wrong_maxiter=1,wrong_residual=float(short.fun),maxiter=30,iterations=int(solved.nit),correct_residual=float(solved.fun),independent_direct_dft_residual=independent,bounds=[0,1])
    bounded=solve(.35,30)
    assert bounded.fun>1e-4 and solved.fun<1e-9
    with np.load(folder/'inverse.npz') as stored:replayed=float(np.mean((oracle(stored['mask'])-stored['target'])**2))
    assert replayed<1e-9
    second=record('repair_transmission_upper_bound',wrong_bound=.35,wrong_residual=float(bounded.fun),correct_bound=1.,correct_residual=float(solved.fun),archive_direct_dft_residual=replayed)
    return [first,second]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for sid,fn in [('03.12.01',forward_tasks),('03.12.02',correction_tasks),('03.12.03',inverse_tasks)]:
        first=fn(1);second=fn(2);assert first==second
        report=dict(scenario_id=sid,status='passed',tasks=first,repeat_runs=2,repeat_equal=True,versions={p:importlib.metadata.version(p) for p in ['prysm','gdstk','scipy','numpy']},source_gate='released_main_packages',boundaries=['Scalar coherent periodic 16x16 local model, no Hopkins partial coherence or calibrated resist.', '01 uses explicit GDS nm bridge; 02 is bounded candidate geometry bias selection, not lithosim broken anneal.', '03 optimizes bounded continuous grey transmission with numerical-gradient L-BFGS-B; not manufacturable binary mask or an executed OpenILT algorithm.','Threshold is an explicitly declared engineering comparator, not an undeclared package resist solver.'])
        (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(sid,'passed',len(first),flush=True)
    (OUT/'requirements.freeze.txt').write_text('\n'.join(sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions()))+'\n',encoding='utf-8')

if __name__=='__main__':main()
