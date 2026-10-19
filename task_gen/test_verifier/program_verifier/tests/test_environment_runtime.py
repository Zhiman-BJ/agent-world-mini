import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from task_gen.test_verifier.program_verifier import environment_runtime as runtime


def evidence(tmp_path):
    root = tmp_path/'bundle'
    (root/'evidence/actual/final').mkdir(parents=True)
    (root/'evidence/actual/final/value.txt').write_text('candidate')
    (root/'evidence/tools.json').write_text(json.dumps([{'name':'inspect'}]))
    (root/'evidence/environment.json').write_text('{}')
    (root/'program_request.json').write_text(json.dumps({
        'initial_state':'evidence/initial',
        'actual':{'final_state':'evidence/actual/final'},
        'reference':{'final_state':'evidence/reference/final'}}))
    return root


def test_fallback_resolves_after_bundle_move_and_rejects_escape(tmp_path):
    root = evidence(tmp_path)
    (root/'fallback_input.json').write_text(json.dumps({'actual_state':'evidence/actual/final'}))
    moved = tmp_path/'moved'
    root.rename(moved)
    assert runtime.load_fallback(moved)['actual_state'] == moved/'evidence/actual/final'
    (moved/'fallback_input.json').write_text('{"actual_state":"../outside"}')
    with pytest.raises(ValueError, match='outside'):
        runtime.load_fallback(moved)


def test_tool_uses_disposable_candidate_copy_and_logs_business_failure(tmp_path, monkeypatch):
    root = evidence(tmp_path)
    import harness.execution
    def inspect(name, arguments, tools, state_root, **options):
        assert name == 'inspect' and tools['inspect']['name'] == name
        assert (state_root/'value.txt').read_text() == 'candidate'
        (state_root/'value.txt').write_text('temporary mutation')
        return {'tool':name, 'result':{'success':False, 'valid':False},
                'error':'工具返回值必须包含 success=true'}
    monkeypatch.setattr(harness.execution, 'call_environment_tool', inspect)
    scratch = tmp_path/'scratch'
    result = runtime.call_environment_tool(root, scratch, 'inspect', {})
    assert result == {'success':False, 'valid':False}
    assert (root/'evidence/actual/final/value.txt').read_text() == 'candidate'
    assert not list(scratch.glob('environment-state-*'))
    log = json.loads(next(scratch.glob('environment_call_*.json')).read_text())
    assert log['status'] == 'completed' and log['seconds'] >= 0


def test_profile_mounts_include_actual_interpreter(tmp_path, monkeypatch):
    root = evidence(tmp_path)
    profile = tmp_path/'profile'; (profile/'bin').mkdir(parents=True)
    base = tmp_path/'cpython'; (base/'bin').mkdir(parents=True)
    binary = base/'bin/python3'; binary.touch()
    python = profile/'bin/python'; python.symlink_to(binary)
    (profile/'pyvenv.cfg').write_text(f'home = {base}/bin\n')
    package = SimpleNamespace(delivery_root=tmp_path/'delivery', software_root=profile,
                              python_path=python, runtime={'backend':'python_profile'})
    monkeypatch.setattr(runtime, '_delivery', lambda *a: package)
    mounts = runtime.runtime_mounts(root)
    assert base in mounts and profile in mounts


def test_view_is_relative_and_does_not_leak_credentials(tmp_path):
    root = evidence(tmp_path)
    scratch = tmp_path/'scratch'
    runtime.prepare_runtime_view(scratch, root, {'api_key':'secret', 'delivery_root':'/example'})
    assert (scratch/'evidence_bundle').resolve() == root
    assert 'secret' not in (scratch/'runtime_config.json').read_text()
    instructions = runtime.runtime_instructions(root, scratch)
    assert '--evidence evidence_bundle' in instructions
    assert '--scratch .' in instructions


def test_runtime_preserves_full_original_contract_with_trimmed_task_schema(tmp_path, monkeypatch):
    import harness.delivery
    root = evidence(tmp_path)
    delivery = tmp_path/'delivery'
    delivery.mkdir()
    selected = delivery/'tools_repaired.json'
    original = [{'name':'inspect', 'description':'full documentation',
                 'parameters':{'type':'object', 'description':'full schema'},
                 'internal':{'code':'original repaired code'}}]
    trimmed = [{'name':'inspect', 'parameters':{'type':'object'},
                'internal':{'code':'original repaired code'}}]
    selected.write_text(json.dumps(original))
    (root/'evidence/tools.json').write_text(json.dumps(trimmed))
    binding = delivery/'binding.json'
    package = SimpleNamespace(binding_path=binding, delivery_root=delivery,
        binding={'tools_path':'tools_repaired.json'}, runtime={'backend':'python_profile','profile_id':'p1'},
        software_root=None)
    monkeypatch.setattr(harness.delivery, 'load_delivery', lambda *a: package)
    case = SimpleNamespace(runtime={'binding_path':str(binding),'tools_path':str(selected)})
    runtime.prepare_runtime(case, root)
    assert json.loads((root/'evidence/runtime_tools.json').read_text()) == original
    assert json.loads((root/'evidence/tools.json').read_text()) == trimmed
    metadata = json.loads((root/'evidence/runtime.json').read_text())
    assert metadata['binding'] == 'binding.json'
    assert runtime._delivery(root, {'delivery_root':str(delivery)}) is package
    trimmed[0]['internal']['code'] = 'unexpected implementation'
    (root/'evidence/tools.json').write_text(json.dumps(trimmed))
    with pytest.raises(ValueError, match='changed'):
        runtime._delivery(root, {'delivery_root':str(delivery)})
