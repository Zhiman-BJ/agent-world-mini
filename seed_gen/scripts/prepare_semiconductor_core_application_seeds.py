#!/usr/bin/env python3
"""Build two focused PyPI Seeds from documented semiconductor workflows."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "pypi_outputs"
DEFAULT_COMBINED_OUTPUT = (
    OUTPUT_DIR / "semiconductor_core_application_seeds_v2_20260910.json"
)


PROFILES: dict[str, dict[str, Any]] = {
    "atomate2": {
        "index": 4,
        "output_name": "atomate2_gan_mg_acceptor_v0.1.5.json",
        "description": (
            "这是一个以 atomate2 为工作流核心的 GaN p 型掺杂计算环境，只处理一个明确问题：评估纤锌矿 GaN 中"
            "Mg 替位 Ga 位点（Mg_Ga）受主缺陷的多电荷态稳定性。研究人员从同一份 GaN 晶体结构生成 Mg_Ga 缺陷"
            "超胞，为 q=-2、-1、0、+1 组织体相参考与缺陷弛豫/静态计算，检查每个 Job 的输入、状态、依赖、"
            "custodian 记录和 VASP 输出，并把各电荷态的总能、结构及 LOCPOT 关联为形成能数据。环境要支持查看和"
            "修改 FormationEnergyMaker 配置、识别缺失或失败的电荷态、复用已完成的体相结果恢复工作流，以及在给定"
            "带边和 Ga-rich/N-rich 化学势条件后比较稳定电荷态与受主跃迁。公开数据以 atomate2 v0.1.5 官方"
            "GaN_Mg_defect 计算夹具为可复现基线；论文中的点缺陷流程说明其真实半导体用途。VASP 可执行程序、受许可"
            "约束的 POTCAR、其他缺陷体系、光学和载流子输运不在本环境范围内。"
        ),
        "domain": {
            "level1": "semiconductor",
            "level2": "gan_doping",
            "level3": "charged_defect_workflow",
        },
        "urls": [
            "https://github.com/materialsproject/atomate2",
            "https://materialsproject.github.io/atomate2/user/index.html",
            "https://doi.org/10.1039/D5DD00019J",
            "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/GaN_Mg_defect",
        ],
        "module_symbols": {
            "atomate2.common.jobs.defect": (
                "get_charged_structures",
                "bulk_supercell_calculation",
                "spawn_defect_q_jobs",
                "check_charge_state",
                "get_defect_entry",
            ),
            "atomate2.common.schemas.defects": (
                "FormationEnergyDiagramDocument",
            ),
            "atomate2.vasp.files": (
                "copy_vasp_outputs",
                "write_vasp_input_set",
            ),
            "atomate2.vasp.flows.defect": ("FormationEnergyMaker",),
            "atomate2.vasp.jobs.base": (
                "BaseVaspMaker",
                "get_vasp_task_document",
            ),
            "atomate2.vasp.jobs.core": (
                "RelaxMaker",
                "StaticMaker",
                "HSEStaticMaker",
            ),
            "atomate2.vasp.powerups": (
                "update_user_incar_settings",
                "update_user_kpoints_settings",
                "add_metadata_to_flow",
                "update_vasp_custodian_handlers",
            ),
            "atomate2.vasp.run": (
                "JobType",
                "run_vasp",
                "should_stop_children",
            ),
            "atomate2.vasp.sets.defect": (
                "ChargeStateRelaxSetGenerator",
                "ChargeStateStaticSetGenerator",
                "HSEChargeStateRelaxSetGenerator",
                "HSEChargeStateStaticSetGenerator",
            ),
        },
        "tasks": [
            {
                "description": (
                    "为 GaN 中的 Mg_Ga 受主建立一条多电荷态 atomate2 Flow。保持同一母体结构、缺陷位点和"
                    "超胞定义，为 q=-2、-1、0、+1 生成可追踪的体相与缺陷计算节点，并给每个节点写入材料、"
                    "缺陷和电荷态元数据。"
                ),
                "input": {
                    "host_structure": "GaN.cif",
                    "substitution": {"site_species": "Ga", "new_species": "Mg"},
                    "supercell_matrix": [[2, 2, 0], [2, -2, 0], [0, 0, 1]],
                    "charge_states": [-2, -1, 0, 1],
                    "maker_settings": {
                        "relax_radius": "auto",
                        "perturb": 0.1,
                        "collect_defect_entry_data": True,
                    },
                },
                "output": {
                    "flow": "一个体相参考节点和四个 Mg_Ga 电荷态分支",
                    "required_links": [
                        "host_structure_id",
                        "defect_id",
                        "bulk_job_uuid",
                        "charge_state",
                        "job_uuid",
                    ],
                    "validation": "所有分支共享同一缺陷定义且电荷态不重不漏",
                },
                "solution_path": [
                    "用领域缺陷生成器从 GaN 结构取得唯一的 Mg_Ga 替位缺陷。",
                    "用 FormationEnergyMaker.make 和给定超胞矩阵创建体相及多电荷态 Flow。",
                    "用 add_metadata_to_flow 为母体、缺陷、电荷态和父子节点补充稳定关联。",
                    "检查 spawn_defect_q_jobs 的分支集合与目标电荷态完全一致。",
                ],
            },
            {
                "description": (
                    "审计一组已经完成或部分完成的 GaN/Mg_Ga 计算目录，判断哪些电荷态可以形成可靠的缺陷"
                    "记录，哪些目录缺文件、未收敛或与声明电荷不一致，并给出只重跑必要节点的恢复清单。"
                ),
                "input": {
                    "bulk_directory": "GaN_Mg_defect/bulk_relax/outputs",
                    "defect_directories": {
                        "-2": "GaN_Mg_defect/relax_Mg_Ga-0_q=-2/outputs",
                        "-1": "GaN_Mg_defect/relax_Mg_Ga-0_q=-1/outputs",
                        "0": "GaN_Mg_defect/relax_Mg_Ga-0_q=0/outputs",
                        "1": "GaN_Mg_defect/relax_Mg_Ga-0_q=1/outputs",
                    },
                    "required_artifacts": [
                        "vasprun.xml",
                        "OUTCAR",
                        "CONTCAR",
                        "LOCPOT",
                        "custodian.json",
                    ],
                },
                "output": {
                    "run_audit": "逐电荷态的 complete/failed/inconsistent/missing 状态及证据",
                    "accepted_defect_entries": "只包含结构、能量、电势和电荷自洽的记录",
                    "resume_jobs": "复用体相结果后仍需恢复的最小节点集合",
                },
                "solution_path": [
                    "用 get_vasp_task_document 解析每个目录并核对收敛、最终结构与总能。",
                    "用 check_charge_state 对照目录声明、电荷设置和计算结果。",
                    "检查 LOCPOT、custodian 记录以及体相和缺陷目录之间的引用关系。",
                    "仅对合格目录调用 get_defect_entry，并把其余问题转成恢复清单。",
                ],
            },
            {
                "description": (
                    "在给定 GaN 带边与 Ga-rich、N-rich 化学势边界时，把合格的 Mg_Ga 多电荷态记录汇总成"
                    "形成能图，确定带隙内的最低能电荷态和热力学跃迁能级，并说明它对 p 型掺杂的含义。"
                ),
                "input": {
                    "defect_entries": "来自同一 Mg_Ga 缺陷和同一体相参考的 q=-2、-1、0、+1 记录",
                    "band_edges": {"vbm_ev": "已知数值", "band_gap_ev": "已知数值"},
                    "chemical_potential_limits": ["Ga-rich", "N-rich"],
                },
                "output": {
                    "formation_energy_diagrams": "每种化学势条件下随费米能级变化的形成能分段",
                    "transition_levels_ev": "位于带隙内的电荷态交点",
                    "stable_charge_states": "各费米能级区间的最低能电荷态",
                    "interpretation": "Mg_Ga 是否表现为可用受主及证据限制",
                },
                "solution_path": [
                    "确认所有 defect entry 指向相同的 GaN 体相、Mg_Ga 位点和计算设置族。",
                    "用 FormationEnergyDiagramDocument 汇总电荷态、能量修正、带边和化学势。",
                    "分别计算 Ga-rich 与 N-rich 条件下的形成能线和交点。",
                    "只报告带隙内且由已有电荷态共同约束的稳定区间和跃迁能级。",
                ],
            },
            {
                "description": (
                    "模拟 q=-1 分支失败后的增量恢复：保留已经完成的体相与其他电荷态，只重建 q=-1 的输入和"
                    "依赖，应用新的收敛设置，并验证恢复结果能安全并入原形成能数据。"
                ),
                "input": {
                    "failed_charge_state": -1,
                    "completed_bulk_directory": "GaN_Mg_defect/bulk_relax/outputs",
                    "completed_charge_states": [-2, 0, 1],
                    "input_adjustment": {
                        "INCAR": {"NELM": 160},
                        "reason": "原 custodian 记录显示电子步未收敛",
                    },
                },
                "output": {
                    "resumed_flow": "只包含 q=-1 恢复节点并引用已有体相结果",
                    "preserved_jobs": [-2, 0, 1],
                    "merge_decision": "设置一致性、收敛性和数据血缘均通过后才接受",
                },
                "solution_path": [
                    "从 custodian 和 VASP 输出确认失败原因，而不是重跑整个 Flow。",
                    "用 copy_vasp_outputs 复用体相所需文件并保留原始来源。",
                    "用 update_user_incar_settings 只修改 q=-1 分支的收敛参数。",
                    "恢复完成后重新生成 task document，并与其他电荷态做设置和血缘一致性检查。",
                ],
            },
        ],
        "evidence": {
            "research_question": (
                "Mg 替位 Ga 位点能否作为 GaN 的有效受主，以及其稳定电荷态如何随费米能级和生长条件变化。"
            ),
            "real_world_basis": [
                {
                    "title": "Atomate2: modular workflows for materials science",
                    "url": "https://doi.org/10.1039/D5DD00019J",
                    "relevance": (
                        "论文将半导体点缺陷列为 atomate2 的正式工作流，并说明多电荷态、有限尺寸修正和结果聚合需求。"
                    ),
                },
                {
                    "title": "First-principles calculations for defects and impurities: applications to III-nitrides",
                    "url": "https://doi.org/10.1063/1.1682673",
                    "relevance": "III 族氮化物掺杂与缺陷计算的应用依据。",
                },
            ],
            "public_data": [
                {
                    "url": "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/GaN_Mg_defect",
                    "contents": (
                        "GaN 体相以及 Mg_Ga 的 q=-2、-1、0、+1 VASP 输入、vasprun.xml、OUTCAR、CONTCAR、LOCPOT 和 custodian 记录。"
                    ),
                },
                {
                    "url": "https://github.com/materialsproject/atomate2/blob/v0.1.5/tests/vasp/flows/test_defect.py",
                    "contents": "与公开计算目录对应的 FormationEnergyMaker 构建、运行和重启方式。",
                },
            ],
            "supporting_packages": [
                "jobflow",
                "pymatgen",
                "pymatgen-analysis-defects",
                "custodian",
                "emmet-core",
            ],
        },
    },
    "pymatgen-core": {
        "index": 3,
        "output_name": "pymatgen-core_gan_piezomobility_v2026.8.30.json",
        "description": (
            "这是一个以 pymatgen-core 为结构数据核心的 p 型 GaN 应变设计环境，只复现“Design of high-mobility "
            "p-type GaN via the piezomobility tensor”这一实际应用。数据包含同一纤锌矿 GaN 在未应变以及六个独立"
            "应变方向 e1-e6、压缩/拉伸 1%-3% 下共 37 组 Quantum ESPRESSO/EPW 计算。pymatgen 负责读取并"
            "规范化 scf.in 中的晶格、元素和分数坐标，以未应变结构为参照恢复每个目录的变形梯度和应变张量，检查"
            "目录标签、结构、赝势和计算参数是否一致，并为缺失或错误条件重写可追踪的 PWInput。随后把这些结构状态"
            "与对应 gan.eig、EPW 设置及温度相关空穴迁移率张量关联，比较 300 K 下不同应变对 p 型输运的影响并拟合"
            "局部压电迁移率响应。公开 Materials Cloud 归档提供全部 37 组输入和 EPW 输出。外部 QE/EPW/GW 重算、"
            "其他材料、n 型器件和任意应变搜索不在环境范围内；EPW 文本结果的数值抽取是配套处理，结构语义与变形"
            "审计仍由 pymatgen-core 承担。"
        ),
        "domain": {
            "level1": "semiconductor",
            "level2": "gan_transport",
            "level3": "strain_mobility_design",
        },
        "urls": [
            "https://github.com/materialsproject/pymatgen",
            "https://pymatgen.org/pymatgen.html",
            "https://doi.org/10.1103/z22d-vlvc",
            "https://doi.org/10.24435/materialscloud:zy-qw",
        ],
        "module_symbols": {
            "pymatgen.core.elasticity.strain": (
                "Deformation",
                "DeformedStructureSet",
                "Strain",
                "convert_strain_to_deformation",
            ),
            "pymatgen.core.lattice": ("Lattice",),
            "pymatgen.core.operations": ("SymmOp",),
            "pymatgen.core.structure": ("Structure",),
            "pymatgen.core.structure_matcher": ("StructureMatcher",),
            "pymatgen.core.tensors": (
                "Tensor",
                "TensorCollection",
                "SquareTensor",
                "symmetry_reduce",
                "TensorMapping",
            ),
            "pymatgen.io.pwscf": ("PWInput",),
            "pymatgen.symmetry.analyzer": ("SpacegroupAnalyzer",),
        },
        "tasks": [
            {
                "description": (
                    "把 Materials Cloud 归档中的未应变 GaN 和 36 个应变条件整理成结构台账。逐个读取 scf.in，"
                    "确认组成、原子数、坐标基准和晶格有效，并验证目录是否完整覆盖 e1-e6、压缩/拉伸 1%-3%。"
                ),
                "input": {
                    "archive": "strain_GaN.tar.gz",
                    "reference_directory": "unstrained",
                    "expected_variants": {
                        "directions": ["e1", "e2", "e3", "e4", "e5", "e6"],
                        "signs": ["compress", "expand"],
                        "magnitudes_percent": [1, 2, 3],
                    },
                },
                "output": {
                    "structure_records": "37 条带目录 ID、晶格、分数坐标、体积和空间群的记录",
                    "coverage": "1 个未应变条件和 36 个唯一应变条件",
                    "anomalies": "缺失、重复、无法解析或不再是 GaN 的目录",
                },
                "solution_path": [
                    "用 PWInput.from_file 读取每个 scf.in 的 Structure 与计算参数。",
                    "以 unstrained 为唯一参考，核对所有记录均为四原子纤锌矿 GaN。",
                    "用 SpacegroupAnalyzer 和 StructureMatcher 检查结构族的一致性。",
                    "按方向、符号和幅度建立完整性矩阵，不用目录名代替结构证据。",
                ],
            },
            {
                "description": (
                    "从 37 组实际晶格反算相对于未应变 GaN 的变形梯度和 Green-Lagrange 应变，检查每个"
                    "compress/expand、1%-3%、e1-e6 标签是否与文件内容相符，并量化体积和对称性的变化。"
                ),
                "input": {
                    "reference_structure_id": "unstrained",
                    "variant_structure_ids": "36 个应变目录 ID",
                    "tolerance": {"strain_component": 0.0005, "structure_match": 0.1},
                },
                "output": {
                    "strain_records": "目录 ID 到 3x3 变形矩阵、应变张量和 Voigt 分量的映射",
                    "label_checks": "标签与实际主应变分量的一致/不一致结论",
                    "symmetry_changes": "空间群及等价位点变化",
                },
                "solution_path": [
                    "从参考与目标 Lattice 计算 Deformation。",
                    "用 Strain.from_deformation 得到张量和 Voigt 表示。",
                    "将主分量的符号、方向和幅度与目录标签逐项比较。",
                    "用 StructureMatcher 排除原子重排或坐标错误造成的伪应变。",
                ],
            },
            {
                "description": (
                    "审计 37 组 scf.in 的计算协议是否可比较，包括 relativistic 设置、ecutwfc、k 点网格、"
                    "收敛阈值、赝势引用和原子顺序；若一个应变目录缺失或参数漂移，依据同组相邻条件生成修复后的"
                    "Quantum ESPRESSO 输入，同时保留原条件 ID 和修改证据。"
                ),
                "input": {
                    "input_files": "37 个 scf.in",
                    "required_protocol": {
                        "calculation": "scf",
                        "noncolin": True,
                        "lspinorb": True,
                        "ecutwfc_ry": 120,
                        "k_points": [8, 8, 8, 0, 0, 0],
                        "conv_thr": "1.0d-14",
                    },
                    "repair_case": "一个缺失或参数不一致的已知应变条件",
                },
                "output": {
                    "protocol_audit": "逐目录参数差异与是否可比较",
                    "repaired_pw_input": "包含正确应变结构、统一参数和原赝势映射的 scf.in",
                    "provenance": "参考目录、变形张量、修改字段和原因",
                },
                "solution_path": [
                    "用 PWInput 分别取得结构、control、system、electrons 和 k 点设置。",
                    "把 unstrained 及同方向相邻幅度作为协议参考，区分结构差异与意外参数漂移。",
                    "用 Deformation 生成目标晶格，并用 Structure 保留 Ga/N 分数坐标和原子顺序。",
                    "用 PWInput 写出修复文件，再读回验证结构、参数和赝势引用。",
                ],
            },
            {
                "description": (
                    "将结构审计结果与每个目录的 gan.eig、EPW 输入和 epw2.out 关联，抽取 300 K 空穴迁移率"
                    "张量，比较所有应变条件相对未应变 GaN 的增益，在小应变区拟合压电迁移率响应，并选出数据完整且"
                    "结构标签可信的最佳应变方案。"
                ),
                "input": {
                    "strain_records": "已通过标签校验的 37 条结构与应变记录",
                    "domain_files": ["gan.eig", "epw1.in", "epw2.in", "epw2.out"],
                    "target_temperature_k": 300,
                    "carrier": "hole",
                },
                "output": {
                    "mobility_records": "条件 ID、应变 Voigt 分量和 300 K 迁移率张量",
                    "relative_gain": "各方向相对 unstrained 的分量与主值变化",
                    "piezomobility_fit": "由小应变数据拟合的局部响应系数和残差",
                    "recommendation": "最佳可信应变条件及其结构、协议和数值证据",
                },
                "solution_path": [
                    "按目录 ID 联结 scf.in、gan.eig、EPW 设置和 epw2.out，缺任一关键文件则标记不完整。",
                    "从 epw2.out 的 300 K 区块提取无磁场迁移率张量并保留单位。",
                    "用 Tensor 表示迁移率，用已反算的 Strain 而不是目录名作为自变量，计算相对增益并拟合小应变响应。",
                    "排名前再次检查结构匹配、计算协议和输出完整性，避免把异常运行当成高迁移率结果。",
                ],
            },
        ],
        "evidence": {
            "research_question": (
                "六种独立应变及其幅度如何改变纤锌矿 GaN 的空穴迁移率，哪一种可缓解 p 沟道 GaN 的低迁移率瓶颈。"
            ),
            "real_world_basis": [
                {
                    "title": "Design of high-mobility p-type GaN via the piezomobility tensor",
                    "url": "https://doi.org/10.1103/z22d-vlvc",
                    "relevance": "场景对应的实际研究，使用从头算 Boltzmann 输运比较完整应变空间。",
                }
            ],
            "public_data": [
                {
                    "url": "https://doi.org/10.24435/materialscloud:zy-qw",
                    "contents": (
                        "未应变和 e1-e6 六方向压缩/拉伸 1%-3% 共 37 组 scf.in、nscf.in、ph.in、EPW 输入、gan.eig 和 epw2.out。"
                    ),
                    "download_url": (
                        "https://archive.materialscloud.org/api/records/qfdfa-cs517/files/strain_GaN.tar.gz/content"
                    ),
                    "size_bytes": 12934938,
                }
            ],
            "supporting_packages": [
                "numpy",
                "Quantum ESPRESSO 7.2",
                "EPW 5.7",
            ],
        },
    },
}


SOURCE_FILES = {
    "atomate2": OUTPUT_DIR / "atomate2_v0.1.5.json",
    "pymatgen-core": OUTPUT_DIR / "pymatgen-core_v2026.8.30.json",
}


def _global_id(source: str, name: str, index: int) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return f"{source}_{normalized}_{index}"


def _source_file_matches(path: str, module: str) -> bool:
    module_path = path.removesuffix(".py").replace("/", ".")
    if module_path.endswith(".__init__"):
        module_path = module_path[: -len(".__init__")]
    return module_path == module


def _load_single_seed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError(f"expected exactly one Seed in {path}")
    if not isinstance(payload[0], dict):
        raise ValueError(f"expected a Seed object in {path}")
    return payload[0]


def _select_tools(
    tools: list[dict[str, Any]], module_symbols: dict[str, tuple[str, ...]]
) -> list[dict[str, Any]]:
    requested = {
        (module, symbol)
        for module, symbols in module_symbols.items()
        for symbol in symbols
    }
    selected = [
        deepcopy(tool)
        for tool in tools
        if (tool.get("module"), tool.get("name")) in requested
    ]
    found = {(tool["module"], tool["name"]) for tool in selected}
    if missing := sorted(requested - found):
        raise ValueError(f"unknown requested tools: {missing}")
    names = [tool["name"] for tool in selected]
    if len(names) != len(set(names)):
        raise ValueError("selected tool names must be unique")
    return selected


def _select_source_files(
    paths: list[str], modules: dict[str, tuple[str, ...]]
) -> list[str]:
    selected = [
        path
        for path in paths
        if any(_source_file_matches(path, module) for module in modules)
    ]
    missing_modules = [
        module
        for module in modules
        if not any(_source_file_matches(path, module) for path in selected)
    ]
    if missing_modules:
        raise ValueError(f"no source file for modules: {missing_modules}")
    return selected


def build_seed(package_name: str) -> dict[str, Any]:
    source = _load_single_seed(SOURCE_FILES[package_name])
    profile = PROFILES[package_name]
    seed = deepcopy(source)
    basic_info = seed["environment"]["basic_info"]
    basic_info["index"] = profile["index"]
    basic_info["url"] = profile["urls"]
    seed["global_id"] = _global_id(
        basic_info["source"], basic_info["name"], basic_info["index"]
    )
    seed["environment"]["description"] = profile["description"]
    seed["environment"]["domain"] = deepcopy(profile["domain"])
    seed["init_ref_tools"] = _select_tools(
        seed["init_ref_tools"], profile["module_symbols"]
    )
    seed["init_ref_tasks"] = deepcopy(profile["tasks"])

    extraction = seed["others"]["python_source_extraction"]
    extraction["requested_modules"] = list(profile["module_symbols"])
    extraction["source_files"] = _select_source_files(
        extraction["source_files"], profile["module_symbols"]
    )
    extraction["selection"] = (
        "only public classes and functions used by the named semiconductor workflow; "
        "class methods remain source-derived"
    )
    extraction["excluded"] = (
        "package capabilities outside the named material system and workflow, imported "
        "callables, private definitions, and unrelated helper APIs"
    )
    seed["others"]["application_evidence"] = deepcopy(profile["evidence"])
    return seed


def _write(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_COMBINED_OUTPUT)
    args = parser.parse_args()

    seeds = []
    for package_name, profile in PROFILES.items():
        seed = build_seed(package_name)
        seeds.append(seed)
        _write(OUTPUT_DIR / profile["output_name"], [seed])
    _write(args.output, seeds)
    print(f"Wrote {len(seeds)} focused semiconductor Seeds to {args.output}")


if __name__ == "__main__":
    main()
