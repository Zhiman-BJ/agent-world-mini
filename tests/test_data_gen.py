from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.composition_estimation import build_composition_estimate
from env_gen.data_gen.analysis.operation_candidates import build_operation_candidates
from env_gen.data_gen.analysis.quality import build_quality_profile
from env_gen.data_gen.analysis.scenario_research import validate_scenario_research_payload
from env_gen.data_gen.analysis.source_plan import build_next_actions
from env_gen.data_gen.steps.collection.commands.save_source_plan import (
    save_source_plan_payload,
)
from env_gen.data_gen.steps.collection.support.round_feedback import (
    collection_progress_snapshot,
    write_round_feedback,
)
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
    minimal_richness_policy,
    prepare_run,
    prepare_step0,
    sample_seed,
    scenario_payload,
    source_plan_payload,
    write_json,
)
from env_gen.data_gen.analysis.seed import canonical_json_sha256
from env_gen.data_gen.config import CollectionPolicy


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

    def test_step1_prompt_starts_from_seed_tools_tasks_and_web_research(self) -> None:
        prompt = _build_research_prompt(Path("/tmp/example-step1"))
        self.assertIn("selected_seed.json", prompt)
        self.assertIn("Seed 中的 URL", prompt)
        self.assertIn("同时完善环境、实体、工具和任务", prompt)
        self.assertNotIn("seed_research_inputs.json", prompt)
        self.assertNotIn("researchctl", prompt)

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

class SourcePlanAndQualityTests(unittest.TestCase):
    def test_source_plan_requires_all_scene_data_needs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_run(run_dir)
            payload = source_plan_payload(seed, digest)
            payload["data_need_coverage"] = []
            with self.assertRaisesRegex(RuntimeError, "缺少预调研需求覆盖"):
                save_source_plan_payload(run_dir, payload)

    def test_step2_can_record_new_deep_research_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_run(run_dir)
            payload = source_plan_payload(seed, digest)
            payload["research_refinements"][0] = {
                "refinement_id": "category_entity_found",
                "finding_type": "entity",
                "status": "new",
                "description": "Documentation exposes a separate category entity.",
                "evidence_source_ids": ["items"],
                "impact_on_collection": "Collect category IDs and labels.",
            }
            saved = save_source_plan_payload(run_dir, payload)
            self.assertEqual(saved["status"], "saved")

    def test_operation_and_composition_counts_are_diagnostic(self) -> None:
        entities = {
            "item": {
                "record_count": 2,
                "fields": {
                    "item_id": {"roles": ["identifier"], "non_null_count": 2, "distinct_count": 2},
                    "name": {"roles": ["text", "varied"], "non_null_count": 2, "distinct_count": 2},
                },
            }
        }
        candidates = build_operation_candidates(entities, [], [])
        estimate = build_composition_estimate(candidates)
        self.assertGreater(len(candidates), 0)
        self.assertIn("estimated_parameterized_cases", estimate)

    def test_quality_does_not_gate_on_operation_estimate(self) -> None:
        seed = sample_seed()
        digest = canonical_json_sha256(seed)
        scenario = scenario_payload(seed, digest)
        plan = source_plan_payload(
            seed,
            digest,
            status="complete",
            record_count=2,
            raw_files=["raw/items.json"],
        )
        data_profile = {
            "entities": {
                "item": {
                    "record_count": 2,
                    "field_count": 3,
                    "primary_key_candidates": ["item_id"],
                    "fields": {
                        "item_id": {"roles": ["identifier"], "non_null_count": 2, "distinct_count": 2},
                        "name": {"roles": ["text", "varied"], "non_null_count": 2, "distinct_count": 2},
                        "category": {"roles": ["category", "varied"], "non_null_count": 2, "distinct_count": 2},
                    },
                }
            },
            "files": [],
            "relation_candidates": [],
            "relation_gap_candidates": [],
        }
        quality = build_quality_profile(
            Path("."),
            seed=seed,
            seed_sha256=digest,
            checkpoint={},
            scenario_research=scenario,
            source_plan=plan,
            policy=minimal_richness_policy(),
            data_profile=data_profile,
        )
        self.assertEqual(quality["quality_tier"], "rich")
        self.assertTrue(quality["diagnostic_only"]["composition_estimate"])

    def test_next_actions_include_unfinished_sources(self) -> None:
        seed = sample_seed()
        digest = canonical_json_sha256(seed)
        actions = build_next_actions(source_plan_payload(seed, digest))
        self.assertEqual(actions[0]["code"], "collect_planned_source")


class RoundFeedbackTests(unittest.TestCase):
    def test_progress_ignores_feedback_timestamp_churn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            before = collection_progress_snapshot(run_dir)
            feedback = write_round_feedback(
                run_dir,
                round_index=1,
                max_rounds=2,
                assessment={
                    "decision": "continue",
                    "quality_tier": "not_rich",
                    "blocking_issues": [],
                    "next_actions": [],
                    "all_sources_resolved": False,
                    "all_data_needs_assessed": False,
                },
                before=before,
                after=collection_progress_snapshot(run_dir),
            )
            self.assertFalse(feedback["progress_changed"])

    def test_business_file_changes_progress_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            before = collection_progress_snapshot(run_dir)
            write_json(run_dir / "workspace/raw/new.json", {"items": []})
            after = collection_progress_snapshot(run_dir)
            self.assertNotEqual(before["fingerprint"], after["fingerprint"])


if __name__ == "__main__":
    unittest.main()
