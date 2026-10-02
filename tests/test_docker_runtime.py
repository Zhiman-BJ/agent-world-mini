from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from env_gen.tool_gen.delivery_contract import load_delivery
from env_gen.tool_gen.docker_runtime import dockerfile, image_name
from env_gen.tool_gen.kimi_mcp import kimi_config


class DockerRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('TOOLGEN_DOCKER_TEST_IMAGE'), '设置测试镜像后执行')
    def test_existing_container_mcp_launch_remains_executable(self):
        from env_gen.tool_gen.runtime_launch import docker_stdio_launch
        from tests.test_kimi_mcp import KimiMcpTests
        import env_gen.tool_gen.kimi_mcp as adapter

        with tempfile.TemporaryDirectory(dir=os.environ.get('TOOLGEN_DOCKER_TEST_ROOT')) as tmp:
            root = Path(tmp)
            binding = KimiMcpTests()._make_delivery(root, with_report_tool=True)
            document = json.loads(binding.read_text())
            runtime = root / 'delivery' / document['runtime_path']
            runtime.write_text(json.dumps({'schema_version': '1.0', 'backend': 'docker',
                'image': os.environ['TOOLGEN_DOCKER_TEST_IMAGE'], 'python_command': 'python',
                'software_root': '/opt/tool-software', 'delivery_mount': '/delivery', 'code_mount': '/opt/agent-world'}))
            delivery = load_delivery(binding)
            run = root / 'run'
            run.mkdir()
            session = run / 'sandbox'
            trace = run / 'calls.jsonl'
            launch = docker_stdio_launch(delivery, server_path=Path(adapter.__file__),
                arguments=[str(Path(adapter.__file__)), str(binding), '--session-root', str(session),
                           '--trace', str(trace), '--max-tool-calls', '5'],
                writable_paths=(session, trace))
            for name in ('resolve_ticket', 'get_ticket'):
                request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': name, 'arguments': {'ticket_id': 'ticket-1'}}}
                result = subprocess.run([str(launch.command), *launch.arguments],
                    input=json.dumps(request)+'\n', text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                response = json.loads(result.stdout.strip())
                self.assertFalse(response['result']['isError'], response)
                self.assertEqual(response['result']['structuredContent']['data']['status'], 'resolved')
            self.assertEqual(json.loads((session / 'session.json').read_text())['tool_calls'], 2)

    @unittest.skipUnless(os.environ.get('TOOLGEN_DOCKER_BUILD_TEST_BASE'), '设置测试基础镜像后执行')
    def test_container_recipe_is_prepared_validated_and_published(self):
        from types import SimpleNamespace
        from env_gen.tool_gen.compiler import ToolGenerator
        from env_gen.tool_gen.delivery import publish
        from env_gen.tool_gen.mcp_server import ToolMcpServer
        from tests.test_kimi_mcp import KimiMcpTests

        with tempfile.TemporaryDirectory(dir=os.environ.get('TOOLGEN_DOCKER_TEST_ROOT')) as tmp:
            root = Path(tmp)
            KimiMcpTests()._make_delivery(root)
            source = root / 'support'
            output = source / 'tool_generation'
            output.mkdir(exist_ok=True)
            plan = {'python': '3.13', 'python_packages': [], 'system_packages': ['kicad'],
                    'container': {'base_image': os.environ['TOOLGEN_DOCKER_BUILD_TEST_BASE'], 'apt_packages': []}}
            (output / 'software_plan.json').write_text(json.dumps(plan))
            generator = ToolGenerator(None, software_repair_attempts=0)
            generator._prepare_software(source)
            environment = json.loads((source / 'environment.json').read_text())
            tools = json.loads((source / 'tools.json').read_text())['tools']
            drafts = [{'tool': tool, 'tests': [{'calls': [{'tool': tool['name'], 'arguments': {'ticket_id': 'ticket-1'}}],
                       'expect_success': True, 'expect_changed': tool['name'] == 'resolve_ticket',
                       'expected_data': {'status': 'resolved' if tool['name'] == 'resolve_ticket' else 'open'}}]}
                      for tool in tools]
            reports = generator._validate(source, environment, drafts)
            self.assertTrue(all(report['status'] == 'passed' for report in reports), reports)
            receipt = json.loads((output / 'container_runtime.json').read_text())
            probe = subprocess.run(['docker', 'run', '--rm', '--runtime', 'runc', '--network', 'none',
                receipt['image'], 'python', '-c',
                "import subprocess; subprocess.run(['/opt/tool-software/bin/kicad-cli','version'],check=True)"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            result = SimpleNamespace(package_root=source, environment_path=source/'environment.json',
                tools_path=source/'tools.json', validation_path=source/'tool_validation.json',
                grounding_path=source/'tool_grounding.json', action_plan_path=source/'action_plan.json')
            binding = publish(result, root/'container-delivery').binding_path
            delivery = load_delivery(binding)
            session = root / 'run/sandbox'
            with ToolMcpServer(delivery, session_root=session) as server:
                updated = server.handle({'method': 'tools/call', 'params': {'name': 'resolve_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
                self.assertFalse(updated['isError'], updated)
            with ToolMcpServer(delivery, session_root=session) as server:
                read = server.handle({'method': 'tools/call', 'params': {'name': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
                self.assertEqual(read['structuredContent']['data']['status'], 'resolved')

    @unittest.skipUnless(os.environ.get('TOOLGEN_DOCKER_BUILD_TEST_BASE'), '设置测试基础镜像后执行')
    def test_image_with_unloadable_executable_is_not_ready(self):
        from env_gen.tool_gen.compiler import ToolGenerator, ToolGenerationError

        with tempfile.TemporaryDirectory(dir=os.environ.get('TOOLGEN_DOCKER_TEST_ROOT')) as tmp:
            source = Path(tmp)
            output = source / 'tool_generation'
            output.mkdir()
            plan = {'python': '3.13', 'python_packages': [],
                    'system_packages': [{'name': 'runtime check', 'executable': 'runtime-check'}],
                    'container': {'base_image': os.environ['TOOLGEN_DOCKER_BUILD_TEST_BASE'], 'apt_packages': []}}
            (output / 'software_plan.json').write_text(json.dumps(plan))
            (output / 'container_runtime.json').write_text(json.dumps({'backend': 'docker', 'image': 'older-image'}))
            with self.assertRaises(ToolGenerationError):
                ToolGenerator(None, software_repair_attempts=0)._prepare_software(source)
            self.assertFalse((output / 'container_runtime.json').exists())
            self.assertEqual(json.loads((output/'software_status.json').read_text())['status'], 'blocked')

    def test_renders_explicit_professional_software_recipe(self) -> None:
        plan = {
            "python": "3.11",
            "common_modules": [],
            "python_packages": ["numpy==2.1.0"],
            "node_packages": [],
            "container": {
                "base_image": "python:3.11-slim-bookworm",
                "apt_packages": ["ngspice", "libglu1-mesa"],
                "environment": {"QT_QPA_PLATFORM": "offscreen"},
            },
        }
        recipe = dockerfile(plan)
        self.assertIn("FROM python:3.11-slim-bookworm", recipe)
        self.assertIn("ENTRYPOINT []", recipe)
        self.assertIn("libglu1-mesa ngspice", recipe)
        self.assertIn('ENV QT_QPA_PLATFORM="offscreen"', recipe)
        self.assertIn("TOOLGEN_SOFTWARE_ROOT=/opt/tool-software", recipe)
        self.assertTrue(image_name(plan).startswith("agentworld/tool-runtime:"))

    def test_container_environment_is_applied_before_install_and_discovery(self):
        recipe = dockerfile({'python_packages': [], 'system_packages': ['kicad'],
            'container': {'apt_packages': [], 'environment': {'PATH': '/opt/kicad/bin:/usr/bin'}}})
        self.assertLess(recipe.index('ENV PATH='), recipe.index('RUN python -m pip'))
        self.assertLess(recipe.index('ENV PATH='), recipe.index('for candidate in kicad-cli'))

    def test_does_not_guess_container_packages(self) -> None:
        with self.assertRaisesRegex(ValueError, "container 配置"):
            dockerfile({"python_packages": ["numpy"]})

    def test_image_identity_ignores_non_runtime_purpose_text(self) -> None:
        first = {
            "python": "3.11",
            "python_packages": [{"name": "numpy", "version": "2.1.0", "purpose": "A"}],
            "container": {"apt_packages": []},
        }
        second = {
            "python": "3.11",
            "python_packages": [{"name": "numpy", "version": "2.1.0", "purpose": "B"}],
            "container": {"apt_packages": []},
        }
        self.assertEqual(image_name(first), image_name(second))

    @unittest.skipUnless(
        os.environ.get("TOOLGEN_DOCKER_TEST_IMAGE"),
        "设置 TOOLGEN_DOCKER_TEST_IMAGE 后运行真实 Docker 会话测试",
    )
    def test_task_container_preserves_final_state_and_is_removed(self) -> None:
        from tests.test_kimi_mcp import KimiMcpTests

        image = os.environ["TOOLGEN_DOCKER_TEST_IMAGE"]
        test_root = os.environ.get("TOOLGEN_DOCKER_TEST_ROOT")
        with tempfile.TemporaryDirectory(dir=test_root) as temporary:
            root = Path(temporary)
            binding = KimiMcpTests()._make_delivery(root, with_report_tool=True)
            document = json.loads(binding.read_text(encoding="utf-8"))
            runtime_path = root / "delivery" / document["runtime_path"]
            runtime_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "backend": "docker",
                        "image": image,
                        "delivery_mount": "/delivery",
                        "code_mount": "/opt/agent-world",
                        "software_root": "/opt/tool-software",
                        "python_command": "python",
                    }
                ),
                encoding="utf-8",
            )
            delivery = load_delivery(binding)
            trace = root / "run/tool_calls.jsonl"
            entry = kimi_config(
                delivery,
                server_name="agent_world_docker_test",
                trace_path=trace,
                max_tool_calls=4,
            )["mcpServers"]["agent_world_docker_test"]
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "resolve_ticket",
                        "arguments": {"ticket_id": "ticket-1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "get_ticket",
                        "arguments": {"ticket_id": "ticket-1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "write_report",
                        "arguments": {"path": "daily.json", "status": "done"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                        "method": "resources/read",
                        "params": {
                            "uri": "aw://reports/daily.json",
                        },
                },
            ]
            before = set(
                subprocess.check_output(
                    ["docker", "ps", "-aq", "--filter", f"ancestor={image}"],
                    text=True,
                ).split()
            )
            result = subprocess.run(
                [entry["command"], *entry["args"]],
                input="\n".join(json.dumps(item) for item in requests),
                capture_output=True,
                text=True,
                cwd=entry["cwd"],
                env={**os.environ, **entry["env"]},
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(responses), 4)
            self.assertTrue(all(not item["result"]["isError"] for item in responses[:3]))
            self.assertEqual(responses[1]["result"]["structuredContent"]["data"]["status"], "resolved")
            self.assertIn("done", responses[3]["result"]["contents"][0]["text"])
            sandbox = root / "run/sandbox"
            with sqlite3.connect(sandbox / "state/records.sqlite") as connection:
                self.assertEqual(
                    connection.execute('SELECT status FROM "tickets"').fetchone()[0],
                    "resolved",
                )
            receipt = json.loads((sandbox / "session.json").read_text())
            self.assertEqual(receipt["tool_calls"], 3)
            contents = (sandbox / "state/filesystem_scopes/reports/daily.json").read_bytes()
            self.assertEqual(json.loads(contents)["status"], "done")
            self.assertEqual(
                receipt["final_snapshot"]["filesystem_scopes"]["reports"]["daily.json"],
                {"sha256": hashlib.sha256(contents).hexdigest(), "size_bytes": len(contents)},
            )
            self.assertEqual(
                (delivery.package.package_root / "state/filesystem_scopes/reports/daily.json").read_text(),
                '{"status":"open"}',
            )
            self.assertEqual((sandbox / "session.json").stat().st_uid, os.getuid())
            after = set(
                subprocess.check_output(
                    ["docker", "ps", "-aq", "--filter", f"ancestor={image}"],
                    text=True,
                ).split()
            )
            self.assertEqual(after, before)

    @unittest.skipUnless(
        os.environ.get("TOOLGEN_DOCKER_TEST_IMAGE"),
        "设置 TOOLGEN_DOCKER_TEST_IMAGE 后运行真实 Docker 会话测试",
    )
    def test_task_eval_mcp_uses_delivery_docker_image(self) -> None:
        from distill.runner import _server_config
        from task_gen.task_eval_mcp import TaskEvalMcpServer
        from tests.test_kimi_mcp import KimiMcpTests

        image = os.environ["TOOLGEN_DOCKER_TEST_IMAGE"]
        with tempfile.TemporaryDirectory(dir=os.environ.get("TOOLGEN_DOCKER_TEST_ROOT")) as temporary:
            root = Path(temporary)
            binding = KimiMcpTests()._make_delivery(root, with_report_tool=True)
            document = json.loads(binding.read_text(encoding="utf-8"))
            runtime_path = root / "delivery" / document["runtime_path"]
            runtime_path.write_text(json.dumps({
                "schema_version": "1.0", "backend": "docker", "image": image,
                "delivery_mount": "/delivery", "code_mount": "/opt/agent-world",
                "software_root": "/opt/tool-software", "python_command": "python",
            }), encoding="utf-8")
            tools_path = root / "delivery" / document["tools_path"]
            tool_document = json.loads(tools_path.read_text(encoding="utf-8"))
            for tool in tool_document["tools"]:
                if tool["name"] == "get_ticket":
                    tool["internal"]["code"] = (
                        "from pathlib import Path\n"
                        "import os, pwd\n"
                        "assert Path('/opt/tool-software').is_dir()\n"
                        "assert pwd.getpwuid(os.getuid()).pw_name == 'tool'\n"
                        + tool["internal"]["code"]
                    )
            tools_path.write_text(json.dumps(tool_document), encoding="utf-8")
            delivery = load_delivery(binding)
            state = root / "task_state"
            shutil.copytree(delivery.package.package_root / "state", state)
            config = _server_config({
                "binding_path": str(binding), "state_root": str(state),
                "trace": str(root / "task_calls.jsonl"), "max_tool_calls": 3,
                "timeout": 30, "memory_limit": 2 * 1024**3,
                "write_limit": 256 * 1024**2,
                "tools": list(delivery.package.tools),
            })
            self.assertEqual(config["runtime"]["backend"], "docker")
            server = TaskEvalMcpServer(config)
            update = server.handle({
                "method": "tools/call", "params": {
                    "name": "resolve_ticket", "arguments": {"ticket_id": "ticket-1"},
                },
            })
            self.assertFalse(update["isError"], update)
            response = server.handle({
                "method": "tools/call", "params": {
                    "name": "get_ticket", "arguments": {"ticket_id": "ticket-1"},
                },
            })
            self.assertFalse(response["isError"], response)
            self.assertEqual(response["structuredContent"]["data"]["status"], "resolved")
            report = server.handle({
                "method": "tools/call", "params": {
                    "name": "write_report", "arguments": {"path": "daily.json", "status": "done"},
                },
            })
            self.assertFalse(report["isError"], report)
            with sqlite3.connect(state / "records.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT status FROM tickets").fetchone()[0], "resolved")
            with sqlite3.connect(delivery.package.package_root / "state/records.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT status FROM tickets").fetchone()[0], "open")
            self.assertEqual(
                json.loads((state / "filesystem_scopes/reports/daily.json").read_text())["status"], "done",
            )
            self.assertEqual(
                json.loads((delivery.package.package_root / "state/filesystem_scopes/reports/daily.json").read_text())["status"],
                "open",
            )


if __name__ == "__main__":
    unittest.main()
