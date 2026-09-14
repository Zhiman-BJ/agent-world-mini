#!/usr/bin/env python3
"""Generate material-agnostic Seeds for core semiconductor workflows."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any

from prepare_semiconductor_core_application_seeds import (
    OUTPUT_DIR,
    SOURCE_FILES,
    _global_id,
    _load_single_seed,
    _select_source_files,
    _select_tools,
    _write,
)


DEFAULT_COMBINED_OUTPUT = (
    OUTPUT_DIR / "semiconductor_general_core_application_seeds_v3_20260910.json"
)


PROFILES: dict[str, dict[str, Any]] = {
    "atomate2": {
        "index": 5,
        "output_name": "atomate2_semiconductor_defect_workflows_v0.1.5.json",
        "description": (
            "这是一个以 atomate2 为核心的半导体点缺陷与掺杂工作流环境。它不绑定某一种材料：母体可以是"
            "III-V、II-VI、氧化物、卤化物、二维或其他具有明确带隙的晶态半导体，缺陷可以是空位、间隙、替位"
            "或复合缺陷。共同工作主线是从母体结构和缺陷定义出发，建立体相参考及多个电荷态的弛豫/静态计算"
            "Flow，维护 Job 依赖、计算设置、运行状态、错误恢复和数据血缘，再把结构、总能、电势、带边、化学势"
            "和有限尺寸修正聚合为形成能、热力学跃迁能级与掺杂倾向。存在成套构型坐标数据时，还可组织两个"
            "电荷态间的位形曲线和非辐射俘获输入。环境中的材料数量不预先限定，但数据必须来自内部关联完整的真实"
            "计算项目；GaN 中 Mg_Ga 和 Si 构型坐标只是公开基线，不是材料边界。VASP 可执行程序、受许可约束的"
            "POTCAR、器件级模拟以及与半导体缺陷无关的 atomate2 工作流不在范围内。"
        ),
        "domain": {
            "level1": "semiconductor",
            "level2": "defects_and_doping",
            "level3": "workflow_automation",
        },
        "urls": [
            "https://github.com/materialsproject/atomate2",
            "https://materialsproject.github.io/atomate2/user/index.html",
            "https://doi.org/10.1039/D5DD00019J",
            "https://doi.org/10.1063/5.0203124",
            "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/GaN_Mg_defect",
            "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/Si_config_coord",
        ],
        "module_symbols": {
            "atomate2.common.jobs.defect": (
                "get_charged_structures",
                "spawn_energy_curve_calcs",
                "get_ccd_documents",
                "get_supercell_from_prv_calc",
                "bulk_supercell_calculation",
                "spawn_defect_q_jobs",
                "check_charge_state",
                "get_defect_entry",
            ),
            "atomate2.common.jobs.utils": (
                "structure_to_primitive",
                "structure_to_conventional",
            ),
            "atomate2.common.schemas.defects": (
                "FormationEnergyDiagramDocument",
                "CCDDocument",
                "get_dQ",
            ),
            "atomate2.vasp.files": (
                "copy_vasp_outputs",
                "write_vasp_input_set",
            ),
            "atomate2.vasp.flows.defect": (
                "FormationEnergyMaker",
                "ConfigurationCoordinateMaker",
                "NonRadiativeMaker",
            ),
            "atomate2.vasp.jobs.base": (
                "BaseVaspMaker",
                "get_vasp_task_document",
            ),
            "atomate2.vasp.jobs.core": (
                "RelaxMaker",
                "StaticMaker",
                "HSERelaxMaker",
                "HSEStaticMaker",
            ),
            "atomate2.vasp.jobs.defect": ("calculate_finite_diff",),
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
            "atomate2.vasp.schemas.defect": ("FiniteDifferenceDocument",),
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
                    "针对给定半导体母体和一组空位、间隙或替位缺陷，建立可复用的带电缺陷 Flow。每个缺陷"
                    "根据化学与氧化态生成候选电荷态，共享同一体相参考，并保留材料、缺陷、超胞、电荷和计算"
                    "方法之间的稳定关联。"
                ),
                "input": {
                    "host_structures": "一个真实项目中的一种或多种半导体晶体结构",
                    "defect_definitions": "带母体位点、缺陷类型和稳定 ID 的缺陷集合",
                    "charge_state_policy": "显式电荷态或由形式氧化态得到的候选范围",
                    "calculation_protocol": "PBE/SCAN/HSE 弛豫和静态 Maker 配置",
                },
                "output": {
                    "flows": "按 host_id/defect_id 组织的体相和多电荷态 Job 图",
                    "metadata": [
                        "host_id",
                        "defect_id",
                        "supercell_id",
                        "charge_state",
                        "job_uuid",
                        "parent_uuid",
                    ],
                    "validation": "同一缺陷的电荷态不重不漏且共享兼容的体相与设置",
                },
                "solution_path": [
                    "先将母体规范为适合缺陷定义的 primitive 或 conventional structure。",
                    "用 FormationEnergyMaker 为每个缺陷创建体相参考和多电荷态分支。",
                    "用 get_charged_structures 与 spawn_defect_q_jobs 核对实际分支。",
                    "用 add_metadata_to_flow 写入跨材料仍稳定的主键和父子关系。",
                ],
            },
            {
                "description": (
                    "审计一批来自不同半导体项目的体相和缺陷计算目录。依据实际输出判断收敛、电荷、文件完整性"
                    "与协议兼容性，区分可聚合记录、需要局部恢复的运行和不能比较的数据，不因文件名相似而混合项目。"
                ),
                "input": {
                    "calculation_directories": "按项目、母体、缺陷和电荷态分组的 VASP 目录",
                    "required_artifacts": ["vasprun.xml", "OUTCAR", "CONTCAR", "LOCPOT"],
                    "run_metadata": "job UUID、父节点、Maker 设置和 custodian 记录",
                },
                "output": {
                    "accepted_entries": "可用于形成能或构型坐标分析的记录",
                    "run_audit": "complete/failed/inconsistent/missing 及逐项证据",
                    "resume_plan": "复用已完成父节点后需要恢复的最小 Job 集合",
                },
                "solution_path": [
                    "用 get_vasp_task_document 解析目录并核对收敛、结构、能量和输入设置。",
                    "用 check_charge_state 核验声明电荷与实际计算。",
                    "只在 host_id、defect_id、超胞和协议兼容时调用 get_defect_entry。",
                    "按依赖图定位失败节点，保留可复用的体相及其他已完成分支。",
                ],
            },
            {
                "description": (
                    "把同一半导体项目中的多电荷态缺陷结果与带边和化学势边界结合，生成形成能图、热力学"
                    "跃迁能级和补偿缺陷比较，用于判断目标施主或受主在不同生长条件下的可实现性。"
                ),
                "input": {
                    "defect_entries": "一个母体内若干缺陷及其多电荷态记录",
                    "band_edges": "同一计算协议下的 VBM 和带隙",
                    "chemical_potential_limits": "来自相稳定性分析的生长条件边界",
                    "corrections": "与各缺陷超胞对应的有限尺寸修正",
                },
                "output": {
                    "formation_energy_diagrams": "每种生长条件下的形成能分段",
                    "transition_levels": "带隙内有数据约束的电荷态交点",
                    "dopability_assessment": "目标掺杂与主要补偿缺陷的比较",
                    "limitations": "缺失电荷态、修正或参考能造成的不确定性",
                },
                "solution_path": [
                    "按母体和计算协议分组，拒绝跨项目拼接不兼容参考能。",
                    "用 FormationEnergyDiagramDocument 聚合能量、带边、化学势和修正。",
                    "分别计算允许的生长条件并识别最低能电荷态和交点。",
                    "比较目标掺杂与补偿缺陷，只在已有数据范围内给出结论。",
                ],
            },
            {
                "description": (
                    "对具有两个相关电荷态和成套位形位移计算的半导体缺陷，构建构型坐标图，检查两条势能曲线"
                    "的采样、弛豫极小值和结构位移，并准备非辐射载流子俘获分析所需的有限差分记录。"
                ),
                "input": {
                    "charge_state_pair": "同一缺陷的两个电荷态",
                    "relaxed_structures": "两个电荷态各自的弛豫结构",
                    "distortion_series": "沿质量加权位形坐标的成套静态计算",
                    "wavefunction_overlap_data": "存在时使用的 WSWQ 或有限差分数据",
                },
                "output": {
                    "configuration_coordinate_document": "位移、两条能量曲线和弛豫点",
                    "finite_difference_documents": "按电荷态关联的波函数重叠记录",
                    "quality_report": "采样缺口、异常点和是否足以继续非辐射分析",
                },
                "solution_path": [
                    "用 ConfigurationCoordinateMaker 组织两个弛豫节点和共同位移网格。",
                    "用 get_dQ 与 get_ccd_documents 复核质量加权位移和能量排序。",
                    "存在波函数数据时用 calculate_finite_diff 生成有限差分文档。",
                    "只有两条曲线和关联数据完整时才交给 NonRadiativeMaker 的后续步骤。",
                ],
            },
            {
                "description": (
                    "当某个材料、缺陷或电荷态运行失败时进行增量恢复。根据错误记录调整该分支的 INCAR、k 点或"
                    "custodian 处理器，复用兼容的体相和成功分支，并验证恢复结果能否并回原项目。"
                ),
                "input": {
                    "failed_jobs": "带错误日志和父节点引用的失败 Job",
                    "completed_jobs": "同一项目中可复用的体相及缺陷 Job",
                    "allowed_adjustments": "有物理依据的最小输入或错误处理修改",
                },
                "output": {
                    "resumed_flows": "只包含失败节点及必要下游节点的新 Flow",
                    "preserved_results": "无需重跑的已完成 Job",
                    "merge_audit": "恢复前后协议差异、收敛性和血缘兼容性",
                },
                "solution_path": [
                    "从运行输出和 custodian 记录确定失败原因。",
                    "用 copy_vasp_outputs 复用兼容父节点并保留原来源。",
                    "用相应 powerup 只修改受影响分支。",
                    "重建 task document，通过协议和血缘核验后再合并。",
                ],
            },
        ],
        "evidence": {
            "scope_rule": (
                "以半导体缺陷与掺杂工作流为边界，材料体系开放；公开实例证明流程和数据形态，不限制后续材料。"
            ),
            "real_applications": [
                {
                    "title": "Atomate2: modular workflows for materials science",
                    "url": "https://doi.org/10.1039/D5DD00019J",
                    "relevance": "正式描述 atomate2 半导体点缺陷的多电荷态、聚合和恢复工作流。",
                },
                {
                    "title": "Simulating charged defects at database scale",
                    "url": "https://doi.org/10.1063/5.0203124",
                    "relevance": "面向多种技术材料的持久化缺陷数据库和高通量计算应用。",
                },
                {
                    "title": "High-throughput calculations of charged point defect properties",
                    "url": "https://doi.org/10.1038/s41524-023-01015-6",
                    "relevance": "245 个已发表缺陷结果上的形成能、跃迁能级和掺杂极限基准。",
                },
            ],
            "public_examples": [
                {
                    "material": "GaN",
                    "workflow": "Mg_Ga 多电荷态形成能",
                    "url": "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/GaN_Mg_defect",
                },
                {
                    "material": "Si",
                    "workflow": "两个电荷态的构型坐标和有限差分",
                    "url": "https://github.com/materialsproject/atomate2/tree/v0.1.5/tests/test_data/vasp/Si_config_coord",
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
        "index": 4,
        "output_name": "pymatgen-core_semiconductor_core_workflows_v2026.8.30.json",
        "description": (
            "这是一个以 pymatgen-core 为核心的通用半导体计算数据环境，不绑定特定材料或单一性质。它面向"
            "晶态半导体研究中反复出现的共同主线：读取 CIF、POSCAR、pymatgen Structure 及 Quantum ESPRESSO"
            "输入，规范化晶格、组成、位点和对称性；维护母体与应变、掺杂、缺陷或多型变体之间的结构关系；生成"
            "一致的 VASP/QE 弛豫、静态、能带、DOS、SOC 或光学输入；解析实际计算输出并审计收敛、结构变化和"
            "计算协议；恢复能带、态密度、带边、轨道贡献、介电响应和电势；最后在同一项目内比较候选或变体，并在"
            "需要时结合相图和化学势判断稳定性。材料和项目数量不预先限定，每个项目也不要求同时拥有所有性质，"
            "但共同完成一项任务的数据必须来自可关联的真实结构和计算链。GaN 应变迁移率、Si 能带和公开非线性"
            "光学结构库只是不同数据形态的实例。外部 DFT/BTE 求解、在线数据库本身、器件电路、制造工艺以及与"
            "半导体结构和电子性质无关的 pymatgen 功能不在范围内。"
        ),
        "domain": {
            "level1": "semiconductor",
            "level2": "computational_characterization",
            "level3": "structure_and_electronic_workflows",
        },
        "urls": [
            "https://github.com/materialsproject/pymatgen",
            "https://pymatgen.org/pymatgen.html",
            "https://doi.org/10.1016/j.commatsci.2012.10.028",
            "https://doi.org/10.24435/materialscloud:zy-qw",
            "https://github.com/materialsproject/pymatgen-test-files",
            "https://doi.org/10.24435/materialscloud:wk-qm",
        ],
        "module_symbols": {
            "pymatgen.analysis.chempot_diagram": ("ChemicalPotentialDiagram",),
            "pymatgen.analysis.phase_diagram": ("PDEntry", "PhaseDiagram"),
            "pymatgen.core.composition": ("Composition", "ChemicalPotential"),
            "pymatgen.core.elasticity.strain": (
                "Deformation",
                "DeformedStructureSet",
                "Strain",
                "convert_strain_to_deformation",
            ),
            "pymatgen.core.entries": (
                "ComputedEntry",
                "ComputedStructureEntry",
                "group_entries_by_structure",
                "group_entries_by_composition",
            ),
            "pymatgen.core.lattice": ("Lattice",),
            "pymatgen.core.local_env": ("CrystalNN",),
            "pymatgen.core.operations": ("SymmOp",),
            "pymatgen.core.periodic_table": ("Element", "Species", "get_el_sp"),
            "pymatgen.core.sites": ("Site", "PeriodicSite"),
            "pymatgen.core.structure": ("IStructure", "Structure"),
            "pymatgen.core.structure_analyzer": ("RelaxationAnalyzer",),
            "pymatgen.core.structure_matcher": (
                "SpeciesComparator",
                "ElementComparator",
                "StructureMatcher",
            ),
            "pymatgen.core.tensors": (
                "Tensor",
                "TensorCollection",
                "SquareTensor",
                "symmetry_reduce",
                "TensorMapping",
            ),
            "pymatgen.electronic_structure.bandstructure": (
                "Kpoint",
                "BandStructure",
                "BandStructureSymmLine",
                "get_reconstructed_band_structure",
            ),
            "pymatgen.electronic_structure.core": ("Spin", "OrbitalType", "Orbital"),
            "pymatgen.electronic_structure.dos": ("Dos", "FermiDos", "CompleteDos"),
            "pymatgen.io.cif": ("CifParser", "CifWriter"),
            "pymatgen.io.pwscf": ("PWInput", "PWOutput"),
            "pymatgen.io.vasp.inputs": ("Poscar", "Incar", "Kpoints", "VaspInput"),
            "pymatgen.io.vasp.optics": ("DielectricFunctionCalculator",),
            "pymatgen.io.vasp.outputs": (
                "BandgapProps",
                "Vasprun",
                "BSVasprun",
                "Outcar",
                "Locpot",
                "Chgcar",
                "Procar",
                "Oszicar",
                "get_band_structure_from_vasp_multiple_branches",
                "get_adjusted_fermi_level",
                "Eigenval",
                "Waveder",
            ),
            "pymatgen.io.vasp.sets": (
                "VaspInputSet",
                "MPRelaxSet",
                "MPStaticSet",
                "MPHSEBSSet",
                "MPNonSCFSet",
                "MPSOCSet",
                "MPAbsorptionSet",
                "get_vasprun_outcar",
                "get_structure_from_prev_run",
                "standardize_structure",
                "batch_write_input",
            ),
            "pymatgen.symmetry.analyzer": ("SpacegroupAnalyzer",),
            "pymatgen.symmetry.bandstructure": ("HighSymmKpath",),
            "pymatgen.symmetry.kpath": (
                "KPathSetyawanCurtarolo",
                "KPathSeek",
                "KPathLatimerMunro",
            ),
            "pymatgen.transformations.site_transformations": (
                "ReplaceSiteSpeciesTransformation",
            ),
            "pymatgen.transformations.standard_transformations": (
                "SupercellTransformation",
                "SubstitutionTransformation",
                "PrimitiveCellTransformation",
                "ConventionalCellTransformation",
                "DeformStructureTransformation",
            ),
        },
        "tasks": [
            {
                "description": (
                    "把一个或多个真实半导体项目中的 CIF、POSCAR、QE 输入和 Structure JSON 统一成结构台账，"
                    "识别母体、应变、掺杂、缺陷和多型关系，同时保留项目内原始 ID 与来源。"
                ),
                "input": {
                    "structure_files": "来自实际项目的 CIF、POSCAR、scf.in 或 Structure JSON",
                    "source_metadata": "项目 ID、材料 ID、计算 ID 和文件来源",
                    "declared_relations": "存在时使用的 parent/variant 关系",
                },
                "output": {
                    "materials": "规范组成、晶格、空间群和结构指纹",
                    "structure_variants": "母体到应变、掺杂、缺陷或多型的显式关系",
                    "anomalies": "重复、不可解析、标签错误或无法关联的结构",
                },
                "solution_path": [
                    "按格式使用 CifParser、Poscar、PWInput 或 Structure 读取实际结构。",
                    "用 SpacegroupAnalyzer 生成 primitive/conventional 表示及空间群。",
                    "用 StructureMatcher 区分重复结构、同一母体变体和不同材料。",
                    "保留原 ID、转换操作和父结构 ID，不用化学式代替数据血缘。",
                ],
            },
            {
                "description": (
                    "根据半导体研究目标和已有父计算，为一批结构生成一致、可比较的 VASP 或 Quantum ESPRESSO"
                    "输入链。只建立任务所需的弛豫、静态、能带/DOS、SOC 或光学阶段，并记录每个阶段的父输出。"
                ),
                "input": {
                    "structures": "已规范化并带稳定 ID 的半导体结构或变体",
                    "property_goal": "band_structure、dos、soc、dielectric 或 absorption",
                    "protocol": "泛函、截断、k 点密度、收敛条件及允许的材料特定覆盖项",
                    "previous_runs": "可复用的弛豫或静态计算输出",
                },
                "output": {
                    "calculation_chain": "每个材料实际需要的阶段及依赖",
                    "input_files": "INCAR/KPOINTS/POSCAR 或 pw.x 输入",
                    "protocol_manifest": "公共设置、材料覆盖项、父计算和预期产物",
                },
                "solution_path": [
                    "根据目标性质选择 MPRelaxSet、MPStaticSet、MPNonSCFSet、MPSOCSet、MPHSEBSSet 或 MPAbsorptionSet。",
                    "需要 QE 时用 PWInput 写入同一 Structure 与可比较协议。",
                    "从 previous run 继承最终结构和必要文件，生成高对称路径或均匀 k 点。",
                    "写出后重新读取并比较协议，避免材料间出现未登记的参数漂移。",
                ],
            },
            {
                "description": (
                    "审计一批实际 VASP 或 QE 计算结果，检查电子和离子收敛、结构弛豫幅度、输入协议与输出文件"
                    "完整性，并确定每个结果可以支持哪些后续半导体分析。"
                ),
                "input": {
                    "calculation_directories": "按材料和阶段关联的 VASP/QE 目录",
                    "expected_protocols": "对应输入链的设置清单",
                    "required_outputs_by_goal": "不同性质分析需要的输出文件集合",
                },
                "output": {
                    "run_audit": "逐计算的 complete/unconverged/inconsistent/missing 状态",
                    "relaxation_metrics": "晶格、体积、键长和位点位移变化",
                    "supported_analyses": "该目录可可靠支持的能带、DOS、SOC、光学或电势分析",
                },
                "solution_path": [
                    "用 Vasprun、Outcar、Oszicar 或 PWOutput 解析实际运行状态和主要结果。",
                    "用 RelaxationAnalyzer 和 StructureMatcher 比较输入、最终结构和父结构。",
                    "将输入设置与项目协议逐字段比较，并按分析目标检查必要文件。",
                    "不完整运行只保留可证实的字段，不将相关文件自动判为完整结果。",
                ],
            },
            {
                "description": (
                    "从同一项目的静态和能带/DOS 结果恢复半导体电子结构，判断金属或半导体、直接或间接带隙、"
                    "VBM/CBM 的 k 点与轨道贡献，并比较不同泛函、SOC、应变、掺杂或结构变体造成的变化。"
                ),
                "input": {
                    "static_runs": "提供费米能级、电子密度和参考结构的合格静态计算",
                    "band_runs": "line-mode 或多分支能带计算",
                    "dos_runs": "uniform k-mesh DOS 及存在时的投影数据",
                    "comparison_groups": "同一母体内协议可比较的结果集合",
                },
                "output": {
                    "electronic_summaries": "带隙、直接性、VBM/CBM、费米能级和轨道成分",
                    "band_structures": "带标签和连续路径的 BandStructureSymmLine",
                    "density_of_states": "总 DOS、分波 DOS 和带边附近积分",
                    "comparisons": "不同方法或结构状态相对基线的变化及可信度",
                },
                "solution_path": [
                    "用 BSVasprun、Vasprun、Eigenval 和 Procar 读取可用电子结构数据。",
                    "用 get_band_structure_from_vasp_multiple_branches 或重建函数合并连续路径。",
                    "用 BandStructure 和 CompleteDos 提取带隙、带边及投影信息。",
                    "只在父结构和协议兼容的 comparison group 内计算差异。",
                ],
            },
            {
                "description": (
                    "分析半导体结构变体对性质的影响。根据真实晶格恢复应变，或根据替位/超胞操作确认掺杂与"
                    "缺陷关系，再将结构状态与电子、介电、电势或外部输运结果关联，识别可信的性质改进。"
                ),
                "input": {
                    "parent_structures": "未修改的半导体母体",
                    "variant_structures": "应变、替位、缺陷、超胞或多型结构",
                    "property_records": "与 structure_id 对应的电子、光学、电势或输运结果",
                },
                "output": {
                    "transformations": "可复现的 Deformation、Substitution 或 Supercell 记录",
                    "variant_groups": "按共同母体和变体维度组织的比较集合",
                    "property_effects": "性质变化、排序和异常点",
                    "recommendations": "结构关系和计算质量均通过的候选或条件",
                },
                "solution_path": [
                    "用 StructureMatcher 验证父结构关系并排除错误配对。",
                    "对应变结构计算 Deformation 和 Strain；对替位或超胞记录具体变换。",
                    "用 Tensor 表示介电、输运等张量并在需要时按对称性约简。",
                    "按稳定 structure_id 关联性质，质量复核后再比较和推荐。",
                ],
            },
            {
                "description": (
                    "对于需要判断可合成性或缺陷生长条件的半导体项目，使用同一能量协议下的竞争相记录建立"
                    "相图和化学势区域，评估候选相稳定性，并为后续缺陷形成能提供有来源的化学势边界。"
                ),
                "input": {
                    "target_compositions": "目标半导体组成",
                    "computed_entries": "目标相和相关竞争相的结构、总能及协议元数据",
                    "open_elements": "需要构造富/贫条件的元素集合",
                },
                "output": {
                    "phase_diagram": "稳定相、分解产物和 hull 距离",
                    "chemical_potential_region": "满足相稳定性的元素化学势边界",
                    "defect_conditions": "可供缺陷形成能使用的具名生长条件",
                    "limitations": "竞争相或协议缺失造成的边界不完整性",
                },
                "solution_path": [
                    "用 ComputedStructureEntry 组织能量并按计算协议分组。",
                    "用 PhaseDiagram 计算稳定性和分解关系。",
                    "用 ChemicalPotentialDiagram 得到允许区域和富/贫极限。",
                    "记录参与边界的竞争相，不在数据不全时声称完整稳定窗口。",
                ],
            },
        ],
        "evidence": {
            "scope_rule": (
                "以跨半导体材料复用的结构与电子性质工作流为边界；实例覆盖不同材料和文件类型，但不要求单个项目拥有全部数据。"
            ),
            "real_applications": [
                {
                    "title": "Design of high-mobility p-type GaN via the piezomobility tensor",
                    "url": "https://doi.org/10.1103/z22d-vlvc",
                    "relevance": "展示结构应变、输入一致性和迁移率张量关联的实际用途。",
                },
                {
                    "title": "Accelerating the discovery of high-performance nonlinear optical materials",
                    "url": "https://doi.org/10.1039/D5TC01335F",
                    "relevance": "公开多种无机半导体的 Structure、带隙、介电和 SHG 张量数据。",
                },
                {
                    "title": "Python Materials Genomics (pymatgen)",
                    "url": "https://doi.org/10.1016/j.commatsci.2012.10.028",
                    "relevance": "pymatgen 结构表示、输入生成和计算结果分析的基础依据。",
                },
            ],
            "public_examples": [
                {
                    "workflow": "GaN 六方向应变与空穴迁移率",
                    "url": "https://doi.org/10.24435/materialscloud:zy-qw",
                    "contents": "37 组 QE/EPW 输入输出和迁移率张量。",
                },
                {
                    "workflow": "Si 静态与能带解析",
                    "url": "https://github.com/materialsproject/pymatgen-test-files/tree/main/io/vasp",
                    "contents": "关联的静态输出、line-mode KPOINTS 和能带 vasprun.xml。",
                },
                {
                    "workflow": "无机半导体带隙、介电和非线性光学筛选",
                    "url": "https://doi.org/10.24435/materialscloud:wk-qm",
                    "contents": "CIF、Structure 字典、带隙、介电和 SHG 张量。",
                },
            ],
            "supporting_packages": [
                "numpy",
                "scipy",
                "spglib",
                "Quantum ESPRESSO",
                "VASP",
            ],
        },
    },
}


def build_seed(package_name: str) -> dict[str, Any]:
    source = deepcopy(_load_single_seed(SOURCE_FILES[package_name]))
    profile = PROFILES[package_name]
    basic_info = source["environment"]["basic_info"]
    basic_info["index"] = profile["index"]
    basic_info["url"] = profile["urls"]
    source["global_id"] = _global_id(
        basic_info["source"], basic_info["name"], basic_info["index"]
    )
    source["environment"]["description"] = profile["description"]
    source["environment"]["domain"] = deepcopy(profile["domain"])
    source["init_ref_tools"] = _select_tools(
        source["init_ref_tools"], profile["module_symbols"]
    )
    source["init_ref_tasks"] = deepcopy(profile["tasks"])

    extraction = source["others"]["python_source_extraction"]
    extraction["requested_modules"] = list(profile["module_symbols"])
    extraction["source_files"] = _select_source_files(
        extraction["source_files"], profile["module_symbols"]
    )
    extraction["selection"] = (
        "public classes and functions used across the named semiconductor workflow family; "
        "material examples do not restrict the supported material system"
    )
    extraction["excluded"] = (
        "package APIs unrelated to semiconductor structure, electronic-property, defect, "
        "or workflow operations in this Seed"
    )
    source["others"]["application_evidence"] = deepcopy(profile["evidence"])
    return source


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
    print(f"Wrote {len(seeds)} general semiconductor Seeds to {args.output}")


if __name__ == "__main__":
    main()
