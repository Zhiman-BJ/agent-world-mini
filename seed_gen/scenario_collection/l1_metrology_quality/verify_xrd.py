"""SmallXRD/XRR/structure fixtures; frozen geometric and independentwave oracles."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import numpy as np
from numpy.testing import assert_allclose,assert_array_equal
BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/xrd')
def result(a,b,oracle,fixture):return dict(tasks=[dict(id=a[0],status='passed',**a[1]),dict(id=b[0],status='passed',**b[1])],oracle=oracle,fixture=fixture)
def integration():
    from pyFAI.integrator.azimuthal import AzimuthalIntegrator
    import pyFAI
    y,x=np.indices((64,64));radius=np.hypot(x+.5-32,y+.5-32);image=7+100*np.exp(-.5*((radius-15)/1.)**2)
    def ai(dist):return AzimuthalIntegrator(dist=dist,poni1=.0032,poni2=.0032,pixel1=.0001,pixel2=.0001,wavelength=1e-10)
    def run(a):return a.integrate1d(image,100,unit='2th_deg',method=('no','histogram','numpy'),correctSolidAngle=False)
    a=ai(.1);out=run(a);peak=float(out.radial[np.argmax(out.intensity)]);expected=math.degrees(math.atan(.0015/.1));assert abs(peak-expected)<.04
    constant=a.integrate1d(np.full((64,64),7.),50,unit='2th_deg',method=('no','histogram','numpy'),correctSolidAngle=False);assert_allclose(constant.intensity,7,atol=1e-12)
    wrong=run(ai(.05));wrong_peak=float(wrong.radial[np.argmax(wrong.intensity)]);assert abs(wrong_peak-expected)>.5
    a.save(str(BASE/'geometry.poni'));restored=pyFAI.load(str(BASE/'geometry.poni'));fixed=run(restored);assert_allclose(fixed.radial,out.radial,atol=1e-12);assert_allclose(fixed.intensity,out.intensity,atol=1e-6)
    return result(('integrate_detector_ring',dict(expected_peak_2theta_deg=expected,actual_peak_2theta_deg=peak,constant_intensity=7)),
        ('repair_detector_distance',dict(wrong_distance_m=.05,wrong_peak_deg=wrong_peak,repaired_distance_m=.1,poni_roundtrip=True)),
        'Independentgeometry2theta=atan(radius15pixels*100um/100mm); constantimageazimuthalmeansmuststay7whenSolidAnglecorrectionoff.',
        'Manufactured64x64ringandflatdetectorimage; knownidealgeometry, noexperimentalcalibration/detector-distortion fit.')
def reciprocal():
    import xrayutilities as xu
    omega=np.array([10.,20.,30.]);wavelength=1.54
    h=xu.HXRD([1,0,0],[0,0,1],wl=wavelength);qx,qy,qz=h.Ang2Q(omega,2*omega)
    expected=4*np.pi/wavelength*np.sin(np.deg2rad(omega));assert_allclose(qx,0,atol=1e-12);assert_allclose(qy,0,atol=1e-12);assert_allclose(qz,expected,atol=1e-12)
    wrong=h.Ang2Q(np.deg2rad(omega),np.deg2rad(2*omega))[2];assert np.max(abs(wrong-expected))>1
    fixed=h.Ang2Q(np.rad2deg(np.deg2rad(omega)),2*omega)[2];assert_allclose(fixed,expected,atol=1e-12)
    payload=dict(wavelength_A=wavelength,omega_deg=omega.tolist(),qz_A_inverse=qz.tolist());path=BASE/'reciprocal.json';path.write_text(json.dumps(payload),encoding='utf-8');assert json.loads(path.read_text())==payload
    return result(('map_symmetric_scan_to_reciprocal_space',dict(qz_A_inverse=qz.tolist(),qx_max=float(abs(qx).max()),qy_max=float(abs(qy).max()))),
        ('repair_degree_radian_input',dict(wrong_qz_A_inverse=wrong.tolist(),fixed_qz_A_inverse=fixed.tolist(),json_roundtrip=True)),
        'Independentsymmetriccoplanarformulaqx=qy=0,qz=4pi*sin(omega)/lambda; lambda1.54Aandangles10/20/30degrees.',
        'Manufacturedsymmetricscanonly; generalfourcircle/area-detectororientationandgriddingnotruntimecovered.')
def reflectivity():
    import xrayutilities as xu
    from lmfit import Parameters,minimize
    # FixedmaterialsusceptibilitiescapturedfrompinnedSi/SiO2dataset at8keV;
    # acceptancewavecalculationdoesnotcallthelibraryforwardmodel.
    chi_si=-1.5340420990025933e-5+3.6564773676162396e-7j
    chi_film=-1.7345742298825476e-5+2.3293630069078043e-7j
    wavelength=1.5498024804150032;angles=np.linspace(.3,2.,151)
    kv=2*np.pi/wavelength*np.sin(np.deg2rad(angles));kf=2*np.pi/wavelength*np.sqrt(np.sin(np.deg2rad(angles))**2+chi_film);ks=2*np.pi/wavelength*np.sqrt(np.sin(np.deg2rad(angles))**2+chi_si)
    r01=(kv-kf)/(kv+kf);r12=(kf-ks)/(kf+ks);phase=np.exp(2j*kf*230.)
    target=abs((r01+r12*phase)/(1+r01*r12*phase))**2
    layer=xu.simpack.Layer(xu.materials.SiO2,230.,roughness=0)
    model=xu.simpack.SpecularReflectivityModel(xu.simpack.Layer(xu.materials.Si,np.inf,roughness=0),layer,energy=8000)
    assert_allclose(model.simulate(angles),target,atol=1e-14)
    def residual(params):layer.thickness=params['d_A'].value;return np.log(model.simulate(angles))-np.log(target)
    probe=Parameters();probe.add('d_A',value=180)
    costs=[]
    for d in np.arange(180.,281.,7.):
        probe['d_A'].value=d;costs.append((float(np.sum(residual(probe)**2)),d))
    initial=min(costs)[1]
    good=Parameters();good.add('d_A',value=initial,min=180,max=280)
    fitted=minimize(residual,good,method='least_squares');assert_allclose(fitted.params['d_A'].value,230,atol=1e-6)
    bad=Parameters();bad.add('d_A',value=50,min=10,max=100);wrong=minimize(residual,bad,method='least_squares');bad_error=float(np.max(abs(residual(wrong.params))));assert bad_error>.1
    fixed=minimize(residual,good,method='least_squares');assert_allclose(fixed.params['d_A'].value,230,atol=1e-6)
    saved=Parameters();saved.loads(fixed.params.dumps());assert_allclose(saved['d_A'].value,230,atol=1e-6)
    return result(('fit_thin_film_thickness',dict(coarse_initial_A=float(initial),thickness_A=float(fitted.params['d_A'].value),target_thickness_nm=23.,max_log_residual=float(np.max(abs(residual(fitted.params)))))),
        ('repair_thickness_bounds',dict(wrong_max_A=100,wrong_max_log_residual=bad_error,repaired_thickness_A=float(fixed.params['d_A'].value),parameters_json_roundtrip=True)),
        'Independenttwo-interfaceFresnelamplituderecursionwithfrozenchiSi/SiO2andtarget230A; sameangulargridandatol1e-6Aforfitandrepair.',
        'Noise-freesingleSiO2film/Si8keV, density/roughnessknownfixed; notjointidentifiability/confidencecalibration orrealXRRfit.')
def structure():
    from diffpy.structure import Atom,Lattice,Structure,load_structure
    a=5.43;fcc=np.array([[0,0,0],[0,.5,.5],[.5,0,.5],[.5,.5,0.]])
    frac=np.vstack([fcc,(fcc+.25)%1]);lattice=Lattice(a,a,a,90,90,90);s=Structure(atoms=[Atom('Si',v,label=f'Si{i}') for i,v in enumerate(frac)],lattice=lattice)
    assert_allclose(lattice.volume,a**3,atol=1e-10)
    distance=s.distance(0,4);expected=a*math.sqrt(3)/4;assert_allclose(distance,expected,atol=1e-12)
    path=BASE/'silicon_p1.cif';s.write(str(path),'cif');restored=load_structure(str(path));assert len(restored)==8;assert_array_equal(restored.element,['Si']*8);assert_allclose(restored.xyz,frac,atol=1e-8)
    wrong=Structure(s);wrong.lattice=Lattice(.543,.543,.543,90,90,90);assert abs(wrong.distance(0,4)-expected)>2
    wrong.lattice=Lattice(a,a,a,90,90,90);assert_allclose(wrong.distance(0,4),expected,atol=1e-12)
    return result(('build_crystal_reference',dict(atoms=8,cell_volume_A3=float(lattice.volume),si_si_distance_A=float(distance),cif_roundtrip=True)),
        ('repair_lattice_length_unit',dict(wrong_lattice_A=.543,repaired_lattice_A=5.43,repaired_distance_A=float(wrong.distance(0,4)))),
        'Eightmanufactureddiamondfractionalsites;a³volumeandnearestbondasqrt3/4 independentgeometry; CIFroundtripretainsSiandfractionalpositions.',
        'ManufacturedSiP1CIFpreservesexplicitsites; noautomaticspacegroupidentificationorDFTrelaxationclaim.')
FUNCTIONS={'07.05.01':integration,'07.05.02':reciprocal,'07.05.03':reflectivity,'07.05.04':structure}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);a=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    for sid,fn in FUNCTIONS.items():
        if a.scene and a.scene!=sid:continue
        value=fn();value.update(scenario_id=sid,status='passed',versions={n:importlib.metadata.version(n) for n in ['pyFAI','xrayutilities','diffpy.structure','lmfit','numpy']},executed_script=Path(__file__).as_posix())
        (BASE/f'{sid}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');print(sid,value['tasks'],flush=True)
if __name__=='__main__':main()
