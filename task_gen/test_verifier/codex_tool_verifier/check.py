"""Checks shared by the generation Codex and the host's independent replay."""
from __future__ import annotations

import argparse
import dis
import json
from pathlib import Path
import runpy
import sys
import tempfile

from jsonschema import Draft202012Validator

from ..program_verifier.generation.model import snapshot
from ..program_verifier.application.runner import _absolute_request
from .contracts import DESIGN_FILES, OPTIONAL_FILES, validate_package, validate_tool_file
from .tool_runner import make_context


def check_workspace(root: Path, task_text: str, stage: str, self_test: Path, *, evidence_root: Path | None = None) -> None:
    root, self_test = Path(root).resolve(), Path(self_test).resolve()
    evidence_root = Path(evidence_root).resolve() if evidence_root is not None else None
    plan = validate_package(root, task_text, design_only=True)
    tool = None
    if stage == 'design':
        if not DESIGN_FILES <= set(snapshot(root)) <= DESIGN_FILES | OPTIONAL_FILES:
            raise ValueError('Design must contain the four design files and optional README.md')
    else:
        tool = next((item for item in plan['tools'] if item['file'] == stage), None)
        if tool is None:
            raise ValueError(f'Unknown implementation target: {stage}')
        validate_tool_file(root, tool)
    if self_test.is_relative_to(root) or not self_test.is_file():
        raise ValueError('Write scratch/self_test.py outside the delivery package and run it before finishing')
    before = snapshot(root)
    outputs = []
    real_outputs = []
    real_context = None
    real_failed = False
    def profile(frame, event, arg):
        nonlocal real_failed
        if tool is not None and frame.f_code.co_name == tool['function'] and Path(frame.f_code.co_filename).resolve() == root/tool['file']:
            context = frame.f_locals.get(frame.f_code.co_varnames[0])
            is_real = real_context is not None and context is real_context
            if event == 'call' and is_real:
                inputs = frame.f_locals.get(frame.f_code.co_varnames[1])
                try:
                    json.dumps(inputs, allow_nan=False)
                    Draft202012Validator(tool['input_schema']).validate(inputs)
                except Exception:
                    real_failed = True
                    raise
            # A profile "return" also reports exception exits with arg=None.
            if event == 'return' and dis.opname[frame.f_code.co_code[frame.f_lasti]].startswith('RETURN_'):
                outputs.append(arg)
                if is_real:
                    real_outputs.append(arg)
            elif event == 'return' and is_real:
                real_failed = True
    previous_profile, previous_argv = sys.getprofile(), sys.argv
    previous_bytecode = sys.dont_write_bytecode
    # Each workspace owns the tools package, including when checks share a Python process.
    previous_tools = {name: module for name, module in sys.modules.items() if name == 'tools' or name.startswith('tools.')}
    for name in previous_tools:
        del sys.modules[name]
    sys.path.insert(0, str(root))
    sys.argv = [str(self_test)]
    sys.dont_write_bytecode = True
    sys.setprofile(profile)
    try:
        namespace = {}
        try:
            namespace = runpy.run_path(str(self_test), run_name='__main__')
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        if tool is not None and evidence_root is not None:
            normal_check = namespace.get('check_real_evidence')
            if not callable(normal_check):
                raise ValueError('For real evidence, define check_real_evidence(context) in self_test.py; the host supplies the actual context')
            request_file = evidence_root/'program_request.json'
            if not request_file.exists():
                request_file = evidence_root/'request.json'
            request = _absolute_request(json.loads(request_file.read_text()), evidence_root)
            with tempfile.TemporaryDirectory(prefix='check-real-', dir=self_test.parent) as scratch:
                real_context = make_context(request, evidence_root, scratch)
                normal_check(real_context)
    finally:
        sys.setprofile(previous_profile)
        sys.argv = previous_argv
        sys.dont_write_bytecode = previous_bytecode
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if name == 'tools' or name.startswith('tools.'):
                del sys.modules[name]
        sys.modules.update(previous_tools)
    if snapshot(root) != before:
        raise ValueError('Self-test changed delivered files')
    if tool is not None:
        if not outputs:
            raise ValueError('Self-test must exercise the delivered tool function')
        for output in outputs:
            try:
                json.dumps(output, allow_nan=False)
                Draft202012Validator(tool['output_schema']).validate(output)
            except Exception as error:
                raise ValueError(f'Tool output schema: {error}') from error
        if evidence_root is not None and (real_failed or not real_outputs or any(output['status'] == 'verification_error' for output in real_outputs)):
            raise ValueError('check_real_evidence must call the tool with the host context and normal results; verification_error on real evidence is not successful generation')


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
    if not request_file.exists():
        request_file = args.evidence/'request.json'
    request = json.loads(request_file.read_text())
    check_workspace(args.workspace, request['task_text'], args.stage, args.self_test, evidence_root=args.evidence)
    result = {'status': 'passed', 'stage': args.stage}
    if args.completion_file:
        temporary = args.completion_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(args.completion_file)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
