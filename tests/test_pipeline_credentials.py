import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph import llm, review_agent


class PipelineCredentialsTest(unittest.TestCase):
    def test_api_uses_explicit_file_without_changing_parent_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            auth = Path(temporary) / 'auth.json'
            auth.write_text(json.dumps({'OPENAI_API_KEY': 'pipeline-test-key'}))
            before = dict(os.environ)
            client = llm._client({'api_key_file': str(auth), 'model': 'test',
                                  'base_url': 'https://example.test/v1'})
            self.assertEqual(client.api_key, 'pipeline-test-key')
            self.assertEqual(dict(os.environ), before)
            auth.unlink()
            with self.assertRaises(ValueError):
                llm._client({'api_key_file': str(auth)})

    def test_review_passes_pipeline_codex_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            state.mkdir()
            auth_home = Path(temporary) / 'pipeline-codex'
            def run(client, prompt, *, working_directory):
                self.assertEqual(client.codex_home, auth_home.resolve())
                return '{}'
            with patch.object(review_agent.CodexAgentClient, 'run', run):
                review_agent.review_with_initial_state(['review'],
                    llm_config={'codex_home': str(auth_home)}, initial_workspace=state)
