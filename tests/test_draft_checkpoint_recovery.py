import json
from pathlib import Path
import tempfile
import unittest

from env_gen.tool_gen.compiler import ToolGenerator
from tests.test_tool_gen import FakeAgent, ToolGenV2Tests, GET_CODE


class CheckpointAgent(FakeAgent):
    def run_until_files(self, prompt, *, working_directory, required_paths):
        if all(path.is_file() for path in required_paths):
            return ''
        return self.run(prompt, working_directory=working_directory)


class RetryableInterruption(RuntimeError):
    retryable = True


class InterruptedRepairAgent(FakeAgent):
    def __init__(self, repair):
        super().__init__(bad_lookup=True)
        self.repair = repair

    def run(self, prompt, *, working_directory):
        if '工具修复' in prompt:
            if self.repair:
                path = working_directory / 'drafts/get_ticket.json'
                draft = json.loads(path.read_text())
                draft['tool']['internal']['code'] = GET_CODE
                path.write_text(json.dumps(draft))
            self.calls.append(prompt)
            raise RetryableInterruption('session interrupted after writing drafts')
        return super().run(prompt, working_directory=working_directory)


class DraftCheckpointRecoveryTests(unittest.TestCase):
    def test_invalid_existing_draft_is_archived_before_completion_check(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ToolGenV2Tests().make_package(Path(directory))
            ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
            path = package / 'tool_generation/drafts/get_ticket.json'
            draft = json.loads(path.read_text())
            draft['tool']['inputSchema']['properties'] = {}
            path.write_text(json.dumps(draft))
            agent = CheckpointAgent()
            result = ToolGenerator(agent, max_repairs=0).generate(package)
            self.assertEqual(set(result.tool_names), {'get_ticket', 'resolve_ticket'})
            backup = package / 'tool_generation/draft_history/get_ticket.1.json'
            self.assertEqual(json.loads(backup.read_text()), draft)
            self.assertTrue(any('draft_condition_fields_missing:ticket_id' in prompt for prompt in agent.calls))

    def test_saved_repair_is_validated_after_retryable_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ToolGenV2Tests().make_package(Path(directory))
            result = ToolGenerator(InterruptedRepairAgent(True), max_repairs=1).generate(package)
            self.assertEqual(set(result.tool_names), {'get_ticket', 'resolve_ticket'})
            self.assertTrue(all(x['status'] == 'passed' for x in json.loads(result.validation_path.read_text())['reports']))
            self.assertTrue((package / 'tool_generation/repair_error.json').exists())

    def test_interruption_retains_rejection_when_draft_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ToolGenV2Tests().make_package(Path(directory))
            result = ToolGenerator(InterruptedRepairAgent(False), max_repairs=1).generate(package)
            self.assertEqual(result.tool_names, ('resolve_ticket',))
            reports = {x['tool']: x for x in json.loads(result.validation_path.read_text())['reports']}
            self.assertEqual(reports['get_ticket']['status'], 'rejected')

    def test_condition_errors_remain_visible_to_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            package = ToolGenV2Tests().make_package(Path(directory))
            ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
            output = package / 'tool_generation'
            path = output / 'drafts/get_ticket.json'
            draft = json.loads(path.read_text())
            draft['tool']['inputSchema']['properties'] = {}
            path.write_text(json.dumps(draft))
            generator = ToolGenerator(FakeAgent())
            environment = json.loads((package / 'environment.json').read_text())
            inventory = generator._load_capability_inventory(environment, output / 'capability_inventory.json')
            errors = {}
            _, loaded = generator._load_agent_drafts(environment, inventory, output / 'action_plan.json', output / 'drafts', allow_missing=True, draft_failures=errors)
            self.assertEqual(errors['get_ticket'], 'draft_condition_fields_missing:ticket_id')
            self.assertEqual([d['tool']['name'] for d in loaded], ['resolve_ticket'])


if __name__ == '__main__':
    unittest.main()
