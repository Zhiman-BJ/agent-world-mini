"""Harmonic periodic-spring fixture; the force constants are explicit test inputs."""
import importlib.metadata
import json
from pathlib import Path
import numpy as np
from scipy.constants import elementary_charge, atomic_mass
from phonopy import Phonopy, load
from phonopy.structure.atoms import PhonopyAtoms

OUT=Path('seed_gen/scenario_collection/runtime/materials_phonon')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    unit=PhonopyAtoms(symbols=['Si'],masses=[28.],cell=np.eye(3)*5.43,scaled_positions=[[0,0,0]])
    ph=Phonopy(unit,np.diag([3,1,1]),primitive_matrix=np.eye(3),is_symmetry=False)
    k=2.0
    fc=np.zeros((3,3,3,3))
    for i in range(3):
        fc[i,i]=2*k*np.eye(3)
        fc[i,(i+1)%3]=-k*np.eye(3)
        fc[i,(i-1)%3]=-k*np.eye(3)
    ph.force_constants=fc
    q=np.array([[0.,0,0],[0.25,0,0],[0.5,0,0]])
    expected=np.sqrt(4*k/28)*np.abs(np.sin(np.pi*q[:,0]))*np.sqrt(elementary_charge/atomic_mass)/1e-10/(2*np.pi*1e12)
    frequencies=ph.run_qpoints(q).frequencies.copy()
    np.testing.assert_allclose(frequencies,np.repeat(expected[:,None],3,axis=1),atol=1e-6,rtol=3e-6)
    np.testing.assert_allclose(fc.sum(axis=1),0,atol=1e-14)
    ph.run_band_structure([q])
    ph.run_mesh([20,1,1],is_mesh_symmetry=False)
    dos=ph.run_total_dos(sigma=0.15,freq_min=-1.,freq_max=float(expected[-1]+1),freq_pitch=.01,use_tetrahedron_method=False)
    integral=np.trapezoid(dos.dos,dos.frequency_points)
    np.testing.assert_allclose(integral,3,atol=1e-6)
    thermal=ph.run_thermal_properties(temperatures=[100.,300.,600.])
    assert np.isfinite(thermal.free_energy).all() and np.all(thermal.heat_capacity>0)
    ph.save(OUT/'spring_phonopy.yaml',settings={'force_constants':True})
    restored=load(OUT/'spring_phonopy.yaml',symmetrize_fc=False,is_symmetry=False)
    np.testing.assert_allclose(restored.run_qpoints([q[1]]).frequencies[0],frequencies[1],atol=1e-8)
    ph.force_constants=-fc
    wrong=ph.run_qpoints([q[1]]).frequencies[0].copy()
    assert np.min(wrong)<-1.
    ph.force_constants=fc
    repaired=ph.run_qpoints([q[1]]).frequencies[0].copy()
    assert np.min(repaired)>0
    np.testing.assert_allclose(repaired,expected[1],rtol=3e-6)
    payload={'scenario_id':'01.05.01','status':'passed','versions':{p:importlib.metadata.version(p) for p in ('phonopy','numpy','scipy')},
             'tasks':[{'id':'harmonic_band_dos','status':'passed'},{'id':'repair_unstable_force_constants','status':'passed'}],
             'metrics':{'frequencies_THz':frequencies.tolist(),'analytic_THz':expected.tolist(),'dos_integral':float(integral),
                        'wrong_frequencies_THz':wrong.tolist(),'repaired_frequencies_THz':repaired.tolist()},
             'scope':'Three-cell periodic spring model with Si label and prescribed mass28u; validates API/units/stability oracles, not an ab-initio silicon spectrum.'}
    (OUT/'01.05.01.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload['metrics']))


if __name__=='__main__': main()
