"""Retry infrastructure failures and reproduced runtime-budget failures."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.tool_graph import run_io
from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.pipeline import run


def main():
    for name in sys.argv[1:]:
        source = sorted((ROOT / 'runs/three_search_recovered_20260917' / name).glob('*/run.json'))[-1].parent
        meta = json.loads((source / 'run.json').read_text())
        assert meta['status'] == 'completed', source
        failed = []
        memory_affected = []
        for path in (source / 'tasks').glob('*/agent_result.json'):
            task = json.loads(path.read_text())
            error = task['execution'].get('error')
            if error and error != 'Agent未完成目标':
                failed.append(task['task_id'])
            if name == 'pypi_doped_6':
                calls = task['execution'].get('raw_tool_calls', [])
                if any(c['tool'] in ('parse_defect_project_batch', 'parse_single_defect_calculation')
                       and 'IndexError: list index out of range' in str(c.get('error')) for c in calls):
                    memory_affected.append(task['task_id'])
                    if task['task_id'] not in failed:
                        failed.append(task['task_id'])
        if not failed:
            print(name, 'no runtime failures', flush=True)
            continue
        values = meta['config']
        for key in ('environment_dir', 'schema_dir', 'output_root'):
            values[key] = Path(values[key])
        values['output_root'] = ROOT / 'runs/three_search_runtime_retry_20260917' / name
        config = Config(**values)
        config.execution['max_concurrency'] = 1
        config.llm['max_concurrency'] = 1
        if name == 'pypi_doped_6':
            config.execution['tool_max_memory_bytes'] = 4 * 1024**3
        target = run_io.create_run_dir(config)
        run_io.save_run_meta(target, config)
        bundle = json.loads((source / 'intermediate/step_2_bundle.json').read_text())
        bundle['tasks'] = [task for task in bundle['tasks'] if task['task_id'] in failed]
        run_io.save_bundle(target, bundle)
        ledger = target / 'recovery.jsonl'
        def record(event, **details):
            with ledger.open('a') as stream:
                stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': event,
                                         'source': str(source), 'tasks': failed, **details}, ensure_ascii=False) + '\n')
        record('retry_start', concurrency=1, python=sys.executable,
               memory_affected=memory_affected, tool_max_memory_bytes=config.execution['tool_max_memory_bytes'])
        try:
            result = run(resume=target)
        except Exception as error:
            record('interrupted', error=repr(error))
            raise
        record('pipeline_returned', result=str(result))
        print(name, result, flush=True)


if __name__ == '__main__':
    main()
