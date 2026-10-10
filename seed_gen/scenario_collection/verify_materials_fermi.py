"""Check published py-sc-fermi 3.0.0 against symmetric-DOS and charge oracles."""
import importlib.metadata
import json
from pathlib import Path
import numpy as np
from scipy.constants import physical_constants
from py_sc_fermi.dos import DOS
from py_sc_fermi.defect_charge_state import DefectChargeState
from py_sc_fermi.defect_species import DefectSpecies
from py_sc_fermi.defect_system import DefectSystem, DefectSystemFactory

OUT=Path('seed_gen/scenario_collection/runtime/materials_fermi')


def make_dos():
    energies=np.arange(-500,701,dtype=float)/100
    rho=np.where((energies<=0)|(energies>=2),1.,0.)
    return DOS(dos=rho,edos=energies,bandgap=2.,nelect=10)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    dos=make_dos()
    factory=DefectSystemFactory(defect_species=[],dos=dos,volume=100.)
    intrinsic=[factory.at(t).result for t in (400.,800.)]
    for result in intrinsic:
        np.testing.assert_allclose(result.fermi_energy,1.,atol=1e-7)
        np.testing.assert_allclose(result.n0,result.p0,rtol=2e-6)
        assert result.n0>0
    assert intrinsic[1].n0>100*intrinsic[0].n0
    donor=DefectSpecies('donor',1,[DefectChargeState(charge=1,fixed_concentration=1e-4)])
    wrong=DefectSystem([donor],dos=dos,volume=10.,temperature=600.).result
    system=DefectSystem([donor],dos=dos,volume=100.,temperature=600.)
    repaired=system.result
    target=1e18
    assert not np.isclose(wrong.n0,target,rtol=1e-5)
    np.testing.assert_allclose(repaired.n0,target,rtol=1e-5)
    balance=repaired.p0-repaired.n0+1e-4*1e24/100
    assert abs(balance)/target<1e-6
    # Integrate the Fermi-Dirac occupation independently using the immutable DOS.
    kt=physical_constants['Boltzmann constant in eV/K'][0]*600
    # Include zero-DOS gap samples so the trapezoid at the band onset is kept.
    # Cutting at E=2 would remove a half-bin from the same discretized DOS.
    mask=dos.edos>1
    occupation=1/(1+np.exp((dos.edos[mask]-repaired.fermi_energy)/kt))
    electron_integral=np.trapezoid(dos.dos[mask]*occupation,dos.edos[mask])*1e22
    np.testing.assert_allclose(repaired.n0,electron_integral,rtol=1e-6)
    reset=DefectSystem.from_dict(system.as_dict()).result
    np.testing.assert_allclose(reset.n0,repaired.n0,rtol=1e-12)
    (OUT/'donor_system.json').write_text(json.dumps(system.as_dict(),indent=2)+'\n',encoding='utf-8')
    payload={'scenario_id':'01.04.04','status':'passed','versions':{p:importlib.metadata.version(p) for p in ('py-sc-fermi','numpy','scipy')},
             'tasks':[{'id':'intrinsic_fermi_temperature','status':'passed'},{'id':'repair_concentration_units','status':'passed'}],
             'metrics':{'intrinsic':[r.as_dict() for r in intrinsic],'wrong_volume_n_cm3':wrong.n0,'target_n_cm3':target,'repaired':repaired.as_dict(),
                        'charge_balance_relative':abs(balance)/target,'independent_n_cm3':electron_integral},
             'scope':'Analytic symmetric model DOS and specified donor density; no material-specific DFT DOS or defect-energy computation.'}
    (OUT/'01.04.04.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload['metrics'],ensure_ascii=False))


if __name__=='__main__': main()
