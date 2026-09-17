"""Normalize the 2026-09-16 semiconductor scenario Seed collection.

The source artifact is retained as evidence.  This script emits a separate,
pipeline-valid collection and keeps every surviving API definition verbatim
apart from removing undocumented class methods.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "seed_gen/pypi_outputs/semiconductor_scenario_02_0916.json"
DEFAULT_OUTPUT = (
    ROOT
    / "seed_gen/pypi_outputs/semiconductor_scenario_02_0916_corrected_v1.1.json"
)

DOMAIN_IDS: dict[str, tuple[str, str]] = {
    "semiconductor_scenario_02_01_01": ("tcad", "poisson_drift_diffusion"),
    "semiconductor_scenario_02_01_02": ("tcad", "heterostructure_quantum_device"),
    "semiconductor_scenario_02_01_03": ("tcad", "solar_optoelectronic_device"),
    "semiconductor_scenario_02_02_01": ("carrier_transport", "mobility_scattering"),
    "semiconductor_scenario_02_03_01": ("mesh_pde", "geometry_construction"),
    "semiconductor_scenario_02_03_02": ("mesh_pde", "mesh_interchange"),
    "semiconductor_scenario_02_03_03": ("mesh_pde", "generic_fem_pde"),
    "semiconductor_scenario_02_04_01": ("spice_simulation", "netlist_execution"),
    "semiconductor_scenario_02_04_02": (
        "spice_simulation",
        "parameter_sweep_monte_carlo",
    ),
    "semiconductor_scenario_02_05_01": ("circuit_analysis", "symbolic_analysis"),
    "semiconductor_scenario_02_05_02": ("circuit_analysis", "design_optimization"),
    "semiconductor_scenario_02_06_01": (
        "rf_network_analysis",
        "s_parameter_network_analysis",
    ),
    "semiconductor_scenario_02_06_02": (
        "rf_network_analysis",
        "calibration_deembedding",
    ),
    "semiconductor_scenario_02_07_01": (
        "photonic_circuit_simulation",
        "circuit_level_s_matrix",
    ),
    "semiconductor_scenario_02_08_01": ("fdtd_simulation", "open_source_fdtd"),
    "semiconductor_scenario_02_08_02": ("fdtd_simulation", "cloud_fdtd"),
    "semiconductor_scenario_02_09_01": ("fem_mode_analysis", "eigenmode"),
    "semiconductor_scenario_02_09_02": ("fem_mode_analysis", "eme_propagation"),
    "semiconductor_scenario_02_10_01": (
        "photonic_inverse_design",
        "geometry_inverse_design",
    ),
    "semiconductor_scenario_02_11_01": (
        "process_physics",
        "plasma_etch_physics",
    ),
    "semiconductor_scenario_02_11_02": (
        "process_physics",
        "reaction_gas_chemistry",
    ),
    "semiconductor_scenario_02_11_03": (
        "process_physics",
        "diffusion_reaction_pde",
    ),
}

# These three source objects combined alternative packages.  The corrected
# scenarios retain only packages that participate in the stated workflow.
PACKAGE_ALLOWLISTS = {
    "semiconductor_scenario_02_11_01": {"plasmapy"},
    "semiconductor_scenario_02_11_02": {"cantera"},
    "semiconductor_scenario_02_11_03": {"fipy", "gmsh", "cantera"},
}

TOOL_DENYLISTS = {
    "semiconductor_scenario_02_02_01": {
        "mobilitypy.mobility.Plottings",
    },
}

# Mesh interchange needs import, structural inspection, quality checks and
# export.  Geometry construction and field configuration belong to 02.03.01.
TOOL_ALLOWLISTS = {
    "semiconductor_scenario_02_03_02": {
        "gmsh.initialize",
        "gmsh.open",
        "gmsh.write",
        "gmsh.finalize",
        "gmsh.model.getEntities",
        "gmsh.model.getPhysicalGroups",
        "gmsh.model.getEntitiesForPhysicalGroup",
        "gmsh.model.getBoundary",
        "gmsh.model.mesh.getNodes",
        "gmsh.model.mesh.getElements",
        "gmsh.model.mesh.getElementProperties",
        "gmsh.model.mesh.getElementQualities",
        "meshio._helpers.read",
        "meshio._helpers.write",
    },
}


def _qualified_name(tool: dict[str, Any]) -> str:
    module = str(tool.get("module") or "").strip()
    name = str(tool.get("name") or "").strip()
    return f"{module}.{name}" if module else name


def _package_for_tool(tool: dict[str, Any]) -> str:
    module = str(tool.get("module") or "").strip().lower()
    if module.startswith("plasmapy."):
        return "plasmapy"
    if module.startswith("cantera."):
        return "cantera"
    if module.startswith("fipy."):
        return "fipy"
    if module == "gmsh" or module.startswith("gmsh."):
        return "gmsh"
    return module.split(".", 1)[0]


def _complete_task_text(items: Any) -> str:
    parts: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("description") or item.get("name") or "").strip()
        else:
            text = ""
        if text:
            parts.append(text)
    if not parts:
        raise ValueError("每条场景必须包含非空参考任务")
    task = "".join(
        part if index == 0 or parts[index - 1].endswith(("；", "。", ";", "."))
        else "；" + part
        for index, part in enumerate(parts)
    )
    if task[-1] not in "。.!?！？":
        task += "。"
    return task


def _filter_package_metadata(others: dict[str, Any], allowed: set[str]) -> None:
    packages = others.get("pypi_package", [])
    basic_info = others.get("basic_info", [])
    metadata = others.get("package_metadata", [])
    if not isinstance(packages, list):
        return
    keep = [
        index
        for index, package in enumerate(packages)
        if str(package).strip().lower() in allowed
    ]
    others["pypi_package"] = [packages[index] for index in keep]
    if isinstance(basic_info, list) and len(basic_info) == len(packages):
        others["basic_info"] = [basic_info[index] for index in keep]
    if isinstance(metadata, list) and len(metadata) == len(packages):
        others["package_metadata"] = [metadata[index] for index in keep]


def _filter_tools(seed: dict[str, Any], original_id: str) -> list[dict[str, Any]]:
    allowed_packages = PACKAGE_ALLOWLISTS.get(original_id)
    allowlist = TOOL_ALLOWLISTS.get(original_id)
    denylist = TOOL_DENYLISTS.get(original_id, set())
    filtered: list[dict[str, Any]] = []
    for source_tool in seed.get("init_ref_tools", []):
        if not isinstance(source_tool, dict):
            continue
        tool = deepcopy(source_tool)
        qualified = _qualified_name(tool)
        if not str(tool.get("description") or "").strip():
            continue
        if allowed_packages is not None and _package_for_tool(tool) not in allowed_packages:
            continue
        if allowlist is not None and qualified not in allowlist:
            continue
        if qualified in denylist:
            continue
        if tool.get("type") == "class":
            tool["function"] = [
                method
                for method in tool.get("function", [])
                if isinstance(method, dict)
                and str(method.get("description") or "").strip()
            ]
        filtered.append(tool)
    if not filtered:
        raise ValueError(f"{original_id} 在筛选后没有参考工具")
    return filtered


def _tool_counts(tools: list[dict[str, Any]]) -> dict[str, int]:
    classes = sum(tool.get("type") == "class" for tool in tools)
    functions = sum(tool.get("type") == "function" for tool in tools)
    class_functions = sum(
        len(tool.get("function", []))
        for tool in tools
        if tool.get("type") == "class"
    )
    return {
        "class": classes,
        "function": functions,
        "class_func": class_functions,
        "all_func": functions + class_functions,
    }


def correct(source: list[Any]) -> list[dict[str, Any]]:
    if len(source) != len(DOMAIN_IDS):
        raise ValueError(f"预期 {len(DOMAIN_IDS)} 条场景，实际 {len(source)} 条")
    corrected: list[dict[str, Any]] = []
    seen_original_ids: set[str] = set()
    for raw_seed in source:
        if not isinstance(raw_seed, dict):
            raise ValueError("Seed 集合只能包含对象")
        seed = deepcopy(raw_seed)
        original_id = str(seed.get("global_id") or "")
        if original_id not in DOMAIN_IDS:
            raise ValueError(f"未知场景：{original_id}")
        if original_id in seen_original_ids:
            raise ValueError(f"重复场景：{original_id}")
        seen_original_ids.add(original_id)

        level2, level3 = DOMAIN_IDS[original_id]
        environment = seed["environment"]
        old_domain = deepcopy(environment["domain"])
        old_name = str(environment["basic_info"]["name"])
        environment["basic_info"].update({
            "source": "semiconductor",
            "name": level3,
            "index": 1,
        })
        environment["domain"] = {
            "level1": "semiconductor_device_physics",
            "level2": level2,
            "level3": level3,
        }
        seed["global_id"] = f"semiconductor_{level3}_1"
        seed["init_ref_tasks"] = [_complete_task_text(seed.get("init_ref_tasks"))]
        seed["init_ref_tools"] = _filter_tools(seed, original_id)
        environment["nums"] = _tool_counts(seed["init_ref_tools"])

        others = seed["others"]
        others["original_global_id"] = original_id
        others["scenario_name"] = old_name
        others["domain_labels"] = old_domain
        others["correction"] = {
            "version": "1.1",
            "task_representation": "single_complete_text",
            "tool_policy": "documented_core_workflow_apis",
        }
        if original_id in PACKAGE_ALLOWLISTS:
            _filter_package_metadata(others, PACKAGE_ALLOWLISTS[original_id])
        corrected.append(seed)

    missing = set(DOMAIN_IDS) - seen_original_ids
    if missing:
        raise ValueError(f"缺少场景：{sorted(missing)}")
    return corrected


def build(input_path: Path = DEFAULT_INPUT, output_path: Path = DEFAULT_OUTPUT) -> list[dict[str, Any]]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("输入 Seed 文件根节点必须是数组")
    corrected = correct(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(corrected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return corrected


def main() -> None:
    parser = argparse.ArgumentParser(description="纠正半导体场景 Seed 集合")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    corrected = build(arguments.input.resolve(), arguments.output.resolve())
    print(json.dumps({
        "output": str(arguments.output.resolve()),
        "seed_count": len(corrected),
        "top_level_tool_count": sum(len(item["init_ref_tools"]) for item in corrected),
        "all_func_count": sum(item["environment"]["nums"]["all_func"] for item in corrected),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
