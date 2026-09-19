"""Distortion geometry and supplied-energy ranking; no electronic relaxation."""
import importlib.metadata
import json
from pathlib import Path
import numpy as np
from pymatgen.core import Lattice, Structure
from doped.core import Vacancy
from shakenbreak.distortions import distort, rattle
from shakenbreak.analysis import get_gs_distortion

OUT = Path('seed_gen/scenario_collection/runtime/materials_reconstruction')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bulk = Structure.from_spacegroup(216, Lattice.cubic(5.653), ['Ga', 'As'], [[0, 0, 0], [.25, .25, .25]])
    defect = Vacancy(bulk, bulk[0], oxi_state=0, multiplicity=4)
    structure, site, _ = defect.get_supercell_structure(sc_mat=np.eye(3, dtype=int)*2, return_sites=True)
    original = structure.copy()
    def generate(factor):
        return distort(structure, 4, factor, frac_coords=site.frac_coords)
    bad = generate(1.2)
    good = generate(.8)
    indices = [i for i, _ in good['distorted_atoms']]
    assert len(set(indices)) == 4
    baseline = np.array([structure.lattice.get_distance_and_image(structure[i].frac_coords, site.frac_coords)[0] for i in indices])
    target = .8*np.sqrt(3)*5.653/4
    def distances(result):
        s = result['distorted_structure']
        return np.array([s.lattice.get_distance_and_image(s[i].frac_coords, site.frac_coords)[0] for i in indices])
    assert not np.allclose(distances(bad), target, atol=1e-8)
    np.testing.assert_allclose(distances(good), target, atol=1e-8)
    assert good['distorted_structure'].composition == structure.composition
    np.testing.assert_allclose(structure.frac_coords, original.frac_coords, atol=1e-12)
    # Fixed randomness is necessary for replay; Monte Carlo d_min is not a hard constraint.
    rattled1 = rattle(good['distorted_structure'], stdev=.02, d_min=1.8, seed=23)
    rattled2 = rattle(good['distorted_structure'], stdev=.02, d_min=1.8, seed=23)
    np.testing.assert_allclose(rattled1.frac_coords, rattled2.frac_coords, atol=1e-12)
    displacement = np.linalg.norm(rattled1.cart_coords-good['distorted_structure'].cart_coords, axis=1)
    assert np.max(displacement) > .001
    rattled1.to(filename=OUT/'candidate.json')
    restored = Structure.from_file(OUT/'candidate.json')
    np.testing.assert_allclose(restored.frac_coords, rattled1.frac_coords, atol=1e-12)
    # Energy values are explicit fixture input, not computed from the structures.
    wrong = {'Unperturbed': -10., 'distortions': {-.2: -10800., .2: -10200.}}
    delta_bad, _ = get_gs_distortion(wrong)
    assert not np.isclose(delta_bad, -.8)
    energies = {'Unperturbed': -10., 'distortions': {k: v/1000 for k, v in wrong['distortions'].items()}}
    energies['distortions']['High_Energy_rejected'] = -100.
    delta, best = get_gs_distortion(energies)
    np.testing.assert_allclose(delta, -.8, atol=1e-12)
    assert best == -.2
    (OUT/'energies.json').write_text(json.dumps(energies, indent=2)+'\n', encoding='utf-8')
    payload = {'scenario_id': '01.04.02', 'status': 'passed',
               'versions': {p: importlib.metadata.version(p) for p in ('shakenbreak', 'doped', 'pymatgen-core', 'hiphive')},
               'tasks': [{'id': 'repair_bond_distortion', 'status': 'passed'}, {'id': 'repair_energy_units', 'status': 'passed'}],
               'metrics': {'nearest_indices': indices, 'initial_distances_A': baseline.tolist(), 'repaired_distances_A': distances(good).tolist(),
                           'rattle_max_displacement_A': float(displacement.max()), 'best_distortion': best, 'energy_lowering_eV': delta, 'wrong_units_lowering': delta_bad},
               'scope': 'Actual doped->ShakeNBreak structure bridge, deterministic rattle and supplied-energy ranking. No relaxation or DFT; lowest energy means only the fixed candidate table, not a physical global ground state.'}
    (OUT/'01.04.02.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(payload['metrics']))


if __name__ == '__main__':
    main()
