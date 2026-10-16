"""Host-owned package, schema, scoring and tool-reference checks."""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

from ..program_verifier.contracts import aggregate_results, validate_evidence
from ..program_verifier.generation.pipeline import source_check

DESIGN_FILES = {'rubrics.json', 'tools.json', 'instructions.md', 'coverage.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_schema(schema):
    require(isinstance(schema, dict) and schema.get('type') == 'object', 'schema must describe an object')
    def check_refs(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ('$ref', '$dynamicRef'):
                    require(isinstance(v, str) and v.startswith('#'), 'external schema refs are forbidden')
                check_refs(v)
        elif isinstance(value, list):
            for v in value:
                check_refs(v)
    check_refs(schema)
    Draft202012Validator.check_schema(schema)


def validate_tool_file(root, tool):
    path = root/tool['file']
    tree = source_check(path.read_text(), str(path))
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
        require(not any(n == 'task_gen' or n.startswith('task_gen.') for n in names), 'generated tools cannot import host internals')
    function = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == tool['function']), None)
    require(function is not None, f'{path}: missing tool function')
    args = function.args
    require([a.arg for a in args.args] == ['context', 'inputs'] and not (args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults), 'expected tool(context, inputs)')


def validate_package(root, task_text=None, *, design_only=False):
    root = Path(root)
    doc = json.loads((root/'rubrics.json').read_text())
    rubrics = doc.get('rubrics')
    tools = json.loads((root/'tools.json').read_text()).get('tools')
    coverage = json.loads((root/'coverage.json').read_text()).get('requirements')
    require(isinstance(rubrics, list) and bool(rubrics), 'rubrics must be nonempty')
    require(isinstance(tools, list), 'tools must be a list; zero tools is allowed')
    require(bool((root/'instructions.md').read_text().strip()), 'instructions must be nonempty')
    ids, tool_ids, files = set(), set(), set()
    for r in rubrics:
        rid = r.get('id')
        require(isinstance(rid, str) and rid.isidentifier() and rid not in ids, 'invalid/duplicate rubric ID')
        ids.add(rid)
        require(type(r.get('weight')) in (int, float) and math.isfinite(r['weight']) and r['weight'] > 0, 'invalid weight')
        require(r.get('scale') in ('binary', 'ordinal_5') and r.get('method') in ('static', 'codex'), 'invalid scale/method')
        for name in ('goal', 'inputs', 'codex_work', 'evidence_plan', 'missing_policy', 'error_policy', 'method_reason'):
            require(bool(r.get(name)), f'{rid}: missing {name}')
        require(not any(k in r for k in ('depends_on', 'dependencies')), 'no scheduling dependencies in rubric')
        levels = range(2 if r['scale'] == 'binary' else 6)
        require(isinstance(r.get('criteria'), dict) and all(bool(r['criteria'].get(str(i))) for i in levels), f'{rid}: every score level needs criteria')
        quotes = r.get('task_quotes')
        require(isinstance(quotes, list) and bool(quotes) and all(isinstance(q, str) and q.strip() and (task_text is None or q in task_text) for q in quotes), f'{rid}: invalid task quote')
        require(isinstance(r.get('static_tools'), list) and all(isinstance(x, str) for x in r['static_tools']), 'static_tools must list IDs')
        require(isinstance(r.get('required_tools', []), list) and set(r.get('required_tools', [])) <= set(r['static_tools']), 'required_tools must be declared static_tools')
    for t in tools:
        tid = t.get('id')
        require(isinstance(tid, str) and tid.isidentifier() and tid not in tool_ids, 'invalid/duplicate tool ID')
        tool_ids.add(tid)
        path = PurePosixPath(t.get('file', ''))
        require(len(path.parts) == 2 and path.parts[0] == 'tools' and path.suffix == '.py' and path.stem.isidentifier() and path.stem != '__init__' and str(path) not in files, 'invalid/duplicate tool file')
        files.add(str(path))
        require(isinstance(t.get('function'), str) and t['function'].isidentifier(), 'invalid tool function name')
        for name in ('purpose', 'applicability', 'limits', 'cost_reason', 'path_policy', 'error_policy'):
            require(bool(t.get(name)), f'{tid}: missing {name}')
        require(isinstance(t.get('rubric_ids'), list) and bool(t['rubric_ids']) and set(t['rubric_ids']) <= ids, f'{tid}: invalid rubric associations')
        for name in ('input_schema', 'output_schema'):
            validate_schema(t.get(name))
        status = t['output_schema'].get('properties', {}).get('status', {}).get('enum', [])
        require(bool(status) and set(status) <= {'ok', 'missing', 'not_applicable', 'verification_error'} and 'status' in t['output_schema'].get('required', []), 'output schema requires explicit status enum')
    for r in rubrics:
        require(set(r['static_tools']) <= tool_ids, 'unknown static tool')
        require(all(r['id'] in t['rubric_ids'] for t in tools if t['id'] in r['static_tools']), 'inconsistent tool/rubric association')
    for t in tools:
        require(all(t['id'] in r['static_tools'] for r in rubrics if r['id'] in t['rubric_ids']), 'inconsistent rubric/tool association')
    require(isinstance(coverage, list) and bool(coverage), 'coverage must be nonempty')
    covered = set()
    for entry in coverage:
        quote = entry.get('task_quote')
        require(isinstance(quote, str) and quote.strip() and (task_text is None or quote in task_text), 'coverage quote must come from task')
        refs = entry.get('rubric_ids')
        require(isinstance(refs, list) and bool(refs) and set(refs) <= ids, 'coverage has invalid rubric IDs')
        covered.update(refs)
    require(covered == ids, 'coverage must include all rubrics')
    allowed = DESIGN_FILES | files | {'tools/__init__.py'}
    for p in root.rglob('*'):
        require(not p.is_symlink(), f'symlink in package: {p}')
        require(not p.is_file() or str(p.relative_to(root)) in allowed, f'unexpected package file: {p}')
    for p in root.rglob('*.py'):
        source_check(p.read_text(), str(p))
    if not design_only:
        for t in tools:
            validate_tool_file(root, t)
    return {'rubrics': rubrics, 'tools': tools, 'coverage': coverage}


def validate_results(plan, results, calls, evidence, scratch):
    try:
        aggregated = aggregate_results(plan['rubrics'], results)
        validate_evidence(results, evidence, scratch)
    except RuntimeError as e:
        raise ValueError(str(e)) from e
    by_call = {c['call_id']: c for c in calls}
    tools = {t['id']: t for t in plan['tools']}
    rubrics = {r['id']: r for r in plan['rubrics']}
    require(all(c.get('status') == 'completed' and c.get('result', {}).get('status') != 'verification_error' for c in calls), 'session contains failed/abandoned tools')
    for result in results:
        rid = result['rubric_id']
        refs = result.get('tool_call_ids')
        require(isinstance(refs, list) and all(isinstance(x, str) for x in refs) and len(set(refs)) == len(refs), 'tool_call_ids must be unique IDs')
        observed = set()
        for ref in refs:
            require(ref in by_call, 'unknown tool call ID in current session')
            tid = by_call[ref]['tool_id']
            require(tid in tools and rid in tools[tid]['rubric_ids'] and tid in rubrics[rid]['static_tools'], 'tool call not associated with this rubric')
            observed.add(tid)
        require(set(rubrics[rid].get('required_tools', [])) <= observed, f'{rid}: required tool evidence missing')
    return aggregated
