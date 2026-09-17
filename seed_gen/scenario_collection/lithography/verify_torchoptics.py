"""Released differentiable Fresnel core and explicit process-window optimization."""
from pathlib import Path
import json,importlib.metadata
import numpy as np
import torch
from torchoptics import Field
from scipy.optimize import minimize

torch.set_num_threads(1)
BASE=Path(__file__).resolve().parent;OUT=BASE/'runtime/torchoptics'
N=16;DX=200e-9;WL=193e-9;WINDOW=[(1e-6,.9),(5e-6,1.1)]
DFT=np.exp(-2j*np.pi*np.outer(np.arange(N),np.arange(N))/N)/np.sqrt(N)
freq=np.array([i if i<N//2 else i-N for i in range(N)])/(N*DX)
fy,fx=np.meshgrid(freq,freq,indexing='ij')

def model(mask,z):
    field=Field(mask,wavelength=WL,spacing=DX)
    return field.propagate_to_z(z,propagation_method='ASM_FRESNEL',asm_pad=(0,0)).intensity()
def direct(mask,z):
    transfer=np.exp(-1j*np.pi*WL*z*(fx**2+fy**2))
    field=DFT.conj().T@((DFT@mask@DFT.T)*transfer)@DFT.conj()
    return np.abs(field)**2

def run_once():
    mask=torch.tensor(np.random.default_rng(2).uniform(.2,.8,(N,N)),dtype=torch.float64,requires_grad=True)
    weights=torch.tensor(np.random.default_rng(3).uniform(.5,1.5,(N,N)),dtype=torch.float64)
    direction=torch.tensor(np.random.default_rng(4).normal(size=(N,N)),dtype=torch.float64)
    failed=False
    try:torch.autograd.grad((model(mask.detach(),WINDOW[0][0])*weights).sum(),mask)
    except RuntimeError:failed=True
    assert failed
    loss=(model(mask,WINDOW[0][0])*weights).sum();grad,=torch.autograd.grad(loss,mask)
    eps=1e-5
    fd=(((model(mask+eps*direction,WINDOW[0][0])-model(mask-eps*direction,WINDOW[0][0]))*weights).sum()/(2*eps)).item()
    auto=(grad*direction).sum().item();relative=abs(auto-fd)/max(abs(fd),1)
    assert relative<1e-8
    independent=direct(mask.detach().numpy(),WINDOW[0][0]);actual=model(mask,WINDOW[0][0]).detach().numpy()
    assert np.max(np.abs(independent-actual))<1e-12
    original=Field(mask,wavelength=WL,spacing=DX);propagated=original.propagate_to_z(WINDOW[0][0],propagation_method='ASM_FRESNEL',asm_pad=(0,0))
    assert torch.allclose(original.power(),propagated.power(),rtol=1e-12,atol=1e-25)
    first=dict(id='repair_detached_gradient_graph',status='passed',detached_path_rejected=True,autodiff_directional=auto,finite_difference_directional=fd,relative_derivative_error=relative,direct_dft_max_error=float(np.abs(independent-actual).max()),power_preserved=True)
    cosine=np.cos(2*np.pi*np.arange(N)/N)[None,:]+np.zeros((N,1))
    targets=[]
    for z,dose in WINDOW:
        phase=np.pi*WL*z/(N*DX)**2
        targets.append(dose*(.16+.16*cosine*np.cos(phase)+.04*cosine**2))
    target=torch.tensor(np.stack(targets),dtype=torch.float64)
    def objective(values,wrong_sign=False):
        state=torch.tensor(values.reshape(N,N),dtype=torch.float64,requires_grad=True)
        images=torch.stack([dose*model(state,z) for z,dose in WINDOW])
        loss=((images-target)**2).mean();gradient,=torch.autograd.grad(loss,state)
        return loss.item(),(-1 if wrong_sign else 1)*gradient.detach().numpy().ravel()
    initial=np.full(N*N,.3)
    wrong=minimize(lambda v:objective(v,True),initial,method='L-BFGS-B',jac=True,bounds=[(0,1)]*(N*N),options=dict(maxiter=30,gtol=1e-8,ftol=1e-14))
    result=minimize(objective,initial,method='L-BFGS-B',jac=True,bounds=[(0,1)]*(N*N),options=dict(maxiter=50,gtol=1e-8,ftol=1e-14))
    errors=[float(np.mean((dose*direct(result.x.reshape(N,N),z)-target_np)**2)) for (z,dose),target_np in zip(WINDOW,targets)]
    assert wrong.fun>.01 and result.fun<1e-10 and max(errors)<1e-10
    np.savez(OUT/'process_window.npz',mask=result.x.reshape(N,N),targets=np.stack(targets))
    with np.load(OUT/'process_window.npz') as saved:
        for i,(z,dose) in enumerate(WINDOW):assert np.mean((dose*direct(saved['mask'],z)-saved['targets'][i])**2)<1e-10
    second=dict(id='repair_process_window_gradient_sign',status='passed',wrong_sign_loss=float(wrong.fun),correct_loss=float(result.fun),iterations=int(result.nit),independent_corner_residuals=errors,process_window=[dict(z_m=z,dose=dose) for z,dose in WINDOW],archive_replayed=True)
    return [first,second]

def main():
    OUT.mkdir(parents=True,exist_ok=True);first=run_once();second=run_once();assert first==second
    report=dict(scenario_id='03.12.04',status='passed',tasks=first,repeat_runs=2,repeat_equal=True,source_kind='official_stable_release',source_gate='released_main_packages',versions={p:importlib.metadata.version(p) for p in ['torchoptics','torch','scipy','numpy']},boundaries=['Differentiable local scalar Fresnel amplitude-mask propagation, 16x16 periodic grid, 193nm wavelength, 200nm pixels, two axial/dose corners.', 'Unpadded periodic model and grey transmission; no calibrated resist, NA-limited projection/Hopkins, binary manufacturability or full-chip claim.', 'PyTorch tensor creation/autograd are documented hard-dependency protocols; SciPy only supplies optimizer; all physical propagation comes from selected TorchOptics Field methods.', 'Excluded unreleased TorchLitho2 custom VJP failed research finite differences; no such code is used in this accepted task report.'])
    (OUT/'03.12.04.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('03.12.04 released core passed',len(first),flush=True)

if __name__=='__main__':main()
