"""Check upstream custom VJP against finite differences before selecting it."""
from pathlib import Path
import sys
import numpy as np
import torch
torch.set_num_threads(1)
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'seed_pypi_raw/lithography/TorchLitho-2.0'))
from pylitho.sim.abbe.func import AbbeFunc
from pylitho.sim.abbe.simulate import AbbeSim
n=16
mask=torch.tensor(np.random.default_rng(2).uniform(.2,.8,(n,n)),dtype=torch.float64,requires_grad=True)
weight=torch.tensor(np.random.default_rng(3).uniform(.5,1.5,(n,n)),dtype=torch.float64)
direction=torch.tensor(np.random.default_rng(4).normal(size=(n,n)),dtype=torch.float64)
for label,sim in [('custom',lambda m:AbbeFunc.apply(m,40,.05,.65,193,None,False,False)),('native',AbbeSim(pixel=40,sigma=.05,NA=.65,wavelength=193))]:
    loss=(sim(mask)*weight).sum();grad,=torch.autograd.grad(loss,mask)
    eps=.001;fd=(((sim(mask+eps*direction)-sim(mask-eps*direction))*weight).sum()/(2*eps)).item();auto=(grad*direction).sum().item()
    print(label,auto,fd,abs(auto-fd)/max(abs(fd),1),flush=True)
sim=AbbeSim(pixel=40,sigma=.05,NA=.65,wavelength=193)
truth=torch.tensor(.4+.2*np.cos(2*np.pi*np.arange(n)/n)[None,:]+np.zeros((n,1)),dtype=torch.float64)
target=sim(truth).detach()
for lr in [0,1,10]:
    a=torch.full((n,n),.3,dtype=torch.float64,requires_grad=True)
    losses=[]
    for step in range(50):
        loss=((sim(a)-target)**2).mean();losses.append(loss.item());grad,=torch.autograd.grad(loss,a)
        with torch.no_grad():a-=lr*grad;a.clamp_(0,1)
    print('descent',lr,losses[0],losses[-1],flush=True)
