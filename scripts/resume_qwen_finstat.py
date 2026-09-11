"""Resume saved failed requests on copied states, keeping raw API responses."""
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_gen.task_eval_react import run_react_agent
from task_gen.task_eval import load_cases
from task_gen.task_eval_verifier import build_evidence, run_verifier, aggregate_results
from task_gen.tool_graph import llm
from task_gen.tool_graph.run_io import load_config, append_llm_call

root = Path('runs/qwen_finstat_resume') / datetime.now().strftime('%Y%m%d_%H%M%S')
root.mkdir(parents=True)
config = load_config(Path('config/task_eval_qwen.yaml')).llm
cases = {c.source_run.name+'__'+c.task['task_id']: c for c in load_cases(Path('/data1/home/tianfang/project/agentworld_20260901_175404/agent-world-mini-zhiman-latest/runs/taskgen'))}
print(root.resolve(), flush=True)
for previous in sorted(Path('runs/qwen_existing_verifiers/20260910_132108').glob('*finstat*')):
    folder = root / previous.name
    folder.mkdir()
    shutil.copytree(previous/'state', folder/'state')
    for name in ('tool_calls.jsonl', 'server.json', 'verifier.json', 'task.json'):
        shutil.copyfile(previous/name, folder/name)
    requests = [json.loads(x) for x in (previous/'state.agent/llm_calls.jsonl').read_text().splitlines()]
    failed = requests[-1]
    assert failed['error'] and failed['answer'] is None
    (folder/'resume_request.json').write_text(json.dumps(failed, ensure_ascii=False, indent=2))
    case = cases[previous.name]
    started = time.perf_counter()
    result = {'task_id': case.task['task_id'], 'resumed_from': str(previous), 'error': None}

    class RawClient:
        model = config['model']
        client = OpenAI(base_url=config['base_url'], api_key='local-only-no-auth', timeout=3600)

        def complete_messages(self, messages, on_delta=None, **parameters):
            response = self.client.chat.completions.create(model=self.model, messages=messages, **parameters)
            with (folder/'raw_responses.jsonl').open('a') as f:
                f.write(response.model_dump_json()+'\n')
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise RuntimeError('Empty content; complete response saved in raw_responses.jsonl')
            return content, response.usage.model_dump() if response.usage else {}

    try:
        with patch.object(llm, '_client', lambda _: RawClient()):
            answer = run_react_agent('', folder/'state', folder/'server.json', folder/'tool_calls.jsonl', config, resume_request=failed)
        result['answer'] = answer
        (folder/'agent_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    calls = [json.loads(x) for x in (folder/'tool_calls.jsonl').read_text().splitlines()]
    result['tool_calls_total'] = len(calls)
    result['tool_calls_added'] = len(calls) - len((previous/'tool_calls.jsonl').read_text().splitlines())
    package = json.loads((folder/'verifier.json').read_text())['verifier']
    evidence = build_evidence(case.initial_state, folder/'state', calls, result.get('answer',''), 65536)
    try:
        with llm.capture_calls('resumed_verifier', lambda r: append_llm_call(folder,r)):
            checks = run_verifier(package,evidence,initial_state=case.initial_state,final_state=folder/'state',tools=json.loads((folder/'server.json').read_text())['tools'],semantic_infer_fn=llm.infer,llm_config=config,task_text=case.task['task_text'])
        result['evaluation'] = aggregate_results(package['requirements'],checks)
    except Exception as error:
        result['verifier_error'] = str(error)
    result['seconds'] = time.perf_counter()-started
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(previous.name, result.get('error'),result.get('evaluation',{}).get('outcome'),flush=True)
