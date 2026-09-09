from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from env_gen.data_gen.config import CollectionPolicy, DataGenConfig
from env_gen.data_gen.steps.step2_collect_data import save_source_research
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
        "schema_version": "1.1",
        "environment": {
            "basic_info": {
                "source": "demo",
                "url": ["https://example.test/catalog"],
                "name": "catalog",
                "version": "2026-09-02",
                "index": 1,
            },
            "description": "A public catalog of items and categories.",
            "domain": {"level1": "general", "level2": None, "level3": None},
            "nums": {"class": 0, "function": 1, "class_func": 0, "all_func": 1},
        },
        "init_ref_tools": [
            {
                "name": "list_items",
                "type": "function",
                "module": None,
                "description": "List and filter items.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"category": {"type": "string"}},
                },
                "outputSchema": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["item_id", "name", "category"],
                                "properties": {
                                    "item_id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "category": {"type": "string"},
                                },
                            },
                        }
                    },
                },
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
        "others": {},
    }


def scenario_payload(seed: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "seed_global_id": seed["global_id"],
        "seed_sha256": digest,
        "environment": {
            "summary": "A public catalog environment for operators who inspect item information.",
            "description": (
                "The environment represents a public catalog used by operators and analysts. "
                "Its main contents are independently identified items and their category "
                "classification, with stable descriptive information suitable for inspection."
            ),
            "source_urls": ["https://example.test/items.json"],
        },
        "entities": [
            {
                "name": "Item",
                "description": "A catalog entry that operators can inspect and compare.",
                "source_urls": ["https://example.test/items.json"],
            }
        ],
        "tools": [
            {
                "name": "list_items",
                "description": "Lists catalog items and filters them using supplied category criteria.",
                "source_urls": ["https://example.test/items.json"],
            }
        ],
        "tasks": [
            {
                "name": "Browse items by category",
                "description": "An operator selects a category, lists matching items, and inspects the result.",
                "source_urls": ["https://example.test/items.json"],
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


def source_research_payload(
    seed: dict[str, Any],
    digest: str,
    *,
    url: str = "https://example.test/items.json",
    status: str = "planned",
    record_count: int = 0,
    raw_files: list[str] | None = None,
) -> dict[str, Any]:
    del raw_files
    complete = status == "complete"
    return {
        "schema_version": "3.0",
        "seed_global_id": seed["global_id"],
        "seed_sha256": digest,
        "summary": (
            "The catalog endpoint provides downloadable item records with identifiers, names, "
            "and categories; the current investigation tracks their suitability for integration."
        ),
        "investigation_targets": [
            {
                "target_id": "item_records",
                "description": "Collect catalog item records used by listing and category browsing.",
                "priority": "core",
                "related_entities": ["Item"],
                "related_tools": ["list_items"],
                "related_tasks": [
                    "Browse items by category",
                    seed["init_ref_tasks"][0]["description"],
                ],
                "expected_content_roles": ["structured_data"],
                "expected_data": ["Stable item identifiers, names, and category values."],
                "variation_dimensions": ["Category and item identity"],
                "connection_keys": ["item_id"],
                "file_context": [],
                "status": "covered" if complete else "pending",
                "source_ids": ["items"],
                "gap": None if complete else "The registered endpoint has not been collected yet.",
            }
        ],
        "sources": [
            {
                "source_id": "items",
                "name": "Items",
                "url": url,
                "registered_urls": [url],
                "source_type": "api",
                "content_roles": ["structured_data"],
                "target_ids": ["item_records"],
                "collection_mode": "complete_source",
                "collection_strategy": "Download the complete small response and profile every item field.",
                "status": status,
                "findings": (
                    f"The complete response contains {record_count} item records."
                    if complete else "The endpoint is registered and awaiting a controlled download."
                ),
                "limitations": [],
            }
        ],
        "expansion_findings": [
            "The complete catalog includes multiple categories and item identities beyond one example task."
        ] if complete else [],
        "seed_tool_observations": [],
        "task_file_formats": [],
        "result": "in_progress",
    }


source_plan_payload = source_research_payload


def prepare_step0(
    run_dir: Path,
    *,
    policy: CollectionPolicy | None = None,
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
    )
    return seed, digest


def prepare_run(
    run_dir: Path,
    *,
    url: str = "https://example.test/items.json",
    policy: CollectionPolicy | None = None,
) -> tuple[dict[str, Any], str]:
    collection_policy = policy or CollectionPolicy()
    seed, digest = prepare_step0(
        run_dir,
        policy=collection_policy,
    )
    save_scenario_research(run_dir, scenario_payload(seed, digest))
    (run_dir / "workspace/raw").mkdir(parents=True, exist_ok=True)
    save_source_research(run_dir, source_research_payload(seed, digest, url=url))
    return seed, digest
