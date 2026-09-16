"""The merged stage uses actual observations, including unsuccessful exploration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.execution_agent import execute_candidates
from task_gen.tool_graph.step_2_chain_sample import sample_chains


class MergedExecutionTest(unittest.TestCase):
    def test_objectives_enter_step3_without_review_or_final_selection(self):
        candidates = [{'chain': ['lookup'], 'objective': str(i), 'score': 1} for i in range(3)]
        with patch('task_gen.tool_graph.step_2_chain_sample._sample_candidates', return_value=([], {})), \
             patch('task_gen.tool_graph.step_2_chain_sample._tools', return_value=([], [])), \
             patch('task_gen.tool_graph.step_2_chain_sample._generate_objectives', return_value=(candidates, [])), \
             patch('task_gen.tool_graph.step_2_chain_sample._deduplicate_objectives', return_value=(candidates, {})), \
             patch('task_gen.tool_graph.step_2_chain_sample._review_chains', side_effect=AssertionError('review in step2')):
            output = sample_chains({'config': Config(planning={'keep_top_count': 1}), 'environment': {}})
        self.assertEqual(len(output['tasks']), 3)

    def test_real_chain_preserves_negative_observation_and_isolates_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'env/state').mkdir(parents=True)
            (root / 'env/state/data').write_text('original')
            tool = {'name': 'lookup', 'description': 'lookup', 'inputSchema': {'type': 'object'},
                    'outputSchema': {'type': 'object', 'properties': {'success': {'type': 'boolean'}}},
                    'internal': {'code': 'def run(arguments, context): return {}'}}
            records = [
                {'tool': 'lookup', 'arguments': {}, 'result': {'success': False, 'error': {'code': 'not_found'}}, 'error': '工具返回值必须包含 success=true'},
                {'tool': 'lookup', 'arguments': {}, 'result': None, 'error': 'timeout'},
                {'tool': 'lookup', 'arguments': {}, 'result': {'success': True}, 'error': None},
            ]
            def run(client, prompt, working_directory):
                self.assertEqual(client.enable_web_search, config.execution.get('enable_web_search', False))
                server = json.loads(client.server_config.read_text())
                Path(server['trace']).write_text('\n'.join(json.dumps(r) for r in records))
                (Path(server['workspace']) / 'data').write_text('changed')
                return json.dumps({'reason': 'Unavailable object, found another.', 'objective': 'Find suitable object',
                                   'completed': working_directory.parent.name != 'failed', 'answer': 'Found', 'score': 4})
            config = Config(environment_dir=root / 'env', planning={'keep_top_count': 1})
            candidates = [{'task_id': i, 'chain': ['lookup'], 'objective': 'Find suitable object', 'score': 1}
                          for i in ('first', 'second', 'failed')]
            with patch('task_gen.tool_graph.execution_agent._ReviewClient.run', new=run):
                output = execute_candidates({'config': config, 'run_dir': root / 'run', 'environment': {'tools': [tool]}, 'tasks': candidates})
            self.assertEqual(len(output['tasks']), 2)
            for candidate in output['tasks']:
                self.assertEqual(candidate['chain'], ['lookup', 'lookup'])
                self.assertEqual(len(candidate['execution']['raw_tool_calls']), 3)
            self.assertFalse(output['tasks'][-1]['execution']['success'])
            self.assertEqual((root / 'env/state/data').read_text(), 'original')
            self.assertTrue(json.loads((root / 'run/tasks/second/agent_result.json').read_text())['execution']['success'])
            config.execution['enable_web_search'] = True
            with patch('task_gen.tool_graph.execution_agent._ReviewClient.run', new=run):
                output = execute_candidates({'config': config, 'run_dir': root / 'search_run', 'environment': {'tools': [tool]}, 'tasks': candidates[:1]})
            self.assertTrue(output['tasks'][0]['execution']['success'])


if __name__ == '__main__':
    unittest.main()
