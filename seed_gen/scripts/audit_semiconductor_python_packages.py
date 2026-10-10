"""Audit goal-table Python package collection and generate coverage reports."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.scripts.extract_release_python_seeds import git
from seed_gen.scripts.select_python_ref_tools import select_seed


ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = ROOT / "seed_gen/pypi_semiconductor_scope.json"
UNRESOLVED_PATH = ROOT / "seed_gen/pypi_unresolved_sources.json"
MANIFEST_PATHS = [
    ROOT / "seed_gen/pypi_release_sources.json",
    ROOT / "seed_gen/pypi_device_defect_sources.json",
    ROOT / "seed_gen/pypi_materials_sources.json",
    ROOT / "seed_gen/pypi_semiconductor_physics_sources.json",
    ROOT / "seed_gen/pypi_circuit_photonic_sources.json",
    ROOT / "seed_gen/pypi_photonic_solver_sources.json",
    ROOT / "seed_gen/pypi_legacy_circuit_inverse_sources.json",
]
REPORT_PATH = ROOT / "seed_gen/pypi_outputs/semiconductor_backend_coverage_20260915.json"
MARKDOWN_PATH = ROOT / "seed_gen/pypi_outputs/半导体Python包覆盖报告.md"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_name(name: str) -> str:
    return name.casefold()


def counts(tools: list[dict]) -> dict[str, int]:
    classes = [tool for tool in tools if tool["type"] == "class"]
    functions = sum(tool["type"] == "function" for tool in tools)
    methods = sum(len(tool["function"]) for tool in classes)
    return {
        "class": len(classes),
        "function": functions,
        "class_func": methods,
        "all_func": functions + methods,
    }


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def normalized_remote(url: str) -> str:
    return url.removesuffix(".git").rstrip("/").casefold()


def schema_summary(validator: Draft202012Validator, payload: list[dict]) -> dict:
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    expected = [
        error
        for error in errors
        if error.validator == "minLength"
        and list(error.absolute_path)[-1:] == ["description"]
        and error.instance == ""
    ]
    unexpected = [error for error in errors if error not in expected]
    return {
        "passes_strict_schema": not errors,
        "error_count": len(errors),
        "empty_source_description_count": len(expected),
        "unexpected_error_count": len(unexpected),
        "unexpected_errors": [
            {"path": list(error.absolute_path), "message": error.message, "validator": error.validator}
            for error in unexpected
        ],
    }


def audit_package(spec: dict, validator: Draft202012Validator, general_target: dict) -> dict:
    filename = f"{spec['name']}_{spec['tag']}.json"
    source_root = ROOT / "seed_pypi_raw" / spec["directory"]
    raw_path = ROOT / "seed_gen/pypi_outputs/ori_all" / filename
    profile_path = ROOT / "seed_gen/pypi_selection_profiles" / filename
    selected_path = ROOT / "seed_gen/pypi_outputs" / filename
    selection_report_path = ROOT / "seed_gen/pypi_outputs/selection_reports" / filename
    for path in (source_root, raw_path, profile_path, selected_path, selection_report_path):
        if not path.exists():
            raise AssertionError(f"Missing required artifact: {path}")

    raw = read(raw_path)
    profile = read(profile_path)
    selected = read(selected_path)
    saved_selection_report = read(selection_report_path)
    release_ref = spec.get("ref", spec["tag"])
    commit = git(source_root, "rev-parse", "HEAD")
    assert commit == spec["commit"], (spec["name"], commit, spec["commit"])
    assert git(source_root, "rev-parse", f"{release_ref}^{{commit}}") == commit
    assert normalized_remote(git(source_root, "remote", "get-url", "origin")) == normalized_remote(spec["repository"])
    assert not git(source_root, "status", "--porcelain")

    regenerated, selection_report = select_seed(
        raw,
        profile,
        input_label=relative(raw_path),
        profile_label=relative(profile_path),
    )
    assert regenerated == selected
    assert selection_report == saved_selection_report
    for payload in (raw, selected):
        assert payload[0]["environment"]["nums"] == counts(payload[0]["init_ref_tools"])

    raw_index = {(tool["module"], tool["name"], tool["type"]): tool for tool in raw[0]["init_ref_tools"]}
    for tool in selected[0]["init_ref_tools"]:
        original = raw_index[(tool["module"], tool["name"], tool["type"])]
        if tool["type"] == "function":
            assert tool == original
            continue
        assert {key: value for key, value in tool.items() if key != "function"} == {
            key: value for key, value in original.items() if key != "function"
        }
        assert all(method in original["function"] for method in tool["function"])

    schema = schema_summary(validator, selected)
    assert not schema["unexpected_errors"], (spec["name"], schema["unexpected_errors"])
    selected_counts = selected[0]["environment"]["nums"]
    target = profile["target_all_func"]
    within_target = target["min"] <= selected_counts["all_func"] <= target["max"]
    within_general_target = (
        general_target["min_all_func"]
        <= selected_counts["all_func"]
        <= general_target["max_all_func"]
    )
    if not within_target:
        assert profile.get("target_exception_reason")
    if not within_general_target:
        assert profile.get("target_exception_reason")
    return {
        "package": spec["name"],
        "version": spec["tag"],
        "commit": commit,
        "repository": spec["repository"],
        "documentation": spec["documentation"],
        "source_directory": relative(source_root),
        "artifacts": {
            "raw": relative(raw_path),
            "profile": relative(profile_path),
            "selected": relative(selected_path),
            "selection_report": relative(selection_report_path),
        },
        "raw_counts": raw[0]["environment"]["nums"],
        "selected_counts": selected_counts,
        "selection_target": target,
        "within_selection_target": within_target,
        "within_general_selection_target": within_general_target,
        "selection_target_exception": profile.get("target_exception_reason", ""),
        "source_identity": "passed",
        "selection_reproducibility": "passed",
        "selected_schema": schema,
        "runtime_verified": False,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# 半导体 Python 包覆盖报告",
        "",
        f"核查日期：{report['checked_on']}。本报告由 `seed_gen.scripts.audit_semiconductor_python_packages` 从目标范围、来源清单、发布源码、全量索引、筛选 profile 和精选产物重算生成。",
        "",
        "## 结论",
        "",
        f"目标表包含 {summary['scenario_count']} 个 L3 场景和 {summary['backend_count']} 个不同 backend；其中 {summary['collected_python_package_count']} 个已完成发布版源码、全量 API、显式筛选 profile、精选 JSON 和筛选报告，{summary['unresolved_or_external_count']} 个按边界记录。全部 L3 场景均有已采集 Python 包或明确的外部边界。",
        "",
        f"{summary['source_identity_passed_count']} 个 Python 包的 remote、发布 ref、HEAD 和干净工作区检查通过；{summary['selection_reproducibility_passed_count']} 个精选结果可从全量 JSON 与 profile 原样重算。严格 Schema 通过 {summary['strict_schema_passed_count']}/{summary['collected_python_package_count']}；其余仅包含源码缺失 docstring 导致的空 `description`，共 {summary['empty_source_description_count']} 项，未补造文本。",
        "",
        f"精选数量通常为 50–100；低于 50 的有据例外为：{'、'.join(summary['packages_below_general_target']) or '无'}。",
        "",
        "## 场景覆盖",
        "",
        "| L2 | L3 | 应用场景 | 场景具体描述 | 包 / 包组合 | 关系 | 可合成任务 |",
        "| :---: | --- | --- | --- | --- | --- | --- |",
    ]
    previous_l2 = None
    for scenario in report["scenarios"]:
        l2 = scenario["l2"] if scenario["l2"] != previous_l2 else ""
        lines.append(
            f"| {l2} | {scenario['l3']} | {scenario['application']} | {scenario['scenario_description']} | "
            f"{scenario['package_combination']} | "
            f"{scenario['relationship'] or '-'} | {scenario['task_design']} |"
        )
        previous_l2 = scenario["l2"]
    lines.extend([
        "",
        "## 包级计数",
        "",
        "| 包 | 版本 | 全量 class/function/class_func/all_func | 精选 class/function/class_func/all_func | Schema |",
        "| --- | --- | ---: | ---: | --- |",
    ])
    for package in report["packages"]:
        raw = package["raw_counts"]
        selected = package["selected_counts"]
        schema = package["selected_schema"]
        schema_text = "通过" if schema["passes_strict_schema"] else f"空 description {schema['empty_source_description_count']}"
        lines.append(
            f"| {package['package']} | {package['version']} | "
            f"{raw['class']}/{raw['function']}/{raw['class_func']}/{raw['all_func']} | "
            f"{selected['class']}/{selected['function']}/{selected['class_func']}/{selected['all_func']} | {schema_text} |"
        )
    lines.extend([
        "",
        "## 未采集边界",
        "",
    ])
    for item in report["unresolved_or_external"]:
        lines.append(f"- `{item['name']}`：{item['reason']}")
    lines.extend([
        "",
        "## 验证边界",
        "",
        "本报告验证的是官方发布来源、源码身份、静态 API 提取、精选子集和数量一致性。外部求解器、许可、云服务、原生库编译、数值精度和端到端科研结果均未由此次采集验证。表中的任务是后续任务合成方向，不代表现有任务已执行通过。",
        "",
    ])
    return "\n".join(lines)


def build_report() -> dict:
    scope = read(SCOPE_PATH)
    unresolved = {canonical_name(item["name"]): item for item in read(UNRESOLVED_PATH)}
    specs: dict[str, dict] = {}
    manifest_by_package: dict[str, str] = {}
    for manifest_path in MANIFEST_PATHS:
        for spec in read(manifest_path):
            key = canonical_name(spec["name"])
            assert key not in specs, f"Duplicate package manifest entry: {spec['name']}"
            specs[key] = spec
            manifest_by_package[key] = relative(manifest_path)

    requested = {
        canonical_name(name)
        for scenario in scope["scenarios"]
        for name in scenario["python_backends"]
    }
    missing = requested - specs.keys() - unresolved.keys()
    assert not missing, f"Backends missing from manifests and unresolved list: {sorted(missing)}"
    goal_specs = {key: specs[key] for key in requested & specs.keys()}
    schema = read(ROOT / "schemas/validation/env_seeds.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    general_target = scope["general_selection_target"]
    packages = [audit_package(spec, validator, general_target) for spec in goal_specs.values()]
    packages.sort(key=lambda item: item["package"].casefold())
    package_results = {canonical_name(item["package"]): item for item in packages}

    scenarios = []
    for scenario in scope["scenarios"]:
        backends = []
        for name in scenario["python_backends"]:
            key = canonical_name(name)
            if key in package_results:
                package = package_results[key]
                backends.append({
                    "name": name,
                    "status": "collected",
                    "version": package["version"],
                    "selected_all_func": package["selected_counts"]["all_func"],
                    "manifest": manifest_by_package[key],
                })
            else:
                boundary = unresolved[key]
                backends.append({
                    "name": name,
                    "status": boundary["status"],
                    "reason": boundary["reason"],
                })
        scenarios.append({**scenario, "backends": backends})

    unresolved_in_scope = [unresolved[key] for key in sorted(requested & unresolved.keys())]
    strict_count = sum(item["selected_schema"]["passes_strict_schema"] for item in packages)
    empty_descriptions = sum(item["selected_schema"]["empty_source_description_count"] for item in packages)
    out_of_target = [item["package"] for item in packages if not item["within_selection_target"]]
    below_general_target = [
        item["package"]
        for item in packages
        if item["selected_counts"]["all_func"] < general_target["min_all_func"]
    ]
    above_general_target = [
        item["package"]
        for item in packages
        if item["selected_counts"]["all_func"] > general_target["max_all_func"]
    ]
    report = {
        "schema_version": "1.0",
        "checked_on": scope["checked_on"],
        "scope": relative(SCOPE_PATH),
        "source_manifests": [relative(path) for path in MANIFEST_PATHS],
        "unresolved_manifest": relative(UNRESOLVED_PATH),
        "summary": {
            "scenario_count": len(scenarios),
            "backend_count": len(requested),
            "collected_python_package_count": len(packages),
            "unresolved_or_external_count": len(unresolved_in_scope),
            "covered_scenario_count": sum(bool(item["backends"]) for item in scenarios),
            "source_identity_passed_count": sum(item["source_identity"] == "passed" for item in packages),
            "selection_reproducibility_passed_count": sum(
                item["selection_reproducibility"] == "passed" for item in packages
            ),
            "strict_schema_passed_count": strict_count,
            "empty_source_description_count": empty_descriptions,
            "unexpected_schema_error_count": sum(
                item["selected_schema"]["unexpected_error_count"] for item in packages
            ),
            "packages_outside_profile_target": out_of_target,
            "packages_below_general_target": below_general_target,
            "packages_above_general_target": above_general_target,
            "runtime_verified_package_count": sum(item["runtime_verified"] for item in packages),
        },
        "scenarios": scenarios,
        "packages": packages,
        "unresolved_or_external": unresolved_in_scope,
        "runtime_verification_boundary": "Static collection only. External solvers, licenses, cloud services, native builds, numerical accuracy, and end-to-end scientific workflows were not executed.",
    }
    assert report["summary"]["covered_scenario_count"] == report["summary"]["scenario_count"]
    assert report["summary"]["unexpected_schema_error_count"] == 0
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare committed reports without writing")
    args = parser.parse_args()
    report = build_report()
    markdown = render_markdown(report)
    if args.check:
        assert read(REPORT_PATH) == report, f"Coverage report differs: {relative(REPORT_PATH)}"
        assert MARKDOWN_PATH.read_text(encoding="utf-8") == markdown, f"Markdown report differs: {relative(MARKDOWN_PATH)}"
    else:
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        MARKDOWN_PATH.write_text(markdown, encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
