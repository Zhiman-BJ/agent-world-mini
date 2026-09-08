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
    execute_reference_program,
    run_step1,
    run_step2,
    run_step3,
    run_step4,
    run_step5,
    run_step6,
    run_step7,
    run_step8,
    run_step9,
    run_step10,
    run_step11,
    run_step12,
)
from task_gen.program_form.utils.io import read_records
from task_gen.program_form.utils.solver import SolverResult
from task_gen.program_form.utils.solver_mcp import serve
from task_gen.program_form.steps.step9_filter_trace_state_jsonl_in_jsonl_out import (
    process_single_task as process_step9_task,
)
from task_gen.program_form.steps.step11_difficulty_eval_jsonl_in_jsonl_out import (
    _judge_rubric,
)


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


LIST_CODE = '''
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        rows = connection.execute(
            "SELECT item_id, score, eligible, status FROM candidates ORDER BY item_id"
        ).fetchall()
    finally:
        connection.close()
    return {
        "success": True,
        "data": {
            "items": [
                {"item_id": row[0], "score": row[1], "eligible": bool(row[2]), "status": row[3]}
                for row in rows
            ]
        },
    }
'''


GET_CODE = '''
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
'''


UPDATE_CODE = '''
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        row = connection.execute(
            "SELECT eligible, status FROM candidates WHERE item_id = ?",
            (arguments["item_id"],),
        ).fetchone()
        if row is None:
            return {"success": False, "error": {"code": "not_found", "path": "$.item_id", "message": "Item not found.", "retryable": False}}
        if not bool(row[0]) or row[1] != "open":
            return {"success": False, "error": {"code": "invalid_state", "path": "$.item_id", "message": "Item is not selectable.", "retryable": False}}
        connection.execute(
            "UPDATE candidates SET status = 'selected' WHERE item_id = ?",
            (arguments["item_id"],),
        )
        connection.commit()
    finally:
        connection.close()
    return {"success": True, "data": {"item_id": arguments["item_id"], "status": "selected"}}
'''


FAILED_MUTATION_CODE = '''
def run(arguments, context):
    import sqlite3
    connection = sqlite3.connect(context.records_path)
    try:
        connection.execute("UPDATE candidates SET status = 'selected' WHERE item_id = 'c'")
        connection.commit()
    finally:
        connection.close()
    return {"success": False, "error": {"code": "invalid_state", "path": "$.item_id", "message": "Simulated business failure.", "retryable": False}}
'''


class ProgramFormV2Tests(unittest.TestCase):
    def make_package(self, root: Path, *, access: str = "copy_on_write") -> Path:
        package = root / "environment"
        state = package / "state"
        (state / "filesystem_scopes").mkdir(parents=True)
        environment = {
            "schema_version": "2.0",
            "environment_id": "candidate_review_v2",
            "name": "Candidate Review Environment",
            "summary": "A structured environment for reviewing and selecting eligible candidates.",
            "description": "This environment contains review candidates with stable identifiers, eligibility evidence, scores, and workflow status. It supports querying the candidate set, inspecting individual records, and recording a selection in an isolated copy when the declared access policy permits writes.",
            "record_sets": [
                {
                    "record_set_id": "candidates",
                    "name": "Candidates",
                    "description": "One record represents a candidate considered in the current review.",
                    "access": access,
                    "key_fields": ["item_id"],
                    "fields": {
                        "item_id": {"type": "string", "description": "Stable candidate identifier.", "nullable": False},
                        "score": {"type": "integer", "description": "Candidate review score.", "nullable": False},
                        "eligible": {"type": "boolean", "description": "Whether the candidate passes the hard eligibility gate.", "nullable": False},
                        "status": {"type": "string", "description": "Current review workflow status.", "nullable": False, "enum": ["open", "selected"]},
                    },
                }
            ],
            "relationships": [],
            "filesystem_scopes": [],
        }
        (package / "environment.json").write_text(
            json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (package / "validation.json").write_text(
            json.dumps({"valid": True, "errors": [], "warnings": []}), encoding="utf-8"
        )
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
                "description": "List all current review candidates with eligibility, score, and workflow status.",
                "inputSchema": closed_object({}, []),
                "outputSchema": tool_output(
                    {"items": {"type": "array", "items": ITEM_SCHEMA}}, ["items"]
                ),
                "internal": {"code": LIST_CODE},
            },
            {
                "name": "get_candidate",
                "description": "Retrieve one candidate by a stable identifier returned by the candidate list.",
                "inputSchema": closed_object({"item_id": {"type": "string"}}, ["item_id"]),
                "outputSchema": tool_output({"item": ITEM_SCHEMA}, ["item"]),
                "internal": {"code": GET_CODE},
            },
            {
                "name": "select_candidate",
                "description": "Select one open and eligible candidate; this changes its workflow status in the task copy.",
                "inputSchema": closed_object({"item_id": {"type": "string"}}, ["item_id"]),
                "outputSchema": tool_output(
                    {"item_id": {"type": "string"}, "status": {"type": "string", "const": "selected"}},
                    ["item_id", "status"],
                ),
                "internal": {"code": UPDATE_CODE},
            },
        ]
        (package / "tools.json").write_text(
            json.dumps({"tools": tools}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return package

    @staticmethod
    def add_failed_mutation_tool(package: Path) -> None:
        path = package / "tools.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tools"].append(
            {
                "name": "failed_candidate_update",
                "description": "Exercise a rejected candidate update for runtime atomicity validation.",
                "inputSchema": closed_object({}, []),
                "outputSchema": tool_output({}, []),
                "internal": {"code": FAILED_MUTATION_CODE},
            }
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def solution() -> str:
        return '''
listed = call_tool("list_candidates", {})
eligible = []
for summary in listed["data"]["items"]:
    detail = call_tool("get_candidate", {"item_id": summary["item_id"]})
    item = detail["data"]["item"]
    if item["eligible"] and item["status"] == "open":
        eligible.append(item)
eligible = sorted(eligible, key=lambda item: (-item["score"], item["item_id"]))
winner = eligible[0]
updated = call_tool("select_candidate", {"item_id": winner["item_id"]})
final_answer = {"selected_item_id": winner["item_id"], "selected_score": winner["score"], "status": updated["data"]["status"]}
'''.strip()

    @staticmethod
    def answer_schema() -> dict:
        return closed_object(
            {
                "selected_item_id": {"type": "string"},
                "selected_score": {"type": "integer"},
                "status": {"type": "string", "const": "selected"},
            },
            ["selected_item_id", "selected_score", "status"],
        )

    def candidate_payload(self) -> dict:
        return {
            "candidates": [
                {
                    "task_internal": "Review the current candidate pool, exclude anyone who fails the eligibility gate or is no longer open for selection, and choose the highest-scoring remaining candidate. If eligible candidates have the same score, use the stable alphabetical identifier order as the tie-break. Record the selected candidate in the review workflow and return the selected identifier, score, and resulting status.",
                    "output_schema": self.answer_schema(),
                    "solution_code": self.solution(),
                }
            ]
        }

    def test_v2_reference_execution_uses_logical_sqlite_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            package = CompleteEnvironmentPackage.load(package_path)
            self.assertEqual(package.package_format, "v2")
            self.assertNotIn("internal", package.public_environment()["tools"][0])
            result = execute_reference_program(package, self.solution(), self.answer_schema())
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.answer["selected_item_id"], "c")
            self.assertEqual(len(result.trace), 5)
            self.assertEqual(result.state_diff["changed_assets"], ["candidates"])
            self.assertEqual(result.state_diff["record_sets"]["candidates"]["updated"], ['["c"]'])
            connection = sqlite3.connect(package_path / "state" / "records.sqlite")
            try:
                statuses = dict(connection.execute("SELECT item_id, status FROM candidates"))
            finally:
                connection.close()
            self.assertEqual(statuses, {"a": "open", "b": "open", "c": "open"})

    def test_v2_runtime_rejects_read_only_record_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = CompleteEnvironmentPackage.load(
                self.make_package(Path(temporary), access="read_only")
            )
            with CompleteEnvironmentRuntime(package) as runtime:
                with self.assertRaisesRegex(RuntimeError, "non_writable_state"):
                    runtime.call("select_candidate", {"item_id": "c"})

    def test_v2_runtime_rolls_back_business_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_path = self.make_package(Path(temporary))
            self.add_failed_mutation_tool(package_path)
            package = CompleteEnvironmentPackage.load(package_path)
            with CompleteEnvironmentRuntime(package) as runtime:
                before = runtime.snapshot()
                with self.assertRaisesRegex(RuntimeError, "业务失败后仍修改"):
                    runtime.call("failed_candidate_update", {})
                self.assertEqual(runtime.snapshot(), before)

    def test_v2_offline_candidate_reaches_ground_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            candidate_path = root / "candidates.json"
            candidate_path.write_text(
                json.dumps(self.candidate_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            output = root / "tasks"
            policy = ProgramGenerationPolicy(
                    task_count=1,
                    min_tool_calls=5,
                    min_distinct_tools=3,
                    clean_replays=2,
                    require_state_change=True,
                )
            step1_path = run_step1(
                environment_package=package_path,
                output_dir=output,
            )
            step2 = run_step2(
                step1_path=step1_path,
                output_dir=output,
                policy=policy,
                agent=None,
                candidates_path=candidate_path,
            )
            step3 = run_step3(
                step1_path=step1_path,
                step2_path=step2.output_path,
                output_dir=output,
                policy=policy,
            )
            step4 = run_step4(
                step1_path=step1_path,
                step3_path=step3.jsonl_path,
                output_dir=output,
                policy=policy,
            )
            task = read_records(step4.output_path)[0]
            self.assertEqual(task["ground_truth"]["candidate_answer"]["selected_item_id"], "c")
            self.assertEqual(task["ground_truth"]["state_diff"]["changed_assets"], ["candidates"])

    def test_v2_steps_exchange_explicit_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            imported = root / "imported_candidates.json"
            imported.write_text(
                json.dumps(self.candidate_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            output = root / "tasks"
            policy = ProgramGenerationPolicy(
                task_count=1,
                min_tool_calls=5,
                min_distinct_tools=3,
                clean_replays=2,
                require_state_change=True,
            )

            step1_path = run_step1(
                environment_package=package_path,
                output_dir=output,
            )
            step2 = run_step2(
                step1_path=step1_path,
                output_dir=output,
                policy=policy,
                candidates_path=imported,
            )
            step3 = run_step3(
                step1_path=step1_path,
                step2_path=step2.output_path,
                output_dir=output,
                policy=policy,
            )
            step4 = run_step4(
                step1_path=step1_path,
                step3_path=step3.jsonl_path,
                output_dir=output,
                policy=policy,
            )
            step5 = run_step5(step4_path=step4.output_path, output_dir=output)
            step6 = run_step6(
                step5_path=step5.output_path,
                output_dir=output,
                policy=policy,
            )
            self.assertTrue(step1_path.is_file())
            self.assertTrue(step2.output_path.is_file())
            self.assertTrue(step3.jsonl_path.is_file())
            self.assertEqual(step4.ground_truth_count, 1)
            self.assertEqual(step5.generated, 1)
            self.assertEqual(step6.succeeded, 1)

    def test_generation_agent_cannot_mutate_authoring_state(self):
        class MutatingAgent:
            def run(self, _prompt: str, *, working_directory: Path) -> str:
                connection = sqlite3.connect(working_directory / "state" / "records.sqlite")
                try:
                    connection.execute("UPDATE candidates SET status = 'selected' WHERE item_id = 'c'")
                    connection.commit()
                finally:
                    connection.close()
                (working_directory / "candidates.json").write_text(
                    json.dumps(ProgramFormV2Tests().candidate_payload()), encoding="utf-8"
                )
                return "done"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            output = root / "tasks"
            with self.assertRaisesRegex(RuntimeError, "0/1"):
                step1_path = run_step1(
                    environment_package=package_path,
                    output_dir=output,
                )
                run_step2(
                    step1_path=step1_path,
                    output_dir=output,
                    agent=MutatingAgent(),
                    policy=ProgramGenerationPolicy(task_count=1, max_repair_rounds=0),
                )
            validation = json.loads((output / "step2_validation.json").read_text(encoding="utf-8"))
            self.assertIn("修改了只读初始 state", validation["rejections"][0]["reasons"][0])

    def test_step1_rejects_partial_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tasks"
            receipt = run_step1(
                environment_package=self.make_package(root),
                output_dir=output,
            )
            receipt.unlink()
            with self.assertRaisesRegex(FileExistsError, "部分产物"):
                run_step1(
                    environment_package=root / "environment",
                    output_dir=output,
                )

    def test_solver_mcp_exposes_public_contract_and_executes_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tasks"
            step1_path = run_step1(
                environment_package=self.make_package(root),
                output_dir=output,
            )
            config = root / "mcp.json"
            config.write_text(
                json.dumps({
                    "step1_path": str(step1_path),
                    "trace_path": str(root / "trace.jsonl"),
                    "state_path": str(root / "state.json"),
                    "max_tool_calls": 3,
                }),
                encoding="utf-8",
            )
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_candidates", "arguments": {}},
                },
            ]
            stdin = StringIO("".join(json.dumps(item) + "\n" for item in requests))
            stdout = StringIO()
            serve(config, stdin=stdin, stdout=stdout)
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            listed_tool = responses[1]["result"]["tools"][0]
            self.assertNotIn("internal", listed_tool)
            self.assertIn("inputSchema", listed_tool)
            self.assertTrue(responses[2]["result"]["structuredContent"]["success"])

    def test_step9_retains_split_vote_like_omnia(self):
        class SplitJudge:
            calls = 0

            def run(self, _prompt: str, *, working_directory: Path) -> str:
                decisions = [False, False, True]
                decision = decisions[self.calls]
                self.calls += 1
                (working_directory / "review.json").write_text(
                    json.dumps({"consistent": decision, "reason": "test vote"}),
                    encoding="utf-8",
                )
                return "done"

        with tempfile.TemporaryDirectory() as temporary:
            task = {
                "task_id": "split_vote",
                "task_public": "Inspect the records and report the supported result.",
                "output_schema": closed_object({"result": {"type": "string"}}, ["result"]),
                "ground_truth": {"init_state": {}, "final_state": {}, "state_diff": {}},
                "solution_trace": [{"tool": "list_items", "arguments": {}, "result": {}}],
            }
            result = process_step9_task(
                task,
                public_environment={},
                policy=ProgramGenerationPolicy(trace_state_judge_runs=3),
                agent=SplitJudge(),
                debug_root=Path(temporary),
            )
            self.assertTrue(result["step9_consistent"])
            self.assertTrue(result["step9_review_required"])

    def test_step11_recomputes_model_declared_rubric_totals(self):
        class MisreportingJudge:
            def run(self, _prompt: str, *, working_directory: Path) -> str:
                (working_directory / "rubric_review.json").write_text(
                    json.dumps({
                        "earned_score": 0,
                        "total_score": 999,
                        "avg_result": 0.0,
                        "summary": "declared totals are intentionally wrong",
                        "items": [
                            {
                                "tag": "G1",
                                "category": "general",
                                "points": 3,
                                "passed": True,
                                "earned_score": 3,
                                "reason": "satisfied",
                            }
                        ],
                    }),
                    encoding="utf-8",
                )
                return "done"

        task = {
            "task_public": "Return the supported result.",
            "output_schema": closed_object({"result": {"type": "string"}}, ["result"]),
            "rubric_items": [
                {"section": "general", "id": "G1", "points": 3, "text": "Return the result."}
            ],
            "ground_truth": {"init_state": {}, "final_state": {}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            score, review = _judge_rubric(
                agent=MisreportingJudge(),
                task=task,
                result=SolverResult(
                    answer={"result": "ok"},
                    trace=[],
                    final_state={},
                    raw_response='{"result":"ok"}',
                    error=None,
                ),
                run_dir=Path(temporary),
            )
        self.assertEqual(score, 1.0)
        self.assertTrue(review["passed"])
        self.assertEqual(review["earned_score"], 3)
        self.assertTrue(review["validation_errors"])

    def test_steps_7_to_12_follow_omnia_pipeline_contract(self):
        class ReviewAgent:
            def run(self, _prompt: str, *, working_directory: Path) -> str:
                if (working_directory / "rewrite_request.json").is_file():
                    (working_directory / "rewrite.json").write_text(
                        json.dumps({
                            "task_public": (
                                "Review the active candidate pool, exclude ineligible or closed "
                                "records, select the strongest remaining candidate using the "
                                "declared tie-break, record the decision, and report the outcome."
                            )
                        }),
                        encoding="utf-8",
                    )
                elif (working_directory / "consistency_request.json").is_file():
                    (working_directory / "review.json").write_text(
                        json.dumps({"consistent": True, "reason": "Trace and state match the task."}),
                        encoding="utf-8",
                    )
                elif (working_directory / "rubric_request.json").is_file():
                    (working_directory / "rubric.json").write_text(
                        json.dumps({
                            "rubric_count": 5,
                            "total_score": 14,
                            "rubrics_text": (
                                "[General]\n"
                                "G1 | 3 | Returns a complete supported result.\n"
                                "G2 | 3 | Avoids conclusions contradicted by the evidence.\n\n"
                                "[Task-Specific]\n"
                                "T1 | 3 | Selects the correct eligible candidate.\n"
                                "T2 | 3 | Records the selection in business state.\n"
                                "T3 | 2 | Reports the selected candidate and status."
                            ),
                            "explanation": "Deterministic test rubric.",
                        }),
                        encoding="utf-8",
                    )
                elif (working_directory / "rubric_judge_request.json").is_file():
                    (working_directory / "rubric_review.json").write_text(
                        json.dumps({
                            "earned_score": 14,
                            "total_score": 14,
                            "avg_result": 1.0,
                            "summary": "All rubric items passed.",
                            "items": [
                                {"tag": "G1", "category": "general", "points": 3, "passed": True, "earned_score": 3, "reason": "complete"},
                                {"tag": "G2", "category": "general", "points": 3, "passed": True, "earned_score": 3, "reason": "supported"},
                                {"tag": "T1", "category": "task_specific", "points": 3, "passed": True, "earned_score": 3, "reason": "correct"},
                                {"tag": "T2", "category": "task_specific", "points": 3, "passed": True, "earned_score": 3, "reason": "recorded"},
                                {"tag": "T3", "category": "task_specific", "points": 2, "passed": True, "earned_score": 2, "reason": "reported"},
                            ],
                        }),
                        encoding="utf-8",
                    )
                return "done"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            output = root / "tasks"
            candidate_path = root / "candidates.json"
            candidate_path.write_text(
                json.dumps(self.candidate_payload(), ensure_ascii=False), encoding="utf-8"
            )
            policy = ProgramGenerationPolicy(
                task_count=1,
                min_tool_calls=5,
                min_distinct_tools=3,
                require_state_change=True,
                consistency_runs=2,
                consistency_threshold=1,
                difficulty_eval_runs=2,
                trace_state_judge_runs=3,
            )
            step1_path = run_step1(environment_package=package_path, output_dir=output)
            step2 = run_step2(
                step1_path=step1_path,
                output_dir=output,
                policy=policy,
                candidates_path=candidate_path,
            )
            step3 = run_step3(
                step1_path=step1_path,
                step2_path=step2.output_path,
                output_dir=output,
                policy=policy,
            )
            step4 = run_step4(
                step1_path=step1_path,
                step3_path=step3.jsonl_path,
                output_dir=output,
                policy=policy,
            )
            step5 = run_step5(step4_path=step4.output_path, output_dir=output)
            step6 = run_step6(
                step5_path=step5.output_path, output_dir=output, policy=policy
            )
            reference = read_records(step6.output_path)[0]["ground_truth"]

            def solve_fn(**_kwargs):
                return SolverResult(
                    answer=reference["candidate_answer"],
                    trace=[],
                    final_state=reference["final_state"],
                    raw_response=json.dumps(reference["candidate_answer"]),
                    error=None,
                )

            step7 = run_step7(
                step1_path=step1_path,
                step6_path=step6.output_path,
                output_dir=output,
                policy=policy,
                model="test-model",
                solve_fn=solve_fn,
            )
            agent = ReviewAgent()
            step8 = run_step8(
                step1_path=step1_path,
                step7_path=step7.output_path,
                output_dir=output,
                agent=agent,
            )
            step9 = run_step9(
                step1_path=step1_path,
                step8_kept_path=step8.kept_path,
                output_dir=output,
                policy=policy,
                agent=agent,
            )
            step10 = run_step10(
                step9_kept_path=step9.kept_path,
                output_dir=output,
                policy=policy,
                agent=agent,
            )
            step11 = run_step11(
                step1_path=step1_path,
                step10_path=step10.output_path,
                output_dir=output,
                policy=policy,
                model="test-model",
                rubric_agent=agent,
                solve_fn=solve_fn,
            )
            step12 = run_step12(
                step1_path=step1_path,
                step11_path=step11.output_path,
                output_dir=output,
            )
            final = json.loads(step12.output_path.read_text(encoding="utf-8"))
            difficulty = read_records(step11.output_path)[0]
            self.assertEqual(step7.kept, 1)
            self.assertEqual(step8.kept, 1)
            self.assertEqual(step9.kept, 1)
            self.assertEqual(step10.generated, 1)
            self.assertEqual(difficulty["difficulty_bucket"], "always_solved")
            self.assertEqual(final[0]["initial_state"], "baseline_environment/state")
            self.assertNotIn("difficulty", final[0])

    def test_generation_agent_failure_is_recorded(self):
        class FailingAgent:
            def run(self, _prompt: str, *, working_directory: Path) -> str:
                raise RuntimeError("authoring failed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = self.make_package(root)
            output = root / "tasks"
            with self.assertRaisesRegex(RuntimeError, "0/1"):
                step1_path = run_step1(
                    environment_package=package_path,
                    output_dir=output,
                )
                run_step2(
                    step1_path=step1_path,
                    output_dir=output,
                    agent=FailingAgent(),
                    policy=ProgramGenerationPolicy(task_count=1, max_repair_rounds=0),
                )
            validation = json.loads((output / "step2_validation.json").read_text(encoding="utf-8"))
            self.assertIn(
                "RuntimeError: authoring failed",
                validation["rejections"][0]["reasons"][0],
            )


if __name__ == "__main__":
    unittest.main()
