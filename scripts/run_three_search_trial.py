"""Run the three prepared environments; persist failures and resume checkpoints."""
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.tool_graph import run_io
from task_gen.tool_graph.pipeline import run
from task_gen.tool_graph.contracts import Config

OUT = ROOT / 'runs/three_search_recovered_20260917'
PREVIOUS = ROOT / 'runs/three_search_trial_20260917'
NAMES = ['pypi_atomate2_5', 'pypi_doped_6', 'pypi_pymatgen_core_4']


def execute(name):
    directory = OUT / name
    directory.mkdir(parents=True, exist_ok=True)
    ledger = directory / 'stability.jsonl'

    def record(event, **data):
        row = {'time': datetime.now(timezone.utc).isoformat(), 'event': event, **data}
        with ledger.open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        print(name, event, data, flush=True)

    runs = sorted(directory.glob('*/run.json'))
    if runs:
        run_dir = runs[-1].parent
    else:
        source = sorted((PREVIOUS / name).glob('*/run.json'))[-1].parent
        values = json.loads((source / 'run.json').read_text())['config']
        for key in ('environment_dir', 'schema_dir', 'output_root'):
            values[key] = Path(values[key])
        values['output_root'] = directory
        config = Config(**values)
        config.execution['enable_web_search'] = True
        # 本轮服务端明确返回并发超限；仅调整试跑配置，不改默认配置。
        config.execution['max_concurrency'] = 4
        config.llm['max_concurrency'] = 4
        run_dir = run_io.create_run_dir(config)
        run_io.save_run_meta(run_dir, config)
        run_io.save_bundle(run_dir, json.loads((source / 'intermediate/step_2_bundle.json').read_text()))
        record('reuse_step2', source=source, run_dir=run_dir, python=sys.executable)
    for attempt in range(1, 4):
        record('attempt_start', attempt=attempt, run_dir=run_dir)
        started = time.monotonic()
        try:
            result = run(resume=run_dir)
        except Exception as error:
            record('interrupted', attempt=attempt, seconds=time.monotonic()-started,
                   error=repr(error), traceback=traceback.format_exc())
            if attempt == 3:
                return False
            # Checkpoints retain completed graph targets and stages on retry.
            time.sleep(5)
        else:
            record('pipeline_returned', seconds=time.monotonic()-started, result=str(result))
            meta = json.loads((run_dir / 'run.json').read_text())
            record('acceptance', task_count=meta.get('task_count', 0), status=meta.get('status'))
            return meta.get('task_count', 0) > 0


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        names = sys.argv[1:] or NAMES
        results = list(pool.map(execute, names))
    print('RESULTS', dict(zip(names, results)), flush=True)
