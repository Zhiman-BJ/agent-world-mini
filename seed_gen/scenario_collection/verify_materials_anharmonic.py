"""RTA transport using the exact official released Si-PBEsol force fixture."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import numpy as np
import phono3py

OUT = Path('seed_gen/scenario_collection/runtime/materials_anharmonic')
DATA = Path('seed_pypi_raw/phono3py/test')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = [DATA/'phono3py_si_pbesol.yaml', DATA/'FORCES_FC3_si_pbesol']
    model = phono3py.load(inputs[0], forces_fc3_filename=inputs[1], make_r0_average=True, log_level=0)
    assert np.isfinite(model.fc2).all() and np.isfinite(model.fc3).all()
    def run(mesh, isotope=False):
        model.mesh_numbers = mesh
        model.init_phph_interaction()
        model.run_thermal_conductivity(temperatures=[300.], is_isotope=isotope)
        values = model.thermal_conductivity.kappa.ravel().copy()
        print('mesh', mesh, 'isotope', isotope, 'kappa', values, flush=True)
        return values
    coarse = run([3, 3, 3])
    target = np.array([107.694, 107.694, 107.694, 0., 0., 0.])
    assert not np.allclose(coarse, target, atol=.5)
    repaired = run([9, 9, 9])
    np.testing.assert_allclose(repaired, target, atol=.5)
    isotope = run([9, 9, 9], True)
    np.testing.assert_allclose(isotope, [97.296, 97.296, 97.296, 0., 0., 0.], atol=.5)
    assert np.all(isotope[:3] > 0) and np.all(isotope[:3] < repaired[:3])
    np.testing.assert_allclose(repaired[:3], repaired[0], atol=1e-6)
    np.testing.assert_allclose(repaired[3:], 0., atol=1e-6)
    payload = {'scenario_id': '01.05.02', 'status': 'passed',
               'versions': {p: importlib.metadata.version(p) for p in ('phono3py', 'phonopy', 'numpy')},
               'tasks': [{'id': 'repair_transport_mesh', 'status': 'passed'}, {'id': 'isotope_scattering_comparison', 'status': 'passed'}],
               'metrics': {'coarse_W_mK': coarse.tolist(), 'repaired_W_mK': repaired.tolist(), 'isotope_W_mK': isotope.tolist()},
               'fixture_sources': [{'path': p.as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs],
               'oracle': 'Official v4.5.0 test/conductivity/test_kappa_RTA.py independent fixed reference; also cubic tensor symmetry and isotope monotonicity.',
               'scope': 'Actual fc2/fc3 and RTA transport from released precomputed forces; 9x9x9 is a fixed regression target, not a demonstrated mesh-converged/material prediction. No fresh DFT or fitted hiPhive model.'}
    (OUT/'01.05.02.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
