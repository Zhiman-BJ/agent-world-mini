"""Execute fixed crystal construction and symmetry tasks with independent oracles."""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from ase.io import read, write
from pymatgen.core import Lattice, Structure
from pymatgen.io.ase import AseAtomsAdaptor
from pymatgen.core.interface import Interface
from pymatgen.core.surface import SlabGenerator
import spglib

OUT = Path('seed_gen/scenario_collection/runtime/materials_structure')
A = 5.653


def gaas():
    return Structure.from_spacegroup(216, Lattice.cubic(A), ['Ga', 'As'], [[0, 0, 0], [0.25, 0.25, 0.25]])


def cell(structure):
    return structure.lattice.matrix, structure.frac_coords, structure.atomic_numbers


def composition_passes(structure):
    return structure.composition.get_el_amt_dict() == {'Ga': 31.0, 'Al': 1.0, 'As': 32.0}


def record(sid, tasks, metrics):
    payload = {'scenario_id': sid, 'status': 'passed', 'fixture_version': 1,
               'versions': {p: importlib.metadata.version(p) for p in ('pymatgen-core', 'ase', 'spglib', 'numpy')},
               'tasks': tasks, 'metrics': metrics,
               'scope': 'Fixed local fixtures only; no DFT, relaxation, heterostructure energetics or complete Agent server.'}
    (OUT / (sid + '.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(sid, json.dumps(metrics))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conventional = gaas()
    assert conventional.num_sites == 8
    assert conventional.composition.get_el_amt_dict() == {'Ga': 4.0, 'As': 4.0}
    supercell = conventional.copy()
    supercell.make_supercell([2, 2, 2])
    assert supercell.num_sites == 64
    np.testing.assert_allclose(supercell.volume, 8 * A ** 3, rtol=1e-12)
    ga_idx = supercell.indices_from_symbol('Ga')[0]
    as_idx = supercell.indices_from_symbol('As')[0]
    wrong = supercell.copy()
    wrong.replace(as_idx, 'Al')
    assert not composition_passes(wrong), 'Wrong sublattice must fail the same immutable composition oracle'
    repaired = supercell.copy()
    repaired.replace(ga_idx, 'Al')
    assert composition_passes(repaired)
    assert repaired.is_valid(tol=0.5)
    atoms = AseAtomsAdaptor.get_atoms(repaired)
    write(OUT / 'AlGaAs.extxyz', atoms, format='extxyz')
    restored = AseAtomsAdaptor.get_structure(read(OUT / 'AlGaAs.extxyz', format='extxyz'))
    assert composition_passes(restored)
    np.testing.assert_allclose(restored.lattice.matrix, repaired.lattice.matrix, atol=1e-8)
    np.testing.assert_allclose(restored.frac_coords, repaired.frac_coords, atol=1e-8)
    structure_path = OUT / 'AlGaAs.json'
    structure_path.write_text(json.dumps(repaired.as_dict()), encoding='utf-8')
    reset = Structure.from_dict(json.loads(structure_path.read_text(encoding='utf-8')))
    assert composition_passes(reset)
    film_crystal = Structure.from_spacegroup(216, Lattice.cubic(A), ['Al', 'As'], [[0, 0, 0], [0.25, 0.25, 0.25]])
    substrate = SlabGenerator(conventional, (0, 0, 1), 6, 0, primitive=False).get_slab()
    film = SlabGenerator(film_crystal, (0, 0, 1), 6, 0, primitive=False).get_slab()
    interface = Interface.from_slabs(substrate, film, gap=2.0, vacuum_over_film=12.0)
    assert interface.num_sites == substrate.num_sites + film.num_sites
    def actual_gap(obj):
        return float(obj.film.cart_coords[:, 2].min() - obj.substrate.cart_coords[:, 2].max())
    np.testing.assert_allclose(actual_gap(interface), 2.0, atol=1e-10)
    interface.gap = 0.2
    assert not np.isclose(actual_gap(interface), 2.0, atol=1e-10)
    interface.gap = 2.0
    np.testing.assert_allclose(actual_gap(interface), 2.0, atol=1e-10)
    assert interface.film.composition.get_el_amt_dict() == film.composition.get_el_amt_dict()
    assert interface.substrate.composition.get_el_amt_dict() == substrate.composition.get_el_amt_dict()
    record('01.01.01', [
        {'id': 'gaas_supercell_interchange', 'status': 'passed', 'assertions': ['8 to 64 sites', 'volume times 8', 'Ga31 Al1 As32', 'ASE extxyz lattice and coordinate roundtrip']},
        {'id': 'repair_doping_sublattice', 'status': 'passed', 'assertions': ['As substitution rejected', 'Ga substitution accepted', 'JSON reload composition preserved']},
        {'id': 'gaas_alas_interface_gap', 'status': 'passed', 'assertions': ['slab atom and species counts conserved', 'actual z gap equals 2 Angstrom', '0.2 Angstrom gap rejected and repaired']},
    ], {'sites': 64, 'volume_A3': repaired.volume, 'composition': repaired.composition.get_el_amt_dict(),
        'wrong_composition': wrong.composition.get_el_amt_dict(), 'bridge_max_frac_error': float(np.max(np.abs(restored.frac_coords-repaired.frac_coords))),
        'interface_atoms': interface.num_sites, 'interface_geometric_gap_A': actual_gap(interface)})
    initial = spglib.get_symmetry_dataset(cell(conventional), symprec=1e-5)
    assert initial is not None and initial.number == 216
    primitive = spglib.standardize_cell(cell(conventional), to_primitive=True, no_idealize=True, symprec=1e-5)
    assert primitive is not None
    lattice, positions, numbers = primitive
    assert len(numbers) == 2
    np.testing.assert_allclose(abs(np.linalg.det(lattice)), A ** 3 / 4, rtol=1e-12)
    assert spglib.get_symmetry_dataset(primitive, symprec=1e-5).number == 216
    rebuilt = Structure(lattice, numbers, positions)
    assert rebuilt.composition.get_el_amt_dict() == {'Ga': 1.0, 'As': 1.0}
    # A single-site displacement physically breaks the fixed ideal reference.
    displaced = conventional.copy()
    displaced.translate_sites([0], [0.017, 0.009, 0.023], frac_coords=True)
    broken = spglib.get_symmetry_dataset(cell(displaced), symprec=1e-5)
    assert broken is not None and broken.number != 216
    corrected = displaced.copy()
    corrected.translate_sites([0], [-0.017, -0.009, -0.023], frac_coords=True)
    final = spglib.get_symmetry_dataset(cell(corrected), symprec=1e-5)
    assert final is not None and final.number == 216
    assert np.allclose(np.mod(corrected.frac_coords - conventional.frac_coords + 0.5, 1) - 0.5, 0, atol=1e-12)
    record('01.02.01', [
        {'id': 'standardize_gaas_cell', 'status': 'passed', 'assertions': ['SG216 known zincblende reference', '8 to 2 sites', 'primitive volume equals a cubed / 4', 'species ratio invariant']},
        {'id': 'repair_symmetry_breaking', 'status': 'passed', 'assertions': ['single-site displacement fails SG216', 'inverse displacement restores SG216 at identical tolerance', 'periodic coordinates restored']},
    ], {'reference_group': int(initial.number), 'displaced_group': int(broken.number), 'repaired_group': int(final.number),
        'conventional_atoms': 8, 'primitive_atoms': len(numbers), 'primitive_volume_A3': abs(float(np.linalg.det(lattice)))})


if __name__ == '__main__':
    main()
