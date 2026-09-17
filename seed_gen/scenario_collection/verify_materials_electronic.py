"""Fixed diffusion, band postprocessing, effective-mass and parameter fixtures.

Manufactured trajectories/bands have independent analytic oracles. The released
Sumo SOC DOS fixture and openbandparams reference tables are identified separately.
No MD, DFT, TCAD or k.p calculation is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import traceback
import warnings

import numpy as np
from scipy import constants

OUT = Path('seed_gen/scenario_collection/runtime/materials_electronic')


def save(sid, tasks, metrics, scope, provenance=None):
    result = {'scenario_id': sid, 'status': 'passed', 'fixture_version': 1,
              'versions': {p: importlib.metadata.version(p) for p in
                           ('sumo', 'effmass', 'openbandparams', 'pymatgen-analysis-diffusion', 'pymatgen-core', 'ase', 'numpy')},
              'tasks': [{'id': name, 'status': 'passed', 'assertions': checks} for name, checks in tasks],
              'metrics': metrics, 'scope': scope, 'provenance': provenance or []}
    (OUT / f'{sid}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(sid, json.dumps(metrics), flush=True)


def diffusion():
    from pymatgen.core import Lattice, Structure
    from pymatgen.analysis.diffusion.analyzer import DiffusionAnalyzer, fit_arrhenius, get_extrapolated_diffusivity

    structure = Structure(Lattice.cubic(10), ['Li', 'Si', 'Si'], [[.1,.1,.1],[.4,.4,.4],[.7,.7,.7]])
    dt_fs = 2 * 5
    times = np.arange(101) * dt_fs
    target = 2e-5  # cm2/s; 1 Angstrom2/fs = 0.1 cm2/s
    drift = times[:, None] * np.array([.0002, -.0001, .0003])
    displacements = np.broadcast_to(drift, (3, 101, 3)).copy()
    displacements[0] += np.sqrt(2 * target * 10 * times)[:, None]

    def analyze(step_skip):
        return DiffusionAnalyzer(structure, displacements, 'Li', 600, 2, step_skip, smoothed=False)

    wrong = analyze(1)
    assert not np.isclose(wrong.diffusivity, target, rtol=1e-9, atol=0)
    good = analyze(5)
    np.testing.assert_allclose(good.diffusivity, target, rtol=1e-10)
    np.testing.assert_allclose(good.diffusivity_components, [target]*3, rtol=1e-10)
    np.testing.assert_allclose(good.msd, 6 * target * 10 * times, atol=1e-12)
    corrected = list(good.get_drift_corrected_structures())
    np.testing.assert_allclose(corrected[-1].cart_coords[1:], structure.cart_coords[1:], atol=1e-12)
    encoded = good.as_dict()
    (OUT/'diffusion.json').write_text(json.dumps(encoded), encoding='utf-8')
    recovered = DiffusionAnalyzer.from_dict(json.loads((OUT/'diffusion.json').read_text(encoding='utf-8')))
    np.testing.assert_allclose(recovered.diffusivity, target, rtol=1e-10)
    good.export_msdt(str(OUT/'msd.csv'))

    temperatures = np.array([500.,650.,800.,950.])
    activation, prefactor = .3, .01
    kb_ev = constants.Boltzmann/constants.elementary_charge
    values = prefactor*np.exp(-activation/(kb_ev*temperatures))
    wrong_fit = fit_arrhenius(temperatures-273.15, values)
    assert not np.isclose(wrong_fit[0], activation, rtol=.01)
    fit = fit_arrhenius(temperatures, values)
    np.testing.assert_allclose(fit[:2], [activation,prefactor], rtol=1e-10)
    extrapolated = get_extrapolated_diffusivity(temperatures,values,400)
    np.testing.assert_allclose(extrapolated,prefactor*np.exp(-activation/(kb_ev*400)),rtol=1e-10)
    save('01.05.03', [
        ('repair_trajectory_timestep', ['step_skip=1 fails fixed D target', 'step_skip=5 restores D=2e-5 cm2/s and all components', 'MSD=6Dt with unit conversion', 'framework drift removed', 'JSON restoration agrees']),
        ('repair_arrhenius_temperature', ['Celsius values fail activation target', 'Kelvin restores Ea=.3 eV, D0=.01 cm2/s', '400 K extrapolation matches independent exponential'])],
        {'wrong_diffusivity_cm2_s':float(wrong.diffusivity),'diffusivity_cm2_s':float(good.diffusivity),
         'activation_eV':float(fit[0]),'prefactor_cm2_s':float(fit[1]),'extrapolated_400K_cm2_s':float(extrapolated)},
        'Manufactured drift-plus-sqrt(time) trajectory and Arrhenius data; verifies analysis and units, not MD sampling or a real material migration barrier.')


def bands():
    from pymatgen.core import Lattice
    from pymatgen.electronic_structure.bandstructure import BandStructureSymmLine
    from pymatgen.electronic_structure.core import Spin
    from pymatgen.io.vasp.outputs import Vasprun
    from sumo.electronic_structure.bandstructure import get_reconstructed_band_structure
    from sumo.electronic_structure.dos import load_dos, write_files

    q = np.linspace(0,1,21)
    k = np.column_stack([q/2,np.zeros((21,2))])
    eigenvalues = np.array([-q*q,1.2+(q-1)**2])
    lattice = Lattice.cubic(5).reciprocal_lattice
    labels = {'G': k[0], 'X':k[-1]}
    parts = [BandStructureSymmLine(k[sl], {Spin.up:eigenvalues[:,sl]},lattice,.6,labels_dict=labels)
             for sl in (slice(0,11),slice(11,None))]
    bad = get_reconstructed_band_structure(parts,efermi=2.)
    assert bad.is_metal()
    merged = get_reconstructed_band_structure(parts,efermi=.6)
    gap = merged.get_band_gap()
    assert not merged.is_metal() and not gap['direct']
    np.testing.assert_allclose(gap['energy'],1.2,atol=1e-12)
    np.testing.assert_allclose(merged.get_direct_band_gap(),1.7,atol=1e-12)
    np.testing.assert_allclose(merged.get_vbm()['energy'],0,atol=1e-12)
    np.testing.assert_allclose(merged.get_cbm()['energy'],1.2,atol=1e-12)
    (OUT/'bandstructure.json').write_text(json.dumps(merged.as_dict()),encoding='utf-8')
    restored = BandStructureSymmLine.from_dict(json.loads((OUT/'bandstructure.json').read_text(encoding='utf-8')))
    np.testing.assert_allclose(restored.get_band_gap()['energy'],1.2,atol=1e-12)

    fixture = Path('seed_pypi_raw/sumo/tests/data/Cs2SnBr6/vasprun.xml.gz')
    vasprun = Vasprun(str(fixture),parse_potcar_file=False)
    dos,pdos = load_dos(vasprun,adjust_fermi=False)
    assert len(dos.energies)==2000 and set(dos.densities)=={Spin.up}
    projected_sum = sum(item.densities[Spin.up] for values in pdos.values() for item in values.values())
    source_sum = sum(values[Spin.up] for site in vasprun.complete_dos.pdos.values() for values in site.values())
    np.testing.assert_allclose(projected_sum,source_sum,rtol=1e-12,atol=1e-12)
    expected_energy = dos.energies - dos.efermi
    write_files(dos,pdos,prefix='wrong',directory=str(OUT),zero_to_efermi=False)
    wrong_energy = np.loadtxt(OUT/'wrong_total_dos.dat')[:,0]
    assert not np.allclose(wrong_energy,expected_energy,atol=1e-10)
    write_files(dos,pdos,prefix='correct',directory=str(OUT),zero_to_efermi=True)
    exported = np.loadtxt(OUT/'correct_total_dos.dat')
    np.testing.assert_allclose(exported[:,0],expected_energy,atol=1e-10)
    np.testing.assert_allclose(exported[:,1],dos.densities[Spin.up],rtol=1e-12)
    save('01.06.01', [
        ('repair_band_reference', ['wrong Fermi level classifies model as metal', 'repair yields indirect gap 1.2 eV and direct gap 1.7 eV', 'VBM0 CBM1.2', 'split merge and JSON restoration preserve result']),
        ('repair_dos_export_reference', ['released SOC fixture has 2000 energies and one spin channel', 'element/orbital sums equal raw site/orbital projections', 'unshifted export fails E-EF target', 'corrected energies and densities round-trip'])],
        {'indirect_gap_eV':float(gap['energy']),'direct_gap_eV':float(merged.get_direct_band_gap()),
         'dos_energy_points':len(dos.energies),'dos_elements':sorted(pdos),'dos_fermi_eV':float(dos.efermi)},
        'Analytic two-band fixture plus released Cs2SnBr6 SOC DOS postprocessing; no new DFT or physical strain prediction.',
        [{'path':fixture.as_posix(),'sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'source':'sumo v3.0.0 official test fixture'}])


def effective_mass():
    from ase import Atoms
    from ase.dft.kpoints import BandPath
    from ase.spectrum.band_structure import BandStructure
    from effmass.inputs import DataASE
    from effmass.analysis import Segment

    atoms = Atoms('Si',positions=[[0,0,0]],cell=[5,5,5],pbc=True)
    coefficient = constants.hbar**2/(2*constants.m_e*constants.e)*1e20  # eV Angstrom2
    captured_warnings = []

    def segment(axis,mass,nonparabolic=False,indices=None,band=1):
        kval = np.linspace(0,.1,21)
        coords = np.zeros((21,3)); coords[:,axis] = kval*5/(2*np.pi)
        # effmass 2.3 extrema masking drops a turning point at exactly E=0.
        # Translate every energy and EF by -1 eV; curvature is invariant.
        conduction = .5 + coefficient/mass*kval**2 + (2000*kval**4 if nonparabolic else 0)
        valence = -1 - coefficient/.5*kval**2
        path = BandPath(atoms.cell,kpts=coords,special_points={})
        bs = BandStructure(path,np.array([np.column_stack([valence,conduction])]),reference=-.25)
        with warnings.catch_warnings(record=True) as notes:
            warnings.simplefilter('always')
            data = DataASE(bs,atoms)
        captured_warnings.extend(str(n.message) for n in notes)
        assert np.max(valence)<data.fermi_energy<np.min(conduction)
        np.testing.assert_allclose(data.reciprocal_lattice,np.eye(3)*2*np.pi/5,atol=1e-12)
        return Segment(data,band,list(range(21)) if indices is None else indices)

    # effmass2.3 uses rounded eV/Hartree and Angstrom/Bohr constants; 2e-5
    # relative tolerance accommodates those documented conversion constants.
    mx = segment(0,.2).five_point_leastsq_effmass()
    my = segment(1,.4).finite_difference_effmass()
    hole = segment(0,.2,band=0).five_point_leastsq_effmass()
    np.testing.assert_allclose([mx,my,hole],[.2,.4,-.5],rtol=2e-5)
    wide = segment(0,.2,True,indices=[0,10,20]).five_point_leastsq_effmass()
    assert not np.isclose(wide,.2,rtol=.02)
    narrow = segment(0,.2,True,indices=[0,1,2]).five_point_leastsq_effmass()
    np.testing.assert_allclose(narrow,.2,rtol=.02)
    assert any('occupancy' in w for w in captured_warnings)
    save('01.06.02', [
        ('directional_effective_mass', ['real ASE BandStructure to DataASE bridge', 'Fermi level explicitly inside gap', 'reciprocal basis includes 2pi', 'electron x=.2 y=.4 m0 and valence curvature=-.5 m0']),
        ('repair_mass_fit_window', ['quartic dispersion wide window fails fixed 2 percent target', 'narrow window passes same target', 'positive electron curvature'])],
        {'electron_x_m0':float(mx),'electron_y_m0':float(my),'valence_curvature_m0':float(hole),
         'wide_window_m0':float(wide),'narrow_window_m0':float(narrow),'expected_warning':captured_warnings[0]},
        'Manufactured parabolic and quartic dispersions, not material-specific DFT bands. Signed valence curvature is negative; physical hole mass is its magnitude. Window sensitivity is demonstrated, not general convergence.')


def parameters():
    from openbandparams import GaAs,InAs,InP,GaInAs

    expected = 1.519 - .0005405*300**2/(300+204)
    wrong = GaAs.Eg(T=0)
    assert not np.isclose(wrong,expected,atol=1e-10)
    right = GaAs.Eg(T=300)
    np.testing.assert_allclose(right,expected,atol=1e-12)
    p = GaAs.get_parameter('Eg')
    # Eg is a minimum across valleys and has no attached references in v1.0;
    # retrieve the actual Gamma-branch dependencies instead of inventing them.
    gamma_parameter = GaAs.get_parameter('Eg_Gamma')
    assert p.units=='eV' and gamma_parameter.get_references()
    unique = GaAs.get_unique_parameters()
    assert len({x.name for x in unique})==len(unique)

    temperature = 800
    target = InP.a(T=temperature)
    wrong_alloy = GaInAs(a=target)  # lattice matching defaults to 300 K
    mismatch = wrong_alloy.a(T=temperature)-target
    assert abs(mismatch)>1e-4
    matched = GaInAs(a=target,T=temperature)
    x = matched.element_fraction('Ga')
    oracle_x = (target-InAs.a(T=temperature))/(GaAs.a(T=temperature)-InAs.a(T=temperature))
    np.testing.assert_allclose(x,oracle_x,atol=1e-10)
    np.testing.assert_allclose(matched.a(T=temperature),target,atol=1e-10)
    try:
        GaInAs(x=1.1)
    except ValueError:
        pass
    else:
        raise AssertionError('Out-of-range alloy composition was accepted')
    output = {'material': 'GaInAs', 'Ga_fraction':x,'In_fraction':matched.element_fraction('In'),
              'temperature_K':temperature,'a_A':matched.a(T=temperature),'Eg_eV':matched.Eg(T=temperature),
              'CBO_eV':matched.CBO(T=temperature),'VBO_eV':matched.VBO(T=temperature),
              'GaAs_Eg_parameter':{'units':p.units,'description':p.description,'reference_count':len(p.get_references()),
                                  'Gamma_reference_count':len(gamma_parameter.get_references())}}
    (OUT/'parameter_selection.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    reconstructed = GaInAs(x=json.loads((OUT/'parameter_selection.json').read_text())['Ga_fraction'])
    np.testing.assert_allclose(reconstructed.a(T=temperature),target,atol=1e-10)
    save('01.06.03', [
        ('repair_parameter_temperature', ['T0 bandgap fails 300 K target', 'T300 agrees independent Varshni formula', 'units and references present; unique parameter names']),
        ('repair_alloy_matching_temperature', ['implicit 300 K match fails 800 K target', 'explicit 800 K matches InP and independent Vegard composition', 'fraction1.1 rejected', 'JSON composition restoration agrees'])],
        {'GaAs_Eg_300K_eV':float(right),'GaAs_Eg_0K_eV':float(wrong),'GaInAs_Ga_fraction_800K':float(x),
         'target_lattice_A':float(target),'wrong_temperature_mismatch_A':float(mismatch)},
        'Versioned openbandparams empirical III-V zinc-blende tables; selected material/temperature/composition and reference metadata only. No TCAD, k.p or strain solver; extrapolation uncertainty not quantified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',choices=['diffusion','bands','mass','parameters','all'],default='all')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    for name, function in [('diffusion',diffusion),('bands',bands),('mass',effective_mass),('parameters',parameters)]:
        if args.scene not in (name,'all'):
            continue
        try:
            function()
        except Exception:
            failure = OUT/f'attempt_{name}_failure.json'
            # Keep the first concrete failure rather than overwrite its evidence.
            if not failure.exists():
                failure.write_text(json.dumps({'scene':name,'status':'failed','traceback':traceback.format_exc()},indent=2),encoding='utf-8')
            raise


if __name__ == '__main__':
    main()
