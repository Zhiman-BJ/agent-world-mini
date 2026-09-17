"""Exercise the released Engine contract with two local analytic ASE backends."""
from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
import pickle
import traceback

import numpy as np
from ase import Atoms
from ase.calculators.lj import LennardJones
from ase.calculators.morse import MorsePotential
from ase.io import read, write
from pyiron_workflow_atomistics.engine import (
    ASEEngine, CalcInputMinimize, CalcInputStatic, EngineOutput, calculate,
)

OUT = Path('seed_gen/scenario_collection/runtime/materials_backend')
OUT.mkdir(parents=True, exist_ok=True)
PROPERTIES = ('energy', 'forces', 'volume')


def lj_reference(distance, epsilon=.5, sigma=1., cutoff=3.):
    """Independent pair formula including ASE's energy-only cutoff shift."""
    x, xc = sigma / distance, sigma / cutoff
    energy = 4 * epsilon * (x**12 - x**6 - xc**12 + xc**6)
    gradient = 24 * epsilon / distance * (x**6 - 2*x**12)
    return energy, gradient


def morse_reference(distance, epsilon=.5, equilibrium=1.2, rho=6.):
    x = np.exp(rho * (1-distance/equilibrium))
    return epsilon*x*(x-2), -2*epsilon*rho/equilibrium*x*(x-1)


def engine(calculator, name, inputs=None):
    return ASEEngine(EngineInput=inputs or CalcInputStatic(), calculator=calculator,
                     properties=PROPERTIES, working_directory=str(OUT/name),
                     optimizer_kwargs={'logfile': None})


def main():
    atoms = Atoms('Ar2', positions=[[0, 0, 0], [1.3, 0, 0]], cell=[10, 10, 10], pbc=False)
    original = atoms.positions.copy()
    lj = engine(LennardJones(epsilon=.5, sigma=1., rc=3.), 'lj')
    morse = engine(MorsePotential(epsilon=.5, r0=1.2, rho0=6.), 'morse')
    results = {}
    for name, backend, oracle in [('lj', lj, lj_reference), ('morse', morse, morse_reference)]:
        out = calculate(structure=atoms, engine=backend)
        expected_energy, derivative = oracle(1.3)
        assert isinstance(out, EngineOutput) and out.converged
        np.testing.assert_allclose(out.final_energy, expected_energy, atol=1e-12, rtol=0)
        np.testing.assert_allclose(out.final_forces, [[derivative, 0, 0], [-derivative, 0, 0]], atol=1e-12, rtol=0)
        np.testing.assert_allclose(out.final_forces.sum(axis=0), 0, atol=1e-14)
        assert abs(out.final_volume-1000) < 1e-10
        assert set(out.to_dict()) >= {'final_structure', 'final_energy', 'final_forces', 'converged'}
        results[name] = {'energy_eV': out.final_energy, 'force_atom0_eV_A': out.final_forces[0, 0]}
    assert abs(results['lj']['energy_eV']-results['morse']['energy_eV']) > .01
    np.testing.assert_array_equal(atoms.positions, original)
    assert atoms.calc is None

    # A unit mistake still yields a successful calculation; the fixed goal must reject it.
    wrong = calculate(structure=atoms, engine=engine(LennardJones(epsilon=500, sigma=1, rc=3), 'wrong-meV'))
    target = lj_reference(1.3)[0]
    assert wrong.converged and abs(wrong.final_energy-target) > 1
    np.testing.assert_allclose(wrong.final_energy/1000, target, atol=1e-12)
    repaired = calculate(structure=atoms, engine=lj.with_working_directory('unit-repair'))
    assert abs(repaired.final_energy-target) < 1e-12
    assert lj.working_directory == str(OUT/'lj')

    # Zero step budget must fail a force/geometric criterion even if a backend reports success.
    failing_input = CalcInputMinimize(force_convergence_tolerance=1e-7,
                                     energy_convergence_tolerance=0, max_iterations=0)
    exhausted = calculate(structure=atoms, engine=engine(LennardJones(epsilon=.5, sigma=1, rc=3), 'zero-budget', failing_input))
    exhausted_fmax = float(np.linalg.norm(exhausted.final_forces, axis=1).max())
    assert not exhausted.converged and exhausted_fmax > .01
    fixed_input = CalcInputMinimize(force_convergence_tolerance=1e-7,
                                   energy_convergence_tolerance=0, max_iterations=100)
    relaxer = engine(LennardJones(epsilon=.5, sigma=1, rc=3), 'relax', fixed_input)
    relaxed = calculate(structure=atoms, engine=relaxer)
    separation = float(np.linalg.norm(np.diff(relaxed.final_structure.positions, axis=0)))
    force_max = float(np.linalg.norm(relaxed.final_forces, axis=1).max())
    assert relaxed.converged and force_max < 1e-7
    assert abs(separation-2**(1/6)) < 1e-7
    assert abs(relaxed.final_energy-lj_reference(2**(1/6))[0]) < 1e-12
    assert relaxed.final_energy < target

    # Serialize only our locally created trusted engine, and a portable scientific result.
    restored_engine = pickle.loads(pickle.dumps(lj))
    replay = calculate(structure=atoms, engine=restored_engine.with_working_directory('restored'))
    assert abs(replay.final_energy-target) < 1e-12
    write(OUT/'relaxed.extxyz', relaxed.final_structure)
    restored_atoms = read(OUT/'relaxed.extxyz')
    assert abs(float(np.linalg.norm(np.diff(restored_atoms.positions, axis=0)))-separation) < 1e-8
    numeric = {'energy_eV': relaxed.final_energy, 'distance_A': separation,
               'forces_eV_A': relaxed.final_forces.tolist()}
    (OUT/'result.json').write_text(json.dumps(numeric, indent=2)+'\n', encoding='utf-8')
    assert json.loads((OUT/'result.json').read_text(encoding='utf-8')) == numeric
    np.testing.assert_array_equal(atoms.positions, original)
    report = {
        'scenario_id': '01.03.04', 'status': 'passed',
        'versions': {name: version(name) for name in ('pyiron-workflow-atomistics', 'pyiron-workflow', 'ase')},
        'tasks': [
            {'id': 'switch_backend_and_repair_units', 'status': 'passed', 'assertions': [
                'Lennard-Jones and Morse results match independent pair formulas',
                'common output contract and momentum balance',
                'successful meV/eV error rejected and repaired', 'input structure and original engine unchanged']},
            {'id': 'repair_relaxation_budget_and_restore', 'status': 'passed', 'assertions': [
                'zero iterations rejected by convergence and force', '100 steps reach r=2^(1/6) and fmax<1e-7',
                'independent analytic energy minimum', 'trusted local engine pickle and extxyz/JSON roundtrip']},
        ],
        'metrics': {'backends': results, 'wrong_energy_eV': wrong.final_energy,
                    'repaired_energy_eV': repaired.final_energy, 'zero_budget_fmax_eV_A': exhausted_fmax,
                    'relaxed_distance_A': separation, 'relaxed_energy_eV': relaxed.final_energy,
                    'relaxed_fmax_eV_A': force_max},
        'scope': 'Real released pyiron ASEEngine and two ASE pair-potential calculators; analytic Ar2 fixture. No DFT, LAMMPS executable, MD, SLURM, distributed workflow or calibrated semiconductor potential. Workflow0.19 is a required dependency only; its0.20 candidate API is excluded. Pickle is only a self-created trusted fixture.',
    }
    (OUT/'01.03.04.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['metrics']), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
