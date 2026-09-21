"""Exercise the scheduler and round boundaries; only external inference is faked."""
import json
from pathlib import Path
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.execution_agent import execute_candidates, meaningful_calls, execution_prompt


def input_for(tmp_path, ids, keep):
    state=tmp_path/'env/state'
    state.mkdir(parents=True)
    (state/'data').write_text('original')
    tool={'name':'lookup','inputSchema':{'type':'object'},
          'outputSchema':{'type':'object','properties':{'success':{'type':'boolean'}}},
          'internal':{'code':'def run(a,c): return {}'}}
    return {'config':Config(environment_dir=tmp_path/'env',planning={'keep_top_count':keep},
        execution={'max_concurrency':4,'retry_count':0}),
        'run_dir':tmp_path/'run','environment':{'tools':[tool]},
        'tasks':[{'task_id':i,'objective':'Goal','chain':['lookup']} for i in ids]}


def fake_calls(counts):
    def run(client,prompt,working_directory):
        client.session_id=str(working_directory)
        server=json.loads(client.server_config.read_text())
        task=working_directory.parents[2].name
        round_no=int(working_directory.parent.name)
        count,completed=counts[task][round_no-1]
        assert (Path(server['workspace'])/'data').read_text()=='original'
        (Path(server['workspace'])/'data').write_text('changed')
        rows=[{'tool':'lookup','arguments':{'i':i},'result':{'success':True},'error':None} for i in range(count)]
        Path(server['trace']).write_text('\n'.join(map(json.dumps,rows))+'\n')
        return json.dumps(dict(objective='Goal',reason='Evidence',completed=completed,answer='Result',score=4))
    return run


def test_only_dispatch_missing_successes(tmp_path):
    inp=input_for(tmp_path,['a','b','c','d','unused'],3)
    outcomes={'a':[(20,False)],'b':[(20,True)],'c':[(20,True)],'d':[(20,True)]}
    with patch('task_gen.tool_graph.execution_agent._ReviewClient.run',new=fake_calls(outcomes)):
        result=execute_candidates(inp)
    assert [x['task_id'] for x in result['tasks']]==['a','b','c','d']
    assert sum(x['execution']['success'] for x in result['tasks'])==3
    assert not (inp['run_dir']/'tasks/unused').exists()
    progress=json.loads((inp['run_dir']/'step3_progress.json').read_text())
    assert [b['task_ids'] for b in progress['batches']]==[['a','b','c'],['d']]


def test_short_chain_restarts_once_and_only_final_round_counts(tmp_path):
    inp=input_for(tmp_path,['a'],1)
    with patch('task_gen.tool_graph.execution_agent._ReviewClient.run',new=fake_calls({'a':[(12,True),(11,True)]})):
        result=execute_candidates(inp)['tasks'][0]
    assert result['execution']['success']
    assert len(result['chain'])==11
    assert len(result['execution']['attempts'])==2
    assert result['execution']['final_state'].endswith('rounds/02/final')
    assert (tmp_path/'env/state/data').read_text()=='original'


def test_ten_calls_rejected_and_pool_exhaustion_is_reported(tmp_path):
    inp=input_for(tmp_path,['a'],2)
    with patch('task_gen.tool_graph.execution_agent._ReviewClient.run',new=fake_calls({'a':[(10,True),(10,True)]})):
        result=execute_candidates(inp)['tasks'][0]
    assert not result['execution']['success']
    assert len(result['execution']['attempts'])==2
    assert not (inp['run_dir']/'tasks/a/rounds/03').exists()
    progress=json.loads((inp['run_dir']/'step3_progress.json').read_text())
    assert progress['remaining_needed']==2 and progress['status']=='pool_exhausted'


def test_negative_observation_preserved_but_service_failure_excluded(tmp_path):
    inp=input_for(tmp_path,['a'],1)
    tool=inp['environment']['tools'][0]
    negative={'tool':'lookup','arguments':{},'result':{'success':False},'error':'not found'}
    timeout={'tool':'lookup','arguments':{},'result':None,'error':'timeout'}
    assert meaningful_calls([negative,timeout],{'lookup':tool})==[negative]
    prompt=execution_prompt(inp['tasks'][0],inp['environment'],enable_web_search=False)
    assert '实际调用内置网络搜索至少一次' not in prompt


def test_failed_retry_never_reuses_first_round_answer(tmp_path):
    inp=input_for(tmp_path,['a'],1)
    first=fake_calls({'a':[(12,True)]})
    def run(client,prompt,working_directory):
        if working_directory.parent.name=='02':
            raise RuntimeError('second round failed')
        return first(client,prompt,working_directory)
    with patch('task_gen.tool_graph.execution_agent._ReviewClient.run',new=run):
        result=execute_candidates(inp)['tasks'][0]
    assert not result['execution']['success']
    assert result['execution']['answer']==''
    assert result['execution']['error']=='second round failed'
