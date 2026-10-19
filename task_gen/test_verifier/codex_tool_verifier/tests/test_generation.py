import json
from pathlib import Path
import shutil
import sys

import pytest

from task_gen.test_verifier.codex_tool_verifier.check import check_workspace
from task_gen.test_verifier.codex_tool_verifier.contracts import DESIGN_FILES
from task_gen.test_verifier.codex_tool_verifier.generation import GenerationModel, generate
from .conftest import dump


def design_copy(bundle, destination, *, zero_tools=False):
    source, _ = bundle
    destination.mkdir(parents=True)
    for name in DESIGN_FILES:
        shutil.copyfile(source/name, destination/name)
    if zero_tools:
        rubric = json.loads((destination/'rubrics.json').read_text())
        rubric['rubrics'][0].update(method='codex', static_tools=[], required_tools=[])
        dump(destination/'rubrics.json', rubric)
        dump(destination/'tools.json', {'tools': []})
    return destination


def test_design_is_frozen_and_only_each_target_is_received(bundle, tmp_path):
    _, evidence = bundle
    output = tmp_path/'run'
    calls = []
    frozen = {}
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = output_dir/('design_workspace' if stage == 'design' else 'implementation_workspace')
        if stage == 'design':
            design_copy(bundle, workspace)
            for name in DESIGN_FILES:
                frozen[name] = (workspace/name).read_bytes()
        else:
            assert stage == 'tools/T1.py'
            shutil.copytree(output_dir/'verifier', workspace)
            assert {name: (workspace/name).read_bytes() for name in DESIGN_FILES} == frozen
            shutil.copyfile(bundle[0]/stage, workspace/stage)
            (workspace.parent/'self_test.py').write_text('assert True\n')
        return workspace
    result = generate(evidence, output, {'final_review_enabled':False}, model_call=model)
    assert result['status'] == 'generated'
    assert calls == ['design', 'tools/T1.py']
    assert {name: (output/'verifier'/name).read_bytes() for name in DESIGN_FILES} == frozen
    assert {str(p.relative_to(output/'verifier')) for p in (output/'verifier').rglob('*') if p.is_file()} == DESIGN_FILES | {'tools/__init__.py', 'tools/T1.py'}


@pytest.mark.parametrize('change_previous', [False, True])
def test_each_tool_has_its_own_target_and_previous_tools_are_frozen(bundle, tmp_path, change_previous):
    _, evidence = bundle
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = output_dir/f'workspace_{len(calls)}'
        if stage == 'design':
            design_copy(bundle, workspace)
            tools = json.loads((workspace/'tools.json').read_text())
            tools['tools'].append(dict(tools['tools'][0], id='T2', file='tools/T2.py'))
            dump(workspace/'tools.json', tools)
            rubrics = json.loads((workspace/'rubrics.json').read_text())
            rubrics['rubrics'][0]['static_tools'].append('T2')
            dump(workspace/'rubrics.json', rubrics)
        else:
            shutil.copytree(output_dir/'verifier', workspace)
            assert stage in prompt
            if stage == 'tools/T2.py':
                assert (workspace/'tools/T1.py').read_text() == 'def tool(context, inputs):\n    return {"status":"missing"}\n'
                if change_previous:
                    (workspace/'tools/T1.py').write_text('changed')
            (workspace/stage).write_text('def tool(context, inputs):\n    return {"status":"missing"}\n')
        return workspace
    result = generate(evidence, tmp_path/'run', {'final_review_enabled':False}, model_call=model)
    assert result['status'] == ('generation_failed' if change_previous else 'generated')
    assert calls == ['design', 'tools/T1.py', 'tools/T2.py']
    assert (tmp_path/'run/verifier/tools/T1.py').read_text() == 'def tool(context, inputs):\n    return {"status":"missing"}\n'


@pytest.mark.parametrize('change', ['rubrics.json', 'instructions.md', 'tools/extra.py'])
def test_implementation_rejects_changes_outside_its_target_without_retry(bundle, tmp_path, change):
    _, evidence = bundle
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = output_dir/f'workspace_{len(calls)}'
        if stage == 'design':
            return design_copy(bundle, workspace)
        shutil.copytree(output_dir/'verifier', workspace)
        shutil.copyfile(bundle[0]/stage, workspace/stage)
        (workspace/change).write_text('changed')
        return workspace
    result = generate(evidence, tmp_path/'run', {'final_review_enabled':False}, model_call=model)
    assert result['status'] == 'generation_failed'
    assert 'outside its target' in result['error']
    assert calls == ['design', 'tools/T1.py']


def test_zero_tools_needs_only_design(bundle, tmp_path):
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        return design_copy(bundle, output_dir/'design_workspace', zero_tools=True)
    result = generate(bundle[1], tmp_path/'run', {'final_review_enabled':False}, model_call=model)
    assert result['status'] == 'generated' and result['tool_count'] == 0
    assert calls == ['design']
    assert not (tmp_path/'run/verifier/main.py').exists()


def test_failed_design_is_recorded_without_retry(bundle, tmp_path):
    calls = []
    def bad(prompt, *, stage, output_dir):
        calls.append(stage)
        raise RuntimeError('provider failed')
    result = generate(bundle[1], tmp_path/'run', {'final_review_enabled':False}, model_call=bad)
    assert result['status'] == 'generation_failed' and result['stage'] == 'design'
    assert calls == ['design']
    assert json.loads((tmp_path/'run/generation/result.json').read_text()) == result


def test_final_session_runs_even_when_design_never_delivers(bundle, tmp_path):
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        if stage == 'design':
            raise TimeoutError('design did not deliver')
        assert stage == 'final'
        assert not list((output_dir/'verifier').iterdir())
        assert (output_dir/'generation/staged_result.json').is_file()
        return bundle[0]
    value = generate(bundle[1], tmp_path/'run', model_call=model)
    assert value['status'] == 'generated'
    assert calls == ['design','final']


def test_final_codex_can_complete_failed_tool_and_correct_design(bundle, tmp_path):
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = output_dir/f'workspace_{len(calls)}'
        if stage == 'design':
            return design_copy(bundle, workspace)
        if stage == 'tools/T1.py':
            raise RuntimeError('implementation failed')
        assert stage == 'final'
        shutil.copytree(output_dir/'verifier', workspace)
        shutil.copyfile(bundle[0]/'tools/T1.py', workspace/'tools/T1.py')
        doc = json.loads((workspace/'rubrics.json').read_text())
        doc['rubrics'][0]['criteria']['1'] = 'The task requested data is valid.'
        dump(workspace/'rubrics.json', doc)
        return workspace
    value = generate(bundle[1], tmp_path/'run', model_call=model)
    assert value['status'] == 'generated'
    assert calls == ['design','tools/T1.py','final']
    assert 'rubrics.json' in value['final_review']['changed_files']


def test_design_self_test_runs_and_cannot_live_in_package(bundle, tmp_path):
    root = design_copy(bundle, tmp_path/'design')
    self_test = tmp_path/'self_test.py'
    marker = tmp_path/'tested'
    self_test.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    check_workspace(root, 'Save data.', 'design', self_test)
    assert marker.is_file()
    self_test.write_text('raise AssertionError("design test failed")\n')
    with pytest.raises(AssertionError, match='design test failed'):
        check_workspace(root, 'Save data.', 'design', self_test)
    self_test.unlink()
    with pytest.raises(ValueError, match='self_test'):
        check_workspace(root, 'Save data.', 'design', self_test)


@pytest.mark.parametrize('body, error', [
    ('assert True', 'exercise'),
    ('from tools.T1 import tool\nassert tool(None, {}) == {"status":"ok", "value":"invalid"}', 'schema'),
])
def test_real_profile_requires_execution_and_valid_return(bundle, tmp_path, body, error):
    root, _ = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    return {"status":"ok", "value":"invalid"}\n')
    self_test = tmp_path/'self_test.py'
    self_test.write_text(body+'\n')
    with pytest.raises(ValueError, match=error):
        check_workspace(root, 'Save data.', 'tools/T1.py', self_test)


def test_real_profile_validates_actual_outputs_not_unused_samples(bundle, tmp_path):
    root, _ = bundle
    self_test = tmp_path/'self_test.py'
    self_test.write_text('from tools.T1 import tool\nassert tool(None, {"path":"/definitely/absent.json"}) == {"status":"missing"}\n')
    check_workspace(root, 'Save data.', 'tools/T1.py', self_test)


@pytest.mark.parametrize('real_status', [None, 'verification_error', 'raised_error', 'fake_context', 'ok', 'missing'])
def test_real_evidence_must_have_a_normal_result_even_if_fixture_passes(bundle, tmp_path, real_status):
    root, evidence = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    if inputs["path"] == "raised_error": raise RuntimeError("broken")\n    return {"status": inputs["path"]}\n')
    self_test = tmp_path/'self_test.py'
    code = ('from types import SimpleNamespace\nfrom tools.T1 import tool\n'
            f'tool(SimpleNamespace(root={str(evidence)!r}, actual={{}}), {{"path":"missing"}})\n')
    if real_status is not None:
        code += ('def check_real_evidence(context):\n'
                 f'    assert context.actual["final_state"] == __import__("pathlib").Path({str(evidence/"final")!r})\n')
        if real_status == 'fake_context':
            code += f'    tool(SimpleNamespace(root={str(evidence)!r}), {{"path":"missing"}})\n'
        elif real_status == 'raised_error':
            code += '    try:\n        tool(context, {"path":"raised_error"})\n    except RuntimeError:\n        pass\n    tool(context, {"path":"missing"})\n'
        else:
            code += f'    tool(context, {{"path":{real_status!r}}})\n'
            code += '    tool(context, {"path":"missing"})\n'
    self_test.write_text(code)
    if real_status in (None, 'verification_error', 'raised_error', 'fake_context'):
        with pytest.raises(ValueError, match='real evidence'):
            check_workspace(root, 'Save data.', 'tools/T1.py', self_test, evidence_root=evidence)
    else:
        check_workspace(root, 'Save data.', 'tools/T1.py', self_test, evidence_root=evidence)


@pytest.mark.parametrize('invalid_return', [False, True])
def test_profile_distinguishes_expected_exceptions_from_invalid_returns(bundle, tmp_path, invalid_return):
    root, _ = bundle
    (root/'tools/T1.py').write_text('def tool(context, inputs):\n    if inputs.get("fail"):\n        raise ValueError("unsupported input")\n    return None if inputs.get("invalid") else {"status":"missing"}\n')
    self_test = tmp_path/'self_test.py'
    self_test.write_text('from tools.T1 import tool\ntry:\n    tool(None, {"fail": True})\nexcept ValueError:\n    pass\nassert tool(None, {}) == {"status":"missing"}\n' + ('tool(None, {"invalid": True})\n' if invalid_return else ''))
    if invalid_return:
        with pytest.raises(ValueError, match='schema'):
            check_workspace(root, 'Save data.', 'tools/T1.py', self_test)
    else:
        check_workspace(root, 'Save data.', 'tools/T1.py', self_test)


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
@pytest.mark.parametrize('stage, broken', [('design', False), ('design', True), ('tools/T1.py', False), ('tools/T1.py', True), ('tools/T1.py', 'write_evidence')])
def test_generation_model_delivers_files_and_host_replays_self_test(bundle, tmp_path, monkeypatch, stage, broken):
    from task_gen.test_verifier.program_verifier.generation import model
    _, evidence = bundle
    output = tmp_path/'run'
    output.mkdir()
    if stage != 'design':
        design_copy(bundle, output/'verifier')
        (output/'verifier/tools').mkdir()
        (output/'verifier/tools/__init__.py').write_text('')
    executable = Path(sys.executable).resolve()
    monkeypatch.setattr(model.shutil, 'which', lambda name: str(executable) if name == 'codex' else '/usr/bin/bwrap')
    monkeypatch.setattr(model, '_isolated_auth', lambda *args: {})
    class Client:
        def __init__(self, **kwargs):
            pass
        def run_until_files(self, prompt, *, working_directory, required_paths):
            assert required_paths == (working_directory/'scratch/acceptance_ready.json',)
            root = working_directory/'scratch/verifier'
            if stage == 'design':
                for name in DESIGN_FILES:
                    shutil.copyfile(bundle[0]/name, root/name)
                test = 'assert True\n'
            else:
                shutil.copyfile(bundle[0]/stage, root/stage)
                test = ('from tools.T1 import tool\n'
                        'def check_real_evidence(context):\n'
                        '    assert tool(context, {"path":str(context.actual["final_state"]/"data.json")}) == {"status":"ok", "value":3}\n')
            if broken == 'write_evidence':
                test += f'from pathlib import Path\nPath({str(evidence/"answer.txt")!r}).write_text("destroyed")\n'
            elif broken:
                test += 'raise AssertionError("broken self test")\n'
            (working_directory/'scratch/self_test.py').write_text(test)
            return 'Files written and tested.'
    monkeypatch.setattr(model, '_VerifierClient', Client)
    logs = output/'generation'
    logs.mkdir()
    generator = GenerationModel(evidence, {'generation_call_timeout_seconds':5}, logs)
    if broken:
        with pytest.raises((RuntimeError, TimeoutError), match='self.test|acceptance'):
            generator('write and test', stage=stage, output_dir=output)
        assert (evidence/'answer.txt').read_text() == 'completed'
    else:
        workspace = generator('write and test', stage=stage, output_dir=output)
        assert workspace.is_dir()
        acceptance = json.loads(next(logs.glob('*/acceptance.json')).read_text())
        assert acceptance['exit_code'] == 0
        assert 'task_gen.test_verifier.codex_tool_verifier.check' in acceptance['command']


@pytest.mark.parametrize('broken', [False, True])
def test_completion_marker_requires_successful_design_self_test(bundle, tmp_path, monkeypatch, broken):
    from task_gen.test_verifier.codex_tool_verifier import check
    root = design_copy(bundle, tmp_path/'design')
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    dump(evidence/'request.json', {'task_text': 'Save data.'})
    test = tmp_path/'self_test.py'
    test.write_text('assert False\n' if broken else 'assert True\n')
    marker = tmp_path/'accepted.json'
    marker.write_text('stale')
    monkeypatch.setattr(sys, 'argv', ['check', '--workspace', str(root), '--evidence', str(evidence),
        '--stage', 'design', '--self-test', str(test), '--completion-file', str(marker)])
    if broken:
        with pytest.raises(AssertionError):
            check.main()
        assert not marker.exists()
    else:
        check.main()
        assert json.loads(marker.read_text()) == {'status': 'passed', 'stage': 'design'}
