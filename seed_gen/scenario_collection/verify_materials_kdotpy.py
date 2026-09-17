"""Kane bulk and QW fixtures with temperature and uniform-offset oracles."""
from importlib.metadata import version
import json
from pathlib import Path
import traceback

import numpy as np
from kdotpy.materials import MaterialsList
from kdotpy.physparams import PhysParams
from kdotpy.hamiltonian.full import hbulk
from kdotpy.hamiltonian.hamiltonian import hz_sparse, hz_sparse_pot
from kdotpy.diagonalization.diagonalization import hbulk as solve_bulk, hz
from kdotpy.vector import Vector

OUT = Path('seed_gen/scenario_collection/runtime/materials_kdotpy')
OUT.mkdir(parents=True, exist_ok=True)
MATERIAL_FILE = 'seed_pypi_raw/kdotpy/src/kdotpy/materials/default'


def once():
    materials = MaterialsList({}).load_from_file(MATERIAL_FILE)
    def bulk(temperature):
        # Explicit evaluation avoids reading/writing personal ~/.kdotpy state.
        material = materials.get_from_string('CdTe', {'T': temperature})
        assert material.check_numeric(quiet=True)
        return PhysParams(kdim=3, l_layers=[1.], m_layers=[material],
                          norbitals=8, rel_strain='none', temperature=temperature)
    parameters = bulk(0)
    gamma = solve_bulk(Vector(0, 0, 0), parameters, ignorestrain=True, return_eivec=True)
    energy = np.sort(gamma.eival)
    expected = np.array([-1480, -1480, -570, -570, -570, -570, 1036, 1036])
    np.testing.assert_allclose(energy, expected, atol=1e-10)
    wrong = solve_bulk(Vector(0, 0, 0), bulk(300), ignorestrain=True, return_eivec=True)
    wrong_gap = float(np.sort(wrong.eival)[-1]-np.sort(wrong.eival)[-3])
    assert abs(wrong_gap-1606) > 70
    assert abs(wrong_gap-(1606-.325*300**2/(78.7+300))) < 1e-9
    residuals = []
    for k in (0., .01, .03):
        a = hbulk([k, 0, 0], 0, parameters, ignorestrain=True)
        np.testing.assert_allclose(a, a.conj().T, atol=1e-12)
        result = solve_bulk(Vector(k, 0, 0), parameters, ignorestrain=True, return_eivec=True)
        negative = solve_bulk(Vector(-k, 0, 0), parameters, ignorestrain=True, return_eivec=True)
        np.testing.assert_allclose(np.sort(result.eival), np.sort(negative.eival), atol=1e-9)
        np.testing.assert_allclose(np.sort(result.eival)[::2], np.sort(result.eival)[1::2], atol=1e-9)
        residuals.append(float(np.max(np.abs(a@result.eivec-result.eivec*result.eival))))
    assert max(residuals) < 1e-8
    # Repair temperature by constructing new parameter state, not reusing cached spectra.
    repaired = solve_bulk(Vector(0, 0, 0), bulk(0), ignorestrain=True, return_eivec=True)
    np.testing.assert_allclose(np.sort(repaired.eival), expected, atol=1e-10)

    layers = [materials.get_from_string(s, {'T': 0}) for s in ('CdTe', 'HgTe', 'CdTe')]
    params = PhysParams(kdim=2, l_layers=[2., 3., 2.], m_layers=layers,
                        norbitals=8, zres=.5, rel_strain='none')
    matrix = hz_sparse([0, 0], 0, params, ignorestrain=True).toarray()
    assert matrix.shape == (120, 120) and params.nz == 15
    np.testing.assert_allclose(matrix, matrix.conj().T, atol=1e-12)
    def solve(offset):
        return hz(Vector(0, 0), params, neig=8, energy=offset,
                  pot=np.full(params.nz, offset), ignorestrain=True, return_eivec=True)
    base, wrong_units, final = solve(0), solve(.02), solve(20)
    base_spectrum = np.sort(base.eival)
    failed_shift = np.sort(wrong_units.eival)-base_spectrum
    shift = np.sort(final.eival)-base_spectrum
    np.testing.assert_allclose(failed_shift, .02, atol=1e-8)
    assert np.max(np.abs(failed_shift-20)) > 19
    np.testing.assert_allclose(shift, 20, atol=1e-8)
    potential = hz_sparse_pot(params, np.full(params.nz, 20)).toarray()
    np.testing.assert_allclose(potential, 20*np.eye(120), atol=1e-12)
    residual = float(np.max(np.abs((matrix+potential)@final.eivec-final.eivec*final.eival)))
    assert residual < 1e-8
    # ARPACK may return degenerate vectors in different bases; compare spectra and residuals.
    model = dict(materials=['CdTe', 'HgTe', 'CdTe'], lengths_nm=[2., 3., 2.],
                 zres_nm=.5, orbitals=8, temperature_K=0, potential_meV=20)
    (OUT/'configuration.json').write_text(json.dumps(model, indent=2)+'\n', encoding='utf-8')
    restored = json.loads((OUT/'configuration.json').read_text(encoding='utf-8'))
    restored_params = PhysParams(kdim=2, l_layers=restored['lengths_nm'],
                                m_layers=[materials.get_from_string(s, {'T': restored['temperature_K']}) for s in restored['materials']],
                                norbitals=restored['orbitals'], zres=restored['zres_nm'], rel_strain='none')
    replay = hz(Vector(0, 0), restored_params, neig=8, energy=20,
                pot=np.full(restored_params.nz, restored['potential_meV']), ignorestrain=True, return_eivec=True)
    np.testing.assert_allclose(np.sort(replay.eival), np.sort(final.eival), atol=1e-8)
    np.savez(OUT/'subbands.npz', initial_meV=base_spectrum, shifted_meV=np.sort(final.eival))
    return {'gamma_eigenvalues_meV': energy.tolist(), 'wrong_temperature_gap_meV': wrong_gap,
            'target_gap_meV': 1606., 'qw_matrix_size': 120,
            'base_subbands_meV': base_spectrum.tolist(), 'repaired_shift_meV': shift.tolist(),
            'bulk_max_residual': max(residuals), 'qw_residual': residual}


def main():
    first, second = once(), once()
    for key in first:
        np.testing.assert_allclose(first[key], second[key], atol=1e-8, rtol=0)
    report = {'scenario_id': '01.08.01', 'status': 'passed', 'repeat_runs': 2,
              'repeat_equal': True, 'repeat_atol': 1e-8, 'versions': {'kdotpy': version('kdotpy')},
              'metrics': first,
              'tasks': [{'id': 'repair_temperature_and_bulk_spectrum', 'status': 'passed', 'assertions': [
                  'fixed CdTe Gamma eigenvalues and1606meV gap', '300K configuration fails0K target; Varshni formula agrees',
                  'repair to0K, Hermiticity, zero-field doublets, E(k)=E(-k), eigenpair residual']},
                        {'id': 'repair_qw_potential_units_and_restore', 'status': 'passed', 'assertions': [
                            'actual120x120 CdTe/HgTe/CdTe quantum-well matrix',
                            '0.02meV error rejected,20meV repair shifts all selected subbands by20',
                            'independent uniform potential20I and eigenpair residual', 'configuration restore recomputes spectrum']}],
              'scope': 'Released kdotpy1.4.1, real bulk and sparse QW high-level diagonalization. Official material table evaluated at explicit T. Coarse0.5nm discretization, strain/BIA/magnetic terms disabled; not a grid-convergence or experimental-fit claim. Personal config/material files are never initialized. SciPy/NumPy matrix checks are verifier infrastructure.'}
    (OUT/'01.08.01.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
