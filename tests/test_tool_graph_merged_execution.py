"""Step2 retains all objective candidates for the deficit scheduler."""
import unittest
from unittest.mock import patch
from task_gen.tool_graph.contracts import Config
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
