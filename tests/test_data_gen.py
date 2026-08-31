from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from env_gen.data_gen import (
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
    DataGenerator,
    DataGenerationError,
    EnvironmentPackageValidator,
    InsufficientPublicDataError,
    ResearchPolicy,
    RichnessPolicy,
)
from env_gen.data_gen.analysis.capability_extraction import extract_capability_atoms
from env_gen.data_gen.analysis.task_space_estimation import build_composition_profile
from env_gen.data_gen.analysis.entity_profiling import profile_entity_groups
from env_gen.data_gen.validator import ValidationReport
from env_gen.data_gen.analysis.record_extraction import (
    build_metadata,
    _canonical_entity_type,
    _field_name_matches,
    _find_identifier_alias,
    _infer_relations,
    _infer_relation_gaps,
    _is_bridge_entity,
    _is_numeric_measure_field,
    _is_relation_key_field,
    _is_text_field,
    _merge_group_records,
    _normalize_groups,
    _without_generated_entity_ids,
    _select_entity_type,
    _related_fact_measure,
    deterministic_entity_groups,
)
from env_gen.data_gen.policy import (
    compile_research_request,
    operation_target_tokens,
    semantic_tokens,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_SCHEMA = PROJECT_ROOT / "schemas" / "environment.schema.json"


def write_valid_package(root: Path) -> None:
    (root / "workspace" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "entities").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "derived").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "reports").mkdir(parents=True, exist_ok=True)
    (root / "provenance").mkdir(parents=True, exist_ok=True)

    (root / "workspace" / "raw" / "items.json").write_text(
        json.dumps([{"id": "a", "name": "Alpha", "score": 1.5, "active": True}]),
        encoding="utf-8",
    )
    (root / "workspace" / "entities" / "items.json").write_text(
        json.dumps(
            {
                "item": [
                    {
                        "item_id": "a",
                        "name": "Alpha",
                        "score": 1.5,
                        "active": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "workspace" / "derived" / "summary.json").write_text(
        json.dumps({"count": 1}), encoding="utf-8"
    )
    environment = {
        "schema_version": "1.0",
        "environment_id": "test_environment",
        "name": "测试环境",
        "description": "用于验证新 DataGen 环境包边界。",
        "resources": [
            {
                "resource_id": "raw_items",
                "name": "原始记录",
                "description": "从公开接口取得的原始记录。",
                "data_type": "raw",
                "storage_type": "file",
                "path": "raw/items.json",
                "format": "json",
                "writable": False,
            },
            {
                "resource_id": "items",
                "name": "规范化记录",
                "description": "由原始记录规范化得到的业务实体。",
                "data_type": "entity",
                "storage_type": "file",
                "path": "entities/items.json",
                "format": "json",
                "writable": False,
                "source_resources": ["raw_items"],
                "entity_schema": {
                    "item": {
                        "description": "一条真实业务记录。",
                        "fields": {
                            "item_id": {"type": "string", "description": "记录的稳定标识。"},
                            "name": {"type": "string", "description": "记录名称。"},
                            "score": {"type": "number", "description": "记录的业务评分。"},
                            "active": {"type": "boolean", "description": "记录当前是否有效。"},
                        },
                    }
                },
            },
            {
                "resource_id": "item_summary",
                "name": "记录统计",
                "description": "根据规范化记录得到的数量统计。",
                "data_type": "derived",
                "storage_type": "file",
                "path": "derived/summary.json",
                "format": "json",
                "writable": False,
                "source_resources": ["items"],
            },
            {
                "resource_id": "reports",
                "name": "报告目录",
                "description": "任务运行期间保存分析报告。",
                "data_type": "output",
                "storage_type": "directory",
                "path": "reports",
                "format": "directory",
                "writable": True,
            },
        ],
        "rules": [
            {
                "description": "item.item_id 在 items 中必须唯一。",
                "resources": ["items"],
            }
        ],
    }
    (root / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    provenance = {
        "schema_version": "1.0",
        "sources": [
            {
                "source_id": "official_items_api",
                "url": "https://example.org/api/items",
                "source_type": "official_api",
                "retrieved_at": "2026-08-25T10:00:00+08:00",
                "license_or_access_note": "公开测试接口，仅用于单元测试。",
                "resource_ids": ["raw_items"],
                "files": [{"path": "raw/items.json"}],
            }
        ],
    }
    (root / "provenance" / "sources.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_research_report(root: Path, *, status: str = "ready") -> None:
    request = json.loads(
        (root / "provenance" / "research_request.json").read_text(encoding="utf-8")
    )
    coverage = [
        {
            "requirement_id": item["requirement_id"],
            "status": "covered" if status == "ready" else "unavailable",
            **(
                {"operation_family": "inspect"}
                if item["kind"] == "seed_operation"
                else {}
            ),
            "evidence": (
                [
                    {
                        "resource_id": "raw_items",
                        "entity_types": [],
                        "field_refs": [],
                        "note": "The official test response directly supports this requirement.",
                    }
                ]
                if status == "ready"
                else []
            ),
            "explanation": "Covered by the official test response."
            if status == "ready"
            else "The test source deliberately exposes no usable public data.",
        }
        for item in request["requirements"]
    ]
    payload = {
        "schema_version": "1.0",
        "request_sha256": request["request_sha256"],
        "status": status,
        "representation_mode": "file_native",
        "summary": "A bounded file-native unit-test environment."
        if status == "ready"
        else "No suitable public data is available for the core request.",
        "coverage": coverage,
        "extensions": [],
        "relations": [],
        "relation_gaps": [],
        "dimensions": [],
        "gaps": (
            []
            if status == "ready"
            else [
                {
                    "requirement_id": request["requirements"][0]["requirement_id"],
                    "category": "not_public",
                    "reason": "The public endpoint has no matching records.",
                    "attempted_urls": ["https://example.org/api/items"],
                    "impact": "The core environment cannot be grounded without fabrication.",
                }
            ]
        ),
        "data_policy": {
            "business_records": "real_public_only",
            "synthetic_business_record_count": 0,
            "deterministic_transformations_only": True,
        },
    }
    (root / "provenance" / "research_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_research_plan(root: Path, *, decision: str = "proceed") -> None:
    request = json.loads(
        (root / "provenance" / "research_request.json").read_text(encoding="utf-8")
    )
    requirement_ids = [item["requirement_id"] for item in request["requirements"]]
    payload = {
        "schema_version": "1.0",
        "request_sha256": request["request_sha256"],
        "decision": decision,
        "summary": "A bounded unit-test source plan."
        if decision == "proceed"
        else "The core public data is unavailable.",
        "sources": (
            [
                {
                    "source_id": "official_items_api",
                    "url": "https://example.org/api/items",
                    "source_type": "official_api",
                    "resource_id": "raw_items",
                    "purpose": "Supports the test environment requirements.",
                    "requirement_ids": requirement_ids,
                }
            ]
            if decision == "proceed"
            else []
        ),
        "coverage": [
            {
                "requirement_id": requirement_id,
                "source_ids": ["official_items_api"],
                "target_resource_kind": "file_native",
                "target_entity_types": [],
                "rationale": "The official test response is the bounded source.",
            }
            for requirement_id in requirement_ids
        ]
        if decision == "proceed"
        else [],
        "gaps": (
            []
            if decision == "proceed"
            else [
                {
                    "requirement_id": requirement_ids[0],
                    "category": "not_public",
                    "reason": "No matching public record exists.",
                    "attempted_urls": ["https://example.org/api/items"],
                }
            ]
        ),
    }
    (root / "provenance" / "research_plan.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


TEST_POLICY = ResearchPolicy(
    max_total_seconds=30,
    max_sources=4,
    max_raw_files=4,
    max_download_bytes=1024 * 1024,
    min_total_entity_records=0,
    min_core_entity_records=0,
    min_operation_records=0,
    min_operation_distinct_values=0,
    min_extension_entity_types=0,
    min_extension_capabilities=0,
    min_extension_operation_families=0,
    min_relations=0,
    min_dimension_kinds=0,
)


def write_data_checkpoint(root: Path, *, status: str = "ready") -> None:
    request = json.loads(
        (root / "provenance" / "research_request.json").read_text(encoding="utf-8")
    )
    workspace = root / "workspace"
    payload = {
        "schema_version": "1.0",
        "request_sha256": request["request_sha256"],
        "status": status,
        "summary": "A bounded unit-test data checkpoint.",
        "raw_files": ["raw/items.json"] if status == "ready" else [],
        "entity_files": ["entities/items.json"] if status == "ready" else [],
        "derived_files": ["derived/summary.json"] if status == "ready" else [],
        "source_urls": ["https://example.org/api/items"],
        "source_file_map": [
            {
                "url": "https://example.org/api/items",
                "file_paths": ["raw/items.json"],
            }
        ] if status == "ready" else [],
        "synthetic_business_record_count": 0,
    }
    (root / "provenance").mkdir(parents=True, exist_ok=True)
    (root / "provenance" / "data_checkpoint.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    source_inventory = {
        "schema_version": "1.0",
        "request_sha256": request["request_sha256"],
        "summary": "A fully enumerated unit-test source surface.",
        "surfaces": [
            {
                "surface_id": "items_api",
                "name": "Test items API",
                "url": "https://example.org/api/items",
                "priority": "core",
                "kind": "paginated_api",
                "entities": ["item"],
                "pagination": {
                    "type": "none",
                    "page_size": None,
                    "pages_collected": 1 if status == "ready" else 0,
                    "reported_total": 1 if status == "ready" else None,
                },
                "collection_mode": "exhaustive" if status == "ready" else "unavailable",
                "collection_status": "complete" if status == "ready" else "blocked",
                "records_collected": 1 if status == "ready" else 0,
                "raw_files": ["raw/items.json"] if status == "ready" else [],
                "related_surface_ids": [],
                "exhaustion_evidence": {
                    "type": "reported_total_reached" if status == "ready" else "source_unavailable",
                    "detail": "The fixture contains its complete one-record universe."
                    if status == "ready"
                    else "The fixture deliberately exposes no records.",
                },
            }
        ],
    }
    (root / "provenance" / "source_inventory.json").write_text(
        json.dumps(source_inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class FakeResearchAgent:
    def __init__(self, *, invalid_description: bool = False) -> None:
        self.prompts: list[str] = []
        self.invalid_description = invalid_description

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.prompts.append(prompt)
        if "真实数据采集 Agent" in prompt:
            (working_directory / "provenance").mkdir(parents=True, exist_ok=True)
            write_valid_package(working_directory)
            write_data_checkpoint(working_directory)
            # 第一阶段只提交数据，清掉第二阶段才应出现的清单。
            (working_directory / "environment.json").unlink(missing_ok=True)
            (working_directory / "provenance" / "sources.json").unlink(missing_ok=True)
            (working_directory / "provenance" / "research_report.json").unlink(missing_ok=True)
        elif "环境语义描述 Agent" in prompt:
            write_valid_package(working_directory)
            write_research_report(working_directory)
            if self.invalid_description:
                # 故意制造一个机械错误，验证 Pipeline 会进入修复轮。
                (working_directory / "provenance" / "sources.json").unlink()
            (working_directory / "provenance" / "agent_done.json").write_text(
                '{"status":"ready"}', encoding="utf-8"
            )
        else:
            write_valid_package(working_directory)
            write_research_report(working_directory)
            (working_directory / "provenance" / "agent_done.json").write_text(
                '{"status":"ready"}', encoding="utf-8"
            )
        return "done"


class InsufficientResearchAgent:
    def run(self, prompt: str, *, working_directory: Path) -> str:
        working_directory.joinpath("provenance").mkdir(parents=True, exist_ok=True)
        write_data_checkpoint(working_directory, status="insufficient_public_data")
        return "stopped"


class ExpandingResearchAgent:
    """首轮保留 partial 数据面，第二轮只新增分页文件。"""

    def __init__(self, *, mutate_existing: bool = False) -> None:
        self.calls = 0
        self.mutate_existing = mutate_existing

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls += 1
        if "环境语义描述 Agent" in prompt:
            write_valid_package(working_directory)
            environment_path = working_directory / "environment.json"
            environment = json.loads(environment_path.read_text(encoding="utf-8"))
            page_path = working_directory / "workspace" / "raw" / "items_page_002.json"
            if page_path.is_file():
                environment["resources"].insert(
                    1,
                    {
                        "resource_id": "raw_items_page_002",
                        "name": "第二页原始记录",
                        "description": "从同一公开接口取得的第二页真实记录。",
                        "data_type": "raw",
                        "storage_type": "file",
                        "path": "raw/items_page_002.json",
                        "format": "json",
                        "writable": False,
                    },
                )
                environment_path.write_text(
                    json.dumps(environment, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                sources_path = working_directory / "provenance" / "sources.json"
                sources = json.loads(sources_path.read_text(encoding="utf-8"))
                sources["sources"][0]["resource_ids"].append("raw_items_page_002")
                sources["sources"][0]["files"].append(
                    {"path": "raw/items_page_002.json"}
                )
                sources_path.write_text(
                    json.dumps(sources, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            write_research_report(working_directory)
            (working_directory / "provenance" / "agent_done.json").write_text(
                '{"status":"ready"}', encoding="utf-8"
            )
            return "environment described"

        if "真实数据采集 Agent" in prompt:
            write_valid_package(working_directory)
            write_data_checkpoint(working_directory)
            inventory_path = working_directory / "provenance" / "source_inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            surface = inventory["surfaces"][0]
            surface["collection_status"] = "partial"
            surface["exhaustion_evidence"] = None
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            for path in (
                working_directory / "environment.json",
                working_directory / "provenance" / "sources.json",
                working_directory / "provenance" / "research_report.json",
            ):
                path.unlink(missing_ok=True)
            return "initial page committed"

        raw_path = working_directory / "workspace" / "raw" / "items_page_002.json"
        raw_path.write_text(
            json.dumps([{"id": "b", "name": "Beta", "score": 2.5, "active": False}]),
            encoding="utf-8",
        )
        if self.mutate_existing:
            existing = working_directory / "workspace" / "raw" / "items.json"
            existing.write_text(existing.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        previous_checkpoint = next(
            (working_directory / "provenance" / "acquisition_rounds").glob(
                "data_checkpoint_round_*.json"
            )
        )
        checkpoint = json.loads(previous_checkpoint.read_text(encoding="utf-8"))
        checkpoint["raw_files"].append("raw/items_page_002.json")
        checkpoint["source_file_map"][0]["file_paths"].append("raw/items_page_002.json")
        (working_directory / "provenance" / "data_checkpoint.json").write_text(
            json.dumps(checkpoint), encoding="utf-8"
        )

        inventory_path = working_directory / "provenance" / "source_inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        surface = inventory["surfaces"][0]
        surface["collection_status"] = "complete"
        surface["records_collected"] = 2
        surface["raw_files"].append("raw/items_page_002.json")
        surface["pagination"]["pages_collected"] = 2
        surface["pagination"]["reported_total"] = 2
        surface["exhaustion_evidence"] = {
            "type": "reported_total_reached",
            "detail": "Both fixture pages were collected.",
        }
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        return "second page committed"


class MetadataTimeoutAgent:
    """第一阶段提交真实文件，第二阶段模拟 Agent 超时。"""

    def __init__(self, *, mutate_business_data: bool = False) -> None:
        self.calls = 0
        self.mutate_business_data = mutate_business_data

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls += 1
        if self.calls == 1:
            write_valid_package(working_directory)
            # 模拟 Agent 只来得及保存 raw；回退编译器应从 raw 恢复实体视图。
            (working_directory / "workspace" / "entities" / "items.json").unlink()
            write_data_checkpoint(working_directory)
            checkpoint_path = working_directory / "provenance" / "data_checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["entity_files"] = []
            checkpoint_path.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (working_directory / "environment.json").unlink(missing_ok=True)
            (working_directory / "provenance" / "sources.json").unlink(missing_ok=True)
            (working_directory / "provenance" / "research_report.json").unlink(missing_ok=True)
            return "data committed"
        if self.mutate_business_data:
            raw_path = working_directory / "workspace" / "raw" / "items.json"
            raw_path.write_text(raw_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        raise RuntimeError("metadata agent timeout")


class NormalizingMetadataAgent:
    """模拟 metadata Agent 创建确定性规范化视图的最小流程。"""

    def __init__(self, *, mutate_normalized: bool = False) -> None:
        self.calls = 0
        self.mutate_normalized = mutate_normalized

    def run(self, prompt: str, *, working_directory: Path) -> str:
        self.calls += 1
        if self.calls == 1:
            write_valid_package(working_directory)
            for path in (
                working_directory / "environment.json",
                working_directory / "provenance" / "sources.json",
                working_directory / "provenance" / "research_report.json",
            ):
                path.unlink(missing_ok=True)
            write_data_checkpoint(working_directory)
            return "data committed"

        request = json.loads(
            (working_directory / "provenance" / "research_request.json").read_text(encoding="utf-8")
        )
        checkpoint = json.loads(
            (working_directory / "provenance" / "data_checkpoint.json").read_text(encoding="utf-8")
        )
        generated = build_metadata(
            working_directory,
            seed={
                "theme_id": "test-seed",
                "seed_label": "Test",
                "candidate_entities": ["item"],
            },
            research_request=request,
            checkpoint=checkpoint,
        )
        normalized_path = working_directory / "workspace" / "entities" / "normalized_entities.json"
        if self.mutate_normalized:
            payload = json.loads(normalized_path.read_text(encoding="utf-8"))
            payload["item"][0]["name"] = "not-from-source"
            normalized_path.write_text(json.dumps(payload), encoding="utf-8")
        (working_directory / "environment.json").write_text(
            json.dumps(generated["environment"], ensure_ascii=False), encoding="utf-8"
        )
        (working_directory / "provenance" / "sources.json").write_text(
            json.dumps(generated["sources"], ensure_ascii=False), encoding="utf-8"
        )
        (working_directory / "provenance" / "research_report.json").write_text(
            json.dumps(generated["research_report"], ensure_ascii=False), encoding="utf-8"
        )
        (working_directory / "provenance" / "agent_done.json").write_text(
            '{"status":"ready"}', encoding="utf-8"
        )
        return "metadata committed"


class EnvironmentPackageValidatorTest(unittest.TestCase):
    def test_source_scope_rejects_unrelated_registered_domain(self) -> None:
        validator = EnvironmentPackageValidator(
            ENVIRONMENT_SCHEMA,
            seed={
                "theme_id": "scoped-seed",
                "seed_label": "Scoped records",
                "source_url": "https://docs.example.org/api",
                "candidate_entities": ["item"],
            },
            research_policy=TEST_POLICY,
        )
        report = ValidationReport()
        validator._validate_source_scope(
            "unrelated.example.net",
            "$.provenance.sources[0].url",
            report,
        )
        self.assertIn("source_outside_seed_scope", {issue.code for issue in report.errors})

    def test_source_scope_allows_seed_subdomains(self) -> None:
        validator = EnvironmentPackageValidator(
            ENVIRONMENT_SCHEMA,
            seed={
                "theme_id": "scoped-seed",
                "seed_label": "Scoped records",
                "source_url": "https://docs.example.org/api",
                "candidate_entities": ["item"],
            },
            research_policy=TEST_POLICY,
        )
        report = ValidationReport()
        validator._validate_source_scope(
            "api.example.org",
            "$.provenance.sources[0].url",
            report,
        )
        self.assertFalse(report.errors, report.to_dict())

    def test_valid_package_and_provenance_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            write_valid_package(package)
            validator = EnvironmentPackageValidator(ENVIRONMENT_SCHEMA)

            report = validator.validate(package)
            self.assertTrue(report.valid, report.to_dict())
            self.assertEqual(report.statistics["entity_records"], {"item": 1})

            validator.finalize_provenance(package)
            provenance = json.loads(
                (package / "provenance" / "sources.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(provenance["sources"][0]["files"][0]["sha256"]), 64)
            self.assertTrue(validator.validate(package).valid)

    def test_invalid_entity_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            write_valid_package(package)
            entity_path = package / "workspace" / "entities" / "items.json"
            payload = json.loads(entity_path.read_text(encoding="utf-8"))
            del payload["item"][0]["name"]
            entity_path.write_text(json.dumps(payload), encoding="utf-8")

            report = EnvironmentPackageValidator(ENVIRONMENT_SCHEMA).validate(package)
            self.assertFalse(report.valid)
            self.assertIn("missing_entity_fields", {issue.code for issue in report.errors})

    def test_source_file_map_is_checked_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            write_valid_package(package)
            request = compile_research_request(
                {
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                },
                TEST_POLICY,
            )
            (package / "provenance" / "research_request.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            write_data_checkpoint(package)
            checkpoint_path = package / "provenance" / "data_checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["source_file_map"] = [
                {
                    "url": "https://example.org/api/items",
                    "file_paths": ["raw/items.json"],
                }
            ]
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            report = EnvironmentPackageValidator(
                ENVIRONMENT_SCHEMA,
                seed={
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                },
                research_policy=TEST_POLICY,
            ).validate_data_checkpoint(package)
            self.assertTrue(report.valid, report.to_dict())

            checkpoint["source_file_map"][0]["file_paths"] = []
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            report = EnvironmentPackageValidator(
                ENVIRONMENT_SCHEMA,
                seed={
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                },
                research_policy=TEST_POLICY,
            ).validate_data_checkpoint(package)
            self.assertIn("incomplete_source_file_map", {issue.code for issue in report.errors})

    def test_ready_checkpoint_requires_source_file_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            write_valid_package(package)
            request = compile_research_request(
                {
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                },
                TEST_POLICY,
            )
            (package / "provenance" / "research_request.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            checkpoint = json.loads(
                (package / "provenance" / "data_checkpoint.json").read_text(encoding="utf-8")
            ) if (package / "provenance" / "data_checkpoint.json").exists() else None
            # 不使用 write_data_checkpoint，明确构造缺少来源映射的 ready 提交点。
            (package / "provenance" / "data_checkpoint.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "request_sha256": request["request_sha256"],
                        "status": "ready",
                        "summary": "missing map",
                        "raw_files": ["raw/items.json"],
                        "entity_files": ["entities/items.json"],
                        "derived_files": ["derived/summary.json"],
                        "source_urls": ["https://example.org/api/items"],
                        "synthetic_business_record_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            report = EnvironmentPackageValidator(
                ENVIRONMENT_SCHEMA,
                seed={
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                },
                research_policy=TEST_POLICY,
            ).validate_data_checkpoint(package)
            self.assertIn("data_checkpoint_schema", {issue.code for issue in report.errors})
            self.assertIn("missing_source_file_map", {issue.code for issue in report.errors})

    def test_coverage_cannot_use_unrelated_entity_or_bridge(self) -> None:
        validator = EnvironmentPackageValidator(ENVIRONMENT_SCHEMA)
        report = ValidationReport()
        inventory = {
            "research_author": {"fields": {"author_id", "display_name"}, "records": []},
            "work_author": {
                "fields": {"work_id", "author_id", "relation_id"},
                "records": [],
            },
            "institution": {"fields": {"institution_id", "display_name"}, "records": []},
        }
        requirement = {
            "kind": "seed_operation",
            "name": "inspect author",
            "target_tokens": ["author"],
            "operation_family": "inspect",
        }
        aligned = validator._validate_requirement_entity_alignment(
            requirement,
            {"work_author"},
            inventory,
            "$.research_report.coverage.seed_operation_01.evidence",
            report,
        )
        self.assertEqual(aligned, set())
        self.assertIn("coverage_entity_semantic_mismatch", {issue.code for issue in report.errors})


class MetadataNormalizationTest(unittest.TestCase):
    def test_request_normalizes_chinese_operations_and_candidate_tools(self) -> None:
        request = compile_research_request(
            {
                "theme_id": "localized-seed",
                "seed_label": "Localized records",
                "source_url": "https://example.org/api",
                "candidate_entities": ["issue"],
                "candidate_tools": [{"name": "排序 issue"}],
                "candidate_operations": ["检索 issue"],
            },
            TEST_POLICY,
        )
        operations = {
            item["name"]: item
            for item in request["requirements"]
            if item["kind"] == "seed_operation"
        }
        self.assertEqual(operations["检索 issue"]["operation_family"], "search")
        self.assertEqual(operations["排序 issue"]["operation_family"], "rank")

    def test_partial_relation_is_reported_without_becoming_traversable(self) -> None:
        groups = {
            "work": [
                {"work_id": "W1", "title": "First"},
                {"work_id": "W2", "title": "Second"},
            ],
            "author": [{"author_id": "A1", "display_name": "Ada"}],
            "work_author": [
                {"work_id": "W1", "author_id": "A1"},
                {"work_id": "W2", "author_id": "A2"},
            ],
        }
        fields = {
            name: {field: "string" for field in records[0]}
            for name, records in groups.items()
        }
        relations = _infer_relations(groups, fields)
        gaps = _infer_relation_gaps(groups, fields)
        self.assertFalse(
            any(
                item["from_entity"] == "work_author"
                and item["to_entity"] == "author"
                for item in relations
            )
        )
        self.assertTrue(
            any(
                item["from_entity"] == "work_author"
                and item["field"] == "author_id"
                and item["to_entity"] == "author"
                and item["missing_value_count"] == 1
                for item in gaps
            )
        )

    def test_research_request_scales_extension_target_with_seed_size(self) -> None:
        request = compile_research_request(
            {
                "theme_id": "larger-seed",
                "seed_label": "A larger environment",
                "candidate_entities": ["work", "author", "topic"],
                "candidate_operations": ["search work", "inspect author", "rank work"],
            },
            ResearchPolicy(),
        )
        policy = request["quality_policy"]
        self.assertEqual(policy["extension_capability_target"], 9)
        self.assertEqual(policy["extension_operation_family_target"], 4)
        self.assertEqual(policy["min_relations"], 2)

    def test_semantic_token_normalization_preserves_s_status_and_plural_words(self) -> None:
        self.assertEqual(operation_target_tokens("inspect status"), ["status"])
        self.assertEqual(operation_target_tokens("inspect analyses"), ["analysis"])
        self.assertEqual(operation_target_tokens("search issues by state"), ["issue"])
        self.assertIn("status", semantic_tokens("system status"))
        self.assertIn("analysis", semantic_tokens("analyses"))

    def test_short_business_names_are_searchable(self) -> None:
        self.assertTrue(
            _is_text_field(
                [{"name": "A"}, {"name": "B"}, {"name": "C"}],
                "name",
            )
        )
        self.assertFalse(
            _is_text_field(
                [{"id": "A"}, {"id": "B"}, {"id": "C"}],
                "id",
            )
        )

    def test_evidence_field_must_belong_to_declared_entity_types(self) -> None:
        validator = EnvironmentPackageValidator(ENVIRONMENT_SCHEMA)
        resources = {"entities": {"data_type": "entity"}}
        inventory = {
            "left": {
                "resource_ids": {"entities"},
                "fields": {"left_id", "name"},
                "field_types": {"left_id": "string", "name": "string"},
                "records": [],
            },
            "right": {
                "resource_ids": {"entities"},
                "fields": {"right_id", "name"},
                "field_types": {"right_id": "string", "name": "string"},
                "records": [],
            },
        }
        report = ValidationReport()
        validator._validate_evidence_list(
            [
                {
                    "resource_id": "entities",
                    "entity_types": ["left"],
                    "field_refs": ["right.right_id"],
                    "note": "不应通过",
                }
            ],
            resources,
            inventory,
            report,
            "test",
        )
        self.assertIn("evidence_field_entity_mismatch", {issue.code for issue in report.errors})

    def test_traverse_extension_must_reference_closed_relation(self) -> None:
        validator = EnvironmentPackageValidator(ENVIRONMENT_SCHEMA)
        relation = {
            "relation_id": "left_right",
            "from_entity": "left",
            "field": "right_id",
            "to_entity": "right",
            "target_field": "right_id",
        }
        report = ValidationReport()
        validator._validate_closed_traverse_evidence(
            [
                {
                    "field_refs": ["left.right_id", "right.right_id"],
                }
            ],
            [relation],
            "test",
            report,
        )
        self.assertTrue(report.valid)
        validator._validate_closed_traverse_evidence(
            [{"field_refs": ["left.right_id", "right.name"]}],
            [relation],
            "test",
            report,
        )
        self.assertIn(
            "extension_traverse_missing_closed_relation",
            {issue.code for issue in report.errors},
        )

    def test_generic_id_does_not_alias_every_foreign_key(self) -> None:
        self.assertFalse(_field_name_matches("id", "country_id"))
        self.assertFalse(_field_name_matches("name", "country_name"))

    def test_technical_node_id_is_not_a_business_relation_key(self) -> None:
        self.assertFalse(_is_relation_key_field("node_id"))
        self.assertFalse(_is_relation_key_field("entity_id"))
        self.assertFalse(_is_relation_key_field("relation_id"))
        self.assertTrue(_is_relation_key_field("author_id"))

    def test_seed_entity_name_wins_over_short_operation_target(self) -> None:
        entity_type = _canonical_entity_type(
            "openalex_topics_search_results",
            existing=set(),
            seed_entity_names=["research topic", "topic"],
            preferred_seed_entity_names=["research topic"],
            records=[{"topic_id": "T1", "display_name": "Climate"}],
        )
        self.assertEqual(entity_type, "research_topic")

    def test_composite_seed_entity_does_not_swallow_definition_records(self) -> None:
        entity_type = _canonical_entity_type(
            "indicator_NY.GDP.PCAP.CD",
            existing=set(),
            seed_entity_names=["indicator observation", "indicator"],
            preferred_seed_entity_names=["indicator observation"],
            records=[{"indicator_id": "NY.GDP.PCAP.CD", "name": "GDP per capita"}],
        )
        self.assertEqual(entity_type, "indicator")

    def test_fact_entity_name_is_not_merged_into_definition_entity(self) -> None:
        self.assertNotEqual(
            _canonical_entity_type(
                "indicator_observation",
                existing=set(),
                seed_entity_names=["indicator observation", "indicator"],
                records=[{"observation_id": "O1", "value": 1}],
            ),
            "indicator",
        )

    def test_partial_projection_does_not_erase_complete_entity_fields(self) -> None:
        merged = _merge_group_records(
            "country",
            [{"country_id": "C1", "name": "Alpha", "capital": "A"}],
            [
                {"country_id": "C1"},
                {"country_id": "C2", "name": "Beta", "capital": "B"},
            ],
        )
        self.assertEqual(merged[0]["name"], "Alpha")
        self.assertEqual(merged[0]["capital"], "A")
        self.assertEqual({record["country_id"] for record in merged}, {"C1", "C2"})

    def test_entity_file_partitions_collapse_into_seed_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "entities").mkdir(parents=True)
            (workspace / "raw").mkdir(parents=True)
            (workspace / "entities" / "normalized.json").write_text(
                json.dumps(
                    {
                        "indicator_observation": [
                            {
                                "observation_id": "C1-I1-2022",
                                "country_id": "C1",
                                "indicator_id": "I1",
                                "year": 2022,
                                "value": 10,
                            }
                        ],
                        "obs_I2_2022": [
                            {
                                "entity_id": "C1-I2",
                                "country_id": "C1",
                                "indicator_id": "I2",
                                "date": "2022",
                                "value": 20,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            request = compile_research_request(
                {
                    "theme_id": "observations",
                    "seed_label": "Indicator observations",
                    "candidate_entities": ["indicator observation"],
                },
                TEST_POLICY,
            )
            checkpoint = {
                "entity_files": ["entities/normalized.json"],
                "raw_files": [],
            }
            groups = deterministic_entity_groups(
                root,
                research_request=request,
                checkpoint=checkpoint,
            )
            self.assertEqual(list(groups), ["indicator_observation"])
            # seed 对应的 canonical 视图是权威实体文件；同一实体的别名
            # 分片只保留为 raw 证据，避免将字段不完整/ID 语义不同的重复
            # 记录拼入可供工具使用的实体集合。
            self.assertEqual(len(groups["indicator_observation"]), 1)

    def test_source_code_is_promoted_to_stable_entity_id(self) -> None:
        normalized = _normalize_groups(
            {
                "countries": [
                    {"country_code": "AAA", "name": "Alpha"},
                    {"country_code": "BBB", "name": "Beta"},
                ]
            }
        )
        self.assertEqual(len(normalized["countries"]), 2)
        self.assertEqual(
            {record["entity_id"] for record in normalized["countries"]},
            {"AAA", "BBB"},
        )

    def test_bridge_records_use_composite_key_instead_of_deduplicating_by_one_fk(self) -> None:
        normalized = _normalize_groups(
            {
                "work_authorships": [
                    {"work_id": "W1", "author_id": "A1", "position": 1},
                    {"work_id": "W1", "author_id": "A2", "position": 2},
                ]
            }
        )
        self.assertEqual(len(normalized["work_authorships"]), 2)
        self.assertEqual(
            {record["entity_id"] for record in normalized["work_authorships"]},
            {
                'author_id="A1"|work_id="W1"',
                'author_id="A2"|work_id="W1"',
            },
        )

    def test_plural_entity_uses_singular_business_id_and_is_not_bridge(self) -> None:
        """带自身外键的复数实体不能因生成 entity_id 被误判为关系桥。"""

        normalized = _normalize_groups(
            {
                "awards": [
                    {
                        "award_id": "A1",
                        "funder_id": "F1",
                        "funder_display_name": "Public Funder",
                    },
                    {
                        "award_id": "A2",
                        "funder_id": "F2",
                        "funder_display_name": "Research Funder",
                    },
                ]
            }
        )
        self.assertEqual(len(normalized["awards"]), 2)
        self.assertEqual(
            {record["award_id"] for record in normalized["awards"]},
            {"A1", "A2"},
        )
        self.assertFalse(
            _is_bridge_entity("awards", set(normalized["awards"][0]))
        )

    def test_merge_removes_only_recomputable_generated_composite_ids(self) -> None:
        generated = {
            "work_id": "W1",
            "author_id": "A1",
            "entity_id": 'author_id="A1"|work_id="W1"',
            "position": 1,
        }
        source_owned = {
            "work_id": "W2",
            "author_id": "A2",
            "entity_id": "source-owned|W2|A2",
            "position": 2,
        }
        cleaned = _without_generated_entity_ids([generated, source_owned])
        self.assertNotIn("entity_id", cleaned[0])
        self.assertEqual(cleaned[1]["entity_id"], "source-owned|W2|A2")

    def test_raw_only_canonical_view_rejects_entity_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            (workspace / "raw").mkdir(parents=True)
            (workspace / "entities").mkdir(parents=True)
            (workspace / "raw" / "items.json").write_text(
                json.dumps([{"id": "real", "name": "Real"}]), encoding="utf-8"
            )
            (workspace / "entities" / "normalized.json").write_text(
                json.dumps(
                    {
                        "item": [
                            {"item_id": "real", "name": "Real"},
                            {"item_id": "fake", "name": "Injected"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            request = compile_research_request(
                {
                    "theme_id": "raw-boundary",
                    "seed_label": "Items",
                    "candidate_entities": ["item"],
                },
                TEST_POLICY,
            )
            groups = deterministic_entity_groups(
                root,
                research_request=request,
                checkpoint={
                    "raw_files": ["raw/items.json"],
                    "entity_files": ["entities/normalized.json"],
                },
                authoritative_raw=True,
            )
            records = next(iter(groups.values()))
            identifiers = {
                record.get("entity_id", record.get("id")) for record in records
            }
            self.assertEqual(identifiers, {"real"})

    def test_relations_require_full_source_coverage(self) -> None:
        groups = {
            "works": [{"work_id": "W1"}, {"work_id": "W2"}],
            "authors": [{"author_id": "A1"}],
            "links": [{"work_id": "W1", "author_id": "A1"}],
        }
        fields = {name: {field: "string" for field in records[0]} for name, records in groups.items()}
        relations = _infer_relations(groups, fields)
        self.assertTrue(any(item["from_entity"] == "links" and item["to_entity"] == "works" for item in relations))
        self.assertFalse(any(item["from_entity"] == "works" and item["to_entity"] == "links" for item in relations))

    def test_operation_entity_selection_prefers_exact_entity_name(self) -> None:
        groups = {
            "indicator": [
                {"indicator_id": "I1", "name": "Population"},
                {"indicator_id": "I2", "name": "GDP"},
            ],
            "indicator_observation": [
                {"observation_id": "O1", "indicator_id": "I1", "value": 1},
                {"observation_id": "O2", "indicator_id": "I2", "value": 2},
            ],
        }
        fields = {
            name: {field: ("number" if field == "value" else "string") for field in records[0]}
            for name, records in groups.items()
        }
        self.assertEqual(_select_entity_type("list indicators", "list", groups, fields), "indicator")

    def test_numeric_operation_does_not_replace_target_with_child_observation(self) -> None:
        groups = {
            "indicator": [
                {"indicator_id": "I1", "name": "Population"},
                {"indicator_id": "I2", "name": "GDP"},
            ],
            "indicator_observation": [
                {"observation_id": "O1", "indicator_id": "I1", "value": 1},
                {"observation_id": "O2", "indicator_id": "I2", "value": 2},
            ],
        }
        fields = {
            name: {field: ("number" if field == "value" else "string") for field in records[0]}
            for name, records in groups.items()
        }
        # rank indicators 必须面对 indicator 实体；没有业务数值字段时，
        # 后续覆盖校验应报告缺口，而不能偷换成 indicator_observation。
        self.assertEqual(
            _select_entity_type("rank indicators", "rank", groups, fields),
            "indicator",
        )

    def test_numeric_operation_can_use_a_closed_related_fact_measure(self) -> None:
        groups = {
            "indicator": [
                {"indicator_id": "I1", "name": "Population"},
                {"indicator_id": "I2", "name": "GDP"},
            ],
            "indicator_observation": [
                {"observation_id": "O1", "indicator_id": "I1", "date": "2022", "value": 1},
                {"observation_id": "O2", "indicator_id": "I2", "date": "2022", "value": 2},
            ],
        }
        fields = {
            "indicator": {"indicator_id": "string", "name": "string"},
            "indicator_observation": {
                "observation_id": "string",
                "indicator_id": "string",
                "date": "string",
                "value": "number",
            },
        }
        fact = _related_fact_measure(
            "indicator",
            groups,
            fields,
            [
                {
                    "relation_id": "indicator_observation_indicator_id_indicator_indicator_id",
                    "from_entity": "indicator_observation",
                    "field": "indicator_id",
                    "to_entity": "indicator",
                    "target_field": "indicator_id",
                }
            ],
        )
        self.assertIsNotNone(fact)
        assert fact is not None
        self.assertEqual(fact[0], "indicator_observation")
        self.assertEqual(fact[1], ["value"])
        self.assertTrue(_is_numeric_measure_field("value"))
        self.assertFalse(_is_numeric_measure_field("decimal"))

    def test_numeric_seed_operation_declares_related_fact_resolution(self) -> None:
        request = compile_research_request(
            {
                "theme_id": "indicator-seed",
                "seed_label": "Indicator observations",
                "candidate_entities": ["country", "indicator observation"],
                "candidate_operations": ["rank indicators"],
            },
            TEST_POLICY,
        )
        operation = next(
            item for item in request["requirements"]
            if item["kind"] == "seed_operation"
        )
        self.assertEqual(operation["target_resolution"], "direct_or_related_fact")

    def test_raw_entity_names_with_shared_prefix_are_not_collapsed(self) -> None:
        self.assertEqual(
            _canonical_entity_type(
                "obs_ny_gdp_pcap_cd_2022",
                existing={"obs_sp_pop_totl_2022", "indicator_observation"},
                seed_entity_names=["indicator observation"],
            ),
            "obs_ny_gdp_pcap_cd_2022",
        )

    def test_raw_alias_uses_structural_fields_when_file_name_is_uninformative(self) -> None:
        alias = _find_identifier_alias(
            "obs_SP.POP.TOTL_2022",
            [
                {
                    "country_id": "C1",
                    "indicator_id": "I1",
                    "country_value": "Alpha",
                    "indicator_value": "Population",
                    "value": 10,
                }
            ],
            {
                "indicator_observation": [
                    {
                        "country_id": "C2",
                        "indicator_id": "I1",
                        "country_name": "Beta",
                        "indicator_name": "Population",
                        "observation_id": "C2-I1",
                        "value": 20,
                    }
                ]
            },
        )
        self.assertEqual(alias, "indicator_observation")

    def test_inspect_operation_prefers_business_entity_over_bridge_entity(self) -> None:
        groups = {
            "research_author": [
                {"author_id": "A1", "display_name": "Ada", "works_count": 3},
                {"author_id": "A2", "display_name": "Grace", "works_count": 4},
            ],
            "work_author": [
                {"work_id": "W1", "author_id": "A1", "author_position": "first"},
                {"work_id": "W2", "author_id": "A2", "author_position": "last"},
            ],
        }
        fields = {
            name: {field: ("integer" if field == "works_count" else "string") for field in records[0]}
            for name, records in groups.items()
        }
        self.assertEqual(
            _select_entity_type("inspect author", "inspect", groups, fields),
            "research_author",
        )

    def test_relation_inference_only_points_to_matching_primary_entity(self) -> None:
        groups = {
            "repository": [
                {"repository_id": "R1", "name": "Repo"},
            ],
            "issue": [
                {"issue_id": "I1", "repository_id": "R1", "title": "Bug"},
            ],
            "label": [
                {"label_id": "L1", "repository_id": "R1", "name": "bug"},
            ],
        }
        fields = {
            name: {field: ("string" if field in {"name", "title"} else "string") for field in records[0]}
            for name, records in groups.items()
        }
        relations = _infer_relations(groups, fields)
        self.assertIn(
            ("issue", "repository_id", "repository", "repository_id"),
            {
                (item["from_entity"], item["field"], item["to_entity"], item["target_field"])
                for item in relations
            },
        )
        self.assertNotIn(
            ("repository", "repository_id", "issue", "repository_id"),
            {
                (item["from_entity"], item["field"], item["to_entity"], item["target_field"])
                for item in relations
            },
        )

    def test_error_payload_is_not_a_valid_raw_checkpoint_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            write_valid_package(package)
            request = compile_research_request(
                {
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                    "candidate_operations": ["search item"],
                },
                TEST_POLICY,
            )
            (package / "provenance" / "research_request.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            write_data_checkpoint(package)
            (package / "workspace" / "raw" / "items.json").write_text(
                json.dumps([{"message": [{"id": "120", "value": "Invalid value"}]}]),
                encoding="utf-8",
            )
            report = EnvironmentPackageValidator(
                ENVIRONMENT_SCHEMA,
                seed={
                    "theme_id": "test-seed",
                    "seed_label": "Test",
                    "candidate_entities": ["item"],
                    "candidate_operations": ["search item"],
                },
                research_policy=TEST_POLICY,
            ).validate_data_checkpoint(package)
            self.assertIn("raw_error_payload", {issue.code for issue in report.errors})


class DataGeneratorTest(unittest.TestCase):
    def test_default_research_agent_uses_terra_high(self) -> None:
        generator = DataGenerator()
        self.assertEqual(generator.agent.model, DEFAULT_RESEARCH_MODEL)
        self.assertEqual(generator.agent.reasoning_effort, DEFAULT_REASONING_EFFORT)

    def test_capability_profile_uses_fields_and_builds_composable_chains(self) -> None:
        groups = {
            "work": [
                {
                    "work_id": f"W{index}",
                    "title": f"Research title {index}",
                    "topic": f"Topic {index % 4}",
                    "citation_count": index * 3,
                    "published_year": 2020 + index % 5,
                }
                for index in range(40)
            ]
        }
        profiles = profile_entity_groups(groups)
        atoms = extract_capability_atoms(profiles, [], [])
        composition = build_composition_profile(atoms)
        numeric_atoms = [
            item
            for item in atoms
            if item["evidence"] == ["work.citation_count"]
        ]
        self.assertEqual(
            {item["operation_family"] for item in numeric_atoms},
            {"rank", "compare", "aggregate"},
        )
        # 三种操作可以分别生成工具，但数据丰富度仍只看到同一个证据字段。
        self.assertEqual(
            {evidence for item in numeric_atoms for evidence in item["evidence"]},
            {"work.citation_count"},
        )
        self.assertGreater(composition["chain_shape_count"], 0)
        self.assertGreater(composition["estimated_task_instances"], 0)

    def test_default_output_root_classifies_not_rich_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps({"themes": [{"theme_id": "test-seed", "seed_label": "Test"}]}),
                encoding="utf-8",
            )
            result = DataGenerator(
                FakeResearchAgent(),
                max_collection_rounds=1,
                max_repair_rounds=0,
                research_policy=TEST_POLICY,
            ).generate(
                seed_path=seed_path,
                seed_id="test-seed",
                schema_path=ENVIRONMENT_SCHEMA,
                output_root=root / "oss",
            )
            self.assertEqual(result.quality_tier, "not_rich")
            self.assertEqual(result.output_dir, root / "oss" / "not_rich" / "test_seed")
            self.assertTrue(result.quality_profile_path.is_file())

    def test_zero_richness_policy_classifies_rich_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps({"themes": [{"theme_id": "test-seed", "seed_label": "Test"}]}),
                encoding="utf-8",
            )
            result = DataGenerator(
                FakeResearchAgent(),
                max_collection_rounds=1,
                max_repair_rounds=0,
                research_policy=TEST_POLICY,
                richness_policy=RichnessPolicy(
                    min_capability_atoms=0,
                    min_operation_families=0,
                    min_evidence_features=0,
                    min_transition_shapes=0,
                    min_chain_shapes=0,
                    min_long_chain_shapes=0,
                    min_estimated_task_instances=0,
                ),
            ).generate(
                seed_path=seed_path,
                seed_id="test-seed",
                schema_path=ENVIRONMENT_SCHEMA,
                output_root=root / "oss",
            )
            self.assertEqual(result.quality_tier, "rich")
            self.assertEqual(result.output_dir, root / "oss" / "rich" / "test_seed")

    def test_second_collection_round_appends_new_raw_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps({"themes": [{"theme_id": "test-seed", "seed_label": "Test"}]}),
                encoding="utf-8",
            )
            agent = ExpandingResearchAgent()
            result = DataGenerator(
                agent,
                max_collection_rounds=2,
                max_repair_rounds=0,
                research_policy=TEST_POLICY,
            ).generate(
                seed_path=seed_path,
                seed_id="test-seed",
                schema_path=ENVIRONMENT_SCHEMA,
                output_dir=root / "result",
            )
            self.assertEqual(result.collection_rounds, 2)
            # 首轮采集、一次扩充、一次环境语义描述。
            self.assertEqual(agent.calls, 3)
            self.assertTrue((result.workspace_path / "raw" / "items_page_002.json").is_file())

    def test_collection_round_cannot_modify_existing_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps({"themes": [{"theme_id": "test-seed", "seed_label": "Test"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(DataGenerationError) as context:
                DataGenerator(
                    ExpandingResearchAgent(mutate_existing=True),
                    max_collection_rounds=2,
                    max_repair_rounds=0,
                    research_policy=TEST_POLICY,
                ).generate(
                    seed_path=seed_path,
                    seed_id="test-seed",
                    schema_path=ENVIRONMENT_SCHEMA,
                    output_dir=root / "result",
                )
            self.assertIn(
                "acquisition_modified_business_file",
                {issue.code for issue in context.exception.report.errors},
            )

    def test_agent_output_is_repaired_validated_and_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps({"themes": [{"theme_id": "test-seed", "seed_label": "Test"}]}),
                encoding="utf-8",
            )
            output_dir = root / "result"
            agent = FakeResearchAgent(invalid_description=True)

            result = DataGenerator(
                agent,
                max_collection_rounds=1,
                max_repair_rounds=1,
                research_policy=TEST_POLICY,
            ).generate(
                seed_path=seed_path,
                seed_id="test-seed",
                schema_path=ENVIRONMENT_SCHEMA,
                output_dir=output_dir,
            )

            self.assertEqual(result.repair_rounds, 1)
            self.assertEqual(len(agent.prompts), 3)
            self.assertIn(str(seed_path), agent.prompts[0])
            self.assertIn("环境语义描述 Agent", agent.prompts[1])
            self.assertIn("声明修复", agent.prompts[2])
            self.assertTrue(result.environment_path.is_file())
            validation = json.loads(result.validation_path.read_text(encoding="utf-8"))
            self.assertTrue(validation["valid"])

    def test_core_public_data_gap_stops_without_repair_and_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "themes": [
                            {
                                "theme_id": "missing-seed",
                                "seed_label": "Unavailable records",
                                "candidate_entities": ["record"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "result"
            with self.assertRaises(InsufficientPublicDataError):
                DataGenerator(
                    InsufficientResearchAgent(),
                    max_collection_rounds=1,
                    max_repair_rounds=2,
                    research_policy=TEST_POLICY,
                ).generate(
                    seed_path=seed_path,
                    seed_id="missing-seed",
                    schema_path=ENVIRONMENT_SCHEMA,
                    output_dir=output_dir,
                )
            failed = list(root.glob("result.failed-*"))
            self.assertEqual(len(failed), 1)
            checkpoint = json.loads(
                (failed[0] / "provenance" / "data_checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["status"], "insufficient_public_data")

    def test_environment_description_timeout_has_no_heuristic_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "themes": [
                            {
                                "theme_id": "fallback-seed",
                                "seed_label": "Fallback records",
                                "candidate_entities": ["item"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "result"
            agent = MetadataTimeoutAgent()
            with self.assertRaises(DataGenerationError) as context:
                DataGenerator(
                    agent,
                    max_collection_rounds=1,
                    max_repair_rounds=0,
                    research_policy=TEST_POLICY,
                ).generate(
                    seed_path=seed_path,
                    seed_id="fallback-seed",
                    schema_path=ENVIRONMENT_SCHEMA,
                    output_dir=output_dir,
                )
            self.assertEqual(agent.calls, 2)
            self.assertIn(
                "environment_description_failed",
                {issue.code for issue in context.exception.report.errors},
            )

    def test_metadata_agent_cannot_modify_committed_business_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "themes": [
                            {
                                "theme_id": "mutation-seed",
                                "seed_label": "Mutation records",
                                "candidate_entities": ["item"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            agent = MetadataTimeoutAgent(mutate_business_data=True)
            with self.assertRaises(Exception) as context:
                DataGenerator(
                    agent,
                    max_collection_rounds=1,
                    max_repair_rounds=0,
                    research_policy=TEST_POLICY,
                ).generate(
                    seed_path=seed_path,
                    seed_id="mutation-seed",
                    schema_path=ENVIRONMENT_SCHEMA,
                    output_dir=root / "result",
                )
            error = context.exception
            self.assertIsInstance(error, DataGenerationError)
            self.assertIn(
                "environment_description_modified_business_file",
                {issue.code for issue in error.report.errors},
            )

    def test_agent_environment_description_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "themes": [
                            {
                                "theme_id": "test-seed",
                                "seed_label": "Test",
                                "candidate_entities": ["item"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = DataGenerator(
                FakeResearchAgent(),
                max_collection_rounds=1,
                max_repair_rounds=0,
                research_policy=TEST_POLICY,
            ).generate(
                seed_path=seed_path,
                seed_id="test-seed",
                schema_path=ENVIRONMENT_SCHEMA,
                output_dir=root / "result",
            )
            environment = json.loads(result.environment_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [resource["path"] for resource in environment["resources"] if resource["data_type"] == "entity"],
                ["entities/items.json"],
            )
            self.assertTrue((result.output_dir / "provenance" / "data_profile.json").is_file())

    def test_environment_description_cannot_create_entity_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "seeds.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "themes": [
                            {
                                "theme_id": "test-seed",
                                "seed_label": "Test",
                                "candidate_entities": ["item"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DataGenerationError) as context:
                DataGenerator(
                    NormalizingMetadataAgent(mutate_normalized=True),
                    max_collection_rounds=1,
                    max_repair_rounds=0,
                    research_policy=TEST_POLICY,
                ).generate(
                    seed_path=seed_path,
                    seed_id="test-seed",
                    schema_path=ENVIRONMENT_SCHEMA,
                    output_dir=root / "result",
                )
            self.assertIn(
                "environment_description_created_business_file",
                {issue.code for issue in context.exception.report.errors},
            )


if __name__ == "__main__":
    unittest.main()
