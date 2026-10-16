"""Uniform task sample; reuse generation and reference application without repair."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import random
import time

from task_gen.eval_cases import load_cases
from .application.runner import apply
from .data import DEFAULT_DATA_ROOT, prepare_evidence
from .generation.pipeline import generate, write_json
from .validate_reference import usage_totals, write_report


def select_sample(data_root: Path, count: int, seed: int) -> dict:
    population = []
    for path in sorted((data_root/'environments').glob('*/tasks.json')):
        for task in json.loads(path.read_text()):
            population.append({'case_name': f'{path.parent.name}__{task["task_id"]}',
                               'environment': path.parent.name, 'task_id': task['task_id'],
                               'task_text': task['task_text']})
    population.sort(key=lambda item: item['case_name'])
    identities = [item['case_name'] for item in population]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate task identities in population')
    selected = random.Random(seed).sample(population, count)
    return {'data_root': str(data_root.resolve()), 'seed': seed, 'sample_count': count,
            'population_count': len(population),
            'population_ids_sha256': hashlib.sha256('\n'.join(identities).encode()).hexdigest(),
            'method': 'uniform random.sample without replacement; sorted environment/task identity; no eligibility filter or replacement',
            'selected': [dict(item, case=f'P{i:02d}') for i, item in enumerate(selected, 1)]}


def load_selection(path: Path, count: int) -> dict:
    selection = json.loads(path.read_text())
    items = selection['selected']
    if not 1 <= count <= len(items):
        raise ValueError('count exceeds fixed sample manifest')
    if len({item['case_name'] for item in items}) != len(items) or len({item['case'] for item in items}) != len(items):
        raise ValueError('duplicate identities in sample manifest')
    for item in items:
        if item['case_name'] != f"{item['environment']}__{item['task_id']}":
            raise ValueError('inconsistent task identity in sample manifest')
    return dict(selection, source_manifest=str(path.resolve()), source_sample_count=len(items),
                sample_count=count, selected=items[:count])


def run_case(item: dict, data_root: Path, batch: Path, config: dict) -> dict:
    out = batch/item['case']
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    stage = 'evidence_preparation'
    application = None
    design = {}
    try:
        cases = load_cases(data_root, selected_case_names={item['case_name']})
        if len(cases) != 1:
            raise ValueError(f'Expected one selected task, found {len(cases)}')
        evidence = prepare_evidence(cases[0], out/'source_evidence')
        stage = 'generation'
        print(f"{item['case']}: generating {item['task_id']}", flush=True)
        generation = generate(evidence, out, config)
        if generation['status'] == 'generated':
            design = json.loads((out/'verifier/rubrics.json').read_text())
            stage = 'application'
            print(f"{item['case']}: applying", flush=True)
            application = apply(out/'verifier', evidence, out/'reference_validation', config)
    except Exception as error:
        detail = f'{type(error).__name__}: {error}'
        if stage == 'application':
            application = {'mode': 'program', 'status': 'verification_error', 'score': None, 'error': detail}
        else:
            generation = {'task_id': item['task_id'], 'status': 'generation_failed',
                          'stage': stage, 'error': detail, 'seconds': time.monotonic()-started}
            write_json(out/'generation/result.json', generation)
    calls = [json.loads(path.read_text()).get('usage') for path in (out/'generation').glob('*/call.json')]
    row = {'case': item['case'], 'task_id': item['task_id'], 'directory': str(out),
           'generation': generation, 'application': application, 'design': design,
           'generation_usage': usage_totals(calls), 'seconds': time.monotonic()-started}
    write_json(out/'row.json', row)
    print(f"{item['case']}: {generation['status']}; application={application and application.get('mode')}; score={application and application.get('score')}", flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('run_config.json'))
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--seed', type=int, default=20261015)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--sampling-manifest', type=Path, help='Reuse the first --count entries of an existing sample, preserving IDs')
    args = parser.parse_args()
    if args.workers < 1 or args.count < 1:
        parser.error('workers and count must be positive')
    config = json.loads(args.config.read_text())
    # Load model dependencies once on the main thread before launching case workers.
    from .generation.model import GenerationModel
    assert GenerationModel
    batch = args.output.resolve()
    selection = load_selection(args.sampling_manifest, args.count) if args.sampling_manifest else select_sample(args.data_root, args.count, args.seed)
    batch.mkdir(parents=True, exist_ok=False)
    write_json(batch/'sampling.json', selection)
    write_json(batch/'run_config.json', config)
    print(f"Sampled {args.count}/{selection['population_count']}; seed={args.seed}; workers={args.workers}", flush=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, item, args.data_root, batch, config) for item in selection['selected']]
        for future in as_completed(futures):
            rows.append(future.result())
            rows.sort(key=lambda row: row['case'])
            write_report(batch, rows)
            summary = json.loads((batch/'summary.json').read_text())
            summary['sampling'] = {key: value for key, value in selection.items() if key != 'selected'}
            summary['pending'] = args.count-len(rows)
            write_json(batch/'summary.json', summary)
            path = batch/'RESULTS.md'
            report = path.read_text().replace('Program verifier 首版实跑结果', 'Program verifier 随机抽样实跑结果').replace('六案例选样统计', '已完成样本统计')
            report += f"\n抽样母集 {selection['population_count']} 个任务；随机种子 {args.seed}；固定抽取 {args.count} 个，已完成 {len(rows)} 个。失败不补抽。完整原文见 sampling.json。\n"
            path.write_text(report)


if __name__ == '__main__':
    main()
