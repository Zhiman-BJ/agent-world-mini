"""Fixed contracts shared by generation and the trusted runtime."""
from __future__ import annotations

import math
from pathlib import Path, PurePosixPath


class RubricVerificationError(RuntimeError):
    def __init__(self, code, message, rubric_id=None):
        super().__init__(message)
        self.code, self.rubric_id = code, rubric_id


def _require(condition, message):
    if not condition:
        raise RubricVerificationError('invalid_contract', message)


def validate_design(design, task_text=None):
    _require(isinstance(design, dict), 'design must be an object')
    rubrics = design.get('rubrics')
    _require(isinstance(rubrics, list) and bool(rubrics), 'rubrics must be nonempty')
    ids, files = set(), set()
    for rubric in rubrics:
        _require(isinstance(rubric, dict), 'rubric must be an object')
        rid = rubric.get('id')
        _require(isinstance(rid, str) and bool(rid) and rid not in ids, 'invalid/duplicate rubric id')
        ids.add(rid)
        weight = rubric.get('weight')
        _require(type(weight) in (int, float) and math.isfinite(weight) and weight > 0, 'weight must be positive finite')
        _require(rubric.get('scale') in ('binary', 'ordinal_5'), 'unknown scale')
        _require(rubric.get('method') in ('static', 'llm', 'codex'), 'unknown method')
        for name in ('goal', 'criteria', 'inputs', 'missing_policy', 'error_policy', 'method_reason'):
            _require(name in rubric and (name == 'inputs' or bool(rubric[name])), f'{rid}: missing {name}')
        criteria = rubric['criteria']
        levels = range(2 if rubric['scale'] == 'binary' else 6)
        _require(isinstance(criteria, dict) and all(str(i) in criteria and bool(criteria[str(i)]) for i in levels), f'{rid}: criteria must describe each score level')
        quotes = rubric.get('task_quotes')
        _require(isinstance(quotes, list) and bool(quotes) and all(isinstance(q, str) and q.strip() for q in quotes), 'task_quotes must be nonempty strings')
        if task_text is not None:
            _require(all(q in task_text for q in quotes), f'{rid}: quote is not in task text')
        _require(not any(k in rubric for k in ('depends_on', 'dependencies')), 'rubric must not declare scheduling dependencies')
        contract = rubric.get('function_contract', {})
        _require(isinstance(contract, dict), 'function_contract must be an object')
        filename = contract.get('file', '')
        _require(isinstance(filename, str), 'filename must be a string')
        path = PurePosixPath(filename)
        _require(not path.is_absolute() and '..' not in path.parts and len(path.parts) == 2 and path.parts[0] == 'rubrics' and path.suffix == '.py' and path.stem != '__init__' and path.stem.isidentifier() and filename not in files, 'each rubric needs its own rubrics/*.py file')
        files.add(filename)
        _require(isinstance(contract.get('name'), str) and contract['name'].isidentifier(), 'invalid function name')
        _require(isinstance(contract.get('inputs'), dict) and isinstance(contract.get('returns'), dict), 'function inputs/returns must be objects')
    for group, folder in (('helpers', 'helpers'), ('model_calls', 'model_calls')):
        items = design.get(group, [])
        _require(isinstance(items, list), f'{group} must be a list')
        for item in items:
            _require(isinstance(item, dict), f'{group} entry must be an object')
            filename = item.get('file', '')
            _require(isinstance(filename, str), 'filename must be a string')
            path = PurePosixPath(filename)
            _require(not path.is_absolute() and '..' not in path.parts and len(path.parts) == 2 and path.parts[0] == folder and path.suffix == '.py' and path.stem != '__init__' and path.stem.isidentifier() and filename not in files, f'invalid/duplicate {group} file')
            files.add(filename)
            if group == 'helpers':
                _require(isinstance(item.get('name'), str) and item['name'].isidentifier() and isinstance(item.get('inputs'), dict) and isinstance(item.get('returns'), dict), 'invalid helper function contract')
            else:
                _require(item.get('method') in ('llm', 'codex') and isinstance(item.get('prompt'), str) and bool(item['prompt'].strip()) and isinstance(item.get('output_contract'), dict), 'invalid model call design')
                if 'response_format' in item:
                    _require(item['method'] == 'llm' and item['response_format'] == 'json_object', 'response_format must be json_object for an llm call')
    return design


def resolve_evidence_path(value, root, scratch_dir):
    root, scratch_dir = Path(root).resolve(), Path(scratch_dir).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = scratch_dir.joinpath(*path.parts[1:]) if path.parts and path.parts[0] == 'scratch' else root/path
    path = path.resolve()
    _require((path.is_relative_to(root) or path.is_relative_to(scratch_dir)) and path.exists(), f'evidence path missing or outside readonly evidence/scratch: {value}')
    return path


def validate_evidence(results, root, scratch_dir):
    for result in results:
        for evidence in result['evidence']:
            resolve_evidence_path(evidence['path'], root, scratch_dir)


def aggregate_results(rubrics, results):
    _require(isinstance(results, list), 'results must be a list')
    expected = {r['id']: r for r in rubrics}
    _require(len(expected) == len(rubrics) and len(results) == len(expected), 'missing/duplicate rubric result')
    seen, total, weights = set(), 0.0, 0.0
    for result in results:
        _require(isinstance(result, dict), 'rubric result must be an object')
        rid = result.get('rubric_id')
        _require(isinstance(rid, str) and rid in expected and rid not in seen, 'unknown/duplicate rubric result')
        seen.add(rid)
        rubric, score = expected[rid], result.get('score')
        maximum = 1 if rubric['scale'] == 'binary' else 5
        _require(type(score) is int and 0 <= score <= maximum, f'{rid}: score must be an integer in 0..{maximum}')
        _require(isinstance(result.get('reason'), str) and bool(result['reason'].strip()), f'{rid}: reason missing')
        refs = result.get('evidence')
        _require(isinstance(refs, list) and bool(refs), f'{rid}: evidence must be a nonempty list')
        for ref in refs:
            _require(isinstance(ref, dict) and all(isinstance(ref.get(k), str) and bool(ref[k].strip()) for k in ('path', 'locator', 'finding')), f'{rid}: malformed evidence')
        weight = rubric['weight']
        _require(type(weight) in (int, float) and math.isfinite(weight) and weight > 0, 'invalid weight')
        total += weight * (score / maximum)
        weights += weight
    _require(bool(weights) and math.isfinite(total) and math.isfinite(weights), 'invalid aggregate')
    return {'score': total / weights, 'rubrics': results}
