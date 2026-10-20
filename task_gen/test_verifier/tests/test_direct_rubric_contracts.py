import copy
import json
import subprocess
import sys

import pytest

from task_gen.test_verifier.direct_codex_rubric.contracts import validate_package
from task_gen.test_verifier.direct_codex_rubric.generation import generate
from task_gen.test_verifier.tests.test_independent_rubric_generation import design


def read_document(root):
    return json.loads((root/'rubrics.json').read_text())


def write_document(root, document):
    (root/'rubrics.json').write_text(json.dumps(document))


@pytest.fixture
def package(tmp_path):
    root = tmp_path/'verifier'
    design(root)
    document = read_document(root)
    child = dict(id='R1.detail', goal='Return value.', task_quotes=['Return value.'],
                 required_for_pass=True, necessity_reason='Explicitly requested.',
                 pass_condition='The value is correct.', veto_condition='Confirmed wrong value.',
                 subcriteria=[])
    leaf = copy.deepcopy(child)
    leaf.update(id='R1.detail.leaf', required_for_pass=False)
    child['subcriteria'] = [leaf]
    # Necessity is independent at each level; the required child is valid here.
    document['rubrics'][0].update(required_for_pass=False, subcriteria=[child])
    write_document(root, document)
    return root


def test_valid_nested_annotations_are_preserved(package):
    original = (package/'rubrics.json').read_bytes()
    plan = validate_package(package, 'Return value.')
    rubric = plan['rubrics'][0]
    assert rubric['required_for_pass'] is False
    assert rubric['subcriteria'][0]['required_for_pass'] is True
    assert (package/'rubrics.json').read_bytes() == original


@pytest.mark.parametrize('field', [
    'required_for_pass', 'necessity_reason', 'pass_condition', 'veto_condition', 'subcriteria',
])
@pytest.mark.parametrize('depth', [0, 2])
def test_missing_necessity_fields_are_rejected_at_all_levels(package, field, depth):
    document = read_document(package)
    item = document['rubrics'][0]
    for _ in range(depth):
        item = item['subcriteria'][0]
    del item[field]
    write_document(package, document)
    with pytest.raises(ValueError, match=field):
        validate_package(package, 'Return value.')


@pytest.mark.parametrize('field,value', [
    ('required_for_pass', 'true'), ('required_for_pass', 1), ('required_for_pass', None),
    ('necessity_reason', ' '), ('necessity_reason', ['because']),
    ('pass_condition', ''), ('pass_condition', {}),
    ('veto_condition', None), ('veto_condition', False),
    ('goal', ' '), ('goal', 3), ('id', ''), ('id', 3),
    ('task_quotes', []), ('task_quotes', 'Return value.'), ('task_quotes', [' ']),
    ('task_quotes', [1]), ('task_quotes', ['Not in the task.']),
    ('subcriteria', None), ('subcriteria', {}), ('subcriteria', ['not an object']),
])
def test_invalid_deep_subcriteria_are_rejected(package, field, value):
    document = read_document(package)
    leaf = document['rubrics'][0]['subcriteria'][0]['subcriteria'][0]
    leaf[field] = value
    write_document(package, document)
    with pytest.raises(ValueError, match=r'rubrics\[0\]\.subcriteria\[0\]\.subcriteria\[0\]'):
        validate_package(package, 'Return value.')


@pytest.mark.parametrize('duplicate', ['ancestor', 'sibling'])
def test_duplicate_ids_across_levels_and_siblings_are_rejected(package, duplicate):
    document = read_document(package)
    child = document['rubrics'][0]['subcriteria'][0]
    if duplicate == 'ancestor':
        child['subcriteria'][0]['id'] = 'R1'
    else:
        child['subcriteria'].append(copy.deepcopy(child['subcriteria'][0]))
    write_document(package, document)
    with pytest.raises(ValueError, match='duplicate rubric/subcriterion ID'):
        validate_package(package, 'Return value.')


def test_common_rubric_method_keeps_its_existing_contract(package):
    from task_gen.test_verifier.codex_rubric_verifier.generation import validate_package as common_validate
    document = read_document(package)
    for field in ('required_for_pass', 'necessity_reason', 'pass_condition', 'veto_condition', 'subcriteria'):
        document['rubrics'][0].pop(field)
    write_document(package, document)
    common_validate(package, 'Return value.')
    with pytest.raises(ValueError, match='necessity_reason'):
        validate_package(package, 'Return value.')


@pytest.mark.parametrize('valid', [False, True])
def test_direct_design_cli_checks_fields_before_issuing_completion(package, tmp_path, valid):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    self_test = tmp_path/'self_test.py'
    ran = tmp_path/'self_test_ran'
    self_test.write_text(f'from pathlib import Path\nPath({str(ran)!r}).touch()\n')
    completion = tmp_path/'completion.json'
    completion.write_text('{"status":"stale"}')
    if not valid:
        document = read_document(package)
        del document['rubrics'][0]['subcriteria'][0]['required_for_pass']
        write_document(package, document)
    result = subprocess.run([
        sys.executable, '-m', 'task_gen.test_verifier.direct_codex_rubric.check',
        '--workspace', str(package), '--evidence', str(evidence), '--stage', 'design',
        '--self-test', str(self_test), '--completion-file', str(completion),
    ], capture_output=True, text=True, timeout=30)
    if valid:
        assert result.returncode == 0, result.stderr
        assert json.loads(completion.read_text())['status'] == 'passed'
        assert ran.exists()
    else:
        assert result.returncode != 0 and 'required_for_pass' in result.stderr
        assert not completion.exists() and not ran.exists()


@pytest.mark.parametrize('bad_stage,review_enabled', [('design', False), ('final', True)])
def test_incomplete_direct_packages_never_freeze(tmp_path, bad_stage, review_enabled):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))

    def model_call(prompt, *, stage, output_dir):
        workspace = tmp_path/stage
        design(workspace)
        if stage == bad_stage:
            document = read_document(workspace)
            del document['rubrics'][0]['required_for_pass']
            write_document(workspace, document)
        return workspace

    output = tmp_path/'output'
    result = generate(evidence, output, {'final_review_enabled':review_enabled}, model_call=model_call)
    assert result['status'] == 'generation_failed', result
    assert result['stage'] == ('design' if bad_stage == 'design' else 'final_review')
    assert 'required_for_pass' in result['error']
    assert not (output/'frozen_manifest.json').exists()


def test_final_review_can_repair_missing_fields_before_freezing(tmp_path):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))

    def model_call(prompt, *, stage, output_dir):
        workspace = tmp_path/stage
        design(workspace)
        if stage == 'design':
            document = read_document(workspace)
            del document['rubrics'][0]['required_for_pass']
            write_document(workspace, document)
        return workspace

    output = tmp_path/'output'
    result = generate(evidence, output, {}, model_call=model_call)
    assert result['status'] == 'generated', result
    assert json.loads((output/'generation/staged_result.json').read_text())['stage'] == 'design'
    assert (output/'frozen_manifest.json').is_file()
    validate_package(output/'verifier', 'Return value.')


def test_final_native_check_rejects_bad_fields_before_applying(package, tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.direct_codex_rubric import application
    document = read_document(package)
    del document['rubrics'][0]['subcriteria'][0]['required_for_pass']
    write_document(package, document)
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    monkeypatch.setattr(application, 'apply', lambda *a, **kw: pytest.fail('invalid package must not be applied'))
    with pytest.raises(ValueError, match='required_for_pass'):
        final_review.check_workspace('direct_codex_rubric', package, evidence,
                                     tmp_path/'self_test.py', {}, tmp_path/'native')
