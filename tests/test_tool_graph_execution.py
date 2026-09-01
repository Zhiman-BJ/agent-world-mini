from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult
from task_gen.tool_graph.step_3_chain_execute import _call_tool, execute_chains


def is_intent_prompt(prompt: str) -> bool:
    return "先根据完整调用链推测" in prompt


def inference(prompt: str, arguments: dict | None = None) -> InferenceResult:
    if is_intent_prompt(prompt):
        return InferenceResult(
            '{"task_intent":"沿整条工具链完成一致的测试任务"}', {}, "test",
        )
    return InferenceResult(json.dumps({"arguments": arguments or {}}), {}, "test")


def tool(name: str, body: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "const": True},
                "data": {"type": "object"},
            },
            "required": ["success", "data"],
            "additionalProperties": False,
        },
        "internal": {"code": body},
    }


WRITE_TOOL = """
from pathlib import Path
def run(arguments, context):
    path = Path(context.workspace_root) / 'state.json'
    state = json.loads(path.read_text())
    state['values'].append(arguments['value'])
    path.write_text(json.dumps(state))
    return {'success': True, 'data': {'value': arguments['value']}}
"""

FAIL_TOOL = """
def run(arguments, context):
    return {'success': False, 'error': {'message': 'no'}}
"""

SLEEP_TOOL = """
def run(arguments, context):
    import time
    time.sleep(10)
    return {'success': True, 'data': {}}
"""


class ExecuteChainsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment_dir = self.root / "environment"
        workspace = self.environment_dir / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "state.json").write_text('{"values":[]}', encoding="utf-8")
        self.run_dir = self.root / "run"
        (self.run_dir / "tasks").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, **execution) -> Config:
        return Config(
            environment_dir=self.environment_dir,
            execution={"max_concurrency": 2, "retry_count": 1, "tool_timeout_seconds": 2} | execution,
        )

    def test_executes_in_workspace_and_preserves_source_and_order(self) -> None:
        environment = {
            "environment_id": "example", "resources": [], "rules": [],
            "tools": [tool("write", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])],
        }
        tasks = [{"task_id": "task2", "chain": ["write"]}, {"task_id": "task1", "chain": ["write"]}]

        def fake_infer(prompt, **_kwargs):
            task_id = "task2" if '"task_id": "task2"' in prompt else "task1"
            return inference(prompt, {"value": task_id})

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            output = execute_chains({
                "config": self.config(), "run_dir": self.run_dir,
                "environment": environment, "tasks": tasks,
            })

        self.assertEqual([item["task_id"] for item in output["tasks"]], ["task2", "task1"])
        self.assertTrue(all(item["execution"]["success"] for item in output["tasks"]))
        self.assertEqual(json.loads((self.environment_dir / "workspace/state.json").read_text()), {"values": []})
        for candidate in output["tasks"]:
            execution = candidate["execution"]
            self.assertTrue((self.run_dir / execution["initial_state"]).is_dir())
            self.assertTrue((self.run_dir / execution["final_state"]).is_dir())
            self.assertEqual(json.loads((self.run_dir / execution["final_state"] / "state.json").read_text())["values"], [candidate["task_id"]])

    def test_builds_one_intent_then_uses_it_with_the_full_chain_for_arguments(self) -> None:
        environment = {
            "environment_id": "example",
            "resources": [{"path": "records/second.json"}],
            "rules": [],
            "tools": [
                tool("write", WRITE_TOOL, {"value": {"type": "string"}}, ["value"]),
                tool("finish", """
def run(arguments, context):
    return {'success': True, 'data': {}}
"""),
            ],
        }
        captured: list[str] = []

        def fake_infer(prompt, **_kwargs):
            captured.append(prompt)
            if is_intent_prompt(prompt):
                return InferenceResult(
                    '{"task_intent":"处理公开的 records/second.json，并使用 QA automation 作为署名"}',
                    {},
                    "test",
                )
            return inference(prompt, {"value": "records/second.json"} if '"name": "write"' in prompt else {})

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            candidate = execute_chains({
                "config": self.config(retry_count=0), "run_dir": self.run_dir,
                "environment": environment,
                "tasks": [{"task_id": "task1", "chain": ["write", "finish"]}],
            })["tasks"][0]

        self.assertTrue(candidate["execution"]["success"])
        self.assertEqual(sum(is_intent_prompt(prompt) for prompt in captured), 1)
        argument_prompt = next(
            prompt for prompt in captured
            if not is_intent_prompt(prompt) and '"name": "write"' in prompt
        )
        self.assertIn("records/second.json", argument_prompt)
        self.assertIn("QA automation", argument_prompt)
        self.assertIn('"finish"', argument_prompt)

    def test_argument_prompt_distinguishes_authored_values_from_existing_facts(self) -> None:
        environment = {
            "environment_id": "example", "resources": [], "rules": [],
            "tools": [tool("write", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])],
        }
        captured: list[str] = []

        def fake_infer(prompt, **_kwargs):
            captured.append(prompt)
            return inference(prompt, {"value": "ok"})

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            execute_chains({
                "config": self.config(retry_count=0), "run_dir": self.run_dir,
                "environment": environment, "tasks": [{"task_id": "task1", "chain": ["write"]}],
            })
        self.assertIn("任务创作值", "".join(captured))
        self.assertIn("新建交易的日期、金额和分录内容", "".join(captured))
        self.assertIn("动态 ID", "".join(captured))
        self.assertIn("公开环境", "".join(captured))
        self.assertIn("前序真实 result", "".join(captured))
        self.assertIn("不得使用 <id>", "".join(captured))
        self.assertIn("必填事实无法从公开环境或 completed_calls 获得", "".join(captured))

    def test_retries_current_argument_generation_without_replaying_prefix(self) -> None:
        first = tool("first", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])
        second = tool("second", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])
        calls: list[str] = []

        def fake_infer(prompt, **_kwargs):
            if is_intent_prompt(prompt):
                return inference(prompt)
            calls.append(prompt)
            if len(calls) == 2:
                return InferenceResult("not json", {}, "test")
            value = "first" if '"tool": "first"' in prompt else "second"
            return inference(prompt, {"value": value})

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            output = execute_chains({
                "config": self.config(retry_count=1), "run_dir": self.run_dir,
                "environment": {
                    "environment_id": "example", "resources": [], "rules": [],
                    "tools": [first, second],
                },
                "tasks": [{"task_id": "task1", "chain": ["first", "second"]}],
            })

        self.assertTrue(output["tasks"][0]["execution"]["success"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            json.loads((self.run_dir / "tasks/task1/initial/state.json").read_text())["values"],
            [],
        )

    def test_exhausted_argument_retries_do_not_restart_the_chain(self) -> None:
        environment = {
            "environment_id": "example", "resources": [], "rules": [],
            "tools": [tool("write", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])],
        }
        argument_calls = 0

        def fake_infer(prompt, **_kwargs):
            nonlocal argument_calls
            if is_intent_prompt(prompt):
                return inference(prompt)
            argument_calls += 1
            return InferenceResult("not json", {}, "test")

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            candidate = execute_chains({
                "config": self.config(retry_count=1), "run_dir": self.run_dir,
                "environment": environment,
                "tasks": [{"task_id": "task1", "chain": ["write"]}],
            })["tasks"][0]

        self.assertEqual(argument_calls, 2)
        self.assertEqual(len(candidate["execution"]["attempts"]), 1)
        self.assertEqual(candidate["execution"]["attempts"][0]["failure_kind"], "llm")

    def test_truncates_large_result_before_next_argument_prompt(self) -> None:
        producer = tool("producer", """
def run(arguments, context):
    return {"success": True, "data": {"payload": "x" * 100, "item_id": "item-123"}}
""")
        consumer = tool("consumer", """
def run(arguments, context):
    return {"success": True, "data": {}}
""")
        captured: list[str] = []

        def fake_infer(prompt, **_kwargs):
            if is_intent_prompt(prompt):
                return inference(prompt)
            captured.append(prompt)
            return inference(prompt)

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            execute_chains({
                "config": self.config(retry_count=0, tool_result_max_bytes=32),
                "run_dir": self.run_dir,
                "environment": {
                    "environment_id": "example", "resources": [], "rules": [],
                    "tools": [producer, consumer],
                },
                "tasks": [{"task_id": "task1", "chain": ["producer", "consumer"]}],
            })

        self.assertEqual(len(captured), 2)
        self.assertIn("已裁剪", captured[1])
        self.assertNotIn("x" * 100, captured[1])
        self.assertIn("item-123", captured[1])

    def test_retries_business_failure_from_clean_workspace_then_removes_task(self) -> None:
        environment = {"environment_id": "example", "resources": [], "rules": [], "tools": [tool("fail", FAIL_TOOL)]}
        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=lambda prompt, **_kwargs: inference(prompt)):
            candidate = execute_chains({
                "config": self.config(), "run_dir": self.run_dir,
                "environment": environment, "tasks": [{"task_id": "task1", "chain": ["fail"]}],
            })["tasks"][0]
        execution = candidate["execution"]
        self.assertFalse(execution["success"])
        self.assertEqual(len(execution["attempts"]), 2)
        self.assertTrue(all(item["failure_kind"] == "business" for item in execution["attempts"]))
        self.assertFalse((self.run_dir / "tasks/task1").exists())

    def test_full_retry_reuses_successful_prefix_arguments(self) -> None:
        first = tool("first", WRITE_TOOL, {"value": {"type": "string"}}, ["value"])
        second = tool("second", """
def run(arguments, context):
    if arguments["value"] == "bad":
        return {"success": False, "error": {"message": "retry"}}
    return {"success": True, "data": {}}
""", {"value": {"type": "string"}}, ["value"])
        counts = {"first": 0, "second": 0}

        def fake_infer(prompt, **_kwargs):
            if is_intent_prompt(prompt):
                return inference(prompt)
            name = "first" if '"name": "first"' in prompt else "second"
            counts[name] += 1
            value = name if name == "first" else ("bad" if counts[name] == 1 else "good")
            return inference(prompt, {"value": value})

        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=fake_infer):
            candidate = execute_chains({
                "config": self.config(retry_count=1), "run_dir": self.run_dir,
                "environment": {
                    "environment_id": "example", "resources": [], "rules": [],
                    "tools": [first, second],
                },
                "tasks": [{"task_id": "task1", "chain": ["first", "second"]}],
            })["tasks"][0]

        self.assertTrue(candidate["execution"]["success"])
        self.assertEqual(counts, {"first": 1, "second": 2})

    def test_timeout_does_not_repeat_the_same_resource_failure(self) -> None:
        environment = {
            "environment_id": "example", "resources": [], "rules": [],
            "tools": [tool("sleep", SLEEP_TOOL)],
        }
        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=lambda prompt, **_kwargs: inference(prompt)):
            candidate = execute_chains({
                "config": self.config(retry_count=3, tool_timeout_seconds=1),
                "run_dir": self.run_dir, "environment": environment,
                "tasks": [{"task_id": "task1", "chain": ["sleep"]}],
            })["tasks"][0]
        self.assertEqual(len(candidate["execution"]["attempts"]), 1)

    def test_preflight_rejects_duplicate_ids_before_creating_any_task(self) -> None:
        environment = {"tools": [tool("write", WRITE_TOOL)]}
        tasks = [{"task_id": "same", "chain": ["write"]}] * 2
        with self.assertRaisesRegex(ValueError, "task_id"):
            execute_chains({
                "config": self.config(), "run_dir": self.run_dir,
                "environment": environment, "tasks": tasks,
            })
        self.assertEqual(list((self.run_dir / "tasks").iterdir()), [])

    def test_times_out_tool_process(self) -> None:
        environment = {"environment_id": "example", "resources": [], "rules": [], "tools": [tool("sleep", SLEEP_TOOL)]}
        with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=lambda prompt, **_kwargs: inference(prompt)):
            candidate = execute_chains({
                "config": self.config(retry_count=0, tool_timeout_seconds=1),
                "run_dir": self.run_dir, "environment": environment,
                "tasks": [{"task_id": "task1", "chain": ["sleep"]}],
            })["tasks"][0]
        self.assertEqual(candidate["execution"]["attempts"][0]["failure_kind"], "timeout")

    def test_tool_cannot_write_outside_its_workspace(self) -> None:
        outside = self.root / "outside.txt"
        code = f"""
from pathlib import Path
def run(arguments, context):
    Path({str(outside)!r}).write_text('escaped')
    return {{'success': True, 'data': {{}}}}
"""

        outcome = _call_tool(
            code,
            {},
            self.environment_dir / "workspace",
            timeout=2,
            memory_limit=256 * 1024 * 1024,
            write_limit=1024 * 1024,
        )

        self.assertEqual(outcome["kind"], "exception")
        self.assertFalse(outside.exists())

    def test_tool_cannot_grow_workspace_beyond_total_write_limit(self) -> None:
        code = """
def run(arguments, context):
    (context.workspace_root / 'one.bin').write_bytes(b'x' * 800)
    (context.workspace_root / 'two.bin').write_bytes(b'x' * 800)
    return {'success': True, 'data': {}}
"""

        outcome = _call_tool(
            code,
            {},
            self.environment_dir / "workspace",
            timeout=2,
            memory_limit=256 * 1024 * 1024,
            write_limit=1024,
        )

        self.assertEqual(outcome["kind"], "exception")
        self.assertIn("总增长", outcome["error"])

    def test_tool_stdout_is_bounded_outside_parent_memory(self) -> None:
        code = """
def run(arguments, context):
    import os
    os.write(1, b'x' * (17 * 1024 * 1024))
    return {'success': True, 'data': {}}
"""

        outcome = _call_tool(
            code,
            {},
            self.environment_dir / "workspace",
            timeout=2,
            memory_limit=256 * 1024 * 1024,
            write_limit=32 * 1024 * 1024,
        )

        self.assertEqual(outcome["kind"], "exception")
        self.assertIn("输出超过", outcome["error"])


if __name__ == "__main__":
    unittest.main()
