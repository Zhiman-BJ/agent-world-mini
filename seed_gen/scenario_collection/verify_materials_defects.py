"""Fixed defect geometry and supplied-energy thermodynamic oracles, without DFT."""
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from pymatgen.core import Lattice, Structure
from pymatgen.analysis.defects.generators import VacancyGenerator, SubstitutionGenerator, InterstitialGenerator
from pymatgen.entries.computed_entries import ComputedStructureEntry
from doped.core import Vacancy, DefectEntry
from doped.thermodynamics import DefectThermodynamics

OUT = Path('seed_gen/scenario_collection/runtime/materials_defects')


def report(sid, tasks, metrics, scope):
    payload = {'scenario_id': sid, 'status': 'passed',
               'versions': {p: importlib.metadata.version(p) for p in ('pymatgen-core', 'pymatgen-analysis-defects', 'doped')},
               'tasks': [{'id': t, 'status': 'passed'} for t in tasks], 'metrics': metrics, 'scope': scope}
    (OUT/f'{sid}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(sid, json.dumps(metrics), flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bulk = Structure.from_spacegroup(216, Lattice.cubic(5.653), ['Ga', 'As'], [[0, 0, 0], [.25, .25, .25]])
    vacancies = list(VacancyGenerator(symprec=1e-5).generate(bulk, oxi_state=0))
    assert len(vacancies) == 2
    assert sorted(v.get_multiplicity() for v in vacancies) == [4, 4]
    by_element = {str(v.site.specie): v for v in vacancies}
    wrong = by_element['As'].get_supercell_structure(sc_mat=np.eye(3, dtype=int)*2)
    fixed = by_element['Ga'].get_supercell_structure(sc_mat=np.eye(3, dtype=int)*2)
    target = {'Ga': 31., 'As': 32.}
    assert wrong.composition.get_el_amt_dict() != target
    assert fixed.composition.get_el_amt_dict() == target and len(fixed) == 63
    np.testing.assert_allclose(fixed.volume, bulk.volume*8, rtol=1e-12)
    restored = Structure.from_dict(fixed.as_dict())
    np.testing.assert_allclose(restored.frac_coords, fixed.frac_coords)
    fixed.to(filename=OUT/'Ga_vacancy.json')
    substitutes = list(SubstitutionGenerator(symprec=1e-5).generate(bulk, {'Ga': 'Al'}, oxi_state=0))
    assert len(substitutes) == 1 and substitutes[0].get_multiplicity() == 4
    subcell = substitutes[0].get_supercell_structure(sc_mat=np.eye(3, dtype=int)*2)
    assert subcell.composition.get_el_amt_dict() == {'Ga': 31., 'As': 32., 'Al': 1.}
    generator = InterstitialGenerator(min_dist=1.)
    blocked = list(generator.generate(bulk, {'H': [bulk[0].frac_coords]}, oxi_state=0))
    assert not blocked
    interstitials = list(generator.generate(bulk, {'H': [[.5, .5, .5]]}, oxi_state=0))
    assert len(interstitials) == 1
    intcell = interstitials[0].get_supercell_structure(sc_mat=np.eye(3, dtype=int)*2)
    assert intcell.composition.get_el_amt_dict() == {'Ga': 32., 'As': 32., 'H': 1.}
    report('01.04.01', ['enumerate_and_repair_vacancy', 'substitution_and_interstitial'],
           {'vacancy_species': sorted(by_element), 'multiplicities': [4, 4], 'wrong_vacancy_composition': wrong.composition.get_el_amt_dict(),
            'repaired_vacancy_composition': target, 'substitution_composition': subcell.composition.get_el_amt_dict(),
            'interstitial_composition': intcell.composition.get_el_amt_dict(), 'collision_rejected': True},
           'Symmetry-distinct vacancies/substitutions and explicitly supplied interstitial sites; geometry/composition, not formation energy or exhaustive interstitial search.')

    defect = Vacancy(bulk, bulk[0], oxi_state=0, multiplicity=4)
    defect_structure = defect.defect_structure
    bulk_energy = -80.
    entries = []
    # With mu_Ga=-3 and VBM=0, these yield E_f(q=0)=1.5, E_f(q=1)=0.5+EF.
    for charge, delta in [(0, 4.5), (1, 3.5)]:
        entries.append(DefectEntry(defect=defect, charge_state=charge,
                                  sc_entry=ComputedStructureEntry(defect_structure, bulk_energy+delta),
                                  bulk_entry=ComputedStructureEntry(bulk, bulk_energy),
                                  sc_defect_frac_coords=tuple(bulk[0].frac_coords),
                                  name=f'v_Ga_{charge:+d}', calculation_metadata={'vbm': 0., 'gap': 2.}))
    chemical = {'Ga': -3., 'As': -4.}
    grid = np.array([0., .5, 1., 1.5, 2.])
    energies = np.array([[entry.formation_energy(chempots=chemical, vbm=0., fermi_level=float(ef)) for ef in grid] for entry in entries])
    np.testing.assert_allclose(energies[0], 1.5, atol=1e-10)
    np.testing.assert_allclose(energies[1], .5+grid, atol=1e-10)
    thermo = DefectThermodynamics(entries, chempots=chemical, vbm=0., band_gap=2., check_compatibility=False)
    transitions = [float(v) for levels in thermo.transition_level_map.values() for v in levels]
    np.testing.assert_allclose(transitions, [1.], atol=1e-9)
    assert entries[int(np.argmin(energies[:, 1]))].charge_state == 1
    assert entries[int(np.argmin(energies[:, 3]))].charge_state == 0
    # A deliberately wrong chemical potential must not pass the fixed energy target.
    wrong_energy = entries[0].formation_energy(chempots={'Ga': 3., 'As': -4.}, vbm=0., fermi_level=.5)
    assert not np.isclose(wrong_energy, 1.5)
    fixed_energy = entries[0].formation_energy(chempots=chemical, vbm=0., fermi_level=.5)
    np.testing.assert_allclose(fixed_energy, 1.5, atol=1e-10)
    # These are controlled input corrections, not computed electrostatic corrections.
    entries[1].corrections['fixture_offset'] = .2
    corrected = entries[1].formation_energy(chempots=chemical, vbm=0., fermi_level=.5)
    np.testing.assert_allclose(corrected, 1.2, atol=1e-10)
    entries[1].corrections.clear()
    entries[0].to_json(OUT/'formation_entry.json')
    roundtrip = DefectEntry.from_json(OUT/'formation_entry.json')
    np.testing.assert_allclose(roundtrip.formation_energy(chempots=chemical, vbm=0., fermi_level=.5), 1.5, atol=1e-10)
    report('01.04.03', ['formation_charge_transition', 'repair_chemical_potential'],
           {'fermi_grid_eV': grid.tolist(), 'formation_energies_eV': energies.tolist(), 'transition_eV': transitions,
            'wrong_mu_energy_eV': wrong_energy, 'repaired_energy_eV': fixed_energy, 'specified_correction_energy_eV': corrected},
           'Real doped energy/transition analysis of explicitly supplied toy energies; no DFT, computed finite-size correction, or chemical-potential stability region validation. check_compatibility=False only because inputs are manufactured.')


if __name__ == '__main__':
    main()
