"""Inventory assertion gaps without claiming that coverage certifies tool semantics."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from .utils.io import read_json, write_json


def schema_leaves(schema: dict, prefix: tuple = ()) -> set[tuple]:
    if not isinstance(schema, dict):
        return set()
    branches = [branch for key in ("oneOf", "anyOf", "allOf") for branch in schema.get(key, [])]
    result = set().union(*(schema_leaves(branch, prefix) for branch in branches)) if branches else set()
    properties = schema.get("properties", {})
    for key, value in properties.items():
        result.update(schema_leaves(value, (*prefix, key)))
    if isinstance(schema.get("items"), dict):
        result.update(schema_leaves(schema["items"], (*prefix, "[]")))
    if not branches and not properties and not isinstance(schema.get("items"), dict):
        result.add(prefix)
    return result


def asserted_leaves(value, prefix: tuple = ()) -> set[tuple]:
    if isinstance(value, dict):
        return set().union(*(asserted_leaves(item, (*prefix, key)) for key, item in value.items()))
    if isinstance(value, list):
        if not value:
            return {prefix}
        return set().union(*(asserted_leaves(item, (*prefix, "[]")) for item in value))
    return {prefix}


def tool_coverage(tool: dict, validation: dict | None) -> dict:
    tests = validation.get("tests", []) if validation else []
    successful = [test for test in tests if test.get("expect_success", True)]
    expected = [test["expected_data"] for test in successful if isinstance(test.get("expected_data"), dict)]
    asserted = set().union(*(asserted_leaves(item) for item in expected))
    declared = {path[1:] for path in schema_leaves(tool.get("outputSchema", {}))
                if path and path[0] == "data"}
    inputs = set(tool.get("inputSchema", {}).get("properties", {}))
    return {"tool": tool["name"], "old_validation_status": validation.get("status") if validation else None,
            "tool_source_sha256": hashlib.sha256(tool.get("internal", {}).get("code", "").encode()).hexdigest(),
            "successful_test_count": len(successful), "successful_tests_with_expected_data": len(expected),
            "asserted_data_leaf_paths": sorted(".".join(path) for path in asserted),
            "advertised_data_leaf_paths_without_assertions": sorted(".".join(path) for path in declared - asserted),
            "assertions_on_possible_input_echoes": sorted(".".join(path) for path in asserted
                                                          if path and path[0] in inputs),
            "independent_expected_value_provenance_verified": False,
            "tests_binding_to_current_tools_verified": False, "semantic_certification": "not_certified"}


def audit_manifest(manifest_path: Path) -> dict:
    manifest = read_json(manifest_path)
    entries = manifest["entries"]
    names = [entry["source_case"] for entry in entries]
    if len(names) != 20 or len(set(names)) != 20:
        raise ValueError("coverage audit must retain the complete frozen twenty")
    environments = {}
    for entry in entries:
        variant = Path(entry["variant_run"])
        bundle = read_json(variant / "intermediate/step_5_bundle.json")
        runtime = bundle["runtime"]
        tools_path = Path(runtime["tools_path"])
        binding_path = Path(runtime["binding_path"])
        tools_sha = hashlib.sha256(tools_path.read_bytes()).hexdigest()
        binding_sha = hashlib.sha256(binding_path.read_bytes()).hexdigest()
        identity = tools_sha + ":" + binding_sha
        if identity in environments:
            environments[identity]["source_cases"].append(entry["source_case"])
            continue
        validation_path = tools_path.parent / "tool_validation.json"
        validation = read_json(validation_path) if validation_path.is_file() else {"reports": []}
        reports = {report["tool"]: report for report in validation.get("reports", [])}
        tools = read_json(tools_path)["tools"]
        environments[identity] = {
            "source_cases": [entry["source_case"]], "environment_id": bundle["environment"]["environment_id"],
            "tools_path": str(tools_path), "tools_sha256": tools_sha,
            "binding_path": str(binding_path), "binding_sha256": binding_sha,
            "validation_path": str(validation_path) if validation_path.is_file() else None,
            "validation_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest()
            if validation_path.is_file() else None,
            "tools": [tool_coverage(tool, reports.get(tool["name"])) for tool in tools],
        }
    tools = [tool for environment in environments.values() for tool in environment["tools"]]
    return {"source_manifest": str(manifest_path),
            "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "source_count": 20, "environment_version_count": len(environments), "tool_count": len(tools),
            "tools_without_successful_value_assertions": sum(
                not tool["asserted_data_leaf_paths"] for tool in tools),
            "tools_with_unasserted_advertised_fields": sum(
                bool(tool["advertised_data_leaf_paths_without_assertions"]) for tool in tools),
            "semantic_certified_count": 0,
            "scope": "Static assertion inventory, not a tool correctness or solvability certificate",
            "environments": list(environments.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("tool coverage audit must not overwrite earlier evidence")
    report = audit_manifest(options.manifest)
    write_json(options.output, report)
    print({key: value for key, value in report.items() if key != "environments"})


if __name__ == "__main__":
    main()
