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
    ScenarioResearchError,
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
    sample_seed,
    scenario_payload,
    write_json,
)
from env_gen.data_gen.analysis.seed import canonical_json_sha256


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

    def test_step1_repair_prompt_only_adds_validation_context(self) -> None:
        prompt = _build_research_prompt(
            Path("/tmp/example-step1"),
            attempt=2,
            failure="$.entities 缺少必要字段",
        )
        self.assertIn("完整读取并执行 `.datagen/RESEARCH_GUIDE.md`", prompt)
        self.assertIn("scenario_research.invalid.json", prompt)
        self.assertIn("$.entities 缺少必要字段", prompt)

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
