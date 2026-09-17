from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from env_gen.data_gen.analysis.artifact_integrity import table_digest
from env_gen.data_gen.config import CollectionPolicy
from env_gen.data_gen.steps.common.constants import (
    COLLECTION_PROFILE_PATH,
    CONTROL_COLLECTION_RESULT,
    CONTROL_RUN_CONFIG,
    INTEGRATION_GUIDE_FILE,
)
from env_gen.data_gen.steps.common.control_io import control_path, read_json
from env_gen.data_gen.steps.integration.direct_commands import (
    assess_environment,
)
from env_gen.data_gen.steps.step2_collect_data import run_data_collection
from env_gen.data_gen.steps.step3_integrate_data import (
    _assessment_after_round,
    build_integration_prompt,
    prepare_integration,
    run_integration_phase,
)
from env_gen.data_gen.steps.step4_freeze_environment import freeze_and_publish_environment
from tests.data_gen_test_helpers import (
    prepare_run,
    prepare_step0,
    sample_python_package_seed,
    write_json,
)


def _environment(summary: str, description: str) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "environment_id": "demo_catalog",
        "name": "Demo catalog",
        "summary": summary,
        "description": description,
        "record_sets": [{
            "record_set_id": "items",
            "name": "Catalog items",
            "description": "One record represents one real catalog item.",
            "access": "read_only",
            "key_fields": ["item_id"],
            "fields": {
                "item_id": {"type": "string", "description": "Stable item identifier.", "nullable": False},
                "name": {"type": "string", "description": "Published item name.", "nullable": False},
                "category": {"type": "string", "description": "Published category.", "nullable": False},
            },
        }],
        "relationships": [],
        "filesystem_scopes": [],
    }


class DirectIntegrationTests(unittest.TestCase):
    def _prepare_collected_run(self, run_dir: Path) -> dict[str, object]:
        seed, _ = prepare_run(run_dir)

        def runner(_prompt: str, _seconds: int, _paths: tuple[Path, ...]) -> str:
            write_json(run_dir / "workspace/raw/items.json", {
                "items": [
                    {"item_id": "item-2", "name": " Second ", "category": "b"},
                    {"item_id": "item-1", "name": "First", "category": "a"},
                ]
            })
            write_json(control_path(run_dir, CONTROL_COLLECTION_RESULT), {
                "schema_version": "1.0",
                "result": "ready",
                "summary": "The complete catalog contains two useful business records.",
                "work_coverage": [{
                    "task_name": "Browse items by category",
                    "status": "supported",
                    "evidence_paths": ["raw/items.json"],
                    "supported_operations": [
                        "List items",
                        "Filter and compare categories",
                    ],
                    "connections": [
                        "item_id identifies records and category groups them"
                    ],
                    "variations": ["The data contains multiple items and categories"],
                    "verification": (
                        "Every returned item must match the requested category."
                    ),
                    "limitations": [],
                }],
                "file_cards": [{
                    "path": "raw/items.json",
                    "source_id": "items",
                    "url": "https://example.test/items.json",
                    "role": "business_records",
                    "name": "Catalog items",
                    "summary": "Two real catalog records with identifiers, names and categories.",
                    "subjects": [
                        {"subject_type": "entity", "subject_name": "Item", "status": "supported", "reason": "Contains item records."},
                        {"subject_type": "tool", "subject_name": "list_items", "status": "supported", "reason": "Contains listable items."},
                        {"subject_type": "task", "subject_name": "Browse items by category", "status": "supported", "reason": "Contains category values."},
                        {"subject_type": "task", "subject_name": seed["init_ref_tasks"][0]["description"], "status": "supported", "reason": "Contains inspectable results."},
                    ],
                    "prepared_paths": [],
                    "limitations": [],
                }],
            })
            return "collected"

        decision, _, _ = run_data_collection(run_dir=run_dir, agent_runner=runner)
        self.assertEqual(decision, "ready")
        prepare_integration(run_dir)
        scenario = json.loads(
            (run_dir / "provenance/scenario_research.json").read_text(encoding="utf-8")
        )
        write_json(
            run_dir / "environment.json",
            _environment(
                scenario["environment"]["summary"],
                scenario["environment"]["description"],
            ),
        )
        payload = read_json(run_dir / "workspace/raw/items.json", "raw items")
        connection = sqlite3.connect(run_dir / "state/records.sqlite")
        connection.execute(
            "CREATE TABLE items (item_id TEXT NOT NULL, name TEXT NOT NULL, "
            "category TEXT NOT NULL) STRICT"
        )
        connection.executemany(
            "INSERT INTO items VALUES (?, ?, ?)",
            sorted(
                (row["item_id"], row["name"].strip(), row["category"])
                for row in payload["items"]
            ),
        )
        connection.commit()
        connection.close()
        return seed

    def _allow_partial(self, run_dir: Path) -> None:
        config_path = control_path(run_dir, CONTROL_RUN_CONFIG)
        config = read_json(config_path, "run config")
        config["allow_partial_integration"] = True
        write_json(config_path, config)
        profile_path = run_dir / COLLECTION_PROFILE_PATH
        profile = read_json(profile_path, "collection profile")
        profile["decision"] = "partial"
        profile["metrics"]["seed"]["overall"]["percent"] = 50.0
        profile["metrics"]["scenario"]["overall"]["percent"] = 50.0
        write_json(profile_path, profile)

    def test_direct_state_assess_and_controller_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            self._prepare_collected_run(run_dir)
            assessment = assess_environment(run_dir)
            self.assertEqual(assessment["decision"], "ready")
            self.assertEqual(assessment["statistics"]["record_count"], 2)
            self.assertTrue(assessment["state_digest"])
            self.assertNotIn("replay_state_digest", assessment)
            self.assertFalse((run_dir / "provenance/build.py").exists())
            controller_assessment = _assessment_after_round(run_dir)
            self.assertEqual(
                controller_assessment["state_digest"],
                assessment["state_digest"],
            )

    def test_phase_accepts_direct_state_without_completion_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            self._prepare_collected_run(run_dir)
            required_paths: list[tuple[Path, ...]] = []

            def runner(
                _prompt: str, _seconds: int, paths: tuple[Path, ...],
            ) -> str:
                required_paths.append(paths)
                return "integrated"

            result = run_integration_phase(
                run_dir=run_dir,
                collection_policy=CollectionPolicy(
                    integration_seconds=30,
                    integration_total_seconds=30,
                    max_integration_rounds=1,
                ),
                agent_runner=runner,
            )

            self.assertEqual(result.assessment["decision"], "ready")
            self.assertEqual(required_paths, [()])
            self.assertFalse(
                (run_dir / ".datagen/integration_finalization.json").exists()
            )

    def test_streaming_table_digest_keeps_existing_digest_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "records.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE samples (item_id INTEGER, label TEXT, optional TEXT)"
            )
            connection.executemany(
                "INSERT INTO samples VALUES (?, ?, ?)",
                [(1, "中文", None), (2, "a,b", "")],
            )
            connection.commit()
            columns = [
                str(row[1])
                for row in connection.execute('PRAGMA table_info("samples")')
            ]
            rows = [
                list(row)
                for row in connection.execute('SELECT * FROM "samples" ORDER BY rowid')
            ]
            connection.close()
            previous_payload = json.dumps(
                {"columns": columns, "rows": rows},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

            self.assertEqual(
                table_digest(database, "samples"),
                hashlib.sha256(previous_payload).hexdigest(),
            )

    def test_step3_owns_prompt_and_requires_full_raw_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            self._prepare_collected_run(run_dir)

            guide = control_path(run_dir, INTEGRATION_GUIDE_FILE).read_text(
                encoding="utf-8"
            )
            prompt = build_integration_prompt(run_dir)

            self.assertIn("对归档查看完整成员清单并打开实际内容", guide)
            self.assertIn("不得只取前几条", guide)
            self.assertIn("程序通过只证明结构和引用正确", guide)
            self.assertIn("两种落地方式", guide)
            self.assertIn("不是默认的业务对象", guide)
            self.assertIn("不要自行发明", guide)
            self.assertIn("保持不同项目、批次、实验或设计的原始边界", guide)
            self.assertIn("先实际检查每个原件", prompt)
            self.assertNotIn("Seed", guide)
            self.assertNotIn("Step 1", guide)
            self.assertNotIn("Step 2", guide)
            self.assertNotIn("集成计划", guide)
            self.assertNotIn("integration_plan", prompt)
            self.assertNotIn("build.py", guide)
            self.assertNotIn("finalize", guide)
            self.assertNotIn("finalize", prompt)

    def test_python_package_integration_uses_generic_integration_guide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            prepare_step0(run_dir, selected_seed=sample_python_package_seed())
            prepare_integration(run_dir)
            guide = control_path(run_dir, INTEGRATION_GUIDE_FILE).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Python 工具包数据约束", guide)
            self.assertNotIn("demo-package", guide)
            self.assertIn("围绕实际业务对象设计最终表和文件工作区", guide)
            self.assertIn("可以自由选择命令、已安装工具或临时脚本", guide)

    def test_step4_publishes_without_plan_or_step5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            output = root / "published"
            self._prepare_collected_run(run_dir)

            result = freeze_and_publish_environment(
                run_dir, final_output_dir=output, overwrite=False,
            )

            self.assertEqual(result["integration_tier"], "integrated")
            self.assertFalse((output / "provenance/build.py").exists())
            self.assertTrue((output / "provenance/integration_receipt.json").is_file())
            self.assertTrue((output / "provenance/raw/items.json").is_file())
            self.assertTrue((output / "state/records.sqlite").is_file())
            self.assertFalse((output / "provenance/integration_plan.json").exists())
            self.assertFalse(any((output / "provenance").rglob("*.pyc")))
            self.assertFalse((output / ".datagen").exists())
            receipt = read_json(
                output / "provenance/integration_receipt.json", "integration receipt"
            )
            self.assertEqual(receipt["integration_method"], "agent_direct_final_state")
            self.assertNotIn("build", receipt)
            self.assertNotIn("replay_state_digest", receipt)

    def test_explicit_partial_mode_integrates_and_preserves_partial_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            output = root / "published"
            self._prepare_collected_run(run_dir)
            self._allow_partial(run_dir)
            assessment = assess_environment(run_dir)
            self.assertEqual(assessment["decision"], "ready")

            result = freeze_and_publish_environment(
                run_dir, final_output_dir=output, overwrite=False,
            )

            self.assertEqual(result["quality_tier"], "partial")
            validation = read_json(output / "validation.json", "validation")
            self.assertTrue(validation["valid"])
            self.assertEqual(validation["quality_tier"], "partial")


if __name__ == "__main__":
    unittest.main()
