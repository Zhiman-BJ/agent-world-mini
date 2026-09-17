"""CPU CHGNet fixed tasks: normalization, finite differences and relaxation."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import traceback

import chgnet
import numpy as np
import torch
from chgnet.model.model import CHGNet
from chgnet.model.dynamics import CHGNetCalculator, StructOptimizer
from pymatgen.core import Lattice, Structure
from pymatgen.io.ase import AseAtomsAdaptor

OUT = Path('seed_gen/scenario_collection/runtime/materials_mlpot')
OUT.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(2)
torch.manual_seed(17)


def fixture():
    lattice = Lattice([[0, 2.715, 2.715], [2.715, 0, 2.715], [2.715, 2.715, 0]])
    structure = Structure(lattice, ['Si', 'Si'], [[0, 0, 0], [.25, .25, .25]])
    structure.translate_sites([0], [.12, 0, 0], frac_coords=False)
    return structure


def once(model):
    structure = fixture()
    before = structure.cart_coords.copy()
    prediction = model.predict_structure(structure, task='ef')
    n = len(structure)
    total = n * float(prediction['e'])
    force = float(prediction['f'][0, 0])
    h = .005
    displaced = []
    for sign in (-1, 1):
        item = structure.copy()
        item.translate_sites([0], [sign*h, 0, 0], frac_coords=False)
        displaced.append(float(model.predict_structure(item, task='e')['e']))
    wrong = -(displaced[1]-displaced[0])/(2*h)
    derivative = n*wrong
    assert abs(derivative-force) < .01
    assert abs(wrong-force) > .1, 'Per-atom energy mistaken for total energy must fail'
    np.testing.assert_allclose(prediction['f'].sum(axis=0), 0, atol=2e-5)
    shifted = structure.copy()
    shifted.translate_sites(list(range(n)), [.3, -.2, .4], frac_coords=False)
    translated = model.predict_structure(shifted, task='ef')
    np.testing.assert_allclose(translated['e'], prediction['e'], atol=2e-5, rtol=0)
    np.testing.assert_allclose(translated['f'], prediction['f'], atol=2e-4, rtol=0)
    atoms = AseAtomsAdaptor.get_atoms(structure)
    atoms.calc = CHGNetCalculator(model=model, use_device='cpu')
    assert abs(atoms.get_potential_energy()-total) < 2e-5
    np.testing.assert_allclose(atoms.get_forces(), prediction['f'], atol=2e-4)

    optimizer = StructOptimizer(model=model, optimizer_class='FIRE', use_device='cpu')
    failed = optimizer.relax(structure, fmax=.025, steps=0, relax_cell=False, verbose=False)
    failing = model.predict_structure(failed['final_structure'], task='ef')
    bad_fmax = float(np.linalg.norm(failing['f'], axis=1).max())
    assert bad_fmax > .1
    repaired = optimizer.relax(structure, fmax=.025, steps=100, relax_cell=False, verbose=False)
    final = repaired['final_structure']
    result = model.predict_structure(final, task='ef')
    fmax = float(np.linalg.norm(result['f'], axis=1).max())
    assert fmax < .025
    assert float(result['e']) < float(prediction['e'])-.005
    np.testing.assert_allclose(final.lattice.matrix, structure.lattice.matrix, atol=1e-12)
    np.testing.assert_array_equal(structure.cart_coords, before)
    final.to(filename=OUT/'relaxed.json')
    restored = Structure.from_file(OUT/'relaxed.json')
    replay = model.predict_structure(restored, task='ef')
    np.testing.assert_allclose(replay['e'], result['e'], atol=1e-6)
    np.testing.assert_allclose(replay['f'], result['f'], atol=2e-5)
    return dict(energy_eV_atom=float(prediction['e']), energy_eV_total=total,
                force_eV_A=force, finite_difference_eV_A=derivative,
                wrong_normalization_eV_A=wrong, zero_budget_fmax_eV_A=bad_fmax,
                final_energy_eV_atom=float(result['e']), final_fmax_eV_A=fmax,
                trajectory_frames=len(repaired['trajectory'].energies))


def main():
    relative = Path('pretrained/0.3.0/chgnet_0.3.0_e29f68s314m37.pth.tar')
    installed = Path(chgnet.__file__).parent/relative
    source = Path('seed_pypi_raw/chgnet/chgnet')/relative
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest(installed) == digest(source)
    model = CHGNet.load(model_name='0.3.0', use_device='cpu')
    first, second = once(model), once(model)
    assert first == second
    report = dict(scenario_id='01.01.04', status='passed', repeat_runs=2, repeat_equal=True,
                  versions={p: version(p) for p in ('chgnet', 'torch', 'pymatgen-core', 'ase')},
                  checkpoint_sha256=digest(installed), metrics=first,
                  tasks=[{'id': 'repair_energy_normalization', 'status': 'passed', 'assertions': [
                      'force agrees with independent finite difference of total energy',
                      'per-atom normalization error rejected then repaired',
                      'translation and net-force symmetry; actual ASE bridge matches']},
                         {'id': 'repair_relaxation_and_restore', 'status': 'passed', 'assertions': [
                             'zero budget fails fixed force criterion', '100-step budget reaches fmax<.025 eV/A',
                             'energy decreases, fixed lattice and unchanged input', 'saved structure replay agrees']}],
                  scope='Actual bundled CHGNet0.3.0 checkpoint with release0.4.2 on CPU. Distorted two-atom Si fixture; finite-difference and invariance tests check internal numerical consistency, not DFT accuracy or transferability. No training, GPU, external model download or MD.')
    (OUT/'01.01.04.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
