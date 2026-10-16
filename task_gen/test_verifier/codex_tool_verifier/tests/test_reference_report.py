import json
from types import SimpleNamespace

import pytest

from task_gen.test_verifier.codex_tool_verifier.validate_reference import run_case, summarize
from task_gen.test_verifier.codex_tool_verifier import validate_reference


def test_unreadable_source_is_not_a_generation_attempt():
    rows = [
        {'generation': {'status': 'generation_failed', 'stage': 'evidence_preparation'}},
        {'generation': {'status': 'generation_failed', 'stage': 'design'}},
        {'generation': {'status': 'generated'}, 'tools': []},
    ]
    stats = summarize(rows)
    assert stats['preparation_failed'] == 1
    assert stats['generation_attempted'] == 2
    assert stats['generation_failed'] == 1
    assert stats['generated'] == 1


@pytest.mark.parametrize('cached,requested,compatible', [
    ({'mode':'codex_with_tools'}, False, False),
    ({'mode':'codex_rubric_only','static_tools_enabled':False}, True, False),
    ({'mode':'fallback_codex','static_tools_enabled':True}, False, False),
    ({'mode':'fallback_codex','static_tools_enabled':False}, True, False),
    ({'mode':'fallback_codex'}, False, False),
    ({'mode':'codex_with_tools'}, True, True),
    ({'mode':'codex_rubric_only','static_tools_enabled':False}, False, True),
    ({'mode':'fallback_codex','static_tools_enabled':False}, False, True),
    ({'mode':'fallback_codex'}, True, True),
    ({'mode':'codex_rubric_only'}, False, True),
    ({'mode':'codex_with_tools','static_tools_enabled':False}, False, False),
])
def test_cached_application_must_match_requested_mode(bundle, tmp_path, cached, requested, compatible):
    root, evidence = bundle
    (tmp_path/'generation').mkdir()
    (tmp_path/'generation/result.json').write_text(json.dumps({'status':'generated'}))
    (tmp_path/'reference_validation').mkdir()
    result_path = tmp_path/'reference_validation/result.json'
    cached = dict(cached, status='completed', score=None if cached['mode']=='fallback_codex' else 1)
    result_path.write_text(json.dumps(cached))
    original = result_path.read_bytes()
    case = SimpleNamespace(task={'task_id':tmp_path.name})
    if compatible:
        row = run_case('test', case, tmp_path.parent, {'static_tools_enabled':requested})
        assert row['application'] == cached
    else:
        with pytest.raises(ValueError, match='mode.*new output directory'):
            run_case('test', case, tmp_path.parent, {'static_tools_enabled':requested})
    assert result_path.read_bytes() == original


def test_batch_rejects_wrong_mode_before_touching_reports_or_starting_cases(tmp_path, monkeypatch):
    case = SimpleNamespace(task={'task_id':'task'})
    monkeypatch.setattr(validate_reference, 'load_selected', lambda *args: [('test', case)])
    def unexpected_call(*args):
        pytest.fail('No report writes or case executions before cache validation')
    monkeypatch.setattr(validate_reference, 'write_report', unexpected_call)
    monkeypatch.setattr(validate_reference, 'run_case', unexpected_call)
    result_path = tmp_path/'task/reference_validation/result.json'
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({'mode':'codex_with_tools'}))
    (tmp_path/'RESULTS.md').write_text('original report')
    with pytest.raises(ValueError, match='mode.*new output directory'):
        validate_reference.run_batch(tmp_path, tmp_path, ['test'], {'static_tools_enabled':False})
    assert (tmp_path/'RESULTS.md').read_text() == 'original report'


def test_interrupted_rubric_only_application_keeps_mode(bundle, tmp_path):
    (tmp_path/'generation').mkdir()
    (tmp_path/'generation/result.json').write_text(json.dumps({'status':'generated'}))
    (tmp_path/'reference_validation').mkdir()
    case = SimpleNamespace(task={'task_id':tmp_path.name})
    row = run_case('test', case, tmp_path.parent, {'static_tools_enabled':False})
    assert row['application']['mode'] == 'codex_rubric_only'
    assert row['application']['static_tools_enabled'] is False
    assert row['application']['status'] == 'verification_error'
