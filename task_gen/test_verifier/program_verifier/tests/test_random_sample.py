import json
import pytest

from task_gen.test_verifier.program_verifier import random_sample


def test_sampling_is_reproducible_and_does_not_filter_missing_references(tmp_path):
    for name in ('b', 'a'):
        folder = tmp_path/'environments'/name
        folder.mkdir(parents=True)
        tasks = [{'task_id': f'{name}_{i}', 'task_text': f'task {i}'} for i in range(12)]
        (folder/'tasks.json').write_text(json.dumps(tasks))
    first = random_sample.select_sample(tmp_path, 20, 20261015)
    second = random_sample.select_sample(tmp_path, 20, 20261015)
    assert first == second
    assert first['population_count'] == 24
    assert len(first['selected']) == len({r['case_name'] for r in first['selected']}) == 20
    assert {r['environment'] for r in first['selected']} == {'a', 'b'}
    assert first['selected'] != random_sample.select_sample(tmp_path, 20, 1)['selected']


def test_evidence_failure_is_retained_as_a_sample_result(tmp_path):
    row = random_sample.run_case(
        {'case': 'P01', 'case_name': 'missing__task', 'task_id': 'task', 'environment': 'missing'},
        tmp_path/'dataset', tmp_path/'batch', {})
    assert row['case'] == 'P01'
    assert row['generation']['status'] == 'generation_failed'
    assert row['generation']['stage'] == 'evidence_preparation'
    assert row['application'] is None
    assert (tmp_path/'batch'/'P01'/'row.json').is_file()


def test_fixed_manifest_prefix_preserves_original_ids_and_order(tmp_path):
    items=[{'case':f'P{i:02d}', 'case_name':f'env__t{i}', 'environment':'env',
            'task_id':f't{i}', 'task_text':'task'} for i in range(1,21)]
    path=tmp_path/'sampling.json'
    path.write_text(json.dumps({'selected':items, 'sample_count':20, 'seed':123}))
    value=random_sample.load_selection(path,10)
    assert value['selected'] == items[:10]
    assert value['sample_count'] == 10
    assert value['source_sample_count'] == 20
    assert value['seed'] == 123
    with pytest.raises(ValueError): random_sample.load_selection(path,21)
