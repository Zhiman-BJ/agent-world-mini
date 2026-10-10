"""CPU probes kept separate because both upstream projects use pylitho."""
import os,sys
from pathlib import Path
import numpy as np
import torch
torch.set_num_threads(1)
ROOT=Path(__file__).resolve().parents[3]
if sys.argv[1]=='torchlitho':
    sys.path.insert(0,str(ROOT/'seed_pypi_raw/lithography/TorchLitho-2.0'))
    from pylitho import Abbe
    from pylitho.sim.abbe.simulate import AbbeSim
    a=torch.tensor(np.random.default_rng(2).uniform(.2,.8,(16,16)),dtype=torch.float64,requires_grad=True)
    weight=torch.tensor(np.random.default_rng(3).uniform(.5,1.5,(16,16)),dtype=torch.float64)
    direction=torch.tensor(np.random.default_rng(4).normal(size=(16,16)),dtype=torch.float64)
    for name,sim in [('native',AbbeSim(pixel=40,sigma=.05,NA=.65,wavelength=193)),('custom',Abbe(canvas=640,pixel=40,sigma=.05,NA=.65,wavelength=193))]:
        image=sim(a)
        if name=='custom':print('custom shape',image.shape,flush=True);continue
        loss=(image*weight).sum();grad,=torch.autograd.grad(loss,a)
        eps=1e-3;fd=(((sim(a+eps*direction)-sim(a-eps*direction))*weight).sum()/(2*eps)).item()
        auto=(grad*direction).sum().item()
        print(name,image.shape,auto,fd,abs(auto-fd)/max(abs(fd),1),flush=True)
elif sys.argv[1]=='openilt':
    source=ROOT/'seed_pypi_raw/lithography/OpenILT'
    os.chdir(source)
    sys.path.insert(0,str(source/'thirdparty/adaptive-boxes'))
    sys.path.insert(0,str(source))
    from pyilt.simpleilt import SimpleCfg,SimpleILT
    from pylitho.exact import LithoSim
    from pyilt.initializer import PixelInit
    from pyilt.evaluation import Basic
    litho=LithoSim('./config/lithosimple.txt')
    n=64;target=np.zeros((n,n),dtype=np.float32);target[24:40,24:40]=1
    target,params=PixelInit().run(target,n,n,0,0)
    cfg=SimpleCfg(dict(Iterations=5,TargetDensity=.5,SigmoidSteepness=4,WeightEPE=0,WeightPVBand=0,WeightPVBL2=0,StepSize=.1,TileSizeX=n,TileSizeY=n,OffsetX=0,OffsetY=0,ILTSizeX=n,ILTSizeY=n))
    sol=SimpleILT(cfg,litho,device=torch.device('cpu'));result=sol.solve(target,params)
    print('openilt',result[0],result[1],Basic(litho).run(result[3].clone(),target),flush=True)
