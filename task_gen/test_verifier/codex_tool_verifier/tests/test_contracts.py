import json

import pytest

from task_gen.test_verifier.codex_tool_verifier.contracts import validate_package, validate_results
from .conftest import dump, result


def test_design_and_zero_score_with_real_call(bundle, tmp_path):
    root, evidence = bundle
    plan = validate_package(root, 'Save data.')
    calls = [{'call_id': 'C1', 'tool_id': 'T1', 'status': 'completed', 'result': {'status': 'ok'}}]
    assert validate_results(plan, [result(['C1'], 0)], calls, evidence, tmp_path)['score'] == 0
    for invalid in ([], [result(['different_session'])], [result(['C1']), result(['C1'])]):
        with pytest.raises(ValueError):
            validate_results(plan, invalid, calls, evidence, tmp_path)


def test_contract_rejects_invalid_quotes_schema_and_coverage(bundle):
    root, _ = bundle
    doc = json.loads((root/'rubrics.json').read_text())
    doc['rubrics'][0]['task_quotes'] = ['Hidden requirement']
    dump(root/'rubrics.json', doc)
    with pytest.raises(ValueError, match='quote'):
        validate_package(root, 'Save data.')
    doc['rubrics'][0]['task_quotes'] = ['Save data.']
    dump(root/'rubrics.json', doc)
    dump(root/'coverage.json', {'requirements': []})
    with pytest.raises(ValueError, match='coverage'):
        validate_package(root, 'Save data.')


def test_tool_schema_cannot_fetch_external_refs(bundle):
    root, _ = bundle
    tools = json.loads((root/'tools.json').read_text())
    tools['tools'][0]['input_schema']['properties']['path'] = {'$ref': 'https://invalid.example/schema'}
    dump(root/'tools.json', tools)
    with pytest.raises(ValueError, match='ref'):
        validate_package(root, 'Save data.')


def test_only_main_codex_can_do_model_work(bundle):
    root, _ = bundle
    doc = json.loads((root/'rubrics.json').read_text())
    doc['rubrics'][0]['method'] = 'llm'
    dump(root/'rubrics.json', doc)
    with pytest.raises(ValueError, match='method'):
        validate_package(root)


def test_ordinal_full_score_is_exactly_one():
    from task_gen.test_verifier.program_verifier.contracts import aggregate_results
    rubric = {'id':'R1', 'weight':0.11, 'scale':'ordinal_5'}
    assert aggregate_results([rubric], [result(score=5)])['score'] == 1


def test_delivery_allows_readme_and_compiler_cache(bundle):
    root, _ = bundle
    (root/'README.md').write_text('Delivery instructions.')
    cache = root/'tools/__pycache__'
    cache.mkdir()
    (cache/'T1.cpython-311.pyc').write_bytes(b'compiled')
    validate_package(root)


def test_host_canonicalizes_absolute_evidence_to_relative_paths(bundle, tmp_path):
    root, evidence = bundle
    value = result(['C1'])
    value['evidence'][0]['path'] = str((evidence/value['evidence'][0]['path']).resolve())
    calls = [{'call_id':'C1', 'tool_id':'T1', 'status':'completed', 'result':{'status':'ok'}}]
    scored = validate_results(validate_package(root), [value], calls, evidence, tmp_path)
    assert scored['rubrics'][0]['evidence'][0]['path'] == 'final/data.json'
