from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult
from task_gen.tool_graph import step_2_chain_sample as sampling
from task_gen.tool_graph.step_3_chain_execute import execute_chains
from task_gen.tool_graph.step_4_task_compose import _resource_constraints
from task_gen.tool_graph.step_5_task_validate import _basic_errors
from tests.test_tool_graph_execution import tool, task_candidate


def response(value):
    return InferenceResult(json.dumps(value), {}, "test")


class InitialStateTest(unittest.TestCase):
    def setUp(self):
        review = patch.object(sampling, "review_with_initial_state", side_effect=
            lambda prompts, **kwargs: sampling.infer(prompts, llm_config=kwargs["llm_config"]))
        self.review = review.start()
        self.addCleanup(review.stop)

    def test_probe_failure_is_explicit_and_summary_failure_keeps_observations(self):
        from task_gen.tool_graph.initial_state_probe import explore_initial_state

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            environment = {"resources": [], "tools": [
                tool("query", "def run(arguments, context):\n    return {'success': True, 'data': {'count': 7}}"),
            ]}
            config = Config(environment_dir=root)
            with patch("task_gen.tool_graph.initial_state_probe.infer", side_effect=RuntimeError("unavailable")):
                failed = explore_initial_state(config, environment)
            self.assertEqual(failed["observations"], [])
            self.assertIn("unavailable", failed["errors"][0])
            with patch("task_gen.tool_graph.initial_state_probe.infer", side_effect=[
                response({"calls": [{"tool": "query", "arguments": {}}]}),
                RuntimeError("summary unavailable"),
            ]):
                partial = explore_initial_state(config, environment)
            self.assertEqual(partial["observations"][0]["result"]["data"]["count"], 7)
            self.assertIn("已有可靠初态观察", partial["summary"])
            self.assertIn("summary unavailable", partial["errors"][0])

    def test_probe_discards_mutations_and_keeps_source_pristine(self):
        from task_gen.tool_graph.initial_state_probe import explore_initial_state

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            state = workspace / "state.json"
            state.write_text('{"existing": "record-7"}')
            environment = {"resources": [], "tools": [
                tool("query", "def run(arguments, context):\n    return {'success': True, 'data': json.loads((context.workspace_root / 'state.json').read_text())}"),
                tool("mutate", "def run(arguments, context):\n    (context.workspace_root / 'state.json').write_text('{}')\n    return {'success': True, 'data': {}}"),
            ]}
            replies = [
                response({"calls": [{"tool": "query", "arguments": {}}, {"tool": "mutate", "arguments": {}}, {"tool": "unknown", "arguments": {}}]}),
                response({"summary": "Existing record: record-7; other state not explored."}),
            ]
            with patch("task_gen.tool_graph.initial_state_probe.infer", side_effect=replies) as mocked:
                report = explore_initial_state(Config(environment_dir=root), environment)
            self.assertEqual([c["tool"] for c in report["observations"]], ["query"])
            self.assertEqual(len(report["errors"]), 2)
            self.assertEqual(state.read_text(), '{"existing": "record-7"}')
            self.assertNotIn("internal", mocked.call_args_list[0].args[0])
            self.assertIn("record-7", mocked.call_args_list[1].args[0])

    def test_objective_precedes_review_and_score_uses_final_chain(self):
        environment = {"resources": [], "tools": [tool(n, "unused") for n in "abc"]}
        graph = [{"from_tool": "a", "to_tool": "b", "weight": 3}, {"from_tool": "b", "to_tool": "c", "weight": 3}]
        config = Config(planning={"sample_count": 1, "review_count": 1, "keep_top_count": 1, "min_chain_length": 2, "max_chain_length": 3})
        report = {"summary": "Existing record-7", "observations": [], "errors": []}
        replies = [
            [response({"objective": "Inspect record-7"})],
            [response({"accepted": True, "chain": ["a", "c"], "reason": "One local repair"})],
            [response({"score": 4, "reason": "Supported objective"})],
        ]
        with patch.object(sampling, "explore_initial_state", return_value=report), patch.object(sampling, "infer", side_effect=replies) as mocked:
            output = sampling.sample_chains({"config": config, "environment": environment, "tool_graph": graph})
        candidate = output["tasks"][0]
        self.assertEqual(candidate["objective"], "Inspect record-7")
        self.assertEqual(candidate["score"], 0)
        self.assertEqual(candidate["llm_review"]["original_chain"], ["a", "b", "c"])
        self.assertIn("Inspect record-7", mocked.call_args_list[1].args[0][0])
        self.assertIn("Existing record-7", mocked.call_args_list[0].args[0][0])
        self.assertEqual(output["initial_state_report"], report)
        self.assertEqual(self.review.call_args.kwargs["initial_workspace"], config.environment_dir / "workspace")
        self.assertEqual(output["sampling_report"]["selected_unknown_edge_count"], 1)

    def test_multitask_objective_reaches_review_unchanged(self):
        environment = {"resources": [], "tools": [tool("security_report", "unused"), tool("quality_update", "unused")]}
        chain = ["security_report", "quality_update"]
        objective = "Summarize security findings; update one existing quality record."
        replies = [[response({"objective": objective})],
                   [response({"accepted": True, "chain": chain, "reason": "Both independent subtask results are supported."})],
                   [response({"score": 4, "reason": "Each subtask has a deliverable."})]]
        with patch.object(sampling, "explore_initial_state", return_value={"summary": "Observed", "observations": [], "errors": []}), patch.object(sampling, "infer", side_effect=replies) as mocked:
            output = sampling.sample_chains({"config": Config(planning={"sample_count": 1, "min_chain_length": 2, "max_chain_length": 2}),
                                             "environment": environment, "tool_graph": [{"from_tool": chain[0], "to_tool": chain[1], "weight": 1}]})
        self.assertEqual(output["tasks"][0]["objective"], objective)
        self.assertEqual(output["tasks"][0]["chain"], chain)
        self.assertEqual(output["sampling_report"]["review_candidate_count"], 1)
        self.assertIn(objective, mocked.call_args_list[1].args[0][0])
        self.assertNotIn('"accepted"', mocked.call_args_list[0].args[0][0])

    def test_execution_propagates_context_and_initial_report(self):
        marker = ContextVar("trace_check", default=None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            config = Config(environment_dir=root, execution={"retry_count": 0, "max_concurrency": 2})
            environment = {"resources": [], "tools": [tool("query", "def run(arguments, context):\n    return {'success': True, 'data': {}}") ]}
            report = {"summary": "Existing record-7", "observations": [], "errors": []}

            def infer(prompt, **kwargs):
                self.assertEqual(marker.get(), "captured")
                self.assertIn("Existing record-7", prompt)
                return response({"arguments": {}})

            token = marker.set("captured")
            try:
                with patch("task_gen.tool_graph.step_3_chain_execute.infer", side_effect=infer):
                    output = execute_chains({"config": config, "run_dir": root / "run", "environment": environment,
                                             "initial_state_report": report, "tasks": [task_candidate(n, ["query"]) for n in ("task1", "task2")]})
            finally:
                marker.reset(token)
            self.assertTrue(all(item["execution"]["success"] for item in output["tasks"]), output)

    def test_readonly_resources_are_explicit_and_failures_are_not_amplified(self):
        constraints = _resource_constraints({"resource_constraints": {"should_modify": ["editable"], "can_modify": [], "must_not_modify": []}, "error": None},
                                            ["readonly", "editable"], {"readonly": False, "editable": True})
        self.assertEqual(constraints["must_not_modify"], ["readonly"])
        self.assertEqual(_basic_errors({"execution": {"success": False, "error": "missing evidence"}}, {}), ["execution 未成功：missing evidence"])


if __name__ == "__main__":
    unittest.main()
