"""Generate once per method/task, then evaluate the frozen package twice."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
import traceback

from task_gen.eval_cases import load_cases
from .program_verifier.data import DEFAULT_DATA_ROOT, prepare_evidence
from .program_verifier.generation.model import snapshot
from .program_verifier.generation.pipeline import write_json
from .program_verifier.metering import MeteringRelay, now
from .program_verifier.repeat_sample import metered_config


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def execute(method, evidence, output, config, *, phase=None):
    output, evidence = Path(output), Path(evidence)
    output.mkdir(parents=True, exist_ok=True)
    if method == 'program':
        from .program_verifier.generation.pipeline import generate
        from .program_verifier.application.runner import apply
    else:
        from .codex_tool_verifier.generation import generate
        from .codex_tool_verifier.application import apply
    row = {'method':method,'status':'running','generation':None,'applications':[]}
    started = time.monotonic()
    try:
        if phase:
            phase['name'] = 'generation'
        generation = read(output/'generation/result.json')
        if generation is None:
            generation = generate(evidence, output, config)
        row['generation'] = generation
        if generation['status'] != 'generated':
            row['status'] = 'generation_failed'
            return row
        root = output/'verifier'
        frozen = read(output/'frozen_manifest.json')
        if frozen is None:
            frozen = snapshot(root)
            write_json(output/'frozen_manifest.json', frozen)
        for repetition in (1, 2):
            if snapshot(root) != frozen:
                raise ValueError('Frozen verifier changed before application')
            if phase:
                phase['name'] = f'application_{repetition}'
            application_dir = output/'applications'/f'run_{repetition:02d}'
            app = read(application_dir/'result.json')
            if app is None:
                if application_dir.exists():
                    raise ValueError(f'Interrupted application retained: {application_dir}')
                application_dir.parent.mkdir(parents=True, exist_ok=True)
                app = apply(root, evidence, application_dir, config)
                write_json(application_dir/'result.json', app)
            row['applications'].append(app)
            if snapshot(root) != frozen:
                raise ValueError('Frozen verifier changed during application')
        a, b = row['applications']
        native = 'program' if method == 'program' else 'codex_with_tools'
        both_native = all(x.get('mode') == native and x.get('status') == 'completed' for x in (a,b))
        row['both_native'] = both_native
        row['same_total_score'] = a['score'] == b['score'] if both_native else None
        scores = lambda value: {r['rubric_id']:r['score'] for r in value.get('rubrics',[])}
        row['same_rubric_scores'] = scores(a) == scores(b) if both_native else None
        row['status'] = 'finished'
    except Exception as error:
        row.update(status='error', error=f'{type(error).__name__}: {error}')
        (output/'error.log').write_text(traceback.format_exc())
    finally:
        row['seconds'] = time.monotonic()-started
        write_json(output/'row.json', row)
    return row


def source_snapshot(batch):
    repo = Path(__file__).resolve().parents[2]
    files = [repo/'task_gen/task_eval_verifier.py', repo/'task_gen/tool_graph/codex.py', repo/'task_gen/tool_graph/llm.py']
    source = Path(__file__).parent
    files += [p for p in source.rglob('*') if p.is_file() and p.suffix in ('.py','.md','.json')
              and not any(part in p.relative_to(source).parts for part in ('runs','__pycache__','manual_s59','tests'))]
    hashes = {str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    revision = hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    target = batch/'source_versions'/revision
    if not target.exists():
        for p in files:
            dest = target/p.relative_to(repo)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
        write_json(target/'hashes.json', hashes)
    return revision


def run_job(method, item, evidence, batch, config, revision):
    output = batch/method/item['sample_id']
    output.mkdir(parents=True, exist_ok=True)
    cached = read(output/'row.json')
    if cached is not None:
        return cached
    home = Path(config['codex'].get('codex_home', Path.home()/'.codex'))
    settings = tomllib.loads((home/'config.toml').read_text())
    upstream = settings['model_providers'][settings['model_provider']]['base_url']
    state = {'name':'generation'}
    def phase():
        name = state['name']
        if name == 'generation' and list((output/'generation').glob('*_final/call.json')):
            return 'final_review'
        if name.startswith('application_'):
            directory = output/'applications'/f'run_{int(name.rsplit("_",1)[1]):02d}'
            if (directory/'program_error.json').exists() or (directory/'verification_error.json').exists():
                return name+'_fallback'
        return name
    write_json(output/'started.json', {'case':item,'method':method,'at':now(),'source_revision':revision})
    try:
        with MeteringRelay(upstream, output/'api', phase) as relay:
            with metered_config(config, relay) as cfg:
                row = execute(method, evidence, output, cfg, phase=state)
    except Exception as error:
        row = {'method':method,'status':'error','generation':None,'applications':[],
               'error':f'{type(error).__name__}: {error}'}
        (output/'error.log').write_text(traceback.format_exc())
    row.update(case=item['sample_id'], task_id=item['task_id'], source_revision=revision, finished_at=now())
    write_json(output/'row.json', row)
    print(f"DONE {method}/{item['sample_id']} {row['status']} "+str([a.get('score',a.get('outcome')) for a in row['applications']]), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--selection', type=Path, default=Path('task_gen/test_verifier/runs/random20_20261015/task_ids.tsv'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument('--methods', nargs='+', choices=['program','codex_tools'], default=['program','codex_tools'])
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--concurrency', type=int, default=4)
    args = parser.parse_args()
    config = read(args.config)
    assert config['codex']['model'] == 'gpt-6.1-sol' and config['codex']['reasoning_effort'] == 'high'
    assert config.get('final_review_enabled',True) is True and args.concurrency > 0
    with args.selection.open(newline='') as stream:
        selected = list(csv.DictReader(stream, delimiter='\t'))
    if args.cases:
        assert set(args.cases) <= {r['sample_id'] for r in selected}
        selected = [r for r in selected if r['sample_id'] in args.cases]
    assert len({r['unique_task_id'] for r in selected}) == len(selected)
    batch = args.output.resolve()
    batch.mkdir(parents=True, exist_ok=True)
    manifest = {'selected':selected,'methods':args.methods,'config':config,
                'selection_sha256':hashlib.sha256(args.selection.read_bytes()).hexdigest()}
    previous = read(batch/'selection.json')
    if previous is not None and previous != manifest:
        raise ValueError('Existing experiment selection/config differs; use a new directory')
    write_json(batch/'selection.json', manifest)
    if not (batch/'pricing.json').exists():
        write_json(batch/'pricing.json', {'currency':'USD','source':'Provider rates unconfirmed; preserve API-reported costs and usage.'})
    revision = source_snapshot(batch)
    write_json(batch/'started.json', {'at':now(),'source_revision':revision,
               'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()})
    loaded = load_cases(args.data_root, selected_case_names={r['unique_task_id'] for r in selected})
    by_id = {f'{case.source_run.name}__{case.task["task_id"]}':case for case in loaded}
    assert set(by_id) == {r['unique_task_id'] for r in selected}
    jobs = []
    for item in selected:
        evidence = batch/'evidence'/item['sample_id']
        if not (evidence/'program_request.json').exists():
            started = time.monotonic()
            prepare_evidence(by_id[item['unique_task_id']], evidence)
            write_json(evidence/'preparation.json', {'seconds':time.monotonic()-started})
        jobs += [(method,item,evidence) for method in args.methods]
    from .final_review_report import write_report
    write_report(batch)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        pending = {pool.submit(run_job,method,item,evidence,batch,config,revision) for method,item,evidence in jobs}
        while pending:
            done, pending = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
            write_report(batch)
    write_json(batch/'finished.json', {'at':now()})
    write_report(batch)


if __name__ == '__main__':
    main()
