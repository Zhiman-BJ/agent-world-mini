from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from env_gen.data_gen.analysis.seed import is_python_package_seed, load_selected_seed
from seed_gen.scripts.correct_semiconductor_scenario_seeds import correct


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "seed_gen/pypi_outputs/semiconductor_scenario_02_0916.json"
SCHEMA = ROOT / "schemas/validation/env_seeds.schema.json"


def test_corrected_semiconductor_seeds_are_pipeline_valid(tmp_path: Path) -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    corrected = correct(source)
    output = tmp_path / "corrected.json"
    output.write_text(json.dumps(corrected, ensure_ascii=False), encoding="utf-8")

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(corrected)
    )
    assert not errors, "\n".join(error.message for error in errors[:20])
    assert len(corrected) == 22
    assert len({item["global_id"] for item in corrected}) == 22

    for seed in corrected:
        loaded, _digest = load_selected_seed(output, seed["global_id"], SCHEMA)
        assert loaded == seed
        assert is_python_package_seed(seed)
        assert len(seed["init_ref_tasks"]) == 1
        assert isinstance(seed["init_ref_tasks"][0], str)
        assert seed["init_ref_tasks"][0].strip()
        assert all(tool["description"].strip() for tool in seed["init_ref_tools"])
        assert all(
            method["description"].strip()
            for tool in seed["init_ref_tools"]
            if tool["type"] == "class"
            for method in tool["function"]
        )

        tools = seed["init_ref_tools"]
        expected = {
            "class": sum(tool["type"] == "class" for tool in tools),
            "function": sum(tool["type"] == "function" for tool in tools),
            "class_func": sum(
                len(tool.get("function", []))
                for tool in tools
                if tool["type"] == "class"
            ),
        }
        expected["all_func"] = expected["function"] + expected["class_func"]
        assert seed["environment"]["nums"] == expected


def test_corrected_process_scenarios_use_their_actual_package_chain() -> None:
    corrected = {
        item["others"]["original_global_id"]: item
        for item in correct(json.loads(SOURCE.read_text(encoding="utf-8")))
    }
    assert corrected["semiconductor_scenario_02_11_01"]["others"]["pypi_package"] == [
        "plasmapy"
    ]
    assert corrected["semiconductor_scenario_02_11_02"]["others"]["pypi_package"] == [
        "cantera"
    ]
    assert corrected["semiconductor_scenario_02_11_03"]["others"]["pypi_package"] == [
        "fipy",
        "gmsh",
        "cantera",
    ]
