from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph import llm
from task_gen.tool_graph import review_agent


class ReviewAgentTest(unittest.TestCase):
    def test_reviews_read_independent_copies_and_preserve_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "workspace"
            source.mkdir()
            (source / "state.json").write_text('{"existing": 2}')
            directories, records = [], []

            def run(client, prompt, *, working_directory):
                directories.append(working_directory)
                self.assertNotEqual(working_directory, source)
                self.assertEqual(client.sandbox, "read-only")
                self.assertEqual(client.reasoning_effort, "low")
                self.assertFalse(client.enable_web_search)
                self.assertIn('features.shell_tool=false', client._llm_arguments({}))
                self.assertIn('--json', client._llm_arguments({}))
                self.assertEqual(list(working_directory.iterdir()), [])
                self.assertEqual((working_directory.parent / "state/state.json").read_text(), '{"existing": 2}')
                log = client.log_directory / "run_01"
                log.mkdir(parents=True)
                (log / "stderr.log").write_text("read state.json: existing=2")
                (log / "stdout.log").write_text('{"type":"thread.started","thread_id":"test"}\n{"type":"item.completed","item":{"type":"agent_message","text":"observed"}}\n')
                return '{"accepted":true,"chain":["query"],"reason":"Two existing records"}'

            with patch.object(review_agent.CodexAgentClient, "run", run), llm.capture_calls("review", records.append):
                results = review_agent.review_with_initial_state(
                    ["first", "second"], llm_config={"max_concurrency": 2, "reasoning_effort": "low"}, initial_workspace=source,
                )
            self.assertEqual(len(results), 2)
            self.assertEqual(len(set(directories)), 2)
            self.assertTrue(all(not directory.exists() for directory in directories))
            self.assertEqual((source / "state.json").read_text(), '{"existing": 2}')
            self.assertEqual(len(records), 2)
            self.assertTrue(all(record["agent_log"]["stderr"] == "read state.json: existing=2" for record in records))
            self.assertEqual({record["batch_index"] for record in records}, {0, 1})
            self.assertTrue(all(len(record['agent_log']['events']) == 2 for record in records))
            self.assertEqual(records[0]['agent_log']['events'][0]['thread_id'], 'test')
            self.assertIn('tools', records[0]['agent_log']['server_config'])

    def test_mutated_copy_fails_only_its_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "state.json").write_text('{}')

            def run(client, prompt, *, working_directory):
                if prompt.endswith("\n\nmutate"):
                    (working_directory.parent / "state/state.json").write_text('{"changed": true}')
                return '{}'

            with patch.object(review_agent.CodexAgentClient, "run", run):
                with self.assertRaises(llm.BatchInferenceError) as caught:
                    review_agent.review_with_initial_state(
                        ["mutate", "read"], llm_config={"max_concurrency": 2}, initial_workspace=source,
                    )
            self.assertIsInstance(caught.exception.outcomes[0], ValueError)
            self.assertIsInstance(caught.exception.outcomes[1], llm.InferenceResult)
            self.assertEqual((source / "state.json").read_text(), '{}')

    def test_missing_workspace_and_symlinks_do_not_launch_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(review_agent.CodexAgentClient, "run") as run:
                with self.assertRaises(ValueError):
                    review_agent.review_with_initial_state(["review"], llm_config={}, initial_workspace=root / "missing")
                (root / "link").symlink_to(root / "missing")
                with self.assertRaises(ValueError):
                    review_agent.review_with_initial_state(["review"], llm_config={}, initial_workspace=root)
                run.assert_not_called()

    def test_agent_failure_keeps_trace_and_cleans_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            directories, records = [], []

            def run(client, prompt, *, working_directory):
                directories.append(working_directory)
                log = client.log_directory / 'run_01'
                log.mkdir(parents=True)
                (log / 'stdout.log').write_text('{"type":"turn.started"}\n{"partial":')
                (working_directory.parent / 'tool_calls.jsonl').write_text('{"tool":"query","result":{}}\n{"partial":')
                raise TimeoutError("agent timed out")

            with patch.object(review_agent.CodexAgentClient, "run", run), llm.capture_calls("review", records.append):
                with self.assertRaises(llm.BatchInferenceError):
                    review_agent.review_with_initial_state(["review"], llm_config={}, initial_workspace=Path(temporary))
            self.assertTrue(all(not directory.exists() for directory in directories))
            self.assertEqual(records[0]["status"], "failed")
            self.assertIn("timed out", records[0]["error"])
            self.assertEqual(records[0]['agent_log']['events'], [{'type': 'turn.started'}])
            self.assertEqual(records[0]['agent_log']['unparsed_event_lines'], ['{"partial":'])
            self.assertEqual(len(records[0]['agent_log']['tool_calls']), 1)
            self.assertEqual(records[0]['agent_log']['unparsed_tool_lines'], ['{"partial":'])


if __name__ == "__main__":
    unittest.main()
