"""Experimental unreleased TorchLitho2 CPU reference, with derivative oracle."""
from pathlib import Path
import sys,json,importlib.metadata
import numpy as np
import torch
torch.set_num_threads(1)
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]
sys.path.insert(0,str(ROOT/'seed_pypi_raw/lithography/TorchLitho-2.0'))
from pylitho.sim.abbe.simulate import AbbeSim
from pylitho.sim.abbe.func import AbbeFunc
OUT=BASE/'runtime/differentiable'

def forward(sim,mask):return sim(mask)
def run_once():
    n=16;sim=AbbeSim(pixel=40,sigma=.05,NA=.65,wavelength=193,defocus=0)
    mask=torch.tensor(np.random.default_rng(2).uniform(.2,.8,(n,n)),dtype=torch.float64,requires_grad=True)
    weight=torch.tensor(np.random.default_rng(3).uniform(.5,1.5,(n,n)),dtype=torch.float64)
    direction=torch.tensor(np.random.default_rng(4).normal(size=(n,n)),dtype=torch.float64)
    def derivative(model):
        loss=(model(mask)*weight).sum();grad,=torch.autograd.grad(loss,mask)
        eps=.001;fd=(((model(mask+eps*direction)-model(mask-eps*direction))*weight).sum()/(2*eps)).item()
        autodiff=(grad*direction).sum().item()
        return autodiff,fd,abs(autodiff-fd)/max(abs(fd),1)
    # Excluded candidate evaluation. It is negative research evidence only;
    # selected task begins with this supplied invalid derivative diagnostic.
    bad=derivative(lambda m:AbbeFunc.apply(m,40,.05,.65,193,None,False,False))
    correct=derivative(sim)
    assert bad[2]>.1 and correct[2]<1e-4
    # Independent direct matrix DFT oracle for this small one-source pupil.
    freq=(np.arange(n)-n//2)/(n*40);fy,fx=np.meshgrid(freq,freq,indexing='ij')
    pupil=(fx**2+fy**2)<(.65/193)**2
    dft=np.exp(-2j*np.pi*np.outer(np.arange(n),np.arange(n))/n)/np.sqrt(n)
    def shift(a):return np.roll(a,(n//2,n//2),axis=(0,1))
    def direct(m):
        spectrum=shift(dft@shift(m)@dft.T)
        return np.abs(shift(dft.conj().T@shift(spectrum*pupil)@dft.conj()))**2
    reference=direct(mask.detach().numpy())
    assert np.max(np.abs(sim(mask).detach().numpy()-reference))<1e-6
    first=dict(id='repair_gradient_execution_path',status='passed',supplied_invalid_vjp_relative_error=bad[2],selected_native_graph_relative_error=correct[2],autodiff_directional=correct[0],finite_difference_directional=correct[1],direct_dft_max_error=float(np.abs(sim(mask).detach().numpy()-reference).max()))
    truth=torch.tensor(.4+.2*np.cos(2*np.pi*np.arange(n)/n)[None,:]+np.zeros((n,1)),dtype=torch.float64)
    target=truth**2
    def optimize(step_size):
        state=torch.full((n,n),.3,dtype=torch.float64,requires_grad=True)
        for _ in range(100):
            loss=((sim(state)-target)**2).mean();gradient,=torch.autograd.grad(loss,state)
            with torch.no_grad():state-=step_size*gradient;state.clamp_(0,1)
        return state.detach().numpy(),float(((sim(state)-target)**2).mean())
    unchanged,bad_loss=optimize(0);optimized,good_loss=optimize(10)
    independent=float(np.mean((direct(optimized)-target.numpy())**2))
    assert bad_loss>.02 and good_loss<1e-6 and independent<1e-6
    assert optimized.min()>=0 and optimized.max()<=1
    np.savez(OUT/'optimized.npz',mask=optimized,target=target.numpy())
    with np.load(OUT/'optimized.npz') as archived:assert np.mean((direct(archived['mask'])-archived['target'])**2)<1e-6
    second=dict(id='repair_gradient_step_size',status='passed',wrong_step_size=0,wrong_loss=bad_loss,correct_step_size=10,iterations=100,correct_loss=good_loss,independent_direct_dft_loss=independent,bounds=[0,1],archive_replayed=True)
    return [first,second],dict(candidate='AbbeFunc custom VJP',autodiff_directional=bad[0],finite_difference_directional=bad[1],relative_error=bad[2],selection='excluded; source unmodified')

def main():
    OUT.mkdir(parents=True,exist_ok=True);first,negative=run_once();second,negative2=run_once();assert first==second and negative==negative2
    report=dict(scenario_id='03.12.04',status='passed',tasks=first,repeat_runs=2,repeat_equal=True,source_kind='unreleased_commit_snapshot',source_gate='BLOCKED_STABLE_RELEASE_REQUIREMENT',source_commit='c5f46ce8282a90d7ce7e224b5f69a8135fcfc6ff',versions={p:importlib.metadata.version(p) for p in ['torch','numpy','scipy']},excluded_candidate_probe=negative,boundaries=['CPU16x16 single-source scalar coherent AbbeSim direct graph; no GPU, full-chip throughput or broad partial coherence validation.', 'Research snapshot has zero tags/no GitHub Release/no PyPI distribution; runtime success DOES NOT satisfy stable-release acceptance.', 'Native tensor differentiation and projected gradient update are explicit fixture engineering infrastructure; no extra hidden optical solver.', 'High-level Abbe/AbbeFunc custom VJP failed independent derivative validation and is excluded.'])
    (OUT/'03.12.04.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('03.12.04 CPU tasks passed; stable release gate BLOCKED',flush=True)

if __name__=='__main__':main()
