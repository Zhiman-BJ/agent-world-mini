"""Self-test acceptance, also independently replayed by the host."""
import argparse
import json
from pathlib import Path

from ..codex_tool_verifier.check import check_workspace
from .generation import validate_package


def main(*, package_validator=validate_package):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--stage', choices=['design'], required=True)
    parser.add_argument('--self-test', type=Path, required=True)
    parser.add_argument('--completion-file', type=Path)
    args = parser.parse_args()
    if args.completion_file:
        args.completion_file.unlink(missing_ok=True)
    request_file = args.evidence/'program_request.json'
    if not request_file.exists():
        request_file = args.evidence/'request.json'
    request = json.loads(request_file.read_text())
    package_validator(args.workspace, request['task_text'])
    check_workspace(args.workspace, request['task_text'], args.stage, args.self_test)
    package_validator(args.workspace, request['task_text'])
    result = {'status':'passed', 'stage':args.stage}
    if args.completion_file:
        temporary = args.completion_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(result)); temporary.replace(args.completion_file)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
