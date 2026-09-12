from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from env_gen.tool_gen import ToolGenerationError, ToolGenerator
from env_gen.tool_gen.__main__ import _reference_tools
from env_gen.tool_gen.compiler import _contains, _normalize_tests
from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime


def closed_object(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def result_schema(data: dict[str, object], required: list[str]) -> dict[str, object]:
    error = closed_object(
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
            closed_object(
                {"success": {"type": "boolean", "const": True}, "data": closed_object(data, required)},
                ["success", "data"],
            ),
            closed_object(
                {"success": {"type": "boolean", "const": False}, "error": error},
                ["success", "error"],
            ),
        ]
    }


def tool(name: str, code: str, data: dict[str, object], required: list[str]) -> dict[str, object]:
    is_write = name == "resolve_ticket"
    return {
        "name": name,
        "description": f"Execute the {name} business operation.",
        "usageConditions": {
            "targetResources": ["tickets"],
            "targetObjects": [{"objectType": "support ticket", "identifiedBy": ["ticket_id"]}],
            "preconditions": [
                "The ticket exists in the current workspace.",
                *(["The ticket status allows resolution."] if is_write else []),
            ],
            "sideEffects": ["Updates the ticket status to resolved."] if is_write else [],
        },
        "inputSchema": closed_object({"ticket_id": {"type": "string"}}, ["ticket_id"]),
        "outputSchema": result_schema(data, required),
        "internal": {"code": code},
    }


GET_CODE = '''
def run(arguments, context):
    record = context.records.get("tickets", {"ticket_id": arguments["ticket_id"]})
    if record is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.ticket_id", "message": "Ticket not found.", "retryable": False}}
    return {"success": True, "data": {"ticket_id": record["ticket_id"], "status": record["status"]}}
'''


RESOLVE_CODE = '''
def run(arguments, context):
    record = context.records.get("tickets", {"ticket_id": arguments["ticket_id"]})
    if record is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.ticket_id", "message": "Ticket not found.", "retryable": False}}
    context.records.update("tickets", {"ticket_id": arguments["ticket_id"]}, {"status": "resolved"})
    return {"success": True, "data": {"ticket_id": record["ticket_id"], "status": "resolved"}}
'''


BAD_AGENT_CODE = '''
def run(arguments, context):
    context.records.update("agents", {"agent_id": "agent-1"}, {"name": "Changed"})
    return {"success": True, "data": {"ticket_id": arguments["ticket_id"], "status": "changed"}}
'''


class FakeAgent:
    def __init__(
        self,
        *,
        bad_lookup: bool = False,
        duplicate_plan: bool = False,
        missing_reality: bool = False,
        standard_only_domain_action: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.bad_lookup = bad_lookup
        self.duplicate_plan = duplicate_plan
        self.missing_reality = missing_reality
        self.standard_only_domain_action = standard_only_domain_action

    def run_until_files(
        self,
        prompt: str,
        *,
        working_directory: Path,
        required_paths: tuple[Path, ...],
    ) -> str:
        return self.run(prompt, working_directory=working_directory)

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls.append(prompt)
        if "负责盘点" in prompt:
            payload = {
                "environment_id": "support_workspace",
                "capabilities": [
                    {
                        "capability_id": "get_ticket",
                        "name": "Get ticket",
                        "family": "query",
                        "asset_ids": ["tickets"],
                        "evidence": ["tickets.ticket_id"],
                        "reference_tools": ["get_ticket"],
                        "reality_evidence": [{
                            "source_type": "reference_tool",
                            "source": "get_ticket",
                            "operation": "Retrieve one support ticket by its stable identifier.",
                            "adaptation": "direct",
                        }],
                        "execution_backends": [{"kind": "record_store", "asset_id": "tickets"}],
                        "decision": "implement",
                        "reason": "Stable key exists.",
                    },
                    {
                        "capability_id": "resolve_ticket",
                        "name": "Resolve ticket",
                        "family": "state_change",
                        "asset_ids": ["tickets"],
                        "evidence": ["tickets is copy_on_write and has status"],
                        "reference_tools": ["resolve_ticket"],
                        "reality_evidence": [{
                            "source_type": "reference_tool",
                            "source": "resolve_ticket",
                            "operation": "Move an existing support ticket into the resolved state.",
                            "adaptation": "adapted",
                        }],
                        "execution_backends": [{"kind": "record_store", "asset_id": "tickets"}],
                        "decision": "implement",
                        "reason": "Mutable business status exists.",
                    },
                ],
            }
            if self.missing_reality:
                payload["capabilities"][0].pop("reality_evidence")
            if self.standard_only_domain_action:
                payload["capabilities"][0]["family"] = "analysis"
                payload["capabilities"][0]["reality_evidence"] = [{
                    "source_type": "standard_operation",
                    "source": "ticket analysis",
                    "operation": "Analyze support tickets.",
                    "adaptation": "direct",
                }]
            (working_directory / "capability_inventory.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            (working_directory / "inventory_done.json").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            return "inventory"
        if "安排工具动作" in prompt:
            resolve_caps = ["resolve_ticket"]
            if self.duplicate_plan:
                resolve_caps.append("get_ticket")
            actions = [
                {
                    "name": "get_ticket",
                    "description": "Get one ticket.",
                    "capability_ids": ["get_ticket"],
                    "asset_ids": ["tickets"],
                    "evidence": ["tickets.ticket_id"],
                    "reference_tools": ["get_ticket"],
                    "effect": "read",
                    "usageConditions": {
                        "targetResources": ["tickets"],
                        "targetObjects": [{"objectType": "support ticket", "identifiedBy": ["ticket_id"]}],
                        "preconditions": ["The ticket exists in the current workspace."],
                        "sideEffects": [],
                    },
                },
                {
                    "name": "resolve_ticket",
                    "description": "Resolve one ticket.",
                    "capability_ids": resolve_caps,
                    "asset_ids": ["tickets"],
                    "evidence": ["tickets.status"],
                    "reference_tools": ["resolve_ticket"],
                    "effect": "write",
                    "usageConditions": {
                        "targetResources": ["tickets"],
                        "targetObjects": [{"objectType": "support ticket", "identifiedBy": ["ticket_id"]}],
                        "preconditions": [
                            "The ticket exists in the current workspace.",
                            "The ticket status allows resolution.",
                        ],
                        "sideEffects": ["Updates the ticket status to resolved."],
                    },
                },
            ]
            (working_directory / "action_plan.json").write_text(
                json.dumps({"environment_id": "support_workspace", "actions": [
                    action for action in actions
                    if all(cap in {
                        item["capability_id"] for item in json.loads(
                            (working_directory / "approved_capabilities.json").read_text(encoding="utf-8")
                        )["capabilities"]
                    } for cap in action["capability_ids"])
                ]}),
                encoding="utf-8",
            )
            return "plan"
        if "这一组相关工具" in prompt:
            drafts = {
                "get_ticket": {
                    "tool": tool(
                        "get_ticket",
                        BAD_AGENT_CODE if self.bad_lookup else GET_CODE,
                        {"ticket_id": {"type": "string"}, "status": {"type": "string"}},
                        ["ticket_id", "status"],
                    ),
                    "tests": [{"calls": [{"tool": "get_ticket", "arguments": {"ticket_id": "ticket-1"}}], "expect_success": True, "expect_changed": False}],
                },
                "resolve_ticket": {
                    "tool": tool(
                        "resolve_ticket",
                        RESOLVE_CODE,
                        {"ticket_id": {"type": "string"}, "status": {"type": "string", "const": "resolved"}},
                        ["ticket_id", "status"],
                    ),
                    "tests": [{"calls": [{"tool": "resolve_ticket", "arguments": {"ticket_id": "ticket-1"}}], "expect_success": True, "expect_changed": True, "expected_data": {"status": "resolved"}}],
                },
            }
            for name, draft in drafts.items():
                if name in prompt:
                    path = working_directory / "drafts" / f"{name}.json"
                    path.write_text(json.dumps(draft), encoding="utf-8")
            return "drafts"
        if "工具修复" in prompt:
            (working_directory / "repair_done.json").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            return "repair"
        raise AssertionError(prompt)


class ToolGenV2Tests(unittest.TestCase):
    def make_package(self, root: Path) -> Path:
        package = root / "support"
        scope = package / "state/filesystem_scopes/reports"
        scope.mkdir(parents=True)
        (package / "provenance").mkdir()
        environment = {
            "schema_version": "2.0",
            "environment_id": "support_workspace",
            "name": "Support workspace",
            "summary": "A support workspace for ticket review and resolution workflows.",
            "description": "This environment contains support tickets, active service agents, and an editable report directory for realistic ticket review and resolution workflows.",
            "record_sets": [
                {
                    "record_set_id": "tickets",
                    "name": "Tickets",
                    "description": "One record represents a support ticket.",
                    "access": "copy_on_write",
                    "key_fields": ["ticket_id"],
                    "fields": {
                        "ticket_id": {"type": "string", "description": "Stable ticket ID.", "nullable": False},
                        "subject": {"type": "string", "description": "Ticket subject.", "nullable": False},
                        "status": {"type": "string", "description": "Current status.", "nullable": False},
                        "assignee_id": {"type": "string", "description": "Assigned agent.", "nullable": True},
                    },
                },
                {
                    "record_set_id": "agents",
                    "name": "Agents",
                    "description": "One record represents a support agent.",
                    "access": "read_only",
                    "key_fields": ["agent_id"],
                    "fields": {
                        "agent_id": {"type": "string", "description": "Stable agent ID.", "nullable": False},
                        "name": {"type": "string", "description": "Agent name.", "nullable": False},
                        "active": {"type": "boolean", "description": "Whether the agent is active.", "nullable": False},
                    },
                },
            ],
            "relationships": [
                {
                    "relationship_id": "ticket_to_agent",
                    "description": "An assigned ticket references an existing support agent.",
                    "from": {"record_set_id": "tickets", "fields": ["assignee_id"]},
                    "to": {"record_set_id": "agents", "fields": ["agent_id"]},
                    "cardinality": "many_to_one",
                }
            ],
            "filesystem_scopes": [
                {
                    "scope_id": "reports",
                    "name": "Reports",
                    "description": "Editable JSON reports produced by support workflows.",
                    "access": "copy_on_write",
                    "structure": {
                        "kind": "directory",
                        "path": ".",
                        "layout": [
                            {"kind": "file_collection", "path": "*.json", "description": "Support reports.", "required": False, "format": "json"}
                        ],
                    },
                }
            ],
        }
        (package / "environment.json").write_text(json.dumps(environment), encoding="utf-8")
        (package / "environment.md").write_text("# Support workspace\n", encoding="utf-8")
        (package / "provenance/scenario_research.json").write_text(
            json.dumps({"tools": [{"name": "get_ticket", "description": "Get one ticket."}, {"name": "resolve_ticket", "description": "Resolve one ticket."}]}),
            encoding="utf-8",
        )
        database = package / "state/records.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE "agents" ("agent_id" TEXT NOT NULL, "name" TEXT NOT NULL, "active" INTEGER NOT NULL) STRICT')
            connection.execute('CREATE UNIQUE INDEX "ux_agents_key" ON "agents" ("agent_id")')
            connection.execute('INSERT INTO "agents" VALUES (?, ?, ?)', ("agent-1", "Alex", 1))
            connection.execute('CREATE TABLE "tickets" ("ticket_id" TEXT NOT NULL, "subject" TEXT NOT NULL, "status" TEXT NOT NULL, "assignee_id" TEXT) STRICT')
            connection.execute('CREATE UNIQUE INDEX "ux_tickets_key" ON "tickets" ("ticket_id")')
            connection.execute('INSERT INTO "tickets" VALUES (?, ?, ?, ?)', ("ticket-1", "Login issue", "open", "agent-1"))
            connection.commit()
        return package

    def test_generates_tools_from_v2_package_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            original = (package / "environment.json").read_text(encoding="utf-8")
            agent = FakeAgent()
            result = ToolGenerator(agent, draft_batch_size=5).generate(package)
            self.assertEqual(result.tool_names, ("get_ticket", "resolve_ticket"))
            self.assertEqual((package / "environment.json").read_text(encoding="utf-8"), original)
            self.assertTrue(result.tools_path.is_file())
            grounding = json.loads(result.grounding_path.read_text(encoding="utf-8"))
            self.assertEqual(grounding["tools"][0]["reality_evidence"][0]["source"], "get_ticket")
            tools = json.loads(result.tools_path.read_text(encoding="utf-8"))["tools"]
            self.assertEqual(tools[1]["usageConditions"]["targetResources"], ["tickets"])
            self.assertEqual(sum("这一组相关工具" in call for call in agent.calls), 1)
            with ToolRuntime(ToolPackage.load(package)) as runtime:
                resolved = runtime.call("resolve_ticket", {"ticket_id": "ticket-1"})
            self.assertEqual(resolved["data"]["status"], "resolved")

    def test_context_uses_datagen_v2_paths_and_research_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            ToolGenerator(FakeAgent()).generate(package)
            output = package / "tool_generation"
            context = json.loads((output / "context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["records_database_path"], "../state/records.sqlite")
            self.assertEqual(context["filesystem_scopes_path"], "../state/filesystem_scopes")
            self.assertNotIn("workspace_path", context)
            hints = json.loads((output / "reference_tools.json").read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in hints], ["get_ticket", "resolve_ticket"])

    def test_rejects_tool_that_modifies_read_only_record_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(FakeAgent(bad_lookup=True), max_repairs=0).generate(package)
            self.assertEqual(result.tool_names, ("resolve_ticket",))
            report = json.loads(result.validation_path.read_text(encoding="utf-8"))
            lookup = next(item for item in report["reports"] if item["tool"] == "get_ticket")
            self.assertEqual(lookup["status"], "rejected")
            self.assertTrue(any("只读资源" in failure for failure in lookup["failures"]))

    def test_rejects_capability_assigned_to_two_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            with self.assertRaisesRegex(ToolGenerationError, "重复安排"):
                ToolGenerator(FakeAgent(duplicate_plan=True)).generate(package)

    def test_rejects_implementable_capability_without_reality_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(FakeAgent(missing_reality=True), max_repairs=0).generate(package)
            self.assertEqual(result.tool_names, ("resolve_ticket",))
            review = json.loads((package / "tool_generation/capability_review.json").read_text(encoding="utf-8"))
            self.assertIn("缺少现实操作依据", review["rejected"][0]["error"])

    def test_domain_action_needs_reference_tool_or_official_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(FakeAgent(standard_only_domain_action=True), max_repairs=0).generate(package)
            self.assertEqual(result.tool_names, ("resolve_ticket",))
            review = json.loads((package / "tool_generation/capability_review.json").read_text(encoding="utf-8"))
            self.assertIn("领域能力", review["rejected"][0]["error"])

    def test_standard_statistics_and_domain_actions_have_distinct_evidence(self) -> None:
        value = {
            "capability_id": "count_by_category", "decision": "implement", "family": "analysis",
            "operation_kind": "standard_data", "asset_ids": ["icons"],
            "reality_evidence": [{"source_type": "standard_operation", "source": "Group by and count",
                                  "operation": "Count records by category", "adaptation": "direct"}],
            "execution_backends": [{"kind": "record_store", "asset_id": "icons"}],
        }
        checked = ToolGenerator._validate_capability(value, set(), {"icons"}, {"record_store": {"icons"}}, set())
        self.assertEqual(checked["capability_id"], "count_by_category")
        value["operation_kind"] = "domain"
        with self.assertRaisesRegex(ToolGenerationError, "领域能力"):
            ToolGenerator._validate_capability(value, set(), {"icons"}, {"record_store": {"icons"}}, set())

    def test_inventory_reports_all_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
            inventory_path = package / "tool_generation/capability_inventory.json"
            document = json.loads(inventory_path.read_text(encoding="utf-8"))
            for item in document["capabilities"]:
                item.pop("reality_evidence")
            inventory_path.write_text(json.dumps(document), encoding="utf-8")
            environment = json.loads((package / "environment.json").read_text(encoding="utf-8"))
            with self.assertRaises(ToolGenerationError) as caught:
                ToolGenerator._load_capability_inventory(environment, inventory_path)
            self.assertIn("get_ticket", str(caught.exception))
            self.assertIn("resolve_ticket", str(caught.exception))
            review = json.loads((inventory_path.parent / "capability_review.json").read_text(encoding="utf-8"))
            self.assertEqual(len(review["rejected"]), 2)
            with self.assertRaisesRegex(ToolGenerationError, "没有任何可实现能力"):
                ToolGenerator._load_capability_inventory(environment, inventory_path, allow_partial=True)

    def test_retry_resumes_existing_files_and_is_bounded(self) -> None:
        from utils.search_agent.codex import CodexTimeoutError, CodexLaunchError

        class InterruptedAgent:
            def __init__(self, error, recover):
                self.calls = 0
                self.error = error
                self.recover = recover

            def run(self, prompt, *, working_directory):
                self.calls += 1
                partial = working_directory / "partial.json"
                if self.calls == 1:
                    partial.write_text("{}")
                else:
                    assert partial.exists()
                    assert "已有文件" in prompt
                    if self.recover:
                        (working_directory / "done.json").write_text("{}")
                        return "done"
                raise self.error

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = InterruptedAgent(CodexTimeoutError("timeout"), True)
            ToolGenerator(agent)._run_agent_until("continue", working_directory=root, required_path=root / "done.json")
            self.assertEqual(agent.calls, 2)
            for error, expected_calls in [(CodexTimeoutError("timeout"), 2), (CodexLaunchError("missing"), 1)]:
                agent = InterruptedAgent(error, False)
                with self.assertRaises(type(error)):
                    ToolGenerator(agent)._run_agent_until("continue", working_directory=root, required_path=root / "other.json")
                self.assertEqual(agent.calls, expected_calls)

    def test_invalid_schema_does_not_block_independent_tool(self) -> None:
        class SchemaAgent(FakeAgent):
            def run(self, prompt, *, working_directory):
                result = super().run(prompt, working_directory=working_directory)
                if "这一组相关工具" in prompt:
                    path = working_directory / "drafts/get_ticket.json"
                    draft = json.loads(path.read_text(encoding="utf-8"))
                    draft["tool"]["outputSchema"]["unsupported_output_keyword"] = True
                    path.write_text(json.dumps(draft), encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(SchemaAgent(), max_repairs=0).generate(package)
            self.assertEqual(result.tool_names, ("resolve_ticket",))
            reports = json.loads(result.validation_path.read_text(encoding="utf-8"))["reports"]
            self.assertIn("unsupported_output_keyword", reports[0]["failures"][0])
            self.assertEqual(reports[1]["status"], "passed")
            ToolPackage.load(package)

    def test_repair_interruption_still_publishes_passed_tools(self) -> None:
        from utils.search_agent.codex import CodexTimeoutError

        class RepairAgent(FakeAgent):
            def run(self, prompt, *, working_directory):
                if "工具修复" in prompt:
                    raise CodexTimeoutError("temporary timeout")
                return super().run(prompt, working_directory=working_directory)

        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(RepairAgent(bad_lookup=True)).generate(package)
            self.assertEqual(result.tool_names, ("resolve_ticket",))
            self.assertTrue((package / "tool_generation/repair_error.json").is_file())
            ToolPackage.load(package)

    def test_output_schema_local_refs_preserve_validation(self) -> None:
        from env_gen.tool_gen.compiler import _inline_output_schema
        from jsonschema import Draft202012Validator
        schema = {"$defs": {"number": {"type": "integer", "minimum": 1}},
                  "type": "object", "properties": {"count": {"$ref": "#/$defs/number"}},
                  "required": ["count"], "additionalProperties": False}
        expanded = _inline_output_schema(schema)
        self.assertNotIn("$defs", expanded)
        for value in ({"count": 2}, {"count": 0}, {"count": "2"}, {}, {"count": 2, "extra": True}):
            self.assertEqual(Draft202012Validator(schema).is_valid(value), Draft202012Validator(expanded).is_valid(value))
        self.assertIn("$ref", schema["properties"]["count"])
        with self.assertRaises(ValueError):
            _inline_output_schema({"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"})

    def test_generated_schema_refs_are_adapted_and_original_preserved(self) -> None:
        class ReferenceAgent(FakeAgent):
            def run(self, prompt, *, working_directory):
                result = super().run(prompt, working_directory=working_directory)
                if "这一组相关工具" in prompt:
                    path = working_directory / "drafts/get_ticket.json"
                    draft = json.loads(path.read_text(encoding="utf-8"))
                    schema = draft["tool"]["outputSchema"]
                    schema["$defs"] = {"error": schema["oneOf"][1]["properties"]["error"]}
                    schema["oneOf"][1]["properties"]["error"] = {"$ref": "#/$defs/error"}
                    path.write_text(json.dumps(draft), encoding="utf-8")
                return result
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            result = ToolGenerator(ReferenceAgent(), max_repairs=0).generate(package)
            self.assertEqual(set(result.tool_names), {"get_ticket", "resolve_ticket"})
            self.assertTrue(list((package / "tool_generation/draft_history").glob("get_ticket.*.json")))

    def test_invalid_code_does_not_block_independent_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = self.make_package(Path(temporary))
            ToolGenerator(FakeAgent(), max_repairs=0).generate(package)
            drafts = [json.loads(path.read_text(encoding="utf-8")) for path in
                      sorted((package / "tool_generation/drafts").glob("*.json"))]
            drafts[0]["tool"]["internal"]["code"] = "def run("
            environment = json.loads((package / "environment.json").read_text(encoding="utf-8"))
            reports = ToolGenerator(FakeAgent())._validate(package, environment, drafts)
            self.assertEqual([item["status"] for item in reports], ["rejected", "passed"])
            drafts[1]["tests"][0]["calls"].insert(0, {"tool": "get_ticket", "arguments": {"ticket_id": "ticket-1"}})
            reports = ToolGenerator(FakeAgent())._validate(package, environment, drafts)
            self.assertEqual([item["status"] for item in reports], ["rejected", "rejected"])

    def test_batch_retries_draft_when_usage_conditions_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            draft_path = Path(temporary) / "get_ticket.json"
            draft_path.write_text(
                json.dumps({
                    "tool": tool(
                        "get_ticket",
                        GET_CODE,
                        {"ticket_id": {"type": "string"}, "status": {"type": "string"}},
                        ["ticket_id", "status"],
                    ),
                    "tests": [],
                }),
                encoding="utf-8",
            )
            action = {
                "usageConditions": {
                    "targetResources": ["tickets"],
                    "targetObjects": [{"objectType": "support ticket", "identifiedBy": ["ticket_id"]}],
                    "preconditions": ["The ticket exists and is assigned."],
                    "sideEffects": [],
                }
            }
            self.assertEqual(
                ToolGenerator._ensure_draft_ready(draft_path, "get_ticket", action),
                "draft_usage_conditions_mismatch",
            )

    def test_reference_tool_catalog_loading_is_preserved(self) -> None:
        catalog = Path(__file__).parents[1] / "seed_gen/data/smithery_140_v1_0824.json"
        document = json.loads(catalog.read_text(encoding="utf-8"))
        seed = document[0]
        tools = _reference_tools(hints_path=None, seed_path=catalog, seed_id=seed["global_id"])
        self.assertEqual(tools, seed["init_ref_tools"])

    def test_normalizes_flat_calls(self) -> None:
        tests = _normalize_tests([
            {"tool": "get_ticket", "arguments": {"ticket_id": "ticket-1"}},
            {"tool": "resolve_ticket", "arguments": {"ticket_id": "ticket-1"}, "expect_changed": True},
        ])
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0]["calls"][-1]["tool"], "resolve_ticket")

    def test_expected_data_matches_nested_subset(self) -> None:
        self.assertTrue(_contains({"status": "resolved", "extra": 1}, {"status": "resolved"}))


if __name__ == "__main__":
    unittest.main()
