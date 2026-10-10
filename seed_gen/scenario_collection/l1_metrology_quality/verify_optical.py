"""Analytic Fresnel/film oracles and manufactured spectra for five optical scenes."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import numpy as np
import pandas as pd
import h5py
import elli
from lmfit import Parameters, minimize
from numpy.testing import assert_allclose

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/optical')

def analytic_rho(wavelength,angle,n1=1.46,d=23.,n0=1.,n2=3.8+.02j):
    """Scalar one-film Fresnel recursion, independent of pyElli matrices."""
    wl=np.asarray(wavelength,dtype=float)
    q=n0*np.sin(np.deg2rad(angle))
    c0=np.sqrt(1-(q/n0)**2+0j);c1=np.sqrt(1-(q/n1)**2+0j);c2=np.sqrt(1-(q/n2)**2+0j)
    phase=np.exp(4j*np.pi*n1*c1*d/wl)
    def coefficient(pol):
        if pol=='s':
            a=(n0*c0-n1*c1)/(n0*c0+n1*c1)
            b=(n1*c1-n2*c2)/(n1*c1+n2*c2)
        else:
            a=(n1*c0-n0*c1)/(n1*c0+n0*c1)
            b=(n2*c1-n1*c2)/(n2*c1+n1*c2)
        return (a+b*phase)/(1+a*b*phase)
    return coefficient('p')/coefficient('s')

def material(n):return elli.ConstantRefractiveIndex(n).get_mat()

def forward(wl,angle,n=1.46,k=0.,d=23.,front=1.,back=3.8+.02j):
    stack=elli.Structure(material(front),[elli.Layer(material(n+1j*k),d)],material(back))
    return stack.evaluate(np.asarray(wl),angle,solver=elli.Solver2x2)

def nexus(path,wl,psi,delta):
    with h5py.File(path,'w') as f:
        f.create_dataset('entry/data_collection/data_type',data=np.bytes_('Psi/Delta'))
        f.create_dataset('entry/data_collection/wavelength_spectrum',data=np.asarray(wl))
        f.create_dataset('entry/instrument/angle_of_incidence',data=[60.])
        f.create_dataset('entry/data_collection/measured_data',data=np.array([[psi,delta]]))

def loading():
    wl=np.array([400.,500.,600.,700.]);psi=np.array([30.,32.,34.,36.]);delta=np.array([170.,175.,180.,185.])
    path=BASE/'manufactured_psi_delta.nxs';nexus(path,wl*10,psi,delta)
    data=elli.read_nexus_psi_delta(path)
    assert_allclose(data.loc[60].index.values,wl,atol=1e-12)
    assert_allclose(data['Ψ'],psi,atol=1e-12);assert_allclose(data['Δ'],delta,atol=1e-12)
    rho=elli.calc_rho(data)
    expected=np.tan(np.deg2rad(psi))*np.exp(-1j*np.deg2rad(delta))
    assert_allclose(np.asarray(rho).ravel(),expected,atol=1e-12)
    wrongpath=BASE/'wrong_nm_written_as_angstrom.nxs';nexus(wrongpath,wl,psi,delta)
    wrong=elli.read_nexus_psi_delta(wrongpath)
    assert not np.allclose(wrong.loc[60].index.values,wl)
    repairedpath=BASE/'repaired_angstrom.nxs';nexus(repairedpath,wl*10,psi,delta)
    repaired=elli.read_nexus_psi_delta(repairedpath)
    assert_allclose(repaired.loc[60].index.values,wl,atol=1e-12)
    assert_allclose(np.asarray(elli.read_nexus_rho(repairedpath)).ravel(),expected,atol=1e-12)
    return dict(tasks=[dict(id='load_psi_delta',status='passed',rows=len(data),wavelength_nm=wl.tolist()),
                       dict(id='repair_wavelength_unit',status='passed',wrong_nm=wrong.loc[60].index.values.tolist(),repaired_nm=wl.tolist())],
        oracle='Explicit input angle/spectral arrays and pyElli convention rho=tan(Psi)*exp(-iDelta); release reader expects wavelength in Angstrom and divides by10.',
        fixture='Manufactured NeXus optical-definition layout, four spectral samples; no real measurement claims.')

def stack():
    wl=np.linspace(400,800,21);target=analytic_rho(wl,60)
    result=forward(wl,60)
    assert_allclose(result.rho,target,atol=1e-12)
    assert_allclose(result.psi,np.rad2deg(np.arctan(abs(target))),atol=1e-11)
    # No phase wrapping ambiguity in complex rho.
    wrong=forward(wl,60,front=3.8+.02j,back=1.)
    error=float(np.max(abs(wrong.rho-target)));assert error>.1
    repaired=forward(wl,60)
    assert_allclose(repaired.rho,target,atol=1e-12)
    zero=forward(wl,60,d=0)
    assert_allclose(zero.rho,analytic_rho(wl,60,d=0),atol=1e-12)
    return dict(tasks=[dict(id='construct_optical_stack',status='passed',thickness_nm=23.,max_error=float(np.max(abs(result.rho-target)))),
                       dict(id='repair_stack_order',status='passed',wrong_error=error,repaired_error=float(np.max(abs(repaired.rho-target))))],
        oracle='Independent single-film Fresnel recursion, plus zero-thickness interface limit; compare complex rho to avoid phase wrapping.',
        fixture='Manufactured isotropic constant-index film n1.46, d23nm, substrate3.8+.02i;21 wavelengths400–800nm at60deg.')

def dispersion():
    wl=np.array([400.,500.,600.,700.,800.]);expected=1.45+10000/wl**2
    model=elli.Cauchy(n0=1.45,n1=100,n2=0,k0=0,k1=0,k2=0)
    observed=model.refractive_index(wl)
    assert_allclose(observed,expected,atol=1e-12)
    constant=elli.ConstantRefractiveIndex(1.45).refractive_index(wl)
    assert float(np.max(abs(constant-expected)))>.06
    wrong=elli.Cauchy(n0=1.45,n1=10000).refractive_index(wl)
    assert float(np.max(abs(wrong-expected)))>1
    repaired=elli.Cauchy(n0=1.45,n1=100).refractive_index(wl)
    assert_allclose(repaired,expected,atol=1e-12)
    return dict(tasks=[dict(id='select_dispersion_model',status='passed',n=expected.tolist(),constant_max_error=float(np.max(abs(constant-expected)))),
                       dict(id='repair_cauchy_units',status='passed',wrong_max_error=float(np.max(abs(wrong-expected))),corrected_n1=100)],
        oracle='Explicit n(lambda)=1.45+10000/lambda_nm²; pyElli Cauchy has factor100 before n1.',
        fixture='Five manufactured transparent-film indices; does not prove a physical dispersion model fits actual material.')

def residual_rho(params,wl,angles,target):
    values=[]
    for angle,reference in zip(angles,target):
        diff=forward(wl,angle,n=params['n'].value,k=params['k'].value,d=params['d'].value).rho-reference
        values.extend([diff.real,diff.imag])
    return np.concatenate(values)

def params_for_fit(dmin=1.,start=15.):
    p=Parameters();p.add('d',value=start,min=dmin,max=80.)
    p.add('n',value=1.6,min=1.2,max=2.5);p.add('k',value=.04,min=0,max=.3)
    return p

def fit_thickness():
    wl=np.linspace(400,800,21);angles=[55.,70.]
    target=[analytic_rho(wl,a,n1=1.8+.08j,d=23.) for a in angles]
    fit=minimize(residual_rho,params_for_fit(),args=(wl,angles,target),method='least_squares')
    values=[fit.params[k].value for k in ['d','n','k']]
    assert fit.success;assert_allclose(values,[23.,1.8,.08],atol=2e-5)
    assert np.max(abs(fit.residual))<1e-8
    wrong=minimize(residual_rho,params_for_fit(dmin=40,start=50),args=(wl,angles,target),method='least_squares')
    wrong_error=float(np.max(abs(wrong.residual)));assert wrong_error>1e-4
    repaired=wrong.params.copy();repaired['d'].set(min=1,max=80,value=20)
    fixed=minimize(residual_rho,repaired,args=(wl,angles,target),method='least_squares')
    assert_allclose([fixed.params[k].value for k in ['d','n','k']],[23,1.8,.08],atol=2e-5)
    saved=Parameters().loads(fixed.params.dumps())
    assert np.max(abs(residual_rho(saved,wl,angles,target)))<1e-8
    return dict(tasks=[dict(id='fit_thickness_n_k',status='passed',d_n_k=values,max_residual=float(np.max(abs(fit.residual)))),
                       dict(id='repair_fit_bounds',status='passed',wrong_max_residual=wrong_error,repaired_d_nm=float(fixed.params['d'].value),parameter_roundtrip=True)],
        oracle='Target generated by independent Fresnel recursion at known d23nm/n1.8/k.08 and two angles; fit pyElli using lmfit, verify known hidden parameters.',
        fixture='Manufactured noise-free84 real/imag samples, two angles. Numerical identifiability in this constrained model only; no field uncertainty validation.')

def quality():
    train=np.array([600.]);held=np.array([400.,450.,550.,650.,750.,800.]);angle=60.
    true=lambda wl:1.45+18000/wl**2
    target_train=analytic_rho(train,angle,n1=true(train),d=23)
    target_held=analytic_rho(held,angle,n1=true(held),d=23)
    # Exact single-wavelength interpolation says nothing about spectral validity.
    wrong_n=float(true(train)[0])
    wrong_train=forward(train,angle,n=wrong_n).rho
    wrong_held=forward(held,angle,n=wrong_n).rho
    train_error=float(np.max(abs(wrong_train-target_train)));held_error=float(np.max(abs(wrong_held-target_held)))
    assert train_error<1e-12 and held_error>1e-3
    train2=np.array([500.,700.]);reference=analytic_rho(train2,angle,n1=true(train2),d=23)
    assert not set(train2).intersection(held)
    def residual(p):
        film=elli.Cauchy(n0=p['n0'].value,n1=p['n1'].value).get_mat()
        result=elli.Structure(material(1),[elli.Layer(film,23)],material(3.8+.02j)).evaluate(train2,angle,solver=elli.Solver2x2)
        diff=result.rho-reference
        return np.r_[diff.real,diff.imag]
    params=Parameters();params.add('n0',1.4,min=1.2,max=2);params.add('n1',100,min=0,max=1000)
    good=minimize(residual,params,method='least_squares')
    assert_allclose([good.params['n0'].value,good.params['n1'].value],[1.45,180],atol=2e-3)
    model=elli.Cauchy(n0=good.params['n0'].value,n1=good.params['n1'].value).get_mat()
    fixed=elli.Structure(material(1),[elli.Layer(model,23)],material(3.8+.02j)).evaluate(held,angle,solver=elli.Solver2x2)
    fixed_error=float(np.max(abs(fixed.rho-target_held)));assert fixed_error<1e-6
    return dict(tasks=[dict(id='reject_training_only_fit',status='passed',training_max_error=train_error,holdout_max_error=held_error),
                       dict(id='repair_spectral_model',status='passed',n0=float(good.params['n0'].value),n1=float(good.params['n1'].value),holdout_max_error=fixed_error)],
        oracle='Withheld wavelengths were generated by independent Fresnel and n=1.45+18000/lambda². One-wavelength constant model matches training exactly but fails unchanged holdout threshold.',
        fixture='Manufactured transparent film, thickness independently known23nm. Repairs use two training wavelengths, heldout six are never fitted; does not establish full model identifiability.')

FUNCTIONS={'07.06.01':loading,'07.06.02':stack,'07.06.03':dispersion,'07.06.04':fit_thickness,'07.06.05':quality}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);args=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    versions={n:importlib.metadata.version(n) for n in ['pyElli','lmfit','numpy','scipy','pandas','h5py']}
    for sid,fn in FUNCTIONS.items():
        if args.scene and args.scene!=sid:continue
        result=fn();result.update(scenario_id=sid,status='passed',versions=versions,executed_script=Path(__file__).as_posix())
        (BASE/f'{sid}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(sid,result['tasks'],flush=True)
if __name__=='__main__':main()
