"""Two-stage generation; design/main are frozen before per-file implementation."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil
from pathlib import Path
import time
from typing import Callable

from ..contracts import validate_design

SDK_CONTRACT = '''
context.root: Path to evidence bundle; context.request: resolved program_request.json dict (not the legacy request.json used by fallback); context.task_id,task_text.
context.initial_state: Path; context.actual/reference: dictionaries with Path keys final_state, answer, calls, execution (reference execution optional).
context.scratch_dir: writable Path. raise context.error(code,message,rubric_id=None) to report a VerificationError; error(...) constructs the exception.
Evidence indices environment/tools resolve relative to context.root. Read answer via context.actual['answer'].read_text(); calls are JSONL.
context.sdk.execute(fn,context,inputs,rubric_id=None): runs/logs one scoring/helper function, propagates errors. Calls must be wrapped this way.
Declared model_calls/*.py build_prompt(context,inputs) may also run through execute, returning prompt text without scoring; omit rubric_id for build_prompt/helpers because execute rubric_id identifies a scoring function, not a logging association. call_llm/call_codex may carry the related rubric_id. parse_output(raw) is called directly with the model response.
context.sdk.parallel([lambda: ..., lambda: ...]): bounded parallel, returns ordered results, error stops queued calls.
context.sdk.call_llm(prompt,*,call_file,function_name=None,rubric_id=None)->str: ordinary model textual response.
context.sdk.call_codex(prompt,*,call_file,function_name=None,rubric_id=None)->str: model can investigate evidence with tools; prompt include relevant paths; scratch writable, evidence readonly.
call_file is the exact declared model-call implementation path, e.g. "model_calls/assess.py"; it is NOT the candidate's tool_calls.jsonl or another evidence path. Evidence is supplied through the built prompt. function_name is a log label.
context.sdk.aggregate(results)->{'score':0..1,'rubrics':[results]}: fixed weighted aggregation, must receive every rubric exactly once; return this from run(context).
Rubric result: {'rubric_id':'R1','score':0 or 1 / integer0..5,'reason':'nonempty','evidence':[{'path':'evidence/...','locator':'...','finding':'...'}], optional 'data':...}.
Evidence path is absolute under context.root/context.scratch_dir, or relative to context.root; scratch/... resolves under context.scratch_dir. It must exist, and may be a FILE OR DIRECTORY. A source-document nickname is not a resolvable evidence path.
context.resolve_evidence_path(path)->Path: resolve, check containment and existence for file OR directory evidence. Use this for evidence references; do not add is_file() restrictions. Only task artifacts explicitly designed as files require is_file(). A directory can substantiate a tree comparison or missing artifact search.
Helpers return their designed data, never aggregated. Inputs explicitly passed by main, no depends_on graph. imports are ordinary absolute package imports: from rubrics.R1 import check_R1 / from helpers.locate import locate / from model_calls.assess import build_prompt,parse_output.
No generated file imports task_gen.test_verifier.program_verifier or task_gen.program.
'''


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def source_check(code: str, filename: str) -> ast.Module:
    tree = ast.parse(code, filename=filename)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        if any(n == 'task_gen.program' or n.startswith('task_gen.program.') for n in names):
            raise ValueError(f'Forbidden task_gen.program import in {filename}')
        if any(n.startswith('task_gen.test_verifier.program_verifier') for n in names):
            raise ValueError(f'Generated file imports host internals: {filename}')
    compile(tree, filename, 'exec')
    return tree


def file_contracts(design: dict) -> list[dict]:
    items=[]
    for r in design['rubrics']:
        c=dict(r['function_contract']);c.update(kind='rubric',design=r)
        items.append(c)
    for h in design.get('helpers', []):
        items.append(dict(h,kind='helper',design=h))
    for m in design.get('model_calls', []):
        items.append(dict(m,kind='model_call',name='build_prompt',design=m))
    seen=set()
    for item in items:
        f=item['file'];p=Path(f)
        folder={'rubric':'rubrics','helper':'helpers','model_call':'model_calls'}[item['kind']]
        if p.is_absolute() or len(p.parts)!=2 or p.parts[0]!=folder or p.suffix!='.py' or p.stem=='__init__' or not p.stem.isidentifier():
            raise ValueError(f'Invalid generated path: {f}')
        if f in seen:
            raise ValueError(f'Duplicate generated file: {f}')
        if not isinstance(item.get('name'),str) or not item['name'].isidentifier():
            raise ValueError(f'Invalid function contract: {f}')
        seen.add(f)
    return items


def check_package(root: Path, task_text: str | None = None, *, design_only: bool = False) -> dict:
    design=validate_design(json.loads((root/'rubrics.json').read_text()),task_text)
    items=file_contracts(design)
    main_tree=source_check((root/'main.py').read_text(),'main.py')
    entry=next((n for n in main_tree.body if isinstance(n,ast.FunctionDef) and n.name=='run'),None)
    if entry is None: raise ValueError('main.py must define run(context)')
    if [a.arg for a in entry.args.args]!=['context'] or entry.args.vararg or entry.args.kwarg or entry.args.kwonlyargs:
        raise ValueError('Wrong run signature; expected run(context)')
    # Actual rubric execution coverage is checked by the application host, not inferred from Python syntax.
    if design_only:
        return design
    allowed={'main.py','rubrics.json','model_calls_design.json'}|{i['file'] for i in items}|{f'{d}/__init__.py' for d in ['rubrics','helpers','model_calls']}
    for p in root.rglob('*'):
        if p.is_symlink(): raise ValueError(f'Symlink in generated package: {p}')
        if p.is_file() and str(p.relative_to(root)) not in allowed:
            raise ValueError(f'Unexpected generated file: {p}')
        if p.is_file() and p.suffix == '.py':
            source_check(p.read_text(), str(p.relative_to(root)))
    for item in items:
        check_file(root, item)
    return design


def check_file(root: Path, item: dict) -> None:
    p=root/item['file']
    if not p.is_file(): raise ValueError(f'Missing implementation: {item["file"]}')
    tree=source_check(p.read_text(),item['file'])
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    if item['name'] not in functions:
        raise ValueError(f'Missing function {item["name"]} in {p}')
    for name in ([item['name'],'parse_output'] if item['kind']=='model_call' else [item['name']]):
        if name not in functions: raise ValueError(f'Missing {name} in {p}')
        args=[a.arg for a in functions[name].args.args]
        expected=['raw'] if name=='parse_output' else ['context','inputs']
        signature=functions[name].args
        if args!=expected or signature.posonlyargs or signature.kwonlyargs or signature.vararg or signature.kwarg:
            raise ValueError(f'Wrong signature {name}: {args}, expected {expected}')


def generate(evidence_dir: Path, output_dir: Path, config: dict | None = None, *, model_call: Callable | None = None) -> dict:
    evidence_dir=evidence_dir.resolve();output_dir=output_dir.resolve()
    logs=output_dir/'generation';logs.mkdir(parents=True,exist_ok=True)
    root=output_dir/'verifier'
    request_file=evidence_dir/'program_request.json'
    if not request_file.exists(): request_file=evidence_dir/'request.json'
    request=json.loads(request_file.read_text())
    started=time.monotonic();stage='design'
    custom_model, owns_root = model_call is not None, False
    try:
        if root.exists(): raise ValueError('Verifier output already exists; use a new run directory, no silent repair')
        root.mkdir()
        owns_root = True
        if model_call is None:
            from .model import GenerationModel
            model_call=GenerationModel(evidence_dir,config or {},logs)
        prompt=(Path(__file__).parent/'prompts/design.md').read_text()+ '\nSDK\n'+SDK_CONTRACT
        prompt+='\nEvidence bundle: '+str(evidence_dir)+'\nRequest:\n'+json.dumps(request,ensure_ascii=False)
        workspace=Path(model_call(prompt,stage=stage,output_dir=output_dir))
        design=validate_design(json.loads((workspace/'rubrics.json').read_text()),request['task_text'])
        write_json(root/'rubrics.json',design)
        shutil.copyfile(workspace/'main.py',root/'main.py')
        write_json(root/'model_calls_design.json',design.get('model_calls',[]))
        check_package(root,request['task_text'],design_only=True)
        frozen=hashlib.sha256((root/'main.py').read_bytes()).hexdigest()
        items=file_contracts(design)
        for folder in ['rubrics','helpers','model_calls']:
            (root/folder).mkdir();(root/folder/'__init__.py').write_text('')
        template=(Path(__file__).parent/'prompts/implement.md').read_text()
        def implement(item):
            target=item['file']
            prompt=template+'\nSDK\n'+SDK_CONTRACT+'\nEvidence: '+str(evidence_dir)
            prompt+='\nRequest:\n'+json.dumps(request,ensure_ascii=False)+'\nTARGET:\n'+json.dumps(item,ensure_ascii=False)
            prompt+='\n完整冻结设计在工作目录 rubrics.json、model_calls_design.json。只按需要读取目标契约和相关依赖；不要重复输出整份设计。\nFrozen main.py:\n'+(root/'main.py').read_text()
            workspace=Path(model_call(prompt,stage=target,output_dir=output_dir))
            check_file(workspace,item)
            shutil.copyfile(workspace/target,root/target)
            if hashlib.sha256((root/'main.py').read_bytes()).hexdigest()!=frozen:
                raise ValueError('Implementation modified frozen main.py')
        stage='implementation'
        workers=int((config or {}).get('implementation_concurrency',2))
        if workers < 1: raise ValueError('implementation_concurrency must be positive')
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for kind in ('model_call','helper','rubric'):
                list(executor.map(implement,[item for item in items if item['kind']==kind]))
        stage='package_check';check_package(root,request['task_text'])
        result={'task_id':request['task_id'],'status':'generated','verifier_dir':str(root),'seconds':time.monotonic()-started,'rubric_count':len(design['rubrics'])}
    except Exception as e:
        result={'task_id':request['task_id'],'status':'generation_failed','stage':stage,'error':f'{type(e).__name__}: {e}','seconds':time.monotonic()-started}
    if (config or {}).get('final_review_enabled', True) and owns_root and any(root.iterdir()):
        write_json(logs/'staged_result.json', result)
        try:
            from ...final_review import run_final_review
            final = run_final_review('program', evidence_dir, output_dir, config or {},
                                     model_call=model_call if custom_model else None)
            design = check_package(root, request['task_text'])
            result = {'task_id':request['task_id'],'status':'generated','verifier_dir':str(root),
                      'rubric_count':len(design['rubrics']),'final_review':final}
        except Exception as error:
            result = {'task_id':request['task_id'],'status':'generation_failed','stage':'final_review',
                      'error':f'{type(error).__name__}: {error}'}
        result['seconds'] = time.monotonic()-started
    write_json(logs/'result.json',result)
    return result
