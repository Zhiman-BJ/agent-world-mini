from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from task_gen.program import ProgramGenerationPolicy, validate_solution_code
from task_gen.program.contracts import Config
from task_gen.program.run_pipeline import build_parser
from task_gen.program.step_1_task_research import (
    _research_payload_from_handoff,
    _research_payload_from_response,
    _run_research_agent,
    build_task_research_prompt,
)
from task_gen.program.step_2_solution_generate import (
    _argument_leaf_paths,
    _authoring_failure_reasons,
    _canonical_payload_hash,
    _CandidateQualityService,
    _compact_failed_review,
    _solution_complexity_errors,
    _solution_complexity_profile,
    _effective_tool_call_count,
    _execution_error_message,
    _is_infrastructure_execution_failure,
    _parameter_audit_errors,
    _first_replay_difference,
    _replay_instability_error,
    _review_issue_messages,
    _review_task_solution,
    _run_candidate_quality_request,
    _replay_comparable_state,
    _run_agent_until_json,
    _terminal_quality_infrastructure_reason,
    ProgramExecutionResult,
    ProgramTaskCandidate,
    build_solution_repair_prompt,
    build_task_solution_prompt,
    build_task_solution_review_prompt,
)
from task_gen.program.utils.io import read_records, write_jsonl
from task_gen.program.utils.schema_docs import (
    STEP1_SCHEMA_DOCS,
    STEP2_SCHEMA_DOCS,
    copy_schema_docs,
)
from task_gen.program.step_2_solution_generate import (
    _argument_audit_paths,
    _ExecutionBudget,
    build_task_solution_review_prompt as build_current_review_prompt,
)


class ProgramFormContractTests(unittest.TestCase):
    def test_production_agent_timeout_defaults_to_one_hour(self):
        arguments = build_parser().parse_args(["--output-dir", "/tmp/program-test"])
        self.assertEqual(arguments.agent_timeout_seconds, 3600)
        self.assertEqual(arguments.execution_timeout_seconds, 1000.0)
        self.assertEqual(Config().agent_timeout_seconds, 3600)
        self.assertEqual(Config().policy.execution_timeout_seconds, 1000.0)

    def test_quality_service_stop_is_bounded(self):
        service = object.__new__(_CandidateQualityService)
        service.stop_event = Mock()
        service.thread = Mock()
        service.thread.is_alive.return_value = True
        service.failure = None

        with self.assertRaisesRegex(TimeoutError, "仍未结束"):
            service.stop()

        service.stop_event.set.assert_called_once_with()
        service.thread.join.assert_called_once()
        self.assertEqual(service.thread.join.call_args.kwargs["timeout"], 30.0)

    def test_quality_reuses_execution_and_replays_after_semantic_review(self):
        candidate = ProgramTaskCandidate(
            archetype_id="test",
            task_internal="internal",
            workspace_brief="workspace",
            task_summary="summary",
            task_public="Use the available evidence and return the result.",
            output_schema={"type": "object"},
            task_resources={"files": [], "record_sets": [], "allowed_tools": []},
            solution_code="final_answer = {}",
        )
        execution = ProgramExecutionResult(
            success=True,
            answer={},
            trace=[],
            initial_state={},
            final_state={},
            state_diff={},
        )
        payload = {"candidates": [{"placeholder": True}]}
        events: list[str] = []

        def preflight(**kwargs):
            events.append("preflight")
            self.assertFalse(kwargs["run_clean_replays"])
            kwargs["evidence"].candidate = candidate
            kwargs["evidence"].execution = execution
            return {"passed": True, "stage": "passed", "errors": [], "metrics": {}}

        def review(**kwargs):
            events.append("review")
            self.assertIs(kwargs["execution"], execution)
            return {
                "accepted": True,
                "reason": "The task, execution evidence, and returned answer are aligned.",
                "parameter_audit": [],
                "issues": [],
            }, []

        def replay(**kwargs):
            events.append("replay")
            self.assertIs(kwargs["first"], execution)
            return [execution], []

        package = Mock()
        package.public_environment.return_value = {"tools": []}
        with tempfile.TemporaryDirectory() as temporary:
            authoring = Path(temporary)
            (authoring / "candidate.draft.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            request = {
                "request_id": "request-1",
                "candidate": "candidate.draft.json",
                "candidate_sha256": _canonical_payload_hash(payload),
                "clean_replays": 2,
                "execution_timeout_seconds": 30,
            }
            with (
                patch(
                    "task_gen.program.step_2_solution_generate._preflight_candidate_payload",
                    side_effect=preflight,
                ),
                patch(
                    "task_gen.program.step_2_solution_generate._review_task_solution",
                    side_effect=review,
                ),
                patch(
                    "task_gen.program.step_2_solution_generate._replay_solution",
                    side_effect=replay,
                ),
                patch(
                    "task_gen.program.step_2_solution_generate.execute_solution_code",
                    side_effect=AssertionError("quality request must reuse preflight execution"),
                ),
            ):
                report = _run_candidate_quality_request(
                    authoring=authoring,
                    package=package,
                    research={},
                    schema={},
                    review_agent=Mock(),
                    request=request,
                    review_dir=authoring / "review",
                )

        self.assertTrue(report["passed"])
        self.assertEqual(report["clean_replay_count"], 2)
        self.assertEqual(events, ["preflight", "review", "replay"])

    def test_replay_instability_reports_nested_tool_result_path(self):
        reference = [{
            "tool": "convert_reference_field",
            "result": {"data": {"sha256": "aaa", "size_bytes": 10}},
        }]
        replay = [{
            "tool": "convert_reference_field",
            "result": {"data": {"sha256": "bbb", "size_bytes": 10}},
        }]

        self.assertEqual(
            _first_replay_difference(reference, replay),
            ("$[0].result.data.sha256", "aaa", "bbb"),
        )
        message = _replay_instability_error(
            index=1,
            label="工具返回结果",
            reference=reference,
            replay=replay,
        )
        self.assertIn("$[0].result.data.sha256", message)
        self.assertIn('首次="aaa"', message)
        self.assertIn('重放="bbb"', message)

    def test_execution_budget_counts_only_solution_source_lines(self):
        budget = _ExecutionBudget(timeout_seconds=60, max_lines=2)

        def external_runtime_work():
            total = 0
            total += 1
            total += 1
            total += 1
            return total

        previous_trace = sys.gettrace()
        try:
            sys.settrace(budget.trace)
            self.assertEqual(external_runtime_work(), 3)
            exec(compile("first = 1\nsecond = 2\n", "<solution-code>", "exec"), {})
        finally:
            sys.settrace(previous_trace)

        self.assertEqual(budget.lines, 2)

    def test_current_review_prompt_defines_zero_based_call_indices(self):
        prompt = build_current_review_prompt()
        self.assertIn("used_tool_contracts", prompt)
        self.assertIn("调用序号严格从 `0` 开始", prompt)
        self.assertIn("`source_call_indices` 也使用同一套从 `0` 开始", prompt)
        self.assertIn("禁止先创建 `{}`", prompt)
        self.assertIn('`path="/derived_network"`', prompt)
        self.assertIn('`path="/data"`', prompt)

    def test_only_parent_quality_result_marks_terminal_infrastructure(self):
        self.assertIsNotNone(
            _terminal_quality_infrastructure_reason({
                "terminal": True,
                "stage": "infrastructure",
                "errors": [{
                    "code": "tool_runtime_unavailable",
                    "message": "父进程工具运行库不可用",
                }],
            })
        )
        self.assertIsNone(
            _terminal_quality_infrastructure_reason({
                "terminal": False,
                "stage": "execution",
                "errors": [{
                    "code": "solution_execution",
                    "message": "嵌套探测出现 bwrap: Operation not permitted",
                }],
            })
        )

    def test_research_description_floor_requires_substance_without_padding(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "task_gen"
            / "program"
            / "schemas"
            / "task_research.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["task_archetypes"]["items"]
            ["properties"]["description"]["minLength"],
            80,
        )

    def test_review_issue_messages_accepts_structured_objects(self):
        self.assertEqual(
            _review_issue_messages([
                {
                    "type": "parameter_source_unresolved",
                    "call_index": 1,
                    "tool": "search_network_files",
                    "path": "/sort_by",
                    "message": "参数没有来自任务或前序工具结果",
                }
            ]),
            [
                "参数没有来自任务或前序工具结果 "
                "(type=parameter_source_unresolved, call_index=1, "
                "tool=search_network_files, path=/sort_by)"
            ],
        )

    def test_export_report_is_audited_as_one_composite_parameter(self):
        self.assertEqual(
            _argument_audit_paths(
                "export_analysis_artifact",
                {
                    "scope_id": "reports",
                    "data": {"summary": {"count": 3}, "items": [1, 2]},
                },
            ),
            {"/scope_id", "/data"},
        )
        self.assertEqual(
            _argument_audit_paths(
                "other_tool",
                {"data": {"summary": {"count": 3}}},
            ),
            {"/data/summary/count"},
        )

    def test_each_agent_prompt_keeps_its_step_responsibility_visible(self):
        policy = ProgramGenerationPolicy()
        research = build_task_research_prompt(1)
        generation = build_task_solution_prompt(1, policy)
        repair = build_solution_repair_prompt(1)
        review = build_task_solution_review_prompt()

        self.assertIn("你没有本项目此前的对话上下文", research)
        self.assertIn("不要生成具体题目、标准答案或程序", research)
        self.assertIn("数据库中的记录集合", research)
        self.assertIn("task_research.json", research)
        self.assertIn("不要用 apply_patch 写这个文件", research)
        self.assertIn("json.dump", research)
        self.assertIn("你没有本项目此前的对话上下文", generation)
        self.assertIn("给未来执行者看的任务正文", generation)
        self.assertIn("`state/`", generation)
        self.assertIn("solution_code", generation)
        self.assertIn("task_resources", generation)
        self.assertIn("任务的深度优先来自结果之间的因果关系", generation)
        self.assertIn("每一个文件都必须在正文中独立写出完整相对路径", generation)
        self.assertIn("tool_runtime_unavailable", generation)
        self.assertIn("你没有此前对话上下文", repair)
        self.assertIn("真实执行错误", repair)
        self.assertIn("把 `task_public` 当作用户发来的完整原始要求", review)
        self.assertIn("只根据下面这些真实证据判断", review)
        self.assertNotIn("task_review", review)
        self.assertNotIn("solution_review", review)
        self.assertIn("parameter_audit", review)

    def test_policy_rejects_invalid_generation_limits(self):
        policy = ProgramGenerationPolicy()
        self.assertEqual(policy.max_repair_rounds, 6)
        self.assertNotIn("min_tool_calls", policy.to_dict())
        self.assertNotIn("min_distinct_tools", policy.to_dict())
        with self.assertRaisesRegex(ValueError, "clean_replays"):
            ProgramGenerationPolicy(clean_replays=1).validate()
        with self.assertRaisesRegex(ValueError, "max_repair_rounds"):
            ProgramGenerationPolicy(max_repair_rounds=-1).validate()
        with self.assertRaisesRegex(ValueError, "execution_timeout_seconds"):
            ProgramGenerationPolicy(execution_timeout_seconds=0).validate()

    def test_parameter_audit_must_cover_every_runtime_argument_leaf(self):
        trace = [{
            "tool": "inspect_item",
            "arguments": {"item_id": "A/1", "options": {"limit": 3}},
        }]
        self.assertEqual(
            _argument_leaf_paths(trace[0]["arguments"]),
            {"/item_id", "/options/limit"},
        )
        review = {
            "parameter_audit": [{
                "call_index": 0,
                "tool": "inspect_item",
                "parameters": [{
                    "path": "/item_id",
                    "source": "task",
                    "source_call_indices": [],
                    "evidence": "The public task names A/1.",
                }],
            }],
        }
        errors = _parameter_audit_errors(review, trace)
        self.assertTrue(any("未覆盖" in error for error in errors))

    def test_parameter_audit_allows_real_structured_parent_path(self):
        trace = [{
            "tool": "inspect_item",
            "arguments": {
                "item_id": "A/1",
                "options": {"limit": 3, "flags": ["x", "y"]},
            },
        }]
        review = {
            "parameter_audit": [{
                "call_index": 0,
                "tool": "inspect_item",
                "parameters": [
                    {
                        "path": "/item_id",
                        "source": "task",
                        "source_call_indices": [],
                        "evidence": "The public task names A/1.",
                    },
                    {
                        "path": "/options",
                        "source": "task",
                        "source_call_indices": [],
                        "evidence": "The public task supplies the complete options object.",
                    },
                ],
            }],
        }
        self.assertEqual(_parameter_audit_errors(review, trace), [])

    def test_parameter_audit_rejects_root_or_overlapping_parent_paths(self):
        trace = [{
            "tool": "inspect_item",
            "arguments": {"item_id": "A/1", "options": {"limit": 3}},
        }]
        review = {
            "parameter_audit": [{
                "call_index": 0,
                "tool": "inspect_item",
                "parameters": [
                    {
                        "path": "/",
                        "source": "task",
                        "source_call_indices": [],
                        "evidence": "An invalid blanket provenance claim.",
                    },
                    {
                        "path": "/options",
                        "source": "task",
                        "source_call_indices": [],
                        "evidence": "The task supplies the options object.",
                    },
                    {
                        "path": "/options/limit",
                        "source": "task",
                        "source_call_indices": [],
                        "evidence": "A duplicate nested provenance claim.",
                    },
                ],
            }],
        }
        errors = _parameter_audit_errors(review, trace)
        self.assertTrue(any("非法 ['/']" in error for error in errors))
        self.assertTrue(any("重叠 ['/options/limit']" in error for error in errors))

    def test_failed_review_feedback_keeps_only_unresolved_parameters(self):
        compact = _compact_failed_review({
            "accepted": False,
            "reason": "A" * 4000,
            "issues": ["missing provenance"],
            "parameter_audit": [
                {
                    "call_index": 0,
                    "tool": "search_items",
                    "parameters": [
                        {"path": "/query", "source": "unresolved", "evidence": ""},
                        {"path": "/project_id", "source": "task", "evidence": "P1"},
                    ],
                },
            ],
        })
        self.assertEqual(len(compact["reason"]), 3000)
        self.assertEqual(compact["parameter_audit_summary"]["reviewed_call_count"], 1)
        self.assertEqual(compact["parameter_audit_summary"]["unresolved_parameter_count"], 1)
        self.assertEqual(compact["parameter_audit"][0]["parameters"][0]["path"], "/query")

    def test_semantic_review_retries_invalid_empty_rejection(self):
        class FlakyReviewAgent:
            def __init__(self):
                self.calls = 0

            def run(self, prompt, *, working_directory):
                self.calls += 1
                if self.calls == 1:
                    (working_directory / "review.json").write_text(json.dumps({
                        "accepted": False,
                        "reason": "The candidate needs changes, but this first response is malformed.",
                        "parameter_audit": [],
                        "issues": [{"description": "missing message field"}],
                    }), encoding="utf-8")
                else:
                    (working_directory / "review.json").write_text(json.dumps({
                        "accepted": True,
                        "reason": "The candidate satisfies every stated requirement using grounded evidence.",
                        "parameter_audit": [],
                        "issues": [],
                    }), encoding="utf-8")
                return "done"

        candidate = ProgramTaskCandidate(
            archetype_id="test",
            task_internal="internal",
            workspace_brief="workspace",
            task_summary="summary",
            task_public="Complete the grounded task.",
            output_schema={"type": "object"},
            task_resources={"files": [], "record_sets": []},
            solution_code="final_answer = {}",
        )
        execution = ProgramExecutionResult(
            success=True, answer={}, trace=[], initial_state={}, final_state={}, state_diff={}
        )
        agent = FlakyReviewAgent()
        with tempfile.TemporaryDirectory() as temporary:
            audit_dir = Path(temporary) / "review"
            review, errors = _review_task_solution(
                agent=agent,
                candidate=candidate,
                execution=execution,
                public_tools=[],
                review_dir=audit_dir,
            )
            self.assertTrue((audit_dir / "attempt_01/review.json").is_file())
            self.assertTrue((audit_dir / "attempt_02/review.json").is_file())
        self.assertEqual(agent.calls, 2)
        self.assertEqual(errors, [])
        self.assertTrue(review["accepted"])

    def test_semantic_review_does_not_retry_actionable_rejection(self):
        class RejectingReviewAgent:
            def __init__(self):
                self.calls = 0

            def run(self, prompt, *, working_directory):
                self.calls += 1
                (working_directory / "review.json").write_text(json.dumps({
                    "accepted": False,
                    "reason": "The final answer omits a result explicitly required by the public task.",
                    "parameter_audit": [],
                    "issues": ["The final answer is missing the required selected identifier."],
                }), encoding="utf-8")
                return "done"

        candidate = ProgramTaskCandidate(
            archetype_id="test", task_internal="internal",
            workspace_brief="workspace", task_summary="summary",
            task_public="Return the selected identifier.",
            output_schema={"type": "object"},
            task_resources={"files": [], "record_sets": []},
            solution_code="final_answer = {}",
        )
        execution = ProgramExecutionResult(
            success=True, answer={}, trace=[], initial_state={}, final_state={}, state_diff={}
        )
        agent = RejectingReviewAgent()
        with tempfile.TemporaryDirectory() as temporary:
            review, errors = _review_task_solution(
                agent=agent, candidate=candidate, execution=execution,
                public_tools=[], review_dir=Path(temporary) / "review",
            )
        self.assertEqual(agent.calls, 1)
        self.assertFalse(review["accepted"])
        self.assertEqual(errors, ["The final answer is missing the required selected identifier."])

    def test_semantic_review_retries_reviewer_audit_contract_error(self):
        class FlakyAuditAgent:
            def __init__(self):
                self.calls = 0

            def run(self, prompt, *, working_directory):
                self.calls += 1
                audit = []
                if self.calls > 1:
                    audit = [{
                        "call_index": 0,
                        "tool": "inspect_item",
                        "parameters": [{
                            "path": "/item_id",
                            "source": "task",
                            "source_call_indices": [],
                            "evidence": "The public task explicitly identifies item A.",
                        }],
                    }]
                (working_directory / "review.json").write_text(json.dumps({
                    "accepted": True,
                    "reason": "The execution completes the public task and returns the requested item.",
                    "parameter_audit": audit,
                    "issues": [],
                }), encoding="utf-8")
                return "done"

        candidate = ProgramTaskCandidate(
            archetype_id="test", task_internal="internal",
            workspace_brief="workspace", task_summary="summary",
            task_public="Inspect item A and return it.",
            output_schema={"type": "object"},
            task_resources={"files": [], "record_sets": []},
            solution_code="final_answer = {}",
        )
        execution = ProgramExecutionResult(
            success=True,
            answer={"item_id": "A"},
            trace=[{"tool": "inspect_item", "arguments": {"item_id": "A"}}],
            initial_state={}, final_state={}, state_diff={},
        )
        agent = FlakyAuditAgent()
        with tempfile.TemporaryDirectory() as temporary:
            review, errors = _review_task_solution(
                agent=agent, candidate=candidate, execution=execution,
                public_tools=[], review_dir=Path(temporary) / "review",
            )
        self.assertEqual(agent.calls, 2)
        self.assertEqual(errors, [])
        self.assertTrue(review["accepted"])

    def test_semantic_review_does_not_retry_unresolved_parameter(self):
        class UnresolvedAuditAgent:
            def __init__(self):
                self.calls = 0

            def run(self, prompt, *, working_directory):
                self.calls += 1
                (working_directory / "review.json").write_text(json.dumps({
                    "accepted": True,
                    "reason": "The requested item is returned, but one tool argument has no stated source.",
                    "parameter_audit": [{
                        "call_index": 0,
                        "tool": "inspect_item",
                        "parameters": [{
                            "path": "/item_id",
                            "source": "unresolved",
                            "source_call_indices": [],
                            "evidence": "No task text or earlier result supplies this identifier.",
                        }],
                    }],
                    "issues": [],
                }), encoding="utf-8")
                return "done"

        candidate = ProgramTaskCandidate(
            archetype_id="test", task_internal="internal",
            workspace_brief="workspace", task_summary="summary",
            task_public="Return the relevant item.",
            output_schema={"type": "object"},
            task_resources={"files": [], "record_sets": []},
            solution_code="final_answer = {}",
        )
        execution = ProgramExecutionResult(
            success=True, answer={"item_id": "A"},
            trace=[{"tool": "inspect_item", "arguments": {"item_id": "A"}}],
            initial_state={}, final_state={}, state_diff={},
        )
        agent = UnresolvedAuditAgent()
        with tempfile.TemporaryDirectory() as temporary:
            _, errors = _review_task_solution(
                agent=agent, candidate=candidate, execution=execution,
                public_tools=[], review_dir=Path(temporary) / "review",
            )
        self.assertEqual(agent.calls, 1)
        self.assertTrue(any("参数来源无法确定" in error for error in errors))

    def test_authoring_failure_preserves_last_checker_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            authoring = Path(temporary)
            (authoring / "candidate_check_result.json").write_text(json.dumps({
                "stage": "semantic_review",
                "errors": [{
                    "code": "semantic_review",
                    "location": "$.candidates[0]",
                    "message": "remove ungrounded limit",
                }],
            }), encoding="utf-8")
            reasons = _authoring_failure_reasons(TimeoutError("late"), authoring)
        self.assertEqual(len(reasons), 2)
        self.assertIn("remove ungrounded limit", reasons[1])

    def test_complexity_rejects_a_single_linear_dependency(self):
        linear = """source = call_tool("inspect_source", {"path": "input.gds"})
written = call_tool("convert", {"path": "input.gds", "output": "output.oas"})
audit = call_tool("audit", {"path": written["data"]["output"]})
ready = audit["data"]["identical"] and written["success"]
final_answer = {"ready": ready}"""
        profile = _solution_complexity_profile(linear)
        self.assertEqual(profile.max_dependency_depth, 2)
        self.assertTrue(any("运行时推理" in error for error in _solution_complexity_errors(linear)))

    def test_complexity_accepts_two_link_decision_chain(self):
        dependent = """listed = call_tool("list_items", {})
chosen = listed["data"]["items"][0]
detail = call_tool("inspect_item", {"item_id": chosen["id"]})
if detail["data"]["eligible"]:
    action = "approve"
else:
    action = "reject"
updated = call_tool("apply_decision", {"item_id": detail["data"]["id"], "action": action})
verified = call_tool("inspect_item", {"item_id": updated["data"]["id"]})
final_answer = {"status": verified["data"]["status"]}"""
        profile = _solution_complexity_profile(dependent)
        self.assertGreaterEqual(profile.max_dependency_depth, 3)
        self.assertGreaterEqual(profile.business_decisions, 1)
        self.assertEqual(_solution_complexity_errors(dependent), [])

    def test_solution_language_rejects_direct_environment_access(self):
        errors = validate_solution_code(
            'value = open("state/data.json").read()\nfinal_answer = {"value": value}'
        )
        self.assertTrue(any("open" in error for error in errors))
        hardcoded = validate_solution_code('final_answer = {"status": "ok"}')
        self.assertTrue(any("final_answer" in error for error in hardcoded))
        grounded = """first = call_tool("list_items", {})
item = first["data"]["items"][0]
detail = call_tool("get_item", {"item_id": item["item_id"]})
final_answer = {"status": detail["data"]["status"]}"""
        self.assertEqual(validate_solution_code(grounded), [])

    def test_repeated_read_does_not_inflate_effective_tool_calls(self):
        unchanged = {"changed_assets": []}
        first = {
            "tool": "read_item",
            "arguments": {"item_id": "a"},
            "result": {"success": True, "data": {"score": 7}},
            "state_diff": unchanged,
        }
        second_item = {
            "tool": "read_item",
            "arguments": {"item_id": "b"},
            "result": {"success": True, "data": {"score": 9}},
            "state_diff": unchanged,
        }
        mutation = {
            "tool": "select_item",
            "arguments": {"item_id": "b"},
            "result": {"success": True, "data": {"status": "selected"}},
            "state_diff": {"changed_assets": ["items"]},
        }

        self.assertEqual(
            _effective_tool_call_count([first, first, second_item, mutation]),
            3,
        )

    def test_tool_profile_crash_is_not_sent_to_solution_repair(self):
        state = {"files": {}}
        execution = ProgramExecutionResult(
            success=False,
            answer=None,
            trace=[],
            initial_state=state,
            final_state=state,
            state_diff={},
            error_type="RuntimeError",
            error="工具 query_items 在 Profile 中执行失败：backend crashed",
        )
        self.assertTrue(_is_infrastructure_execution_failure(execution))

    def test_failed_tool_detail_survives_bare_assertion(self):
        message = _execution_error_message(AssertionError(), [{
            "tool": "inspect_gmsh_mesh",
            "result": {
                "success": False,
                "error": {
                    "code": "mesh_read_error",
                    "message": "libGLU.so.1: cannot open shared object file",
                    "retryable": False,
                },
            },
        }])
        self.assertIn("inspect_gmsh_mesh", message)
        self.assertIn("mesh_read_error", message)
        self.assertIn("retryable=false", message)
        execution = ProgramExecutionResult(
            success=False,
            answer=None,
            trace=[],
            initial_state={"files": {}},
            final_state={"files": {}},
            state_diff={},
            error_type="AssertionError",
            error=message,
        )
        self.assertTrue(_is_infrastructure_execution_failure(execution))

    def test_domain_runtime_unavailable_is_terminal_even_if_retryable(self):
        state = {"files": {}}
        execution = ProgramExecutionResult(
            success=False,
            answer=None,
            trace=[{
                "tool": "inspect_devsim_restart",
                "arguments": {"result_id": "bjt_restart_device"},
                "result": {
                    "success": False,
                    "error": {
                        "code": "devsim_unavailable",
                        "message": "DEVSIM 运行库不可用: 'PATH'",
                        "retryable": True,
                    },
                },
            }],
            initial_state=state,
            final_state=state,
            state_diff={},
            error_type="AssertionError",
            error="失败工具详情：inspect_devsim_restart [devsim_unavailable, retryable=true]",
        )
        self.assertTrue(_is_infrastructure_execution_failure(execution))

    def test_backend_shared_library_failure_is_infrastructure(self):
        state = {"files": {}}
        execution = ProgramExecutionResult(
            success=False,
            answer=None,
            trace=[{
                "tool": "run_emepy_propagation",
                "arguments": {"study_id": "bragg_grating"},
                "result": {
                    "success": False,
                    "error": {
                        "code": "backend_unavailable",
                        "message": (
                            "ImportError: libtorch_cpu.so: failed to map segment "
                            "from shared object"
                        ),
                        "retryable": True,
                    },
                },
            }],
            initial_state=state,
            final_state=state,
            state_diff={},
            error_type="AssertionError",
            error=(
                "失败工具详情：run_emepy_propagation "
                "[backend_unavailable, retryable=true]"
            ),
        )
        self.assertTrue(_is_infrastructure_execution_failure(execution))

    def test_ghostscript_unavailable_is_terminal_even_if_retryable(self):
        state = {"files": {}}
        execution = ProgramExecutionResult(
            success=False,
            answer=None,
            trace=[{
                "tool": "render_eps_result",
                "arguments": {"input_path": "result.eps"},
                "result": {
                    "success": False,
                    "error": {
                        "code": "ghostscript_unavailable",
                        "message": "Ghostscript 解释器不可用",
                        "retryable": True,
                    },
                },
            }],
            initial_state=state,
            final_state=state,
            state_diff={},
            error_type="AssertionError",
            error="失败工具详情：render_eps_result [ghostscript_unavailable, retryable=true]",
        )
        self.assertTrue(_is_infrastructure_execution_failure(execution))

    def test_layout_replay_ignores_only_embedded_container_hash(self):
        first = {
            "files": {
                "result.gds": {"sha256": "first", "size": 100},
                "report.json": {"sha256": "stable", "size": 20},
            }
        }
        second = {
            "files": {
                "result.gds": {"sha256": "second", "size": 100},
                "report.json": {"sha256": "stable", "size": 20},
            }
        }
        self.assertEqual(
            _replay_comparable_state(first),
            _replay_comparable_state(second),
        )
        second["files"]["report.json"]["sha256"] = "changed"
        self.assertNotEqual(
            _replay_comparable_state(first),
            _replay_comparable_state(second),
        )

    def test_agent_stops_when_json_handoff_is_complete(self):
        calls = []

        class Agent:
            def run_until_json_file(self, prompt, *, working_directory, required_path):
                calls.append((prompt, working_directory, required_path))
                return "done"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = _run_agent_until_json(
                Agent(),
                "review",
                working_directory=root,
                filename="review.json",
            )
            self.assertEqual(result, "done")
            self.assertEqual(calls, [("review", root, root / "review.json")])

    def test_research_agent_returns_final_json_handoff(self):
        calls = []

        class Agent:
            def run(self, prompt, *, working_directory):
                calls.append((prompt, working_directory))
                return '{"schema_version":"1.0"}'

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = _run_research_agent(
                Agent(),
                "research",
                working_directory=root,
            )
            self.assertEqual(result, '{"schema_version":"1.0"}')
            self.assertEqual(calls, [("research", root)])

    def test_research_agent_uses_stable_json_checkpoint_when_supported(self):
        calls = []

        class Agent:
            def run_until_json_file(
                self,
                prompt,
                *,
                working_directory,
                required_path,
            ):
                calls.append((prompt, working_directory, required_path))
                required_path.write_text(
                    '{"schema_version":"1.0"}',
                    encoding="utf-8",
                )
                return "已到达文件提交点。"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = _run_research_agent(
                Agent(),
                "research",
                working_directory=root,
            )
            self.assertEqual(result, "已到达文件提交点。")
            self.assertEqual(
                calls,
                [("research", root, root / "task_research.json")],
            )

    def test_research_handoff_prefers_checkpoint_and_falls_back_to_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response_payload = {"schema_version": "response"}
            checkpoint_payload = {"schema_version": "checkpoint"}
            self.assertEqual(
                _research_payload_from_handoff(
                    json.dumps(response_payload),
                    working_directory=root,
                ),
                response_payload,
            )
            (root / "task_research.json").write_text(
                json.dumps(checkpoint_payload),
                encoding="utf-8",
            )
            self.assertEqual(
                _research_payload_from_handoff(
                    json.dumps(response_payload),
                    working_directory=root,
                ),
                checkpoint_payload,
            )

    def test_research_json_handoff_accepts_plain_and_fenced_json(self):
        expected = {"schema_version": "1.0", "task_archetypes": []}
        serialized = json.dumps(expected, ensure_ascii=False)

        self.assertEqual(_research_payload_from_response(serialized), expected)
        fence = chr(96) * 3
        self.assertEqual(
            _research_payload_from_response(
                f"已完成。\n{fence}json\n{serialized}\n{fence}"
            ),
            expected,
        )

    def test_research_json_handoff_rejects_missing_object(self):
        with self.assertRaisesRegex(ValueError, "没有完整 JSON 对象"):
            _research_payload_from_response("research complete")

    def test_jsonl_reader_uses_latest_checkpoint_for_an_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            write_jsonl(path, [{"value": 1}, {"value": 2}])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"__idx": 0, "item": {"value": 9}}) + "\n")
            self.assertEqual(read_records(path), [{"value": 9}, {"value": 2}])

    def test_current_program_package_has_only_three_step_entry_files(self):
        package = Path(__file__).resolve().parents[1] / "task_gen" / "program"
        self.assertFalse((package / "steps").exists())
        self.assertTrue((package / "step_0_environment_load.py").is_file())
        self.assertTrue((package / "step_1_task_research.py").is_file())
        self.assertTrue((package / "step_2_solution_generate.py").is_file())

    def test_step_contract_docs_are_explicitly_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_schema_docs(root, STEP1_SCHEMA_DOCS)
            self.assertEqual(
                {path.name for path in (root / "references").iterdir()},
                set(STEP1_SCHEMA_DOCS),
            )
            copy_schema_docs(root, STEP2_SCHEMA_DOCS)
            self.assertEqual(
                {path.name for path in (root / "references").iterdir()},
                set(STEP2_SCHEMA_DOCS),
            )


if __name__ == "__main__":
    unittest.main()
