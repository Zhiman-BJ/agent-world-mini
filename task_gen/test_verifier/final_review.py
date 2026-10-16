"""One Codex repairs the whole generated verifier, then the host tests it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import shutil
import sys
import time
import uuid

from .program_verifier.generation.model import GenerationModel, snapshot
from .program_verifier.generation.pipeline import check_package, write_json


def package_check(method, root, task_text):
    if method == 'program':
        return check_package(root, task_text)
    if method == 'codex_tools':
        from .codex_tool_verifier.contracts import validate_package
        return validate_package(root, task_text)
    raise ValueError(f'Unknown verifier method: {method}')


def check_workspace(method, root, evidence, self_test, config, native_output):
    root, evidence, self_test = map(lambda p: Path(p).resolve(), (root, evidence, self_test))
    request_file = evidence/'program_request.json'
    if not request_file.exists():
        request_file = evidence/'request.json'
    request = json.loads(request_file.read_text())
    package_check(method, root, request['task_text'])
    if not self_test.is_file() or self_test.is_relative_to(root):
        raise ValueError('Write and run scratch/self_test.py outside verifier/')
    before = snapshot(root)
    previous_path, previous_argv = list(sys.path), sys.argv
    sys.path.insert(0, str(root))
    sys.argv = [str(self_test)]
    try:
        try:
            runpy.run_path(str(self_test), run_name='__main__')
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
    finally:
        sys.path[:], sys.argv = previous_path, previous_argv
    if snapshot(root) != before:
        raise ValueError('Self-test changed delivered verifier files')
    native_config = dict(config, fallback_enabled=False)
    if method == 'program':
        from .program_verifier.application.runner import apply
        expected = 'program'
    else:
        from .codex_tool_verifier.application import apply
        expected = 'codex_with_tools'
        native_config['static_tools_enabled'] = True
    native_output = Path(native_output)
    native_output.parent.mkdir(parents=True, exist_ok=True)
    result = apply(root, evidence, native_output, native_config)
    write_json(native_output.with_name(native_output.name+'_result.json'), result)
    if snapshot(root) != before:
        raise ValueError('Native test changed delivered verifier files')
    if result.get('mode') != expected or result.get('status') != 'completed':
        raise ValueError('Native verifier test failed: '+json.dumps(result, ensure_ascii=False))
    # A valid zero is a completed evaluation, not a reason to relax criteria.
    return result


class FinalReviewModel(GenerationModel):
    check_module = 'task_gen.test_verifier.final_review'

    def __init__(self, evidence_dir, config, logs, method):
        super().__init__(evidence_dir, config, logs)
        self.final_method = method


def run_final_review(method, evidence_dir, output_dir, config, *, model_call=None):
    evidence_dir, output_dir = Path(evidence_dir).resolve(), Path(output_dir).resolve()
    root, logs = output_dir/'verifier', output_dir/'generation'
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    before = snapshot(root)
    shutil.copytree(root, logs/'before_final')
    record = {'status':'running','method':method,'before':before}
    try:
        request_file = evidence_dir/'program_request.json'
        if not request_file.exists():
            request_file = evidence_dir/'request.json'
        request = json.loads(request_file.read_text())
        prompt = Path(__file__).with_name('final_review_prompt.md').read_text()
        prompt += '\nMethod: '+method+'\nEvidence bundle: '+str(evidence_dir)
        prompt += '\nTask request:\n'+json.dumps(request, ensure_ascii=False)
        prompt += '\n生成阶段问题和日志（按需读取）：'+str(logs)
        if model_call is None:
            model_call = FinalReviewModel(evidence_dir, config, logs, method)
        workspace = Path(model_call(prompt, stage='final', output_dir=output_dir))
        package_check(method, workspace, request['task_text'])
        after = snapshot(workspace)
        shutil.rmtree(root)
        shutil.copytree(workspace, root)
        record.update(status='completed', after=after,
                      changed_files=sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k)))
        write_json(logs/'manifest.json', {'method':method,'files':after})
        return record
    except Exception as error:
        record.update(status='error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        record['seconds'] = time.monotonic()-started
        write_json(logs/'final_result.json', record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--self-test', type=Path, required=True)
    parser.add_argument('--method', choices=['program','codex_tools'], required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--stage', choices=['final'], required=True)
    parser.add_argument('--completion-file', type=Path)
    args = parser.parse_args()
    if args.completion_file:
        args.completion_file.unlink(missing_ok=True)
    config = json.loads(args.config.read_text())
    native = args.self_test.parent/'native_runs'/uuid.uuid4().hex
    result = check_workspace(args.method, args.workspace, args.evidence, args.self_test, config, native)
    if args.completion_file:
        write_json(args.completion_file, {'status':'passed','score':result['score']})
    print(json.dumps({'status':'passed','native_run':str(native),'score':result['score']}))


if __name__ == '__main__':
    main()
