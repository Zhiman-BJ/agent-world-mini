from __future__ import annotations

import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.scripts.build_semiconductor_scenario_seeds import build


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "seed_gen/semiconductor_domain"


def _read(name: str):
    return json.loads((CATALOG / name).read_text(encoding="utf-8"))


def test_catalog_counts_and_coverage():
    taxonomy = _read("taxonomy_v1.json")
    scenarios = _read("scenarios_v1.json")["scenarios"]
    selection = _read("seed_selection_v1.json")["selected"]

    l2_ids = {item["id"] for level in taxonomy["levels"] for item in level["level2"]}
    scenario_ids = [item["id"] for item in scenarios]
    selected_ids = [item["scenario_id"] for item in selection]

    assert len(taxonomy["levels"]) == 10
    assert len(l2_ids) == 50
    assert len(scenarios) == 100
    assert len(scenario_ids) == len(set(scenario_ids))
    assert {item["l2_id"] for item in scenarios} == l2_ids
    assert len(selection) == 39
    assert len(selected_ids) == len(set(selected_ids))
    assert selected_ids == [
        item["id"] for item in scenarios if item["id"] in set(selected_ids)
    ]


def test_taxonomy_has_authoritative_evidence_and_operational_boundaries():
    taxonomy = _read("taxonomy_v1.json")
    authority = _read("authoritative_sources_v1.json")["sources"]
    authority_ids = [item["id"] for item in authority]

    assert len(authority_ids) == len(set(authority_ids))
    for level in taxonomy["levels"]:
        assert level["authority_ids"]
        assert set(level["authority_ids"]) <= set(authority_ids)
        assert level["definition"].strip()
        for level2 in level["level2"]:
            assert level2["authority_ids"]
            assert set(level2["authority_ids"]) <= set(authority_ids)
            assert level2["scope"].strip()
            assert level2["core_objects"]
            assert level2["typical_outputs"]


def test_every_scenario_has_complete_business_and_evidence_fields():
    authority = _read("authoritative_sources_v1.json")["sources"]
    workflow = _read("workflow_sources_v1.json")["sources"]
    evidence_ids = {item["id"] for item in [*authority, *workflow]}

    for scenario in _read("scenarios_v1.json")["scenarios"]:
        assert re.fullmatch(r"SC-\d{2}\.\d{2}-\d{2}", scenario["id"])
        for key in (
            "name",
            "goal",
            "verification",
            "data_availability",
        ):
            assert isinstance(scenario[key], str) and scenario[key].strip()
        for key in (
            "roles",
            "inputs",
            "workflow_steps",
            "outputs",
            "evidence_ids",
            "python_tool_candidates",
        ):
            assert isinstance(scenario[key], list) and scenario[key]
            assert all(isinstance(item, str) and item.strip() for item in scenario[key])
        assert len(scenario["workflow_steps"]) >= 4
        assert set(scenario["evidence_ids"]) <= evidence_ids
        assert scenario["data_availability"] in {"low", "medium", "high"}
        assert isinstance(scenario["seed_candidate"], bool)


def test_selected_scenarios_have_implementation_evidence_and_no_low_data():
    workflow_ids = {
        item["id"] for item in _read("workflow_sources_v1.json")["sources"]
    }
    scenarios = {
        item["id"]: item for item in _read("scenarios_v1.json")["scenarios"]
    }

    for selection in _read("seed_selection_v1.json")["selected"]:
        scenario = scenarios[selection["scenario_id"]]
        assert set(scenario["evidence_ids"]) & workflow_ids
        assert scenario["data_availability"] in {"medium", "high"}
        assert scenario["python_tool_candidates"]


def test_selection_only_uses_ready_scenarios_and_contiguous_numbers():
    scenarios = {
        item["id"]: item for item in _read("scenarios_v1.json")["scenarios"]
    }
    selected = _read("seed_selection_v1.json")["selected"]

    assert [item["seed_number"] for item in selected] == list(range(1, 40))
    for item in selected:
        assert scenarios[item["scenario_id"]]["seed_candidate"] is True
        for dimension in (
            "business_evidence",
            "data_availability",
            "python_executability",
            "verification_strength",
            "workflow_depth",
        ):
            assert item[dimension] in {"moderate", "strong"}
        assert item["reason"].strip()


def test_generated_seeds_match_sources_and_contract():
    seeds, report = build(
        taxonomy_path=CATALOG / "taxonomy_v1.json",
        authority_sources_path=CATALOG / "authoritative_sources_v1.json",
        workflow_sources_path=CATALOG / "workflow_sources_v1.json",
        scenarios_path=CATALOG / "scenarios_v1.json",
        selection_path=CATALOG / "seed_selection_v1.json",
    )
    committed = _read("scenario_seeds_v1.json")
    committed_report = _read("seed_build_report_v1.json")

    assert seeds == committed
    assert report == committed_report
    assert len(seeds) == 39
    assert len({item["global_id"] for item in seeds}) == 39
    assert all(item["init_ref_tools"] == [] for item in seeds)
    assert all(len(item["init_ref_tasks"]) == 1 for item in seeds)
    assert all(item["others"]["python_tool_candidates"] for item in seeds)

    schema = json.loads(
        (ROOT / "schemas/validation/env_seeds.schema.json").read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(seeds)
    )
    assert not errors, "\n".join(error.message for error in errors)

    for seed in seeds:
        info = seed["environment"]["basic_info"]
        normalized_name = re.sub(r"[^A-Za-z0-9]+", "_", info["name"]).strip("_").lower()
        assert seed["global_id"] == (
            f"{info['source']}_{normalized_name}_{info['index']}"
        )
        assert seed["environment"]["domain"]["level3"] is None
