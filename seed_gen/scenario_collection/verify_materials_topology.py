"""TBmodels to Z2Pack against a two-band Chern oracle and gap-closing test."""
from importlib.metadata import version
import json
import logging
from pathlib import Path
import traceback

import h5py
import numpy as np
import tbmodels
import z2pack

OUT = Path('seed_gen/scenario_collection/runtime/materials_topology')
OUT.mkdir(parents=True, exist_ok=True)
logging.getLogger('z2pack').setLevel(logging.ERROR)
SX = np.array([[0, 1], [1, 0]])
SY = np.array([[0, -1j], [1j, 0]])
SZ = np.diag([1, -1])


def model(mass):
    return tbmodels.Model(on_site=[mass, -mass],
                          hop={(1, 0): .5*SZ-.5j*SX, (0, 1): .5*SZ-.5j*SY},
                          contains_cc=False, pos=[[0, 0], [0, 0]], uc=np.eye(2), occ=1)


def analytic_hamiltonian(k, mass):
    x, y = 2*np.pi*np.asarray(k)
    return np.sin(x)*SX+np.sin(y)*SY+(mass+np.cos(x)+np.cos(y))*SZ


def oracle_chern(mass):
    assert mass not in (-2, 0, 2), 'Gapless points do not have an isolated occupied band'
    # Degree of the d-vector from the four Dirac masses, for the documented orientation.
    return -.5*(np.sign(mass+2)+np.sign(mass-2)-2*np.sign(mass))


def discrete_chern(mass, size=31):
    # Independent occupied-eigenvector plaquette calculation on the analytic H(k).
    vectors = np.empty((size, size, 2), complex)
    for i in range(size):
        for j in range(size):
            vectors[i, j] = np.linalg.eigh(analytic_hamiltonian([i/size, j/size], mass))[1][:, 0]
    total = 0.
    for i in range(size):
        for j in range(size):
            corners = [vectors[i, j], vectors[(i+1)%size, j],
                       vectors[(i+1)%size, (j+1)%size], vectors[i, (j+1)%size]]
            product = np.prod([np.vdot(corners[k], corners[(k+1)%4]) for k in range(4)])
            total += np.angle(product)
    return total/(2*np.pi)


def surface(mass, iterator=range(8, 49, 8)):
    return z2pack.surface.run(system=z2pack.tb.System(model(mass)),
                             surface=lambda s, t: [s, t], num_lines=11,
                             iterator=iterator, pos_tol=.01, gap_tol=.2, move_tol=.2)


def converged(result):
    for group in result.convergence_report.values():
        for check in group.values():
            if check.get('FAILED') or check.get('MISSING'):
                return False
    return True


def once():
    wrong = surface(3)
    wrong_c = z2pack.invariant.chern(wrong)
    assert abs(wrong_c) < 1e-10 and abs(wrong_c+1) > .9
    fixed = surface(-1)
    c = z2pack.invariant.chern(fixed)
    assert converged(fixed) and abs(c-oracle_chern(-1)) < 1e-10
    discrete = discrete_chern(-1)
    assert abs(c-discrete) < 1e-10
    tb = model(-1)
    for point in ([0, 0], [.25, 0], [.5, .5], [.13, .27]):
        np.testing.assert_allclose(tb.hamilton(point), analytic_hamiltonian(point, -1), atol=1e-12)
    with h5py.File(OUT/'model.h5', 'w') as handle:
        tb.to_hdf5(handle)
    restored = tbmodels.Model.from_hdf5_file(OUT/'model.h5')
    np.testing.assert_allclose(restored.hamilton([.13, .27]), tb.hamilton([.13, .27]), atol=1e-12)
    replay = z2pack.surface.run(system=z2pack.tb.System(restored), surface=lambda s, t: [s, t],
                               num_lines=11, iterator=range(8, 49, 8), pos_tol=.01, gap_tol=.2, move_tol=.2)
    assert converged(replay) and abs(z2pack.invariant.chern(replay)-c) < 1e-10

    # A returned result with exhausted line budget is not evidence of convergence.
    exhausted = surface(-1, iterator=range(4, 5))
    assert not converged(exhausted)
    assert converged(fixed)
    zero_spectrum = model(0).eigenval([.5, 0])
    zero_gap = float(zero_spectrum[1]-zero_spectrum[0])
    assert zero_gap < 1e-10
    phase_values = {}
    for mass in (-.5, .5):
        answer = surface(mass)
        answer_c = z2pack.invariant.chern(answer)
        assert converged(answer)
        assert abs(answer_c-oracle_chern(mass)) < 1e-10
        assert abs(answer_c-discrete_chern(mass)) < 1e-10
        phase_values[str(mass)] = answer_c
    assert abs(phase_values['-0.5']+1) < 1e-10 and abs(phase_values['0.5']-1) < 1e-10
    metrics = {'wrong_mass_chern': wrong_c, 'repaired_chern': c,
               'independent_plaquette_chern': discrete, 'gap_at_m0_energy_units': zero_gap,
               'adjacent_phase_chern': phase_values, 'budget_failure_detected': True,
               'repaired_surface_lines': len(fixed.t)}
    (OUT/'topological_summary.json').write_text(json.dumps(metrics, indent=2)+'\n', encoding='utf-8')
    return metrics


def main():
    first, second = once(), once()
    assert first == second
    report = {'scenario_id': '01.09.03', 'status': 'passed', 'repeat_runs': 2, 'repeat_equal': True,
              'versions': {p: version(p) for p in ('z2pack', 'tbmodels', 'numpy')}, 'metrics': first,
              'tasks': [{'id': 'repair_mass_and_validate_chern', 'status': 'passed', 'assertions': [
                  'm=3 trivial phase rejected for targetC=-1; repairedm=-1 matches Dirac-mass formula',
                  'independent plaquette integer agrees', 'TBmodels to Z2Pack Hamiltonian convention checked',
                  'HDF5 model restore and repeated Wilson-loop result']},
                        {'id': 'repair_convergence_and_locate_transition', 'status': 'passed', 'assertions': [
                            'insufficient iterator returns failed convergence and is rejected',
                            'repaired iteration budget passes all checks',
                            'gap closes at m=0,k=(.5,0); no isolated-band invariant accepted there',
                            'm=-.5 and+.5 have independently verified Chern-1 and+1']}],
              'scope': 'Explicit synthetic two-orbital square-lattice model with hopping energy unit1. No Wannier90/DFT input, experimental topological material claim, or Z2 time-reversal invariant calculation. Actual TBmodels and Z2Pack bridge, with independent analytic mass and plaquette oracles.'}
    (OUT/'01.09.03.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
