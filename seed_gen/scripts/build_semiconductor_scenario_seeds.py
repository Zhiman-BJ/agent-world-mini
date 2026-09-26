#!/usr/bin/env python3
"""Build scenario-first semiconductor Seeds from the researched catalog."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


SEED_GEN_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = SEED_GEN_ROOT / "semiconductor_domain"
DEFAULT_TAXONOMY = CATALOG_ROOT / "taxonomy_v1.json"
DEFAULT_AUTHORITY_SOURCES = CATALOG_ROOT / "authoritative_sources_v1.json"
DEFAULT_WORKFLOW_SOURCES = CATALOG_ROOT / "workflow_sources_v1.json"
DEFAULT_SCENARIOS = CATALOG_ROOT / "scenarios_v1.json"
DEFAULT_SELECTION = CATALOG_ROOT / "seed_selection_v1.json"
DEFAULT_OUTPUT = CATALOG_ROOT / "scenario_seeds_v1.json"
DEFAULT_REPORT = CATALOG_ROOT / "seed_build_report_v1.json"
SOURCE_ID = "semiconductor_scenario_catalog"


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _unique_index(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values = [str(item.get(key, "")) for item in items]
    if any(not value for value in values):
        raise ValueError(f"{label} contains an empty {key}")
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate {label} {key}: {duplicates}")
    return dict(zip(values, items, strict=True))


def _normalize_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not normalized:
        raise ValueError(f"Cannot normalize identifier: {value!r}")
    return normalized


def _build_seed(
    *,
    scenario: dict[str, Any],
    selection: dict[str, Any],
    catalog_index: int,
    l1: dict[str, Any],
    l2: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    version: str,
) -> dict[str, Any]:
    scenario_id = str(scenario["id"])
    source_records = [evidence[source_id] for source_id in scenario["evidence_ids"]]
    source_urls = [str(item["url"]) for item in source_records]
    roles = "、".join(str(item) for item in scenario["roles"])
    inputs = "、".join(str(item) for item in scenario["inputs"])
    outputs = "、".join(str(item) for item in scenario["outputs"])
    description = (
        f"这是一个“{l2['name']}”业务环境，服务于{roles}。现实目标是{scenario['goal']}"
        f"环境围绕同一工作上下文中的{inputs}组织数据，支持依次完成"
        f"{'、'.join(str(item) for item in scenario['workflow_steps'])}。"
        f"最终形成{outputs}，并按以下规则验收：{scenario['verification']}"
        "Python 包和外部求解器只是实现手段，可以在不改变业务对象、数据关系和验收语义的前提下替换。"
    )
    global_id = f"{SOURCE_ID}_{_normalize_id(scenario_id)}_{catalog_index}"
    return {
        "global_id": global_id,
        "schema_version": "1.1",
        "environment": {
            "basic_info": {
                "source": SOURCE_ID,
                "url": source_urls,
                "name": scenario_id,
                "version": version,
                "index": catalog_index,
            },
            "description": description,
            "domain": {
                "level1": str(l1["key"]),
                "level2": str(l2["key"]),
                "level3": None,
            },
            "nums": {
                "class": 0,
                "function": 0,
                "class_func": 0,
                "all_func": 0,
            },
        },
        # The catalog records implementation candidates, not source-owned APIs.
        # Step 1 should research the actual toolchain instead of treating invented
        # wrappers as an authoritative package contract.
        "init_ref_tools": [],
        "init_ref_tasks": [
            {
                "description": str(scenario["goal"]),
                "input": {
                    "required_business_inputs": list(scenario["inputs"]),
                    "shared_context": "输入必须来自同一项目、器件、设计、批次或其他可证明一致的工作上下文。",
                },
                "output": {
                    "expected_results": list(scenario["outputs"]),
                    "acceptance": str(scenario["verification"]),
                },
                "solution_path": list(scenario["workflow_steps"]),
            }
        ],
        "others": {
            "scenario_catalog": {
                "scenario_id": scenario_id,
                "l1_id": str(l1["id"]),
                "l2_id": str(l2["id"]),
                "roles": list(scenario["roles"]),
                "evidence": [
                    {
                        "id": str(item["id"]),
                        "kind": str(item.get("kind", "authority")),
                        "title": str(item["title"]),
                        "url": str(item["url"]),
                        "support": str(item.get("evidence") or item.get("authority_role")),
                    }
                    for item in source_records
                ],
                "verification": str(scenario["verification"]),
                "selection": {
                    key: value
                    for key, value in selection.items()
                    if key not in {"scenario_id", "seed_number"}
                },
            },
            "python_tool_candidates": list(scenario["python_tool_candidates"]),
        },
    }


def build(
    *,
    taxonomy_path: Path,
    authority_sources_path: Path,
    workflow_sources_path: Path,
    scenarios_path: Path,
    selection_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    taxonomy = _read_object(taxonomy_path)
    authority_sources = _read_object(authority_sources_path)
    workflow_sources = _read_object(workflow_sources_path)
    scenario_catalog = _read_object(scenarios_path)
    selection_catalog = _read_object(selection_path)
    version = scenario_catalog.get("researched_at")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Scenario catalog must declare a non-empty researched_at version")

    l1_by_id = _unique_index(list(taxonomy["levels"]), "id", "L1")
    l2_by_id: dict[str, dict[str, Any]] = {}
    l1_for_l2: dict[str, dict[str, Any]] = {}
    for l1 in l1_by_id.values():
        for l2 in l1["level2"]:
            l2_id = str(l2["id"])
            if l2_id in l2_by_id:
                raise ValueError(f"Duplicate L2 id: {l2_id}")
            l2_by_id[l2_id] = l2
            l1_for_l2[l2_id] = l1

    evidence_items = [*authority_sources["sources"], *workflow_sources["sources"]]
    evidence = _unique_index(evidence_items, "id", "evidence")
    scenarios = list(scenario_catalog["scenarios"])
    scenario_by_id = _unique_index(scenarios, "id", "scenario")
    selections = list(selection_catalog["selected"])
    selection_by_scenario = _unique_index(selections, "scenario_id", "selection")

    expected_numbers = list(range(1, len(selections) + 1))
    actual_numbers = [int(item["seed_number"]) for item in selections]
    if actual_numbers != expected_numbers:
        raise ValueError("seed_number must be contiguous and ordered from 1")

    scenario_position = {
        str(scenario["id"]): index for index, scenario in enumerate(scenarios, start=1)
    }
    seeds: list[dict[str, Any]] = []
    for selection in selections:
        scenario_id = str(selection["scenario_id"])
        if scenario_id not in scenario_by_id:
            raise ValueError(f"Selection references unknown scenario: {scenario_id}")
        scenario = scenario_by_id[scenario_id]
        if scenario.get("seed_candidate") is not True:
            raise ValueError(f"Selected scenario is not a Seed candidate: {scenario_id}")
        l2_id = str(scenario["l2_id"])
        if l2_id not in l2_by_id:
            raise ValueError(f"Scenario references unknown L2: {scenario_id} -> {l2_id}")
        missing_evidence = sorted(set(scenario["evidence_ids"]) - set(evidence))
        if missing_evidence:
            raise ValueError(f"Scenario {scenario_id} has unknown evidence: {missing_evidence}")
        seeds.append(
            _build_seed(
                scenario=scenario,
                selection=selection,
                catalog_index=scenario_position[scenario_id],
                l1=l1_for_l2[l2_id],
                l2=l2_by_id[l2_id],
                evidence=evidence,
                version=version,
            )
        )

    global_ids = [seed["global_id"] for seed in seeds]
    if len(global_ids) != len(set(global_ids)):
        raise ValueError("Generated global_id values are not unique")

    selected_ids = set(selection_by_scenario)
    report = {
        "schema_version": "1.0",
        "taxonomy": {
            "l1_count": len(l1_by_id),
            "l2_count": len(l2_by_id),
        },
        "research": {
            "authority_source_count": len(authority_sources["sources"]),
            "workflow_source_count": len(workflow_sources["sources"]),
            "scenario_count": len(scenarios),
            "covered_l2_count": len({str(item["l2_id"]) for item in scenarios}),
        },
        "selection": {
            "selected_seed_count": len(seeds),
            "deferred_candidate_count": sum(
                1
                for scenario in scenarios
                if scenario.get("seed_candidate") is True
                and str(scenario["id"]) not in selected_ids
            ),
            "not_ready_count": sum(
                1 for scenario in scenarios if scenario.get("seed_candidate") is not True
            ),
            "selected_by_l1": dict(
                sorted(
                    Counter(
                        str(l1_for_l2[str(scenario_by_id[scenario_id]["l2_id"])]["id"])
                        for scenario_id in selected_ids
                    ).items()
                )
            ),
            "selected_scenario_ids": [str(item["scenario_id"]) for item in selections],
        },
        "generated_global_ids": global_ids,
    }
    return seeds, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--authority-sources", type=Path, default=DEFAULT_AUTHORITY_SOURCES)
    parser.add_argument("--workflow-sources", type=Path, default=DEFAULT_WORKFLOW_SOURCES)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    seeds, report = build(
        taxonomy_path=args.taxonomy.resolve(),
        authority_sources_path=args.authority_sources.resolve(),
        workflow_sources_path=args.workflow_sources.resolve(),
        scenarios_path=args.scenarios.resolve(),
        selection_path=args.selection.resolve(),
    )
    _write_json(args.output.resolve(), seeds)
    _write_json(args.report.resolve(), report)
    print(
        f"Wrote {len(seeds)} scenario-first semiconductor Seeds to "
        f"{args.output.resolve()}"
    )


if __name__ == "__main__":
    main()
