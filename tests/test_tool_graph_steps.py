from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult
from task_gen.tool_graph.step_0_environment_load import load_environment
from task_gen.tool_graph.step_1_graph_build import build_graph
from task_gen.tool_graph import step_2_chain_sample
from task_gen.tool_graph.step_2_chain_sample import _select_diverse_chains, sample_chains


def write_environment(root: Path, *, resource_path: str = "data.json") -> Config:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "data.json").write_text("{}", encoding="utf-8")
    (root / "environment.json").write_text(json.dumps({
        "environment_id": "example",
        "resources": [{
            "resource_id": "data",
            "storage_type": "file",
            "path": resource_path,
            "writable": False,
        }],
        # Step 0 现在校验工具的四个公开字段、outputSchema 含 oneOf 与
        # internal.code 非空，夹具必须提供，否则清单检查会先失败。
        "tools": [{
            "name": "read_data",
            "description": "read the data file",
            "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "outputSchema": {"oneOf": [
                {"properties": {"success": {"const": True}, "data": {"type": "object"}}},
                {"properties": {"success": {"const": False}, "error": {"type": "object"}}},
            ]},
            "internal": {"code": "def run(arguments, context):\n    return {'success': True, 'data': {}}"},
        }],
    }), encoding="utf-8")
    (root / "validation.json").write_text('{"status":"passed"}', encoding="utf-8")
    return Config(environment_dir=root)


class EnvironmentLoadTest(unittest.TestCase):
    def test_loads_complete_environment_without_workspace_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = write_environment(Path(temporary))
            output = load_environment({"config": config})
            self.assertEqual(set(output), {"environment"})
            self.assertEqual(output["environment"]["environment_id"], "example")

    def test_rejects_path_escape_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = write_environment(root, resource_path="../outside.json")
            with self.assertRaisesRegex(ValueError, "data"):
                load_environment({"config": config})


def graph_environment() -> dict:
    return {
        "environment_id": "graph_example",
        "name": "Graph Example",
        "description": "test",
        "resources": [],
        "rules": [],
        "tools": [
            {
                "name": name,
                "description": f"{name} description",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "outputSchema": {"type": "object"},
                "internal": {"code": "SECRET"},
            }
            for name in ("a", "b", "c", "d")
        ],
    }


class GraphBuildTest(unittest.TestCase):
    def test_retries_one_bad_target_response_without_leaking_partial_edges(self) -> None:
        invalid_target = {"dependencies": [
            {
                "from_tool": "a", "weight": 2, "reason": "valid prefix",
                "parameter_evidence": [], "state_evidence": [],
            },
            {"from_tool": "missing"},
        ]}
        def all_zero(target: str) -> InferenceResult:
            return InferenceResult(json.dumps({"dependencies": [
                {"from_tool": name, "weight": 0}
                for name in ("a", "b", "c", "d") if name != target
            ]}), {}, "test")

        batch = [
            all_zero("a"),
            InferenceResult(json.dumps(invalid_target), {}, "test"),
            all_zero("c"),
            all_zero("d"),
        ]
        retry = InferenceResult(json.dumps({"dependencies": [
            {"from_tool": "a", "weight": 2, "reason": "valid retry"},
            {"from_tool": "c", "weight": 0},
            {"from_tool": "d", "weight": 0},
        ]}), {}, "test")

        with patch(
            "task_gen.tool_graph.step_1_graph_build.infer",
            side_effect=[batch, retry],
        ) as mocked:
            output = build_graph({"config": Config(), "environment": graph_environment()})

        self.assertEqual(len(output["tool_graph"]), 1)
        self.assertEqual(output["tool_graph"][0]["reason"], "valid retry")
        self.assertEqual(mocked.call_count, 2)

    def test_builds_validated_stable_edges_without_internal(self) -> None:
        # 审查完整性是硬门禁：每个目标必须对全部候选表态，无依赖的显式给 weight=0。
        def reviewed(target: str, *edges: dict) -> dict:
            named = {edge["from_tool"] for edge in edges}
            zeros = [
                {"from_tool": name, "weight": 0}
                for name in ("a", "b", "c", "d")
                if name != target and name not in named
            ]
            return {"dependencies": [*edges, *zeros]}

        responses = [
            reviewed("a"),
            reviewed("b", {"from_tool": "a", "weight": 2, "reason": "a directly prepares b"}),
            reviewed("c", {"from_tool": "b", "weight": 3, "reason": "b directly prepares c"}),
            reviewed("d", {"from_tool": "a", "weight": 1, "reason": "a helps d"}),
        ]
        captured: list[str] = []

        def fake_infer(prompts, **_kwargs):
            captured.extend(prompts)
            return [InferenceResult(json.dumps(item), {}, "test") for item in responses]

        with patch("task_gen.tool_graph.step_1_graph_build.infer", side_effect=fake_infer):
            output = build_graph({"config": Config(llm={}), "environment": graph_environment()})

        self.assertEqual(
            [(edge["from_tool"], edge["to_tool"], edge["weight"]) for edge in output["tool_graph"]],
            [("b", "c", 3), ("a", "b", 2), ("a", "d", 1)],
        )
        self.assertNotIn("SECRET", "".join(captured))
        self.assertNotIn('"internal"', "".join(captured))
        # evidence 字段已从契约中移除，prompt 不再要求"严格返回空数组"；
        # 改为断言 prompt 明确限定只返回三个字段。
        self.assertIn("只包含 from_tool、weight、reason", "".join(captured))
        self.assertNotIn('"tools":', "".join(captured))

    def test_rejects_invalid_dependency_instead_of_returning_partial_graph(self) -> None:
        bad = InferenceResult('{"dependencies":[{"from_tool":"missing"}]}', {}, "test")
        with patch("task_gen.tool_graph.step_1_graph_build.infer", return_value=[bad] * 4):
            with self.assertRaisesRegex(ValueError, "目标工具"):
                build_graph({"config": Config(), "environment": graph_environment()})


class ChainSampleTest(unittest.TestCase):
    def test_diversity_penalty_can_skip_high_score_chain_from_new_start(self) -> None:
        candidates = [
            (("a", "x", "y", "z"), 10),
            (("b", "x", "y", "z"), 9),
            (("a", "p", "q", "r"), 1),
        ]

        selected = _select_diverse_chains(candidates, count=2, diversity_lambda=10)

        self.assertEqual([item[0] for item in selected], [
            ("a", "x", "y", "z"),
            ("a", "p", "q", "r"),
        ])

    def test_post_review_selection_preserves_logic_score_then_diversifies(self) -> None:
        candidates = [
            {"chain": ["a", "x", "y", "z"], "score": 10, "logic_score": 5},
            {"chain": ["b", "x", "y", "z"], "score": 9, "logic_score": 5},
            {"chain": ["a", "p", "q", "r"], "score": 1, "logic_score": 5},
            {"chain": ["m", "n"], "score": 100, "logic_score": 4},
        ]

        selected = step_2_chain_sample._select_final_chains(
            candidates, count=2, diversity_lambda=10,
        )

        self.assertEqual([item["chain"] for item in selected], [
            ["a", "x", "y", "z"],
            ["a", "p", "q", "r"],
        ])

    def setUp(self):
        probe = patch.object(step_2_chain_sample, "explore_initial_state", return_value={
            "summary": "Initial evidence", "observations": [], "errors": [],
        })
        probe.start()
        self.addCleanup(probe.stop)
        review = patch.object(step_2_chain_sample, "review_with_initial_state", side_effect=
            lambda prompts, **kwargs: step_2_chain_sample.infer(prompts, llm_config=kwargs["llm_config"]))
        review.start()
        self.addCleanup(review.stop)

    def test_samples_deterministically_reviews_and_deduplicates(self):
        graph = [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "b", "to_tool": "c", "weight": 2},
            {"from_tool": "b", "to_tool": "d", "weight": 1},
        ]
        config = Config(planning={"sample_count": 100, "review_count": 2, "keep_top_count": 10,
                                  "min_chain_length": 3, "max_chain_length": 3})
        captured = []

        def fake_infer(prompts, **kwargs):
            captured.append(prompts)
            phase = (len(captured) - 1) % 2
            payload = (
                {"objective": "Inspect an existing record."} if phase == 0 else
                {"accepted": True, "chain": ["a", "b", "c"], "reason": "Local repair", "score": 5}
            )
            return [InferenceResult(json.dumps(payload), {}, "test") for _ in prompts]

        with patch.object(step_2_chain_sample, "infer", side_effect=fake_infer):
            first = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
            second = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
        self.assertEqual(first, second)
        self.assertEqual(len(first["tasks"]), 1)
        self.assertEqual(first["tasks"][0]["score"], 5)
        self.assertEqual(first["tasks"][0]["logic_score"], 5)
        self.assertEqual(len(captured), 4)
        self.assertEqual(first["sampling_report"]["objective_generated_count"], 2)
        self.assertIn("Initial evidence", captured[0][0])
        self.assertIn("Inspect an existing record.", captured[1][0])
        self.assertNotIn("SECRET", "".join(p for batch in captured for p in batch))

    def test_review_cannot_override_frozen_objective(self):
        item = {"chain": ["a", "b"], "score": 3, "objective": "Frozen"}
        for payload in (
            {"accepted": True, "chain": ["a", "b"], "objective": "Replacement", "reason": "Changed", "score": 4},
            {"accepted": True, "chain": ["a"], "reason": "Too short", "score": 4},
            {"accepted": True, "chain": ["a", "unknown"], "reason": "Unknown", "score": 4},
        ):
            with self.subTest(payload=payload), patch.object(step_2_chain_sample, "infer", return_value=[
                InferenceResult(json.dumps(payload), {}, "test")
            ]):
                reviewed, errors, changed, rejected = step_2_chain_sample._review_chains(
                    [item], graph_environment(), graph_environment()["tools"], [], set("abcd"), {}, 2, 2,
                    initial_workspace=Path("unused"),
                )
            self.assertEqual(reviewed, [])
            self.assertEqual(errors, 1)
            self.assertEqual(item["objective"], "Frozen")

    def test_review_can_complete_objective_beyond_sampling_length(self):
        objective = "Inspect both selected records and summarize their results."
        completed = ["a", "b", "b", "c"]
        replies = [
            [InferenceResult(json.dumps({"objective": objective}), {}, "test")],
            [InferenceResult(json.dumps({
                "accepted": True, "chain": completed,
                "reason": "Read the second record, then summarize both results.", "score": 5,
            }), {}, "test")],
        ]
        with patch.object(step_2_chain_sample, "infer", side_effect=replies):
            output = sample_chains({
                "config": Config(planning={"sample_count": 1, "review_count": 1,
                                           "min_chain_length": 2, "max_chain_length": 2,
                                           "random_seed": 1}),
                "environment": graph_environment(),
                "tool_graph": [{"from_tool": "a", "to_tool": "b", "weight": 3}],
            })
        self.assertEqual(output["sampling_report"]["review_error_count"], 0)
        self.assertEqual(output["tasks"][0]["chain"], completed)
        self.assertEqual(output["tasks"][0]["objective"], objective)
        self.assertEqual(output["tasks"][0]["llm_review"]["original_chain"], ["a", "b"])
        self.assertEqual(output["tasks"][0]["score"], 3)

    def test_short_chain_fallback_retains_only_longest_observed_candidates(self):
        replies = [
            [InferenceResult('{"objective":"Frozen"}', {}, "test")],
            [InferenceResult('{"accepted":true,"chain":["a","b","c"],"reason":"Add missing final query","score":4}', {}, "test")],
        ]
        with patch.object(step_2_chain_sample, "infer", side_effect=replies):
            output = sample_chains({
                "config": Config(planning={"sample_count": 1, "min_chain_length": 3, "max_chain_length": 3, "random_seed": 1}),
                "environment": graph_environment(),
                "tool_graph": [{"from_tool": "a", "to_tool": "b", "weight": 3}],
            })
        self.assertTrue(output["sampling_report"]["short_chain_fallback"])
        self.assertEqual(output["sampling_report"]["longest_observed_length"], 2)
        self.assertEqual(output["tasks"][0]["llm_review"]["original_chain"], ["a", "b"])

    def test_invalid_objective_outputs_are_generation_errors_not_chain_rejections(self):
        for payload in (
            {"accepted": False, "objective": None, "reason": "Incoherent main chain"},
            {"chain": ["a", "b"], "objective": "Changed chain"},
            {"objective": " "},
        ):
            with self.subTest(payload=payload), patch.object(step_2_chain_sample, "infer", return_value=[
                InferenceResult(json.dumps(payload), {}, "test")
            ]) as mocked:
                output = sample_chains({
                    "config": Config(planning={"sample_count": 1, "review_count": 1, "keep_top_count": 1,
                                               "min_chain_length": 2, "max_chain_length": 2}),
                    "environment": graph_environment(),
                    "tool_graph": [{"from_tool": "a", "to_tool": "b", "weight": 3}],
                })
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(output["tasks"], [])
            self.assertEqual(output["sampling_report"]["objective_error_count"], 1)
            self.assertNotIn("objective_rejected_count", output["sampling_report"])
            self.assertNotIn("accepted", output["sampling_report"]["objective_records"][0])
            self.assertEqual(len(output["sampling_report"]["objective_records"]), 1)

    def test_explicit_review_rejection_does_not_enter_selection(self):
        replies = [
            [InferenceResult(json.dumps({"objective": "Frozen"}), {}, "test")],
            [InferenceResult(json.dumps({"accepted": False, "chain": [], "reason": "Requires redesign", "score": 0}), {}, "test")],
        ]
        with patch.object(step_2_chain_sample, "infer", side_effect=replies) as mocked:
            output = sample_chains({
                "config": Config(planning={"sample_count": 1, "review_count": 1, "keep_top_count": 1,
                                           "min_chain_length": 2, "max_chain_length": 2}),
                "environment": graph_environment(),
                "tool_graph": [{"from_tool": "a", "to_tool": "b", "weight": 3}],
            })
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(output["tasks"], [])
        self.assertEqual(output["sampling_report"]["review_rejected_count"], 1)

    def test_review_score_is_validated_per_candidate(self):
        items = [{"chain": ["a", "b"], "score": 3, "objective": "Inspect a record"}] * 7
        replies = [InferenceResult(json.dumps({
            "accepted": True, "chain": ["a", "b"], "reason": "Read a then verify b", "score": score,
        }), {}, "test") for score in (True, -1, 6, 4.5, "4", 0, 5)]
        records = []
        with patch.object(step_2_chain_sample, "infer", return_value=replies):
            reviewed, errors, changed, rejected = step_2_chain_sample._review_chains(
                items, graph_environment(), graph_environment()["tools"], [], set("abcd"), {}, 2, 2,
                records=records, initial_workspace=Path("unused"),
            )
        self.assertEqual((errors, changed, rejected), (5, 0, 0))
        self.assertEqual([item["logic_score"] for item in reviewed], [0, 5])
        self.assertEqual([record["score"] for record in records[-2:]], [0, 5])
        self.assertEqual(reviewed[1]["logic_reason"], "Read a then verify b")

    def test_rejects_graph_without_eligible_root(self) -> None:
        graph = [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "b", "to_tool": "a", "weight": 3},
            {"from_tool": "c", "to_tool": "d", "weight": 3},
            {"from_tool": "d", "to_tool": "c", "weight": 3},
        ]
        with self.assertRaisesRegex(ValueError, "起点"):
            sample_chains({"config": Config(planning={
                "sample_count": 1, "keep_top_count": 1, "min_chain_length": 1,
                "max_chain_length": 2, "max_tool_visits": 1, "random_seed": 1,
            }), "environment": graph_environment(), "tool_graph": graph})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = write_environment(root)
            (root / "workspace" / "link").symlink_to(root / "workspace" / "data.json")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                load_environment({"config": config})

    def test_rejects_unpassed_environment_and_missing_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = write_environment(root)
            (root / "validation.json").write_text('{"status":"failed"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "passed"):
                load_environment({"config": config})

            (root / "validation.json").write_text('{"status":"passed"}', encoding="utf-8")
            (root / "workspace" / "data.json").unlink()
            with self.assertRaisesRegex(ValueError, "data"):
                load_environment({"config": config})



if __name__ == "__main__":
    unittest.main()
