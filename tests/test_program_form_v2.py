from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from task_gen.program_form import (
    CompleteEnvironmentPackage,
    CompleteEnvironmentRuntime,
    ProgramGenerationPolicy,
    execute_solution_code,
    run_step1,
    run_step2,
    run_step3,
    run_step4,
    run_step5,
)
from task_gen.program_form.steps.step2_research_real_world_tasks import (
    validate_task_research,
)
from task_gen.program_form.steps.step5_evaluate_difficulty import SolverResult
from task_gen.program_form.utils.io import read_json, read_records, write_json
from task_gen.program_form.utils.solver_mcp import serve


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
        write_json(working_directory / "review.json", {
            "accepted": True,
            "checks": {
                "research_grounded": True,
                "requirements_preserved": True,
                "no_implementation_leak": True,
                "execution_aligned": True,
                "output_schema_complete": True,
                "environment_supported": True,
            },
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


class RubricAgent:
    def run(self, _prompt: str, *, working_directory: Path) -> str:
        request = read_json(working_directory / "rubric_judge_request.json")
        passed = (request.get("candidate_answer") or {}).get("selected_item_id") == "c"
        write_json(working_directory / "rubric_review.json", {
            "items": [
                {
                    "id": item["id"],
                    "passed": passed,
                    "reason": "The answer and state evidence agree." if passed else "Evidence does not satisfy the criterion.",
                }
                for item in request["rubric_items"]
            ]
        })
        return "done"


class ProgramFormV2Tests(unittest.TestCase):
    def make_package(self, root: Path) -> Path:
        package = root / "environment"
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
                    "role": "Review coordinator",
                    "trigger": "A review cycle reaches the final candidate selection stage.",
                    "business_goal": "Select the highest-scoring candidate who satisfies every eligibility condition and record the decision.",
                    "workflow": [
                        "Inspect the complete candidate pool and eligibility evidence.",
                        "Compare eligible scores and record the final selection.",
                    ],
                    "required_evidence": ["Eligibility and score evidence for every candidate."],
                    "hard_constraints": ["Ineligible candidates cannot be selected."],
                    "expected_deliverable": "A recorded selection and a structured summary of the winning candidate.",
                    "common_failure_modes": ["Selecting the highest score without checking eligibility."],
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
                    "solution_code": solution or self.solution_code(),
                }
            ]
        }

    @staticmethod
    def scoring_payload() -> dict:
        return {
            "rubric_items": [
                {"id": "G1", "section": "general", "points": 3, "criterion": "The returned object follows every required output field and type exactly.", "evidence_sources": ["candidate_answer"], "judge_method": "deterministic"},
                {"id": "G2", "section": "general", "points": 3, "criterion": "The reported result is supported by the executed business operation evidence.", "evidence_sources": ["candidate_answer", "tool_trace"], "judge_method": "rubric_judge"},
                {"id": "T1", "section": "task_specific", "points": 3, "criterion": "Every candidate was considered before the final selection decision was made.", "evidence_sources": ["tool_trace"], "judge_method": "rubric_judge"},
                {"id": "T2", "section": "task_specific", "points": 3, "criterion": "The selected candidate is eligible and has the highest eligible review score.", "evidence_sources": ["candidate_answer", "tool_trace"], "judge_method": "rubric_judge"},
                {"id": "T3", "section": "task_specific", "points": 2, "criterion": "The final business state records the chosen candidate as selected.", "evidence_sources": ["final_state"], "judge_method": "deterministic"},
            ],
            "answer_verifier_code": "def verify(candidate_answer, ground_truth_answer):\n    return 1.0 if candidate_answer == ground_truth_answer else 0.0",
            "state_verifier_code": "def verify_state(candidate_state, ground_truth_state, initial_state):\n    return 1.0 if candidate_state == ground_truth_state else 0.0",
            "state_verification": {
                "mode": "exact_final_state",
                "required_effects": ["The chosen candidate status must be recorded as selected."],
                "forbidden_effects": ["No unrelated candidate record may be modified by the task."],
            },
            "explanation": "The answer, business state, and observable execution evidence are checked separately so a plausible response cannot hide a missing selection operation.",
        }

    def prepare_through_step4(self, root: Path):
        package_path = self.make_package(root)
        output = root / "tasks"
        research_path = root / "research.json"
        candidate_path = root / "candidates.json"
        scoring_path = root / "scoring.json"
        write_json(research_path, self.research_payload())
        write_json(candidate_path, self.candidate_payload())
        write_json(scoring_path, self.scoring_payload())
        step1 = run_step1(environment_package=package_path, output_dir=output)
        step2 = run_step2(
            step1_path=step1,
            output_dir=output,
            agent=None,
            research_fixture_path=research_path,
        )
        step3 = run_step3(
            step1_path=step1,
            step2_path=step2.output_path,
            output_dir=output,
            policy=ProgramGenerationPolicy(
                min_tool_calls=5,
                min_distinct_tools=3,
                clean_replays=2,
                require_state_change=True,
            ),
            generation_agent=None,
            review_agent=ReviewAgent(),
            candidates_path=candidate_path,
        )
        step4 = run_step4(
            step3_path=step3.output_path,
            output_dir=output,
            policy=ProgramGenerationPolicy(),
            agent=None,
            scoring_fixture_path=scoring_path,
        )
        return step1, step2, step3, step4, output

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

    def test_step2_rejects_unknown_environment_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step1 = run_step1(
                environment_package=self.make_package(root),
                output_dir=root / "out",
            )
            package = CompleteEnvironmentPackage.load(root / "out" / "baseline_environment")
            payload = self.research_payload()
            payload["task_archetypes"][0]["environment_support"]["tools"].append("missing_tool")
            schema = read_json(
                Path(__file__).resolve().parents[1]
                / "task_gen/program_form/schemas/task_research.schema.json"
            )
            errors = validate_task_research(
                payload,
                schema=schema,
                public_environment=package.public_environment(),
                env_id="candidate_review_v2",
            )
            self.assertTrue(any("missing_tool" in error for error in errors))
            self.assertTrue(step1.is_file())

    def test_step3_repairs_real_error_and_cleanly_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            output = root / "tasks"
            research_path = root / "research.json"
            candidates_path = root / "candidates.json"
            write_json(research_path, self.research_payload())
            broken = self.solution_code().replace('["items"]', '["missing"]', 1)
            write_json(candidates_path, self.candidate_payload(solution=broken))
            step1 = run_step1(environment_package=package_path, output_dir=output)
            step2 = run_step2(
                step1_path=step1,
                output_dir=output,
                agent=None,
                research_fixture_path=research_path,
            )
            result = run_step3(
                step1_path=step1,
                step2_path=step2.output_path,
                output_dir=output,
                policy=ProgramGenerationPolicy(
                    min_tool_calls=5,
                    min_distinct_tools=3,
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

    def test_step3_rejects_public_tool_name_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tasks"
            step1 = run_step1(environment_package=self.make_package(root), output_dir=output)
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
            step2 = run_step2(
                step1_path=step1,
                output_dir=output,
                agent=None,
                research_fixture_path=research_path,
            )
            with self.assertRaisesRegex(RuntimeError, "0/1"):
                run_step3(
                    step1_path=step1,
                    step2_path=step2.output_path,
                    output_dir=output,
                    policy=ProgramGenerationPolicy(),
                    generation_agent=None,
                    review_agent=ReviewAgent(),
                    candidates_path=candidates_path,
                )
            validation = read_json(output / "step3_validation.json")
            self.assertIn("泄露工具名", validation["rejections"][0]["reasons"][0])

    def test_step4_writes_all_three_scoring_layers(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, _, step4, _ = self.prepare_through_step4(Path(temporary))
            task = read_records(step4.output_path)[0]
            self.assertTrue(task["scoring_ready"])
            self.assertEqual(task["rubric_total_score"], 14)
            self.assertIn("def verify(", task["verifier_code"])
            self.assertIn("def verify_state(", task["state_verifier_code"])

    def test_step5_retries_infrastructure_then_publishes_valid_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            step1, _, _, step4, output = self.prepare_through_step4(Path(temporary))
            task = read_records(step4.output_path)[0]
            calls = 0

            def solve(**_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return SolverResult(None, [], None, "", "MCP launch failed", True)
                return SolverResult(
                    task["ground_truth"]["candidate_answer"],
                    task["solution_trace"],
                    task["ground_truth"]["final_state"],
                    "",
                    None,
                )

            result = run_step5(
                step1_path=step1,
                step4_path=step4.output_path,
                output_dir=output,
                policy=ProgramGenerationPolicy(
                    difficulty_eval_runs=2,
                    minimum_passing_runs=1,
                    infrastructure_retries=1,
                ),
                model="test",
                rubric_agent=RubricAgent(),
                solve_fn=solve,
            )
            evaluated = read_records(result.difficulty_path)[0]
            self.assertEqual(calls, 3)
            self.assertEqual(evaluated["valid_rollouts"], 2)
            self.assertEqual(evaluated["pass_count"], 2)
            self.assertEqual(len(evaluated["infrastructure_failures"]), 1)
            self.assertEqual(result.published, 1)
            final = read_json(result.final_path)
            self.assertEqual(len(final), 1)
            self.assertIsInstance(final[0]["environment_summary"], str)
            self.assertIn("record_sets", final[0]["environment_contract"])
            self.assertEqual(
                final[0]["ground_truth_state"],
                task["ground_truth"]["final_state"],
            )

    def test_step5_sends_zero_success_task_to_rework(self):
        with tempfile.TemporaryDirectory() as temporary:
            step1, _, _, step4, output = self.prepare_through_step4(Path(temporary))
            task = read_records(step4.output_path)[0]

            def solve(**_kwargs):
                return SolverResult(
                    {"selected_item_id": "a", "selected_score": 4, "status": "open"},
                    [],
                    task["ground_truth"]["init_state"],
                    "",
                    None,
                )

            result = run_step5(
                step1_path=step1,
                step4_path=step4.output_path,
                output_dir=output,
                policy=ProgramGenerationPolicy(difficulty_eval_runs=2),
                model="test",
                rubric_agent=RubricAgent(),
                solve_fn=solve,
            )
            rework = read_records(result.rework_path)[0]
            self.assertEqual(rework["difficulty_bucket"], "unsolved_rework")
            self.assertIn("zero_successful_rollouts", rework["publish_reason"])
            self.assertEqual(read_json(result.final_path), [])

    def test_solver_mcp_exposes_public_tools_and_writes_final_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tasks"
            step1 = run_step1(environment_package=self.make_package(root), output_dir=output)
            config = root / "mcp.json"
            trace = root / "trace.jsonl"
            state = root / "state.json"
            write_json(config, {
                "step1_path": str(step1),
                "trace_path": str(trace),
                "state_path": str(state),
                "max_tool_calls": 5,
            })
            requests = "\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "select_candidate", "arguments": {"item_id": "c"}}}),
            ]) + "\n"
            stdout = StringIO()
            serve(config, StringIO(requests), stdout)
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            tools = responses[1]["result"]["tools"]
            self.assertNotIn("internal", tools[0])
            self.assertTrue(responses[2]["result"]["structuredContent"]["success"])
            self.assertTrue(state.is_file())
            self.assertTrue(trace.is_file())


if __name__ == "__main__":
    unittest.main()
