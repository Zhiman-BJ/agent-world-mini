"""Run the newest two historical task groups, never generate a verifier."""
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import zip_longest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_gen.task_eval import load_cases, _agent_prompt, _run_agent, _tools, _workspace_signature
from task_gen.task_eval_verifier import build_evidence, run_verifier, aggregate_results, validate_verifier
from task_gen.tool_graph.llm import capture_calls, infer
from task_gen.tool_graph.run_io import load_config, append_llm_call


def main():
    source = Path('/data1/home/tianfang/project/agentworld_20260901_175404/agent-world-mini-zhiman-latest/runs/taskgen')
    old = Path('/data1/home/tianfang/agent-world-mini-zhiman/.worktrees/task-execution-verifier/runs/task_eval')
    output = Path('runs/qwen_existing_verifiers') / datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True)
    config = load_config(Path('config/task_eval_qwen.yaml')).llm
    cases = load_cases(source)
    groups = sorted({c.source_run.name for c in cases}, reverse=True)[:2]
    selected = [[c for c in cases if c.source_run.name == group] for group in groups]
    queue = [c for pair in zip_longest(*selected) for c in pair if c is not None]
    verifiers = {}
    for p in sorted(old.glob('*/verifiers/*.json')):
        verifiers[p.name] = p

    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    manifest = []
    for c in queue:
        name = c.source_run.name + '__' + c.task['task_id']
        vp = verifiers.get(name + '.json')
        if vp:
            validate_verifier(json.loads(vp.read_text())['verifier'])
        manifest.append({'name': name, 'verifier_path': str(vp) if vp else None})
    save(output / 'manifest.json', manifest)
    print('OUTPUT=' + str(output.resolve()), flush=True)

    def run(item):
        index, case = item
        entry = manifest[index]
        folder = output / entry['name']
        folder.mkdir()
        state = folder / 'state'
        initial_signature = _workspace_signature(case.initial_state)
        shutil.copytree(case.initial_state, state)
        trace = folder / 'tool_calls.jsonl'
        tools = list(_tools(case.environment).values())
        server = folder / 'server.json'
        save(server, {'tools': tools, 'environment': case.environment, 'max_tool_calls': 50,
                      'timeout': 300, 'memory_limit': 2147483648, 'write_limit': 268435456})
        save(folder / 'task.json', case.task)
        if entry['verifier_path']:
            shutil.copyfile(entry['verifier_path'], folder / 'verifier.json')
        started = time.perf_counter()
        result = {'task_id': case.task['task_id'], 'source_run': case.source_run.name,
                  'verifier_path': entry['verifier_path'], 'error': None}
        print('START ' + entry['name'], flush=True)
        try:
            answer = _run_agent(_agent_prompt(case, 50), state, server, trace, config)
            result['agent_seconds'] = time.perf_counter() - started
            result['answer'] = answer
            save(folder / 'agent_result.json', result)
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            result['tool_calls'] = len(calls)
            result['tool_errors'] = sum(c['error'] is not None for c in calls)
            if entry['verifier_path']:
                package = json.loads((folder / 'verifier.json').read_text())['verifier']
                evidence = build_evidence(case.initial_state, state, calls, answer, 65536)
                with capture_calls('task_eval.existing_verifier', lambda r: append_llm_call(folder, r)):
                    checks = run_verifier(package, evidence, initial_state=case.initial_state,
                                          final_state=state, tools=tools, semantic_infer_fn=infer,
                                          llm_config=config, task_text=case.task['task_text'])
                result['evaluation'] = aggregate_results(package['requirements'], checks)
                result['outcome'] = result['evaluation']['outcome']
                save(folder / 'evidence.json', evidence)
            else:
                result['outcome'] = 'unscored_missing_verifier'
        except Exception as error:
            result['outcome'] = 'error'
            result['error'] = f'{type(error).__name__}: {error}'
        finally:
            assert _workspace_signature(case.initial_state) == initial_signature, 'Source initial state changed'
            result['seconds'] = time.perf_counter() - started
            save(folder / 'result.json', result)
        print('DONE ' + entry['name'] + ' ' + result['outcome'], flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, enumerate(queue)))
    save(output / 'results.json', results)
    print('COMPLETE ' + str(output), flush=True)


if __name__ == '__main__':
    main()
