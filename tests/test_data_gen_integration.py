from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Import the executable stage modules so syntax/import failures cannot hide
# behind unit tests that exercise only their lower-level helpers.
from env_gen.data_gen.steps.exploration import explorectl as _explorectl
from env_gen.data_gen.steps.exploration import workflow as _exploration_workflow
from env_gen.data_gen.steps.integration import integratectl as _integratectl
from env_gen.data_gen.steps.integration import workflow as _integration_workflow
from env_gen.data_gen.steps import step2_explore_sources as _step2_explore_sources

from env_gen.data_gen.analysis.integration_materialization import (
    materialize_record_set,
    materialize_scope,
)
from env_gen.data_gen.analysis.integration_plan import validate_integration_plan
from env_gen.data_gen.analysis.integration_profiling import build_integration_profile
from env_gen.data_gen.analysis.field_review import (
    build_field_review_payload,
    field_review_issues,
)
from env_gen.data_gen.analysis.environment_quality import build_environment_quality_profile
from env_gen.data_gen.analysis.filesystem_scopes import (
    permitted_invalid_files,
    validate_scope_tree,
)
from env_gen.data_gen.analysis.source_inventory import (
    _repository_url_stability,
    build_source_inventory,
    validate_source_inventory,
)
from env_gen.data_gen.analysis.source_plan import SourcePlanValidator
from env_gen.data_gen.config import CollectionPolicy

from tests.data_gen_test_helpers import ROOT, prepare_run, sample_seed, scenario_payload, source_plan_payload, write_json
from env_gen.data_gen.analysis.seed import canonical_json_sha256
from env_gen.data_gen.analysis.environment_quality import EnvironmentQualityPolicy
from env_gen.data_gen.steps.common.workspace_files import file_sha256
from env_gen.data_gen.steps.common.round_budget import is_last_available_round
from env_gen.data_gen.steps.collection.commands.download_raw import download_raw_file
from env_gen.data_gen.steps.collection.commands.save_source_plan import save_source_plan_payload
from env_gen.data_gen.steps.integration.commands import (
    assess_integration,
    build_record_set,
    finalize_integration,
    save_integration_plan,
)
from env_gen.data_gen.steps.integration.commands import _terminal_coverage
from env_gen.data_gen.steps.integration.workflow import prepare_integration
from env_gen.data_gen.steps.integration.transformation_runner import run_record_transformation
from env_gen.data_gen.steps.step4_freeze_environment import freeze_environment
from env_gen.data_gen.steps.step5_validate_and_publish import validate_and_publish
from tests.test_data_gen_step1_downloads import server


SCHEMA_ROOT = ROOT / "env_gen/data_gen/analysis/checkpoint_schemas"


class PhaseRoundBudgetTests(unittest.TestCase):
    def test_remaining_budget_marks_the_second_exploration_call_as_final(self) -> None:
        policy = CollectionPolicy(
            exploration_seconds=10,
            exploration_total_seconds=15,
            max_exploration_rounds=3,
        )
        prompts: list[str] = []

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir, policy=policy)

            def agent_runner(
                prompt: str,
                _timeout_seconds: int,
                _required_paths: tuple[Path, ...],
            ) -> str:
                prompts.append(prompt)
                raise TimeoutError("test timeout")

            with mock.patch.object(
                _step2_explore_sources.time,
                "monotonic",
                side_effect=[0, 0, 6, 15],
            ):
                with self.assertRaises(_step2_explore_sources.SourceExplorationError):
                    _step2_explore_sources.run_source_exploration(
                        run_dir=run_dir,
                        collection_policy=policy,
                        agent_runner=agent_runner,
                    )

        self.assertEqual(len(prompts), 2)
        self.assertIn("可用总预算内的最后一轮", prompts[1])
        self.assertIn("不要发现新来源", prompts[1])

    def test_round_limit_and_time_limit_both_define_the_last_round(self) -> None:
        self.assertFalse(is_last_available_round(
            round_index=1, max_rounds=3,
            remaining_seconds=11, per_round_seconds=10,
        ))
        self.assertTrue(is_last_available_round(
            round_index=2, max_rounds=3,
            remaining_seconds=10, per_round_seconds=10,
        ))
        self.assertTrue(is_last_available_round(
            round_index=3, max_rounds=3,
            remaining_seconds=100, per_round_seconds=10,
        ))


def field(field_type: str, description: str, *, nullable: bool = False, **extra: object) -> dict[str, object]:
    return {"type": field_type, "description": description, "nullable": nullable, **extra}


def record_set(
    record_set_id: str,
    *,
    source_id: str,
    source_path: str,
    fields: dict[str, object],
    key_fields: list[str],
    standalone_reason: str | None = None,
) -> dict[str, object]:
    return {
        "record_set_id": record_set_id,
        "name": record_set_id,
        "description": f"一条记录表示一个 {record_set_id}。",
        "access": "read_only",
        "key_fields": key_fields,
        "fields": fields,
        "importance": "core",
        "source_ids": [source_id],
        "source_paths": [source_path],
        "merge_strategy": "single_source",
        "identity_strategy": {
            "kind": "source_key",
            "fields": key_fields,
            "description": "使用来源稳定键。",
        },
        "conflict_policy": "not_applicable",
        "transformation": "从来源记录确定性选择并改名字段。",
        "transformation_id": f"build_{record_set_id}",
        "standalone_reason": standalone_reason,
    }


class SourceInventoryTests(unittest.TestCase):
    def test_repository_url_stability_distinguishes_commits_from_branches(self) -> None:
        sha = "a" * 40
        self.assertEqual(
            _repository_url_stability(
                "https://raw.githubusercontent.com/org/repo/main/data.json"
            ),
            "mutable_repository",
        )
        self.assertEqual(
            _repository_url_stability(
                f"https://raw.githubusercontent.com/org/repo/{sha}/data.json"
            ),
            "immutable_repository",
        )
        self.assertEqual(
            _repository_url_stability(
                "https://github.com/org/repo/archive/refs/heads/main.zip"
            ),
            "mutable_repository",
        )
        self.assertIsNone(_repository_url_stability("https://api.example.test/items"))

    def test_profiles_real_json_without_deciding_final_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            raw = run_dir / "workspace/raw/items.json"
            write_json(raw, {"items": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
            seed = sample_seed()
            digest = canonical_json_sha256(seed)
            plan = source_plan_payload(
                seed, digest, status="complete", record_count=2,
                raw_files=["raw/items.json"],
            )
            inventory = build_source_inventory(
                run_dir,
                seed_global_id=seed["global_id"],
                seed_sha256=digest,
                source_plan=plan,
            )
            self.assertEqual(inventory["summary"]["structured_record_count"], 2)
            self.assertEqual(inventory["files"][0]["shape"]["record_groups"][0]["path"], "$.items")
            self.assertNotIn("record_sets", inventory)
            self.assertEqual(
                validate_source_inventory(inventory, SCHEMA_ROOT / "source_inventory.schema.json"),
                [],
            )

    def test_uncollected_terminal_source_is_not_bad_data(self) -> None:
        seed = sample_seed()
        digest = canonical_json_sha256(seed)
        plan = source_plan_payload(seed, digest)
        plan["sources"][0]["status"] = "unavailable"
        with tempfile.TemporaryDirectory() as directory:
            inventory = build_source_inventory(
                Path(directory), seed_global_id=seed["global_id"],
                seed_sha256=digest, source_plan=plan,
            )
        self.assertEqual(inventory["sources"][0]["profile_status"], "not_collected")

    def test_inventory_rejects_explicit_placeholder_and_parses_xsd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            raw = run_dir / "workspace/raw"
            raw.mkdir(parents=True)
            (raw / "pointer.md").write_text(
                'As of VAST 4.3, updates are found <a href="https://example.test/spec">here</a>.',
                encoding="utf-8",
            )
            (raw / "schema.xsd").write_text(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>',
                encoding="utf-8",
            )
            seed = sample_seed()
            digest = canonical_json_sha256(seed)
            plan = source_plan_payload(
                seed, digest, status="complete", record_count=2,
                raw_files=["raw/pointer.md", "raw/schema.xsd"],
            )
            inventory = build_source_inventory(
                run_dir, seed_global_id=seed["global_id"],
                seed_sha256=digest, source_plan=plan,
            )
            files = {item["path"]: item for item in inventory["files"]}
            self.assertEqual(files["raw/pointer.md"]["parse_status"], "placeholder")
            self.assertEqual(files["raw/schema.xsd"]["parse_status"], "parsed")
            self.assertEqual(inventory["sources"][0]["profile_status"], "partial")
            self.assertEqual(inventory["summary"]["usable_file_count"], 1)
            self.assertEqual(
                validate_source_inventory(
                    inventory, SCHEMA_ROOT / "source_inventory.schema.json"
                ),
                [],
            )

    def test_source_code_is_domain_data_but_unknown_binary_is_not_usable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            raw = run_dir / "workspace/raw"
            raw.mkdir(parents=True)
            (raw / "Contract.sol").write_text("pragma solidity ^0.8.20;\n", encoding="utf-8")
            (raw / "detector.py").write_text("class Detector:\n    pass\n", encoding="utf-8")
            (raw / "payload.zzz").write_bytes(b"\x00\x01opaque")
            seed = sample_seed()
            digest = canonical_json_sha256(seed)
            plan = source_plan_payload(
                seed, digest, status="complete", record_count=0,
                raw_files=[
                    "raw/Contract.sol", "raw/detector.py", "raw/payload.zzz",
                ],
            )
            inventory = build_source_inventory(
                run_dir, seed_global_id=seed["global_id"],
                seed_sha256=digest, source_plan=plan,
            )
            files = {item["path"]: item for item in inventory["files"]}
            self.assertEqual(files["raw/Contract.sol"]["format"], "solidity")
            self.assertEqual(files["raw/Contract.sol"]["content_roles"], ["domain_file"])
            self.assertEqual(files["raw/Contract.sol"]["parse_status"], "sampled")
            self.assertEqual(files["raw/detector.py"]["content_roles"], ["domain_file"])
            self.assertEqual(files["raw/payload.zzz"]["content_roles"], ["unknown"])
            self.assertEqual(inventory["summary"]["usable_file_count"], 2)
            self.assertEqual(inventory["sources"][0]["profile_status"], "partial")

    def test_file_format_roles_detect_alias_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed = sample_seed()
            digest = canonical_json_sha256(seed)
            plan = source_plan_payload(seed, digest)
            plan["data_mode"] = "hybrid"
            plan["required_file_formats"] = ["md"]
            plan["evidence_file_formats"] = ["markdown"]
            plan["file_dependent_seed_paths"] = ["$.environment.description"]
            plan_path = run_dir / "provenance/source_plan.json"
            write_json(plan_path, plan)
            validator = SourcePlanValidator(SCHEMA_ROOT / "source_plan.schema.json")
            _, issues = validator.validate(
                plan_path,
                seed_global_id=seed["global_id"],
                seed_sha256=digest,
                checkpoint={"raw_files": [], "source_urls": [plan["sources"][0]["url"]]},
                scenario_research=scenario_payload(seed, digest),
            )
            self.assertIn("file_format_role_conflict", {item.code for item in issues})


class IntegrationPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seed = sample_seed()
        self.digest = canonical_json_sha256(self.seed)
        self.scenario = scenario_payload(self.seed, self.digest)
        self.source_plan = source_plan_payload(
            self.seed, self.digest, status="complete", record_count=2,
            raw_files=["raw/items.json"],
        )
        self.inventory = {
            "files": [{"path": "raw/items.json", "source_id": "items"}],
            "sources": [{"source_id": "items", "profile_status": "usable"}],
        }
        self.schema = json.loads((SCHEMA_ROOT / "integration_plan.schema.json").read_text())

    def plan(self) -> dict[str, object]:
        item = record_set(
            "item", source_id="items", source_path="raw/items.json",
            fields={
                "item_id": field("string", "条目标识。"),
                "name": field("string", "条目名称。"),
            },
            key_fields=["item_id"],
            standalone_reason="本环境只有一个核心记录集合。",
        )
        return {
            "schema_version": "1.0",
            "seed_global_id": self.seed["global_id"],
            "seed_sha256": self.digest,
            "environment_id": "demo_catalog",
            "name": "Demo catalog",
            "description": "Public catalog data.",
            "record_sets": [item],
            "relationships": [],
            "filesystem_scopes": [],
            "need_bindings": [{
                "need_id": "item_records",
                "status": "realized",
                "record_set_ids": ["item"],
                "scope_ids": [],
                "description": "Item records support catalog lookup.",
            }],
            "source_decisions": [{
                "source_id": "items", "decision": "core", "reason": "Primary catalog source."
            }],
        }

    def validate(self, plan: dict[str, object]):
        return validate_integration_plan(
            plan,
            schema=self.schema,
            seed_global_id=self.seed["global_id"],
            seed_sha256=self.digest,
            scenario_research=self.scenario,
            source_plan=self.source_plan,
            source_inventory=self.inventory,
        )

    def test_valid_plan(self) -> None:
        self.assertEqual(self.validate(self.plan()), [])

    def test_rejects_deep_object_array_nesting(self) -> None:
        plan = self.plan()
        plan["record_sets"][0]["fields"]["too_deep"] = field(
            "object", "Outer.", properties={
                "middle": field("object", "Middle.", properties={
                    "inner": field("array", "Inner.", items=field("string", "Value."))
                }, required=["inner"], additionalProperties=False)
            }, required=["middle"], additionalProperties=False,
        )
        codes = {issue.code for issue in self.validate(plan)}
        self.assertIn("container_nesting_too_deep", codes)

    def test_rejects_unbound_need_and_unknown_raw(self) -> None:
        plan = self.plan()
        plan["need_bindings"][0]["record_set_ids"] = []
        plan["record_sets"][0]["source_paths"] = ["raw/not-downloaded.json"]
        codes = {issue.code for issue in self.validate(plan)}
        self.assertIn("unbound_data_need", codes)
        self.assertIn("asset_unknown_source_path", codes)

    def test_rejects_need_status_rewrite_and_source_path_mismatch(self) -> None:
        plan = self.plan()
        plan["need_bindings"][0]["status"] = "unavailable"
        self.inventory["files"][0]["source_id"] = "another_source"
        codes = {issue.code for issue in self.validate(plan)}
        self.assertIn("need_status_mismatch", codes)
        self.assertIn("asset_source_path_mismatch", codes)

    def test_rejects_need_binding_without_step2_source_lineage(self) -> None:
        plan = self.plan()
        other_source = dict(self.source_plan["sources"][0])
        other_source.update({
            "source_id": "other",
            "raw_files": ["raw/other.json"],
        })
        self.source_plan["sources"].append(other_source)
        self.inventory["files"].append({
            "path": "raw/other.json", "source_id": "other",
        })
        self.inventory["sources"].append({
            "source_id": "other", "profile_status": "usable",
        })
        plan["record_sets"][0]["source_ids"] = ["other"]
        plan["record_sets"][0]["source_paths"] = ["raw/other.json"]
        plan["source_decisions"] = [
            {"source_id": "items", "decision": "evidence_only", "reason": "Need evidence."},
            {"source_id": "other", "decision": "core", "reason": "Final records."},
        ]

        codes = {issue.code for issue in self.validate(plan)}

        self.assertIn("need_binding_without_source_lineage", codes)

    def test_rejects_selected_unusable_source(self) -> None:
        self.inventory["sources"][0]["profile_status"] = "unusable"
        codes = {issue.code for issue in self.validate(self.plan())}
        self.assertIn("selected_source_not_usable", codes)

    def test_rejects_final_asset_from_evidence_only_source(self) -> None:
        plan = self.plan()
        plan["source_decisions"][0]["decision"] = "evidence_only"
        codes = {issue.code for issue in self.validate(plan)}
        self.assertIn("asset_uses_unselected_source", codes)

    def test_rejects_volatile_counts_in_semantic_descriptions(self) -> None:
        plan = self.plan()
        plan["record_sets"][0]["description"] = "Six fixed VAST XML inputs for diagnostics."
        plan["need_bindings"][0]["description"] = (
            "The Scope contains three fixed, well-formed IAB fixtures."
        )
        codes = {issue.code for issue in self.validate(plan)}
        self.assertIn("volatile_cardinality_description", codes)

    def test_allows_versions_and_years_in_descriptions(self) -> None:
        plan = self.plan()
        plan["record_sets"][0]["description"] = (
            "One record represents an item published under schema 2.0 in 2026."
        )
        self.assertEqual(self.validate(plan), [])


class MaterializationAndProfileTests(unittest.TestCase):
    def test_integration_profile_reports_state_left_behind_by_plan_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            item = record_set(
                "item", source_id="items", source_path="raw/items.json",
                fields={"item_id": field("string", "Item ID.")},
                key_fields=["item_id"],
                standalone_reason="The only current business record set.",
            )
            write_json(run_dir / "items.json", {"item": [{"item_id": "a"}]})
            materialize_record_set(
                run_dir / "state/records.sqlite", record_set=item,
                input_path=run_dir / "items.json",
            )
            with sqlite3.connect(run_dir / "state/records.sqlite") as connection:
                connection.execute("CREATE TABLE stale_table (stale_id TEXT) STRICT")
            stale_scope = run_dir / "state/filesystem_scopes/stale_scope"
            stale_scope.mkdir(parents=True)
            (stale_scope / "old.txt").write_text("old", encoding="utf-8")
            plan = {
                "record_sets": [item], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [{"need_id": "items", "status": "realized",
                    "record_set_ids": ["item"], "scope_ids": [],
                    "description": "Current item records."}],
                "source_decisions": [
                    {"source_id": "items", "decision": "core", "reason": "Primary source."}
                ],
            }

            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )

            codes = {item["code"] for item in profile["integration_gaps"]}
            self.assertIn("undeclared_record_tables", codes)
            self.assertIn("undeclared_scope_directories", codes)
            self.assertEqual(
                profile["asset_profile"]["undeclared_record_tables"], ["stale_table"],
            )
            self.assertEqual(
                profile["asset_profile"]["undeclared_scope_directories"], ["stale_scope"],
            )

    def test_supporting_records_cannot_satisfy_total_core_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            core = record_set(
                "core_item", source_id="core_source", source_path="raw/core.json",
                fields={
                    "item_id": field("string", "Item ID."),
                    "name": field("string", "Item name."),
                }, key_fields=["item_id"],
            )
            supporting = record_set(
                "lookup", source_id="lookup_source", source_path="raw/lookup.json",
                fields={
                    "lookup_id": field("string", "Lookup ID."),
                    "label": field("string", "Lookup label."),
                }, key_fields=["lookup_id"],
            )
            supporting["importance"] = "supporting"
            plan = {
                "record_sets": [core, supporting], "filesystem_scopes": [],
                "need_bindings": [{"need_id": "core", "status": "realized",
                    "record_set_ids": ["core_item"], "scope_ids": [], "description": "Core."}],
                "source_decisions": [],
            }
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                scenario_research={"data_needs": [{"need_id": "core", "priority": "core"}]},
                source_plan={"required_file_formats": [], "sources": []},
                source_inventory={"sources": []},
                integration_profile={
                    "integration_tier": "integrated",
                    "asset_profile": {
                        "record_counts": {"core_item": 25, "lookup": 1000},
                        "scope_file_counts": {}, "filesystem_scopes": [],
                    },
                    "relationship_profile": {}, "file_reference_profile": {},
                    "connectivity_profile": {"components": [["record:core_item"]]},
                    "source_integration_profile": {}, "need_binding_profile": {},
                },
                policy=EnvironmentQualityPolicy(
                    min_total_records=500, min_core_records=25,
                    min_records_per_substantial_record_set=25,
                    min_core_business_fields=0, min_need_coverage_percent=0,
                    min_realized_need_count=0,
                ),
            )
            self.assertEqual(quality["record_profile"]["total_record_count"], 1025)
            self.assertEqual(quality["record_profile"]["core_record_count"], 25)
            self.assertIn(
                "insufficient_record_depth",
                {item["code"] for item in quality["quality_gaps"]},
            )

    def test_empty_declared_fields_do_not_count_as_core_business_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            item = record_set(
                "item", source_id="source", source_path="raw/items.json",
                fields={
                    "item_id": field("string", "Item ID."),
                    "name": field("string", "Item name."),
                    "empty_note": field("string", "Optional note.", nullable=True),
                }, key_fields=["item_id"], standalone_reason="Single record collection.",
            )
            write_json(run_dir / "items.json", {"item": [
                {"item_id": f"item-{index}", "name": f"Item {index}", "empty_note": None}
                for index in range(25)
            ]})
            materialize_record_set(
                run_dir / "state/records.sqlite", record_set=item,
                input_path=run_dir / "items.json",
            )
            plan = {
                "record_sets": [item], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [{"need_id": "items", "status": "realized",
                    "record_set_ids": ["item"], "scope_ids": [], "description": "Items."}],
                "source_decisions": [{"source_id": "source", "decision": "core", "reason": "Core."}],
            }
            integration = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                scenario_research={"data_needs": [{"need_id": "items", "priority": "core"}]},
                source_plan={"required_file_formats": [],
                    "sources": [{"source_id": "source", "status": "complete"}]},
                source_inventory={"sources": [{"source_id": "source", "profile_status": "usable"}]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_total_records=0, min_core_records=25,
                    min_records_per_substantial_record_set=25,
                    min_core_business_fields=2, min_need_coverage_percent=100,
                ),
            )
            self.assertEqual(
                quality["record_profile"]["usable_business_field_counts"]["item"], 1,
            )
            self.assertIn("item", quality["record_profile"]["weak_core_record_sets"])

    def test_unavailable_core_need_prevents_rich_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            item = record_set(
                "item", source_id="source", source_path="raw/items.json",
                fields={
                    "item_id": field("string", "Item ID."),
                    "name": field("string", "Item name."),
                }, key_fields=["item_id"],
            )
            plan = {
                "record_sets": [item], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [
                    {"need_id": "items", "status": "realized",
                     "record_set_ids": ["item"], "scope_ids": [], "description": "Items."},
                    {"need_id": "deployments", "status": "unavailable",
                     "record_set_ids": [], "scope_ids": [], "description": "No public source."},
                ],
                "source_decisions": [],
            }
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                scenario_research={"data_needs": [
                    {"need_id": "items", "priority": "core"},
                    {"need_id": "deployments", "priority": "core"},
                ]},
                source_plan={"required_file_formats": [], "sources": []},
                source_inventory={"sources": []},
                integration_profile={
                    "integration_tier": "integrated",
                    "asset_profile": {
                        "record_counts": {"item": 25}, "scope_file_counts": {},
                        "filesystem_scopes": [],
                    },
                    "relationship_profile": {}, "file_reference_profile": {},
                    "connectivity_profile": {"components": [["record:item"]]},
                    "source_integration_profile": {}, "need_binding_profile": {},
                },
                policy=EnvironmentQualityPolicy(
                    min_total_records=0, min_core_records=25,
                    min_records_per_substantial_record_set=25,
                    min_core_business_fields=0, min_need_coverage_percent=0,
                    min_realized_need_count=0,
                ),
            )
            self.assertEqual(
                quality["need_profile"]["unavailable_core_needs"], ["deployments"],
            )
            self.assertIn(
                "unavailable_core_needs",
                {item["code"] for item in quality["quality_gaps"]},
            )

    def test_supporting_large_table_cannot_supply_core_record_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            core = record_set(
                "core_item", source_id="core_source", source_path="raw/core.json",
                fields={
                    "item_id": field("string", "Item ID."),
                    "name": field("string", "Item name."),
                },
                key_fields=["item_id"],
            )
            supporting = record_set(
                "lookup_value", source_id="lookup_source", source_path="raw/lookup.json",
                fields={
                    "value_id": field("string", "Value ID."),
                    "label": field("string", "Value label."),
                },
                key_fields=["value_id"],
            )
            supporting["importance"] = "supporting"
            plan = {
                "seed_global_id": "demo", "seed_sha256": "0" * 64,
                "record_sets": [core, supporting], "relationships": [],
                "filesystem_scopes": [],
                "need_bindings": [
                    {"need_id": "core_records", "status": "realized",
                     "record_set_ids": ["core_item"], "scope_ids": [],
                     "description": "Core business records."},
                    {"need_id": "lookup_registry", "status": "realized",
                     "record_set_ids": ["lookup_value"], "scope_ids": [],
                     "description": "Supporting lookup registry."},
                ],
                "source_decisions": [
                    {"source_id": "core_source", "decision": "core", "reason": "Core."},
                    {"source_id": "lookup_source", "decision": "supporting", "reason": "Lookup."},
                ],
            }
            integration = {
                "integration_tier": "integrated",
                "asset_profile": {
                    "record_counts": {"core_item": 5, "lookup_value": 1000},
                    "scope_file_counts": {}, "filesystem_scopes": [],
                },
                "relationship_profile": {"declared_count": 0, "valid_count": 0},
                "file_reference_profile": {"valid_reference_field_count": 0},
                "connectivity_profile": {"components": [["record:core_item", "record:lookup_value"]]},
                "source_integration_profile": {"selected_source_count": 2},
                "need_binding_profile": {"bound_asset_count": 2},
            }
            quality = build_environment_quality_profile(
                run_dir,
                plan=plan,
                scenario_research={"data_needs": [
                    {"need_id": "core_records", "priority": "core"},
                    {"need_id": "lookup_registry", "priority": "supporting"},
                ]},
                source_plan={
                    "required_file_formats": [],
                    "sources": [
                        {"source_id": "core_source", "status": "complete"},
                        {"source_id": "lookup_source", "status": "complete"},
                    ],
                },
                source_inventory={"sources": [
                    {"source_id": "core_source", "profile_status": "usable"},
                    {"source_id": "lookup_source", "profile_status": "usable"},
                ]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_total_records=0,
                    min_records_per_substantial_record_set=25,
                    min_core_records=25,
                    min_core_business_fields=0,
                    min_need_coverage_percent=0,
                    min_realized_need_count=0,
                ),
            )
            self.assertEqual(quality["quality_tier"], "not_rich")
            self.assertEqual(quality["record_profile"]["substantial_record_set_count"], 0)
            self.assertIn(
                "insufficient_substantial_record_sets",
                {item["code"] for item in quality["quality_gaps"]},
            )
            self.assertEqual(
                quality["need_profile"]["underdeveloped_core_needs"], ["core_records"],
            )

    def test_core_need_cannot_rely_only_on_supporting_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            samples = record_set(
                "sample", source_id="samples", source_path="raw/samples.json",
                fields={
                    "sample_id": field("string", "Sample ID."),
                    "label": field("string", "Sample label."),
                },
                key_fields=["sample_id"],
            )
            samples["importance"] = "supporting"
            plan = {
                "seed_global_id": "demo", "seed_sha256": "0" * 64,
                "record_sets": [samples], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [{
                    "need_id": "primary_samples", "status": "realized",
                    "record_set_ids": ["sample"], "scope_ids": [],
                    "description": "Primary samples.",
                }],
                "source_decisions": [{
                    "source_id": "samples", "decision": "core", "reason": "Samples."
                }],
            }
            integration = {
                "integration_tier": "integrated",
                "asset_profile": {
                    "record_counts": {"sample": 100},
                    "scope_file_counts": {}, "filesystem_scopes": [],
                },
                "relationship_profile": {"declared_count": 0, "valid_count": 0},
                "file_reference_profile": {"valid_reference_field_count": 0},
                "connectivity_profile": {"components": [["record:sample"]]},
                "source_integration_profile": {"selected_source_count": 1},
                "need_binding_profile": {"bound_asset_count": 1},
            }
            quality = build_environment_quality_profile(
                run_dir,
                plan=plan,
                scenario_research={"data_needs": [
                    {"need_id": "primary_samples", "priority": "core"},
                ]},
                source_plan={
                    "required_file_formats": [],
                    "sources": [{"source_id": "samples", "status": "complete"}],
                },
                source_inventory={"sources": [
                    {"source_id": "samples", "profile_status": "usable"},
                ]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_total_records=0,
                    min_records_per_substantial_record_set=0,
                    min_core_records=0,
                    min_core_business_fields=0,
                    min_need_coverage_percent=0,
                    min_realized_need_count=0,
                ),
            )
            self.assertIn(
                "underdeveloped_core_needs",
                {item["code"] for item in quality["quality_gaps"]},
            )
            self.assertEqual(
                quality["need_profile"]["underdeveloped_core_needs"],
                ["primary_samples"],
            )

    def test_core_asset_must_serve_a_core_data_need(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            lookup = record_set(
                "lookup_value", source_id="lookup_source", source_path="raw/lookup.json",
                fields={
                    "value_id": field("string", "Value ID."),
                    "label": field("string", "Value label."),
                },
                key_fields=["value_id"],
            )
            plan = {
                "seed_global_id": "demo", "seed_sha256": "0" * 64,
                "record_sets": [lookup], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [{
                    "need_id": "lookup_registry", "status": "realized",
                    "record_set_ids": ["lookup_value"], "scope_ids": [],
                    "description": "Supporting lookup registry.",
                }],
                "source_decisions": [{
                    "source_id": "lookup_source", "decision": "core", "reason": "Authoritative."
                }],
            }
            integration = {
                "integration_tier": "integrated",
                "asset_profile": {
                    "record_counts": {"lookup_value": 1000},
                    "scope_file_counts": {}, "filesystem_scopes": [],
                },
                "relationship_profile": {"declared_count": 0, "valid_count": 0},
                "file_reference_profile": {"valid_reference_field_count": 0},
                "connectivity_profile": {"components": [["record:lookup_value"]]},
                "source_integration_profile": {"selected_source_count": 1},
                "need_binding_profile": {"bound_asset_count": 1},
            }
            quality = build_environment_quality_profile(
                run_dir,
                plan=plan,
                scenario_research={"data_needs": [
                    {"need_id": "primary_records", "priority": "core"},
                    {"need_id": "lookup_registry", "priority": "supporting"},
                ]},
                source_plan={
                    "required_file_formats": [],
                    "sources": [{"source_id": "lookup_source", "status": "complete"}],
                },
                source_inventory={"sources": [
                    {"source_id": "lookup_source", "profile_status": "usable"},
                ]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_total_records=0,
                    min_records_per_substantial_record_set=0,
                    min_core_records=0,
                    min_core_business_fields=0,
                    min_need_coverage_percent=0,
                    min_realized_need_count=0,
                ),
            )
            self.assertIn(
                "misclassified_core_assets",
                {item["code"] for item in quality["quality_gaps"]},
            )
            self.assertEqual(
                quality["record_profile"]["misclassified_core_assets"], ["lookup_value"],
            )

    def test_transformation_cannot_modify_raw_or_read_candidate_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            raw = run_dir / "workspace/raw/input.json"
            raw.parent.mkdir(parents=True)
            raw.write_text('{"value":"real"}\n', encoding="utf-8")
            state_secret = run_dir / "state/secret.txt"
            state_secret.parent.mkdir(parents=True)
            state_secret.write_text("candidate-only", encoding="utf-8")
            script = run_dir / "probe.py"
            script.write_text(
                """import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--run-dir', type=Path, required=True)
p.add_argument('--asset-id', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
(a.run_dir / 'workspace/raw/input.json').write_text('tampered')
a.output.write_text((a.run_dir / 'state/secret.txt').read_text())
""",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                run_record_transformation(
                    run_dir,
                    script=script,
                    output=root / "write-probe/records.json",
                    asset_id="item",
                    timeout_seconds=20,
                )
            self.assertEqual(raw.read_text(encoding="utf-8"), '{"value":"real"}\n')
            read_probe = run_dir / "read_probe.py"
            read_probe.write_text(
                """import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--run-dir', type=Path, required=True)
p.add_argument('--asset-id', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.write_text('visible' if (a.run_dir / 'state/secret.txt').exists() else 'hidden')
""",
                encoding="utf-8",
            )
            output = root / "read-probe/records.json"
            run_record_transformation(
                run_dir,
                script=read_probe,
                output=output,
                asset_id="item",
                timeout_seconds=20,
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "hidden")

    def test_thin_file_collection_is_not_rich(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            source = run_dir / "single.xml"
            source.write_text("<root/>", encoding="utf-8")
            materialize_scope(
                run_dir / "state/filesystem_scopes",
                scope_id="samples",
                sources=[source],
                mode="copy",
            )
            scope = {
                "scope_id": "samples",
                "name": "Samples",
                "description": "XML samples.",
                "access": "copy_on_write",
                "structure": {"kind": "file_collection", "path": "*.xml", "format": "xml"},
                "importance": "core",
                "source_ids": ["xml_source"],
                "source_paths": ["raw/single.xml"],
                "materialization": "copy",
                "transformation": "Copy the public XML sample.",
                "transformation_id": "copy_samples",
                "standalone_reason": "The environment is file-native.",
            }
            plan = {
                "seed_global_id": "demo",
                "seed_sha256": "0" * 64,
                "record_sets": [],
                "relationships": [],
                "filesystem_scopes": [scope],
                "need_bindings": [{
                    "need_id": "samples",
                    "status": "realized",
                    "record_set_ids": [],
                    "scope_ids": ["samples"],
                    "description": "Actual XML samples.",
                }],
                "source_decisions": [{
                    "source_id": "xml_source", "decision": "core", "reason": "Primary samples."
                }],
            }
            integration = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            self.assertEqual(integration["integration_tier"], "integrated")
            quality = build_environment_quality_profile(
                run_dir,
                plan=plan,
                source_plan={
                    "required_file_formats": ["xml"],
                    "sources": [{"source_id": "xml_source", "status": "complete"}],
                },
                source_inventory={
                    "sources": [{"source_id": "xml_source", "profile_status": "usable"}],
                },
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_collection_members=2,
                    min_need_coverage_percent=100,
                ),
            )
            self.assertEqual(quality["quality_tier"], "not_rich")
            self.assertIn(
                "insufficient_scope_collection_members",
                {item["code"] for item in quality["quality_gaps"]},
            )

    def test_scope_layout_must_match_actual_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project").mkdir()
            (root / "project/main.txt").write_text("not solidity", encoding="utf-8")
            structure = {
                "kind": "directory",
                "path": "project",
                "layout": [{
                    "kind": "file_collection",
                    "path": "contracts/*.sol",
                    "description": "Solidity contracts.",
                    "required": True,
                    "format": "solidity",
                }],
            }
            codes = {item.code for item in validate_scope_tree(root, structure)}
            self.assertIn("missing_required_scope_path", codes)

    def test_nested_collection_counts_drive_file_richness_and_format_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            scope_root = run_dir / "state/filesystem_scopes/samples"
            scope_root.mkdir(parents=True)
            for index in range(4):
                (scope_root / f"sample_{index}.xml").write_text("<root/>", encoding="utf-8")
            (scope_root / "README.md").write_text("# Samples\n", encoding="utf-8")
            scope = {
                "scope_id": "samples", "name": "Samples", "description": "Samples.",
                "access": "copy_on_write", "importance": "core",
                "source_ids": ["source"], "source_paths": ["raw/archive.zip"],
                "materialization": "extract", "transformation": "Extract.",
                "transformation_id": "extract_samples", "standalone_reason": "File-native.",
                "structure": {
                    "kind": "directory", "path": ".", "layout": [
                        {"kind": "file_collection", "path": "*.xml", "description": "XML.",
                         "required": True, "format": "xml"},
                        {"kind": "file", "path": "README.md", "description": "Guide.",
                         "required": True, "format": "markdown"},
                    ],
                },
            }
            plan = {
                "seed_global_id": "demo", "seed_sha256": "0" * 64,
                "record_sets": [], "relationships": [], "filesystem_scopes": [scope],
                "need_bindings": [{"need_id": "samples", "status": "realized",
                    "record_set_ids": [], "scope_ids": ["samples"], "description": "Samples."}],
                "source_decisions": [{"source_id": "source", "decision": "core", "reason": "Core."}],
            }
            integration = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                source_plan={"required_file_formats": ["xml", "md"],
                             "evidence_file_formats": ["html", "json"],
                             "sources": [{"source_id": "source", "status": "complete"}]},
                source_inventory={"sources": [{"source_id": "source", "profile_status": "usable"}]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(min_collection_members=4, min_need_coverage_percent=100),
            )
            self.assertEqual(quality["quality_tier"], "rich")
            self.assertEqual(quality["file_profile"]["collection_member_counts"]["samples"], 4)
            self.assertEqual(
                quality["file_profile"]["collection_profiles"]["samples"][0]["member_count"], 4,
            )
            self.assertEqual(quality["file_profile"]["required_formats"], ["markdown", "xml"])
            self.assertEqual(quality["file_profile"]["evidence_formats"], ["html", "json"])

    def test_directory_collection_richness_counts_projects_not_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            scope_root = run_dir / "state/filesystem_scopes/projects"
            for project_index in range(2):
                project = scope_root / f"project_{project_index}"
                project.mkdir(parents=True)
                for file_index in range(5):
                    (project / f"Contract{file_index}.sol").write_text(
                        "pragma solidity ^0.8.20;\n", encoding="utf-8",
                    )
            scope = {
                "scope_id": "projects", "name": "Projects", "description": "Projects.",
                "access": "copy_on_write", "importance": "core",
                "source_ids": ["source"], "source_paths": ["raw/projects.zip"],
                "materialization": "extract", "transformation": "Extract projects.",
                "transformation_id": "extract_projects", "standalone_reason": "File-native.",
                "structure": {
                    "kind": "directory_collection", "path": "project_*",
                    "layout": [{"kind": "file_collection", "path": "*.sol",
                        "description": "Contracts.", "required": True, "format": "sol"}],
                },
            }
            plan = {
                "record_sets": [], "relationships": [], "filesystem_scopes": [scope],
                "need_bindings": [{"need_id": "projects", "status": "realized",
                    "record_set_ids": [], "scope_ids": ["projects"], "description": "Projects."}],
                "source_decisions": [{"source_id": "source", "decision": "core", "reason": "Core."}],
            }
            integration = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                scenario_research={"data_needs": [{"need_id": "projects", "priority": "core"}]},
                source_plan={"required_file_formats": ["solidity"],
                    "sources": [{"source_id": "source", "status": "complete"}]},
                source_inventory={"sources": [{"source_id": "source", "profile_status": "usable"}]},
                integration_profile=integration,
                policy=EnvironmentQualityPolicy(
                    min_collection_members=8, min_need_coverage_percent=100,
                ),
            )
            self.assertEqual(quality["file_profile"]["collection_member_counts"]["projects"], 2)
            self.assertIn(
                "insufficient_scope_collection_members",
                {item["code"] for item in quality["quality_gaps"]},
            )

    def test_same_raw_file_cannot_be_materialized_into_multiple_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            scopes = []
            for scope_id in ("editable", "references"):
                root = run_dir / "state/filesystem_scopes" / scope_id
                root.mkdir(parents=True)
                (root / "sample.xml").write_text("<root/>", encoding="utf-8")
                scopes.append({
                    "scope_id": scope_id, "name": scope_id,
                    "description": "XML files.", "access": "read_only",
                    "structure": {"kind": "file", "path": "sample.xml", "format": "xml"},
                    "importance": "supporting", "source_ids": ["samples"],
                    "source_paths": ["raw/sample.xml"], "materialization": "copy",
                    "transformation": "Copy sample.", "transformation_id": f"copy_{scope_id}",
                    "standalone_reason": "Test scope.",
                })
            plan = {
                "record_sets": [], "relationships": [], "filesystem_scopes": scopes,
                "need_bindings": [{"need_id": "samples", "status": "realized",
                    "record_set_ids": [], "scope_ids": ["editable", "references"],
                    "description": "Samples."}],
                "source_decisions": [{"source_id": "samples", "decision": "core", "reason": "Core."}],
            }
            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            self.assertIn(
                "duplicate_scope_source_paths",
                {item["code"] for item in profile["integration_gaps"]},
            )

    def test_multi_source_catch_all_scope_is_fragmented(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            root = run_dir / "state/filesystem_scopes/source_materials"
            root.mkdir(parents=True)
            names = [
                "one.xml", "two.xml", "three.json", "four.json",
                "five.md", "six.md", "seven.txt", "eight.txt",
            ]
            for name in names:
                (root / name).write_text("<root/>" if name.endswith(".xml") else "content", encoding="utf-8")
            scope = {
                "scope_id": "source_materials", "name": "Source materials",
                "description": "All source files.", "access": "read_only",
                "importance": "core", "source_ids": ["a", "b", "c"],
                "source_paths": [f"raw/{name}" for name in names],
                "materialization": "copy", "transformation": "Copy all files.",
                "transformation_id": "copy_all", "standalone_reason": "File-native.",
                "structure": {
                    "kind": "directory", "path": ".", "layout": [
                        {"kind": "file_collection", "path": "*.xml", "format": "xml",
                         "description": "XML files.", "required": True},
                        {"kind": "file_collection", "path": "*.json", "format": "json",
                         "description": "JSON files.", "required": True},
                        {"kind": "file_collection", "path": "*.md", "format": "markdown",
                         "description": "Markdown files.", "required": True},
                        {"kind": "file_collection", "path": "*.txt", "format": "text",
                         "description": "Text files.", "required": True},
                    ],
                },
            }
            plan = {
                "record_sets": [], "relationships": [], "filesystem_scopes": [scope],
                "need_bindings": [{
                    "need_id": "materials", "status": "realized",
                    "record_set_ids": [], "scope_ids": ["source_materials"],
                    "description": "Materials.",
                }],
                "source_decisions": [
                    {"source_id": source_id, "decision": "core", "reason": "Material."}
                    for source_id in ("a", "b", "c")
                ],
            }
            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            self.assertEqual(profile["integration_tier"], "fragmented")
            self.assertIn(
                "broad_flat_filesystem_scope",
                {item["code"] for item in profile["integration_gaps"]},
            )

    def test_selected_mutable_repository_raw_is_not_rich(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            source = run_dir / "sample.xml"
            source.write_text("<root/>", encoding="utf-8")
            materialize_scope(
                run_dir / "state/filesystem_scopes",
                scope_id="samples", sources=[source], mode="copy",
            )
            scope = {
                "scope_id": "samples", "name": "Samples", "description": "XML.",
                "access": "read_only",
                "structure": {"kind": "file", "path": "sample.xml", "format": "xml"},
                "importance": "core", "source_ids": ["repo"],
                "source_paths": ["raw/sample.xml"], "materialization": "copy",
                "transformation": "Copy.", "transformation_id": "copy_sample",
                "standalone_reason": "File-native.",
            }
            plan = {
                "record_sets": [], "relationships": [], "filesystem_scopes": [scope],
                "need_bindings": [{"need_id": "samples", "status": "realized",
                    "record_set_ids": [], "scope_ids": ["samples"], "description": "Samples."}],
                "source_decisions": [{"source_id": "repo", "decision": "core", "reason": "Core."}],
            }
            integration = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            quality = build_environment_quality_profile(
                run_dir, plan=plan,
                source_plan={"required_file_formats": ["xml"],
                             "sources": [{"source_id": "repo", "status": "complete"}]},
                source_inventory={
                    "sources": [{"source_id": "repo", "profile_status": "usable"}],
                    "files": [{"path": "raw/sample.xml",
                               "retrieval_stability": "mutable_repository"}],
                },
                integration_profile=integration,
            )
            self.assertEqual(quality["quality_tier"], "not_rich")
            self.assertIn(
                "mutable_repository_sources",
                {item["code"] for item in quality["quality_gaps"]},
            )

    def test_directory_layout_must_cover_every_exposed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "sample.xml").write_text("<root/>", encoding="utf-8")
            (project / "preview.mp4").write_bytes(b"video")
            structure = {
                "kind": "directory",
                "path": "project",
                "layout": [{
                    "kind": "file_collection",
                    "path": "*.xml",
                    "description": "XML samples.",
                    "required": True,
                    "format": "xml",
                }],
            }
            issues = validate_scope_tree(root, structure)
            self.assertIn(
                "unmodeled_files_in_directory_scope",
                {item.code for item in issues},
            )
            self.assertIn("preview.mp4", next(
                item.message for item in issues
                if item.code == "unmodeled_files_in_directory_scope"
            ))

    def test_scope_can_explicitly_retain_invalid_input_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.xml").write_text("<root/>", encoding="utf-8")
            (root / "broken.xml").write_text("<root>", encoding="utf-8")
            strict = {
                "kind": "file_collection", "path": "*.xml", "format": "xml",
            }
            self.assertIn(
                "invalid_scope_file_format",
                {item.code for item in validate_scope_tree(root, strict)},
            )
            diagnostic = {
                **strict,
                "content_validation": "allow_invalid",
            }
            self.assertEqual(validate_scope_tree(root, diagnostic), [])
            self.assertEqual(
                [item["path"] for item in permitted_invalid_files(root, diagnostic)],
                ["broken.xml"],
            )

    def test_directory_cannot_disable_child_content_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.xml").write_text("<root/>", encoding="utf-8")
            structure = {
                "kind": "directory",
                "path": ".",
                "content_validation": "allow_invalid",
                "layout": [{
                    "kind": "file",
                    "path": "input.xml",
                    "description": "Input XML.",
                    "required": True,
                    "format": "xml",
                }],
            }
            self.assertIn(
                "directory_with_content_validation",
                {item.code for item in validate_scope_tree(root, structure)},
            )

    def test_materializes_sqlite_and_accepts_justified_standalone_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            input_path = run_dir / "items.json"
            write_json(input_path, {"item": [
                {"item_id": "a", "active": True, "tags": ["x"]},
                {"item_id": "b", "active": False, "tags": []},
            ]})
            item = record_set(
                "item", source_id="items", source_path="raw/items.json",
                fields={
                    "item_id": field("string", "条目标识。"),
                    "active": field("boolean", "是否有效。"),
                    "tags": field("array", "标签。", items=field("string", "一个标签。")),
                },
                key_fields=["item_id"],
                standalone_reason="该环境只有一个自包含目录表。",
            )
            count = materialize_record_set(
                run_dir / "state/records.sqlite", record_set=item, input_path=input_path,
            )
            self.assertEqual(count, 2)
            with sqlite3.connect(run_dir / "state/records.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT active, tags FROM item ORDER BY item_id").fetchall(), [(1, '[\"x\"]'), (0, '[]')])
            plan = {
                "record_sets": [item],
                "relationships": [],
                "filesystem_scopes": [],
                "need_bindings": [{"need_id": "items", "status": "realized", "record_set_ids": ["item"], "scope_ids": [], "description": "items"}],
                "source_decisions": [{"source_id": "items", "decision": "core", "reason": "primary"}],
            }
            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            self.assertEqual(profile["integration_tier"], "integrated")

    def test_field_profile_exposes_bounded_samples_and_semantic_review_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            item = record_set(
                "api_member", source_id="docs", source_path="raw/docs.html",
                fields={
                    "member_id": field("string", "Member ID."),
                    "member_kind": field("string", "Member category."),
                    "member_type": field("string", "Member type."),
                    "visibility": field("string", "Member visibility."),
                    "summary": field("string", "Member summary."),
                },
                key_fields=["member_id"],
                standalone_reason="A self-contained documented API collection.",
            )
            rows = []
            for index in range(50):
                rows.append({
                    "member_id": f"member-{index}",
                    "member_kind": (
                        "error" if index < 48 else "event" if index == 48 else "function"
                    ),
                    "member_type": "function",
                    "visibility": (
                        "error" if index < 5 else "event" if index < 10 else "public"
                    ),
                    "summary": "x" * (index + 1),
                })
            write_json(run_dir / "members.json", {"api_member": rows})
            materialize_record_set(
                run_dir / "state/records.sqlite", record_set=item,
                input_path=run_dir / "members.json",
            )
            plan = {
                "record_sets": [item], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [{"need_id": "api", "status": "realized",
                    "record_set_ids": ["api_member"], "scope_ids": [],
                    "description": "Documented API."}],
                "source_decisions": [
                    {"source_id": "docs", "decision": "core", "reason": "API source."}
                ],
            }

            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )

            self.assertEqual(profile["integration_tier"], "integrated")
            record_profile = profile["asset_profile"]["record_sets"][0]
            kind = record_profile["fields"]["member_kind"]
            self.assertEqual(kind["top_values"][0], {
                "value": "error", "count": 48, "percent_of_populated": 96.0,
            })
            self.assertLessEqual(len(kind["sample_values"]), 5)
            self.assertEqual(
                record_profile["fields"]["summary"]["value_shape"]["max_length"], 50,
            )
            review = profile["asset_profile"]["field_review"]
            self.assertEqual(review["status"], "attention")
            self.assertIn(
                "dominant_categorical_value",
                {finding["code"] for finding in review["findings"]},
            )
            self.assertIn(
                "overlapping_categorical_domains",
                {finding["code"] for finding in review["findings"]},
            )
            self.assertIn(
                "constant_categorical_value",
                {finding["code"] for finding in review["findings"]},
            )

            plan_path = run_dir / "provenance/integration_plan.json"
            profile_path = run_dir / "provenance/integration_profile.json"
            review_path = run_dir / "provenance/field_review.json"
            write_json(plan_path, plan)
            write_json(profile_path, profile)
            (run_dir / "workspace/raw").mkdir(parents=True)
            (run_dir / "workspace/raw/docs.html").write_text(
                "<h3>Functions</h3><div>member evidence</div>", encoding="utf-8"
            )
            missing = field_review_issues(
                run_dir, profile=profile, plan=plan, review_path=review_path,
                integration_plan_path=plan_path, integration_profile_path=profile_path,
            )
            self.assertEqual({item["code"] for item in missing}, {"missing_field_review"})
            draft = {
                "schema_version": "1.0",
                "findings": [{
                    "finding_id": finding["finding_id"],
                    "decision": "verified_against_raw",
                    "reason": "The complete source card was checked and this distribution matches its markup.",
                    "evidence_paths": ["raw/docs.html"],
                } for finding in review["findings"]],
            }
            payload = build_field_review_payload(
                run_dir, draft=draft, profile=profile, plan=plan,
                integration_plan_path=plan_path, integration_profile_path=profile_path,
            )
            write_json(review_path, payload)
            self.assertEqual(field_review_issues(
                run_dir, profile=profile, plan=plan, review_path=review_path,
                integration_plan_path=plan_path, integration_profile_path=profile_path,
            ), [])
            changed_profile = {**profile, "summary": "A changed profile snapshot."}
            write_json(profile_path, changed_profile)
            stale = field_review_issues(
                run_dir, profile=changed_profile, plan=plan, review_path=review_path,
                integration_plan_path=plan_path, integration_profile_path=profile_path,
            )
            self.assertIn("stale_field_review", {item["code"] for item in stale})

    def test_detects_multisource_fragmentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            left = record_set(
                "left", source_id="source_a", source_path="raw/a.json",
                fields={"left_id": field("string", "Left ID.")}, key_fields=["left_id"],
            )
            right = record_set(
                "right", source_id="source_b", source_path="raw/b.json",
                fields={"right_id": field("string", "Right ID.")}, key_fields=["right_id"],
            )
            write_json(run_dir / "left.json", {"left": [{"left_id": "l1"}]})
            write_json(run_dir / "right.json", {"right": [{"right_id": "r1"}]})
            materialize_record_set(run_dir / "state/records.sqlite", record_set=left, input_path=run_dir / "left.json")
            materialize_record_set(run_dir / "state/records.sqlite", record_set=right, input_path=run_dir / "right.json")
            plan = {
                "record_sets": [left, right], "relationships": [], "filesystem_scopes": [],
                "need_bindings": [
                    {"need_id": "left", "status": "realized", "record_set_ids": ["left"], "scope_ids": [], "description": "left"},
                    {"need_id": "right", "status": "realized", "record_set_ids": ["right"], "scope_ids": [], "description": "right"},
                ],
                "source_decisions": [
                    {"source_id": "source_a", "decision": "core", "reason": "left"},
                    {"source_id": "source_b", "decision": "core", "reason": "right"},
                ],
            }
            profile = build_integration_profile(run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64)
            codes = {item["code"] for item in profile["integration_gaps"]}
            self.assertIn("unjustified_isolated_assets", codes)
            self.assertIn("multiple_sources_without_integration", codes)

    def test_detects_bad_relationship_and_file_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            parent = record_set(
                "parent", source_id="source_a", source_path="raw/a.json",
                fields={"parent_id": field("string", "Parent ID.")}, key_fields=["parent_id"],
            )
            child = record_set(
                "child", source_id="source_b", source_path="raw/b.json",
                fields={
                    "child_id": field("string", "Child ID."),
                    "parent_id": field("string", "Parent reference."),
                    "file_path": field("string", "Input path.", reference={
                        "kind": "filesystem_path", "scope_id": "documents", "target": "file"
                    }),
                }, key_fields=["child_id"],
            )
            write_json(run_dir / "parent.json", {"parent": [{"parent_id": "p1"}]})
            write_json(run_dir / "child.json", {"child": [{"child_id": "c1", "parent_id": "missing", "file_path": "missing.xml"}]})
            materialize_record_set(run_dir / "state/records.sqlite", record_set=parent, input_path=run_dir / "parent.json")
            materialize_record_set(run_dir / "state/records.sqlite", record_set=child, input_path=run_dir / "child.json")
            raw_file = run_dir / "raw.xml"
            raw_file.write_text("<root/>", encoding="utf-8")
            materialize_scope(run_dir / "state/filesystem_scopes", scope_id="documents", sources=[raw_file], mode="copy")
            scope = {
                "scope_id": "documents", "importance": "core", "source_ids": ["source_b"],
                "source_paths": ["raw/raw.xml"], "standalone_reason": None,
                "transformation_id": "build_documents",
            }
            plan = {
                "record_sets": [parent, child],
                "relationships": [{
                    "relationship_id": "child_to_parent", "description": "Child belongs to parent.",
                    "from": {"record_set_id": "child", "fields": ["parent_id"]},
                    "to": {"record_set_id": "parent", "fields": ["parent_id"]},
                    "cardinality": "many_to_one",
                }],
                "filesystem_scopes": [scope],
                "need_bindings": [{"need_id": "all", "status": "realized", "record_set_ids": ["parent", "child"], "scope_ids": ["documents"], "description": "all"}],
                "source_decisions": [
                    {"source_id": "source_a", "decision": "core", "reason": "parent"},
                    {"source_id": "source_b", "decision": "core", "reason": "child"},
                ],
            }
            profile = build_integration_profile(run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64)
            codes = {item["code"] for item in profile["integration_gaps"]}
            self.assertIn("invalid_relationship", codes)
            self.assertIn("invalid_file_reference", codes)

    def test_empty_relationship_and_file_reference_do_not_connect_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            parent = record_set(
                "parent", source_id="source_a", source_path="raw/a.json",
                fields={"parent_id": field("string", "Parent ID.")}, key_fields=["parent_id"],
            )
            child = record_set(
                "child", source_id="source_b", source_path="raw/b.json",
                fields={
                    "child_id": field("string", "Child ID."),
                    "parent_id": field("string", "Optional parent.", nullable=True),
                    "file_path": field("string", "Optional file.", nullable=True, reference={
                        "kind": "filesystem_path", "scope_id": "documents", "target": "file",
                    }),
                }, key_fields=["child_id"],
            )
            write_json(run_dir / "parent.json", {"parent": [{"parent_id": "p1"}]})
            write_json(run_dir / "child.json", {
                "child": [{"child_id": "c1", "parent_id": None, "file_path": None}],
            })
            materialize_record_set(
                run_dir / "state/records.sqlite", record_set=parent,
                input_path=run_dir / "parent.json",
            )
            materialize_record_set(
                run_dir / "state/records.sqlite", record_set=child,
                input_path=run_dir / "child.json",
            )
            raw_file = run_dir / "document.xml"
            raw_file.write_text("<root/>", encoding="utf-8")
            materialize_scope(
                run_dir / "state/filesystem_scopes", scope_id="documents",
                sources=[raw_file], mode="copy",
            )
            plan = {
                "record_sets": [parent, child],
                "relationships": [{
                    "relationship_id": "child_parent", "description": "Optional parent.",
                    "from": {"record_set_id": "child", "fields": ["parent_id"]},
                    "to": {"record_set_id": "parent", "fields": ["parent_id"]},
                    "cardinality": "many_to_one",
                }],
                "filesystem_scopes": [{
                    "scope_id": "documents", "importance": "supporting",
                    "source_ids": ["source_b"], "source_paths": ["raw/document.xml"],
                    "standalone_reason": None, "transformation_id": "copy_documents",
                }],
                "need_bindings": [{"need_id": "all", "status": "realized",
                    "record_set_ids": ["parent", "child"], "scope_ids": ["documents"],
                    "description": "All assets."}],
                "source_decisions": [
                    {"source_id": "source_a", "decision": "core", "reason": "Parent."},
                    {"source_id": "source_b", "decision": "core", "reason": "Child."},
                ],
            }
            profile = build_integration_profile(
                run_dir, plan=plan, seed_global_id="demo", seed_sha256="0" * 64,
            )
            codes = {item["code"] for item in profile["integration_gaps"]}
            self.assertIn("empty_relationship", codes)
            self.assertIn("empty_file_reference", codes)
            self.assertIn("multiple_sources_without_integration", codes)
            self.assertEqual(profile["connectivity_profile"]["edge_count"], 0)
            self.assertEqual(profile["relationship_profile"]["valid_count"], 0)
            self.assertEqual(profile["file_reference_profile"]["valid_reference_field_count"], 0)


class FiveStagePackageTests(unittest.TestCase):
    def test_terminal_coverage_allows_truthful_exhausted_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_run(run_dir)
            plan = source_plan_payload(
                seed, digest, status="unavailable", record_count=0,
            )
            plan["data_need_coverage"][0]["status"] = "unavailable"
            source_plan_path = run_dir / "provenance/source_plan.json"
            receipt_path = run_dir / ".datagen/source_plan_receipt.json"
            write_json(source_plan_path, plan)
            write_json(receipt_path, {"sha256": file_sha256(source_plan_path)})
            self.assertEqual(_terminal_coverage(run_dir), (True, True))
            write_json(
                source_plan_path,
                source_plan_payload(seed, digest, status="planned"),
            )
            write_json(receipt_path, {"sha256": file_sha256(source_plan_path)})
            self.assertEqual(_terminal_coverage(run_dir), (False, False))

    def test_controlled_materialization_freeze_and_publish(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            output_dir = root / "published"
            url = f"{base}/items.json"
            seed = sample_seed()
            digest = canonical_json_sha256(seed)
            from tests.data_gen_test_helpers import prepare_run

            prepare_run(
                run_dir,
                url=url,
                environment_quality_policy=EnvironmentQualityPolicy(
                    min_total_records=2,
                    min_records_per_substantial_record_set=2,
                    min_core_records=2,
                    min_core_business_fields=2,
                    min_need_coverage_percent=100,
                    min_realized_need_count=1,
                ),
            )
            download_raw_file(
                run_dir,
                url=url,
                output="raw/items.json",
                expected_format="json",
                timeout_seconds=10,
                source_id="items",
            )
            save_source_plan_payload(
                run_dir,
                source_plan_payload(
                    seed,
                    digest,
                    url=url,
                    status="complete",
                    record_count=2,
                    raw_files=["raw/items.json"],
                ),
            )
            prepare_integration(run_dir)
            plan_builder = IntegrationPlanTests()
            plan_builder.setUp()
            integration_plan = plan_builder.plan()
            integration_plan["record_sets"][0]["fields"]["category"] = field(
                "string", "Item category."
            )
            draft_plan = run_dir / ".datagen/drafts/integration_plan.json"
            write_json(draft_plan, integration_plan)
            save_integration_plan(run_dir, input_path=draft_plan)
            package = run_dir / ".datagen/drafts/build_item"
            package.mkdir()
            (package / "helper.py").write_text(
                "def normalize(value):\n    return value.strip()\n",
                encoding="utf-8",
            )
            script = package / "main.py"
            script.write_text(
                """from __future__ import annotations
import argparse
import json
from pathlib import Path
from helper import normalize

parser = argparse.ArgumentParser()
parser.add_argument('--run-dir', type=Path, required=True)
parser.add_argument('--asset-id', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
payload = json.loads((args.run_dir / 'workspace/raw/items.json').read_text())
records = [
    {'item_id': row['item_id'], 'name': normalize(row['name']), 'category': row['category']}
    for row in payload['items']
]
args.output.write_text(json.dumps({args.asset_id: records}, sort_keys=True) + '\\n')
""",
                encoding="utf-8",
            )
            result = build_record_set(
                run_dir,
                record_set_id="item",
                script_path=script,
                package_directory=package,
                timeout_seconds=20,
            )
            self.assertEqual(result["item_count"], 2)
            installed_helper = run_dir / "provenance/transformations/build_item/helper.py"
            self.assertTrue(installed_helper.is_file())
            installed_helper.write_text(
                "def normalize(value):\n    return value.upper()\n",
                encoding="utf-8",
            )
            changed = assess_integration(run_dir)
            self.assertEqual(changed["decision"], "fix")
            self.assertIn(
                "transformation_changed",
                {item["code"] for item in changed["blocking_issues"]},
            )
            build_record_set(
                run_dir,
                record_set_id="item",
                script_path=script,
                package_directory=package,
                timeout_seconds=20,
            )
            assessment = assess_integration(run_dir)
            self.assertEqual(assessment["decision"], "ready")
            finalize_integration(run_dir)

            frozen = freeze_environment(run_dir)
            self.assertEqual(frozen["quality_profile"]["quality_tier"], "rich")
            replay_asset = frozen["freeze_manifest"]
            self.assertTrue(replay_asset["files"])
            reproducibility = json.loads(
                (run_dir / "provenance/reproducibility_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                reproducibility["assets"][0]["state_digest"],
                reproducibility["assets"][0]["replay_state_digest"],
            )
            self.assertFalse((run_dir / "workspace").exists())
            self.assertTrue((run_dir / "provenance/raw/items.json").is_file())
            published = validate_and_publish(
                run_dir,
                final_output_dir=output_dir,
                overwrite=False,
            )
            self.assertEqual(published["integration_tier"], "integrated")
            self.assertTrue((output_dir / "state/records.sqlite").is_file())
            self.assertTrue((output_dir / "environment.md").is_file())
            self.assertFalse((output_dir / ".datagen").exists())


if __name__ == "__main__":
    unittest.main()
