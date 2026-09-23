from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.scenario_research import validate_scenario_research_payload
from env_gen.data_gen.config import CollectionPolicy, DataGenConfig
from env_gen.data_gen.run_pipeline import _make_agent_runner, run_pipeline
from env_gen.data_gen.steps.step1_research_scenario import (
    RESEARCH_GUIDE,
    ScenarioResearchError,
    _build_research_guide,
    _build_research_prompt,
    run_scenario_research,
)
from env_gen.data_gen.steps.common.constants import (
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    SCENARIO_RESEARCH_PATH,
)
from env_gen.data_gen.steps.common.control_io import control_path
from tests.data_gen_test_helpers import (
    ROOT,
    prepare_step0,
    sample_python_package_seed,
    sample_seed,
    scenario_payload,
    write_json,
)
from env_gen.data_gen.analysis.seed import (
    canonical_json_sha256,
    load_selected_seed,
    reference_tool_labels,
)


class ScenarioResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seed = sample_seed()
        self.digest = canonical_json_sha256(self.seed)
        self.schema = json.loads(
            (ROOT / "env_gen/data_gen/analysis/checkpoint_schemas/scenario_research.schema.json").read_text()
        )

    def test_schema_is_valid(self) -> None:
        Draft202012Validator.check_schema(self.schema)

    def test_concise_research_payload_is_valid(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        self.assertEqual(
            validate_scenario_research_payload(
                payload,
                schema=self.schema,
                seed=self.seed,
                seed_sha256=self.digest,
            ),
            [],
        )
        self.assertEqual(set(payload), {
            "schema_version", "seed_global_id", "seed_sha256", "environment",
            "entities", "tools", "tasks", "research_notes",
        })

    def test_every_reference_tool_requires_an_independent_description(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        payload["tools"] = []
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=self.seed,
            seed_sha256=self.digest,
        )
        self.assertIn("missing_reference_tools", {item.code for item in issues})

    def test_python_package_seed_uses_real_apis_without_requiring_the_whole_index(self) -> None:
        seed = sample_python_package_seed()
        payload = scenario_payload(seed, canonical_json_sha256(seed))
        payload["tools"] = [{
            "name": "demo_package.alpha.RecordParser",
            "description": "Parses a real domain record in the package's usage environment.",
            "source_urls": ["https://example.test/items.json"],
        }]
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=seed,
            seed_sha256=canonical_json_sha256(seed),
        )
        self.assertEqual(issues, [])

        payload["tools"].append({
            "name": "invented.module.Tool",
            "description": "An API that is not present in this package Seed.",
            "source_urls": ["https://example.test/items.json"],
        })
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=seed,
            seed_sha256=canonical_json_sha256(seed),
        )
        self.assertIn("unknown_python_package_tools", {item.code for item in issues})

    def test_both_real_python_package_seeds_pass_seed_loading(self) -> None:
        cases = (
            ("atomate2_v0.1.5.json", "pypi_atomate2_2"),
            ("pymatgen-core_v2026.8.30.json", "pypi_pymatgen_core_1"),
        )
        for filename, global_id in cases:
            seed, _ = load_selected_seed(
                ROOT / "seed_gen/pypi_outputs" / filename,
                global_id,
                ROOT / "schemas/validation/env_seeds.schema.json",
            )
            self.assertEqual(seed["global_id"], global_id)

    def test_scenario_seed_uses_dedicated_contract_and_l3_identity(self) -> None:
        scenario_seed = {
            "global_id": "semiconductor_scenario_01_01_01",
            "schema_version": "scenario-1.1",
            "environment": {
                "basic_info": {
                    "source": "deep_research",
                    "url": [
                        "https://example.test/application",
                        "https://example.test/reference",
                        "https://example.test/workflow",
                    ],
                    "name": "晶体结构构建与转换",
                    "version": "2026-09-18",
                    "index": 1,
                },
                "description": "使用固定结构数据完成构建、转换和验证。",
                "domain": {
                    "level1": "01 材料与器件研发",
                    "level2": "01.01 材料结构与数据",
                    "level3": "01.01.01 晶体结构构建与转换",
                },
                "nums": {"class": 0, "function": 1, "class_func": 0, "all_func": 1},
            },
            "init_ref_tools": [{
                "name": "read_structure",
                "type": "function",
                "module": "demo.structure",
                "description": "",
                "input": {},
                "output": None,
            }],
            "init_ref_tasks": ["读取并验证一个结构文件。", "转换结构后重新读取并比较。"],
            "others": {
                "basic_info": [{"name": "demo-package"}],
                "pypi_package": ["demo-package"],
                "package_relation": "demo-package 提供结构读取和转换。",
                "package_metadata": [{"python_source_extraction": {}}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            write_json(path, [scenario_seed])
            loaded, _ = load_selected_seed(
                path,
                scenario_seed["global_id"],
                ROOT / "schemas/validation/env_seeds.schema.json",
            )
        self.assertEqual(loaded, scenario_seed)
        self.assertEqual(reference_tool_labels(loaded), {"demo.structure.read_structure"})

    def test_scenario_seed_collection_rejects_duplicate_indices(self) -> None:
        first = {
            "global_id": "semiconductor_scenario_01_01_01",
            "schema_version": "scenario-1.1",
            "environment": {
                "basic_info": {
                    "source": "deep_research",
                    "url": ["https://example.test/a", "https://example.test/b", "https://example.test/c"],
                    "name": "场景一",
                    "version": "2026-09-18",
                    "index": 1,
                },
                "description": "第一个测试场景。",
                "domain": {
                    "level1": "01 材料与器件研发",
                    "level2": "01.01 材料结构与数据",
                    "level3": "01.01.01 晶体结构构建与转换",
                },
                "nums": {"class": 0, "function": 1, "class_func": 0, "all_func": 1},
            },
            "init_ref_tools": [{
                "name": "run",
                "type": "function",
                "module": "demo",
                "description": "Run.",
                "input": {},
                "output": None,
            }],
            "init_ref_tasks": ["执行任务一。", "执行任务二。"],
            "others": {
                "basic_info": [{"name": "demo"}],
                "pypi_package": ["demo"],
                "package_relation": "demo 提供计算。",
                "package_metadata": [{"python_source_extraction": {}}],
            },
        }
        second = json.loads(json.dumps(first, ensure_ascii=False))
        second["global_id"] = "semiconductor_scenario_01_01_02"
        second["environment"]["domain"]["level3"] = "01.01.02 材料数据库检索"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            write_json(path, [first, second])
            with self.assertRaisesRegex(ValueError, "重复 index"):
                load_selected_seed(
                    path,
                    first["global_id"],
                    ROOT / "schemas/validation/env_seeds.schema.json",
                )

    def test_python_package_step1_gets_a_larger_research_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_step0(
                run_dir,
                selected_seed=sample_python_package_seed(),
            )
            observed: list[int] = []

            def runner(_prompt: str, seconds: int, _paths: tuple[Path, ...]) -> str:
                observed.append(seconds)
                payload = scenario_payload(seed, digest)
                payload["tools"] = [{
                    "name": "demo_package.alpha.RecordParser",
                    "description": "Parses records in the package usage environment.",
                    "source_urls": ["https://example.test/items.json"],
                }]
                write_json(run_dir / ".datagen/drafts/scenario_research.json", payload)
                return "complete"

            run_scenario_research(run_dir=run_dir, agent_runner=runner)
            self.assertEqual(observed, [900])

    def test_researched_items_must_reference_registered_sources(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        payload["tools"][0]["source_urls"] = ["https://other.example/tool"]
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=self.seed,
            seed_sha256=self.digest,
        )
        self.assertIn("unregistered_research_source", {item.code for item in issues})

    def test_entity_attributes_are_not_part_of_scenario_research(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        payload["entities"][0]["key_attributes"] = ["invented identifier"]
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=self.seed,
            seed_sha256=self.digest,
        )
        self.assertIn("scenario_research_schema", {item.code for item in issues})

    def test_concise_grounded_tool_description_is_valid(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        payload["tools"][0]["description"] = "Lists catalog items."
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=self.seed,
            seed_sha256=self.digest,
        )
        self.assertEqual(issues, [])

    def test_step1_prompt_uses_research_guide_as_single_task_definition(self) -> None:
        prompt = _build_research_prompt(Path("/tmp/example-step1"))
        self.assertIn("/tmp/example-step1", prompt)
        self.assertIn("完整读取并执行 `.datagen/RESEARCH_GUIDE.md`", prompt)
        self.assertNotIn("selected_seed.json", prompt)
        self.assertNotIn("停止条件", prompt)
        self.assertNotIn("scenario_research.json", prompt)
        self.assertNotIn("seed_research_inputs.json", prompt)
        self.assertNotIn("researchctl", prompt)
        self.assertNotIn("Python 工具包 Seed 的附加要求", RESEARCH_GUIDE)
        self.assertNotIn("代表性 API", RESEARCH_GUIDE)
        regular_guide = _build_research_guide(self.seed)
        self.assertEqual(regular_guide, RESEARCH_GUIDE)
        package_guide = _build_research_guide(sample_python_package_seed())
        self.assertEqual(package_guide, RESEARCH_GUIDE)
        self.assertNotIn("Python 工具包 Seed 的附加要求", package_guide)
        self.assertIn("先理解完整工作流", package_guide)
        self.assertIn("不必逐项阅读或把整个索引复刻进场景", package_guide)
        self.assertIn("不要等到全部调研结束才写结果", RESEARCH_GUIDE)
        self.assertIn("不能为了继续搜索而把首次交付推迟到会话末尾", RESEARCH_GUIDE)
        self.assertIn("Seed 自带 URL 是调研入口，不是来源边界", RESEARCH_GUIDE)
        self.assertIn("不必为了增加数量继续搜索", RESEARCH_GUIDE)
        self.assertIn("20-160 个字符", RESEARCH_GUIDE)
        self.assertIn("80-800 个字符", RESEARCH_GUIDE)

    def test_step1_repair_prompt_only_adds_validation_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            invalid = run_dir / ".datagen/drafts/scenario_research.invalid.json"
            invalid.parent.mkdir(parents=True)
            invalid.write_text("{}", encoding="utf-8")
            prompt = _build_research_prompt(
                run_dir,
                attempt=2,
                failure="$.entities 缺少必要字段",
            )
            self.assertIn("完整读取并执行 `.datagen/RESEARCH_GUIDE.md`", prompt)
            self.assertIn("scenario_research.invalid.json", prompt)
            self.assertIn("$.entities 缺少必要字段", prompt)

    def test_step1_missing_draft_repair_uses_previous_run_before_research(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt = _build_research_prompt(
                Path(directory),
                attempt=2,
                failure="缺少scenario_research 草稿",
            )
            self.assertIn("不要重新联网搜索", prompt)
            self.assertIn("上一轮的 `stderr.log`", prompt)
            self.assertIn("第一项实质操作必须是", prompt)
            self.assertNotIn("scenario_research.invalid.json`，未通过校验", prompt)

    def test_step0_prepares_shared_context_without_step_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_step0(run_dir)
            config = json.loads(
                control_path(run_dir, CONTROL_RUN_CONFIG).read_text(encoding="utf-8")
            )
            self.assertNotIn("scenario_research_submit_command", config)
            self.assertNotIn("step1_write_interface", config)
            self.assertNotIn("step2_write_interface", config)
            self.assertNotIn("protocols", config)
            self.assertNotIn("richness_policy", config)
            self.assertNotIn("quality_profile_schema_path", config)
            self.assertFalse((run_dir / ".datagen/seed_research_inputs.json").exists())
            self.assertFalse((run_dir / ".datagen/researchctl").exists())
            self.assertFalse((run_dir / ".datagen/RESEARCH_GUIDE.md").exists())
            self.assertFalse((run_dir / "workspace").exists())

    def test_duplicate_entity_names_are_rejected(self) -> None:
        payload = scenario_payload(self.seed, self.digest)
        payload["entities"].append(dict(payload["entities"][0]))
        issues = validate_scenario_research_payload(
            payload,
            schema=self.schema,
            seed=self.seed,
            seed_sha256=self.digest,
        )
        self.assertIn("duplicate_entities_name", {item.code for item in issues})

    def test_step1_runner_accepts_only_saved_research(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_step0(run_dir)

            def runner(_prompt: str, _seconds: int, paths: tuple[Path, ...]) -> str:
                self.assertEqual(paths, (run_dir / ".datagen/drafts/scenario_research.json",))
                write_json(
                    paths[0],
                    scenario_payload(self.seed, self.digest),
                )
                return "drafted"

            research, agent_calls = run_scenario_research(
                run_dir=run_dir,
                agent_runner=runner,
            )
            self.assertEqual(agent_calls, 1)
            self.assertIn(
                "public catalog environment",
                research["environment"]["summary"],
            )
            self.assertTrue((run_dir / SCENARIO_RESEARCH_PATH).is_file())

    def test_step1_rejects_agent_changes_to_complete_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_step0(run_dir)

            def runner(_prompt: str, _seconds: int, _paths: tuple[Path, ...]) -> str:
                control_path(run_dir, CONTROL_SELECTED_SEED).write_text(
                    "{}\n",
                    encoding="utf-8",
                )
                return "modified"

            with self.assertRaisesRegex(ScenarioResearchError, "修改了只读文件"):
                run_scenario_research(
                    run_dir=run_dir,
                    agent_runner=runner,
                )

    def test_step1_retries_once_from_existing_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_step0(run_dir)
            prompts: list[str] = []

            def runner(prompt: str, _seconds: int, _paths: tuple[Path, ...]) -> str:
                prompts.append(prompt)
                if len(prompts) == 1:
                    invalid = scenario_payload(self.seed, self.digest)
                    invalid["tools"] = []
                    write_json(
                        run_dir / ".datagen/drafts/scenario_research.json",
                        invalid,
                    )
                    return "invalid draft"
                write_json(
                    run_dir / ".datagen/drafts/scenario_research.json",
                    scenario_payload(self.seed, self.digest),
                )
                return "corrected draft"

            _, agent_calls = run_scenario_research(
                run_dir=run_dir,
                agent_runner=runner,
            )
            self.assertEqual(agent_calls, 2)
            self.assertIn("missing_reference_tools", prompts[1])
            self.assertIn("scenario_research.invalid.json", prompts[1])


class PipelineAgentRunnerTests(unittest.TestCase):
    def test_default_agent_loads_local_download_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = DataGenConfig(
                seed_path=root / "seeds.json",
                global_id="credential_loading_test",
                output_dir=root / "output",
            )
            with (
                patch("env_gen.data_gen.run_pipeline.load_local_environment") as load,
                patch(
                    "env_gen.data_gen.run_pipeline.CodexAgentClient",
                    side_effect=RuntimeError("stop after credential loading"),
                ),
                self.assertRaisesRegex(RuntimeError, "stop after credential loading"),
            ):
                run_pipeline(config)
            load.assert_called_once_with()

        example = (ROOT / "config/api_keys.env.example").read_text(encoding="utf-8")
        self.assertIn("GH_TOKEN=", example)
        self.assertIn("HF_TOKEN=", example)
        self.assertIn("KAGGLE_API_TOKEN=", example)

    def test_single_json_checkpoint_waits_for_stable_json(self) -> None:
        class Agent:
            timeout_seconds = 60

            def __init__(self) -> None:
                self.calls: list[str] = []

            def run_until_json_file(self, *_args: object, **_kwargs: object) -> str:
                self.calls.append("json")
                return "stable"

            def run_until_files(self, *_args: object, **_kwargs: object) -> str:
                self.calls.append("files")
                return "present"

        with tempfile.TemporaryDirectory() as directory:
            agent = Agent()
            run = _make_agent_runner(
                agent,
                staging=Path(directory),
                policy=CollectionPolicy(max_total_seconds=60),
                started=time.monotonic(),
            )
            result = run("write json", 30, (Path(directory) / "result.json",))

        self.assertEqual(result, "stable")
        self.assertEqual(agent.calls, ["json"])

if __name__ == "__main__":
    unittest.main()
