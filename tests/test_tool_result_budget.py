import json
import unittest
from copy import deepcopy

from task_gen.tool_graph.step_3_chain_execute import _bounded_calls


class ToolResultBudgetTests(unittest.TestCase):
    def test_bounds_unicode_and_wide_results_without_mutating_evidence(self):
        calls = [
            {'tool': 'small', 'result': {'success': True, 'id': 'small'}},
            {'tool': 'large', 'result': {'success': True, 'id': 'keep-me',
                                       'text': '中文"\\' * 10000,
                                       'rows': list(range(10000))}},
            {'tool': 'wide', 'result': {str(i): '中文' * 100 for i in range(1000)}},
        ]
        original = deepcopy(calls)
        projected = _bounded_calls(calls, 1024)
        for call in projected:
            self.assertLessEqual(len(json.dumps(call['result'], ensure_ascii=False,
                                               separators=(',', ':')).encode()), 1024)
        self.assertEqual(projected[0], original[0])
        self.assertIn('keep-me', json.dumps(projected[1]))
        self.assertTrue(projected[1]['result']['_truncated'])
        self.assertIn('_truncated', json.dumps(projected[1]['result']['data']['rows']))
        self.assertEqual(calls, original)
