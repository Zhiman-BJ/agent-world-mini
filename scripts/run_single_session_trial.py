"""Small real Step3 trial; reuses frozen Step2 candidates, never production state."""
import json
from dataclasses import asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.tool_graph import execution_agent as agent, run_io
from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import capture_calls


def main():
    source = sorted((ROOT.parent.parent / 'runs/semiconductor_160_20260919_step3_514331a/semiconductor_scenario_01_06_01').glob('*/run.json'))[-1].parent
    bundle = json.loads((source / 'intermediate/step_2_bundle.json').read_text())
    # Same historically expensive task6, plus two real spare candidates.
    by_id = {t['task_id']: t for t in bundle['tasks']}
    bundle['tasks'] = [by_id[i] for i in ('task6', 'task1', 'task2')]
    values = json.loads((source / 'run.json').read_text())['config']
    for key in ('environment_dir', 'schema_dir', 'output_root'):
        values[key] = Path(values[key])
    values['llm'].update(model='gpt-5.6-sol', reasoning_effort='low', timeout_seconds=600, review_choice_seed=42)
    values['planning']['keep_top_count'] = 2
    values['execution'].update(min_tool_calls=11, target_tool_calls=20, max_concurrency=2,
        retry_count=1, enable_web_search=True, agent_max_tool_calls=60,
        tool_timeout_seconds=120, tool_max_memory_bytes=4*1024**3)
    values['execution'].pop('max_rounds', None)
    values['execution'].pop('max_repairs_per_round', None)
    config = Config(**values)
    directory = ROOT / 'runs/single_session_trial'
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
    print(json.dumps(json.loads((directory / 'step3_progress.json').read_text()), ensure_ascii=False))


if __name__ == '__main__':
    main()
