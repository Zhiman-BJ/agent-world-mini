"""Score actual interrupted states with their saved verifiers; no agent retries."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_gen.task_eval import load_cases, _tools
from task_gen.task_eval_verifier import build_evidence, run_verifier, aggregate_results
from task_gen.tool_graph.llm import capture_calls, infer
from task_gen.tool_graph.run_io import load_config, append_llm_call

root = Path(sys.argv[1])
cases = {c.source_run.name + '__' + c.task['task_id']: c for c in load_cases(Path(
    '/data1/home/tianfang/project/agentworld_20260901_175404/agent-world-mini-zhiman-latest/runs/taskgen'))}
config = load_config(Path('config/task_eval_qwen.yaml')).llm
for path in sorted(root.glob('*/result.json')):
    result = json.loads(path.read_text())
    if result['outcome'] != 'error' or not result.get('verifier_path'):
        continue
    folder = path.parent
    case = cases[folder.name]
    calls = [json.loads(x) for x in (folder / 'tool_calls.jsonl').read_text().splitlines()]
    package = json.loads((folder / 'verifier.json').read_text())['verifier']
    evidence = build_evidence(case.initial_state, folder / 'state', calls, result.get('answer', ''), 65536)
    started = time.perf_counter()
    outcome = {'agent_error': result['error'], 'tool_calls': len(calls)}
    try:
        with capture_calls('task_eval.interrupted_state_verifier', lambda r: append_llm_call(folder, r)):
            checks = run_verifier(package, evidence, initial_state=case.initial_state,
                                  final_state=folder / 'state', tools=list(_tools(case.environment).values()),
                                  semantic_infer_fn=infer, llm_config=config, task_text=case.task['task_text'])
        outcome['evaluation'] = aggregate_results(package['requirements'], checks)
    except Exception as error:
        outcome['verifier_error'] = f'{type(error).__name__}: {error}'
    outcome['seconds'] = time.perf_counter() - started
    (folder / 'interrupted_state_verification.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
    (folder / 'interrupted_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(folder.name, json.dumps(outcome, ensure_ascii=False), flush=True)
