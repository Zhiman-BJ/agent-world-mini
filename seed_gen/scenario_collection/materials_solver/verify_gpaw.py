"""Real GPAW 26.7.0 SCF with its official, unmodified pure-Python backend.

Run under the private WSL environment. These are coarse-mesh interface and
conservation checks, not a production cutoff/k-point/force convergence study.
"""
import os
os.environ['GPAW_NO_C_EXTENSION']='1'
os.environ['OMP_NUM_THREADS']='1'
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'seed_pypi_raw/gpaw'))
import hashlib
import importlib.metadata
import json
import math
import subprocess
import numpy as np
from ase.build import bulk
from ase.io.ulm import Reader
from gpaw import KohnShamConvergenceError
import gpaw
from gpaw.new.ase_interface import GPAW

BASE=ROOT/'seed_gen/scenario_collection/materials_solver'
OUT=BASE/'runtime/gpaw'

def calculator(path,maxiter):
    return GPAW(mode={'name':'pw','ecut':100},xc='LDA',kpts=(1,1,1),nbands=6,
                random=True,symmetry='off',mixer={'name':'pulay'},
                convergence={'energy':1e-4,'density':1e-3},maxiter=maxiter,txt=str(path))

def run_once(index):
    folder=OUT/f'run{index}';folder.mkdir(parents=True,exist_ok=True)
    atoms=bulk('Si','diamond',a=5.43)
    atoms.calc=calculator(folder/'budget1.txt',1)
    failed=False
    try:atoms.get_potential_energy()
    except KohnShamConvergenceError:failed=True
    assert failed,'One-step SCF must fail the unchanged convergence criteria'
    atoms.calc=calculator(folder/'repaired.txt',80)
    energy=float(atoms.get_potential_energy())
    forces=atoms.get_forces()
    calc=atoms.calc
    steps=int(calc.get_number_of_iterations())
    # 26.7.0 default raw=False applies an in-place *= weight to the underlying
    # occupations. Use raw=True + a copied array to keep observation read-only.
    occupations=calc.get_occupation_numbers(raw=True).copy()*2
    assert 1<steps<=80 and np.isfinite(energy) and np.all(np.isfinite(forces))
    assert math.isclose(float(np.sum(occupations)),8,abs_tol=1e-10)
    assert calc.get_number_of_electrons()==8 and calc.get_number_of_bands()==6
    assert np.allclose(calc.get_k_point_weights(),[1])
    assert math.isclose(float(np.linalg.det(atoms.cell)),5.43**3/4,abs_tol=1e-10)
    gpw=folder/'si.gpw';calc.write(str(gpw))
    first=dict(id='repair_scf_budget',status='passed',wrong_maxiter=1,
               wrong_run_convergence_error=failed,correct_maxiter=80,iterations=steps,
               energy_eV=energy,electrons_from_occupations=float(np.sum(occupations)),
               expected_electrons=2*4,volume_A3=float(np.linalg.det(atoms.cell)),
               oracle_volume_A3=5.43**3/4,max_abs_force_eV_A=float(np.abs(forces).max()))
    # Read independent serialized scalar/energy terms through ASE ULM, without
    # using GPAW's result getters to calculate the comparison target.
    with Reader(str(gpw)) as raw:
        stored=float(raw.results.energy)
        terms=raw.energy_contributions.asdict()
        kinetic=terms.get('kinetic',terms['band']+terms['kinetic_correction']+terms.get('hybrid_kinetic_correction',0))
        reference_free=kinetic+sum(terms.get(k,0) for k in ('coulomb','zero','external','xc','entropy','spinorbit','hybrid_xc_cc','hybrid_xc_vc','hybrid_xc_vv'))
        oracle_energy=reference_free+terms['extrapolation']
        declared_ha_eV=float(raw.ha)
        assert raw.gpaw_version=='26.7.0' and raw.version==7
    restarted=GPAW(str(gpw),txt=str(folder/'restart.txt'))
    restored_atoms=restarted.get_atoms()
    restored=float(restored_atoms.get_potential_energy())
    wrong=stored*27.211386245988 # error: GPW stores results.energy in eV already
    assert abs(wrong-energy)>1
    assert math.isclose(restored,oracle_energy,abs_tol=1e-10)
    assert math.isclose(restored,energy,abs_tol=1e-10)
    assert np.allclose(restored_atoms.positions,atoms.positions,atol=1e-12)
    assert np.allclose(restored_atoms.cell,atoms.cell,atol=1e-12)
    assert np.array_equal(restored_atoms.numbers,[14,14])
    assert math.isclose(float(np.sum(restarted.get_occupation_numbers(raw=True).copy()*2)),8,abs_tol=1e-10)
    second=dict(id='repair_restart_energy_units',status='passed',wrong_eV=wrong,
                restored_eV=restored,oracle_energy_terms_eV=float(oracle_energy),
                stored_eV=stored,declared_ha_eV=declared_ha_eV,positions_and_cell_preserved=True,
                electrons_after_restart=8)
    return [first,second]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    first=run_once(1);second=run_once(2)
    assert first==second,'Fixed SCF initialization and checks must repeat exactly'
    datafiles=[p for folder in gpaw.setup_paths for p in Path(folder).glob('Si.LDA*')]
    assert datafiles,'Record the actual Si LDA PAW data'
    report=dict(scenario_id='01.07.01',status='passed',tasks=first,repeat_runs=2,
                repeat_equal=True,backend='GPAW official purepython via GPAW_NO_C_EXTENSION=1',
                source_commit='9c6f4ccd94355b3e8c1c418b4b605d7ce7552e30',
                versions={'gpaw':gpaw.__version__,**{p:importlib.metadata.version(p) for p in ['ase','numpy','scipy','gpaw-data']}},
                fixtures=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in datafiles],
                boundaries=['Actual fresh Si2 plane-wave SCF and native GPW restart ran; no fake extension or upstream patches.',
                            '100 eV and Gamma-only sampling are deliberately coarse; no production energy, force, gap or mesh convergence claim.',
                            'No compiled _gpaw, MPI, native libxc or broad XC/backend coverage; LDA only.',
                            'For 26.7.0 occupations use raw=True and copy before spin/weight scaling: default raw=False mutates the internal occupation array in this released new-calculator path.',
                            'ULM energy terms and 8-electron/diamond volume formulas are independent oracles for interface consistency, not external DFT accuracy benchmarks.'])
    (OUT/'01.07.01.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'requirements.freeze.txt').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True),encoding='utf-8')
    subprocess.run([sys.executable,'-m','pip','check'],check=True)
    print('01.07.01 passed',len(first),flush=True)

if __name__=='__main__':main()
