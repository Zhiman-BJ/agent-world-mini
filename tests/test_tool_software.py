from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

from env_gen.tool_gen.compiler import ToolGenerator, ToolGenerationError
from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime
from env_gen.tool_gen.software import (
    DEFAULT_PYPI_INDEX,
    OFFICIAL_PYPI_INDEX,
    expanded_packages,
    prepare_software,
    software_download_environment,
    inspect_system_packages,
    validate_in_runtime,
)
from tests import test_tool_gen as fixtures
from tests.test_tool_gen import FakeAgent, tool


class SoftwareTests(unittest.TestCase):
    def test_reused_profile_rechecks_declared_system_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            output = package / "tool_generation"
            software_root = output / "software"
            software_root.mkdir(parents=True)
            plan = {
                "system_packages": [
                    {"name": "required tool", "executable": "missing-tool"}
                ]
            }
            (output / "software_plan.json").write_text(json.dumps(plan))
            (output / "software_environment.json").write_text(json.dumps({
                "python": sys.executable,
                "prefix": sys.prefix,
                "root": str(software_root),
                "plan": plan,
            }))

            with self.assertRaisesRegex(RuntimeError, "required tool"):
                prepare_software(package)

            status = json.loads(
                (output / "software_system_status.json").read_text()
            )
            self.assertEqual(status["status"], "blocked")

    def test_system_package_probe_includes_multiarch_library_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "system/usr/bin/example-tool"
            library = root / "system/usr/lib/x86_64-linux-gnu"
            binary.parent.mkdir(parents=True)
            library.mkdir(parents=True)
            binary.write_text(
                "#!/bin/sh\n"
                f"case :$LD_LIBRARY_PATH: in *:{library}:*) echo example-tool 1.0;; "
                "*) echo missing library path >&2; exit 1;; esac\n"
            )
            binary.chmod(0o755)

            status = inspect_system_packages(
                root,
                {"system_packages": [{"name": "example", "executable": "example-tool"}]},
            )

            self.assertEqual(status["status"], "ready")

    def test_system_package_probe_requires_declared_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            executable = root / "bin" / "iverilog"
            executable.write_text("#!/bin/sh\necho iverilog version\nexit 0\n")
            executable.chmod(0o755)
            status = inspect_system_packages(
                root,
                {"system_packages": ["iverilog", "verilator"]},
            )
            self.assertEqual(status["status"], "blocked")
            self.assertEqual(status["missing"], ["verilator"])
            self.assertEqual(
                {item["name"]: item["status"] for item in status["checks"]},
                {"iverilog": "ready", "verilator": "missing"},
            )

    def test_system_package_probe_accepts_explicit_python_import(self):
        status = inspect_system_packages(
            Path(sys.prefix),
            {
                "system_packages": [
                    {"name": "probe", "python_import": "json"},
                ]
            },
            python=sys.executable,
        )
        self.assertEqual(status["status"], "ready")

    def test_download_environment_uses_shared_cache_and_mirror_fallback(self):
        environment = software_download_environment({}, shared_root=Path("/shared"))
        self.assertEqual(environment["UV_INDEX"], DEFAULT_PYPI_INDEX)
        self.assertEqual(environment["UV_DEFAULT_INDEX"], OFFICIAL_PYPI_INDEX)
        self.assertEqual(environment["PIP_INDEX_URL"], DEFAULT_PYPI_INDEX)
        self.assertEqual(environment["PIP_EXTRA_INDEX_URL"], OFFICIAL_PYPI_INDEX)
        self.assertEqual(environment["UV_CACHE_DIR"], "/shared/cache/uv")
        self.assertEqual(
            environment["UV_PYTHON_INSTALL_DIR"], "/shared/interpreters"
        )

    def test_download_environment_preserves_explicit_configuration(self):
        environment = software_download_environment(
            {
                "UV_INDEX": "https://packages.example/simple",
                "PIP_INDEX_URL": "https://packages.example/simple",
                "UV_CACHE_DIR": "/existing/cache",
            },
            shared_root=Path("/shared"),
        )
        self.assertEqual(environment["UV_INDEX"], "https://packages.example/simple")
        self.assertEqual(
            environment["PIP_INDEX_URL"], "https://packages.example/simple"
        )
        self.assertEqual(environment["UV_CACHE_DIR"], "/existing/cache")

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
            software_root = package / 'tool_generation/software'
            software_root.mkdir()
            (package / 'tool_generation/software_environment.json').write_text(json.dumps({
                'python': str(executable), 'prefix': str(selected), 'root': str(software_root)}))
            probe = tool('get_ticket', 'import toolgen_probe_library\ndef run(arguments, context):\n    return {"success": True, "data": {"value": toolgen_probe_library.VALUE}}',
                         {'value': {'type': 'integer'}}, ['value'])
            drafts = [{'tool': probe, 'tests': [{'calls': [{'tool': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}],
                      'expect_success': True, 'expect_changed': False, 'expected_data': {'value': 731}}]}]
            env = json.loads((package / 'environment.json').read_text())
            report = ToolGenerator(FakeAgent())._validate(package, env, drafts)
            self.assertEqual(report[0]['status'], 'passed', report)

    def test_runtime_validation_starts_a_clean_process_for_each_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "tool_generation").mkdir()
            drafts = [
                {
                    "tool": {"name": "first_tool"},
                    "tests": [{"calls": [{"tool": "first_tool", "arguments": {}}]}],
                },
                {
                    "tool": {"name": "second_tool"},
                    "tests": [{
                        "calls": [
                            {"tool": "first_tool", "arguments": {}},
                            {"tool": "second_tool", "arguments": {}},
                        ]
                    }],
                },
            ]
            targets = []

            def command(arguments, **_kwargs):
                request = json.loads(Path(arguments[-2]).read_text(encoding="utf-8"))
                targets.append(request["target_tool"])
                Path(arguments[-1]).write_text(json.dumps([{
                    "tool": request["target_tool"],
                    "status": "passed",
                    "failures": [],
                    "tests": next(
                        item["tests"] for item in request["drafts"]
                        if item["tool"]["name"] == request["target_tool"]
                    ),
                }]), encoding="utf-8")
                return ""

            runtime = {
                "python": "/selected/python",
                "prefix": "/selected",
                "root": "/selected",
            }
            with patch("env_gen.tool_gen.software.runtime_info", return_value=runtime), \
                 patch("env_gen.tool_gen.software._command", side_effect=command):
                reports = validate_in_runtime(package, drafts)

            self.assertEqual(targets, ["first_tool", "second_tool"])
            self.assertEqual([item["tool"] for item in reports], targets)
            saved = json.loads((package / "tool_generation/runtime_validation_output.json").read_text())
            self.assertEqual(saved, reports)

    def test_tool_runtime_uses_the_prepared_software_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixtures.ToolGenV2Tests().make_package(root)
            ToolGenerator(FakeAgent()).generate(package)
            prepared = root / "shared-software-profile"
            prepared.mkdir()
            with patch.dict(os.environ, {"TOOLGEN_SOFTWARE_ROOT": str(prepared)}):
                with ToolRuntime(ToolPackage.load(package)) as runtime:
                    self.assertEqual(runtime.context.software_root, prepared)

    def test_runtime_validation_continues_after_one_process_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "tool_generation").mkdir()
            drafts = [
                {"tool": {"name": name}, "tests": []}
                for name in ("broken_tool", "working_tool")
            ]

            def command(arguments, **_kwargs):
                request = json.loads(Path(arguments[-2]).read_text(encoding="utf-8"))
                if request["target_tool"] == "broken_tool":
                    raise RuntimeError("process exited")
                Path(arguments[-1]).write_text(json.dumps([{
                    "tool": "working_tool",
                    "status": "passed",
                    "failures": [],
                    "tests": [],
                }]), encoding="utf-8")
                return ""

            runtime = {"python": "/selected/python", "prefix": "/selected", "root": "/selected"}
            with patch("env_gen.tool_gen.software.runtime_info", return_value=runtime), \
                 patch("env_gen.tool_gen.software._command", side_effect=command):
                reports = validate_in_runtime(package, drafts)

            self.assertEqual([item["status"] for item in reports], ["rejected", "passed"])
            self.assertIn("runtime_process_error", reports[0]["failures"][0])


if __name__ == '__main__':
    unittest.main()
