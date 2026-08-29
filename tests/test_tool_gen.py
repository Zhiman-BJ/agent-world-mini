from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from env_gen.tool_gen import ToolGenerationError, ToolGenerator
from env_gen.tool_gen.compiler import _normalize_tests
from env_gen.tool_gen.__main__ import _reference_tools
from task_gen.program_form import CompleteEnvironmentPackage, CompleteEnvironmentRuntime


def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def output_schema(data: dict[str, object], required: list[str]) -> dict[str, object]:
    error = object_schema(
        {
            "code": {"type": "string", "enum": ["not_found", "invalid_state"]},
            "path": {"type": "string"},
            "message": {"type": "string", "minLength": 1},
            "retryable": {"type": "boolean"},
        },
        ["code", "path", "message", "retryable"],
    )
    return {
        "oneOf": [
            object_schema({"success": {"type": "boolean", "const": True}, "data": object_schema(data, required)}, ["success", "data"]),
            object_schema({"success": {"type": "boolean", "const": False}, "error": error}, ["success", "error"]),
        ]
    }


ASSIGN_CODE = '''
def run(arguments, context):
    import json
    tickets_path = context.workspace_root / "entities" / "tickets.json"
    agents_path = context.workspace_root / "entities" / "agents.json"
    tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    agents = json.loads(agents_path.read_text(encoding="utf-8"))
    ticket = next((row for row in tickets if row["ticket_id"] == arguments["ticket_id"]), None)
    if ticket is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.ticket_id", "message": "Ticket not found.", "retryable": False}}
    agent = next((row for row in agents if row["agent_id"] == arguments["agent_id"] and row["active"]), None)
    if agent is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.agent_id", "message": "Active agent not found.", "retryable": False}}
    if ticket["status"] == "resolved":
        return {"success": False, "error": {"code": "invalid_state", "path": "$.ticket_id", "message": "Resolved ticket cannot be assigned.", "retryable": False}}
    ticket["assignee_id"] = agent["agent_id"]
    tickets_path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "data": {"ticket_id": ticket["ticket_id"], "assignee_id": ticket["assignee_id"]}}
'''


RESOLVE_CODE = '''
def run(arguments, context):
    import json
    path = context.workspace_root / "entities" / "tickets.json"
    tickets = json.loads(path.read_text(encoding="utf-8"))
    ticket = next((row for row in tickets if row["ticket_id"] == arguments["ticket_id"]), None)
    if ticket is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.ticket_id", "message": "Ticket not found.", "retryable": False}}
    if not ticket["assignee_id"] or ticket["status"] != "open":
        return {"success": False, "error": {"code": "invalid_state", "path": "$.ticket_id", "message": "Ticket must be assigned and open.", "retryable": False}}
    ticket["status"] = "resolved"
    ticket["resolution"] = arguments["resolution"]
    path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "data": {"ticket_id": ticket["ticket_id"], "status": "resolved"}}
'''


LOOKUP_CODE = '''
def run(arguments, context):
    import json
    path = context.workspace_root / "entities" / "tickets.json"
    tickets = json.loads(path.read_text(encoding="utf-8"))
    ticket = next((row for row in tickets if row["ticket_id"] == arguments["ticket_id"]), None)
    if ticket is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.ticket_id", "message": "Ticket not found.", "retryable": False}}
    return {"success": True, "data": {"ticket_id": ticket["ticket_id"], "status": ticket["status"]}}
'''


class FakeToolAgent:
    def __init__(
        self,
        *,
        bad_write: bool = False,
        include_lookup: bool = False,
        repair_bad_write: bool = False,
        lookup_mutates: bool = False,
        omit_capability: bool = False,
        malformed_inventory: bool = False,
    ) -> None:
        self.bad_write = bad_write
        self.include_lookup = include_lookup
        self.repair_bad_write = repair_bad_write
        self.lookup_mutates = lookup_mutates
        self.omit_capability = omit_capability
        self.malformed_inventory = malformed_inventory
        self.calls: list[str] = []

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls.append(prompt)
        if "工具修复" in prompt:
            if self.repair_bad_write:
                self.bad_write = False
                self._write_draft(working_directory, "assign_ticket")
            (working_directory / "repair_done.json").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            return "repair complete"

        if "能力盘点文件修复" in prompt:
            path = working_directory / "capability_inventory.json"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    '"cap_assign_ticket"',
                    '"capability_id": "cap_assign_ticket"',
                    1,
                ),
                encoding="utf-8",
            )
            (working_directory / "inventory_repair_done.json").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            return "inventory repaired"

        if "负责盘点" in prompt:
            capabilities = [
                {
                    "capability_id": "cap_assign_ticket",
                    "name": "Assign a ticket",
                    "family": "state_change",
                    "resource_ids": ["tickets", "agents"],
                    "evidence": ["tickets.assignee_id relates to agents.agent_id"],
                    "reference_tools": ["assign_ticket"],
                    "decision": "implement",
                    "reason": "The ticket resource is writable.",
                },
                {
                    "capability_id": "cap_resolve_ticket",
                    "name": "Resolve a ticket",
                    "family": "state_change",
                    "resource_ids": ["tickets"],
                    "evidence": ["tickets has status and resolution fields"],
                    "reference_tools": [],
                    "decision": "implement",
                    "reason": "The business rule supports resolution.",
                },
            ]
            if self.include_lookup:
                capabilities.append(
                    {
                        "capability_id": "cap_lookup_ticket",
                        "name": "Look up a ticket",
                        "family": "query",
                        "resource_ids": ["tickets"],
                        "evidence": ["tickets has a stable ticket_id"],
                        "reference_tools": [],
                        "decision": "implement",
                        "reason": "The ticket entity can be read by ID.",
                    }
                )
            inventory_text = json.dumps(
                {
                    "environment_id": "support_ticket_workspace",
                    "capabilities": capabilities,
                },
                ensure_ascii=False,
                indent=2,
            )
            if self.malformed_inventory:
                inventory_text = inventory_text.replace(
                    '"capability_id": "cap_assign_ticket"',
                    '"cap_assign_ticket"',
                    1,
                )
            (working_directory / "capability_inventory.json").write_text(
                inventory_text,
                encoding="utf-8",
            )
            (working_directory / "inventory_done.json").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            return "inventory complete"

        actions = [
            {
                "name": "assign_ticket",
                "description": "Assign an open ticket to an active agent.",
                "capability_ids": ["cap_assign_ticket"],
                "resource_ids": ["tickets", "agents"],
                "evidence": ["workspace/entities/tickets.json: ticket_id and assignee_id"],
                "reference_tools": ["assign_ticket"],
                "effect": "state change",
            },
            {
                "name": "resolve_ticket",
                "description": "Resolve an assigned ticket with a resolution note.",
                "capability_ids": ["cap_resolve_ticket"],
                "resource_ids": ["tickets"],
                "evidence": ["workspace/entities/tickets.json: status and resolution"],
                "reference_tools": [],
                "effect": "state change",
            },
        ]
        if self.include_lookup:
            actions.append(
                {
                    "name": "lookup_ticket",
                    "description": "Look up a support ticket.",
                    "capability_ids": ["cap_lookup_ticket"],
                    "resource_ids": ["tickets"],
                    "evidence": ["workspace/entities/tickets.json: ticket_id"],
                    "reference_tools": [],
                    "effect": "read",
                }
            )
        if self.omit_capability:
            actions = [item for item in actions if item["name"] != "resolve_ticket"]
        (working_directory / "action_plan.json").write_text(
            json.dumps(
                {"environment_id": "support_ticket_workspace", "actions": actions},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        for action in actions:
            self._write_draft(working_directory, action["name"])
        (working_directory / "agent_done.json").write_text(
            json.dumps({"status": "ready"}), encoding="utf-8"
        )
        return "generation complete"

    def _write_draft(self, working_directory: Path, action: str) -> None:
        if action == "assign_ticket":
            code = ASSIGN_CODE
            if self.bad_write:
                code = code.replace(
                    '    tickets_path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")',
                    '    raw_path = context.workspace_root / "raw" / "tickets-source.json"\n'
                    '    raw_path.write_text(json.dumps({"changed": True}), encoding="utf-8")\n'
                    '    tickets_path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")',
                )
            tool = {
                "name": "assign_ticket",
                "description": "Assign an open support ticket to an active support agent.",
                "inputSchema": object_schema({"ticket_id": {"type": "string"}, "agent_id": {"type": "string"}}, ["ticket_id", "agent_id"]),
                "outputSchema": output_schema({"ticket_id": {"type": "string"}, "assignee_id": {"type": "string"}}, ["ticket_id", "assignee_id"]),
                "internal": {"code": code},
            }
            tests = [{"calls": [{"tool": "assign_ticket", "arguments": {"ticket_id": "ticket-1", "agent_id": "agent-1"}}], "expect_success": True, "expect_changed": True, "expected_data": {"ticket_id": "ticket-1", "assignee_id": "agent-1"}}]
        elif action == "resolve_ticket":
            tool = {
                "name": "resolve_ticket",
                "description": "Resolve an assigned open support ticket and record a resolution.",
                "inputSchema": object_schema({"ticket_id": {"type": "string"}, "resolution": {"type": "string", "minLength": 1}}, ["ticket_id", "resolution"]),
                "outputSchema": output_schema({"ticket_id": {"type": "string"}, "status": {"type": "string", "const": "resolved"}}, ["ticket_id", "status"]),
                "internal": {"code": RESOLVE_CODE},
            }
            tests = [{"calls": [
                {"tool": "assign_ticket", "arguments": {"ticket_id": "ticket-1", "agent_id": "agent-1"}},
                {"tool": "resolve_ticket", "arguments": {"ticket_id": "ticket-1", "resolution": "Password reset link sent."}},
            ], "expect_success": True, "expect_changed": True, "expected_data": {"ticket_id": "ticket-1", "status": "resolved"}}]
        else:
            code = LOOKUP_CODE
            if self.lookup_mutates:
                code = code.replace(
                    '    return {"success": True, "data": {"ticket_id": ticket["ticket_id"], "status": ticket["status"]}}',
                    '    ticket["resolution"] = "changed by a read tool"\n'
                    '    path.write_text(json.dumps(tickets), encoding="utf-8")\n'
                    '    return {"success": True, "data": {"ticket_id": ticket["ticket_id"], "status": ticket["status"]}}',
                )
            tool = {
                "name": "lookup_ticket",
                "description": "Look up one support ticket.",
                "inputSchema": object_schema({"ticket_id": {"type": "string"}}, ["ticket_id"]),
                "outputSchema": output_schema({"ticket_id": {"type": "string"}, "status": {"type": "string"}}, ["ticket_id", "status"]),
                "internal": {"code": code},
            }
            tests = [{"calls": [{"tool": "lookup_ticket", "arguments": {"ticket_id": "ticket-1"}}], "expect_success": True, "expect_changed": False, "expected_data": {"ticket_id": "ticket-1", "status": "open"}}]
        (working_directory / "drafts" / f"{action}.json").write_text(
            json.dumps({"tool": tool, "tests": tests}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        done_dir = working_directory / "draft_done"
        done_dir.mkdir(parents=True, exist_ok=True)
        (done_dir / f"{action}.json").write_text(
            json.dumps({"status": "ready"}), encoding="utf-8"
        )


class StepwiseToolAgent(FakeToolAgent):
    """模拟按动作逐次写文件的 Agent，便于验证断点行为。"""

    def __init__(self, *, fail_action: str | None = None) -> None:
        super().__init__()
        self.fail_action = fail_action

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls.append(prompt)
        if "负责盘点" in prompt:
            return super().run(prompt, working_directory=working_directory)
        if "安排工具动作" in prompt:
            actions = [
                {
                    "name": "assign_ticket",
                    "description": "Assign an open ticket.",
                    "capability_ids": ["cap_assign_ticket"],
                    "resource_ids": ["tickets", "agents"],
                    "effect": "state change",
                },
                {
                    "name": "resolve_ticket",
                    "description": "Resolve an assigned ticket.",
                    "capability_ids": ["cap_resolve_ticket"],
                    "resource_ids": ["tickets"],
                    "effect": "state change",
                },
            ]
            (working_directory / "action_plan.json").write_text(
                json.dumps({"environment_id": "support_ticket_workspace", "actions": actions}),
                encoding="utf-8",
            )
            return "plan complete"
        if "编写一个可执行工具：" in prompt:
            action = prompt.split("编写一个可执行工具：", 1)[1].split("。", 1)[0]
            if action == self.fail_action:
                return "stopped before writing"
            self._write_draft(working_directory, action)
            return "draft complete"
        return super().run(prompt, working_directory=working_directory)


class ToolGenerationTests(unittest.TestCase):
    def make_package(self, root: Path) -> Path:
        package = root / "support"
        workspace = package / "workspace"
        provenance = package / "provenance"
        (workspace / "raw").mkdir(parents=True)
        (workspace / "entities").mkdir()
        provenance.mkdir()
        (workspace / "raw" / "tickets-source.json").write_text(json.dumps([{"ticket_id": "ticket-1", "subject": "Password reset"}]), encoding="utf-8")
        (workspace / "raw" / "agents-source.json").write_text(json.dumps([{"agent_id": "agent-1", "name": "Maya"}]), encoding="utf-8")
        (workspace / "entities" / "tickets.json").write_text(json.dumps([{"ticket_id": "ticket-1", "subject": "Password reset", "status": "open", "assignee_id": None, "resolution": None}]), encoding="utf-8")
        (workspace / "entities" / "agents.json").write_text(json.dumps([{"agent_id": "agent-1", "name": "Maya", "active": True}]), encoding="utf-8")
        environment = {
            "schema_version": "1.0",
            "environment_id": "support_ticket_workspace",
            "name": "Support ticket workspace",
            "description": "Support tickets and active agents for account-recovery requests.",
            "resources": [
                {"resource_id": "raw_tickets", "name": "Ticket source", "description": "Original ticket records.", "data_type": "raw", "storage_type": "file", "path": "raw/tickets-source.json", "format": "json", "writable": False},
                {"resource_id": "raw_agents", "name": "Agent source", "description": "Original agent records.", "data_type": "raw", "storage_type": "file", "path": "raw/agents-source.json", "format": "json", "writable": False},
                {"resource_id": "tickets", "name": "Working tickets", "description": "Mutable local support tickets.", "data_type": "entity", "storage_type": "file", "path": "entities/tickets.json", "format": "json", "writable": True, "source_resources": ["raw_tickets"], "entity_schema": {"ticket": {"description": "One support ticket.", "fields": {"ticket_id": {"type": "string", "description": "Stable ticket ID."}, "subject": {"type": "string", "description": "Customer issue."}, "status": {"type": "string", "description": "Current ticket status."}, "assignee_id": {"type": "string", "description": "Assigned agent ID."}, "resolution": {"type": "string", "description": "Recorded resolution."}}}}},
                {"resource_id": "agents", "name": "Working agents", "description": "Local active support agents.", "data_type": "entity", "storage_type": "file", "path": "entities/agents.json", "format": "json", "writable": False, "source_resources": ["raw_agents"], "entity_schema": {"agent": {"description": "One support agent.", "fields": {"agent_id": {"type": "string", "description": "Stable agent ID."}, "name": {"type": "string", "description": "Agent name."}, "active": {"type": "boolean", "description": "Whether assignments are allowed."}}}}},
            ],
            "rules": [{"description": "A resolved ticket must retain its assignee and resolution.", "resources": ["tickets", "agents"]}],
        }
        (package / "environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8")
        provenance_payloads = {
            "research_request.json": {"focus": "support ticket operations"},
            "research_report.json": {"relations": [{"from": "tickets.assignee_id", "to": "agents.agent_id"}]},
            "source_inventory.json": {"surfaces": ["tickets", "agents"]},
            "data_profile.json": {"records": {"tickets": 1, "agents": 1}},
            "quality_profile.json": {"status": "ready"},
            "sources.json": {"sources": [{"url": "https://example.test/support"}]},
        }
        for name, payload in provenance_payloads.items():
            (provenance / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return package

    def test_generates_complete_environment_and_executes_stateful_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            result = ToolGenerator(FakeToolAgent()).generate(package_path, tool_hints=[{"name": "assign_ticket"}])
            self.assertEqual(result.tool_names, ("assign_ticket", "resolve_ticket"))
            package = CompleteEnvironmentPackage.load(package_path)
            self.assertEqual(set(package.tool_names), {"assign_ticket", "resolve_ticket"})
            self.assertEqual(set(package.environment["tools"][0]), {"name", "description", "inputSchema", "outputSchema", "internal"})
            with CompleteEnvironmentRuntime(package) as runtime:
                assigned = runtime.call("assign_ticket", {"ticket_id": "ticket-1", "agent_id": "agent-1"})
                resolved = runtime.call("resolve_ticket", {"ticket_id": "ticket-1", "resolution": "Password reset link sent."})
            self.assertTrue(assigned["success"])
            self.assertEqual(resolved["data"]["status"], "resolved")
            report = json.loads(result.validation_path.read_text(encoding="utf-8"))
            self.assertTrue(all(item["status"] == "passed" for item in report["reports"]))

    def test_rejects_tool_that_modifies_read_only_resource_without_mutating_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            with self.assertRaises(ToolGenerationError):
                ToolGenerator(FakeToolAgent(bad_write=True), max_repairs=0).generate(package_path)
            environment = json.loads((package_path / "environment.json").read_text(encoding="utf-8"))
            self.assertNotIn("tools", environment)
            report = json.loads((package_path / "tool_generation" / "tool_validation.json").read_text(encoding="utf-8"))
            assign_report = next(item for item in report["reports"] if item["tool"] == "assign_ticket")
            self.assertEqual(assign_report["status"], "rejected")
            self.assertTrue(any("non_writable" in item for item in assign_report["failures"]))

    def test_keeps_other_passing_tools_when_one_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            result = ToolGenerator(
                FakeToolAgent(bad_write=True, include_lookup=True),
                max_repairs=0,
            ).generate(package_path)
            self.assertEqual(result.tool_names, ("lookup_ticket",))
            package = CompleteEnvironmentPackage.load(package_path)
            self.assertEqual(package.tool_names, ("lookup_ticket",))
            report = json.loads(result.validation_path.read_text(encoding="utf-8"))
            self.assertEqual(
                next(item for item in report["reports"] if item["tool"] == "assign_ticket")["status"],
                "rejected",
            )

    def test_agent_context_points_to_datagen_files_without_embedding_workspace_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            hints = [{"name": "assign_ticket", "description": "Assign one ticket."}]
            ToolGenerator(FakeToolAgent()).generate(package_path, tool_hints=hints)

            output_dir = package_path / "tool_generation"
            context_text = (output_dir / "context.json").read_text(encoding="utf-8")
            context = json.loads(context_text)
            self.assertEqual(context["environment_path"], "../environment.json")
            self.assertEqual(context["workspace_path"], "../workspace")
            self.assertEqual(
                context["provenance_files"],
                [
                    "../provenance/research_request.json",
                    "../provenance/research_report.json",
                    "../provenance/source_inventory.json",
                    "../provenance/data_profile.json",
                    "../provenance/quality_profile.json",
                    "../provenance/sources.json",
                ],
            )
            self.assertTrue(
                all(set(item) == {"path", "size"} for item in context["workspace_files"])
            )
            self.assertNotIn("Password reset", context_text)
            self.assertEqual(
                json.loads((output_dir / "reference_tools.json").read_text(encoding="utf-8")),
                hints,
            )
            self.assertEqual(context["tool_schema_path"], "tool.schema.json")
            self.assertEqual(context["draft_example_path"], "draft_example.json")
            self.assertTrue((output_dir / "tool.schema.json").is_file())
            example = json.loads(
                (output_dir / "draft_example.json").read_text(encoding="utf-8")
            )
            self.assertEqual(example["tests"][0]["calls"][0]["tool"], "get_item")

    def test_runs_one_repair_round_only_when_a_tool_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            agent = FakeToolAgent(bad_write=True, repair_bad_write=True)
            result = ToolGenerator(agent, max_repairs=1).generate(package_path)
            self.assertEqual(result.tool_names, ("assign_ticket", "resolve_ticket"))
            self.assertEqual(len(agent.calls), 3)
            self.assertIn("负责盘点", agent.calls[0])
            self.assertIn("工具修复", agent.calls[2])

    def test_loads_reference_tools_from_current_upstream_seed_catalog(self):
        catalog = Path(__file__).parents[1] / "seed_gen" / "data" / "smithery_140_v1_0824.json"
        document = json.loads(catalog.read_text(encoding="utf-8"))
        seed = document[0]
        tools = _reference_tools(
            hints_path=None,
            seed_path=catalog,
            seed_id=seed["global_id"],
        )
        self.assertEqual(tools, seed["init_ref_tools"])
        self.assertGreater(len(tools), 0)

    def test_normalizes_flat_agent_calls_into_one_runtime_sequence(self):
        tests = _normalize_tests(
            [
                {"tool": "assign_ticket", "arguments": {"ticket_id": "ticket-1"}},
                {
                    "tool": "resolve_ticket",
                    "arguments": {"ticket_id": "ticket-1"},
                    "expect_success": True,
                    "expect_changed": True,
                    "expected_data": {"status": "resolved"},
                },
            ]
        )
        self.assertEqual(
            [call["tool"] for call in tests[0]["calls"]],
            ["assign_ticket", "resolve_ticket"],
        )
        self.assertEqual(tests[0]["expected_data"], {"status": "resolved"})

    def test_rejects_read_tool_that_changes_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            result = ToolGenerator(
                FakeToolAgent(include_lookup=True, lookup_mutates=True),
                max_repairs=0,
            ).generate(package_path)
            self.assertNotIn("lookup_ticket", result.tool_names)
            report = json.loads(result.validation_path.read_text(encoding="utf-8"))
            lookup = next(item for item in report["reports"] if item["tool"] == "lookup_ticket")
            self.assertTrue(any("workspace_change_mismatch" in item for item in lookup["failures"]))

    def test_rejects_action_plan_that_omits_an_implementable_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            with self.assertRaisesRegex(ToolGenerationError, "cap_resolve_ticket"):
                ToolGenerator(FakeToolAgent(omit_capability=True)).generate(package_path)

    def test_repairs_a_malformed_inventory_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            agent = FakeToolAgent(malformed_inventory=True)
            result = ToolGenerator(agent, max_repairs=1).generate(package_path)
            self.assertEqual(result.tool_names, ("assign_ticket", "resolve_ticket"))
            self.assertEqual(len(agent.calls), 3)
            self.assertIn("能力盘点文件修复", agent.calls[1])

    def test_generates_each_action_in_a_separate_agent_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            agent = StepwiseToolAgent()
            result = ToolGenerator(agent).generate(package_path)
            self.assertEqual(result.tool_names, ("assign_ticket", "resolve_ticket"))
            self.assertEqual(sum("编写一个可执行工具：" in prompt for prompt in agent.calls), 2)
            progress = json.loads(
                (package_path / "tool_generation" / "progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(progress["status"], "drafts_ready")
            self.assertEqual(progress["completed_actions"], ["assign_ticket", "resolve_ticket"])

    def test_keeps_completed_drafts_when_a_later_action_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            agent = StepwiseToolAgent(fail_action="resolve_ticket")
            result = ToolGenerator(agent).generate(package_path)
            self.assertEqual(result.tool_names, ("assign_ticket",))
            self.assertTrue(
                (package_path / "tool_generation" / "drafts" / "assign_ticket.json").is_file()
            )
            self.assertFalse(
                (package_path / "tool_generation" / "drafts" / "resolve_ticket.json").is_file()
            )
            progress = json.loads(
                (package_path / "tool_generation" / "progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(progress["status"], "drafts_ready_with_skips")
            self.assertEqual(progress["failed_actions"], ["resolve_ticket"])
            reports = json.loads(result.validation_path.read_text(encoding="utf-8"))["reports"]
            self.assertEqual(next(item for item in reports if item["tool"] == "resolve_ticket")["status"], "skipped")

    def test_resumes_from_existing_completed_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            first_agent = StepwiseToolAgent(fail_action="resolve_ticket")
            first_result = ToolGenerator(first_agent).generate(package_path)
            self.assertEqual(first_result.tool_names, ("assign_ticket",))
            environment = json.loads((package_path / "environment.json").read_text(encoding="utf-8"))
            environment.pop("tools", None)
            (package_path / "environment.json").write_text(
                json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            second_agent = StepwiseToolAgent()
            result = ToolGenerator(second_agent).generate(package_path)
            self.assertEqual(result.tool_names, ("assign_ticket", "resolve_ticket"))
            self.assertEqual(
                sum("编写一个可执行工具：" in prompt for prompt in second_agent.calls),
                1,
            )


if __name__ == "__main__":
    unittest.main()
