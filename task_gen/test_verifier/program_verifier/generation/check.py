"""Codex and the host run the same workspace checks in isolated subprocesses."""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import runpy
import sys

from .pipeline import check_file, check_package, file_contracts, write_json


def check_workspace(root: Path, task_text: str, stage: str, self_test: Path) -> None:
    root, self_test = root.resolve(), self_test.resolve()
    design = check_package(root, task_text, design_only=True)
    if stage == 'design':
        target, required = root/'main.py', {'run'}
    else:
        item = next(item for item in file_contracts(design) if item['file'] == stage)
        check_file(root, item)
        target = root/stage
        required = {item['name'], 'parse_output'} if item['kind'] == 'model_call' else {item['name']}
    if not self_test.is_file():
        raise ValueError('Write scratch/self_test.py and exercise the delivered code before finishing')
    observed = set()
    model_contracts = {item['file']:item['method'] for item in design.get('model_calls',[])}
    model_errors = set()
    def profile(frame, event, arg):
        if event == 'call' and frame.f_code.co_filename == str(target):
            observed.add(frame.f_code.co_name)
        caller = frame.f_back
        if (event == 'call' and frame.f_code.co_name in ('call_llm','call_codex') and caller
                and Path(caller.f_code.co_filename).resolve().is_relative_to(root)
                and frame.f_locals.get('self') is not None
                and frame.f_locals['self'] is getattr(caller.f_locals.get('context'),'sdk',None)):
            arguments = inspect.getargvalues(frame)
            keywords = arguments.locals.get(arguments.keywords,{})
            call_file = arguments.locals.get('call_file',keywords.get('call_file'))
            method = frame.f_code.co_name.removeprefix('call_')
            if not isinstance(call_file,str) or model_contracts.get(call_file) != method:
                model_errors.add(f'{method}: {call_file!r}')
    previous_profile, previous_argv = sys.getprofile(), sys.argv
    previous_path = list(sys.path)
    runtime_paths = {Path(p).resolve() for p in previous_path} | {root}
    sys.path.insert(0, str(root))
    sys.argv = [str(self_test)]
    sys.setprofile(profile)
    try:
        runpy.run_path(str(self_test), run_name='__main__')
    except SystemExit as error:
        if error.code not in (None, 0): raise
    finally:
        sys.setprofile(previous_profile)
        sys.argv = previous_argv
        extra_paths = {Path(p).resolve() for p in sys.path} - runtime_paths
        sys.path[:] = previous_path
    if extra_paths:
        raise ValueError(f'Self-test added import paths unavailable at runtime: {sorted(map(str, extra_paths))}; use the runtime environment dependencies, not test-only installs')
    if model_errors:
        raise ValueError(f'Self-test violates SDK model-call contract: {sorted(model_errors)}; call_file must be the declared model_calls/*.py path with matching method, not an evidence file')
    if not required <= observed:
        raise ValueError(f'Self-test must exercise delivered functions: {sorted(required-observed)}')
    allowed = {'main.py', 'rubrics.json', 'README.md'}
    if stage != 'design':
        allowed |= {'model_calls_design.json'} | {item['file'] for item in file_contracts(design)}
        allowed |= {f'{folder}/__init__.py' for folder in ('rubrics', 'helpers', 'model_calls')}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f'Symlink in generated workspace: {relative}')
        if '__pycache__' in relative.parts and path.suffix == '.pyc':
            continue
        if path.is_file() and relative.as_posix() not in allowed:
            raise ValueError(f'Unexpected delivery file: {relative}; move test artifacts outside verifier/ and set the test context.scratch_dir there')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--self-test', type=Path, required=True)
    parser.add_argument('--completion-file', type=Path)
    args = parser.parse_args()
    if args.completion_file:
        args.completion_file.unlink(missing_ok=True)
    request_file = args.evidence/'program_request.json'
    if not request_file.exists(): request_file = args.evidence/'request.json'
    request = json.loads(request_file.read_text())
    check_workspace(args.workspace, request['task_text'], args.stage, args.self_test)
    result = {'status':'passed', 'stage':args.stage}
    if args.completion_file:
        temporary = args.completion_file.with_suffix('.tmp')
        write_json(temporary, result)
        temporary.replace(args.completion_file)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
