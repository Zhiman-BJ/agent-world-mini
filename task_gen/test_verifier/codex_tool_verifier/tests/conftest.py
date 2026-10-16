import json
from pathlib import Path

import pytest


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value))


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path/'verifier'
    root.mkdir()
    evidence = tmp_path/'source'
    evidence.mkdir()
    for name in ('initial', 'final'):
        (evidence/name).mkdir()
    dump(evidence/'final/data.json', {'value': 3})
    for name in ('environment.json', 'tools.json', 'calls.jsonl', 'execution.json'):
        dump(evidence/name, {})
    (evidence/'answer.txt').write_text('completed')
    request = {'task_id': 'test', 'task_text': 'Save data.', 'environment': 'environment.json',
               'tools': 'tools.json', 'initial_state': 'initial',
               'actual': {'final_state': 'final', 'answer': 'answer.txt', 'calls': 'calls.jsonl', 'execution': 'execution.json'},
               'reference': {'final_state': 'final', 'answer': 'answer.txt'}}
    dump(evidence/'program_request.json', request)
    dump(evidence/'request.json', request)
    dump(evidence/'fallback_input.json', {})
    rubric = {'id': 'R1', 'goal': 'Save data.', 'task_quotes': ['Save data.'], 'weight': 2,
              'scale': 'binary', 'criteria': {'0': 'No valid data', '1': 'Valid data'},
              'method': 'static', 'inputs': {'source': 'actual.final_state'},
              'static_tools': ['T1'], 'required_tools': ['T1'], 'codex_work': 'Judge the returned value.',
              'evidence_plan': 'Read actual data file.', 'missing_policy': 'Missing file scores zero.',
              'error_policy': 'Unreadable structured data is a verifier error.', 'method_reason': 'Structured data is cheap to read.'}
    tool = {'id': 'T1', 'file': 'tools/T1.py', 'function': 'tool', 'purpose': 'Read fixed JSON value.',
            'rubric_ids': ['R1'], 'applicability': 'JSON object with value field.',
            'limits': 'Does not assess meaning.', 'cost_reason': 'One parse instead of repeated reading.',
            'path_policy': 'Absolute path selected by Codex from actual.final_state.',
            'error_policy': 'Missing returns missing; invalid JSON errors.',
            'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'], 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': {'status': {'enum': ['ok', 'missing', 'verification_error']}, 'value': {'type': 'integer'}}, 'required': ['status'], 'additionalProperties': False}}
    dump(root/'rubrics.json', {'rubrics': [rubric]})
    dump(root/'tools.json', {'tools': [tool]})
    dump(root/'coverage.json', {'requirements': [{'task_quote': 'Save data.', 'rubric_ids': ['R1']}]})
    (root/'instructions.md').write_text('Use T1 to read the JSON, then judge R1.')
    (root/'tools').mkdir()
    (root/'tools/T1.py').write_text('import json\nfrom pathlib import Path\ndef tool(context, inputs):\n    path=Path(inputs["path"])\n    if not path.exists(): return {"status":"missing"}\n    return {"status":"ok", "value":json.loads(path.read_text())["value"]}\n')
    return root, evidence


def result(call_ids=(), score=1):
    return {'rubric_id': 'R1', 'score': score, 'reason': 'Value is valid.',
            'evidence': [{'path': 'final/data.json', 'locator': '/value', 'finding': 'value is 3'}],
            'tool_call_ids': list(call_ids)}
