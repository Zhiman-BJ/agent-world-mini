from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from env_gen.data_gen.analysis.entity_profiling import (
    profile_entity_groups,
    profile_workspace_files,
)
from env_gen.data_gen.analysis.operation_candidates import build_operation_candidates
from env_gen.data_gen.analysis.structured_io import read_entity_groups
from env_gen.data_gen.steps.collection.commands.add_workspace_data import (
    add_derived_file,
    add_entity_file,
    data_file_receipt_issues,
)
from env_gen.data_gen.steps.collection.commands.assess_workspace import assess_workspace
from env_gen.data_gen.steps.collection.commands.save_source_plan import (
    save_source_plan_payload,
)
from env_gen.data_gen.steps.common.control_io import write_json

from tests.data_gen_test_helpers import prepare_run, source_plan_payload


ROWS = [
    {"item_id": "i1", "name": "Alpha", "category": "a"},
    {"item_id": "i2", "name": "Beta", "category": "b"},
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_raw(run_dir: Path, *, relative: str = "raw/items.json") -> Path:
    path = run_dir / "workspace" / relative
    write_json(path, {"items": ROWS})
    url = "https://example.test/items.json"
    write_json(
        run_dir / ".datagen/download_receipts.json",
        {
            "schema_version": "1.0",
            "downloads": [
                {
                    "url": url,
                    "effective_url": url,
                    "source_id": "items",
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "retrieved_at": "2026-08-30T00:00:00+00:00",
                    "reused_existing_file": False,
                }
            ],
        },
    )
    return path


class StructuredEntityTests(unittest.TestCase):
    def test_json_jsonl_and_csv_are_canonical_entity_formats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "item.json"
            write_json(json_path, {"item": ROWS})
            self.assertEqual(len(read_entity_groups(json_path)["item"]), 2)

            jsonl_path = root / "item.jsonl"
            jsonl_path.write_text(
                "\n".join(json.dumps(row) for row in ROWS) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                len(read_entity_groups(jsonl_path, entity_name="item")["item"]),
                2,
            )

            csv_path = root / "item.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(ROWS[0]))
                writer.writeheader()
                writer.writerows(ROWS)
            self.assertEqual(
                len(read_entity_groups(csv_path, entity_name="item")["item"]),
                2,
            )

    def test_add_entity_records_direct_raw_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            prepare_run(run_dir)
            install_raw(run_dir)
            draft = Path(directory) / "item.json"
            write_json(draft, {"item": ROWS})
            result = add_entity_file(
                run_dir,
                input_path=draft,
                output="entities/item.json",
                source_files=["raw/items.json"],
            )
            self.assertEqual(result["entity_counts"], {"item": 2})
            self.assertEqual(data_file_receipt_issues(run_dir), [])

    def test_csv_entity_requires_explicit_entity_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            install_raw(run_dir)
            draft = run_dir / "item.csv"
            draft.write_text(
                "item_id,name,category\ni1,Alpha,a\ni2,Beta,b\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "--entity-name"):
                add_entity_file(
                    run_dir,
                    input_path=draft,
                    output="entities/item.csv",
                    source_files=["raw/items.json"],
                )
            result = add_entity_file(
                run_dir,
                input_path=draft,
                output="entities/item.csv",
                source_files=["raw/items.json"],
                entity_name="item",
            )
            self.assertEqual(result["format"], "csv")

    def test_derived_requires_explicit_derivation_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            install_raw(run_dir)
            draft = run_dir / "summary.json"
            write_json(draft, {"count": 2})
            result = add_derived_file(
                run_dir,
                input_path=draft,
                output="derived/summary.json",
                source_files=["raw/items.json"],
                derivation_type="aggregate",
            )
            self.assertEqual(result["derivation_type"], "aggregate")
            self.assertEqual(data_file_receipt_issues(run_dir), [])

    def test_modified_entity_source_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            raw = install_raw(run_dir)
            draft = run_dir / "item.json"
            write_json(draft, {"item": ROWS})
            add_entity_file(
                run_dir,
                input_path=draft,
                output="entities/item.json",
                source_files=["raw/items.json"],
            )
            write_json(raw, {"items": [ROWS[0]]})
            codes = {item["code"] for item in data_file_receipt_issues(run_dir)}
            self.assertIn("data_file_source_modified", codes)


class FileAndAssessmentTests(unittest.TestCase):
    def test_file_index_produces_resolve_and_edit_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            layout = workspace / "raw/layouts/chip.gds"
            layout.parent.mkdir(parents=True)
            layout.write_bytes(b"\x00\x06\x00\x02\x00\x07")
            entities = profile_entity_groups(
                {
                    "layout": [
                        {
                            "layout_id": "l1",
                            "file_path": "raw/layouts/chip.gds",
                            "revision": "r1",
                        }
                    ]
                }
            )
            files = profile_workspace_files(
                workspace,
                {"raw_files": ["raw/layouts/chip.gds"], "entity_files": [], "derived_files": []},
            )
            candidates = build_operation_candidates(entities, files, [])
            families = {item["operation_family"] for item in candidates}
            self.assertIn("resolve_file", families)
            self.assertIn("edit", families)

    def test_assessment_recomputes_ready_from_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            seed, digest = prepare_run(run_dir)
            install_raw(run_dir)
            draft = Path(directory) / "item.json"
            write_json(draft, {"item": ROWS})
            add_entity_file(
                run_dir,
                input_path=draft,
                output="entities/item.json",
                source_files=["raw/items.json"],
            )
            save_source_plan_payload(
                run_dir,
                source_plan_payload(
                    seed,
                    digest,
                    status="complete",
                    record_count=2,
                    raw_files=["raw/items.json"],
                ),
            )
            assessment = assess_workspace(run_dir)
            self.assertEqual(assessment["decision"], "ready")
            quality = json.loads(
                (run_dir / "provenance/quality_profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(quality["quality_tier"], "rich")
            self.assertEqual(quality["data_need_profile"]["supported_count"], 1)

    def test_entity_file_reference_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir)
            install_raw(run_dir)
            draft = run_dir / "item.json"
            write_json(
                draft,
                {
                    "item": [
                        {"item_id": "i1", "name": "Alpha", "file_path": "raw/missing.xml"}
                    ]
                },
            )
            add_entity_file(
                run_dir,
                input_path=draft,
                output="entities/item.json",
                source_files=["raw/items.json"],
            )
            codes = {item["code"] for item in data_file_receipt_issues(run_dir)}
            self.assertIn("missing_entity_file_reference", codes)


if __name__ == "__main__":
    unittest.main()
