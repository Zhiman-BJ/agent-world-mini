"""Load the six PRD cases and materialize their existing reference executions."""
from __future__ import annotations

import json
from pathlib import Path

from task_gen.eval_cases import load_cases
from task_gen.task_eval_verifier import prepare_verification_workspace
from .generation.pipeline import write_json
from .environment_runtime import prepare_runtime

DEFAULT_DATA_ROOT=Path('/data1/agent_world/task_program/complete_10_task_environments_20261005')
CASES={
 'S21':('semiconductor_scenario_07_07_02','semiconductor_scenario_07_07_02_program_000'),
 'S24':('semiconductor_symbolic_analysis_1','semiconductor_symbolic_analysis_1_program_005'),
 'S91':('semiconductor_scenario_02_10_01_0181','semiconductor_scenario_02_10_01_0181_program_002'),
 'S56':('semiconductor_scenario_01_04_02_0030','sic_oxygen_pl6_research_program_005'),
 'S81':('semiconductor_scenario_07_03_04_0439','peled_interfacial_analysis_program_009'),
 'S71':('semiconductor_scenario_08_02_w1_0514','semiconductor_scenario_08_02_w1_0514_program_003'),
}


def load_selected(data_root: Path, names: list[str]):
    unknown=set(names)-CASES.keys()
    if unknown: raise ValueError(f'Unknown cases: {sorted(unknown)}')
    selected={f'{CASES[n][0]}__{CASES[n][1]}' for n in names}
    loaded=load_cases(data_root,selected_case_names=selected)
    by_id={c.task['task_id']:c for c in loaded}
    if set(by_id)!={CASES[n][1] for n in names}: raise ValueError('Selected PRD case missing from dataset')
    return [(n,by_id[CASES[n][1]]) for n in names]


def full_reference_trace(case):
    path=case.source_run/'step2_task_solution.jsonl'
    if path.is_file():
        for line in path.open():
            item=json.loads(line).get('item',{})
            if item.get('task_id')==case.task['task_id'] and isinstance(item.get('solution_trace'),list):
                return item['solution_trace']
    return case.reference_calls


def prepare_evidence(case, out: Path, *, actual_state: Path | None = None,
                     actual_answer: str | None = None, actual_calls: list | None = None,
                     execution: dict | None = None) -> Path:
    if case.reference_state is None: raise ValueError('Reference state missing')
    reference_trace=full_reference_trace(case)
    answer=case.task.get('reference',{}).get('answer','')
    candidate_state=actual_state or case.reference_state
    candidate_answer=answer if actual_answer is None else actual_answer
    candidate_calls=reference_trace if actual_calls is None else actual_calls
    execution=execution or {'source':'existing reference run; assumed ground truth'}
    fallback={'task':case.task,'environment':case.environment,'initial_state':str(case.initial_state),
        'actual_state':str(candidate_state),'calls':candidate_calls,'answer':candidate_answer,'execution':execution,
        'reference_state':str(case.reference_state),'reference_calls':reference_trace,'reference_answer':answer,
        'reference_initial_state':str(case.initial_state)}
    paths={k:Path(v) for k,v in fallback.items() if k.endswith('_state')}
    evidence={**fallback,**paths}
    prepare_verification_workspace(out,**evidence)
    # Fallback reads the copied bundle too, so moving it never revives source paths.
    fallback.update(initial_state='evidence/initial', actual_state='evidence/actual/final',
                    reference_state='evidence/reference/final', reference_initial_state='evidence/reference/initial')
    request={'task_id':case.task['task_id'],'task_text':case.task['task_text'],
        'environment':'evidence/environment.json','tools':'evidence/tools.json','initial_state':'evidence/initial',
        'actual':{'final_state':'evidence/actual/final','answer':'evidence/actual/answer.txt','calls':'evidence/actual/tool_calls.jsonl','execution':'evidence/actual/execution.json'},
        'reference':{'initial_state':'evidence/reference/initial','final_state':'evidence/reference/final','answer':'evidence/reference/answer.txt','calls':'evidence/reference/tool_calls.jsonl'}}
    write_json(out/'program_request.json',request)
    write_json(out/'fallback_input.json',fallback)
    prepare_runtime(case, out)
    return out
