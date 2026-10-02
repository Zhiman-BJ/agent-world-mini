"""Run a resumable delivery trial with isolated environment processes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def record(path, event, **fields):
    with path.open('a') as stream:
        stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
                                 'event': event, **fields}, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=5)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--worker')
    args = parser.parse_args()
    source, output, config = (p.resolve() for p in (args.source, args.output, args.config))
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.pipeline import run
        directory = output / args.worker
        directory.mkdir(exist_ok=True)
        existing = sorted(directory.glob('*/run.json'))
        if existing:
            run_dir = existing[-1].parent
            meta = json.loads(existing[-1].read_text())
            if meta.get('status') == 'completed':
                tasks_path = run_dir / 'tasks.json'
                try:
                    task_count = len(json.loads(tasks_path.read_text())) if tasks_path.exists() else 0
                except (OSError, json.JSONDecodeError, TypeError):
                    task_count = 0
                if task_count:
                    return
                # A completed run with no tasks is a failed terminal artifact;
                # start a clean attempt so a fixed implementation can be tested.
                existing = []
            # Fixed-seed sampling with no eligible chains cannot improve on retry.
            if meta.get('failed_step') == 'step_2_chain_sample' and '不使用越界链兜底' in str(meta.get('error')):
                return
        if not existing:
            cfg = run_io.load_config(config, {'environment_dir': source / args.worker,
                                               'output_root': directory})
            run_dir = run_io.create_run_dir(cfg)
            run_io.save_run_meta(run_dir, cfg)
        record(directory / 'stability.jsonl', 'start_or_resume', run_dir=str(run_dir))
        try:
            result = run(resume=run_dir)
            record(directory / 'stability.jsonl', 'finished', tasks=result.task_count if result else None)
        except Exception as error:
            record(directory / 'stability.jsonl', 'failed', error=str(error))
            raise
        return

    names = [p.parent.name for p in sorted(source.glob('*/binding.json'))[args.offset:args.offset + 10]]
    if len(names) != 10 or args.concurrency < 1:
        parser.error('Need ten deliveries and positive concurrency')
    manifest = {'environments': names, 'environment_concurrency': args.concurrency,
                'config': str(config), 'source': str(source), 'evaluation': False,
                'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}
    (output / 'active_manifest.json').write_text(json.dumps(manifest, indent=2))

    def launch(name):
        directory = output / name
        directory.mkdir(exist_ok=True)
        with (directory / 'pipeline.log').open('a') as log:
            result = subprocess.run([sys.executable, '-u', str(Path(__file__).resolve()),
                '--source', str(source), '--output', str(output), '--config', str(config),
                '--worker', name], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        meta_path = sorted(directory.glob('*/run.json'))[-1]
        meta = json.loads(meta_path.read_text())
        tasks_path = meta_path.parent / 'tasks.json'
        tasks = len(json.loads(tasks_path.read_text())) if tasks_path.exists() else 0
        summary = {'status': meta.get('status'), 'tasks': tasks, 'error': meta.get('error'),
                   'failed_step': meta.get('failed_step'), 'exit_code': result.returncode,
                   'run_dir': str(meta_path.parent)}
        (directory / 'result_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(name, json.dumps(summary, ensure_ascii=False), flush=True)
        return name, summary

    summaries = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for future in as_completed([pool.submit(launch, name) for name in names]):
            name, summary = future.result()
            summaries[name] = summary
            (output / 'batch_result.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
