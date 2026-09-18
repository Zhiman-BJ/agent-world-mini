"""Reuse the four complete Step2 pools; run fresh Step3–5 with isolated runtimes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import run_three_search_trial as trial
from task_gen.tool_graph import run_io
from task_gen.tool_graph.contracts import Config

ROOT = trial.ROOT
OUT = ROOT / 'runs/four_science_adjusted_20260918'
SOURCES = {
    'pypi_doped_6': 'two_search_feasible_20260917',
    'pypi_klayout_7': 'two_search_feasible_runtime_retry_20260917',
    'pypi_atomate2_5': 'three_search_recovered_20260917',
    'pypi_pymatgen_core_4': 'three_search_recovered_20260917',
}


def prepare(name):
    source = sorted((ROOT / 'runs' / SOURCES[name] / name).glob('*/run.json'))[-1].parent
    values = json.loads((source / 'run.json').read_text())['config']
    for key in ('environment_dir', 'schema_dir', 'output_root'):
        values[key] = Path(values[key])
    values['output_root'] = OUT / name
    config = Config(**values)
    config.execution.update(max_concurrency=2, tool_result_max_bytes=65536)
    config.llm['max_concurrency'] = 2
    target = run_io.create_run_dir(config)
    run_io.save_run_meta(target, config)
    bundle = json.loads((source / 'intermediate/step_2_bundle.json').read_text())
    run_io.save_bundle(target, bundle)
    event = {'time': datetime.now(timezone.utc).isoformat(), 'event': 'prepared',
             'source': str(source), 'run_dir': str(target), 'candidates': len(bundle['tasks']),
             'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
             'concurrency': 2, 'result_limit_bytes': 65536}
    with (target.parent / 'stability.jsonl').open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
    return target


def launch(name):
    directory = OUT / name
    if not list(directory.glob('*/run.json')):
        prepare(name)
    # Existing cached environments hold the upstream scientific dependency versions.
    runtime = (Path.home() / '.cache/uv/archive-v0' /
               ('7M7Oqr6MwfZHkKvi' if name == 'pypi_klayout_7' else '_YdszU22Dnynt7ql') / 'bin/python')
    with (directory / 'pipeline.log').open('a') as log:
        result = subprocess.run([str(runtime), '-u', str(Path(__file__).resolve()), '--worker', name],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    print(name, 'process_exit=', result.returncode, flush=True)
    return result.returncode


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        trial.OUT = OUT
        sys.exit(0 if trial.execute(sys.argv[2]) else 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, SOURCES))
    print('RESULTS', dict(zip(SOURCES, results)), flush=True)
