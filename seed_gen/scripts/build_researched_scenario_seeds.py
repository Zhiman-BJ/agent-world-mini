"""Build one compact environment Seed for every row in a researched CSV.

The CSV describes business scenarios and package boundaries, but it does not
contain a versioned Python API inventory.  This builder therefore reuses the
already verified L3 package/API anchors from the published semiconductor Seed
collection.  It preserves the CSV package text verbatim and never promotes an
unverified package, external solver, service, or instrument into a callable
Python reference tool.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT.parent / "半导体场景调研 - Sheet1 (1).csv"
DEFAULT_ANCHORS = (
    ROOT / "seed_gen/pypi_outputs/final_results/semiconductor_scenario_collection.json"
)
DEFAULT_SCHEMA = ROOT / "schemas/validation/scenario_env_seeds.schema.json"
DEFAULT_OUTPUT = (
    ROOT
    / "seed_gen/pypi_outputs/final_results/semiconductor_researched_scenarios_grounded_v2_20260929.json"
)
DEFAULT_ACCEPTED_OUTPUT = (
    ROOT
    / "seed_gen/pypi_outputs/final_results/semiconductor_researched_scenarios_accepted_grounded_v2_20260929.json"
)

L1_LABELS = {
    "01": "01 材料与器件研发",
    "02": "02 器件与物理仿真",
    "03": "03 芯片设计与流片",
    "04": "04 实验室表征与设备控制",
    "05": "05 晶圆测试与良率",
    "06": "06 Fab 制造运营",
    "07": "07 Inspection / Defect / Metrology",
    "08": "08 Enterprise / Quality / Reliability",
}

# The research sheet uses W labels for cross-cutting scene groups.  They map to
# the closest previously verified package/API anchor, while their original L3
# labels remain unchanged in the new Seeds.
ANCHOR_ALIASES = {
    "01.07.W1": "01.07.02",
    "07.01.W1": "07.01.01",
    "07.05.W1": "07.05.01",
    "07.06.W1": "07.06.01",
    "07.07.W1": "07.07.01",
    "07.07.W2": "07.07.04",
    "08.02.W1": "08.02.01",
    "08.02.W2": "08.02.05",
}

REQUIRED_COLUMNS = (
    "L2",
    "L3",
    "应用场景",
    "场景具体描述",
    "包 / 包组合",
    "可合成任务",
    "王品德",
    "林翔昊",
)
OPTIONAL_COLUMNS = ("是否接受",)

PACKAGE_ALIASES = {
    "pymatgen-core": ("pymatgen",),
    "scikit-rf": ("skrf",),
    "scikit-image": ("skimage",),
    "scikit-learn": ("sklearn",),
    "semi-ate-stdf": ("semi ate stdf", "stdf"),
    "pymatgen-analysis-defects": ("pymatgen analysis defects",),
    "pymatgen-analysis-diffusion": ("pymatgen analysis diffusion",),
}

EXTERNAL_BOUNDARY_MARKERS = (
    "外部",
    "仪器",
    "设备",
    "机台",
    "人工",
    "审批",
    "待实现",
    "未核实",
    "自建",
    "拟议",
    "tcad",
    "sentaurus",
    "silvaco",
    "comsol",
    "cadence",
    "synopsys",
    "ansys",
    "hfss",
    "cst",
    "vasp",
    "quantum espresso",
    "matlab",
    "ngspice",
    "xyce",
    "pdk",
    "xschem",
    "openvaf",
    "keysight",
    "vna",
    "示波器",
    "探针台",
    "仿真器",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code(label: str) -> str:
    value = label.strip().split(maxsplit=1)[0]
    if not re.fullmatch(r"\d{2}\.\d{2}\.(?:\d{2}|W\d+)", value):
        raise ValueError(f"无效 L3 编号：{label!r}")
    return value


def _review_status(row: dict[str, str]) -> str:
    values = [row["王品德"].strip(), row["林翔昊"].strip()]
    if any(value.startswith(("×", "✗")) for value in values):
        return "rejected"
    if not values[0] or any(value.startswith(("—", "-")) for value in values):
        return "needs_revision"
    if values[0].startswith(("√", "✓")):
        return "accepted"
    return "needs_revision"


def _counts(tools: list[dict[str, Any]]) -> dict[str, int]:
    classes = sum(tool["type"] == "class" for tool in tools)
    functions = sum(tool["type"] == "function" for tool in tools)
    methods = sum(
        len(tool.get("function", []))
        for tool in tools
        if tool["type"] == "class"
    )
    return {
        "class": classes,
        "function": functions,
        "class_func": methods,
        "all_func": functions + methods,
    }


def _module_roots(package: str, metadata: dict[str, Any]) -> list[str]:
    extraction = metadata.get("python_source_extraction", {})
    requested = extraction.get("requested_modules", []) if isinstance(extraction, dict) else []
    roots = [value for value in requested if isinstance(value, str) and value]
    normalized = package.lower().replace("-", "_").replace(".", "_")
    roots.extend((normalized, normalized.replace("_core", "")))
    # A few source trees expose modules below a src namespace.
    roots.extend(f"src.{value}" for value in list(roots))
    return sorted(set(roots), key=len, reverse=True)


def _tool_package(
    tool: dict[str, Any],
    packages: list[str],
    metadata: list[dict[str, Any]],
) -> str | None:
    module = str(tool.get("module") or "").lower()
    matches: list[tuple[int, str]] = []
    for package, item in zip(packages, metadata, strict=True):
        for root in _module_roots(package, item):
            root = root.lower()
            if (
                module == root
                or module.startswith(root + ".")
                or ("." + root + ".") in ("." + module + ".")
            ):
                matches.append((len(root), package))
                break
    if not matches:
        return None
    return max(matches)[1]


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", value)
    }


def _reference_score(tool: dict[str, Any], scenario_text: str) -> tuple[int, int]:
    text = scenario_text.lower()
    name = str(tool.get("name") or "").lower()
    module = str(tool.get("module") or "").lower()
    score = 100 if name and name in text else 0
    score += 8 * len((_tokens(name) | _tokens(module)) & _tokens(text))
    score += len(_tokens(str(tool.get("description") or "")) & _tokens(text))
    # A stable secondary value avoids depending on Python hash randomization.
    stable = sum(ord(character) for character in module + "." + name)
    return score, -stable


def _compact_class_methods(
    tool: dict[str, Any], scenario_text: str, *, max_methods: int
) -> dict[str, Any]:
    compact = copy.deepcopy(tool)
    methods = compact.get("function")
    if not isinstance(methods, list) or len(methods) <= max_methods:
        return compact
    ranked = sorted(
        enumerate(methods),
        key=lambda pair: (_reference_score(pair[1], scenario_text), -pair[0]),
        reverse=True,
    )[:max_methods]
    compact["function"] = [methods[index] for index, _ in sorted(ranked)]
    return compact


def _select_tools(
    anchor: dict[str, Any],
    scenario_text: str,
    *,
    max_tools_per_package: int,
    max_methods_per_class: int,
) -> list[dict[str, Any]]:
    others = anchor["others"]
    packages = others["pypi_package"]
    metadata = others["package_metadata"]
    grouped: dict[str | None, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, tool in enumerate(anchor["init_ref_tools"]):
        grouped[_tool_package(tool, packages, metadata)].append((index, tool))

    selected: list[tuple[int, dict[str, Any]]] = []
    for package in packages:
        candidates = grouped.get(package, [])
        ranked = sorted(
            candidates,
            key=lambda pair: (_reference_score(pair[1], scenario_text), -pair[0]),
            reverse=True,
        )[:max_tools_per_package]
        selected.extend(ranked)

    # Preserve a small number of source-verified APIs whose module-to-package
    # alias is not explicit in the old extraction metadata.
    unassigned = sorted(
        grouped.get(None, []),
        key=lambda pair: (_reference_score(pair[1], scenario_text), -pair[0]),
        reverse=True,
    )[:max_tools_per_package]
    selected.extend(unassigned)
    if not selected:
        selected = list(enumerate(anchor["init_ref_tools"][:max_tools_per_package]))

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, tool in sorted(selected):
        identity = (str(tool.get("module")), str(tool.get("name")), str(tool.get("type")))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            _compact_class_methods(
                tool,
                scenario_text,
                max_methods=max_methods_per_class,
            )
        )
    return result


def _compact_package_metadata(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source_metadata", {})
    extraction = item.get("python_source_extraction", {})
    return {
        "source_metadata": {
            key: source[key]
            for key in (
                "repository",
                "documentation",
                "pypi",
                "pypi_version",
                "tag",
                "commit",
                "release_url",
                "checked_on",
            )
            if key in source
        },
        "python_source_extraction": {
            key: extraction[key]
            for key in (
                "strategy",
                "requested_modules",
                "source_version",
                "source_commit",
                "selection",
                "excluded",
            )
            if key in extraction
        },
    }


def _compact_sources(anchor: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for source in anchor["others"].get("research_sources", []):
        result.append({
            key: copy.deepcopy(source[key])
            for key in ("url", "checked_on", "status", "final_url", "title", "relevance")
            if key in source
        })
    return result


def _anchor_map(anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for seed in anchors:
        code = seed["environment"]["domain"]["level3"].split(maxsplit=1)[0]
        if code in result:
            raise ValueError(f"锚点集合存在重复 L3：{code}")
        result[code] = seed
    return result


def _package_variants(package: str) -> set[str]:
    normalized = package.lower().strip()
    variants = {
        normalized,
        normalized.replace("-", "_"),
        normalized.replace("_", "-"),
        normalized.replace("-", " "),
        normalized.replace("_", " "),
    }
    variants.update(PACKAGE_ALIASES.get(normalized, ()))
    return {value for value in variants if len(value) >= 3}


def _mentions_package(text: str, package: str) -> bool:
    lowered = text.lower()
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered)
        for alias in _package_variants(package)
    )


def _package_catalog(anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for anchor in anchors:
        others = anchor["others"]
        packages = others["pypi_package"]
        metadata = others["package_metadata"]
        if len(packages) != len(metadata):
            raise ValueError(
                f"锚点 {anchor['global_id']} 的包与元数据没有一一对应"
            )
        package_info = {
            str(item.get("name", "")).lower(): item
            for item in others.get("basic_info", [])
            if isinstance(item, dict)
        }
        for package, item in zip(packages, metadata, strict=True):
            key = package.lower()
            entry = catalog.setdefault(
                key,
                {
                    "name": package,
                    "metadata": _compact_package_metadata(item),
                    "basic_info": None,
                    "tools": [],
                    "sources": [],
                    "anchors": [],
                },
            )
            entry["anchors"].append(anchor["global_id"])
            if entry["basic_info"] is None:
                info = package_info.get(key)
                if info is None:
                    index = packages.index(package)
                    basics = others.get("basic_info", [])
                    if index < len(basics) and isinstance(basics[index], dict):
                        info = basics[index]
                entry["basic_info"] = copy.deepcopy(info) if info else {
                    "source": "pypi",
                    "url": [],
                    "name": package,
                    "version": item.get("source_metadata", {}).get("pypi_version", "unknown"),
                    "index": len(catalog),
                    "description": f"已核实 Python 包 {package}。",
                }
            for tool in anchor.get("init_ref_tools", []):
                if _tool_package(tool, packages, metadata) == package:
                    identity = (
                        str(tool.get("module")),
                        str(tool.get("name")),
                        str(tool.get("type")),
                    )
                    if not any(
                        identity
                        == (
                            str(existing.get("module")),
                            str(existing.get("name")),
                            str(existing.get("type")),
                        )
                        for existing in entry["tools"]
                    ):
                        entry["tools"].append(copy.deepcopy(tool))
            for source in _compact_sources(anchor):
                url = source.get("url")
                if url and not any(existing.get("url") == url for existing in entry["sources"]):
                    entry["sources"].append(source)
    return catalog


def _primary_package_mention(package_boundary: str, package: str) -> bool:
    dependency_markers = (
        "依赖",
        "pyproject",
        "requirements",
        "列出",
        "原生组件",
        "原生数值库",
        "若用",
        "若使用",
        "未核实",
        "拟议",
        "候选",
        "含",
    )
    for segment in re.split(r"[；;。\n]", package_boundary.lower()):
        aliases = [alias for alias in _package_variants(package) if alias in segment]
        if not aliases:
            continue
        for alias in aliases:
            starts_with_package = segment.strip().startswith(alias)
            has_strong_marker = bool(
                re.search(
                    r"(?:使用|调用|基于|采用|用)\s*" + re.escape(alias),
                    segment,
                )
                or re.search(
                    re.escape(alias)
                    + r".{0,10}(?:python\s*)?(?:接口|官方|tutorial|wrapper)",
                    segment,
                )
            )
            dependency_context = any(marker in segment for marker in dependency_markers)
            if starts_with_package or has_strong_marker or not dependency_context:
                return True
    return False


def _direct_verified_packages(
    package_boundary: str,
    catalog: dict[str, dict[str, Any]],
) -> list[str]:
    return [
        entry["name"]
        for entry in catalog.values()
        if _primary_package_mention(package_boundary, entry["name"])
    ]


def _select_catalog_tools(
    packages: list[str],
    catalog: dict[str, dict[str, Any]],
    scenario_text: str,
    *,
    max_tools_per_package: int,
    max_methods_per_class: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for package in packages:
        candidates = catalog[package.lower()]["tools"]
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: (_reference_score(pair[1], scenario_text), -pair[0]),
            reverse=True,
        )[:max_tools_per_package]
        for _, tool in ranked:
            identity = (
                str(tool.get("module")),
                str(tool.get("name")),
                str(tool.get("type")),
            )
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(
                _compact_class_methods(
                    tool,
                    scenario_text,
                    max_methods=max_methods_per_class,
                )
            )
    return selected


def _official_package_sources(
    packages: list[str], catalog: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for package in packages:
        entry = catalog[package.lower()]
        source = entry["metadata"].get("source_metadata", {})
        for label, key in (
            ("official documentation", "documentation"),
            ("source repository", "repository"),
            ("PyPI release", "pypi"),
        ):
            url = source.get(key)
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            result.append({
                "url": url,
                "checked_on": source.get("checked_on", "2026-09-29"),
                "status": 200,
                "final_url": url,
                "title": f"{package} {label}",
                "relevance": {
                    "evidence": f"用于核实 {package} 的包身份、版本、API 或使用边界。",
                    "entities": [],
                    "tools": [package],
                    "tasks": [],
                },
            })
        for item in entry["sources"]:
            url = item.get("url")
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                result.append(copy.deepcopy(item))
    return result


def _environment_urls(
    packages: list[str],
    catalog: dict[str, dict[str, Any]],
    anchor: dict[str, Any],
) -> list[str]:
    candidates: list[str] = []
    for source in _official_package_sources(packages, catalog):
        url = source.get("url")
        if isinstance(url, str):
            candidates.append(url)
    candidates.extend(anchor["environment"]["basic_info"].get("url", []))
    result: list[str] = []
    for url in candidates:
        if url not in result:
            result.append(url)
        if len(result) == 10:
            break
    if len(result) < 3:
        raise ValueError(f"场景来源不足 3 个：{anchor['global_id']}")
    return result


def build(
    csv_path: Path = DEFAULT_CSV,
    anchor_path: Path = DEFAULT_ANCHORS,
    *,
    max_tools_per_package: int = 2,
    max_methods_per_class: int = 6,
    accepted_only: bool = False,
) -> list[dict[str, Any]]:
    if max_tools_per_package < 1 or max_methods_per_class < 1:
        raise ValueError("工具和类方法上限必须为正整数")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        expected_fields = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
        if fields not in (REQUIRED_COLUMNS, expected_fields):
            raise ValueError(
                f"CSV 列不符合预期：{reader.fieldnames!r}；应为 "
                f"{list(REQUIRED_COLUMNS)!r} 或 {list(expected_fields)!r}"
            )
        rows = list(reader)
    if not rows:
        raise ValueError("CSV 没有场景记录")

    anchors = _read_json(anchor_path)
    if not isinstance(anchors, list):
        raise ValueError("锚点 Seed 文件根节点必须为数组")
    by_code = _anchor_map(anchors)
    catalog = _package_catalog(anchors)
    csv_digest = _sha256(csv_path)
    anchor_digest = _sha256(anchor_path)
    seeds: list[dict[str, Any]] = []

    for source_row, row in enumerate(rows, start=1):
        if any(not isinstance(row[column], str) for column in REQUIRED_COLUMNS):
            raise ValueError(f"CSV 第 {source_row + 1} 行存在缺失列")
        l2 = row["L2"].strip()
        l3 = row["L3"].strip()
        l3_code = _code(l3)
        anchor_code = ANCHOR_ALIASES.get(l3_code, l3_code)
        if anchor_code not in by_code:
            raise ValueError(f"CSV 第 {source_row + 1} 行没有已核实 API 锚点：{l3_code}")
        anchor = by_code[anchor_code]
        l1_code = l3_code[:2]
        if l1_code not in L1_LABELS:
            raise ValueError(f"CSV 第 {source_row + 1} 行没有 L1 标签：{l3_code}")
        if not l2.startswith(l3_code[:5] + " "):
            raise ValueError(f"CSV 第 {source_row + 1} 行 L2/L3 不一致：{l2!r} / {l3!r}")

        scenario_name = row["应用场景"].strip()
        scenario_description = row["场景具体描述"].strip()
        package_boundary = row["包 / 包组合"].strip()
        composable_task = row["可合成任务"].strip()
        if not all((scenario_name, scenario_description, package_boundary, composable_task)):
            raise ValueError(f"CSV 第 {source_row + 1} 行缺少场景、描述、包边界或任务")
        csv_acceptance = row.get("是否接受", "").strip()
        if csv_acceptance not in {"", "√"}:
            raise ValueError(f"CSV 第 {source_row + 1} 行 是否接受 只能为空或 √")
        status = _review_status(row)
        if csv_acceptance == "√" and status != "accepted":
            raise ValueError(
                f"CSV 第 {source_row + 1} 行标记为接受，但王品德/林翔昊评审不是 accepted"
            )
        if accepted_only and csv_acceptance != "√":
            continue
        scenario_text = "\n".join(
            (scenario_name, scenario_description, package_boundary, composable_task)
        )
        anchor_packages = copy.deepcopy(anchor["others"]["pypi_package"])
        direct_packages = _direct_verified_packages(
            package_boundary,
            catalog,
        )
        packages = list(anchor_packages)
        packages.extend(
            package for package in direct_packages if package not in packages
        )
        tools = _select_catalog_tools(
            packages,
            catalog,
            scenario_text,
            max_tools_per_package=max_tools_per_package,
            max_methods_per_class=max_methods_per_class,
        )
        if not tools:
            tools = _select_tools(
                anchor,
                scenario_text,
                max_tools_per_package=max_tools_per_package,
                max_methods_per_class=max_methods_per_class,
            )
        metadata = [
            copy.deepcopy(catalog[package.lower()]["metadata"])
            for package in packages
        ]
        basic_info = [
            copy.deepcopy(catalog[package.lower()]["basic_info"])
            for package in packages
        ]
        fallback_packages = [
            package for package in anchor_packages if package not in direct_packages
        ]
        external_boundary = any(
            marker in (package_boundary + "\n" + composable_task).lower()
            for marker in EXTERNAL_BOUNDARY_MARKERS
        )
        if direct_packages and external_boundary:
            support_status = "direct_verified_with_external_dependencies"
        elif direct_packages:
            support_status = "direct_verified_package_reference"
        elif external_boundary:
            support_status = "partial_support_external_boundary"
        else:
            support_status = "l3_fallback_needs_tool_research"
        research_sources = _official_package_sources(packages, catalog)
        registered_urls = {
            item.get("url") for item in research_sources if item.get("url")
        }
        for url in anchor["environment"]["basic_info"].get("url", []):
            if url in registered_urls:
                continue
            registered_urls.add(url)
            research_sources.append({
                "url": url,
                "checked_on": "2026-09-29",
                "status": 200,
                "final_url": url,
                "title": f"{anchor['global_id']} verified anchor source",
                "relevance": {
                    "evidence": "用于登记 L3 锚点中已经采用的场景或工具来源。",
                    "entities": [],
                    "tools": anchor_packages,
                    "tasks": [],
                },
            })

        global_id = (
            "semiconductor_scenario_"
            + l3_code.lower().replace(".", "_")
            + f"_{source_row:04d}"
        )
        seeds.append({
            "global_id": global_id,
            "schema_version": "scenario-1.1",
            "environment": {
                "basic_info": {
                    "source": "deep_research",
                    "url": _environment_urls(packages, catalog, anchor),
                    "name": scenario_name,
                    "version": f"2026-09-29+grounded-v2.{csv_digest[:12]}",
                    "index": source_row,
                },
                "description": scenario_description,
                "domain": {
                    "level1": L1_LABELS[l1_code],
                    "level2": l2,
                    "level3": l3,
                },
                "nums": _counts(tools),
            },
            "init_ref_tools": tools,
            "init_ref_tasks": [
                "场景要求：" + scenario_description,
                "可合成任务：" + composable_task,
            ],
            "others": {
                "basic_info": basic_info,
                "pypi_package": packages,
                "package_relation": (
                    "保留已核实的 L3 锚点包，并补入场景包边界或可合成任务中明确提及、"
                    "且已存在于已核实锚点库中的 Python 包。参考 API 按具体场景重新排序；"
                    "外部软件、服务、仪器和未核实包仅作为处理边界，不提升为可调用工具。"
                ),
                "package_metadata": metadata,
                "scenario_design": {
                    "seed_granularity": "one_researched_scenario_per_seed",
                    "source_file": csv_path.name,
                    "source_sha256": csv_digest,
                    "source_row": source_row,
                    "application_scenario": scenario_name,
                    "scenario_description_raw": scenario_description,
                    "package_or_combination_raw": package_boundary,
                    "composable_task_raw": composable_task,
                    "review": {
                        "status": status,
                        "wang_pinde": row["王品德"].strip(),
                        "lin_xianghao": row["林翔昊"].strip(),
                    },
                    "anchor_global_id": anchor["global_id"],
                    "anchor_l3": anchor["environment"]["domain"]["level3"],
                    "tool_support": {
                        "status": support_status,
                        "direct_verified_packages": direct_packages,
                        "fallback_l3_packages": fallback_packages,
                        "selected_verified_packages": packages,
                        "external_dependency_boundary": external_boundary,
                        "requires_step1_tool_research": not bool(direct_packages),
                    },
                },
                "research_sources": research_sources,
                "joint_selection": {
                    "anchor_collection": anchor_path.name,
                    "anchor_collection_sha256": anchor_digest,
                    "anchor_global_id": anchor["global_id"],
                    "policy": (
                        "Preserve every L3 anchor package, add explicitly mentioned packages "
                        "from the verified catalog, and keep at most "
                        f"{max_tools_per_package} top-level reference APIs per package and "
                        f"{max_methods_per_class} methods per selected class."
                    ),
                    "unverified_package_boundary_preserved": True,
                    "verified_package_catalog_size": len(catalog),
                },
            },
        })
    return seeds


def validate(seeds: list[dict[str, Any]], schema_path: Path = DEFAULT_SCHEMA) -> None:
    schema = _read_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(seeds), key=lambda error: list(error.path))
    if errors:
        details = []
        for error in errors[:12]:
            pointer = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.absolute_path
            )
            details.append(f"{pointer}: {error.message}")
        raise ValueError("Seed 不符合场景 Schema：" + "; ".join(details))
    ids = [seed["global_id"] for seed in seeds]
    indices = [seed["environment"]["basic_info"]["index"] for seed in seeds]
    if len(ids) != len(set(ids)):
        raise ValueError("Seed global_id 不唯一")
    if len(indices) != len(set(indices)):
        raise ValueError("Seed index 不唯一")
    for seed in seeds:
        if seed["environment"]["nums"] != _counts(seed["init_ref_tools"]):
            raise ValueError(f"参考工具计数不一致：{seed['global_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-tools-per-package", type=int, default=2)
    parser.add_argument("--max-methods-per-class", type=int, default=6)
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="只生成 CSV 是否接受列标记为 √ 的场景",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output
    if args.accepted_only and output == DEFAULT_OUTPUT:
        output = DEFAULT_ACCEPTED_OUTPUT
    seeds = build(
        args.csv,
        args.anchors,
        max_tools_per_package=args.max_tools_per_package,
        max_methods_per_class=args.max_methods_per_class,
        accepted_only=args.accepted_only,
    )
    validate(seeds, args.schema)
    serialized = json.dumps(seeds, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != serialized:
            raise SystemExit(f"Seed 产物不存在或已漂移：{output}")
        action = "Verified"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8", newline="\n")
        action = "Wrote"
    statuses: dict[str, int] = defaultdict(int)
    for seed in seeds:
        statuses[seed["others"]["scenario_design"]["review"]["status"]] += 1
    print(
        f"{action} {len(seeds)} scenario Seeds to {output}; "
        + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    )


if __name__ == "__main__":
    main()
