from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from task_gen.program_form import (
    CompleteEnvironmentPackage,
    CompleteEnvironmentRuntime,
    ProgramGenerationPolicy,
    ToolGenDelivery,
    execute_solution_code,
    run_step0,
    run_step1,
    run_step2,
)
from task_gen.program_form.step_1_task_research import (
    validate_task_research,
)
from task_gen.program_form.utils.io import read_json, read_records, write_json


def closed_object(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def tool_output(data_properties: dict, data_required: list[str]) -> dict:
    error = closed_object(
        {
            "code": {
                "type": "string",
                "enum": ["not_found", "invalid_state"],
            },
            "path": {"type": "string"},
            "message": {"type": "string", "minLength": 1},
            "retryable": {"type": "boolean"},
        },
        ["code", "path", "message", "retryable"],
    )
    return {
        "oneOf": [
            closed_object(
                {
                    "success": {"type": "boolean", "const": True},
                    "data": closed_object(data_properties, data_required),
                },
                ["success", "data"],
            ),
            closed_object(
                {
                    "success": {"type": "boolean", "const": False},
                    "error": error,
                },
                ["success", "error"],
            ),
        ]
    }


ITEM_SCHEMA = closed_object(
    {
        "item_id": {"type": "string"},
        "score": {"type": "integer"},
        "eligible": {"type": "boolean"},
        "status": {"type": "string", "enum": ["open", "selected"]},
    },
    ["item_id", "score", "eligible", "status"],
)


LIST_CODE = """
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        rows = connection.execute(
            "SELECT item_id, score, eligible, status FROM candidates ORDER BY item_id"
        ).fetchall()
    finally:
        connection.close()
    return {"success": True, "data": {"items": [
        {"item_id": row[0], "score": row[1], "eligible": bool(row[2]), "status": row[3]}
        for row in rows
    ]}}
"""


GET_CODE = """
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        row = connection.execute(
            "SELECT item_id, score, eligible, status FROM candidates WHERE item_id = ?",
            (arguments["item_id"],),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {"success": False, "error": {"code": "not_found", "path": "$.item_id", "message": "Item not found.", "retryable": False}}
    return {"success": True, "data": {"item": {"item_id": row[0], "score": row[1], "eligible": bool(row[2]), "status": row[3]}}}
"""


SELECT_CODE = """
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        row = connection.execute(
            "SELECT eligible, status FROM candidates WHERE item_id = ?",
            (arguments["item_id"],),
        ).fetchone()
        if row is None or not bool(row[0]) or row[1] != "open":
            return {"success": False, "error": {"code": "invalid_state", "path": "$.item_id", "message": "Item is not selectable.", "retryable": False}}
        connection.execute(
            "UPDATE candidates SET status = 'selected' WHERE item_id = ?",
            (arguments["item_id"],),
        )
        connection.commit()
    finally:
        connection.close()
    return {"success": True, "data": {"item_id": arguments["item_id"], "status": "selected"}}
"""


class ReviewAgent:
    def run(self, _prompt: str, *, working_directory: Path) -> str:
        request = read_json(working_directory / "review_request.json")

        def leaf_paths(value, prefix=""):
            if isinstance(value, dict):
                if not value:
                    return [prefix] if prefix else []
                return [
                    path
                    for key, child in value.items()
                    for path in leaf_paths(child, f"{prefix}/{key}")
                ]
            if isinstance(value, list):
                if not value:
                    return [prefix] if prefix else []
                return [
                    path
                    for index, child in enumerate(value)
                    for path in leaf_paths(child, f"{prefix}/{index}")
                ]
            return [prefix]

        write_json(working_directory / "review.json", {
            "accepted": True,
            "reason": "The executed solution completes every requirement in the public task.",
            "parameter_audit": [
                {
                    "call_index": index,
                    "tool": call["tool"],
                    "parameters": [
                        {
                            "path": path,
                            "source": "task",
                            "source_call_indices": [],
                            "evidence": "The public task determines this test parameter.",
                        }
                        for path in leaf_paths(call["arguments"])
                    ],
                }
                for index, call in enumerate(request["solution_trace"])
            ],
            "issues": [],
        })
        return "done"


class RepairAgent:
    def __init__(self, solution: str):
        self.solution = solution

    def run(self, _prompt: str, *, working_directory: Path) -> str:
        write_json(working_directory / "repair.json", {
            "solution_code": self.solution,
            "modification": "Use the declared list response field.",
        })
        return "done"


class ProgramFormV2Tests(unittest.TestCase):
    def make_package(self, root: Path, relative: str = "environment") -> Path:
        package = root / relative
        state = package / "state"
        (state / "filesystem_scopes").mkdir(parents=True)
        write_json(package / "environment.json", {
            "schema_version": "2.0",
            "environment_id": "candidate_review_v2",
            "name": "Candidate Review Environment",
            "summary": "Review eligible candidates and record the selected candidate.",
            "description": "A complete review environment containing candidate scores, eligibility evidence, workflow state, and operations for inspecting and recording a final selection.",
            "record_sets": [
                {
                    "record_set_id": "candidates",
                    "name": "Candidates",
                    "description": "Candidate records available in the current review workflow.",
                    "access": "copy_on_write",
                    "key_fields": ["item_id"],
                    "fields": {
                        "item_id": {"type": "string", "description": "Stable candidate identifier.", "nullable": False},
                        "score": {"type": "integer", "description": "Candidate review score.", "nullable": False},
                        "eligible": {"type": "boolean", "description": "Whether the candidate passes eligibility.", "nullable": False},
                        "status": {"type": "string", "description": "Current workflow status.", "nullable": False, "enum": ["open", "selected"]},
                    },
                }
            ],
            "relationships": [],
            "filesystem_scopes": [],
        })
        write_json(package / "validation.json", {"valid": True, "errors": [], "warnings": []})
        connection = sqlite3.connect(state / "records.sqlite")
        try:
            connection.execute(
                "CREATE TABLE candidates (item_id TEXT NOT NULL, score INTEGER NOT NULL, eligible INTEGER NOT NULL, status TEXT NOT NULL) STRICT"
            )
            connection.execute("CREATE UNIQUE INDEX ux_candidates_key ON candidates(item_id)")
            connection.executemany(
                "INSERT INTO candidates VALUES (?, ?, ?, ?)",
                [("a", 4, 1, "open"), ("b", 9, 0, "open"), ("c", 7, 1, "open")],
            )
            connection.commit()
        finally:
            connection.close()
        tools = [
            {
                "name": "list_candidates",
                "description": "List all current candidates and their review evidence.",
                "inputSchema": closed_object({}, []),
                "outputSchema": tool_output(
                    {"items": {"type": "array", "items": ITEM_SCHEMA}},
                    ["items"],
                ),
                "internal": {"code": LIST_CODE},
            },
            {
                "name": "get_candidate",
                "description": "Retrieve one candidate using a stable identifier.",
                "inputSchema": closed_object({"item_id": {"type": "string"}}, ["item_id"]),
                "outputSchema": tool_output({"item": ITEM_SCHEMA}, ["item"]),
                "internal": {"code": GET_CODE},
            },
            {
                "name": "select_candidate",
                "description": "Record one open and eligible candidate as selected.",
                "inputSchema": closed_object({"item_id": {"type": "string"}}, ["item_id"]),
                "outputSchema": tool_output(
                    {"item_id": {"type": "string"}, "status": {"type": "string"}},
                    ["item_id", "status"],
                ),
                "internal": {"code": SELECT_CODE},
            },
        ]
        for tool in tools:
            tool["usageConditions"] = {
                "targetResources": ["candidates"],
                "targetObjects": [
                    {"objectType": "candidate", "identifiedBy": ["item_id"]}
                ],
                "preconditions": [],
                "sideEffects": (
                    ["Updates one candidate status to selected."]
                    if tool["name"] == "select_candidate"
                    else []
                ),
            }
        write_json(package / "tools.json", {"tools": tools})
        return package

    @staticmethod
    def solution_code() -> str:
        return """listed = call_tool("list_candidates", {})
eligible = []
for row in listed["data"]["items"]:
    detail = call_tool("get_candidate", {"item_id": row["item_id"]})
    if detail["data"]["item"]["eligible"]:
        eligible.append(detail["data"]["item"])
eligible = sorted(eligible, key=lambda row: row["score"], reverse=True)
winner = eligible[0]
updated = call_tool("select_candidate", {"item_id": winner["item_id"]})
final_answer = {"selected_item_id": winner["item_id"], "selected_score": winner["score"], "status": updated["data"]["status"]}"""

    @staticmethod
    def answer_schema() -> dict:
        return closed_object(
            {
                "selected_item_id": {"type": "string"},
                "selected_score": {"type": "integer"},
                "status": {"type": "string"},
            },
            ["selected_item_id", "selected_score", "status"],
        )

    @staticmethod
    def research_payload() -> dict:
        source = "https://example.org/candidate-review-workflow"
        return {
            "schema_version": "1.0",
            "env_id": "candidate_review_v2",
            "research_summary": "Documented review workflows require eligibility screening, evidence inspection, comparative ranking, and recording a final decision with an auditable result.",
            "sources": [
                {
                    "url": source,
                    "description": "A documented workflow describing evidence-based candidate screening and final selection recording.",
                }
            ],
            "task_archetypes": [
                {
                    "archetype_id": "select_best_eligible_candidate",
                    "name": "Select the best eligible candidate",
                    "description": (
                        "A review coordinator handles the final stage of a candidate review cycle when a decision is needed. "
                        "They inspect the complete pool and the evidence attached to each record, exclude anyone who fails a mandatory eligibility condition, "
                        "compare the scores of the remaining candidates, record the selected person, and report the decision for audit. "
                        "The work commonly fails when a coordinator chooses the highest score without checking eligibility or records a decision without preserving the evidence."
                    ),
                    "requirements": [
                        "Check eligibility evidence and comparable scores for every candidate before selecting anyone.",
                        "Never select an ineligible candidate; record the winning decision and verify the resulting status.",
                    ],
                    "source_urls": [source],
                    "environment_support": {
                        "record_sets": ["candidates"],
                        "relationships": [],
                        "filesystem_scopes": [],
                        "tools": ["list_candidates", "get_candidate", "select_candidate"],
                        "unsupported_requirements": [],
                        "generatable": True,
                        "reason": "The environment exposes candidate evidence, scores, and a controlled selection operation.",
                    },
                }
            ],
        }

    @staticmethod
    def task_public() -> str:
        return (
            "Review every candidate in the current cycle, exclude anyone who is not eligible, "
            "select the eligible person with the highest review score, record that selection, "
            "and return the selected identifier, score, and resulting status."
        )

    def candidate_payload(self, *, solution: str | None = None, task_public: str | None = None) -> dict:
        return {
            "candidates": [
                {
                    "archetype_id": "select_best_eligible_candidate",
                    "task_internal": "Inspect all current candidate records, enforce the eligibility gate, compare the eligible scores, persist the winning selection, and report the selected candidate and resulting status.",
                    "task_public": task_public or self.task_public(),
                    "output_schema": self.answer_schema(),
                    "task_resources": {
                        "record_sets": ["candidates"],
                        "relationships": [],
                        "files": [],
                        "allowed_tools": [
                            "list_candidates",
                            "get_candidate",
                            "select_candidate",
                        ],
                    },
                    "solution_code": solution or self.solution_code(),
                }
            ]
        }

    def prepare_through_step2(self, root: Path):
        package_path = self.make_package(root)
        output = root / "tasks"
        research_path = root / "research.json"
        candidate_path = root / "candidates.json"
        write_json(research_path, self.research_payload())
        write_json(candidate_path, self.candidate_payload())
        step0 = run_step0(environment_package=package_path, output_dir=output)
        step1 = run_step1(
            step0_path=step0,
            output_dir=output,
            agent=None,
            research_fixture_path=research_path,
        )
        step2 = run_step2(
            step0_path=step0,
            step1_path=step1.output_path,
            output_dir=output,
            policy=ProgramGenerationPolicy(
                clean_replays=2,
                require_state_change=True,
            ),
            generation_agent=None,
            review_agent=ReviewAgent(),
            candidates_path=candidate_path,
        )
        return step0, step1, step2, output

    def test_runtime_executes_solution_against_isolated_logical_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = CompleteEnvironmentPackage.load(self.make_package(root))
            result = execute_solution_code(package, self.solution_code(), self.answer_schema())
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.answer["selected_item_id"], "c")
            self.assertEqual(len(result.trace), 5)
            self.assertEqual(result.state_diff["changed_assets"], ["candidates"])
            with CompleteEnvironmentRuntime(package) as runtime:
                baseline = runtime.call("get_candidate", {"item_id": "c"})
            self.assertEqual(baseline["data"]["item"]["status"], "open")

    def test_step0_accepts_complete_toolgen_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            package = self.make_package(
                delivery,
                "environments/candidate_package/environment",
            )
            tools_dir = delivery / "environments" / "candidate_package" / "tools"
            tools_dir.mkdir(parents=True)
            tools = read_json(package / "tools.json")["tools"]
            write_json(tools_dir / "tools.json", {
                "schema_version": "1.0",
                "environment_id": "candidate_review_v2",
                "tools": tools,
            })
            write_json(tools_dir / "tool_validation.json", {
                "schema_version": "1.0",
                "environment_id": "candidate_review_v2",
                "reports": [
                    {"tool": tool["name"], "status": "passed"}
                    for tool in tools
                ],
            })
            binding = delivery / "environments" / "candidate_package" / "binding.json"
            software = delivery / "environments" / "candidate_package" / "software"
            software.mkdir()
            write_json(software / "profile.json", {"profile_id": None})
            write_json(binding, {
                "schema_version": "1.0",
                "package_id": "candidate_package",
                "environment_id": "candidate_review_v2",
                "package_path": "environments/candidate_package",
                "tools_path": "environments/candidate_package/tools/tools.json",
                "tool_validation_path": "environments/candidate_package/tools/tool_validation.json",
                "environment_path": "environments/candidate_package/environment",
                "environment_validation_path": (
                    "environments/candidate_package/environment/validation.json"
                ),
                "software_mapping_path": (
                    "environments/candidate_package/software/profile.json"
                ),
                "software_profile": None,
                "software_profile_path": None,
            })

            resolved = ToolGenDelivery.load(binding, delivery_root=delivery)
            receipt_path = run_step0(
                binding_path=binding,
                delivery_root=delivery,
                output_dir=root / "out",
            )
            receipt = read_json(receipt_path)

            self.assertEqual(resolved.environment_path, package.resolve())
            self.assertEqual(
                receipt["toolgen_delivery"]["package_id"],
                "candidate_package",
            )
            self.assertTrue((root / "out/baseline_environment/delivery.json").is_file())

    def test_step1_rejects_unknown_environment_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            step0 = run_step0(
                environment_package=self.make_package(root),
                output_dir=output,
            )
            package = CompleteEnvironmentPackage.load(output / "baseline_environment")
            payload = self.research_payload()
            payload["task_archetypes"][0]["environment_support"]["tools"].append("missing_tool")
            schema = read_json(
                Path(__file__).resolve().parents[1]
                / "task_gen/program/schemas/task_research.schema.json"
            )
            errors = validate_task_research(
                payload,
                schema=schema,
                public_environment=package.public_environment(),
                env_id="candidate_review_v2",
            )
            self.assertTrue(any("missing_tool" in error for error in errors))
            self.assertTrue(step0.is_file())

    def test_step1_rejects_shallow_task_archetype(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            step0 = run_step0(
                environment_package=self.make_package(root),
                output_dir=output,
            )
            package = CompleteEnvironmentPackage.load(output / "baseline_environment")
            payload = self.research_payload()
            archetype = payload["task_archetypes"][0]
            archetype["description"] = archetype["description"][:40]
            archetype["requirements"] = archetype["requirements"][:1]
            schema = read_json(
                Path(__file__).resolve().parents[1]
                / "task_gen/program/schemas/task_research.schema.json"
            )

            errors = validate_task_research(
                payload,
                schema=schema,
                public_environment=package.public_environment(),
                env_id="candidate_review_v2",
            )

            self.assertTrue(any("description" in error for error in errors))
            self.assertTrue(any("requirements" in error for error in errors))
            self.assertTrue(step0.is_file())

    def test_step2_repairs_real_error_and_cleanly_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            output = root / "tasks"
            research_path = root / "research.json"
            candidates_path = root / "candidates.json"
            write_json(research_path, self.research_payload())
            broken = self.solution_code().replace('["items"]', '["missing"]', 1)
            write_json(candidates_path, self.candidate_payload(solution=broken))
            step0 = run_step0(environment_package=package_path, output_dir=output)
            step1 = run_step1(
                step0_path=step0,
                output_dir=output,
                agent=None,
                research_fixture_path=research_path,
            )
            result = run_step2(
                step0_path=step0,
                step1_path=step1.output_path,
                output_dir=output,
                policy=ProgramGenerationPolicy(
                    require_state_change=True,
                ),
                generation_agent=RepairAgent(self.solution_code()),
                review_agent=ReviewAgent(),
                candidates_path=candidates_path,
            )
            task = read_records(result.output_path)[0]
            self.assertEqual(len(task["solution_validation"]["debug_history"]), 1)
            self.assertEqual(task["solution_validation"]["clean_replay_count"], 2)
            self.assertEqual(task["ground_truth"]["candidate_answer"]["selected_item_id"], "c")

    def test_step2_rejects_public_tool_name_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tasks"
            step0 = run_step0(
                environment_package=self.make_package(root),
                output_dir=output,
            )
            research_path = root / "research.json"
            candidates_path = root / "candidates.json"
            write_json(research_path, self.research_payload())
            write_json(
                candidates_path,
                self.candidate_payload(
                    task_public=(
                        "Use list_candidates to inspect every record, enforce eligibility, select "
                        "the highest eligible score, record the final decision, and return a complete result."
                    )
                ),
            )
            step1 = run_step1(
                step0_path=step0,
                output_dir=output,
                agent=None,
                research_fixture_path=research_path,
            )
            with self.assertRaisesRegex(RuntimeError, "0/1"):
                run_step2(
                    step0_path=step0,
                    step1_path=step1.output_path,
                    output_dir=output,
                    policy=ProgramGenerationPolicy(),
                    generation_agent=None,
                    review_agent=ReviewAgent(),
                    candidates_path=candidates_path,
                )
            validation = read_json(output / "step2_validation.json")
            self.assertIn("泄露工具名", validation["rejections"][0]["reasons"][0])

    def test_step2_exports_external_task_and_ground_truth_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, step2, output = self.prepare_through_step2(Path(temporary))
            generated = read_records(step2.output_path)[0]
            tasks = read_json(step2.tasks_path)
            bundle = read_json(step2.bundle_path)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(
                tasks[0]["task_resources"],
                generated["task_resources"],
            )
            self.assertEqual(tasks[0]["reference"]["tool_calls"][0]["tool"], "list_candidates")
            self.assertNotIn("result", tasks[0]["reference"]["tool_calls"][0])
            self.assertTrue((output / tasks[0]["initial_state"]).is_dir())
            self.assertTrue(
                (output / tasks[0]["reference"]["final_state"]).is_dir()
            )
            self.assertEqual(
                bundle["tasks"][0]["execution"]["final_state"],
                tasks[0]["reference"]["final_state"],
            )


if __name__ == "__main__":
    unittest.main()
