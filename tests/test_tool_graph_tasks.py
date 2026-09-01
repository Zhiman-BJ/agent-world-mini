from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult
from task_gen.tool_graph.step_4_task_compose import compose_tasks
from task_gen.tool_graph.step_5_task_validate import validate_tasks


def public_tool() -> dict:
    return {
        "name": "write_data",
        "description": "write data",
        "inputSchema": {
            "type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"], "additionalProperties": False,
        },
        "outputSchema": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "const": True},
                        "data": {"type": "object"},
                    },
                    "required": ["success", "data"],
                },
                {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "const": False},
                        "error": {"type": "object"},
                    },
                    "required": ["success", "error"],
                },
            ],
        },
        "internal": {"code": "SECRET"},
    }


def environment() -> dict:
    return {
        "environment_id": "example_environment",
        "name": "Example", "description": "Example environment", "rules": [],
        "resources": [
            {"resource_id": "data", "storage_type": "file", "path": "data.txt", "writable": True},
            {"resource_id": "fixed", "storage_type": "file", "path": "fixed.txt", "writable": False},
        ],
        "tools": [public_tool()],
    }


def successful_candidate() -> dict:
    call = {
        "tool": "write_data", "arguments": {"value": "new"},
        "result": {"success": True, "data": {"value": "new"}},
    }
    return {
        "task_id": "task1", "chain": ["write_data"] * 6,
        "execution": {
            "success": True,
            "tool_calls": [dict(call) for _ in range(6)],
            "initial_state": "tasks/task1/initial",
            "final_state": "tasks/task1/final",
            "error": None, "attempts": [],
        },
    }


class ComposeTasksTest(unittest.TestCase):
    def test_composes_with_reflection_and_redacts_private_context(self) -> None:
        captured: list[str] = []
        replies = iter([
            {"task_text": "Update the data file.", "error": None},
            {
                "analyze": "The draft is already a natural result-oriented task with one clear deliverable.",
                "need_revision": False,
                "task_text": "This text must be ignored when revision is unnecessary.",
            },
            {"reference_answer": "The data file was updated.", "error": None},
            {
                "resource_constraints": {
                    "should_modify": ["data"],
                    "can_modify": [],
                    "must_not_modify": [],
                },
                "error": None,
            },
        ])

        def fake_infer(prompts, **_kwargs):
            captured.extend(prompts)
            return [InferenceResult(json.dumps(next(replies)), {}, "test") for _ in prompts]

        with patch("task_gen.tool_graph.step_4_task_compose.infer", side_effect=fake_infer):
            task = compose_tasks({
                "config": Config(), "environment": environment(),
                "tasks": [successful_candidate()],
            })["tasks"][0]
        self.assertEqual(task["task_text"], "Update the data file.")
        self.assertEqual(task["reference_answer"], "The data file was updated.")
        self.assertEqual(task["resource_constraints"]["must_not_modify"], [])
        self.assertIsNone(task["compose_error"])
        self.assertEqual(len(captured), 4)
        self.assertNotIn("SECRET", "".join(captured))
        self.assertNotIn("initial_state", "".join(captured))
        self.assertNotIn("final_state", "".join(captured))
        self.assertNotIn("Update the data file.", captured[0])
        self.assertIn("发生在写入之前", captured[0])
        self.assertIn("用户可表达的业务要求", captured[0])
        self.assertIn("执行实现产生的参数不得写入任务", captured[0])
        self.assertIn("以最终业务结果为中心", captured[0])
        self.assertIn("Update the data file.", captured[1])
        self.assertIn("自然、结果导向", captured[1])
        self.assertNotIn("This text must be ignored", captured[2])
        self.assertIn("Update the data file.", captured[2])
        self.assertIn("The data file was updated.", captured[3])

    def test_reflection_revision_is_used_before_following_rounds(self) -> None:
        captured: list[str] = []
        replies = iter([
            [{"task_text": "Draft task.", "error": None}],
            [{
                "analyze": "The draft lists lookup and confirmation steps instead of expressing the final business result.",
                "need_revision": True,
                "task_text": "Complete the requested business result.",
            }],
            [{"reference_answer": "The requested business result was completed.", "error": None}],
            [{
                "resource_constraints": {"should_modify": ["data"], "can_modify": [], "must_not_modify": []},
                "error": None,
            }],
        ])

        def fake_infer(prompts, **_kwargs):
            captured.extend(prompts)
            return [InferenceResult(json.dumps(next(replies)[0]), {}, "test") for _ in prompts]

        with patch("task_gen.tool_graph.step_4_task_compose.infer", side_effect=fake_infer):
            task = compose_tasks({
                "config": Config(), "environment": environment(),
                "tasks": [successful_candidate()],
            })["tasks"][0]

        self.assertEqual(task["task_text"], "Complete the requested business result.")
        self.assertIn("Draft task.", captured[1])
        self.assertIn("Complete the requested business result.", captured[2])

    def test_invalid_reflection_stops_following_rounds(self) -> None:
        responses = iter([
            InferenceResult(json.dumps({"task_text": "Draft task.", "error": None}), {}, "test"),
            InferenceResult(json.dumps({"analyze": "missing decision", "need_revision": "no", "task_text": ""}), {}, "test"),
        ])
        with patch("task_gen.tool_graph.step_4_task_compose.infer", side_effect=lambda prompts, **_k: [next(responses) for _ in prompts]) as mocked:
            task = compose_tasks({
                "config": Config(), "environment": environment(),
                "tasks": [successful_candidate()],
            })["tasks"][0]

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(task["task_text"], "Draft task.")
        self.assertIsNone(task["reference_answer"])
        self.assertIn("任务文本反思", task["compose_error"])

    def test_keeps_task_text_when_reference_answer_generation_fails(self) -> None:
        responses = iter([
            InferenceResult(json.dumps({
                "task_text": "Update the data file.", "error": None,
            }), {}, "test"),
            InferenceResult(json.dumps({
                "analyze": "The draft is natural and result-oriented.",
                "need_revision": False,
                "task_text": "",
            }), {}, "test"),
            InferenceResult(json.dumps({
                "reference_answer": None, "error": "No grounded answer",
            }), {}, "test"),
        ])
        with patch("task_gen.tool_graph.step_4_task_compose.infer", side_effect=lambda prompts, **_k: [next(responses) for _ in prompts]) as mocked:
            task = compose_tasks({
                "config": Config(), "environment": environment(),
                "tasks": [successful_candidate()],
            })["tasks"][0]

        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(task["task_text"], "Update the data file.")
        self.assertIsNone(task["reference_answer"])
        self.assertIsNone(task["resource_constraints"])
        self.assertIn("参考答案", task["compose_error"])
        self.assertIn("No grounded answer", task["compose_error"])

    def test_skips_failed_execution(self) -> None:
        failed = successful_candidate()
        failed["execution"]["success"] = False
        with patch("task_gen.tool_graph.step_4_task_compose.infer") as mocked:
            output = compose_tasks({"config": Config(), "environment": environment(), "tasks": [failed]})
        mocked.assert_not_called()
        self.assertIsNone(output["tasks"][0]["task_text"])
        self.assertIsNotNone(output["tasks"][0]["compose_error"])


class ValidateTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment_dir = self.root / "environment"
        source = self.environment_dir / "workspace"
        source.mkdir(parents=True)
        (source / "data.txt").write_text("old", encoding="utf-8")
        (source / "fixed.txt").write_text("fixed", encoding="utf-8")
        self.run_dir = self.root / "run"
        initial = self.run_dir / "tasks/task1/initial"
        final = self.run_dir / "tasks/task1/final"
        shutil.copytree(source, initial)
        shutil.copytree(source, final)
        (final / "data.txt").write_text("new", encoding="utf-8")
        self.config = Config(
            environment_dir=self.environment_dir,
            schema_dir=Path(__file__).parents[1] / "schemas",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate(self) -> dict:
        value = successful_candidate()
        value.update({
            "task_text": "Update the data file.",
            "reference_answer": "The data file was updated.",
            "resource_constraints": {
                "should_modify": ["data"], "can_modify": [], "must_not_modify": ["fixed"],
            },
            "compose_error": None,
        })
        return value

    def test_assembles_and_semantically_validates_task(self) -> None:
        captured: list[str] = []

        def fake_infer(prompts, **_kwargs):
            captured.extend(prompts)
            return [InferenceResult(json.dumps({
                "chain_matches_task": True,
                "task_has_required_information": True,
                "errors": [],
            }), {}, "test") for _ in prompts]

        with patch("task_gen.tool_graph.step_5_task_validate.infer", side_effect=fake_infer):
            candidate = validate_tasks({
                "config": self.config, "run_dir": self.run_dir,
                "environment": environment(), "tasks": [self.candidate()],
            })["tasks"][0]
        self.assertTrue(candidate["validation"]["passed"], candidate["validation"]["errors"])
        self.assertTrue(candidate["validation"]["chain_matches_task"])
        self.assertTrue(candidate["validation"]["task_has_required_information"])
        self.assertNotIn("internal", candidate["task"]["available_tools"][0])
        self.assertEqual(candidate["task"]["reference"]["tool_calls"], [
            {"tool": "write_data", "arguments": {"value": "new"}},
        ] * 6)
        self.assertEqual(candidate["task"]["resource_constraints"], {
            "should_modify": ["data"], "can_modify": [], "must_not_modify": ["fixed"],
        })
        self.assertNotIn("SECRET", captured[0])
        self.assertIn('"result"', captured[0])
        self.assertNotIn("The data file was updated.", captured[0])

    def test_rejects_semantic_mismatch_without_weakening_review(self) -> None:
        response = InferenceResult(json.dumps({
            "chain_matches_task": False,
            "task_has_required_information": True,
            "errors": ["The task asks for an email, but the trace only updates a file."],
        }), {}, "test")
        with patch("task_gen.tool_graph.step_5_task_validate.infer", return_value=[response]):
            result = validate_tasks({
                "config": self.config, "run_dir": self.run_dir,
                "environment": environment(), "tasks": [self.candidate()],
            })["tasks"][0]
        self.assertFalse(result["validation"]["passed"])
        self.assertFalse(result["validation"]["chain_matches_task"])
        self.assertTrue(result["validation"]["task_has_required_information"])
        self.assertIn("only updates a file", result["validation"]["errors"][0])

    def test_missing_step_four_field_skips_llm_review(self) -> None:
        candidate = self.candidate()
        del candidate["compose_error"]
        with patch("task_gen.tool_graph.step_5_task_validate.infer") as mocked:
            result = validate_tasks({
                "config": self.config, "run_dir": self.run_dir,
                "environment": environment(), "tasks": [candidate],
            })["tasks"][0]
        mocked.assert_not_called()
        self.assertFalse(result["validation"]["passed"])
        self.assertIn("缺少 Step 4 中间字段", result["validation"]["errors"])

    def test_rejects_invalid_llm_review_shape(self) -> None:
        response = InferenceResult(json.dumps({
            "chain_matches_task": "yes",
            "task_has_required_information": True,
            "errors": [],
        }), {}, "test")
        with patch("task_gen.tool_graph.step_5_task_validate.infer", return_value=[response]):
            result = validate_tasks({
                "config": self.config, "run_dir": self.run_dir,
                "environment": environment(), "tasks": [self.candidate()],
            })["tasks"][0]
        self.assertFalse(result["validation"]["passed"])
        self.assertIn("chain_matches_task", "\n".join(result["validation"]["errors"]))

    def test_rejects_reviewed_chain_shorter_than_six_calls(self) -> None:
        candidate = self.candidate()
        candidate["chain"] = ["write_data"]
        candidate["execution"]["tool_calls"] = [{
            "tool": "write_data", "arguments": {"value": "new"},
            "result": {"success": True, "data": {"value": "new"}},
        }]
        with patch("task_gen.tool_graph.step_5_task_validate.infer") as mocked:
            result = validate_tasks({
                "config": self.config, "run_dir": self.run_dir,
                "environment": environment(), "tasks": [candidate],
            })["tasks"][0]
        mocked.assert_not_called()
        self.assertFalse(result["validation"]["passed"])
        self.assertIn("至少 6 次工具调用", "\n".join(result["validation"]["errors"]))


if __name__ == "__main__":
    unittest.main()
