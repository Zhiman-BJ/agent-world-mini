#!/usr/bin/env python3
"""Generate two protocol-compatible PyPI Seeds for semiconductor applications."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "pypi_outputs"
DEFAULT_COMBINED_OUTPUT = OUTPUT_DIR / "semiconductor_application_seeds_v1_20260909.json"


PROFILES: dict[str, dict[str, Any]] = {
    "atomate2": {
        "new_index": 3,
        "output_name": "atomate2_semiconductor_v0.1.5.json",
        "domain": {
            "level1": "semiconductor",
            "level2": "computational_materials",
            "level3": "workflow_automation",
        },
        "description": (
            "这是以 atomate2 为核心的半导体第一性原理计算工作流环境，重点复现公开光伏筛选和带电缺陷项目中的实际用法。"
            "研究人员从 CIF、Materials Project 标识或已有 pymatgen Structure 取得一批候选半导体，用 atomate2 Maker "
            "批量创建 jobflow Job 和 Flow，完成 GGA/HSE 结构弛豫、静态、能带、态密度、介电与光学计算；缺陷研究还会为"
            "母体、超胞及不同缺陷和电荷态组织 relax/static/HSE 链，保存 LOCPOT、CHGCAR、WAVECAR、校正输入和形成能结果。"
            "工作流通过 powerup 调整 INCAR、KPOINTS、赝势类型和元数据，由 custodian 处理 VASP 错误，再交给 FireWorks "
            "或 jobflow-remote 调度，并将 TaskDocument、目录、状态和依赖写入数据存储。具有完整电子结构、介电、弹性及"
            "形变势数据时，可继续用 atomate2 的 AMSET Flow 评估半导体载流子输运。环境必须保留同一研究项目中的候选表、"
            "结构、Maker 配置、Job/Flow、各阶段文件和结果文档之间的稳定关联。VASP 可执行程序、受许可约束的赝势、真实"
            "集群和重新执行昂贵计算不属于离线能力。"
        ),
        "module_symbols": {
            "atomate2.amset.jobs": ("AmsetMaker",),
            "atomate2.amset.schemas": (
                "TransportData",
                "MeshData",
                "AmsetTaskDocument",
            ),
            "atomate2.common.flows.defect": (
                "ConfigurationCoordinateMaker",
                "FormationEnergyMaker",
            ),
            "atomate2.common.flows.elastic": ("BaseElasticMaker",),
            "atomate2.common.jobs.defect": (
                "get_charged_structures",
                "get_ccd_documents",
                "bulk_supercell_calculation",
                "spawn_defect_q_jobs",
                "check_charge_state",
                "get_defect_entry",
            ),
            "atomate2.common.jobs.elastic": (
                "generate_elastic_deformations",
                "run_elastic_deformations",
                "fit_elastic_tensor",
            ),
            "atomate2.common.jobs.utils": (
                "structure_to_primitive",
                "structure_to_conventional",
                "retrieve_structure_from_materials_project",
            ),
            "atomate2.common.schemas.defects": (
                "FormationEnergyDiagramDocument",
                "CCDDocument",
            ),
            "atomate2.common.schemas.elastic": ("ElasticDocument",),
            "atomate2.vasp.builders.elastic": ("ElasticBuilder",),
            "atomate2.vasp.drones": ("VaspDrone",),
            "atomate2.vasp.files": ("copy_vasp_outputs", "write_vasp_input_set"),
            "atomate2.vasp.flows.amset": (
                "DeformationPotentialMaker",
                "VaspAmsetMaker",
                "HSEVaspAmsetMaker",
            ),
            "atomate2.vasp.flows.core": (
                "DoubleRelaxMaker",
                "BandStructureMaker",
                "UniformBandStructureMaker",
                "LineModeBandStructureMaker",
                "HSEBandStructureMaker",
                "HSEUniformBandStructureMaker",
                "HSELineModeBandStructureMaker",
                "RelaxBandStructureMaker",
                "OpticsMaker",
                "HSEOpticsMaker",
            ),
            "atomate2.vasp.flows.defect": (
                "FormationEnergyMaker",
                "ConfigurationCoordinateMaker",
                "NonRadiativeMaker",
            ),
            "atomate2.vasp.flows.elastic": ("ElasticMaker",),
            "atomate2.vasp.flows.mp": (
                "MPGGADoubleRelaxMaker",
                "MPMetaGGADoubleRelaxMaker",
                "MPGGADoubleRelaxStaticMaker",
                "MPMetaGGADoubleRelaxStaticMaker",
                "MP24DoubleRelaxMaker",
                "MP24DoubleRelaxStaticMaker",
            ),
            "atomate2.vasp.jobs.amset": (
                "DenseUniformMaker",
                "StaticDeformationMaker",
                "HSEStaticDeformationMaker",
                "HSEDenseUniformMaker",
                "run_amset_deformations",
                "calculate_deformation_potentials",
                "calculate_polar_phonon_frequency",
                "generate_wavefunction_coefficients",
            ),
            "atomate2.vasp.jobs.base": ("BaseVaspMaker", "get_vasp_task_document"),
            "atomate2.vasp.jobs.core": (
                "StaticMaker",
                "RelaxMaker",
                "RelaxConstVolMaker",
                "TightRelaxMaker",
                "TightRelaxConstVolMaker",
                "TightConstVolRelaxMaker",
                "NonSCFMaker",
                "HSERelaxMaker",
                "HSETightRelaxMaker",
                "HSEStaticMaker",
                "HSEBSMaker",
                "DielectricMaker",
                "PolarizationMaker",
                "TransmuterMaker",
            ),
            "atomate2.vasp.jobs.defect": ("calculate_finite_diff",),
            "atomate2.vasp.jobs.elastic": ("ElasticRelaxMaker",),
            "atomate2.vasp.jobs.mp": (
                "MPGGARelaxMaker",
                "MPGGAStaticMaker",
                "MPPreRelaxMaker",
                "MPMetaGGARelaxMaker",
                "MPMetaGGAStaticMaker",
                "MP24PreRelaxMaker",
                "MP24RelaxMaker",
                "MP24StaticMaker",
            ),
            "atomate2.vasp.powerups": (
                "update_vasp_input_generators",
                "update_user_incar_settings",
                "update_user_potcar_settings",
                "update_user_potcar_functional",
                "update_user_kpoints_settings",
                "use_auto_ispin",
                "add_metadata_to_flow",
                "update_vasp_custodian_handlers",
            ),
            "atomate2.vasp.run": ("JobType", "run_vasp", "should_stop_children"),
            "atomate2.vasp.schemas.defect": ("FiniteDifferenceDocument",),
            "atomate2.vasp.sets.base": ("VaspInputGenerator",),
            "atomate2.vasp.sets.core": (
                "RelaxSetGenerator",
                "RelaxConstVolSetGenerator",
                "TightRelaxSetGenerator",
                "TightRelaxConstVolSetGenerator",
                "StaticSetGenerator",
                "NonSCFSetGenerator",
                "HSERelaxSetGenerator",
                "HSETightRelaxSetGenerator",
                "HSEStaticSetGenerator",
                "HSEBSSetGenerator",
            ),
            "atomate2.vasp.sets.defect": (
                "ChargeStateRelaxSetGenerator",
                "ChargeStateStaticSetGenerator",
                "HSEChargeStateRelaxSetGenerator",
                "HSEChargeStateStaticSetGenerator",
            ),
        },
    },
    "pymatgen-core": {
        "new_index": 2,
        "output_name": "pymatgen-core_semiconductor_v2026.8.30.json",
        "domain": {
            "level1": "semiconductor",
            "level2": "computational_materials",
            "level3": "structure_electronic_analysis",
        },
        "description": (
            "这是以 pymatgen-core 为核心的半导体候选准备和第一性原理结果分析环境，覆盖公开光伏、带电缺陷、瞬态吸收与"
            "载流子输运项目中的实际数据处理。研究人员从 CIF、Materials Project 记录或计算目录取得半导体，用 Composition、"
            "Lattice、Site 和 Structure 统一表示母体、掺杂、替位和缺陷超胞，完成空间群标准化、局域配位、结构匹配、"
            "候选去重、相稳定性与化学势分析，并写出 GGA/HSE、SOC、能带和光学 VASP 输入。计算后读取 vasprun.xml、"
            "OUTCAR、LOCPOT、CHGCAR、PROCAR、WAVECAR 和 WAVEDER，检查收敛与弛豫变化，恢复能带、态密度和介电响应，"
            "判断带隙、带边、轨道贡献及缺陷计算所需的体相参照。pymatgen 对象还作为 doped、pymatgen-analysis-defects、"
            "sumo、PyTASER 和 AMSET 的数据接口，分别支撑缺陷形成能与费米能级、能带/DOS 与有效质量、瞬态吸收以及"
            "迁移率和散射分析；这些包是明确的半导体下游，而不是用来扩展到无关材料场景。外部 DFT 求解、在线数据库服务、"
            "器件电路和制造工艺不属于这个离线环境。"
        ),
        "module_symbols": {
            "pymatgen.analysis.chempot_diagram": ("ChemicalPotentialDiagram",),
            "pymatgen.analysis.phase_diagram": (
                "PDEntry",
                "PhaseDiagram",
                "GrandPotentialPhaseDiagram",
                "CompoundPhaseDiagram",
                "PatchedPhaseDiagram",
            ),
            "pymatgen.core.bond_valence": (
                "BVAnalyzer",
            ),
            "pymatgen.core.composition": ("Composition", "ChemicalPotential"),
            "pymatgen.core.elasticity.elastic": (
                "ElasticTensor",
            ),
            "pymatgen.core.elasticity.strain": (
                "Deformation",
                "Strain",
            ),
            "pymatgen.core.entries": (
                "Entry",
                "ComputedEntry",
                "ComputedStructureEntry",
                "group_entries_by_structure",
                "group_entries_by_composition",
                "EntrySet",
            ),
            "pymatgen.core.lattice": ("Lattice",),
            "pymatgen.core.local_env": (
                "VoronoiNN",
                "CrystalNN",
            ),
            "pymatgen.core.operations": ("SymmOp",),
            "pymatgen.core.periodic_table": (
                "Element",
                "Species",
                "DummySpecies",
                "get_el_sp",
            ),
            "pymatgen.core.sites": ("Site", "PeriodicSite"),
            "pymatgen.core.structure": ("IStructure", "Structure"),
            "pymatgen.core.structure_analyzer": (
                "RelaxationAnalyzer",
            ),
            "pymatgen.core.structure_matcher": (
                "SpeciesComparator",
                "ElementComparator",
                "FrameworkComparator",
                "OrderDisorderElementComparator",
                "OccupancyComparator",
                "StructureMatcher",
            ),
            "pymatgen.electronic_structure.bandstructure": (
                "Kpoint",
                "BandStructure",
                "BandStructureSymmLine",
                "get_reconstructed_band_structure",
            ),
            "pymatgen.electronic_structure.core": ("Spin", "OrbitalType", "Orbital"),
            "pymatgen.electronic_structure.dos": (
                "Dos",
                "FermiDos",
                "DosFingerprint",
                "CompleteDos",
            ),
            "pymatgen.io.cif": ("CifParser", "CifWriter"),
            "pymatgen.io.vasp.inputs": (
                "Poscar",
                "Incar",
                "KpointsSupportedModes",
                "Kpoints",
                "PotcarSingle",
                "Potcar",
                "VaspInput",
            ),
            "pymatgen.io.vasp.optics": ("DielectricFunctionCalculator",),
            "pymatgen.io.vasp.outputs": (
                "KpointOptProps",
                "BandgapProps",
                "Vasprun",
                "BSVasprun",
                "Outcar",
                "VolumetricData",
                "Locpot",
                "Chgcar",
                "Elfcar",
                "Procar",
                "Oszicar",
                "get_band_structure_from_vasp_multiple_branches",
                "Wavecar",
                "Eigenval",
                "Waveder",
            ),
            "pymatgen.io.vasp.sets": (
                "VaspInputSet",
                "MPRelaxSet",
                "MPScanRelaxSet",
                "MP24RelaxSet",
                "MPHSERelaxSet",
                "MPStaticSet",
                "MPScanStaticSet",
                "MP24StaticSet",
                "MPHSEBSSet",
                "MPNonSCFSet",
                "MPSOCSet",
                "MVLElasticSet",
                "MPAbsorptionSet",
                "get_vasprun_outcar",
                "get_structure_from_prev_run",
                "standardize_structure",
                "batch_write_input",
            ),
            "pymatgen.symmetry.analyzer": ("SpacegroupAnalyzer",),
            "pymatgen.symmetry.bandstructure": ("HighSymmKpath",),
            "pymatgen.symmetry.groups": ("PointGroup", "SpaceGroup"),
            "pymatgen.symmetry.kpath": (
                "KPathSetyawanCurtarolo",
                "KPathSeek",
                "KPathLatimerMunro",
            ),
            "pymatgen.symmetry.site_symmetries": (
                "get_site_symmetries",
                "get_shared_symmetry_operations",
            ),
            "pymatgen.symmetry.structure": ("SymmetrizedStructure",),
            "pymatgen.transformations.site_transformations": (
                "InsertSitesTransformation",
                "ReplaceSiteSpeciesTransformation",
                "RemoveSitesTransformation",
                "PartialRemoveSitesTransformation",
            ),
            "pymatgen.transformations.standard_transformations": (
                "OxidationStateDecorationTransformation",
                "OxidationStateRemovalTransformation",
                "SupercellTransformation",
                "SubstitutionTransformation",
                "RemoveSpeciesTransformation",
                "PartialRemoveSpecieTransformation",
                "OrderDisorderedStructureTransformation",
                "PrimitiveCellTransformation",
                "ConventionalCellTransformation",
                "DeformStructureTransformation",
            ),
        },
    },
}


def _global_id(source: str, name: str, index: int) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return f"{source}_{normalized}_{index}"


def _matches_source_file(path: str, modules: set[str]) -> bool:
    module_path = path.removesuffix(".py").replace("/", ".")
    if module_path.endswith(".__init__"):
        module_path = module_path[: -len(".__init__")]
    return module_path in modules


def derive(source_path: Path, package_name: str) -> dict[str, Any]:
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ValueError(f"expected one package Seed: {source_path}")

    seed = deepcopy(payload[0])
    profile = PROFILES[package_name]
    module_symbols = {
        module: set(symbols) for module, symbols in profile["module_symbols"].items()
    }
    basic_info = seed["environment"]["basic_info"]
    basic_info["index"] = int(profile["new_index"])
    seed["global_id"] = _global_id(
        str(basic_info["source"]), str(basic_info["name"]), basic_info["index"]
    )
    seed["environment"]["description"] = profile["description"]
    seed["environment"]["domain"] = deepcopy(profile["domain"])

    selected_tools = [
        tool
        for tool in seed["init_ref_tools"]
        if isinstance(tool, dict)
        and isinstance(tool.get("module"), str)
        and isinstance(tool.get("name"), str)
        and tool["name"] in module_symbols.get(tool["module"], set())
    ]
    if not selected_tools:
        raise ValueError(f"no tools selected for {package_name}")
    requested_symbols = {
        (module, name)
        for module, names in module_symbols.items()
        for name in names
    }
    selected_symbols = {(tool["module"], tool["name"]) for tool in selected_tools}
    if missing := sorted(requested_symbols - selected_symbols):
        raise ValueError(f"unknown selected symbols for {package_name}: {missing}")
    seed["init_ref_tools"] = selected_tools

    extraction = seed["others"]["python_source_extraction"]
    source_files = [
        path
        for path in extraction["source_files"]
        if _matches_source_file(path, set(module_symbols))
    ]
    if not source_files:
        raise ValueError(f"no source files selected for {package_name}")
    extraction["requested_modules"] = list(module_symbols)
    extraction["source_files"] = source_files
    extraction["selection"] = (
        "curated public classes/functions that perform a core operation in a documented "
        "semiconductor workflow; __init__ and public source-defined class methods"
    )
    extraction["excluded"] = (
        "private definitions, imported/inherited callables, generic helpers without an "
        "independent semiconductor workflow role, and APIs outside the selected application "
        "families"
    )
    return seed


def _write_seed(path: Path, seed: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([seed], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_COMBINED_OUTPUT)
    parser.add_argument(
        "--atomate2",
        type=Path,
        default=OUTPUT_DIR / "atomate2_v0.1.5.json",
    )
    parser.add_argument(
        "--pymatgen-core",
        type=Path,
        default=OUTPUT_DIR / "pymatgen-core_v2026.8.30.json",
    )
    args = parser.parse_args()

    source_paths = {
        "atomate2": args.atomate2,
        "pymatgen-core": args.pymatgen_core,
    }
    seeds = []
    for package_name, source_path in source_paths.items():
        seed = derive(source_path, package_name)
        seeds.append(seed)
        _write_seed(OUTPUT_DIR / PROFILES[package_name]["output_name"], seed)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seeds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(seeds)} semiconductor Seeds to {args.output}")


if __name__ == "__main__":
    main()
