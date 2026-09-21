"""Ten environments, five concurrent processes, one shared generation config."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.tool_graph import run_io
from task_gen.tool_graph.pipeline import run

CONFIG = ROOT / 'config/semiconductor_160.yaml'
SOURCE = Path('/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments')
NAMES = ['semiconductor_calibration_deembedding_1',
    'semiconductor_circuit_level_s_matrix_1', 'semiconductor_netlist_execution_1',
    'semiconductor_scenario_01_06_01', 'semiconductor_scenario_03_11_03',
    'semiconductor_parameter_sweep_monte_carlo_1', 'semiconductor_scenario_01_05_03',
    'semiconductor_scenario_03_02_02', 'semiconductor_geometry_construction_1',
    'semiconductor_scenario_04_01_01']
OUT = run_io.load_config(CONFIG).output_root
BATCH_FILE = None


def record(directory, event, **values):
    with (directory / 'stability.jsonl').open('a') as stream:
        stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
            'event': event, **values}, ensure_ascii=False, default=str) + '\n')


def worker(name):
    directory = OUT / name
    existing = sorted(directory.glob('*/run.json'))
    if existing:
        run_dir = existing[-1].parent
        if json.loads(existing[-1].read_text()).get('status') == 'completed':
            return
    else:
        config = run_io.load_config(CONFIG, {'environment_dir': SOURCE / name, 'output_root': directory})
        run_dir = run_io.create_run_dir(config)
        run_io.save_run_meta(run_dir, config)
    record(directory, 'start', run_dir=run_dir)
    try:
        result = run(resume=run_dir)
        record(directory, 'finished', tasks=result.task_count)
    except Exception as error:
        record(directory, 'interrupted', error=repr(error))
        raise


def launch(name):
    directory = OUT / name
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'pipeline.log').open('a') as log:
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker', name]
        if BATCH_FILE:
            command += ['--batch-file', str(BATCH_FILE)]
        result = subprocess.run(command,
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    record(directory, 'process_exit', code=result.returncode)
    return name, result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    parser.add_argument('--batch-file', type=Path)
    args = parser.parse_args()
    if args.batch_file:
        BATCH_FILE = args.batch_file.resolve()
        batch = json.loads(BATCH_FILE.read_text())
        NAMES = batch['environments']
        OUT = Path(batch['output_root']).resolve()
    if args.worker:
        assert args.worker in NAMES
        worker(args.worker)
    else:
        assert len(NAMES) == len(set(NAMES)) == 10
        assert all((SOURCE / name / 'binding.json').is_file() for name in NAMES)
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / '.batch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (OUT / 'manifest.json').write_text(json.dumps({'environments': NAMES,
                'environment_concurrency': 5, 'config': str(CONFIG), 'evaluation': False,
                'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}, indent=2))
            results = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                for future in as_completed([pool.submit(launch, name) for name in NAMES]):
                    name, code = future.result()
                    results[name] = code
                    (OUT / 'batch_result.json').write_text(json.dumps(results, indent=2))
                    print(name, code, flush=True)
