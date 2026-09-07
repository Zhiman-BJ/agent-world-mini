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
    def _decision(
        source: str, weight: int = 0, reason: str = "no direct relationship"
    ) -> dict:
        return {
            "from_tool": source,
            "weight": weight,
            "reason": reason,
        }

    def test_prompt_defines_edges_by_downstream_chain_quality(self) -> None:
        prompt = graph_build.PROMPT_TEMPLATE

        for principle in (
            "你的输出将直接用于工具链采样",
            "把 B 直接放在 A 后面",
            "典型真实任务中会自然地连续执行并共同产生有效进展",
            "参数、对象或判断依据的承接只用于帮助判断",
            "自然连续",
            "权重只由连续调用在真实工作流中的结构作用决定",
            "仅能放进同一任务不足以形成边",
            "不能凭名称、领域常识或未声明的实体关系补全故事",
            "边有方向且不具传递性",
            "prerequisite 不表示 A -> B 是否是一条好边",
            "不能由自然任务输入提供",
        ):
            self.assertIn(principle, prompt)
        for duplicate in (
            "工具图连接的是两次实际执行",
            "任务文本或更早的调用可以提供 B 所需的信息",
            "承接会增强关系",
        ):
            self.assertNotIn(duplicate, prompt)
        for prescriptive_heading in (
            "标准一：依据真实",
            "标准二：直接且有价值",
            "标准三：按最强成立关系定级",
            "标准四：前置条件独立判断",
        ):
            self.assertNotIn(prescriptive_heading, prompt)
        self.assertLess(len(prompt), 2600)

    def test_prompt_defines_weights_by_chain_role(self) -> None:
        prompt = graph_build.PROMPT_TEMPLATE

        for role in (
            "同一条实际工作线的连续推进",
            "不同子任务之间的明确衔接",
            "任务层面的合理关联",
            "构成工具链的主要骨架",
            "用于探索和增加任务多样性",
        ):
            self.assertIn(role, prompt)
        self.assertNotIn("产生高质量工具链的稳定程度", prompt)

    def test_validates_edges_and_target_prerequisite_alternatives(self) -> None:
        raw = {
            "decisions": [
                self._decision("a", 3, "a directly prepares b"),
                self._decision("c"),
            ],
            "prerequisite_alternatives": [
                {"all_of": ["a"], "reason": "a supplies the required dynamic value"},
            ],
        }

        edges, prerequisites = graph_build._validate_decisions("b", raw, {"a", "b", "c"})

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

    def test_rejects_prerequisite_that_references_zero_weight_candidate(self) -> None:
        raw = {
            "decisions": [self._decision("a")],
            "prerequisite_alternatives": [{"all_of": ["a"], "reason": "invalid"}],
        }

        with self.assertRaisesRegex(ValueError, "正边"):
            graph_build._validate_decisions("b", raw, {"a", "b"})

    def test_rejects_incomplete_decisions(self) -> None:
        raw = {"decisions": [], "prerequisite_alternatives": []}
        with self.assertRaisesRegex(ValueError, "漏审"):
            graph_build._validate_decisions("b", raw, {"a", "b"})

    def test_requires_every_decision_reason_and_deduplicates_prerequisites(self) -> None:
        raw = {
            "decisions": [
                self._decision("a", 3, "a and c prepare b"),
                self._decision("c", 3, "a and c prepare b"),
            ],
            "prerequisite_alternatives": [
                {"all_of": ["a", "c"], "reason": "both are required"},
                {"all_of": ["c", "a"], "reason": "same alternative"},
            ],
        }
        _edges, prerequisites = graph_build._validate_decisions("b", raw, ["a", "b", "c"])
        self.assertEqual(len(prerequisites[0]["any_of"]), 1)

        raw["decisions"][1] = {
            "from_tool": "c", "weight": 0, "reason": "no direct relationship"
        }
        raw["prerequisite_alternatives"] = [{"all_of": ["a"], "reason": "a is required"}]
        edges, _prerequisites = graph_build._validate_decisions("b", raw, ["a", "b", "c"])
        self.assertEqual([edge["from_tool"] for edge in edges], ["a"])

        raw["decisions"][1]["reason"] = ""
        with self.assertRaisesRegex(ValueError, "reason"):
            graph_build._validate_decisions("b", raw, ["a", "b", "c"])

    def test_rejects_malformed_reason_before_deduplicating_prerequisites(self) -> None:
        raw = {
            "decisions": [self._decision("a", 3, "a prepares b")],
            "prerequisite_alternatives": [
                {"all_of": ["a"], "reason": "valid first copy"},
                {"all_of": ["a"], "reason": ""},
            ],
        }
        with self.assertRaisesRegex(ValueError, "reason"):
            graph_build._validate_decisions("b", raw, ["a", "b"])

    def test_builds_graph_with_one_call_per_target(self) -> None:
        names = ("a", "b", "c", "d")
        results = []
        for target in names:
            decisions = []
            for source in names:
                if source == target:
                    continue
                direct = source == "a" and target == "b"
                decisions.append(self._decision(
                    source, 3 if direct else 0,
                    "a supplies b" if direct else "no direct relationship",
                ))
            results.append(InferenceResult(json.dumps({
                "decisions": decisions,
                "prerequisite_alternatives": (
                    [{"all_of": ["a"], "reason": "a supplies b's dynamic value"}]
                    if target == "b" else []
                ),
            }), {}, "test"))

        with patch("task_gen.tool_graph.step_1_graph_build.infer", return_value=results) as mocked:
            output = build_graph({"config": Config(), "environment": graph_environment()})

        self.assertEqual(mocked.call_count, 1)
        prompts = mocked.call_args.args[0]
        self.assertEqual(len(prompts), len(names))
        self.assertNotIn("SECRET", json.dumps(prompts))
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

        def decisions_for(target: str) -> InferenceResult:
            return InferenceResult(json.dumps({
                "decisions": [self._decision(
                    source,
                    2 if source == "a" and target == "b" else 0,
                    "valid retry" if source == "a" and target == "b" else "no relationship",
                ) for source in names if source != target],
                "prerequisite_alternatives": [],
            }), {}, "test")

        first_batch = [decisions_for(target) for target in names]
        first_batch[1] = InferenceResult('{"decisions":[],"prerequisite_alternatives":[]}', {}, "test")
        retry = decisions_for("b")

        with patch(
            "task_gen.tool_graph.step_1_graph_build.infer",
            side_effect=[first_batch, [retry]],
        ) as mocked:
            output = build_graph({"config": Config(), "environment": graph_environment()})

        self.assertEqual(output["tool_graph"]["edges"][0]["reason"], "valid retry")
        self.assertEqual(mocked.call_count, 2)


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

    def test_review_prompt_judges_values_by_role_and_requires_real_progress(self) -> None:
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
        self.assertIn("参数的实际语义", prompt)
        self.assertIn("能够由任务自然规定", prompt)
        self.assertIn("不得把已有对象的内部标识当作新对象的标识复用", prompt)
        self.assertIn("每次调用必须处理新的对象、利用新的状态或产生新的进展", prompt)
        self.assertIn("目标已经完整实现后应当结束", prompt)
        self.assertNotIn("必填标识（如 *_id），只能依据前序", prompt)
        self.assertIn("2 到 2 个工具", prompt)

    def test_logic_score_prompt_rates_one_goal_progress_and_natural_ending(self) -> None:
        prompt = step_2_chain_sample._logic_score_prompt(
            graph_environment(),
            graph_environment()["tools"],
            {"edges": [], "prerequisites": []},
            {"chain": ["a", "b"], "llm_review": {"reason": "reviewed"}},
        )

        self.assertIn("所有调用共同服务于该目标", prompt)
        self.assertIn("每一步都利用已有信息或状态产生新的任务进展", prompt)
        self.assertIn("目标完成后仍继续操作", prompt)
        self.assertIn("5：目标清楚", prompt)

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
        self.assertTrue(any("评价下面的工具链" in prompt for prompt in calls[1]))
        self.assertEqual(output["tasks"][0]["logic_score"], 5)


if __name__ == "__main__":
    unittest.main()
