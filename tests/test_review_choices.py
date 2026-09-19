import unittest
import io
import json
from pathlib import Path
import tempfile
from task_gen.tool_graph.review_choices import ReviewChoices
from task_gen.task_eval_mcp import serve, TaskEvalMcpServer


class ChoicesTest(unittest.TestCase):
    def test_resume_restores_choice_state_and_call_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {'tools': [], 'workspace': str(root), 'trace': str(root / 'trace.jsonl'),
                      'max_tool_calls': 2, 'review_choice_seed': 42, 'resume_trace': True}
            request = {'method': 'tools/call', 'params': {'name': 'review_select_plan', 'arguments': {
                'question': '选择对象', 'options': [
                    {'description': '甲', 'basis': '记录甲'}, {'description': '乙', 'basis': '记录乙'}]}}}
            original = TaskEvalMcpServer(config)
            original.handle(request)
            resumed = TaskEvalMcpServer(config)
            self.assertEqual(resumed.calls, 1)
            self.assertEqual(resumed.handle(request), original.handle(request))
            with self.assertRaisesRegex(Exception, '上限'):
                resumed.handle(request)

    def test_mcp_choice_is_review_only_and_logged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {'tools': [], 'workspace': str(root), 'trace': str(root / 'trace.jsonl'),
                      'max_tool_calls': 10, 'review_choice_seed': 42}
            path = root / 'config.json'
            path.write_text(json.dumps(config))
            requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                'name': 'review_select_plan', 'arguments': {'question': '选择对象', 'options': [
                    {'description': '甲', 'basis': '记录甲'}, {'description': '乙', 'basis': '记录乙'}]}}}]
            output = io.StringIO()
            serve(path, io.StringIO('\n'.join(map(json.dumps, requests))), output)
            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(responses[0]['result']['tools'][0]['name'], 'review_select_plan')
            self.assertFalse(responses[1]['result']['isError'])
            self.assertEqual(json.loads((root / 'trace.jsonl').read_text())['result'], responses[1]['result']['structuredContent'])
            del config['review_choice_seed']
            path.write_text(json.dumps(config))
            output = io.StringIO()
            serve(path, io.StringIO(json.dumps(requests[0])), output)
            self.assertEqual(json.loads(output.getvalue())['result']['tools'], [])

    def test_replay_rollback_and_exhaustion(self):
        args = {'question': '对象范围', 'options': [
            {'description': '范围甲', 'basis': '查询甲'},
            {'description': '范围乙', 'basis': '查询乙'}]}
        chooser = ReviewChoices(42)
        first = chooser.choose(args)
        self.assertEqual(first, ReviewChoices(42).choose(args))
        second = chooser.choose(args)
        retry = chooser.choose({'choice_id': first['choice_id'], 'failure_evidence': '实际查询无适用对象'})
        self.assertNotEqual(first['selected'], retry['selected'])
        self.assertEqual(retry['invalidated'], [second['choice_id']])
        with self.assertRaises(ValueError):
            chooser.choose({'choice_id': second['choice_id'], 'failure_evidence': '已失效'})
        exhausted = chooser.choose({'choice_id': first['choice_id'], 'failure_evidence': '另一方案也无法实现'})
        self.assertTrue(exhausted['exhausted'])
        with self.assertRaises(ValueError):
            chooser.choose(args)
