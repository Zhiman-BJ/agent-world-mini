import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from task_gen.test_verifier.manual_npz_verifier import prepare, check_files, validate_answer_review

DATA = Path('/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_03_03_0023')
TASK = 'aiida_dft_provenance_recovery_program_000'


@pytest.fixture(scope='module')
def standard(tmp_path_factory):
    if not DATA.exists():
        pytest.skip('real task dataset is not mounted')
    return prepare(DATA / 'tasks.json', tmp_path_factory.mktemp('s59') / 'standard')


def test_reference_and_alternative_compression(standard, tmp_path):
    actual = DATA / 'tasks' / TASK / 'final'
    assert all(c['passed'] for c in check_files(standard, actual)['checks'])
    # A different ZIP encoding and member name must preserve the same array semantics.
    root = tmp_path / 'state'
    shutil.copytree(actual / 'filesystem_scopes/aiida_demo_archive/derived', root / 'filesystem_scopes/aiida_demo_archive/derived')
    entry = standard['arrays'][0]
    dest = root / entry['output']
    with np.load(actual / entry['output'], allow_pickle=False) as z:
        np.savez(dest, arbitrary_name=z[z.files[0]])
    report = root / entry['report']
    value = json.loads(report.read_text()); value['output_size'] = dest.stat().st_size
    report.write_text(json.dumps(value))
    # Isolate packaging equivalence; repository preservation is covered separately.
    result = check_files({**standard, 'originals': {}}, root)
    assert next(c for c in result['checks'] if c['id'] == 'array:atomic_species_name.npy')['passed']
    assert next(c for c in result['checks'] if c['id'] == 'report:atomic_species_name.npy')['passed']


@pytest.mark.parametrize('fault', ['missing', 'value', 'dtype', 'report', 'original'])
def test_key_faults(standard, tmp_path, fault):
    entry = next(e for e in standard['arrays'] if e['name'] == 'cells.npy')
    actual = DATA / 'tasks' / TASK / 'final'
    for field in ['output', 'report', 'source']:
        target = tmp_path / entry[field]; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(actual / entry[field], target)
    if fault == 'missing':
        (tmp_path / entry['output']).unlink()
    elif fault in ['value', 'dtype']:
        with np.load(actual / entry['output'], allow_pickle=False) as z:
            a = z[z.files[0]].copy()
        if fault == 'value': a.flat[0] += 1
        else: a = a.astype('float32')
        np.savez(tmp_path / entry['output'], data=a)
    elif fault == 'report':
        p = tmp_path / entry['report']; v = json.loads(p.read_text()); v['source_sha256'] = 'forged'; p.write_text(json.dumps(v))
    else:
        (tmp_path / entry['source']).write_bytes(b'changed')
    checks = {c['id']: c for c in check_files(standard, tmp_path)['checks']}
    target = 'report:cells.npy' if fault == 'report' else 'originals' if fault == 'original' else 'array:cells.npy'
    assert checks[target]['passed'] is False


def test_llm_result_cannot_omit_requirements_or_invent_quotes():
    requirements = [{'id':'A1'}]
    answer = '不能逐帧配对。'
    good = {'checks':[{'id':'A1','passed':True,'reason':'明确说明不能配对','quotes':['不能逐帧配对。']}]}
    assert validate_answer_review(good, requirements, answer) == good['checks']
    with pytest.raises(ValueError): validate_answer_review({'checks':[]}, requirements, answer)
    good['checks'][0]['quotes'] = ['可以配对']
    with pytest.raises(ValueError): validate_answer_review(good, requirements, answer)
