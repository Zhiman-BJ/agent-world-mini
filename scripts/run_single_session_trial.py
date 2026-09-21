"""Small real Step3 trial; reuses frozen Step2 candidates, never production state."""
import json
import argparse
from dataclasses import asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.tool_graph import execution_agent as agent, run_io
from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import capture_calls


def summarize(directory):
    rows = []
    for result in sorted(directory.glob('tasks/*/agent_result.json')):
        value = json.loads(result.read_text())
        sessions, searches = {}, set()
        for log in sorted(result.parent.glob('rounds/*/logs/run_*/stdout.log')):
            session = None
            for line in log.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') == 'thread.started':
                    session = event['thread_id']
                    sessions.setdefault(session, {})
                if event.get('type') == 'turn.completed' and session:
                    sessions[session] = event['usage']
                item = event.get('item', {})
                if event.get('type') == 'item.completed' and item.get('type') == 'web_search':
                    searches.add((session, item.get('id')))
        usage = {k: sum(u.get(k, 0) for u in sessions.values()) for k in
                 ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
        usage['uncached_input_tokens'] = usage['input_tokens'] - usage['cached_input_tokens']
        rows.append({'task_id': value['task_id'], 'accepted': value['execution']['success'],
            'calls': len(value['execution']['tool_calls']), 'attempts': len(value['execution']['attempts']),
            'sessions': len(sessions), 'unmetered_sessions': sum(not u for u in sessions.values()),
            'searches': len(searches), **usage})
    agent._write(directory / 'metrics.json', {'tasks': rows,
        'total': {k: sum(row[k] for row in rows) for k in
                  ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens', 'output_tokens')},
        'progress': json.loads((directory / 'step3_progress.json').read_text())})
    return rows


def main(directory=None, source=None, task_ids=None):
    source = Path(source).resolve() if source else sorted((ROOT.parent.parent / 'runs/semiconductor_160_20260919_step3_514331a/semiconductor_scenario_01_06_01').glob('*/run.json'))[-1].parent
    bundle = json.loads((source / 'intermediate/step_2_bundle.json').read_text())
    # Same historically expensive task6, plus two real spare candidates.
    by_id = {t['task_id']: t for t in bundle['tasks']}
    bundle['tasks'] = [by_id[i] for i in (task_ids or ('task6', 'task1', 'task2'))]
    config = run_io.load_config(ROOT / 'config/semiconductor_160.yaml', {
        'environment_dir': json.loads((source / 'run.json').read_text())['config']['environment_dir']})
    directory = Path(directory).resolve() if directory else ROOT / 'runs/single_session_trial'
    directory.mkdir(parents=True, exist_ok=False)
    agent._write(directory / 'config.json', json.loads(json.dumps(asdict(config), default=str)))
    agent._write(directory / 'candidates.json', bundle['tasks'])
    agent._write(directory / 'source.json', {'bundle': str(source / 'intermediate/step_2_bundle.json')})
    started = time.monotonic()
    agent._write(directory / 'status.json', {'status': 'running'})
    with capture_calls('step_3_chain_execute', lambda r: run_io.append_llm_call(directory, r)):
        result = agent.execute_candidates(run_io.to_execute_chains_input(bundle, config, directory))
    agent._write(directory / 'output.json', result)
    agent._write(directory / 'status.json', {'status': 'completed', 'seconds': time.monotonic()-started})
    summarize(directory)
    print(json.dumps(json.loads((directory / 'step3_progress.json').read_text()), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-run', type=Path)
    parser.add_argument('--task-id', action='append')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    main(args.output, args.source_run, args.task_id)
