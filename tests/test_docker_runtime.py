from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from env_gen.tool_gen.delivery_contract import load_delivery
from env_gen.tool_gen.docker_runtime import dockerfile, image_name
from env_gen.tool_gen.kimi_mcp import kimi_config


class DockerRuntimeTests(unittest.TestCase):
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
                    "method": "tools/call",
                    "params": {
                        "name": "inspect_environment_resource",
                        "arguments": {"ref": "aw://reports/daily.json"},
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
            self.assertTrue(all(not item["result"]["isError"] for item in responses))
            self.assertEqual(responses[1]["result"]["structuredContent"]["data"]["status"], "resolved")
            self.assertIn("done", responses[3]["result"]["structuredContent"]["data"]["text_preview"])
            sandbox = root / "run/sandbox"
            with sqlite3.connect(sandbox / "state/records.sqlite") as connection:
                self.assertEqual(
                    connection.execute('SELECT status FROM "tickets"').fetchone()[0],
                    "resolved",
                )
            receipt = json.loads((sandbox / "session.json").read_text())
            self.assertEqual(receipt["tool_calls"], 4)
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


if __name__ == "__main__":
    unittest.main()
