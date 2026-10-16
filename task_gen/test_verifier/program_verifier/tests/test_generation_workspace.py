import json
from pathlib import Path
import shutil

import pytest

from task_gen.test_verifier.program_verifier.generation.model import GenerationModel
from task_gen.test_verifier.program_verifier.tests.test_generation import design


def test_snapshot_ignores_only_python_bytecode_cache(tmp_path):
    import py_compile
    from task_gen.test_verifier.program_verifier.generation.model import snapshot
    source = tmp_path/'main.py'
    source.write_text('def run(context):\n    return {}\n')
    before = snapshot(tmp_path)
    py_compile.compile(str(source), doraise=True)
    assert list((tmp_path/'__pycache__').glob('*.pyc'))
    assert snapshot(tmp_path) == before
    (tmp_path/'__pycache__/unexpected.py').write_text('print("changed")\n')
    assert snapshot(tmp_path) != before


@pytest.mark.skipif(not shutil.which('bwrap'), reason='bubblewrap unavailable')
@pytest.mark.parametrize('broken_test', [False, True])
def test_codex_delivers_files_and_host_replays_its_self_test(tmp_path, monkeypatch, broken_test):
    from task_gen.test_verifier.program_verifier.generation import model
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_text':'检查数据'}))
    executable=Path(shutil.which('python')).resolve()
    monkeypatch.setattr(model.shutil,'which',lambda name: str(executable) if name=='codex' else '/usr/bin/bwrap')
    monkeypatch.setattr(model,'_isolated_auth',lambda *args: {})
    class Client:
        def __init__(self, **kwargs): pass
        def run_until_files(self, prompt, *, working_directory, required_paths):
            assert required_paths == (working_directory/'scratch/acceptance_ready.json',)
            root=working_directory/'scratch/verifier'
            (root/'rubrics.json').write_text(json.dumps(design()))
            (root/'main.py').write_text('def run(context):\n    return {"ready": True}\n')
            import py_compile
            py_compile.compile(str(root/'main.py'), doraise=True)
            (working_directory/'scratch/self_test.py').write_text(
                'from main import run\nassert run(None)=={"ready":True}\n'
                + ('raise AssertionError("self test failed")\n' if broken_test else ''))
            return '代码已写入，完成测试。'
    monkeypatch.setattr(model,'_VerifierClient',Client)
    logs=tmp_path/'generation';logs.mkdir()
    generator=GenerationModel(evidence,{},logs)
    if broken_test:
        with pytest.raises(RuntimeError,match='self.test|acceptance'):
            generator('write and test',stage='design',output_dir=tmp_path)
    else:
        root=generator('write and test',stage='design',output_dir=tmp_path)
        assert (root/'main.py').is_file()
        acceptance=json.loads(next(logs.glob('*/acceptance.json')).read_text())
        assert acceptance['exit_code']==0
    assert json.loads(next(logs.glob('*/call.json')).read_text())['status']==('error' if broken_test else 'completed')


def test_check_requires_exercising_the_delivered_function(tmp_path):
    from task_gen.test_verifier.program_verifier.generation.check import check_workspace
    root=tmp_path/'verifier';root.mkdir()
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    test=tmp_path/'self_test.py';test.write_text('assert True\n')
    with pytest.raises(ValueError,match='exercise'):
        check_workspace(root,'检查数据','design',test)


def test_self_test_cannot_leave_artifacts_in_delivery_directory(tmp_path, monkeypatch):
    import sys
    from task_gen.test_verifier.program_verifier.generation.check import check_workspace
    root=tmp_path/'verifier';root.mkdir()
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    test=tmp_path/'self_test.py'
    test.write_text('from main import run\nrun(None)\nfrom pathlib import Path\nPath('+repr(str(root/'expected_facts.json'))+').write_text("{}")\n')
    monkeypatch.delitem(sys.modules,'main',raising=False)
    with pytest.raises(ValueError,match='Unexpected.*expected_facts'):
        check_workspace(root,'检查数据','design',test)


@pytest.mark.parametrize('test_only_dependency',[False,True])
def test_self_test_cannot_add_runtime_unavailable_import_paths(tmp_path,monkeypatch,test_only_dependency):
    import sys
    from task_gen.test_verifier.program_verifier.generation.check import check_workspace
    root=tmp_path/'verifier';root.mkdir()
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    extra=tmp_path/'dependencies' if test_only_dependency else root
    test=tmp_path/'self_test.py'
    test.write_text('import sys\nsys.path.insert(0,'+repr(str(extra))+')\nfrom main import run\nrun(None)\n')
    monkeypatch.delitem(sys.modules,'main',raising=False)
    before=list(sys.path)
    if test_only_dependency:
        with pytest.raises(ValueError,match='unavailable at runtime'):
            check_workspace(root,'检查数据','design',test)
    else:
        check_workspace(root,'检查数据','design',test)
    assert sys.path==before


@pytest.mark.parametrize('bad_call_file',[False,True])
@pytest.mark.parametrize('explicit_parameter',[False,True])
def test_design_self_test_checks_model_binding_even_with_a_loose_sdk_stub(tmp_path,monkeypatch,bad_call_file,explicit_parameter):
    import sys
    from task_gen.test_verifier.program_verifier.generation.check import check_workspace
    root=tmp_path/'verifier';root.mkdir()
    spec=design();spec['model_calls']=[{'file':'model_calls/assess.py','method':'llm','prompt':'assess','output_contract':{}}]
    (root/'rubrics.json').write_text(json.dumps(spec))
    call_file='evidence/actual/tool_calls.jsonl' if bad_call_file else 'model_calls/assess.py'
    (root/'main.py').write_text('def run(context):\n    return context.sdk.call_llm("assess",call_file='+repr(call_file)+')\n')
    signature='*, call_file' if explicit_parameter else '**options'
    test=tmp_path/'self_test.py'
    test.write_text('from types import SimpleNamespace\nfrom main import run\nclass SDK:\n    def call_llm(self,prompt,'+signature+'): return "ok"\nassert run(SimpleNamespace(sdk=SDK()))=="ok"\n')
    monkeypatch.delitem(sys.modules,'main',raising=False)
    if bad_call_file:
        with pytest.raises(ValueError,match='SDK model-call contract'):
            check_workspace(root,'检查数据','design',test)
    else:
        check_workspace(root,'检查数据','design',test)


def test_implementation_cannot_change_frozen_design(tmp_path, monkeypatch):
    from task_gen.test_verifier.program_verifier.generation import model
    evidence=tmp_path/'evidence';evidence.mkdir();(evidence/'request.json').write_text('{}')
    original=tmp_path/'verifier';original.mkdir()
    (original/'rubrics.json').write_text(json.dumps(design()))
    (original/'main.py').write_text('def run(context):\n    return {}\n')
    executable=Path(shutil.which('python')).resolve()
    monkeypatch.setattr(model.shutil,'which',lambda _:str(executable))
    monkeypatch.setattr(model,'sandbox_command',lambda *args,**kwargs:())
    monkeypatch.setattr(model,'_isolated_auth',lambda *args: {})
    class Client:
        def __init__(self, **kwargs):pass
        def run_until_files(self,prompt,*,working_directory,required_paths):
            (working_directory/'scratch/verifier/rubrics.json').write_text('{}')
            return 'done'
    monkeypatch.setattr(model,'_VerifierClient',Client)
    logs=tmp_path/'generation';logs.mkdir()
    with pytest.raises(ValueError,match='outside its target'):
        GenerationModel(evidence,{},logs)('implement',stage='rubrics/R1.py',output_dir=tmp_path)
    assert json.loads((original/'rubrics.json').read_text())['rubrics'][0]['weight']==1


@pytest.mark.parametrize('broken', [False, True])
def test_completion_checkpoint_written_only_after_success(tmp_path, monkeypatch, broken):
    from task_gen.test_verifier.program_verifier.generation import check
    root=tmp_path/'verifier'; root.mkdir()
    (root/'rubrics.json').write_text(json.dumps(design()))
    (root/'main.py').write_text('def run(context):\n    return {}\n')
    evidence=tmp_path/'evidence'; evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_text':'检查数据'}))
    self_test=tmp_path/'self_test.py'
    self_test.write_text('from main import run\nrun(None)\n'+('assert False\n' if broken else ''))
    marker=tmp_path/'acceptance_ready.json'
    monkeypatch.delitem(check.sys.modules, 'main', raising=False)
    monkeypatch.setattr(check.sys,'argv',['check','--workspace',str(root),'--evidence',str(evidence),
        '--stage','design','--self-test',str(self_test),'--completion-file',str(marker)])
    if broken:
        with pytest.raises(AssertionError): check.main()
        assert not marker.exists()
    else:
        check.main()
        assert json.loads(marker.read_text()) == {'status':'passed','stage':'design'}
