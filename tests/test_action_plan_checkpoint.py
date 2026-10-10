import json
from pathlib import Path
import tempfile
import unittest
from env_gen.tool_gen.compiler import ToolGenerator
from tests.test_tool_gen import FakeAgent, ToolGenV2Tests


class ExistingCheckpointAgent(FakeAgent):
    def run_until_files(self, prompt, *, working_directory, required_paths):
        if all(path.is_file() for path in required_paths):
            return ''
        return self.run(prompt, working_directory=working_directory)


class ActionPlanCheckpointTests(unittest.TestCase):
    def test_invalid_plan_is_preserved_and_repaired_before_drafting(self):
        for field, value in [('sideEffects', 'changes status'), ('targetObjects', [])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                package = ToolGenV2Tests().make_package(Path(directory))
                ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
                path = package / 'tool_generation/action_plan.json'
                broken = json.loads(path.read_text())
                broken['actions'][0]['usageConditions'][field] = value
                path.write_text(json.dumps(broken))
                agent = ExistingCheckpointAgent()
                result = ToolGenerator(agent, max_repairs=0).generate(package)
                self.assertEqual(set(result.tool_names), {'get_ticket', 'resolve_ticket'})
                backup = path.with_name('action_plan.invalid.1.json')
                self.assertEqual(json.loads(backup.read_text()), broken)
                self.assertNotEqual(json.loads(path.read_text()), broken)
                self.assertTrue(any('action_plan.invalid.1.json' in prompt for prompt in agent.calls))

    def test_valid_plan_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ToolGenV2Tests().make_package(Path(directory))
            ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
            agent = ExistingCheckpointAgent()
            ToolGenerator(agent, max_repairs=0).generate(package)
            self.assertEqual(agent.calls, [])
            self.assertFalse(list((package / 'tool_generation').glob('action_plan.invalid.*.json')))


if __name__ == '__main__':
    unittest.main()
