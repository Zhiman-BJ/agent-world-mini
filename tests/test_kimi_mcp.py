from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from env_gen.tool_gen.delivery import publish
from env_gen.tool_gen.kimi_mcp import kimi_config, load_delivery, serve


def _closed_object(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _output_schema() -> dict[str, object]:
    return {
        "type": "object",
        "oneOf": [
            _closed_object(
                {
                    "success": {"type": "boolean", "const": True},
                    "data": _closed_object(
                        {
                            "ticket_id": {"type": "string"},
                            "status": {"type": "string", "enum": ["open", "resolved"]},
                        },
                        ["ticket_id", "status"],
                    ),
                },
                ["success", "data"],
            ),
            _closed_object(
                {
                    "success": {"type": "boolean", "const": False},
                    "error": _closed_object(
                        {
                            "code": {"type": "string", "enum": ["not_found"]},
                            "path": {"type": "string"},
                            "message": {"type": "string", "minLength": 1},
                            "retryable": {"type": "boolean"},
                        },
                        ["code", "path", "message", "retryable"],
                    ),
                },
                ["success", "error"],
            ),
        ]
    }


def _tool(name: str, code: str, description: str, side_effects: list[str]) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "usageConditions": {
            "targetResources": ["tickets"],
            "targetObjects": [
                {"objectType": "support ticket", "identifiedBy": ["ticket_id"]}
            ],
            "preconditions": ["The ticket exists in the current environment."],
            "sideEffects": side_effects,
        },
        "inputSchema": _closed_object(
            {"ticket_id": {"type": "string", "minLength": 1}}, ["ticket_id"]
        ),
        "outputSchema": _output_schema(),
        "internal": {"code": code},
    }


GET_TICKET = '''
def run(arguments, context):
    record = context.records.get("tickets", {"ticket_id": arguments["ticket_id"]})
    if record is None:
        return {
            "success": False,
            "error": {
                "code": "not_found",
                "path": "$.ticket_id",
                "message": "Ticket not found.",
                "retryable": False,
            },
        }
    return {"success": True, "data": {"ticket_id": record["ticket_id"], "status": record["status"]}}
'''


RESOLVE_TICKET = '''
def run(arguments, context):
    record = context.records.get("tickets", {"ticket_id": arguments["ticket_id"]})
    if record is None:
        return {
            "success": False,
            "error": {
                "code": "not_found",
                "path": "$.ticket_id",
                "message": "Ticket not found.",
                "retryable": False,
            },
        }
    context.records.update("tickets", {"ticket_id": arguments["ticket_id"]}, {"status": "resolved"})
    return {"success": True, "data": {"ticket_id": record["ticket_id"], "status": "resolved"}}
'''


class KimiMcpTests(unittest.TestCase):
    def test_real_venv_interpreter_mapping_preserves_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = self._make_delivery(root, with_software_profile=True)
            source = root / 'support'
            software = source / 'tool_generation/software'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', '--clear', str(software / 'python')], check=True)
            # publish again using an actual venv launcher, which is normally a symlink.
            result = SimpleNamespace(package_root=source, environment_path=source / 'environment.json',
                tools_path=source / 'tools.json', validation_path=source / 'tool_validation.json',
                grounding_path=source / 'tool_grounding.json', action_plan_path=source / 'action_plan.json')
            binding = publish(result, root / 'real_delivery').binding_path
            delivery = load_delivery(binding)
            prefix = subprocess.check_output([str(delivery.python_path), '-I', '-c', 'import sys; print(sys.prefix)'], text=True).strip()
            self.assertEqual(Path(prefix), delivery.software_root / 'python')

    def _make_delivery(self, root: Path, *, with_software_profile: bool = False) -> Path:
        source = root / "support"
        (source / "state/filesystem_scopes/reports").mkdir(parents=True)
        environment = {
            "schema_version": "2.0",
            "environment_id": "support_workspace",
            "name": "Support workspace",
            "summary": "A support workspace for ticket resolution.",
            "description": (
                "This environment contains support tickets and an editable report directory "
                "for realistic ticket review and resolution workflows."
            ),
            "record_sets": [
                {
                    "record_set_id": "tickets",
                    "name": "Tickets",
                    "description": "One record represents a support ticket.",
                    "access": "copy_on_write",
                    "key_fields": ["ticket_id"],
                    "fields": {
                        "ticket_id": {
                            "type": "string",
                            "description": "Stable ticket ID.",
                            "nullable": False,
                        },
                        "status": {
                            "type": "string",
                            "description": "Current ticket status.",
                            "nullable": False,
                        },
                    },
                }
            ],
            "relationships": [],
            "filesystem_scopes": [
                {
                    "scope_id": "reports",
                    "name": "Reports",
                    "description": "Editable support reports.",
                    "access": "copy_on_write",
                    "structure": {
                        "kind": "directory",
                        "path": ".",
                        "layout": [
                            {
                                "kind": "file_collection",
                                "path": "*.json",
                                "description": "Support reports.",
                                "required": False,
                                "format": "json",
                            }
                        ],
                    },
                }
            ],
        }
        (source / "environment.json").write_text(
            json.dumps(environment), encoding="utf-8"
        )
        (source / "validation.json").write_text(
            json.dumps({"valid": True}), encoding="utf-8"
        )
        with sqlite3.connect(source / "state/records.sqlite") as connection:
            connection.execute(
                'CREATE TABLE "tickets" ("ticket_id" TEXT NOT NULL, "status" TEXT NOT NULL) STRICT'
            )
            connection.execute(
                'CREATE UNIQUE INDEX "ux_tickets_key" ON "tickets" ("ticket_id")'
            )
            connection.execute('INSERT INTO "tickets" VALUES (?, ?)', ("ticket-1", "open"))
            connection.commit()

        tools = [
            _tool("get_ticket", GET_TICKET, "Read one support ticket.", []),
            _tool(
                "resolve_ticket",
                RESOLVE_TICKET,
                "Resolve one support ticket.",
                ["The ticket status becomes resolved."],
            ),
        ]
        tools_path = source / "tools.json"
        tools_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "environment_id": "support_workspace",
                    "tools": tools,
                }
            ),
            encoding="utf-8",
        )
        validation_path = source / "tool_validation.json"
        validation_path.write_text(
            json.dumps(
                {
                    "environment_id": "support_workspace",
                    "reports": [
                        {"tool": tool["name"], "status": "passed"} for tool in tools
                    ],
                }
            ),
            encoding="utf-8",
        )
        grounding_path = source / "tool_grounding.json"
        grounding_path.write_text(json.dumps({"tools": []}), encoding="utf-8")
        action_plan_path = source / "action_plan.json"
        action_plan_path.write_text(json.dumps({"actions": []}), encoding="utf-8")
        if with_software_profile:
            software_root = source / "tool_generation/software"
            python = software_root / "python/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source / "tool_generation/software_environment.json").write_text(
                json.dumps(
                    {
                        "root": str(software_root),
                        "python": str(python),
                        "prefix": str(software_root / "python"),
                        "plan": {
                            "python": "3.12",
                            "python_packages": ["jsonschema>=4.18"],
                            "node_packages": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
        result = SimpleNamespace(
            package_root=source,
            environment_path=source / "environment.json",
            tools_path=tools_path,
            validation_path=validation_path,
            grounding_path=grounding_path,
            action_plan_path=action_plan_path,
        )
        return publish(result, root / "delivery").binding_path

    def test_kimi_stdio_session_lists_tools_and_preserves_state_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = self._make_delivery(root)
            trace = root / "calls.jsonl"
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "resolve_ticket",
                        "arguments": {"ticket_id": "ticket-1"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "get_ticket",
                        "arguments": {"ticket_id": "ticket-1"},
                    },
                },
            ]
            stdin = io.StringIO("\n".join(json.dumps(item) for item in requests))
            stdout = io.StringIO()

            serve(binding, trace_path=trace, stdin=stdin, stdout=stdout)

            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual([item["id"] for item in responses], [1, 2, 3, 4])
            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
            listed = responses[1]["result"]["tools"]
            self.assertEqual(
                [item["name"] for item in listed],
                [
                    "get_environment_overview",
                    "list_environment_resources",
                    "inspect_environment_resource",
                    "get_ticket",
                    "resolve_ticket",
                ],
            )
            self.assertIn("Preconditions", listed[3]["description"])
            self.assertNotIn("internal", listed[3])
            self.assertEqual(listed[3]["outputSchema"]["type"], "object")
            self.assertEqual(
                responses[3]["result"]["structuredContent"]["data"]["status"],
                "resolved",
            )
            records = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual(records[0]["state_changes"]["record_sets"], ["tickets"])
            self.assertEqual(records[1]["state_changes"]["record_sets"], [])

            sandbox = root / "sandbox"
            receipt = json.loads((sandbox / "session.json").read_text())
            self.assertEqual(receipt["tool_calls"], 2)
            with sqlite3.connect(sandbox / "state/records.sqlite") as connection:
                sandbox_status = connection.execute(
                    'SELECT status FROM "tickets"'
                ).fetchone()[0]
            self.assertEqual(sandbox_status, "resolved")

            delivery = load_delivery(binding)
            with sqlite3.connect(
                delivery.package.package_root / "state/records.sqlite"
            ) as connection:
                status = connection.execute('SELECT status FROM "tickets"').fetchone()[0]
            self.assertEqual(status, "open")

    def test_resource_tools_expose_scope_files_without_workspace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = self._make_delivery(root)
            environment_root = (
                root / "delivery/environments/support/environment/state/filesystem_scopes/reports"
            )
            (environment_root / "daily.json").write_text(
                json.dumps({"status": "open"}), encoding="utf-8"
            )
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "get_environment_overview",
                        "arguments": {},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "list_environment_resources",
                        "arguments": {"scope_id": "reports", "query": "daily"},
                    },
                },
            ]
            stdout = io.StringIO()
            serve(
                binding,
                stdin=io.StringIO("\n".join(json.dumps(item) for item in requests)),
                stdout=stdout,
            )
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            overview = responses[0]["result"]["structuredContent"]["data"]
            self.assertEqual(overview["filesystem_scopes"][0]["file_count"], 1)
            resources = responses[1]["result"]["structuredContent"]["data"]["resources"]
            self.assertEqual(resources[0]["ref"], "aw://reports/daily.json")
            self.assertNotIn(str(root), json.dumps(resources))

    def test_business_failure_is_returned_as_structured_mcp_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binding = self._make_delivery(Path(temporary))
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_ticket",
                    "arguments": {"ticket_id": "missing"},
                },
            }
            stdout = io.StringIO()

            serve(binding, stdin=io.StringIO(json.dumps(request)), stdout=stdout)

            result = json.loads(stdout.getvalue())["result"]
            self.assertTrue(result["isError"])
            self.assertFalse(result["structuredContent"]["success"])
            self.assertEqual(result["structuredContent"]["error"]["code"], "not_found")

    def test_config_uses_the_delivery_binding_and_adapter_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = self._make_delivery(root)
            delivery = load_delivery(binding)

            config = kimi_config(
                delivery,
                server_name="agent_world_support",
                trace_path=root / "trace.jsonl",
                max_tool_calls=25,
            )

            entry = config["mcpServers"]["agent_world_support"]
            self.assertEqual(entry["transport"], "stdio")
            self.assertEqual(Path(entry["cwd"]), delivery.package.package_root)
            self.assertEqual(Path(entry["command"]), Path(sys.executable).absolute())
            self.assertEqual(Path(entry["args"][1]), binding)
            self.assertTrue(entry["args"][0].endswith("kimi_mcp.py"))
            self.assertIn("--trace", entry["args"])
            self.assertIn("--session-root", entry["args"])
            self.assertIn(str(root / "sandbox"), entry["args"])
            self.assertEqual(entry["args"][-2:], ["--max-tool-calls", "25"])

    def test_config_uses_bound_software_profile_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binding = self._make_delivery(
                Path(temporary), with_software_profile=True
            )
            delivery = load_delivery(binding)
            nested_library = delivery.software_root / "python/lib"
            nested_library.mkdir(parents=True, exist_ok=True)
            petsc_prefix = delivery.software_root / "python-3.11"
            (petsc_prefix / "lib/petsc").mkdir(parents=True)
            (petsc_prefix / "lib/slepc").mkdir(parents=True)

            config = kimi_config(
                delivery,
                server_name="agent_world_support",
                trace_path=None,
                max_tool_calls=10,
            )

            entry = config["mcpServers"]["agent_world_support"]
            command = Path(entry["command"])
            self.assertEqual(command, delivery.python_path)
            self.assertTrue(command.is_relative_to(delivery.delivery_root))
            self.assertEqual(delivery.software_root, command.parents[2])
            self.assertEqual(
                Path(entry["env"]["TOOLGEN_SOFTWARE_ROOT"]),
                delivery.software_root,
            )
            self.assertIn(str(delivery.python_path.parent), entry["env"]["PATH"])
            self.assertIn(str(nested_library), entry["env"]["LD_LIBRARY_PATH"])
            self.assertEqual(Path(entry["env"]["PETSC_DIR"]), petsc_prefix)
            self.assertEqual(Path(entry["env"]["SLEPC_DIR"]), petsc_prefix)

    def test_config_uses_declared_docker_image_and_translates_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = self._make_delivery(root)
            binding_document = json.loads(binding.read_text(encoding="utf-8"))
            runtime_path = root / "delivery" / binding_document["runtime_path"]
            runtime_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "backend": "docker",
                        "image": "agentworld/kicad:9.0",
                        "delivery_mount": "/delivery",
                        "code_mount": "/opt/agent-world",
                        "software_root": "/opt/tool-software",
                        "python_command": "python",
                    }
                ),
                encoding="utf-8",
            )
            delivery = load_delivery(binding)
            config = kimi_config(
                delivery,
                server_name="agent_world_kicad",
                trace_path=root / "traces/calls.jsonl",
                max_tool_calls=20,
            )

            entry = config["mcpServers"]["agent_world_kicad"]
            self.assertEqual(Path(entry["command"]).name, "docker")
            self.assertEqual(entry["args"][:3], ["run", "--rm", "-i"])
            self.assertIn("agentworld/kicad:9.0", entry["args"])
            self.assertIn(
                "/delivery/environments/support/binding.json", entry["args"]
            )
            self.assertTrue(
                any(
                    item.endswith("/env_gen/tool_gen/kimi_mcp.py")
                    and item.startswith("/opt/agent-world/")
                    for item in entry["args"]
                )
            )
            self.assertIn("/external/0/calls.jsonl", entry["args"])
            self.assertIn("/external/0/sandbox", entry["args"])
            external_mounts = [
                item
                for item in entry["args"]
                if item.startswith("type=bind,source=")
                and "target=/external/" in item
            ]
            self.assertEqual(len(external_mounts), 1)
            self.assertIn("--user", entry["args"])
            self.assertEqual(entry["env"], {})


if __name__ == "__main__":
    unittest.main()
