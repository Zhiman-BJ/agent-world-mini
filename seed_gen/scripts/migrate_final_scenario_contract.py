"""Migrate the published semiconductor collection to scenario Seed v1.1."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.scripts.build_joint_scenario_seeds import counts, require


DEFAULT_OUTPUT = Path("seed_gen/pypi_outputs/final_results")
SCHEMA = Path("schemas/validation/scenario_env_seeds.schema.json")


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def scenario_id(seed: dict) -> str:
    level3 = seed["environment"]["domain"]["level3"]
    match = re.match(r"^(\d{2}\.\d{2}\.\d{2})(?:\s|$)", level3)
    require(match is not None, f"Invalid L3 label: {level3}")
    return match.group(1)


def migrate(output_dir: Path, schema_path: Path) -> list[dict]:
    partitions: dict[Path, list[dict]] = {}
    seeds: list[dict] = []
    for path in sorted(output_dir.glob("semiconductor_scenario_[0-9][0-9].json")):
        payload = read(path)
        require(isinstance(payload, list) and payload, f"Invalid partition: {path}")
        partitions[path] = payload
        seeds.extend(payload)

    require(len(partitions) == 8, f"Expected 8 L1 partitions, got {len(partitions)}")
    require(len(seeds) == 160, f"Expected 160 scenarios, got {len(seeds)}")
    seeds.sort(key=scenario_id)
    l2_indices = {
        scenario_id(seed): index
        for index, seed in enumerate(
            (seed for seed in seeds if scenario_id(seed).startswith("02.")),
            start=28,
        )
    }
    require(len(l2_indices) == 22, f"Expected 22 L1=02 scenarios, got {len(l2_indices)}")

    for seed in seeds:
        sid = scenario_id(seed)
        seed["schema_version"] = "scenario-1.1"
        seed["environment"]["basic_info"]["source"] = "deep_research"
        if sid.startswith("02."):
            seed["environment"]["basic_info"]["index"] = l2_indices[sid]
            seed["environment"]["domain"]["level1"] = "02 器件与物理仿真"
        require(
            seed["global_id"] == "semiconductor_scenario_" + sid.replace(".", "_"),
            f"global_id/L3 mismatch: {sid}",
        )
        require(
            counts(seed["init_ref_tools"]) == seed["environment"]["nums"],
            f"Tool count mismatch: {sid}",
        )

    indices = [seed["environment"]["basic_info"]["index"] for seed in seeds]
    require(len(indices) == len(set(indices)), "Scenario indices are not unique")
    require(set(indices) == set(range(1, 161)), "Scenario indices must cover 1..160")
    require(len({seed["global_id"] for seed in seeds}) == len(seeds), "Duplicate global_id")

    schema = read(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(seeds)
    )
    require(
        not errors,
        "Scenario schema errors: "
        + "; ".join(f"{list(error.absolute_path)}: {error.message}" for error in errors[:20]),
    )

    by_l1 = {
        l1: [seed for seed in seeds if scenario_id(seed).startswith(l1 + ".")]
        for l1 in (f"{value:02d}" for value in range(1, 9))
    }
    for path in partitions:
        l1 = path.stem[-2:]
        write(path, by_l1[l1])
    write(output_dir / "semiconductor_scenario_collection.json", seeds)
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--schema", type=Path, default=SCHEMA)
    args = parser.parse_args()
    seeds = migrate(args.output_dir, args.schema)
    print(f"Migrated {len(seeds)} scenarios to scenario-1.1.")


if __name__ == "__main__":
    main()
