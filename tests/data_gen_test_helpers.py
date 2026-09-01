from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.quality import RichnessPolicy
from env_gen.data_gen.config import CollectionPolicy, DataGenConfig
from env_gen.data_gen.analysis.environment_quality import EnvironmentQualityPolicy
from env_gen.data_gen.steps.collection.commands.save_source_plan import (
    save_source_plan_payload,
)
from env_gen.data_gen.steps.collection.support.agent_workflow import prepare_collection
from env_gen.data_gen.steps.step1_research_scenario import save_scenario_research
from env_gen.data_gen.steps.step0_prepare_run import prepare_generation_run


ROOT = Path(__file__).resolve().parents[1]
SEED_VALIDATION = ROOT / "schemas/validation/env_seeds.schema.json"
ENVIRONMENT_CONTRACT = ROOT / "schemas/环境契约-v2.0.md"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sample_seed() -> dict[str, Any]:
    return {
        "global_id": "demo_catalog_1",
        "schema_version": "1.0",
        "environment": {
            "basic_info": {
                "source": "demo",
                "url": "https://example.test/catalog",
                "name": "catalog",
                "index": 1,
            },
            "description": "A public catalog of items and categories.",
            "domain": {"level1": "general", "level2": None, "level3": None},
        },
        "init_ref_tools": [
            {
                "name": "list_items",
                "description": "List and filter items.",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
            }
        ],
        "init_ref_tasks": [
            {
                "description": "Find public items in one category and inspect the result.",
                "input": {"category": "example"},
                "output": {"item_ids": ["item-1"]},
                "solution_path": [{"tool_name": "list_items"}],
            }
        ],
        "others": {"data_directions": ["Public item and category records"]},
    }


def scenario_payload(seed: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "seed_global_id": seed["global_id"],
        "seed_sha256": digest,
        "environment": {
            "summary": "A public catalog environment for operators who inspect item information.",
            "description": (
                "The environment represents a public catalog used by operators and analysts. "
                "Its main contents are independently identified items and their category "
                "classification, with stable descriptive information suitable for inspection."
            ),
        },
        "entities": [
            {
                "name": "Item",
                "description": "A catalog entry that operators can inspect and compare.",
                "key_attributes": ["item identifier", "name", "category"],
            }
        ],
        "tools": [
            {
                "name": "list_items",
                "description": "Lists catalog items and filters them using supplied category criteria.",
            }
        ],
        "tasks": [
            {
                "name": "Browse items by category",
                "description": "An operator selects a category, lists matching items, and inspects the result.",
            }
        ],
        "research_notes": {
            "data_directions": ["Collect stable item and category records."],
            "sources": [
                {
                    "url": "https://example.test/items.json",
                    "description": "Candidate catalog API entry point and record example.",
                }
            ],
            "open_questions": ["Whether a separate category dataset exists."],
        },
    }


def source_plan_payload(
    seed: dict[str, Any],
    digest: str,
    *,
    url: str = "https://example.test/items.json",
    status: str = "planned",
    record_count: int = 0,
    raw_files: list[str] | None = None,
) -> dict[str, Any]:
    raw_files = list(raw_files or [])
    complete = status == "complete"
    return {
        "schema_version": "1.0",
        "seed_global_id": seed["global_id"],
        "seed_sha256": digest,
        "summary": "Public catalog source plan.",
        "data_mode": "structured_records",
        "data_mode_reason": "Structured records support the scenario.",
        "research_deviation_note": None,
        "deep_research_summary": "The candidate endpoint is suitable for direct probing.",
        "research_refinements": [
            {
                "refinement_id": "item_entity_check",
                "finding_type": "entity",
                "status": "confirmed" if complete else "revised",
                "description": "The source is expected to expose item records.",
                "evidence_source_ids": ["items"],
                "impact_on_collection": "Collect and normalize item rows.",
            }
        ],
        "required_file_formats": [],
        "evidence_file_formats": ["json"],
        "file_dependent_seed_paths": [],
        "data_need_coverage": [
            {
                "need_id": "item_records",
                "status": "supported" if complete else "planned",
                "source_ids": ["items"],
                "evidence_entity_types": ["item"] if complete else [],
                "evidence_fields": ["item.name", "item.category"] if complete else [],
                "assessment": "Collected item evidence." if complete else "Pending probe.",
            }
        ],
        "sources": [
            {
                "source_id": "items",
                "name": "Items",
                "url": url,
                "registered_urls": [url],
                "priority": "core",
                "source_type": "single_file",
                "scenario_source_lead_id": "catalog_api",
                "discovery_note": "Selected from the Step 1 scenario research.",
                "need_ids": ["item_records"],
                "target_entity_types": ["item"],
                "retrieval": {
                    "method": "download",
                    "page_size": None,
                    "units_collected": 1 if complete else 0,
                    "reported_total": record_count if complete else None,
                },
                "coverage_strategy": "exhaustive",
                "status": status,
                "access_status": "public" if complete else "unknown",
                "access_note": "Anonymous public response." if complete else None,
                "record_count": record_count,
                "raw_files": raw_files,
                "related_source_ids": [],
                "status_evidence": (
                    {
                        "type": "reported_total_reached",
                        "detail": "The complete file contains the reported records.",
                    }
                    if complete
                    else None
                ),
            }
        ],
    }


def minimal_richness_policy() -> RichnessPolicy:
    return RichnessPolicy(
        min_entity_types=1,
        min_total_entity_records=2,
        min_substantial_entity_types=1,
        min_records_per_substantial_entity=2,
        min_core_entity_records=2,
        min_core_business_fields=2,
        min_data_need_coverage_percent=100,
        min_supported_data_need_count=1,
        max_unassessed_data_needs=0,
        min_closed_relations=0,
        max_relation_gaps=10,
    )


def prepare_step0(
    run_dir: Path,
    *,
    policy: CollectionPolicy | None = None,
    environment_quality_policy: EnvironmentQualityPolicy | None = None,
) -> tuple[dict[str, Any], str]:
    seed = sample_seed()
    seed_path = run_dir / "seeds.json"
    write_json(seed_path, [seed])
    digest = prepare_generation_run(
        run_dir,
        DataGenConfig(
            seed_path=seed_path,
            global_id=seed["global_id"],
            seed_validation_schema_path=SEED_VALIDATION,
            contract_path=ENVIRONMENT_CONTRACT,
        ),
        limits=asdict(policy or CollectionPolicy()),
        quality=asdict(environment_quality_policy or EnvironmentQualityPolicy()),
    )
    return seed, digest


def prepare_run(
    run_dir: Path,
    *,
    url: str = "https://example.test/items.json",
    policy: CollectionPolicy | None = None,
    environment_quality_policy: EnvironmentQualityPolicy | None = None,
) -> tuple[dict[str, Any], str]:
    collection_policy = policy or CollectionPolicy()
    seed, digest = prepare_step0(
        run_dir,
        policy=collection_policy,
        environment_quality_policy=environment_quality_policy,
    )
    save_scenario_research(run_dir, scenario_payload(seed, digest))
    prepare_collection(run_dir)
    save_source_plan_payload(run_dir, source_plan_payload(seed, digest, url=url))
    return seed, digest
