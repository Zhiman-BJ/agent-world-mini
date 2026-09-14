from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

from env_gen.tool_gen.compiler import ToolGenerator, ToolGenerationError
from env_gen.tool_gen.software import expanded_packages, prepare_software
from tests import test_tool_gen as fixtures
from tests.test_tool_gen import FakeAgent, tool


class SoftwareTests(unittest.TestCase):
    def test_versions_and_standard_library(self):
        self.assertEqual(expanded_packages({"python_packages": [{"name": "numpy", "version": ">=2,<3"}],
            "common_modules": ["json", "matplotlib"]}), ["numpy>=2,<3", "matplotlib"])
        with self.assertRaises(ValueError):
            expanded_packages({"python_packages": [{"name": "spglib", "version": "随包提供"}]})

    def test_install_error_returns_to_agent_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "tool_generation"
            output.mkdir()
            (output / "software_plan.json").write_text('{}')
            class RepairAgent:
                def run(self, prompt, *, working_directory):
                    assert 'source_research.json' in prompt
                    (working_directory / 'software_plan.json').write_text('{"python":"3.11"}')
                    (working_directory / 'software_repair_1.json').write_text('{"status":"ready"}')
            calls = []
            def install(package):
                plan = json.loads((output / 'software_plan.json').read_text())
                calls.append(plan)
                if plan.get('python') != '3.11':
                    raise RuntimeError('requires Python >=3.11')
            with patch('env_gen.tool_gen.software.prepare_software', side_effect=install):
                ToolGenerator(RepairAgent())._prepare_software(root)
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads((output / 'software_status.json').read_text())['status'], 'ready')

    def test_exhausted_repairs_preserve_plan_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'tool_generation'
            output.mkdir()
            (output / 'software_plan.json').write_text('{}')
            class Agent:
                def run(self, prompt, *, working_directory):
                    pass
            with patch('env_gen.tool_gen.software.prepare_software', side_effect=RuntimeError('no network')) as install:
                with self.assertRaises(ToolGenerationError):
                    ToolGenerator(Agent(), software_repair_attempts=1)._prepare_software(root)
            self.assertEqual(install.call_count, 2)
            self.assertEqual(json.loads((output / 'software_status.json').read_text())['status'], 'blocked')
            self.assertTrue((output / 'software_plan.json').exists())

    def test_validation_executes_with_selected_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixtures.ToolGenV2Tests().make_package(root)
            ToolGenerator(FakeAgent()).generate(package)
            selected = root / 'selected-python'
            venv.EnvBuilder(with_pip=True).create(selected)
            executable = selected / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            subprocess.run([str(executable), '-m', 'pip', 'install', 'jsonschema>=4.18'], check=True, stdout=subprocess.DEVNULL)
            code = 'import sysconfig; print(sysconfig.get_path("purelib"))'
            site = Path(subprocess.check_output([str(executable), '-c', code], text=True).strip())
            site.mkdir(parents=True, exist_ok=True)
            (site / 'toolgen_probe_library.py').write_text('VALUE = 731\n')
            with self.assertRaises(ModuleNotFoundError):
                __import__('toolgen_probe_library')
            (package / 'tool_generation/software_environment.json').write_text(json.dumps({
                'python': str(executable), 'prefix': str(selected), 'root': str(package / 'tool_generation/software')}))
            probe = tool('get_ticket', 'import toolgen_probe_library\ndef run(arguments, context):\n    return {"success": True, "data": {"value": toolgen_probe_library.VALUE}}',
                         {'value': {'type': 'integer'}}, ['value'])
            drafts = [{'tool': probe, 'tests': [{'calls': [{'tool': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}],
                      'expect_success': True, 'expect_changed': False, 'expected_data': {'value': 731}}]}]
            env = json.loads((package / 'environment.json').read_text())
            report = ToolGenerator(FakeAgent())._validate(package, env, drafts)
            self.assertEqual(report[0]['status'], 'passed', report)


if __name__ == '__main__':
    unittest.main()
