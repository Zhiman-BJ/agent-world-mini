from __future__ import annotations

import csv
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from env_gen.data_gen.analysis.seed import load_selected_seed
from env_gen.data_gen.steps.step3_integrate_data import _integration_guide
from env_gen.data_gen.steps.integration.direct_commands import _writable_asset_issues
from seed_gen.scripts.build_researched_scenario_seeds import (
    DEFAULT_ANCHORS,
    DEFAULT_CSV,
    DEFAULT_SCHEMA,
    build,
    validate,
)
from tests.data_gen_test_helpers import ROOT


class ResearchedScenarioSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seeds = build(DEFAULT_CSV, DEFAULT_ANCHORS)

    def test_every_csv_scenario_is_preserved_as_one_seed(self) -> None:
        with DEFAULT_CSV.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(self.seeds), len(rows))
        self.assertEqual(len({seed["global_id"] for seed in self.seeds}), len(rows))
        for source_row, (row, seed) in enumerate(zip(rows, self.seeds, strict=True), 1):
            design = seed["others"]["scenario_design"]
            self.assertEqual(seed["environment"]["basic_info"]["index"], source_row)
            self.assertEqual(design["source_row"], source_row)
            self.assertEqual(design["application_scenario"], row["应用场景"].strip())
            self.assertEqual(
                design["package_or_combination_raw"], row["包 / 包组合"].strip()
            )
            self.assertEqual(design["composable_task_raw"], row["可合成任务"].strip())

    def test_accepted_only_uses_csv_marker(self) -> None:
        accepted = build(DEFAULT_CSV, DEFAULT_ANCHORS, accepted_only=True)
        self.assertEqual(len(accepted), 480)
        self.assertTrue(all(
            seed["others"]["scenario_design"]["review"]["status"] == "accepted"
            for seed in accepted
        ))

    def test_collection_validates_and_keeps_review_decisions(self) -> None:
        validate(self.seeds, DEFAULT_SCHEMA)
        statuses = Counter(
            seed["others"]["scenario_design"]["review"]["status"]
            for seed in self.seeds
        )
        self.assertEqual(
            statuses,
            {"accepted": 480, "needs_revision": 34, "rejected": 10},
        )

    def test_compaction_preserves_every_anchor_package(self) -> None:
        anchors = {
            seed["global_id"]: seed
            for seed in json.loads(DEFAULT_ANCHORS.read_text(encoding="utf-8"))
        }
        for seed in self.seeds:
            anchor_id = seed["others"]["scenario_design"]["anchor_global_id"]
            self.assertTrue(
                set(anchors[anchor_id]["others"]["pypi_package"]).issubset(
                    seed["others"]["pypi_package"]
                )
            )
            self.assertGreaterEqual(len(seed["init_ref_tools"]), 1)
            self.assertLessEqual(
                max(
                    (
                        len(tool.get("function", []))
                        for tool in seed["init_ref_tools"]
                        if tool["type"] == "class"
                    ),
                    default=0,
                ),
                6,
            )

    def test_explicit_verified_packages_are_added_across_l3_anchors(self) -> None:
        by_id = {seed["global_id"]: seed for seed in self.seeds}
        recovery = by_id["semiconductor_scenario_01_03_02_0019"]
        self.assertIn("atomate2", recovery["others"]["pypi_package"])
        self.assertIn("jobflow", recovery["others"]["pypi_package"])
        fermi = by_id["semiconductor_scenario_01_04_04_0037"]
        self.assertIn("doped", fermi["others"]["pypi_package"])

    def test_every_seed_records_tool_support_and_registered_package_sources(self) -> None:
        allowed = {
            "direct_verified_with_external_dependencies",
            "direct_verified_package_reference",
            "partial_support_external_boundary",
            "l3_fallback_needs_tool_research",
        }
        for seed in self.seeds:
            support = seed["others"]["scenario_design"]["tool_support"]
            self.assertIn(support["status"], allowed)
            self.assertEqual(
                support["selected_verified_packages"],
                seed["others"]["pypi_package"],
            )
            urls = {
                source["url"]
                for source in seed["others"]["research_sources"]
                if source.get("url")
            }
            self.assertTrue(set(seed["environment"]["basic_info"]["url"]).issubset(urls))

    def test_datagen_loads_row_level_and_workgroup_ids(self) -> None:
        selected = [self.seeds[0], self.seeds[67]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.json"
            path.write_text(
                json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for seed in selected:
                loaded, _ = load_selected_seed(
                    path,
                    seed["global_id"],
                    ROOT / "schemas/validation/env_seeds.schema.json",
                )
                self.assertEqual(loaded, seed)

    def test_future_business_assets_are_copy_on_write(self) -> None:
        guide = _integration_guide("environment.schema.json", None)
        self.assertIn("最终 Record Set\n  统一声明为 `copy_on_write`", guide)
        self.assertIn("最终 Scope\n  同样统一声明为 `copy_on_write`", guide)
        self.assertIn("不允许修改采集原件或共享基线", guide)
        self.assertEqual(
            _writable_asset_issues({
                "record_sets": [{
                    "record_set_id": "measurements",
                    "access": "copy_on_write",
                }],
                "filesystem_scopes": [{
                    "scope_id": "projects",
                    "access": "copy_on_write",
                }],
            }),
            [],
        )
        issues = _writable_asset_issues({
            "record_sets": [{"record_set_id": "evidence", "access": "read_only"}],
            "filesystem_scopes": [],
        })
        self.assertEqual(issues[0]["code"], "business_asset_not_writable")


if __name__ == "__main__":
    unittest.main()
