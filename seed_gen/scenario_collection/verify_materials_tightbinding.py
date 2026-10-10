"""Released PythTB2 API against independent analytic and finite-chain oracles."""
from importlib.metadata import version
import json
from pathlib import Path
import traceback

import numpy as np
from pythtb.models import ssh

OUT = Path('seed_gen/scenario_collection/runtime/materials_tightbinding')
OUT.mkdir(parents=True, exist_ok=True)


def chain_matrix(n, v, w):
    h = np.zeros((2*n, 2*n))
    for i in range(n):
        h[2*i, 2*i+1] = h[2*i+1, 2*i] = v
        if i+1 < n:
            h[2*i+1, 2*i+2] = h[2*i+2, 2*i+1] = w
    return h


def once():
    v, w, n = .5, 1., 8
    model = ssh(v, w)
    points = np.linspace(0, 1, 101)[:, None]
    energy = model.solve_ham(points)
    root = np.sqrt(v*v+w*w+2*v*w*np.cos(2*np.pi*points[:, 0]))
    expected = np.column_stack((-root, root))
    np.testing.assert_allclose(energy, expected, atol=1e-12)
    assert abs(np.min(energy[:, 1]-energy[:, 0])-1) < 1e-12
    broken = model.copy()
    broken.set_onsite([.3, -.3])
    error = float(np.max(np.abs(broken.solve_ham(points)-expected)))
    assert error > .08
    broken.set_onsite([0, 0], mode='reset')
    np.testing.assert_allclose(broken.solve_ham(points), expected, atol=1e-12)
    np.testing.assert_allclose(model.solve_ham(points), expected, atol=1e-12)

    closed = model.cut_piece(n, 0, glue_edges=True)
    wrong_gap = float(np.min(np.abs(closed.solve_ham())))
    assert wrong_gap > .49
    finite = model.cut_piece(n, 0, glue_edges=False)
    eigenvalues, eigenvectors = finite.solve_ham(return_eigvecs=True)
    matrix = chain_matrix(n, v, w)
    np.testing.assert_allclose(finite.hamiltonian(), matrix, atol=1e-12)
    np.testing.assert_allclose(eigenvalues, np.linalg.eigvalsh(matrix), atol=1e-12)
    assert np.sum(np.abs(eigenvalues) < .01) == 2
    edge_vectors = eigenvectors[np.abs(eigenvalues) < .01]
    end_weight = np.sum(np.abs(edge_vectors[:, [0, -1]])**2, axis=1)
    assert np.min(end_weight) > .74
    # Check residual and normalization directly rather than trusting solver status.
    np.testing.assert_allclose(matrix@eigenvectors.T, eigenvectors.T*eigenvalues, atol=1e-12)
    np.testing.assert_allclose(eigenvectors@eigenvectors.conj().T, np.eye(2*n), atol=1e-12)
    params = {'v_eV': v, 'w_eV': w, 'cells': n, 'glue_edges': False}
    (OUT/'model_parameters.json').write_text(json.dumps(params, indent=2)+'\n', encoding='utf-8')
    restored = json.loads((OUT/'model_parameters.json').read_text(encoding='utf-8'))
    replay = ssh(restored['v_eV'], restored['w_eV']).cut_piece(restored['cells'], 0, glue_edges=restored['glue_edges'])
    np.testing.assert_allclose(replay.solve_ham(), eigenvalues, atol=1e-12)
    np.savez(OUT/'bands_states.npz', k=points, energies=energy, finite_energies=eigenvalues, finite_states=eigenvectors)
    return {'band_error_eV': float(np.max(np.abs(energy-expected))), 'bulk_gap_eV': 1.,
            'wrong_staggered_onsite_error_eV': error, 'closed_nearest_zero_eV': wrong_gap,
            'open_edge_energies_eV': eigenvalues[np.abs(eigenvalues) < .01].tolist(),
            'edge_endpoint_weights': end_weight.tolist(), 'finite_oracle_error_eV': float(np.max(np.abs(eigenvalues-np.linalg.eigvalsh(matrix))))}


def main():
    first, second = once(), once()
    assert first == second
    report = {'scenario_id': '01.08.02', 'status': 'passed', 'repeat_runs': 2, 'repeat_equal': True,
              'versions': {'pythtb': version('pythtb'), 'numpy': version('numpy')}, 'metrics': first,
              'tasks': [{'id': 'repair_bulk_hamiltonian', 'status': 'passed', 'assertions': [
                  '101 k-points agree with closed-form SSH spectrum', 'fixed band gap1eV',
                  'unwanted staggered onsite potential fails then resets successfully', 'input model isolated']},
                        {'id': 'repair_boundary_and_verify_edges', 'status': 'passed', 'assertions': [
                            'closed boundary has no near-zero states; open repair has two',
                            'independent16x16 matrix, eigenvalue residual and orthonormality',
                            'endpoint probability>.74; configuration/result archive replay']}],
              'scope': 'Actual PythTB2.0.2 solver on an explicit synthetic SSH chain. Independent analytical bulk spectrum and independently assembled finite matrix. No DFT/Wannier fitting, contacts, NEGF transport or material-parameter accuracy. JSON archive stores fixture parameters, not an invented package persistence API.'}
    (OUT/'01.08.02.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
