"""AbiPy input repair and official ABINIT reference-result analysis.

No external ABINIT executable is invoked. NetCDF reader and explicit numeric
formulas provide an oracle independent of AbiPy's high-level result wrappers.
"""
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import numpy as np
from netCDF4 import Dataset
from abipy.abio.inputs import AbinitInput,MultiDataset
from abipy.electrons.gsr import GsrFile

BASE=Path('seed_gen/scenario_collection/materials_solver');OUT=BASE/'runtime/abipy'
SRC=Path('seed_pypi_raw/abipy')
PSEUDO=SRC/'abipy/data/pseudos/14si.pspnc'
GSR=SRC/'abipy/data/refs/si_ebands/si_scf_GSR.nc'
CELL=dict(acell=[10.217]*3,rprim=[[0,.5,.5],[.5,0,.5],[.5,.5,0]],natom=2,ntypat=1,znucl=[14],typat=[1,1],xred=[[0,0,0],[.25,.25,.25]])
def task(i,**kw):return dict(id=i,status='passed',**kw)

def input_repair():
    multi=MultiDataset(structure=CELL,pseudos=[str(PSEUDO.resolve())],ndtset=2)
    multi.set_vars(ecut=6,nband=8)
    misspelled=False
    try:multi[0].set_vars(ecu=6)
    except AbinitInput.Error:misspelled=True
    assert misspelled
    multi[0].set_kmesh([2,2,2],[0,0,0]);multi[0].set_vars(tolvrs=1e-8)
    multi[1].set_kmesh([2,2,2],[0,0,0]);wrong_mode=multi[1]['kptopt'];assert wrong_mode==1
    multi[1].set_kpath(ndivsm=6,kptbounds=[[.5,0,0],[0,0,0],[0,.5,.5]])
    multi[1].set_vars(tolwfr=1e-12)
    scf,nscf=multi.split_datasets()
    assert scf['kptopt']==1 and nscf['kptopt']==-2 and nscf['iscf']==-2
    assert scf.num_valence_electrons==8 and nscf.num_valence_electrons==8 and len(scf.structure)==2
    volume=(10.217*.529177210903)**3/4
    assert math.isclose(scf.structure.volume,volume,rel_tol=1e-7)
    scf.write(str(OUT/'si_scf.abi'));nscf.write(str(OUT/'si_nscf.abi'))
    scf.set_vars(ecut=8);assert nscf['ecut']==6
    restored=AbinitInput.from_dict(nscf.as_dict());assert restored['kptopt']==-2 and restored.num_valence_electrons==8
    return task('repair_scf_nscf_roles',invalid_variable_rejected=True,wrong_nscf_kptopt=wrong_mode,scf_kptopt=scf['kptopt'],nscf_kptopt=nscf['kptopt'],nscf_iscf=nscf['iscf'],natom=2,valence_electrons=8,volume_A3=float(scf.structure.volume),independent_volume_A3=volume,dataset_mutation_isolated=True,boundary='Prepared/serialized inputs only; ABINIT not executed')

def result_units():
    # ABINIT etotal is Hartree. Use fixed CODATA conversion independent of AbiPy.
    hartree_eV=27.211386245988
    with Dataset(GSR) as raw:
        etotal=float(raw.variables['etotal'][...]);natom=len(raw.dimensions['number_of_atoms'])
        eigen=np.asarray(raw.variables['eigenvalues'][...],dtype=float)
        occup=np.asarray(raw.variables['occupations'][...],dtype=float)
        kweight=np.asarray(raw.variables['kpoint_weights'][...],dtype=float)
        oracle_gap=float((eigen[0,:,4].min()-eigen[0,:,3].max())*hartree_eV)
    with GsrFile(str(GSR)) as gsr:
        energy=float(gsr.energy);per_atom=float(gsr.energy_per_atom);gap=float(gsr.ebands.fundamental_gaps[0].energy)
        wrong=etotal # deliberate Ha scalar mislabeled eV
        assert abs(wrong-energy)>200 and math.isclose(energy,etotal*hartree_eV,rel_tol=1e-7)
        assert math.isclose(per_atom,energy/natom,rel_tol=1e-12)
        assert math.isclose(gap,oracle_gap,rel_tol=1e-7)
        # Header metadata and independent occupation-weight sum agree.
        electrons=float(np.sum(occup[0]*kweight[:,None]));assert math.isclose(electrons,8,abs_tol=1e-12) and gsr.nelect==8
        return task('repair_gsr_energy_units',wrong_eV=wrong,correct_eV=energy,oracle_eV=etotal*hartree_eV,per_atom_eV=per_atom,natom=natom,gap_eV=gap,independent_gap_eV=oracle_gap,electrons=electrons,abinit_reference_version=gsr.abinit_version,boundary='Released Si GSR fixture from ABINIT8.0.6; no fresh DFT/DFPT run')

def main():
    OUT.mkdir(parents=True,exist_ok=True);first=[input_repair(),result_units()];second=[input_repair(),result_units()];assert first==second
    report=dict(scenario_id='01.07.02',status='passed',tasks=first,repeat_runs=2,repeat_equal=True,versions={p:importlib.metadata.version(p) for p in ['abipy','ase','pymatgen-core','numpy','netCDF4']},backend_executable=shutil.which('abinit'),source_commit='414ef88c4523fef2677da0fd954444e17884e536',fixtures=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [PSEUDO,GSR]],boundaries=['Input generation/serialization and official output analysis only.','No newly executed ABINIT or DFPT backend; model limits and convergence are not production validation.'])
    (OUT/'01.07.02.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    installed=sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions())
    (OUT/'requirements.freeze.txt').write_text('\n'.join(installed)+'\n',encoding='utf-8')
    print('01.07.02 passed',len(first),flush=True)

if __name__=='__main__':main()
