import json
import importlib
from pathlib import Path
import shutil
import os
import sys

import pytest

from task_gen.test_verifier.program_verifier.tests.test_generation import design


@pytest.mark.parametrize('method,mode', [('codex_rubric_verifier', 'codex_rubric_only'),
                                       ('direct_codex_rubric', 'direct_codex_rubric')])
@pytest.mark.parametrize('status', ['completed', 'verification_error'])
def test_rubric_final_check_uses_its_native_runtime_without_fallback(tmp_path, monkeypatch, method, mode, status):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.tests.test_independent_rubric_generation import design as rubric_design
    application = importlib.import_module('task_gen.test_verifier.'+method+'.application')
    root = tmp_path/'verifier'; rubric_design(root)
    evidence = tmp_path/'evidence'; evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'test', 'task_text':'Return value.'}))
    test = tmp_path/'self_test.py'; test.write_text('assert True\n')
    def apply(verifier, source, output, config):
        assert config['fallback_enabled'] is False
        assert config['static_tools_enabled'] is False
        output.mkdir()
        return {'mode':mode, 'status':status, 'score':0, 'outcome':'fail', 'error':'native inspection failed'}
    monkeypatch.setattr(application, 'apply', apply)
    if status == 'completed':
        result = final_review.check_workspace(method, root, evidence, test, {}, tmp_path/'native')
        assert result['score'] == 0 and result['outcome'] == 'fail'
    else:
        with pytest.raises(ValueError, match='native inspection failed'):
            final_review.check_workspace(method, root, evidence, test, {}, tmp_path/'native')
    assert json.loads((tmp_path/'native_result.json').read_text())['status'] == status


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
@pytest.mark.parametrize('method,mode', [('codex_rubric_verifier', 'codex_rubric_only'),
                                       ('direct_codex_rubric', 'direct_codex_rubric')])
def test_rubric_generation_final_review_and_native_acceptance_in_sandbox(tmp_path, monkeypatch, method, mode):
    from task_gen import task_eval_verifier as base
    from task_gen.test_verifier.codex_rubric_verifier import generation
    from task_gen.test_verifier.program_verifier.generation import model
    from task_gen.test_verifier.tests.test_independent_rubric_generation import design as rubric_design
    state = tmp_path/'state'; state.mkdir()
    (state/'data.json').write_text('{"value":8}')
    evidence = base.prepare_verification_workspace(tmp_path/'source', task={'task_id':'test','task_text':'Return value.'},
        environment={}, initial_state=state, actual_state=state, calls=[], answer='8', execution={})
    (evidence/'program_request.json').write_text(json.dumps({
        'task_id':'test', 'task_text':'Return value.', 'environment':'evidence/environment.json',
        'tools':'evidence/tools.json', 'initial_state':'evidence/initial',
        'actual':{'final_state':'evidence/actual/final', 'answer':'evidence/actual/answer.txt',
                  'calls':'evidence/actual/tool_calls.jsonl', 'execution':'evidence/actual/execution.json'},
        'reference':{}}))
    executable = tmp_path/'node/lib/codex/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
args=sys.argv
working=Path(args[args.index('--cd')+1])
scratch=working if working.name=='scratch' else working/'scratch'
(scratch/'investigation.md').write_text('R1: checked answer and data.json: 8.')
value={{'task_id':'test','outcome':'fail','summary':'Checked fixed criteria.',
    'rubrics':[{{'rubric_id':'R1','score':0,'reason':'Valid zero for acceptance.',
    'evidence':[{{'path':'evidence/actual/answer.txt','locator':'line 1','finding':'8'}}],
    'tool_call_ids':[]}}]}}
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(value))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':17,'output_tokens':3}}}}))
''')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(executable.parent)+os.pathsep+os.environ['PATH'])
    def auth(config, target):
        (target/'config.toml').write_text('model="test"\n')
        return {}
    monkeypatch.setattr(model, '_isolated_auth', auth)
    class Designer:
        def __init__(self, *args):
            pass
        def __call__(self, prompt, *, stage, output_dir):
            assert stage == 'design'
            workspace = tmp_path/'design'; rubric_design(workspace)
            return workspace
    class Reviewer:
        def __init__(self, **kwargs):
            pass
        def run_until_files(self, prompt, *, working_directory, required_paths):
            assert '--method '+method in prompt
            assert not (output/'frozen_manifest.json').exists()
            workspace = working_directory/'scratch/verifier'
            (workspace/'instructions.md').write_text('Reviewed: inspect actual answer under fixed criteria.')
            (working_directory/'scratch/self_test.py').write_text(
                'import json\nfrom pathlib import Path\n'
                f'root = Path({str(workspace)!r})\n'
                'assert json.loads((root/"tools.json").read_text()) == {"tools":[]}\n'
                'assert json.loads((root/"rubrics.json").read_text())["rubrics"][0]["id"] == "R1"\n')
            return 'Ready for independent host replay.'
    monkeypatch.setattr(generation, 'GenerationModel', Designer)
    monkeypatch.setattr(model, '_VerifierClient', Reviewer)
    output = tmp_path/'run'
    result = generation.generate_for(method, evidence, output,
        {'application_timeout_seconds':15, 'final_review_timeout_seconds':120,
         'codex':{'executable':str(executable), 'attempts':1, 'record_usage':True}})
    assert result['status'] == 'generated', result
    native = list((output/'generation/01_final/scratch/native_runs').glob('*/result.json'))
    assert len(native) == 1
    value = json.loads(native[0].read_text())
    assert value['status'] == 'completed' and value['mode'] == mode, value
    assert value['score'] == 0 and value['outcome'] == 'fail'
    assert value['cost']['calls'][0]['usage'] == [{'input_tokens':17,'output_tokens':3}]
    assert not list(native[0].parent.glob('fallback*'))
    assert 'Reviewed' in (output/'verifier/instructions.md').read_text()
    assert json.loads((output/'frozen_manifest.json').read_text()) == model.snapshot(output/'verifier')


def program_bundle(tmp_path):
    root = tmp_path/'run/verifier'
    (root/'rubrics').mkdir(parents=True)
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'model_calls_design.json').write_text('[]')
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    (root/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    test = tmp_path/'self_test.py'
    test.write_text('assert True\n')
    return root, evidence, test


def test_final_check_accepts_zero_and_disables_fallback(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    seen = []
    def apply(verifier, source, output, config):
        seen.append(config)
        output.mkdir()
        return {'mode':'program','status':'completed','score':0,'rubrics':[]}
    monkeypatch.setattr(runner, 'apply', apply)
    report = final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert report['score'] == 0
    assert seen[0]['fallback_enabled'] is False


def test_final_check_rejects_native_error_and_preserves_report(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    def apply(*args):
        return {'mode':'program','status':'verification_error','score':None,'error':'broken interface'}
    monkeypatch.setattr(runner, 'apply', apply)
    with pytest.raises(ValueError, match='broken interface'):
        final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert json.loads((tmp_path/'native_result.json').read_text())['status'] == 'verification_error'


def test_successful_self_test_exit_cannot_skip_native_run(tmp_path, monkeypatch):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.application import runner
    root, evidence, test = program_bundle(tmp_path)
    test.write_text('raise SystemExit(0)\n')
    def apply(verifier, source, output, config):
        output.mkdir()
        (output/'executed').touch()
        return {'mode':'program','status':'completed','score':0}
    monkeypatch.setattr(runner, 'apply', apply)
    value = final_review.check_workspace('program', root, evidence, test, {}, tmp_path/'native')
    assert value['score'] == 0 and (tmp_path/'native/executed').is_file()


def test_final_session_can_change_main_and_rubric_together(tmp_path):
    from task_gen.test_verifier.final_review import run_final_review
    root, evidence, _ = program_bundle(tmp_path)
    def model(prompt, *, stage, output_dir):
        assert stage == 'final'
        assert '检查数据' in prompt
        workspace = tmp_path/'final_workspace'
        shutil.copytree(root, workspace)
        (workspace/'main.py').write_text('def run(context):\n    return {"fixed": True}\n')
        doc = json.loads((workspace/'rubrics.json').read_text())
        doc['rubrics'][0]['criteria']['1'] = '符合原任务的明确条件'
        (workspace/'rubrics.json').write_text(json.dumps(doc))
        return workspace
    report = run_final_review('program', evidence, root.parent, {}, model_call=model)
    assert 'fixed' in (root/'main.py').read_text()
    assert json.loads((root/'rubrics.json').read_text())['rubrics'][0]['criteria']['1'] == '符合原任务的明确条件'
    assert report['status'] == 'completed'
    assert report['after'] != report['before']
    assert (root.parent/'generation/before_final/main.py').read_text() == 'def run(context):\n    return {}\n'


def test_failed_final_session_never_replaces_deliverable(tmp_path):
    from task_gen.test_verifier.final_review import run_final_review
    root, evidence, _ = program_bundle(tmp_path)
    original = (root/'main.py').read_bytes()
    def bad(*args, **kwargs):
        raise RuntimeError('budget exhausted')
    with pytest.raises(RuntimeError, match='budget exhausted'):
        run_final_review('program', evidence, root.parent, {}, model_call=bad)
    assert (root/'main.py').read_bytes() == original
    assert json.loads((root.parent/'generation/final_result.json').read_text())['status'] == 'error'


@pytest.mark.parametrize('broken', [False, True])
def test_final_model_replays_complete_native_run_in_sandbox(tmp_path, monkeypatch, broken):
    from task_gen.test_verifier.final_review import run_final_review
    from task_gen.test_verifier.program_verifier.tests.test_runtime import bundle
    from task_gen.test_verifier.program_verifier.generation import model
    code = ('def check_R1(context, inputs):\n'
            '    return {"rubric_id":"R1","score":0,"reason":"zero is valid",'
            '"evidence":[{"path":".","locator":"directory","finding":"checked"}]}\n')
    root, evidence = bundle(tmp_path, code)
    (evidence/'request.json').write_bytes((evidence/'program_request.json').read_bytes())
    executable = tmp_path/'node/lib/codex/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.touch()
    real_which = shutil.which
    monkeypatch.setattr(model.shutil, 'which', lambda name: str(executable) if name == 'codex' else real_which(name))
    monkeypatch.setattr(model, '_isolated_auth', lambda *args: {})
    class Client:
        def __init__(self, **kwargs):
            pass
        def run_until_files(self, prompt, *, working_directory, required_paths):
            if broken and '宿主独立复验失败' in prompt:
                raise RuntimeError('still broken; cannot repair')
            workspace = working_directory/'scratch/verifier'
            (workspace/'main.py').write_text((root/'main.py').read_text()+'\n# final repair\n')
            if broken:
                (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    raise RuntimeError("still broken")\n')
            (working_directory/'scratch/self_test.py').write_text('assert True\n')
            return 'Ready for independent host test.'
    monkeypatch.setattr(model, '_VerifierClient', Client)
    config = {'program_timeout_seconds':5, 'final_review_timeout_seconds':100}
    if broken:
        with pytest.raises(RuntimeError, match='still broken'):
            run_final_review('program', evidence, root.parent, config)
        assert 'final repair' not in (root/'main.py').read_text()
    else:
        value = run_final_review('program', evidence, root.parent, config)
        assert value['status'] == 'completed'
        assert 'final repair' in (root/'main.py').read_text()
        native = list((root.parent/'generation/01_final/scratch/native_runs').glob('*/result.json'))
        assert len(native) == 1 and json.loads(native[0].read_text())['score'] == 0


def test_pipeline_final_session_can_complete_failed_implementation(tmp_path):
    from task_gen.test_verifier.program_verifier.generation.pipeline import generate
    calls = []
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace = tmp_path/f'workspace_{len(calls)}'
        if stage == 'design':
            workspace.mkdir()
            (workspace/'rubrics.json').write_text(json.dumps(design()))
            (workspace/'main.py').write_text('def run(context):\n    return {}\n')
        elif stage == 'rubrics/R1.py':
            raise RuntimeError('design interface contradiction')
        else:
            assert stage == 'final'
            shutil.copytree(output_dir/'verifier', workspace)
            (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
        return workspace
    value = generate(evidence, tmp_path/'run', model_call=model)
    assert value['status'] == 'generated'
    assert calls == ['design','rubrics/R1.py','final']
    assert json.loads((tmp_path/'run/generation/staged_result.json').read_text())['status'] == 'generation_failed'


def test_final_session_runs_even_when_design_never_delivers(tmp_path):
    from task_gen.test_verifier.program_verifier.generation.pipeline import generate
    root, evidence, _ = program_bundle(tmp_path)
    calls = []
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        if stage == 'design':
            raise TimeoutError('design did not deliver')
        assert stage == 'final'
        assert not list((output_dir/'verifier').iterdir())
        assert (output_dir/'generation/staged_result.json').is_file()
        return root
    value = generate(evidence, tmp_path/'new_run', model_call=model)
    assert value['status'] == 'generated'
    assert calls == ['design','final']


@pytest.mark.parametrize('exhaust_budget', [False, True])
def test_host_failure_repairs_same_workspace_with_shared_deadline_and_usage(tmp_path, monkeypatch, exhaust_budget):
    from task_gen.test_verifier import final_review
    from task_gen.test_verifier.program_verifier.generation import model
    from task_gen.test_verifier.program_verifier.tests.test_runtime import bundle
    code = ('def check_R1(context, inputs):\n'
            '    return {"rubric_id":"R1","score":0,"reason":"valid zero",'
            '"evidence":[{"path":".","locator":"directory","finding":"checked"}]}\n')
    root, evidence = bundle(tmp_path, code)
    (evidence/'request.json').write_bytes((evidence/'program_request.json').read_bytes())
    executable = tmp_path/'node/lib/codex/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.touch()
    real_which = shutil.which
    monkeypatch.setattr(model.shutil, 'which', lambda name: str(executable) if name == 'codex' else real_which(name))
    monkeypatch.setattr(model, '_isolated_auth', lambda *args: {})
    clock = [0.0]
    monkeypatch.setattr(model.time, 'monotonic', lambda: clock[0])
    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.options = kwargs
        def run_until_files(self, prompt, *, working_directory, required_paths):
            calls.append((working_directory, self.options['timeout_seconds']))
            self.options['log_directory'].mkdir(parents=True, exist_ok=True)
            (self.options['log_directory']/'stdout.log').write_text('{"usage":{"input_tokens":17}}\n')
            workspace = working_directory/'scratch/verifier'
            if len(calls) == 1:
                (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    raise RuntimeError("host-specific failure")\n')
            else:
                assert not required_paths[0].exists()
                assert 'host-specific failure' in prompt
                assert 'acceptance.stderr.log' in prompt
                (workspace/'rubrics/R1.py').write_text(code)
            (working_directory/'scratch/self_test.py').write_text('assert True\n')
            required_paths[0].write_text('{"status":"passed"}')
            clock[0] = 40.0 if exhaust_budget else clock[0] + 2.0
            return 'Model-side check passed; ready for host replay.'
    monkeypatch.setattr(model, '_VerifierClient', Client)
    config = {'program_timeout_seconds':5, 'final_review_timeout_seconds':100}
    if exhaust_budget:
        with pytest.raises(TimeoutError, match='budget'):
            final_review.run_final_review('program', evidence, root.parent, config)
        assert 'host-specific failure' not in (root/'rubrics/R1.py').read_text()
    else:
        result = final_review.run_final_review('program', evidence, root.parent, config)
        assert result['status'] == 'completed'
        assert len(calls) == 2 and calls[0][0] == calls[1][0]
        assert calls[1][1] < calls[0][1]
    call = root.parent/'generation/01_final'
    record = json.loads((call/'call.json').read_text())
    assert record['usage'] == [{'input_tokens':17}] * len(calls)
    assert record['attempts'][0]['status'] == 'error'
    assert 'host-specific failure' in record['attempts'][0]['error']
    assert (call/'acceptance.stderr.log').is_file()
    assert record['status'] == ('error' if exhaust_budget else 'completed')


def test_final_package_allows_readme_and_python_cache_but_not_extra_code(tmp_path):
    import py_compile
    from task_gen.test_verifier.final_review import package_check
    root, _, _ = program_bundle(tmp_path)
    (root/'README.md').write_text('Verifier usage.\n')
    py_compile.compile(str(root/'main.py'), doraise=True)
    package_check('program', root, '检查数据')
    (root/'__pycache__/hidden.py').write_text('print("not bytecode")\n')
    with pytest.raises(ValueError, match='Unexpected'):
        package_check('program', root, '检查数据')


def test_failed_implementation_survives_for_final_repair(tmp_path):
    from task_gen.test_verifier.program_verifier.generation.pipeline import generate
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    partial = 'def check_R1(context, inputs):\n    return {"partial": True}\nBROKEN(\n'
    def model(prompt, *, stage, output_dir):
        workspace = tmp_path/stage.replace('/', '_')
        if stage == 'design':
            workspace.mkdir()
            (workspace/'rubrics.json').write_text(json.dumps(design()))
            (workspace/'main.py').write_text('def run(context):\n    return {}\n')
        elif stage == 'rubrics/R1.py':
            (workspace/'rubrics').mkdir(parents=True)
            (workspace/stage).write_text(partial)
        else:
            assert stage == 'final'
            assert (output_dir/'verifier/rubrics/R1.py').read_text() == partial
            shutil.copytree(output_dir/'verifier', workspace)
            (workspace/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
        return workspace
    result = generate(evidence, tmp_path/'run', model_call=model)
    assert result['status'] == 'generated'


def test_final_sandbox_explicitly_reads_prior_failed_workspace_without_write_access(tmp_path, monkeypatch):
    import subprocess
    import sys
    from task_gen.test_verifier.final_review import run_final_review
    from task_gen.test_verifier.program_verifier.generation import model
    from task_gen.test_verifier.program_verifier.tests.test_runtime import bundle
    code = ('def check_R1(context, inputs):\n'
            '    return {"rubric_id":"R1","score":0,"reason":"valid zero",'
            '"evidence":[{"path":".","locator":"directory","finding":"checked"}]}\n')
    root, evidence = bundle(tmp_path, code)
    (evidence/'request.json').write_bytes((evidence/'program_request.json').read_bytes())
    prior = root.parent/'generation/02_rubrics_R1/scratch/verifier/rubrics/R1.py'
    prior.parent.mkdir(parents=True)
    prior.write_text('partial failed implementation')
    previous_log = prior.parents[3]/'acceptance.stderr.log'
    previous_log.write_text('prior diagnosis')
    executable = tmp_path/'node/lib/codex/bin/codex'
    executable.parent.mkdir(parents=True)
    executable.touch()
    real_which = shutil.which
    monkeypatch.setattr(model.shutil, 'which', lambda name: str(executable) if name == 'codex' else real_which(name))
    monkeypatch.setattr(model, '_isolated_auth', lambda *args: {})
    class Client:
        def __init__(self, **kwargs):
            self.prefix = list(kwargs['command_prefix'])
        def run_until_files(self, prompt, *, working_directory, required_paths):
            probe = (f'from pathlib import Path\np=Path({str(prior)!r})\n'
                     'assert p.read_text()=="partial failed implementation"\n'
                     f'assert Path({str(previous_log)!r}).read_text()=="prior diagnosis"\n'
                     'try:\n    p.write_text("tampered")\nexcept OSError:\n    pass\n'
                     'else:\n    raise AssertionError("prior workspace writable")\n')
            subprocess.run(self.prefix+[sys.executable, '-c', probe], check=True)
            (working_directory/'scratch/self_test.py').write_text('assert True\n')
            return 'ready'
    monkeypatch.setattr(model, '_VerifierClient', Client)
    result = run_final_review('program', evidence, root.parent,
                              {'program_timeout_seconds':5,'final_review_timeout_seconds':100})
    assert result['status'] == 'completed'
    assert prior.read_text() == 'partial failed implementation'
