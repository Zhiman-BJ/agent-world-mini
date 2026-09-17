"""Build scenario-first API references from complete, version-pinned package indexes.

Selection decisions are reviewed profiles, not a numerical/LLM ranking.  This
builder enforces provenance, method subsets, capability and task references.
It never promotes an unexecuted design into a verified runtime environment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES = Path('seed_gen/scenario_pilot/profiles')
DEFAULT_OUTPUT = Path('seed_gen/pypi_outputs/scenario_pilot')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def counts(tools):
    nclass = sum(t['type'] == 'class' for t in tools)
    nfunc = sum(t['type'] == 'function' for t in tools)
    nmethod = sum(len(t['function']) for t in tools if t['type'] == 'class')
    return {'class': nclass, 'function': nfunc, 'class_func': nmethod,
            'all_func': nfunc + nmethod}


def key(tool):
    return tool['module'], tool['name'], tool['type']


def ref(tool):
    return tool['module'] + '.' + tool['name']


def classification_rows(text, wanted_ids=None):
    """Read numbered table rows; preserve labels and fill only merged L2 cells.

    Freeform numbered lists in the classification are not silently converted
    into researched scenes. They need an explicit application boundary first.
    """
    rows, l1, l2, headers = {}, '', '', []
    for line in text.splitlines():
        heading = re.match(r'^#\s+(\d{2}\s+.+)$', line)
        if heading:
            l1, l2, headers = heading[1].strip(), '', []
        if not line.startswith('|'):
            headers = []
            continue
        cells = [v.strip().replace('**', '') for v in line.strip('|').split('|')]
        if 'L3' in cells:
            headers = cells
            continue
        if not headers or all(re.fullmatch(r':?-+:?', c) for c in cells):
            continue
        require(len(cells) == len(headers), 'Malformed classification table row')
        row = dict(zip(headers, cells))
        l3 = row['L3']
        match = re.match(r'^(\d{2}\.\d{2}\.\d{2})\s', l3)
        if not match:
            continue
        l2 = row.get('L2', '') or l2
        # Some later sections define L2 in a heading rather than this table.
        # Do not guess that hierarchy when it has not been normalized.
        if not l1 or not l2 or not l3.startswith(l2.split()[0] + '.'):
            continue
        scene_id = match[1]
        if wanted_ids is not None and scene_id not in wanted_ids:
            continue
        require(scene_id not in rows, f'Duplicate classification ID: {scene_id}')
        application = next((row[h] for h in ('实际应用场景', '应用场景', '场景', '工程场景') if h in row), '')
        require(application, f'Missing application: {scene_id}')
        rows[scene_id] = {'domain': {'level1': l1, 'level2': l2, 'level3': l3},
                          'application': application, 'source_row': row}
    return rows


def select_joint(profile, payloads):
    """Select jointly from *all* candidates, returning an exhaustive audit."""
    packages = profile['packages']
    names = [p['name'] for p in packages]
    require(len(names) == len(set(names)), 'Duplicate candidate package')
    require(set(names) == set(payloads), 'Candidate package/payload mismatch')
    capabilities = {c['id']: c for c in profile['capabilities']}
    require(len(capabilities) == len(profile['capabilities']), 'Duplicate capability')
    require(bool(capabilities), 'No scenario capabilities')
    indexes, package_by_name = {}, {p['name']: p for p in packages}
    for package in packages:
        name = package['name']
        payload = payloads[name]
        require(isinstance(payload, list) and len(payload) == 1, 'Expected single-package raw seed')
        require(package['role'] in ('primary', 'complement', 'excluded'), 'Unknown package role')
        require(bool(package['reason']), 'Package decision needs a reason')
        info = payload[0]['environment']['basic_info']
        require(info['name'] == name and info['version'] == package['version'], f'Package/version mismatch: {name}')
        require(_canonical_sha256(payload) == package['raw_sha256'], f'Raw hash mismatch: {name}')
        tools = payload[0]['init_ref_tools']
        index = {key(t): t for t in tools}
        require(len(index) == len(tools), f'Duplicate source symbol in {name}')
        for tool in tools:
            methods = tool.get('function', [])
            require(len({m['name'] for m in methods}) == len(methods), f'Duplicate source method: {ref(tool)}')
        indexes[name] = index
    selected, choices, missing_docs, construction_gaps = [], {}, [], []
    global_keys = set()
    for spec in profile['symbols']:
        name = spec['package']
        require(name in indexes, f'Unknown package: {name}')
        require(package_by_name[name]['role'] != 'excluded', f'Excluded alternative selected: {name}')
        identity = key(spec)
        require((name, identity) not in choices, f'Duplicate selection: {name}:{identity}')
        require(identity not in global_keys, f'Conflicting cross-package identity: {identity}')
        require(spec['capability'] in capabilities, f'Unknown capability: {spec["capability"]}')
        require(bool(spec['reason']), f'Symbol needs a reason: {ref(spec)}')
        require(identity in indexes[name], f'Symbol absent from full index: {ref(spec)}')
        source = indexes[name][identity]
        tool = copy.deepcopy(source)
        if source['type'] == 'class':
            methods = spec.get('methods')
            require(isinstance(methods, list) and len(methods) == len(set(methods)), f'Explicit unique method subset required: {ref(spec)}')
            available = {m['name']: m for m in source['function']}
            require(set(methods) <= set(available), f'Unknown method: {ref(spec)}')
            if '__init__' in available and '__init__' not in methods:
                require(bool(spec.get('construction_reason')), f'Constructor removed without explanation: {ref(spec)}')
            if '__init__' not in available:
                construction_gaps.append({'symbol': ref(spec), 'reason': spec.get('construction_reason',
                    '构造方法由继承、dataclass 或运行时生成；必须在适配/小样例中核对，不能伪造源定义。')})
            tool['function'] = [copy.deepcopy(available[m]) for m in methods]
        else:
            require('methods' not in spec, f'Function cannot select methods: {ref(spec)}')
        if not source.get('description') or any(not m.get('description') for m in tool.get('function', [])):
            require(bool(spec.get('missing_description_reason')), f'Empty source description needs reason: {ref(spec)}')
            missing_docs.append({'symbol': ref(spec), 'reason': spec['missing_description_reason'],
                                 'methods': [m['name'] for m in tool.get('function', []) if not m.get('description')]})
        choices[(name, identity)] = spec
        global_keys.add(identity)
        selected.append(tool)
    require(bool(selected), 'No tools selected')
    for name in names:
        if package_by_name[name]['role'] != 'excluded':
            require(any(n == name for n, _ in choices), f'Active package contributes no tool: {name}')
    coverage = {c: [ref(s) for s in profile['symbols'] if s['capability'] == c] for c in capabilities}
    for cid, cap in capabilities.items():
        require(coverage[cid] or not cap.get('required', True), f'Uncovered required capability: {cid}')
    tools_ref = {ref(t) for t in selected}
    tools_ref |= {ref(t) + '.' + m['name'] for t in selected for m in t.get('function', [])}
    # Infrastructure notes cannot grant a task permission to call an excluded
    # API. Only explicit runtime callables derived from a selected factory are
    # allowed as additional references, under the reserved runtime namespace.
    candidates = {ref(t) for idx in indexes.values() for t in idx.values()}
    candidates |= {ref(t) + '.' + m['name'] for idx in indexes.values() for t in idx.values() for m in t.get('function', [])}
    infra = set()
    for entry in profile.get('runtime_infrastructure', []):
        reference = entry['reference']
        require(reference not in candidates - tools_ref, 'Infrastructure cannot reintroduce an unselected candidate API')
        if entry.get('kind') == 'derived_callable':
            require(reference.startswith('runtime.') and reference not in candidates, 'Derived callable must use runtime namespace')
            require(entry.get('derived_from') in tools_ref, 'Derived callable requires a selected factory')
            infra.add(reference)
    require(len(profile['tasks']) >= 2, 'At least two complete task recipes are required')
    task_ids = set()
    for task in profile['tasks']:
        require(task['id'] not in task_ids, 'Duplicate task ID')
        task_ids.add(task['id'])
        require(all(task.get(f) for f in ('description', 'initial_state', 'steps', 'assertions', 'fixture')), 'Incomplete task recipe')
        for step in task['steps']:
            require(step.get('tool_refs'), f'Task step has no tool references: {task["id"]}')
            require(set(step['tool_refs']) <= tools_ref | infra,
                    f'Task calls unselected tools: {set(step["tool_refs"]) - tools_ref - infra}')
    audit = []
    for name in names:
        for identity, tool in indexes[name].items():
            chosen = choices.get((name, identity))
            method_names = set(chosen.get('methods', [])) if chosen else set()
            audit.append({'package': name, 'module': tool['module'], 'name': tool['name'], 'type': tool['type'],
                          'selected': chosen is not None,
                          'capability': chosen['capability'] if chosen else None,
                          'reason': chosen['reason'] if chosen else (
                              package_by_name[name]['reason'] if package_by_name[name]['role'] == 'excluded'
                              else 'outside_scenario_boundary_or_duplicate_role'),
                          'methods': [{'name': m['name'], 'selected': m['name'] in method_names,
                                       'reason': chosen['reason'] if m['name'] in method_names else 'outside_selected_method_boundary'}
                                      for m in tool.get('function', [])]})
    return selected, {'candidate_count': len(audit), 'decisions': audit, 'coverage': coverage,
                      'missing_source_descriptions': missing_docs, 'construction_gaps': construction_gaps}


def build_profile(profile, *, root=ROOT):
    table = root / profile['classification_source']
    require(file_sha(table) == profile['classification_sha256'], 'Classification changed: review profile first')
    row = classification_rows(table.read_text(encoding='utf-8'), {profile['scenario_id']})[profile['scenario_id']]
    if profile.get('classification_upstream'):
        from seed_gen.scripts.prepare_scenario_inventory import normalize
        upstream = profile['classification_upstream']
        upstream_path = root / upstream['path']
        require(file_sha(upstream_path) == upstream['sha256'], 'Original classification changed')
        originals = normalize(upstream_path.read_text(encoding='utf-8'))
        original = next(r for r in originals if r['scenario_id'] == profile['scenario_id'])
        require(original['domain'] == row['domain'] and original['application'] == row['application'],
                'Normalized classification does not match the original/reviewed supplement')
    source_checks = verify_raw_sources(profile, root)
    research = root / profile['research_file']
    require(file_sha(research) == profile['research_sha256'], 'Research changed: review and refresh profile')
    runtime_evidence = []
    for runtime in profile.get('runtime_reports', []):
        require(file_sha(root / runtime['path']) == runtime['sha256'], 'Runtime evidence changed: review and refresh profile')
        result = read(root / runtime['path'])
        require(result['scenario_id'] == profile['scenario_id'], 'Wrong runtime scenario report')
        tasks = result.get('tasks', result.get('task_results', result.get('fixtures', [])))
        runtime_evidence.append({'path': runtime['path'], 'status': result['status'],
                                 'tasks': [{'id': t['id'], 'status': t['status']} for t in tasks]})
    passed_tasks = {t['id'] for r in runtime_evidence if r['status'] == 'passed'
                    for t in r['tasks'] if t['status'] == 'passed'}
    for task in profile['tasks']:
        if str(task.get('validation_status', '')).startswith('passed'):
            require(task['id'] in passed_tasks, 'Task claims passed without matching runtime evidence')
    payloads = {p['name']: read(root / p['raw_path']) for p in profile['packages']}
    tools, audit = select_joint(profile, payloads)
    sources = profile['research_sources']
    urls = list(dict.fromkeys(s['url'] for s in sources if s.get('use_in_seed', True)))
    require(3 <= len(urls) <= 10, 'Scenario needs 3-10 unique application URLs')
    for source in sources:
        require(source.get('relevance'), 'Research source needs entity/action/task relevance')
        require(source.get('checked_on') and source.get('status'), 'Research source needs retrieval evidence')
        require(urlsplit(source['url']).scheme in ('https', 'http') and urlsplit(source['url']).netloc, 'Invalid research URL')
        if source.get('use_in_seed', True):
            require(source['status'] == 200 and source.get('title'), 'Selected research page was not successfully inspected')
        if source.get('content_file'):
            evidence_file = root / source['content_file']
            require(file_sha(evidence_file) == source.get('content_sha256'), 'Research page snapshot changed')
            require(evidence_file.stat().st_size > 0, 'Research page snapshot is empty')
    nums = counts(tools)
    target = profile['target_all_func']
    require(target['min'] <= nums['all_func'] <= target['max'] or profile.get('count_exception'), 'Count outside target without explanation')
    active = [p for p in profile['packages'] if p['role'] != 'excluded']
    design = {k: copy.deepcopy(profile.get(k, [])) for k in (
        'entities', 'capabilities', 'bridges', 'runtime_infrastructure', 'boundaries', 'proposed_facade')}
    design['task_recipes'] = copy.deepcopy(profile['tasks'])
    design['status'] = 'seed_design; reference APIs are not an implemented agent tool server'
    seed = {'global_id': 'semiconductor_scenario_' + profile['scenario_id'].replace('.', '_'),
            'schema_version': 'scenario-1.0',
            'environment': {'basic_info': {'source': 'deep research', 'url': urls, 'name': row['application'],
                                           'version': profile['checked_on'], 'index': profile['index']},
                            'description': profile['description'], 'domain': row['domain'], 'nums': nums},
            'init_ref_tools': tools, 'init_ref_tasks': [t['description'] for t in profile['tasks']],
            'others': {'basic_info': [dict(payloads[p['name']][0]['environment']['basic_info'],
                                           description=payloads[p['name']][0]['environment']['description']) for p in active],
                       'pypi_package': [p['name'] for p in active], 'package_relation': profile['package_relation'],
                       'package_metadata': [copy.deepcopy(payloads[p['name']][0]['others']) for p in active],
                       'scenario_design': design,
                       'research_sources': sources,
                       'joint_selection': {'profile_sha256': _canonical_sha256(profile),
                                           'candidate_packages': profile['packages'],
                                           'classification_source': profile['classification_source'],
                                           'classification_sha256': profile['classification_sha256'],
                                           'count_policy': 'all_func = function + class_func; reference operations, including constructors/properties'}}}
    audit.update({'scenario_id': profile['scenario_id'], 'nums': nums,
                  'profile_sha256': _canonical_sha256(profile), 'target_all_func': target,
                  'count_exception': profile.get('count_exception', ''),
                  'static_verification': 'passed', 'runtime_verified_all_tools': False,
                  'source_reextraction': source_checks,
                  'capability_check_scope': 'explicit group membership; semantic/dataflow completeness requires reviewed design and executed fixtures',
                  'runtime_reports': profile.get('runtime_reports', []),
                  'runtime_evidence': runtime_evidence,
                  'pilot_task_gate_passed': {t['id'] for t in profile['tasks']} <= passed_tasks,
                  'package_decisions': profile['packages']})
    # The existing package contract is not silently changed to fit this pilot.
    # Reuse its tool definition, recording only pre-existing empty docstrings.
    contract = read(root / 'schemas/validation/env_seeds.schema.json')
    tool_schema = {'$schema': contract['$schema'], '$defs': contract['$defs'],
                   'type': 'array', 'items': {'$ref': '#/$defs/referenceTool'}}
    validator = Draft202012Validator(tool_schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(tools))
    empty_docs = [e for e in errors if e.validator == 'minLength' and e.instance == ''
                  and list(e.absolute_path)[-1:] == ['description']]
    require(len(errors) == len(empty_docs), 'Unexpected tool-schema errors: ' + '; '.join(e.message for e in errors if e not in empty_docs))
    audit['source_empty_description_schema_exceptions'] = [list(e.absolute_path) for e in empty_docs]
    audit['unexpected_tool_schema_errors'] = 0
    return [seed], audit


def verify_raw_sources(profile, root=ROOT):
    """Re-extract from a clean pinned checkout; never rewrite source snapshots."""
    from seed_gen.scripts.extract_release_python_seeds import build_seed
    require(Path.cwd().resolve() == root.resolve(), 'Run source re-extraction from the repository root')
    checks = []
    for package in profile['packages']:
        manifests = read(root / package['release_manifest'])
        matches = [s for s in manifests if s['name'] == package['name'] and s['tag'] == package['version']]
        require(len(matches) == 1, f'Ambiguous release specification: {package["name"]}')
        # Relative paths are part of the original provenance serialization.
        fresh = build_seed(matches[0], Path('seed_pypi_raw'))
        stored = read(root / package['raw_path'])
        require(fresh == stored, f'Raw index no longer matches release source: {package["name"]}')
        checks.append({'package': package['name'], 'raw_sha256': _canonical_sha256(stored),
                       'source_commit': matches[0]['commit'], 'reextraction': 'identical'})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=DEFAULT_PROFILES)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-sources', action='store_true', help='Compatibility flag; release-source re-extraction is always performed')
    parser.add_argument('--merged-name', default='semiconductor_scenario_pilot.json', help='Filename for the combined output')
    parser.add_argument('--group-by-l1', action='store_true', help='Also write semiconductor_scenario_XX.json by L1')
    args = parser.parse_args()
    profiles = sorted(args.profiles.glob('*.json'))
    require(profiles, 'No scenario profiles')
    outputs, summary, merged, seen, indices = {}, [], [], set(), set()
    for path in profiles:
        profile = read(path)
        sid = profile['scenario_id']
        require(sid not in seen and profile['index'] not in indices, 'Duplicate scenario identity/index')
        seen.add(sid)
        indices.add(profile['index'])
        payload, report = build_profile(profile)
        if args.verify_sources:
            for check in report['source_reextraction']:
                print('Source verified:', check['package'], check['source_commit'])
        outputs[args.output_dir / f'{sid}.json'] = payload
        outputs[args.output_dir / 'reports' / f'{sid}.json'] = report
        merged.extend(payload)
        summary.append({'scenario_id': sid, 'name': payload[0]['environment']['basic_info']['name'],
                        'packages': payload[0]['others']['pypi_package'], 'nums': report['nums'],
                        'candidate_count': report['candidate_count'],
                        'missing_description_count': len(report['missing_source_descriptions']),
                        'construction_gaps': report['construction_gaps'],
                        'pilot_task_gate_passed': report['pilot_task_gate_passed'],
                        'runtime_reports': report['runtime_reports']})
    require(Path(args.merged_name).name == args.merged_name and args.merged_name.endswith('.json'), 'Merged name must be a JSON filename')
    outputs[args.output_dir / args.merged_name] = merged
    if args.group_by_l1:
        for l1 in sorted({s['environment']['domain']['level1'][:2] for s in merged}):
            outputs[args.output_dir / f'semiconductor_scenario_{l1}.json'] = [
                s for s in merged if s['environment']['domain']['level1'].startswith(l1 + ' ')]
    outputs[args.output_dir / 'reports/summary.json'] = summary
    for path, payload in outputs.items():
        text = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
        if args.check:
            require(path.exists() and path.read_text(encoding='utf-8') == text, f'Artifact drift: {path}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')
    print(f'{"Verified" if args.check else "Generated"} {len(profiles)} scenario-first seeds.')


if __name__ == '__main__':
    main()
