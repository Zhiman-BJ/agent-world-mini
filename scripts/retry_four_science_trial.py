"""Retry recorded infrastructure failures once, retaining original runs and states."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import run_three_search_trial as trial
from task_gen.tool_graph import run_io
from task_gen.tool_graph.contracts import Config


def main(name):
    source = sorted((trial.ROOT / 'runs/four_science_adjusted_20260918' / name).glob('*/run.json'))[-1].parent
    meta = json.loads((source / 'run.json').read_text())
    assert meta['status'] == 'completed'
    final = json.loads((source / 'intermediate/step_5_bundle.json').read_text())
    for stage, suffix in ((2, 'runtime_retry'), (3, 'compose_retry')):
        if stage == 2:
            selected = [t['task_id'] for t in final['tasks']
                        if t.get('execution', {}).get('error') not in (None, 'Agent未完成目标')]
        else:
            selected = [t['task_id'] for t in final['tasks']
                        if t.get('execution', {}).get('success') and t.get('compose_error')]
        if not selected:
            continue
        trial.OUT = trial.ROOT / ('runs/four_science_' + suffix + '_20260918')
        directory = trial.OUT / name
        if list(directory.glob('*/run.json')):
            print('Existing recovery retained:', directory, flush=True)
            continue
        values = dict(meta['config'])
        for key in ('environment_dir', 'schema_dir', 'output_root'):
            values[key] = Path(values[key])
        values['output_root'] = directory
        config = Config(**values)
        config.execution['max_concurrency'] = config.llm['max_concurrency'] = 1
        target = run_io.create_run_dir(config)
        run_io.save_run_meta(target, config)
        bundle = json.loads((source / f'intermediate/step_{stage}_bundle.json').read_text())
        bundle['tasks'] = [t for t in bundle['tasks'] if t['task_id'] in selected]
        run_io.save_bundle(target, bundle)
        if stage == 3:
            tasks = target / 'tasks'
            tasks.mkdir(exist_ok=True)
            for task_id in selected:
                (tasks / task_id).symlink_to(source / 'tasks' / task_id, target_is_directory=True)
        event = {'time': datetime.now(timezone.utc).isoformat(), 'event': 'targeted_retry',
                 'source': str(source), 'run_dir': str(target), 'tasks': selected,
                 'resume_after': stage, 'python': sys.executable,
                 'reason': 'Retry recorded execution infrastructure or composition errors; quality rejections unchanged.'}
        with (directory / 'stability.jsonl').open('a') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        trial.execute(name)


if __name__ == '__main__':
    for name in sys.argv[1:]:
        main(name)
