from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from task_gen.program import ProgramGenerationPolicy, validate_solution_code
from task_gen.program.step_1_task_research import (
    _run_research_agent,
    build_task_research_prompt,
)
from task_gen.program.step_2_solution_generate import (
    _argument_leaf_paths,
    _solution_complexity_errors,
    _solution_complexity_profile,
    _effective_tool_call_count,
    _is_infrastructure_execution_failure,
    _parameter_audit_errors,
    _replay_comparable_state,
    _run_agent_until_json,
    ProgramExecutionResult,
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
        self.assertIn('`path="/data"`', prompt)

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
        self.assertIn("你没有本项目此前的对话上下文", generation)
        self.assertIn("给未来执行者看的任务正文", generation)
        self.assertIn("`state/`", generation)
        self.assertIn("solution_code", generation)
        self.assertIn("task_resources", generation)
        self.assertIn("任务的深度优先来自结果之间的因果关系", generation)
        self.assertIn("你没有此前对话上下文", repair)
        self.assertIn("真实执行错误", repair)
        self.assertIn("把 `task_public` 当作用户发来的完整原始要求", review)
        self.assertIn("只根据下面这些真实证据判断", review)
        self.assertNotIn("task_review", review)
        self.assertNotIn("solution_review", review)
        self.assertIn("parameter_audit", review)

    def test_policy_rejects_invalid_generation_limits(self):
        policy = ProgramGenerationPolicy()
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
        self.assertTrue(any("参数叶子" in error for error in errors))

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

    def test_research_agent_stops_when_research_json_is_complete(self):
        calls = []

        class Agent:
            def run_until_json_file(self, prompt, *, working_directory, required_path):
                calls.append((prompt, working_directory, required_path))
                return "done"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = _run_research_agent(
                Agent(),
                "research",
                working_directory=root,
            )
            self.assertEqual(result, "done")
            self.assertEqual(
                calls,
                [("research", root, root / "task_research.json")],
            )

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
