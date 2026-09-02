from __future__ import annotations

import json
import random
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_gen.tool_graph import step_1_graph_build as graph_build
from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult
from task_gen.tool_graph.step_0_environment_load import load_environment
from task_gen.tool_graph.step_1_graph_build import build_graph
from task_gen.tool_graph import step_2_chain_sample
from task_gen.tool_graph.step_2_chain_sample import _graph, _select_diverse_chains, sample_chains


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
    @staticmethod
    def _assessment(
        source: str,
        *,
        immediate: bool = True,
        intermediate: bool = False,
        connection: str = "required_input",
    ) -> dict:
        return {
            "from_tool": source,
            "immediate_next": immediate,
            "intermediate_tool_required": intermediate,
            "connection": connection,
            "value_origin": "selected" if connection != "none" else "not_applicable",
            "evidence": f"evidence for {source}",
            "condition": None,
        }

    def test_validates_complete_first_round_assessments(self) -> None:
        raw = [
            {
                "from_tool": "a",
                "immediate_next": True,
                "intermediate_tool_required": False,
                "connection": "required_input",
                "value_origin": "selected",
                "evidence": "a returns the identifier required by b",
                "condition": None,
            },
            {
                "from_tool": "c",
                "immediate_next": False,
                "intermediate_tool_required": True,
                "connection": "none",
                "value_origin": "not_applicable",
                "evidence": "another tool must convert the result before b",
                "condition": None,
            },
        ]

        assessments = graph_build._validate_assessments("b", raw, {"a", "b", "c"})

        self.assertEqual(assessments, raw)

    def test_evidence_prompt_names_every_allowed_value_origin_literal(self) -> None:
        prompt = graph_build._build_evidence_prompt(
            {"name": "b"}, [{"name": "a"}], {"name": "environment"}
        )
        for value in graph_build.VALUE_ORIGINS:
            self.assertIn(f"`{value}`", prompt)

    def test_rejects_contradictory_first_round_assessment(self) -> None:
        raw = [{
            "from_tool": "a",
            "immediate_next": True,
            "intermediate_tool_required": True,
            "connection": "required_input",
            "value_origin": "selected",
            "evidence": "claims both direct and indirect",
            "condition": None,
        }]

        with self.assertRaisesRegex(ValueError, "immediate_next"):
            graph_build._validate_assessments("b", raw, {"a", "b"})

    def test_rejects_incomplete_first_round_assessments(self) -> None:
        with self.assertRaisesRegex(ValueError, "漏审"):
            graph_build._validate_assessments("b", [], {"a", "b"})

    def test_normalizes_first_round_assessments_to_candidate_order(self) -> None:
        assessments = graph_build._validate_assessments(
            "b", [self._assessment("c"), self._assessment("a")], ["a", "b", "c"]
        )
        self.assertEqual([item["from_tool"] for item in assessments], ["a", "c"])

    def test_validates_edges_and_target_prerequisite_alternatives(self) -> None:
        assessments = [
            self._assessment("a"),
            self._assessment("c", immediate=False, connection="none"),
        ]
        raw = {
            "decisions": [
                {"from_tool": "a", "weight": 3, "reason": "a directly prepares b"},
                {"from_tool": "c", "weight": 0, "reason": "c has no direct effect on b"},
            ],
            "prerequisite_alternatives": [
                {"all_of": ["a"], "reason": "a supplies the required dynamic value"},
            ],
        }

        edges, prerequisites = graph_build._validate_decisions(
            "b", raw, assessments, {"a", "b", "c"}
        )

        self.assertEqual(edges, [{
            "from_tool": "a",
            "to_tool": "b",
            "weight": 3,
            "reason": "a directly prepares b",
        }])
        self.assertEqual(prerequisites, [{
            "to_tool": "b",
            "any_of": [{
                "all_of": ["a"],
                "reason": "a supplies the required dynamic value",
            }],
        }])

    def test_rejects_positive_decision_for_non_direct_assessment(self) -> None:
        assessments = [self._assessment("a", intermediate=True, immediate=False)]
        raw = {
            "decisions": [{"from_tool": "a", "weight": 1, "reason": "invalid"}],
            "prerequisite_alternatives": [],
        }

        with self.assertRaisesRegex(ValueError, "第一轮"):
            graph_build._validate_decisions("b", raw, assessments, {"a", "b"})

    def test_rejects_prerequisite_that_references_zero_weight_candidate(self) -> None:
        assessments = [self._assessment("a", immediate=False, connection="none")]
        raw = {
            "decisions": [{"from_tool": "a", "weight": 0, "reason": "not direct"}],
            "prerequisite_alternatives": [{"all_of": ["a"], "reason": "invalid"}],
        }

        with self.assertRaisesRegex(ValueError, "正边"):
            graph_build._validate_decisions("b", raw, assessments, {"a", "b"})

    def test_accepts_empty_reason_for_zero_weight_and_deduplicates_prerequisites(self) -> None:
        assessments = [self._assessment("a"), self._assessment("c")]
        raw = {
            "decisions": [
                {"from_tool": "a", "weight": 3, "reason": "a and c prepare b"},
                {"from_tool": "c", "weight": 3, "reason": "a and c prepare b"},
            ],
            "prerequisite_alternatives": [
                {"all_of": ["a", "c"], "reason": "both are required"},
                {"all_of": ["c", "a"], "reason": "same alternative"},
            ],
        }
        _edges, prerequisites = graph_build._validate_decisions(
            "b", raw, assessments, ["a", "b", "c"]
        )
        self.assertEqual(len(prerequisites[0]["any_of"]), 1)

        raw["decisions"][1] = {"from_tool": "c", "weight": 0, "reason": ""}
        raw["prerequisite_alternatives"] = [{"all_of": ["a"], "reason": "a is required"}]
        edges, _prerequisites = graph_build._validate_decisions(
            "b", raw, assessments, ["a", "b", "c"]
        )
        self.assertEqual([edge["from_tool"] for edge in edges], ["a"])

    def test_rejects_strong_required_input_edge_from_echoed_value(self) -> None:
        assessments = [self._assessment("a") | {"value_origin": "echoed"}]
        raw = {
            "decisions": [{"from_tool": "a", "weight": 3, "reason": "echoed id fills b"}],
            "prerequisite_alternatives": [],
        }
        with self.assertRaisesRegex(ValueError, "echoed"):
            graph_build._validate_decisions("b", raw, assessments, ["a", "b"])

    def test_allows_strong_state_observation_even_when_identifier_is_echoed(self) -> None:
        assessments = [self._assessment("a", connection="state_observation") | {"value_origin": "echoed"}]
        raw = {
            "decisions": [{"from_tool": "a", "weight": 3, "reason": "b reads state changed by a"}],
            "prerequisite_alternatives": [],
        }
        edges, _prerequisites = graph_build._validate_decisions("b", raw, assessments, ["a", "b"])
        self.assertEqual(edges[0]["weight"], 3)

    def test_rejects_weight_above_connection_strength(self) -> None:
        for connection, weight in (("semantic_influence", 2), ("workflow_transition", 3), ("optional_input", 3)):
            assessments = [self._assessment("a", connection=connection)]
            raw = {
                "decisions": [{"from_tool": "a", "weight": weight, "reason": "too strong"}],
                "prerequisite_alternatives": [],
            }
            with self.subTest(connection=connection), self.assertRaisesRegex(ValueError, "上限"):
                graph_build._validate_decisions("b", raw, assessments, ["a", "b"])

    def test_prompts_distinguish_new_entity_ids_and_supported_text_influence(self) -> None:
        evidence = graph_build._build_evidence_prompt(
            {"name": "b"}, [{"name": "a"}], {"name": "environment"}
        )
        decision = graph_build._build_decision_prompt(
            {"name": "b"}, [{"name": "a"}], {"name": "environment"}, [self._assessment("a")]
        )
        self.assertIn("新实体", evidence)
        self.assertIn("明确用途", evidence)
        self.assertIn("回显", decision)
        self.assertIn("最多为 1", decision)

    def test_builds_graph_from_separate_evidence_and_decision_rounds(self) -> None:
        names = ("a", "b", "c", "d")
        first_round = []
        second_round = []
        for target in names:
            assessments = []
            decisions = []
            for source in names:
                if source == target:
                    continue
                direct = source == "a" and target == "b"
                assessments.append(self._assessment(
                    source,
                    immediate=direct,
                    connection="required_input" if direct else "none",
                ))
                decisions.append({
                    "from_tool": source,
                    "weight": 3 if direct else 0,
                    "reason": "a supplies b" if direct else "no direct relationship",
                })
            first_round.append(InferenceResult(
                json.dumps({"assessments": assessments}), {}, "test"
            ))
            second_round.append(InferenceResult(json.dumps({
                "decisions": decisions,
                "prerequisite_alternatives": (
                    [{"all_of": ["a"], "reason": "a supplies b's dynamic value"}]
                    if target == "b" else []
                ),
            }), {}, "test"))

        calls = []

        def fake_infer(prompts, **_kwargs):
            calls.append(prompts)
            return first_round if len(calls) == 1 else second_round

        with patch("task_gen.tool_graph.step_1_graph_build.infer", side_effect=fake_infer):
            output = build_graph({"config": Config(), "environment": graph_environment()})

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(isinstance(call, list) for call in calls))
        self.assertNotIn("SECRET", json.dumps(calls))
        self.assertEqual(output, {"tool_graph": {
            "edges": [{
                "from_tool": "a",
                "to_tool": "b",
                "weight": 3,
                "reason": "a supplies b",
            }],
            "prerequisites": [{
                "to_tool": "b",
                "any_of": [{
                    "all_of": ["a"],
                    "reason": "a supplies b's dynamic value",
                }],
            }],
        }})

    def test_retries_one_bad_target_response_without_leaking_partial_edges(self) -> None:
        names = ("a", "b", "c", "d")

        def assessments_for(target: str) -> InferenceResult:
            return InferenceResult(json.dumps({"assessments": [
                self._assessment(
                    source,
                    immediate=source == "a" and target == "b",
                    connection=(
                        "required_input" if source == "a" and target == "b" else "none"
                    ),
                )
                for source in names if source != target
            ]}), {}, "test")

        def decisions_for(target: str) -> InferenceResult:
            return InferenceResult(json.dumps({
                "decisions": [{
                    "from_tool": source,
                    "weight": 2 if source == "a" and target == "b" else 0,
                    "reason": "valid retry" if source == "a" and target == "b" else "none",
                } for source in names if source != target],
                "prerequisite_alternatives": [],
            }), {}, "test")

        first_batch = [assessments_for(target) for target in names]
        first_batch[1] = InferenceResult('{"assessments":[]}', {}, "test")
        retry = assessments_for("b")
        second_batch = [decisions_for(target) for target in names]

        with patch(
            "task_gen.tool_graph.step_1_graph_build.infer",
            side_effect=[first_batch, [retry], second_batch],
        ) as mocked:
            output = build_graph({"config": Config(), "environment": graph_environment()})

        self.assertEqual(output["tool_graph"]["edges"][0]["reason"], "valid retry")
        self.assertEqual(mocked.call_count, 3)


class ChainSampleTest(unittest.TestCase):
    def test_sampler_blocks_target_until_prerequisites_are_satisfied(self) -> None:
        adjacency = {"a": [("c", 3)], "b": [("c", 3)], "c": []}
        prerequisites = {"a": [], "b": [], "c": [frozenset({"a", "b"})]}
        chain = step_2_chain_sample._sample_one_chain(
            random.Random(1), ["a"], adjacency, prerequisites, {1: 1, 2: 1, 3: 1}, 3, 1
        )
        self.assertEqual(chain, ["a"])

        adjacency["a"] = [("b", 3)]
        chain = step_2_chain_sample._sample_one_chain(
            random.Random(1), ["a"], adjacency, prerequisites, {1: 1, 2: 1, 3: 1}, 3, 1
        )
        self.assertEqual(chain, ["a", "b", "c"])

    def test_sampler_accepts_any_prerequisite_alternative(self) -> None:
        adjacency = {"a": [("c", 3)], "b": [], "c": []}
        prerequisites = {"a": [], "b": [], "c": [frozenset({"a"}), frozenset({"b"})]}
        chain = step_2_chain_sample._sample_one_chain(
            random.Random(1), ["a"], adjacency, prerequisites, {1: 1, 2: 1, 3: 1}, 2, 1
        )
        self.assertEqual(chain, ["a", "c"])

    def test_level_three_without_prerequisites_remains_a_root(self) -> None:
        graph = {
            "edges": [{
                "from_tool": "a",
                "to_tool": "b",
                "weight": 3,
                "reason": "clear direct transition",
            }],
            "prerequisites": [],
        }

        _adjacency, prerequisites = _graph(graph, {"a", "b"})

        self.assertEqual(prerequisites, {"a": [], "b": []})

    def test_prerequisite_groups_are_independent_from_weight(self) -> None:
        graph = {
            "edges": [{
                "from_tool": "a",
                "to_tool": "b",
                "weight": 2,
                "reason": "a prepares required state",
            }],
            "prerequisites": [{
                "to_tool": "b",
                "any_of": [{"all_of": ["a"], "reason": "a prepares b"}],
            }],
        }

        _adjacency, prerequisites = _graph(graph, {"a", "b"})

        self.assertEqual(prerequisites, {"a": [], "b": [frozenset({"a"})]})

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

    def test_samples_deterministically_reviews_and_deduplicates(self) -> None:
        graph = {"edges": [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "b", "to_tool": "c", "weight": 2},
            {"from_tool": "b", "to_tool": "d", "weight": 1},
        ], "prerequisites": []}
        config = Config(planning={
            "sample_count": 100,
            "keep_top_count": 10,
            "min_chain_length": 8,
            "max_chain_length": 15,
            "max_tool_visits": 2,
            "random_seed": 42,
        })

        def fake_infer(prompts, **_kwargs):
            # Force every review to the same valid chain to verify post-review deduplication.
            return [InferenceResult(
                '{"chain":["a","b","c","a","b","d","b","c"],"reason":"valid"}',
                {},
                "test",
            ) for _ in prompts]

        with patch("task_gen.tool_graph.step_2_chain_sample.infer", side_effect=fake_infer):
            first = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
        with patch("task_gen.tool_graph.step_2_chain_sample.infer", side_effect=fake_infer):
            second = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})

        self.assertEqual(first, second)
        self.assertEqual(len(first["tasks"]), 1)
        self.assertEqual(first["tasks"][0]["chain"], ["a", "b", "c", "a", "b", "d", "b", "c"])
        self.assertTrue(first["sampling_report"]["short_chain_fallback"])
        self.assertEqual(first["sampling_report"]["attempt_count"], 100)

    def test_review_prompt_requires_required_identifiers_to_come_from_prior_results(self) -> None:
        graph = {"edges": [{"from_tool": "a", "to_tool": "b", "weight": 1}], "prerequisites": []}
        captured: list[str] = []

        def fake_infer(prompts, **_kwargs):
            captured.extend(prompts)
            return [InferenceResult('{"chain":["a","b"],"reason":"valid"}', {}, "test")]

        with patch("task_gen.tool_graph.step_2_chain_sample.infer", side_effect=fake_infer):
            sample_chains({
                "config": Config(planning={
                    "sample_count": 1, "keep_top_count": 1, "min_chain_length": 2,
                    "max_chain_length": 2, "max_tool_visits": 1, "random_seed": 1,
                }),
                "environment": graph_environment(), "tool_graph": graph,
            })
        prompt = "".join(captured)
        self.assertIn("必填标识", prompt)
        self.assertIn("插入能产生该标识的发现工具", prompt)
        self.assertIn("2 到 2 个工具", prompt)

    def test_bad_review_falls_back_to_original_chain(self) -> None:
        graph = {"edges": [{"from_tool": "a", "to_tool": "b", "weight": 3}], "prerequisites": [
            {"to_tool": "b", "any_of": [{"all_of": ["a"], "reason": "a"}]},
        ]}
        config = Config(planning={
            "sample_count": 1, "keep_top_count": 1, "min_chain_length": 2,
            "max_chain_length": 2, "max_tool_visits": 1, "random_seed": 1,
        })
        with patch(
            "task_gen.tool_graph.step_2_chain_sample.infer",
            return_value=[InferenceResult("not json", {}, "test")],
        ):
            output = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
        self.assertEqual(output["tasks"][0]["chain"], ["a", "b"])
        self.assertIsNotNone(output["tasks"][0]["llm_review"]["error"])

    def test_review_that_breaks_prerequisite_falls_back_to_original_chain(self) -> None:
        graph = {"edges": [{"from_tool": "a", "to_tool": "b", "weight": 3}], "prerequisites": [
            {"to_tool": "b", "any_of": [{"all_of": ["a"], "reason": "a prepares b"}]},
        ]}
        config = Config(planning={
            "sample_count": 1, "keep_top_count": 1, "min_chain_length": 2,
            "max_chain_length": 2, "max_tool_visits": 1, "random_seed": 1,
        })
        with patch(
            "task_gen.tool_graph.step_2_chain_sample.infer",
            side_effect=[
                [InferenceResult('{"chain":["b","a"],"reason":"reverse"}', {}, "test")],
                [InferenceResult('{"score":5,"reason":"valid"}', {}, "test")],
            ],
        ):
            output = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
        self.assertEqual(output["tasks"][0]["chain"], ["a", "b"])
        self.assertIn("prerequisite", output["tasks"][0]["llm_review"]["error"])

    def test_reviewed_chain_outside_length_limit_falls_back_to_original(self) -> None:
        graph = {"edges": [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "b", "to_tool": "c", "weight": 3},
        ], "prerequisites": [
            {"to_tool": "b", "any_of": [{"all_of": ["a"], "reason": "a"}]},
            {"to_tool": "c", "any_of": [{"all_of": ["b"], "reason": "b"}]},
        ]}
        config = Config(planning={
            "sample_count": 1, "keep_top_count": 1, "min_chain_length": 2,
            "max_chain_length": 2, "max_tool_visits": 1, "random_seed": 1,
        })
        with patch(
            "task_gen.tool_graph.step_2_chain_sample.infer",
            return_value=[InferenceResult('{"chain":["a","b","c"],"reason":"too long"}', {}, "test")],
        ):
            output = sample_chains({"config": config, "environment": graph_environment(), "tool_graph": graph})
        self.assertEqual(output["tasks"][0]["chain"], ["a", "b"])
        self.assertIn("长度", output["tasks"][0]["llm_review"]["error"])

    def test_rejects_graph_without_eligible_root(self) -> None:
        graph = {"edges": [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "b", "to_tool": "a", "weight": 3},
            {"from_tool": "c", "to_tool": "d", "weight": 3},
            {"from_tool": "d", "to_tool": "c", "weight": 3},
        ], "prerequisites": [
            {"to_tool": "a", "any_of": [{"all_of": ["b"], "reason": "b"}]},
            {"to_tool": "b", "any_of": [{"all_of": ["a"], "reason": "a"}]},
            {"to_tool": "c", "any_of": [{"all_of": ["d"], "reason": "d"}]},
            {"to_tool": "d", "any_of": [{"all_of": ["c"], "reason": "c"}]},
        ]}
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

    def test_runs_review_and_logic_scoring_rounds(self) -> None:
        graph = {"edges": [
            {"from_tool": "a", "to_tool": "b", "weight": 3},
            {"from_tool": "a", "to_tool": "c", "weight": 3},
            {"from_tool": "b", "to_tool": "d", "weight": 3},
            {"from_tool": "c", "to_tool": "d", "weight": 3},
        ], "prerequisites": []}
        config = Config(planning={
            "sample_count": 100,
            "review_count": 2,
            "keep_top_count": 1,
            "min_chain_length": 3,
            "max_chain_length": 3,
            "max_tool_visits": 1,
            "random_seed": 1,
        })
        calls: list[list[str]] = []

        def fake_infer(prompts, **_kwargs):
            calls.append(prompts)
            if len(calls) == 1:
                chains = [["a", "b", "d"], ["a", "c", "d"]]
                return [
                    InferenceResult(json.dumps({"chain": chains[index], "reason": "keep"}), {}, "test")
                    for index, _prompt in enumerate(prompts)
                ]
            return [
                InferenceResult(json.dumps({"score": 5 - index, "reason": "natural objective"}), {}, "test")
                for index, _prompt in enumerate(prompts)
            ]

        with patch("task_gen.tool_graph.step_2_chain_sample.infer", side_effect=fake_infer):
            output = sample_chains({
                "config": config,
                "environment": graph_environment(),
                "tool_graph": graph,
            })

        self.assertEqual(len(calls), 2)
        self.assertTrue(any("逻辑性评分" in prompt for prompt in calls[1]))
        self.assertEqual(output["tasks"][0]["logic_score"], 5)


if __name__ == "__main__":
    unittest.main()
