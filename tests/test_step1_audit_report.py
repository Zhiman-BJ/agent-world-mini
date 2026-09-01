from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_step1_audit_report import (
    _document,
    _suggests_synthetic_business_records,
    build_payload,
)


class Step1AuditReportTests(unittest.TestCase):
    def test_synthetic_language_check_distinguishes_suggestion_from_prohibition(self) -> None:
        self.assertTrue(
            _suggests_synthetic_business_records(
                "Use synthetic business records to fill the missing account history."
            )
        )
        self.assertTrue(
            _suggests_synthetic_business_records("可以生成模拟记录来补充数据。")
        )
        self.assertFalse(
            _suggests_synthetic_business_records(
                "Do not substitute synthetic KYC or operational histories."
            )
        )
        self.assertFalse(
            _suggests_synthetic_business_records("禁止使用合成记录替代真实业务事实。")
        )

    def test_report_is_self_contained_and_embeds_only_final_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "environment"
            provenance = root / "provenance"
            provenance.mkdir(parents=True)
            scenario = {
                "seed_global_id": "demo_environment",
                "scenario_outline": {
                    "name": "Demo",
                    "overview": "A demo scenario.",
                    "key_workflows": ["Inspect records"],
                },
                "entity_candidates": [{"entity_id": "record", "name": "Record"}],
                "relationship_candidates": [],
                "operation_candidates": [{"operation_id": "inspect", "name": "Inspect"}],
                "task_candidates": [{"task_id": "inspect_record", "description": "Inspect one record"}],
                "data_needs": [{"need_id": "records", "description": "Records"}],
                "data_shape_hypothesis": {
                    "likely_mode": "structured_records",
                    "rationale": "Only records are needed.",
                },
                "source_leads": [{
                    "source_lead_id": "official_api",
                    "name": "Official API",
                    "entry_url": "https://example.test/api",
                    "publisher": "Example",
                    "source_kind": "official_api",
                    "authority_notes": "First party.",
                    "need_ids": ["records"],
                    "investigation_notes": "Verify in Step 2.",
                }],
                "seed_synthesis": [{
                    "theme_id": "records_theme",
                    "summary": "The Seed describes record inspection.",
                    "seed_paths": ["$.environment.description"],
                }],
                "open_questions": [],
                "risks": [],
            }
            (provenance / "scenario_research.json").write_text(
                json.dumps(scenario), encoding="utf-8"
            )
            (provenance / "source_plan.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {"source_id": "official_api", "status": "complete"}
                        ],
                        "research_refinements": [
                            {
                                "refinement_id": "records_confirmed",
                                "finding_type": "entity",
                                "status": "confirmed",
                                "description": "The public response contains records.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (provenance / "data_checkpoint.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "raw_files": ["raw/records.json"],
                        "entity_files": ["entities/records.json"],
                        "derived_files": [],
                    }
                ),
                encoding="utf-8",
            )
            (provenance / "quality_profile.json").write_text(
                json.dumps({"quality_tier": "rich", "quality_gaps": []}),
                encoding="utf-8",
            )
            (provenance / "data_profile.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "entity_type_count": 1,
                            "entity_record_count": 1,
                            "file_count": 2,
                            "file_bytes": 32,
                        },
                        "entities": {
                            "record": {
                                "record_count": 1,
                                "field_count": 2,
                                "primary_key_candidates": ["id"],
                                "fields": {
                                    "id": {
                                        "type": "integer",
                                        "non_null_ratio": 1,
                                        "distinct_count": 1,
                                        "roles": ["identifier"],
                                    },
                                    "file_path": {
                                        "type": "string",
                                        "non_null_ratio": 1,
                                        "distinct_count": 1,
                                        "roles": ["file_reference"],
                                        "file_formats": ["json"],
                                    },
                                },
                            }
                        },
                        "files": [
                            {
                                "path": "raw/records.json",
                                "bucket": "raw",
                                "format": "json",
                                "bytes": 11,
                                "record_count": 1,
                            },
                            {
                                "path": "entities/records.json",
                                "bucket": "entity",
                                "format": "json",
                                "bytes": 21,
                                "record_count": 1,
                            },
                        ],
                        "relation_candidates": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "environment.json").write_text(
                json.dumps(
                    {
                        "environment_id": "demo_environment",
                        "name": "Final Demo Environment",
                        "description": "The final published environment.",
                        "resources": [
                            {
                                "resource_id": "raw_records",
                                "name": "Raw records",
                                "description": "Original public response.",
                                "data_type": "raw",
                                "storage_type": "file",
                                "path": "raw/records.json",
                                "format": "json",
                                "writable": False,
                            },
                            {
                                "resource_id": "records",
                                "name": "Records",
                                "description": "Canonical records.",
                                "data_type": "entity",
                                "storage_type": "file",
                                "path": "entities/records.json",
                                "format": "json",
                                "writable": False,
                                "source_resources": ["raw_records"],
                                "entity_schema": {
                                    "record": {
                                        "description": "One final business record.",
                                        "fields": {
                                            "id": {
                                                "type": "integer",
                                                "description": "Stable identifier.",
                                            },
                                            "file_path": {
                                                "type": "string",
                                                "description": "Indexed source file.",
                                            },
                                        },
                                    }
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raw = root / "workspace/raw/records.json"
            entity = root / "workspace/entities/records.json"
            raw.parent.mkdir(parents=True)
            entity.parent.mkdir(parents=True)
            raw.write_text('[{"id": 1}]', encoding="utf-8")
            entity.write_text('{"record": [{"id": 1}]}', encoding="utf-8")
            (provenance / "sources.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "source_id": "official_api",
                                "url": "https://example.test/api",
                                "files": [
                                    {
                                        "path": "raw/records.json",
                                        "sha256": hashlib.sha256(
                                            raw.read_bytes()
                                        ).hexdigest(),
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "validation.json").write_text(
                json.dumps({"valid": True, "errors": []}), encoding="utf-8"
            )

            payload = build_payload([root])
            document = _document(payload)
            self.assertEqual(payload["environment_count"], 1)
            self.assertEqual(
                payload["environments"][0]["counts"]["derived_files"], 0
            )
            self.assertEqual(
                payload["environments"][0]["counts"]["entity_records"], 1
            )
            self.assertEqual(
                payload["environments"][0]["artifacts"]["entities"][0][
                    "entity_type"
                ],
                "record",
            )
            self.assertEqual(
                payload["environments"][0]["artifacts"]["file_indexes"][0][
                    "field"
                ],
                "file_path",
            )
            self.assertNotIn("entity_candidates", payload["environments"][0])
            self.assertNotIn("seed_synthesis", payload["environments"][0])
            self.assertNotIn("source_leads", payload["environments"][0])
            self.assertIn("demo_environment", document)
            self.assertIn("最终实体 Schema、分布与复核", document)
            self.assertIn("文件与索引", document)
            self.assertIn('id="audit-data"', document)
            self.assertNotIn("<script src=", document)
            self.assertNotIn("<link rel=", document)
            self.assertNotIn("fetch(", document)
            self.assertEqual(payload["environments"][0]["audit_issues"], [])

    def test_v2_report_reads_sqlite_scopes_relationships_and_file_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v2_environment"
            provenance = root / "provenance"
            state = root / "state"
            scope_root = state / "filesystem_scopes/documents"
            provenance.mkdir(parents=True)
            scope_root.mkdir(parents=True)
            (scope_root / "guide.txt").write_text("public guide\n", encoding="utf-8")
            database = state / "records.sqlite"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE folders (folder_id TEXT NOT NULL, name TEXT NOT NULL)")
                connection.execute("CREATE TABLE documents (document_id TEXT NOT NULL, folder_id TEXT NOT NULL, file_path TEXT NOT NULL)")
                connection.execute("INSERT INTO folders VALUES ('f1', 'Public')")
                connection.execute("INSERT INTO documents VALUES ('d1', 'f1', 'guide.txt')")
            string_field = lambda description, **extra: {
                "type": "string", "description": description, "nullable": False, **extra,
            }
            record_sets = [
                {
                    "record_set_id": "folders", "name": "Folders",
                    "description": "Logical document folders.", "key_fields": ["folder_id"],
                    "fields": {
                        "folder_id": string_field("Folder ID."),
                        "name": string_field("Folder name."),
                    }, "access": "read_only", "importance": "core",
                    "source_ids": ["official"], "source_paths": ["raw/catalog.json"],
                },
                {
                    "record_set_id": "documents", "name": "Documents",
                    "description": "Documents linked to files.", "key_fields": ["document_id"],
                    "fields": {
                        "document_id": string_field("Document ID."),
                        "folder_id": string_field("Folder ID."),
                        "file_path": string_field("Scope path.", reference={
                            "kind": "filesystem_path", "scope_id": "documents", "target": "file",
                        }),
                    }, "access": "read_only", "importance": "core",
                    "source_ids": ["official"], "source_paths": ["raw/catalog.json"],
                },
            ]
            relationships = [{
                "relationship_id": "document_folder", "description": "Document folder.",
                "from": {"record_set_id": "documents", "fields": ["folder_id"]},
                "to": {"record_set_id": "folders", "fields": ["folder_id"]},
                "cardinality": "many_to_one",
            }]
            scopes = [{
                "scope_id": "documents", "name": "Document files",
                "description": "Task-facing text files.", "access": "copy_on_write",
                "importance": "core", "source_ids": ["official"],
                "source_paths": ["raw/guide.txt"],
                "structure": {"kind": "file_collection", "path": "*.txt", "format": "text"},
            }]
            environment = {
                "schema_version": "2.0", "environment_id": "v2_demo",
                "name": "V2 Demo", "description": "A final v2 data environment.",
                "record_sets": record_sets, "relationships": relationships,
                "filesystem_scopes": scopes,
            }
            plan = {
                **environment, "seed_global_id": "v2_demo", "seed_sha256": "0" * 64,
                "need_bindings": [{
                    "need_id": "documents", "status": "realized",
                    "record_set_ids": ["folders", "documents"],
                    "scope_ids": ["documents"], "description": "Document navigation.",
                }],
                "source_decisions": [{
                    "source_id": "official", "decision": "core", "reason": "Primary source.",
                }],
            }
            for name, payload in {
                "environment.json": environment,
                "provenance/integration_plan.json": plan,
                "provenance/scenario_research.json": {
                    "seed_global_id": "v2_demo", "seed_sha256": "0" * 64,
                    "data_needs": [{"need_id": "documents", "priority": "core"}],
                },
                "provenance/source_plan.json": {
                    "required_file_formats": ["text"],
                    "sources": [{"source_id": "official", "status": "complete", "raw_files": []}],
                },
                "provenance/source_inventory.json": {"files": [], "sources": [{
                    "source_id": "official", "profile_status": "usable",
                }]},
                "provenance/integration_profile.json": {
                    "integration_tier": "integrated", "integration_gaps": [],
                    "relationship_profile": {"relationships": [{
                        "relationship_id": "document_folder", "source_non_null_count": 1,
                        "valid": True,
                    }]},
                    "file_reference_profile": {"references": [{
                        "record_set_id": "documents", "field": "file_path",
                        "checked_path_count": 1, "valid": True,
                    }]},
                },
                "provenance/quality_profile.json": {
                    "quality_tier": "rich", "quality_gaps": [], "shape": "hybrid",
                    "summary": "Rich and integrated.",
                    "policy": {
                        "min_total_records": 0,
                        "min_records_per_substantial_record_set": 0,
                        "min_core_records": 0,
                        "min_core_business_fields": 0,
                        "min_core_field_non_null_percent": 0,
                        "min_collection_members": 0,
                        "min_need_coverage_percent": 0,
                        "min_realized_need_count": 0
                    },
                    "file_profile": {"available_formats": ["text"]},
                },
                "validation.json": {"valid": True, "errors": []},
            }.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")

            payload = build_payload([root])
            row = payload["environments"][0]
            document = _document(payload)
            self.assertEqual(row["counts"]["entity_types"], 2)
            self.assertEqual(row["counts"]["entity_records"], 2)
            self.assertEqual(row["counts"]["business_files"], 1)
            self.assertEqual(row["artifacts"]["relations"][0]["edge_count"], 1)
            self.assertEqual(row["artifacts"]["file_indexes"][0]["indexed_path_count"], 1)
            self.assertEqual(
                {item["storage_type"] for item in row["artifacts"]["resources"]},
                {"sqlite_table", "directory"},
            )
            issue_codes = {item["code"] for item in row["audit_issues"]}
            self.assertNotIn("checkpoint_missing", issue_codes)
            self.assertNotIn("no_entity_files", issue_codes)
            self.assertIn("V2 Demo", document)


if __name__ == "__main__":
    unittest.main()
