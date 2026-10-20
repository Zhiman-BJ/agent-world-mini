"""Each invocation designs fresh fixed criteria from the original task."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time

from ..codex_tool_verifier.contracts import DESIGN_FILES, OPTIONAL_FILES, require, validate_package as validate_design
from ..program_verifier.generation.model import GenerationModel as BaseGenerationModel, snapshot
from ..program_verifier.generation.pipeline import write_json


class GenerationModel(BaseGenerationModel):
    design_files = DESIGN_FILES
    optional_design_files = OPTIONAL_FILES
    check_module = 'task_gen.test_verifier.codex_rubric_verifier.check'
    isolate_experiments = True


def validate_package(root, task_text=None):
    plan = validate_design(root, task_text)
    require(not plan['tools'], 'Rubric-only generation cannot declare static tools')
    require(all(r['method'] == 'codex' and not r['static_tools'] and not r.get('required_tools')
                for r in plan['rubrics']), 'Each rubric must use codex without generated tools')
    require(DESIGN_FILES <= set(snapshot(Path(root))) <= DESIGN_FILES | OPTIONAL_FILES,
            'Deliver four rubric design files and optional README.md')
    return plan


def generate_for(method, evidence_dir, output_dir, config=None, *, model_call=None):
    package_validator = validate_package
    if method == 'direct_codex_rubric':
        from ..direct_codex_rubric.contracts import validate_package as package_validator
    evidence, output = Path(evidence_dir).resolve(), Path(output_dir).resolve()
    logs = output/'generation'; logs.mkdir(parents=True, exist_ok=True)
    root = output/'verifier'
    request_file = evidence/'program_request.json'
    if not request_file.exists():
        request_file = evidence/'request.json'
    request = json.loads(request_file.read_text())
    started = time.monotonic()
    custom_model, owns_root = model_call is not None, False
    result = dict(task_id=request['task_id'], method=method, generation_source='original_task',
                  task_sha256=hashlib.sha256(request['task_text'].encode()).hexdigest())
    try:
        require(not root.exists(), 'Verifier output already exists; use a new run directory, no repair or reuse')
        root.mkdir()
        owns_root = True
        prompt = (Path(__file__).parent/'prompts/design.md').read_text()
        prompt += '\n本次应用方式：\n'+(Path(__file__).parent.parent/method/'prompts/application_contract.md').read_text()
        prompt += '\nEvidence bundle: '+str(evidence)+'\nOriginal request:\n'+json.dumps(request, ensure_ascii=False)
        call = model_call or GenerationModel(evidence, config or {}, logs)
        if not custom_model and method == 'direct_codex_rubric':
            call.check_module = 'task_gen.test_verifier.direct_codex_rubric.check'
        workspace = Path(call(prompt, stage='design', output_dir=output))
        plan = package_validator(workspace, request['task_text'])
        for name in DESIGN_FILES | (OPTIONAL_FILES & set(snapshot(workspace))):
            shutil.copyfile(workspace/name, root/name)
        package_validator(root, request['task_text'])
        result.update(status='generated', verifier_dir=str(root), rubric_count=len(plan['rubrics']), tool_count=0)
    except Exception as error:
        result.update(status='generation_failed', stage='design', error=f'{type(error).__name__}: {error}')
    if (config or {}).get('final_review_enabled', True) and owns_root:
        write_json(logs/'staged_result.json', result)
        try:
            from ..final_review import run_final_review
            final = run_final_review(method, evidence, output, config or {},
                                     model_call=model_call if custom_model else None)
            plan = package_validator(root, request['task_text'])
            result.pop('stage', None)
            result.pop('error', None)
            result.update(status='generated', verifier_dir=str(root), rubric_count=len(plan['rubrics']),
                          tool_count=0, final_review=final)
        except Exception as error:
            result.update(status='generation_failed', stage='final_review', error=f'{type(error).__name__}: {error}')
    if result['status'] == 'generated':
        write_json(output/'frozen_manifest.json', snapshot(root))
    result['seconds'] = time.monotonic()-started
    # An accidental second call cannot replace the provenance of an existing generation.
    if not (logs/'result.json').exists():
        write_json(logs/'result.json', result)
    return result


def generate(evidence_dir, output_dir, config=None, *, model_call=None):
    return generate_for('codex_rubric_verifier', evidence_dir, output_dir, config, model_call=model_call)
