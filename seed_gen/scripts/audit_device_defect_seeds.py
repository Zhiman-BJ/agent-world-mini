"""Audit the collected release seeds without importing or executing target APIs."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.scripts.extract_release_python_seeds import git
from seed_gen.scripts.select_python_ref_tools import select_seed


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def counts(tools):
    classes = [t for t in tools if t["type"] == "class"]
    functions = sum(t["type"] == "function" for t in tools)
    methods = sum(len(t["function"]) for t in classes)
    return {"class": len(classes), "function": functions, "class_func": methods, "all_func": functions + methods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    validator = Draft202012Validator(read("schemas/validation/env_seeds.schema.json"), format_checker=FormatChecker())
    specs = read("seed_gen/pypi_device_defect_sources.json")
    results = []
    for spec in specs:
        filename = f"{spec['name']}_{spec['tag']}.json"
        root = Path("seed_pypi_raw") / spec["directory"]
        source_path = Path("seed_gen/pypi_outputs/ori_all") / filename
        profile_path = Path("seed_gen/pypi_selection_profiles") / filename
        selected_path = Path("seed_gen/pypi_outputs") / filename
        report_path = Path("seed_gen/pypi_outputs/selection_reports") / filename
        raw, selected, profile = read(source_path), read(selected_path), read(profile_path)
        assert git(root, "rev-parse", "HEAD") == spec["commit"]
        assert git(root, "rev-parse", f"{spec['tag']}^{{commit}}") == spec["commit"]
        assert git(root, "remote", "get-url", "origin").removesuffix(".git").lower() == spec["repository"].lower()
        assert not git(root, "status", "--porcelain", "--untracked-files=no")
        regenerated, report = select_seed(raw, profile, input_label=source_path.as_posix(), profile_label=profile_path.as_posix())
        assert regenerated == selected and report == read(report_path)
        for cap in profile["capabilities"]:
            assert cap["verification_tier"].endswith(("planned", "required"))
            for evidence in cap.get("evidence_sources", []):
                assert Path(evidence).is_file(), evidence
        raw_index = {(t["module"], t["name"], t["type"]): t for t in raw[0]["init_ref_tools"]}
        for tool in selected[0]["init_ref_tools"]:
            original = raw_index[tool["module"], tool["name"], tool["type"]]
            if tool["type"] == "class":
                assert {k: v for k, v in tool.items() if k != "function"} == {k: v for k, v in original.items() if k != "function"}
                assert len({m["name"] for m in tool["function"]}) == len(tool["function"])
                for method in tool["function"]:
                    assert method in original["function"]
            else:
                assert tool == original
        for payload in (raw, selected):
            seed = payload[0]
            assert seed["environment"]["nums"] == counts(seed["init_ref_tools"])
            assert seed["global_id"] == f"pypi_{re.sub(r'[^a-z0-9]+', '_', spec['name'].lower()).strip('_')}_{spec['index']}"
        schema_results = {}
        for label, payload in (("raw", raw), ("selected", selected)):
            errors = list(validator.iter_errors(payload))
            unexpected = [e for e in errors if not (e.validator == "minLength" and list(e.path)[-1] == "description")]
            assert not unexpected, [(list(e.path), e.message) for e in unexpected]
            schema_results[label] = {
                "passes_strict_schema": not errors,
                "empty_description_violations": len(errors),
                "violations": [{"path": list(e.path), "message": e.message, "validator": e.validator} for e in errors],
            }
        summary = report["summary"]
        assert summary["selected_tool_count"] + summary["excluded_tool_count"] == summary["raw_tool_count"]
        counted_methods = sum(len(s["selected_methods"]) + len(s["excluded_methods"]) for s in report["selected_symbols"])
        counted_methods += sum(s["available_method_count"] for s in report["excluded_symbols"])
        assert counted_methods == raw[0]["environment"]["nums"]["class_func"]
        results.append({"package": spec["name"], "version": spec["tag"], "commit": spec["commit"],
                        "raw_counts": raw[0]["environment"]["nums"], "selected_counts": selected[0]["environment"]["nums"],
                        "source_and_subset_checks": "passed", "schema": schema_results,
                        "size_exception": profile.get("target_exception_reason", ""), "runtime_verified": False})
        print(spec["name"], selected[0]["environment"]["nums"], "empty descriptions:", schema_results["selected"]["empty_description_violations"])
    # Collection-level identity audit includes existing selected packages.
    ids, indexes = [], []
    for path in Path("seed_gen/pypi_outputs").glob("*.json"):
        payload = read(path)
        if isinstance(payload, list):
            for seed in payload:
                ids.append(seed["global_id"])
                indexes.append(seed["environment"]["basic_info"]["index"])
    assert all(n == 1 for n in Counter(ids).values())
    assert all(n == 1 for n in Counter(indexes).values())
    artifact = {"checked_on": "2026-09-10", "manifest": "seed_gen/pypi_device_defect_sources.json",
                "collection_identity_checks": "passed", "runtime_verified": False, "packages": results}
    destination = Path("seed_gen/pypi_outputs/selection_reports/device_defect_validation.json")
    if args.check:
        assert read(destination) == artifact
    else:
        destination.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
