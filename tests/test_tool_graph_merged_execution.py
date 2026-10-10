"""Step3 resumes execution for review and retains the accepted round."""
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
                *[{'tool': 'lookup', 'arguments': {}, 'result': {'success': True}, 'error': None} for _ in range(10)],
            ]

            def run(client, prompt, working_directory):
                client.session_id = client.session_id or str(working_directory)
                client.turns = getattr(client, 'turns', 0) + 1
                server = json.loads(client.server_config.read_text())
                self.assertEqual(client.enable_web_search, config.execution.get('enable_web_search', True))
                failed = 'failed' in working_directory.parts
                payload = {'objective': 'Find suitable object', 'reason': 'Checked actual results.',
                           'completed': not failed, 'answer': 'Unavailable' if failed else 'Found', 'score': 0 if failed else 4}
                if client.turns == 2:
                    return json.dumps({**payload, 'call_indices': [1, *range(3, 13)],
                                       'chain_changes': 'Retry from the original state.' if failed else 'Keep the verified chain.',
                                       'restart': failed})
                self.assertEqual((Path(server['workspace']) / 'data').read_text(), 'original')
                with Path(server['trace']).open('a') as stream:
                    stream.write('\n'.join(json.dumps(r) for r in records) + '\n')
                (Path(server['workspace']) / 'data').write_text('changed')
                return json.dumps(payload)

            config = Config(environment_dir=root / 'env', planning={'keep_top_count': 1},
                            execution={'min_tool_calls': 11, 'target_tool_calls': 11})
            candidates = [{'task_id': i, 'chain': ['lookup'], 'objective': 'Find suitable object', 'score': 1}
                          for i in ('failed', 'second', 'pending')]
            with patch('task_gen.tool_graph.execution_agent._ReviewClient.run', new=run):
                output = execute_candidates({'config': config, 'run_dir': root / 'run', 'environment': {'tools': [tool]}, 'tasks': candidates})
            self.assertEqual(len(output['tasks']), 2)
            failed, accepted = output['tasks']
            self.assertFalse(failed['execution']['success'])
            self.assertEqual(len(failed['execution']['attempts']), 2)
            self.assertTrue(failed['execution']['final_state'].endswith('rounds/02/final'))
            self.assertTrue(accepted['execution']['success'])
            self.assertEqual(accepted['chain'], ['lookup'] * 11)
            self.assertEqual(len(accepted['execution']['raw_tool_calls']), 12)
            self.assertFalse(accepted['execution']['tool_calls'][0]['result']['success'])
            self.assertEqual((root / 'env/state/data').read_text(), 'original')
            self.assertFalse((root / 'run/tasks/pending').exists())
            config.execution['enable_web_search'] = False
            with patch('task_gen.tool_graph.execution_agent._ReviewClient.run', new=run):
                output = execute_candidates({'config': config, 'run_dir': root / 'search_run', 'environment': {'tools': [tool]}, 'tasks': candidates[1:2]})
            self.assertTrue(output['tasks'][0]['execution']['success'])


if __name__ == '__main__':
    unittest.main()
