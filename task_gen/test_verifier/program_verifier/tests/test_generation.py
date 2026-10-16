import json
from pathlib import Path

import pytest

from task_gen.test_verifier.program_verifier.generation.pipeline import check_package, generate


def design():
    return {'rubrics': [{'id':'R1','goal':'检查数据','task_quotes':['检查数据'],
        'weight':1,'scale':'binary','criteria':{'0':'不满足','1':'满足'},'method':'static',
        'inputs':{}, 'function_contract':{'file':'rubrics/R1.py','name':'check_R1','inputs':{},'returns':{}},
        'missing_policy':'缺失数据为0','error_policy':'无法解析报错','method_reason':'结构明确'}],
        'helpers':[], 'model_calls':[]}


def test_main_is_produced_during_design_and_never_changed_by_implementation(tmp_path):
    calls=[]
    main='from rubrics.R1 import check_R1\ndef run(context):\n    return context.sdk.aggregate([context.sdk.execute(check_R1, context, {}, rubric_id="R1")])\n'
    def model(prompt, *, stage, output_dir):
        calls.append(stage)
        workspace=output_dir/('design_workspace' if stage=='design' else 'implementation_workspace')
        workspace.mkdir()
        if stage == 'design':
            (workspace/'rubrics.json').write_text(json.dumps(design(),ensure_ascii=False))
            (workspace/'main.py').write_text(main)
            return workspace
        assert (output_dir/'verifier/main.py').read_text() == main
        (workspace/'rubrics').mkdir()
        (workspace/stage).write_text('def check_R1(context, inputs):\n    return {"rubric_id":"R1","score":1,"reason":"数据符合要求","evidence":[{"path":"evidence/actual/answer.txt","locator":"all","finding":"数据"}]}\n')
        return workspace
    evidence=tmp_path/'evidence_bundle';evidence.mkdir()
    (evidence/'request.json').write_text(json.dumps({'task_id':'x','task_text':'检查数据'}))
    result=generate(evidence, tmp_path, {'final_review_enabled':False}, model_call=model)
    assert result['status']=='generated'
    assert calls==['design','rubrics/R1.py']
    assert (tmp_path/'verifier/main.py').read_text()==main


def test_generation_failure_is_recorded_without_retry(tmp_path):
    calls=[]
    def bad(prompt, *, stage, output_dir):
        calls.append(stage)
        return 'not json'
    e=tmp_path/'e';e.mkdir();(e/'request.json').write_text(json.dumps({'task_id':'x','task_text':'x'}))
    result=generate(e,tmp_path,{'final_review_enabled':False},model_call=bad)
    assert result['status']=='generation_failed'
    assert calls==['design']
    assert (tmp_path/'generation/result.json').exists()


def test_rejects_missing_rubric_implementation_and_program_import(tmp_path):
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    return check_R1(context, {})\n')
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    with pytest.raises(ValueError):
        check_package(tmp_path,'检查数据')
    (tmp_path/'rubrics').mkdir()
    (tmp_path/'rubrics/R1.py').write_text('from task_gen.program import x\ndef check_R1(context, inputs):\n    pass\n')
    with pytest.raises(ValueError,match='task_gen.program'):
        check_package(tmp_path,'检查数据')


def test_planning_does_not_claim_runtime_call_coverage(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    return {}\n')
    check_package(tmp_path,'检查数据',design_only=True)


def test_dispatch_through_module_level_helper_is_not_rejected(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef dispatch(context):\n    return context.sdk.execute(check_R1, context, {}, rubric_id="R1")\ndef run(context):\n    return context.sdk.aggregate([dispatch(context)])\n')
    check_package(tmp_path,'检查数据',design_only=True)


def test_main_entry_signature_is_checked(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context, extra):\n    return context.sdk.execute(check_R1,context,{})\n')
    with pytest.raises(ValueError,match='signature'):
        check_package(tmp_path,'检查数据',design_only=True)


@pytest.mark.parametrize('bad_signature', ['context, inputs, *, extra', 'context, inputs, *args', 'context, inputs, **kwargs', 'context, inputs, /'])
def test_child_function_rejects_unsupported_signature(tmp_path, bad_signature):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    return check_R1(context, {})\n')
    (tmp_path/'rubrics').mkdir()
    (tmp_path/'rubrics/R1.py').write_text(f'def check_R1({bad_signature}):\n    return {{}}\n')
    with pytest.raises(ValueError, match='signature'):
        check_package(tmp_path)


def test_initializers_are_compiled(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1\ndef run(context):\n    return check_R1(context, {})\n')
    (tmp_path/'rubrics').mkdir()
    (tmp_path/'rubrics/R1.py').write_text('def check_R1(context, inputs):\n    return {}\n')
    (tmp_path/'rubrics/__init__.py').write_text('def invalid(')
    with pytest.raises(SyntaxError):
        check_package(tmp_path)


def test_explicit_dynamic_module_dispatch_is_valid_planning_coverage(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('import importlib\ndef run(context):\n    specs=[("R1", "rubrics.R1")]\n    return context.sdk.aggregate([context.sdk.execute(getattr(importlib.import_module(module), "check_"+rid), context, {}, rubric_id=rid) for rid,module in specs])\n')
    check_package(tmp_path, design_only=True)


def test_failed_generation_logs_inherited_model_and_usage(tmp_path, monkeypatch):
    from task_gen.test_verifier.program_verifier.generation import model
    evidence=tmp_path/'evidence';evidence.mkdir();(evidence/'request.json').write_text('{}')
    executable=tmp_path/'bin/codex';executable.parent.mkdir();executable.write_text('')
    monkeypatch.setattr(model.shutil, 'which', lambda _:str(executable))
    monkeypatch.setattr(model, 'sandbox_command', lambda *args,**kwargs:())
    def auth(config, directory):
        (directory/'config.toml').write_text('model="inherited-model"\n')
        return {}
    monkeypatch.setattr(model,'_isolated_auth',auth)
    class Client:
        def __init__(self, **kwargs):self.logs=kwargs['log_directory']
        def run_until_files(self,*args,**kwargs):
            self.logs.mkdir(parents=True)
            (self.logs/'stdout.log').write_text('{"usage":{"input_tokens":17}}\n')
            raise RuntimeError('provider failed')
    monkeypatch.setattr(model,'_VerifierClient',Client)
    (tmp_path/'logs').mkdir()
    generator=model.GenerationModel(evidence,{},tmp_path/'logs')
    with pytest.raises(RuntimeError,match='provider failed'):
        generator('prompt',stage='design',output_dir=tmp_path)
    row=json.loads(next((tmp_path/'logs').glob('*/call.json')).read_text())
    assert row['model']=='inherited-model' and row['usage']==[{'input_tokens':17}]
    assert row['status']=='error'


def test_higher_order_aliased_rubric_reference_is_supported(tmp_path):
    (tmp_path/'rubrics.json').write_text(json.dumps(design()))
    (tmp_path/'main.py').write_text('from rubrics.R1 import check_R1 as check\ndef run(context):\n    checks=[check]\n    return context.sdk.aggregate([context.sdk.execute(fn,context,{}) for fn in checks])\n')
    check_package(tmp_path,design_only=True)
