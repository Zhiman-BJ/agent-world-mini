"""File-based design followed by private, one-tool implementation calls."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Callable

from ..program_verifier.generation.model import GenerationModel as BaseGenerationModel, snapshot
from ..program_verifier.generation.pipeline import write_json
from .contracts import DESIGN_FILES, validate_package, validate_tool_file


class GenerationModel(BaseGenerationModel):
    design_files = DESIGN_FILES
    check_module = 'task_gen.test_verifier.codex_tool_verifier.check'


TOOL_CONTEXT = '''
The host calls the declared function as tool(context, inputs) -> dict.
context.root: Path to the read-only evidence bundle.
context.request: request dictionary with evidence paths resolved to absolute paths.
context.task_id, context.task_text: original task identity and exact text.
context.initial_state: Path; context.actual and context.reference: dictionaries of Path values
for final_state, answer, calls, execution where present. Answers are UTF-8 text, calls are JSONL.
context.scratch_dir: writable Path for this call only. Outputs referencing files must state their path base.
The host CLI envelope returns call_id, tool_id, scratch_dir (this call's absolute directory), and result.
For paths relative to context.scratch_dir, the main Codex joins them to the envelope's scratch_dir,
not the main session scratch. Tool results may also return absolute artifact paths.
context.error(code, message): creates an exception; raise it for an unexpected verification error.
No model or SDK is available inside tools. All model judgments belong to the single main Codex.
Inputs and returned objects must satisfy the exact JSON schemas in tools.json; non-finite JSON numbers are invalid.
'''


def generate(evidence_dir: Path, output_dir: Path, config: dict | None = None, *, model_call: Callable | None = None) -> dict:
    evidence_dir, output_dir = Path(evidence_dir).resolve(), Path(output_dir).resolve()
    logs = output_dir/'generation'
    logs.mkdir(parents=True, exist_ok=True)
    root = output_dir/'verifier'
    request_file = evidence_dir/'program_request.json'
    if not request_file.exists():
        request_file = evidence_dir/'request.json'
    request = json.loads(request_file.read_text())
    started, stage = time.monotonic(), 'design'
    custom_model, owns_root = model_call is not None, False
    try:
        if root.exists():
            raise ValueError('Verifier output already exists; use a new run directory, no silent repair')
        root.mkdir()
        owns_root = True
        if model_call is None:
            model_call = GenerationModel(evidence_dir, config or {}, logs)
        prompt_dir = Path(__file__).parent/'prompts'
        context = '\nTool context:\n'+TOOL_CONTEXT+'\nEvidence bundle: '+str(evidence_dir)
        context += '\nRequest (evidence paths are relative to the evidence bundle unless absolute):\n'+json.dumps(request, ensure_ascii=False)
        workspace = Path(model_call((prompt_dir/'design.md').read_text()+context, stage=stage, output_dir=output_dir))
        if set(snapshot(workspace)) != DESIGN_FILES:
            raise ValueError('Design must deliver exactly the four design files')
        plan = validate_package(workspace, request['task_text'], design_only=True)
        for name in DESIGN_FILES:
            shutil.copyfile(workspace/name, root/name)
        frozen = snapshot(root)
        (root/'tools').mkdir()
        (root/'tools/__init__.py').write_text('')
        template = (prompt_dir/'implement_tool.md').read_text()
        for tool in plan['tools']:
            stage = tool['file']
            before = snapshot(root)
            prompt = template+context+'\nTARGET:\n'+json.dumps(tool, ensure_ascii=False)
            prompt += '\nFrozen design:\n'+json.dumps({name: (root/name).read_text() for name in sorted(DESIGN_FILES)}, ensure_ascii=False)
            workspace = Path(model_call(prompt, stage=stage, output_dir=output_dir))
            after = snapshot(workspace)
            if {name: value for name, value in after.items() if name != stage} != before:
                raise ValueError('Implementation changed files outside its target')
            validate_tool_file(workspace, tool)
            shutil.copyfile(workspace/stage, root/stage)
            if any(snapshot(root).get(name) != value for name, value in frozen.items()):
                raise ValueError('Implementation modified frozen design')
        stage = 'package_check'
        validate_package(root, request['task_text'])
        result = {'task_id': request['task_id'], 'status': 'generated', 'verifier_dir': str(root),
                  'rubric_count': len(plan['rubrics']), 'tool_count': len(plan['tools']), 'seconds': time.monotonic()-started}
    except Exception as error:
        result = {'task_id': request['task_id'], 'status': 'generation_failed', 'stage': stage,
                  'error': f'{type(error).__name__}: {error}', 'seconds': time.monotonic()-started}
    if (config or {}).get('final_review_enabled', True) and owns_root and any(root.iterdir()):
        write_json(logs/'staged_result.json', result)
        try:
            from ..final_review import run_final_review
            final = run_final_review('codex_tools', evidence_dir, output_dir, config or {},
                                     model_call=model_call if custom_model else None)
            plan = validate_package(root, request['task_text'])
            result = {'task_id':request['task_id'],'status':'generated','verifier_dir':str(root),
                      'rubric_count':len(plan['rubrics']),'tool_count':len(plan['tools']),'final_review':final}
        except Exception as error:
            result = {'task_id':request['task_id'],'status':'generation_failed','stage':'final_review',
                      'error':f'{type(error).__name__}: {error}'}
        result['seconds'] = time.monotonic()-started
    write_json(logs/'result.json', result)
    return result
