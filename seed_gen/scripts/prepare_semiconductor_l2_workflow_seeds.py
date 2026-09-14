#!/usr/bin/env python3
"""Generate two L2 workflow Seeds and combine them with the existing core Seeds."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from extract_python_ref_tools import extract_file, extract_modules


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "pypi_outputs"
EXISTING_SEEDS = OUTPUT_DIR / "semiconductor_general_core_application_seeds_v3_20260910.json"
DEFAULT_OUTPUT = OUTPUT_DIR / "semiconductor_l2_workflow_seeds_v4_20260911.json"

DOPED_MODULE_SYMBOLS: dict[str, tuple[str, ...]] = {
    "doped.core": (
        "DefectEntry", "Vacancy", "Substitution", "Interstitial", "is_shallow",
    ),
    "doped.generation": (
        "DefectsGenerator", "get_defect_entry_from_defect", "guess_defect_charge_states",
        "get_ideal_supercell_matrix", "get_interstitial_sites",
    ),
    "doped.vasp": ("DefectRelaxSet", "DefectsSet", "scaled_ediff"),
    "doped.analysis": (
        "DefectsParser", "DefectParser", "defect_entry_from_paths",
        "defect_from_structures", "shallow_dopant_binding_energy",
    ),
    "doped.corrections": ("get_freysoldt_correction", "get_kumagai_correction"),
    "doped.chemical_potentials": (
        "CompetingPhases", "CompetingPhasesAnalyzer", "get_doped_chempots_from_entries",
        "get_X_rich_limit", "get_X_poor_limit",
    ),
    "doped.thermodynamics": (
        "DefectThermodynamics", "FermiSolver", "get_e_h_concs", "scissor_dos",
    ),
}

SHAKENBREAK_MODULE_SYMBOLS: dict[str, tuple[str, ...]] = {
    "shakenbreak.input": (
        "Distortions", "apply_snb_distortions", "distort_and_rattle_defect_entry",
    ),
    "shakenbreak.distortions": ("distort_and_rattle", "local_mc_rattle"),
    "shakenbreak.analysis": (
        "get_gs_distortion", "get_energies", "compare_structures",
    ),
    "shakenbreak.energy_lowering_distortions": (
        "get_energy_lowering_distortions", "write_retest_inputs",
        "write_groundstate_structure",
    ),
}

KLAYOUT_CLASS_METHODS: dict[str, tuple[str, ...]] = {
    "LayerInfo": ("new", "from_string", "to_s"),
    "Layout": (
        "new", "read", "write", "read_bytes", "write_bytes", "create_cell", "cell_by_name",
        "top_cells", "each_cell", "layer", "layer_infos", "clip", "flatten", "cleanup",
    ),
    "Cell": (
        "new", "bbox", "bbox_per_layer", "shapes", "each_inst", "begin_shapes_rec",
        "begin_shapes_rec_overlapping", "flatten", "transform", "copy_tree_shapes",
    ),
    "Region": (
        "new", "area", "count", "merge", "merged", "inside", "interacting", "overlapping",
        "width_check", "space_check", "enclosed_check", "enclosing_check", "overlap_check",
        "notch_check", "isolated_check", "sized", "moved", "insert_into", "write",
    ),
    "Shapes": ("new", "each", "insert", "erase", "replace", "transform"),
    "RecursiveShapeIterator": (
        "new", "each", "select_cells", "unselect_cells", "reset", "shape", "path", "layer",
    ),
    "LayoutDiff": ("new", "compare"),
    "LoadLayoutOptions": ("new", "select_all_layers", "set_layer_map"),
    "SaveLayoutOptions": (
        "new", "set_format_from_filename", "select_all_cells", "select_all_layers",
        "select_cell", "add_layer",
    ),
}

GDSTK_CLASS_METHODS: dict[str, tuple[str, ...]] = {
    "Library": ("__init__", "new_cell", "add", "top_level", "write_gds", "write_oas"),
    "Cell": (
        "__init__", "add", "remove", "area", "bounding_box", "dependencies", "flatten",
        "get_polygons", "get_paths", "get_labels",
    ),
    "Polygon": (
        "__init__", "area", "bounding_box", "contain", "fillet", "fracture", "translate",
        "rotate", "scale", "transform",
    ),
    "Reference": ("__init__", "bounding_box", "get_polygons", "get_paths", "get_labels"),
    "FlexPath": ("__init__", "segment", "arc", "turn", "to_polygons", "translate"),
    "RobustPath": ("__init__", "segment", "arc", "turn", "to_polygons", "translate"),
}
GDSTK_FUNCTIONS = {"read_gds", "read_oas", "boolean", "inside", "offset", "slice"}


def _read_seeds(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"Seed file must contain a non-empty object array: {path}")
    return payload


def _write(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _select_tools(
    tools: list[dict[str, Any]], module_symbols: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    wanted = {(module, name) for module, names in module_symbols.items() for name in names}
    selected = [deepcopy(tool) for tool in tools if (tool.get("module"), tool.get("name")) in wanted]
    found = {(str(tool["module"]), str(tool["name"])) for tool in selected}
    if missing := sorted(wanted - found):
        raise ValueError(f"Missing extracted definitions: {missing}")
    return selected


def _trim_class_methods(tool: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    result = deepcopy(tool)
    methods = result.get("function", [])
    result["function"] = [method for method in methods if method.get("name") in names]
    found = {str(method.get("name")) for method in result["function"]}
    if missing := sorted(set(names) - found):
        raise ValueError(f"{tool['module']}.{tool['name']} is missing methods: {missing}")
    return result


def _doped_tasks() -> list[dict[str, Any]]:
    return [
        {
            "description": "从一批真实半导体母体结构系统生成空位、替位和间隙缺陷，选择满足最小镜像距离和原子数约束的超胞，并为每个对称不等价缺陷建立可追踪的候选电荷态集合。",
            "input": {"host_structures": "具有稳定材料 ID 的多种晶态半导体结构", "defect_families": ["vacancy", "substitution", "interstitial"], "supercell_constraints": "最小镜像距离、最大原子数及电荷态策略"},
            "output": {"defect_entries": "按 host_id、defect_id、site、supercell 和 charge 关联的缺陷记录", "audit": "对称去重、超胞质量及未生成缺陷的原因"},
            "solution_path": ["用 DefectsGenerator 枚举对称不等价缺陷。", "用 get_ideal_supercell_matrix 检查不同形状超胞。", "用 guess_defect_charge_states 建立并复核电荷态。", "保存母体、缺陷和超胞之间的稳定关联。"],
        },
        {
            "description": "为多个材料和电荷态生成一致的体相、缺陷弛豫及静态 VASP 输入，加入 ShakeNBreak 的成键与反成键畸变候选，并从每组候选中保留可比较的低能构型。",
            "input": {"defect_entries": "已定义超胞和电荷态的缺陷", "distortion_candidates": "来自 ShakeNBreak 的局部结构扰动", "protocol": "相同泛函、赝势族、k 点与收敛策略"},
            "output": {"calculation_sets": "可直接执行且具有来源关系的输入目录", "configuration_map": "未畸变和畸变构型到缺陷电荷态的关联"},
            "solution_path": ["用 DefectsSet 生成体相和缺陷输入。", "按缺陷与电荷态登记 ShakeNBreak 构型。", "比较输入协议并排除不兼容分支。", "保留构型来源和预期输出清单。"],
        },
        {
            "description": "批量审计真实缺陷计算目录，解析体相与多电荷态结果，识别缺文件、未收敛、声明电荷错误、结构重构或计算设置不一致，并形成可以增量修复的最小重算清单。",
            "input": {"bulk_and_defect_directories": "包含 vasprun.xml、OUTCAR、CONTCAR 和 LOCPOT 的项目目录", "project_manifest": "材料、缺陷、电荷态、协议和父计算 ID"},
            "output": {"accepted_entries": "可继续热力学分析的 DefectEntry", "run_audit": "完整、失败、不一致和缺失状态", "rerun_plan": "只重算受影响分支的建议"},
            "solution_path": ["用 DefectsParser 批量发现并解析目录。", "用 DefectParser 检查单个复杂异常。", "比较体相与缺陷结构、能量、设置和电荷。", "将问题定位到具体文件和计算分支。"],
        },
        {
            "description": "针对各向同性或各向异性介电常数，对带电缺陷执行 Freysoldt 或 Kumagai 有限尺寸修正，比较不同方案的电势对齐和误差，并拒绝缺少兼容 LOCPOT、OUTCAR 或介电数据的条目。",
            "input": {"charged_defect_entries": "带体相参照、电势和电荷的缺陷条目", "dielectric_data": "同一材料与协议下的标量或张量介电常数"},
            "output": {"corrected_entries": "含修正能和元数据的缺陷记录", "correction_audit": "方法、对齐、误差和拒绝理由"},
            "solution_path": ["依据可用电势数据选择修正方法。", "调用 get_freysoldt_correction 或 get_kumagai_correction。", "检查远离缺陷区域的对齐质量。", "保留修正前后能量与全部证据。"],
        },
        {
            "description": "从同一能量协议的目标相和竞争相结果建立化学势稳定区，把多缺陷多电荷态记录组合成形成能图和热力学跃迁能级，并比较富/贫生长条件下的主导补偿缺陷。",
            "input": {"phase_entries": "目标相及竞争相总能", "defect_entries": "同一母体的修正后多电荷态条目", "band_edges": "一致协议下的 VBM 和带隙"},
            "output": {"chemical_potential_limits": "有竞争相约束的生长条件", "formation_energies": "随费米能级变化的形成能", "transition_levels": "带隙内跃迁和稳定电荷区间"},
            "solution_path": ["用 CompetingPhasesAnalyzer 审计竞争相完备性。", "用 get_doped_chempots_from_entries 建立化学势边界。", "用 DefectThermodynamics 聚合形成能。", "比较目标掺杂与补偿缺陷并注明数据边界。"],
        },
        {
            "description": "结合缺陷热力学、电子态密度、有效态密度及外源掺杂条件，自洽求解不同温度下的费米能级、电子和空穴浓度及缺陷浓度，评估材料的 n 型或 p 型可掺杂性。",
            "input": {"defect_thermodynamics": "多缺陷形成能和简并度", "electronic_dos": "同一母体的电子态密度", "temperatures": "生长与测量温度范围", "dopant_constraints": "固定或可变外源掺杂浓度"},
            "output": {"fermi_level_series": "温度和生长条件对应的自洽费米能级", "carrier_and_defect_concentrations": "载流子与各缺陷浓度", "dopability_report": "主导缺陷、补偿机制和不确定性"},
            "solution_path": ["用 FermiSolver 建立电中性方程。", "用 get_e_h_concs 复核载流子浓度。", "扫描温度、化学势和掺杂条件。", "识别限制费米能级移动的主导缺陷。"],
        },
    ]


def _layout_tasks() -> list[dict[str, Any]]:
    return [
        {
            "description": "载入真实开放 PDK 的 GDS/OASIS 版图和 layer map，遍历顶层单元、层级实例、图层与多边形，识别缺失引用、意外空层、非法坐标和与工艺层定义不一致的图形。",
            "input": {"layouts": "IHP SG13G2 等真实 PDK 或设计项目的 GDS/OASIS", "technology": "layer map、单位、顶层单元和预期图层"},
            "output": {"hierarchy_inventory": "单元、实例、图层和边界统计", "integrity_issues": "带 cell/layer/bbox 定位的结构问题"},
            "solution_path": ["用 Layout.read 载入版图并检查单位。", "遍历 top_cells、Cell 实例和 RecursiveShapeIterator。", "按 LayerInfo 对齐工艺层。", "把每项异常定位到层级路径和边界框。"],
        },
        {
            "description": "对真实 PDK QA 单元、标准单元或测量版图执行宽度、间距、包围、重叠、凹口和密度规则检查，按 FEOL、BEOL、器件和特殊规则组运行，并保存可定位的违规 marker。",
            "input": {"layout": "与 PDK 匹配的 GDS", "drc_decks": "官方 KLayout DRC 脚本及规则配置", "rule_groups": "需要运行的规则组和参数"},
            "output": {"drc_run": "规则版本、参数、状态和汇总", "violations": "rule_id、cell、layer、geometry、severity 和 marker"},
            "solution_path": ["确认版图与规则 deck 使用同一工艺和单位。", "运行官方 KLayout DRC deck。", "用 Region 检查关键几何规则。", "按规则和层级汇总 marker 而不丢失原始定位。"],
        },
        {
            "description": "分析一批 DRC marker，区分单个局部形状问题、层级复用造成的重复违规和全局密度问题，选择不会破坏相邻层或设计意图的修复候选，并记录修复依据。",
            "input": {"violations": "带规则、层级路径和几何区域的 marker", "layout_context": "违规周边图形、单元复用和层定义", "rule_constraints": "最小宽度、间距、包围及禁止区域"},
            "output": {"repair_candidates": "移动、扩张、裁剪、布尔或保持不变的候选", "impact_analysis": "对相邻规则、实例和边界的影响"},
            "solution_path": ["把 marker 映射回原 cell 和 shape。", "用 Region 的相交、包含和尺寸操作取得局部上下文。", "识别应在源 cell 修复还是仅改单个实例。", "排除会产生新违规的候选。"],
        },
        {
            "description": "使用 gdstk 对选定多边形执行布尔、offset、slice、移动或层变换，写出候选 GDS，再由 KLayout 重新读取并运行同一组 DRC，只有违规减少且层级和非目标几何保持一致时才接受。",
            "input": {"layout": "待修复版图", "repair_candidates": "明确 shape、操作和目标几何", "baseline_violations": "修复前 marker 集合"},
            "output": {"repaired_layout": "可再次交付的 GDS/OASIS", "verification": "违规差异、版图差异和新增问题", "change_log": "每次几何修改与原因"},
            "solution_path": ["用 gdstk.read_gds 和层级引用定位图形。", "调用 boolean、offset 或 slice 形成候选。", "写出后用 KLayout 重新读取和 DRC。", "用 LayoutDiff 及 marker 差异验证非目标区域不变。"],
        },
        {
            "description": "比较同一设计的两个版图修订，区分预期 ECO、单元重命名、展平差异和真实几何变化，并把差异关联到变更说明与 DRC 结果，判断新版是否可以进入下一签核阶段。",
            "input": {"baseline_layout": "签核基线版图", "candidate_layout": "ECO 或修复后的版图", "expected_changes": "允许变化的 cell、layer 和区域", "drc_results": "两个版本的规则结果"},
            "output": {"layout_diff": "按 cell/layer 分类的增删改", "unexpected_changes": "超出批准范围的差异", "signoff_decision": "通过、返修或需人工复核"},
            "solution_path": ["规范化单位、顶层和 cell 映射。", "用 LayoutDiff.compare 获取结构化差异。", "将差异与 expected_changes 匹配。", "联合 DRC 回归结果作出签核决定。"],
        },
        {
            "description": "准备一组可交付版图：裁剪指定顶层、清理未引用单元、控制输出图层与格式，在 GDS 和 OASIS 间转换，并在写出后重新读取，核验层级、边界、图形数量、精度和规则结果未发生意外变化。",
            "input": {"project_layouts": "多个真实设计或 QA 顶层", "delivery_manifest": "顶层、允许图层、单位、格式与命名要求"},
            "output": {"deliverables": "按清单输出的 GDS/OASIS", "roundtrip_audit": "写出前后层级和几何比较", "excluded_content": "清理内容及原因"},
            "solution_path": ["用 Layout.clip/cleanup 生成交付视图。", "用 SaveLayoutOptions 选择 cell、layer 和格式。", "重新读取每个产物。", "比较层级、bbox、面积、图形数和 DRC 汇总。"],
        },
    ]


def build_doped_seed(
    template: dict[str, Any], source_root: Path, shakenbreak_source_root: Path,
) -> dict[str, Any]:
    tools, source_files = extract_modules(source_root, list(DOPED_MODULE_SYMBOLS))
    snb_tools, snb_source_files = extract_modules(
        shakenbreak_source_root, list(SHAKENBREAK_MODULE_SYMBOLS)
    )
    seed = deepcopy(template)
    seed["global_id"] = "pypi_doped_6"
    seed["environment"] = {
        "basic_info": {
            "source": "pypi", "name": "doped", "version": "v3.2.1", "index": 6,
            "url": ["https://pypi.org/project/doped/", "https://github.com/SMTG-Bham/doped", "https://doped.readthedocs.io/en/latest/", "https://doi.org/10.21105/joss.06433"],
        },
        "description": "这是一个面向多种晶态半导体的点缺陷、掺杂与载流子热力学环境，以 doped 为主要分析工具，并连接 pymatgen-analysis-defects 的缺陷对象、ShakeNBreak 的低能构型搜索和 py-sc-fermi 兼容的费米能级求解。环境覆盖母体与竞争相、空位/替位/间隙及复合缺陷、多电荷态 VASP 输入输出、有限尺寸修正、形成能、跃迁能级和温度相关浓度。任务基于 CdTe、Cu2SiSe3 等公开计算项目的数据形态，但材料范围不限于这些实例；要求同一结论内的结构、能量、电势、介电、带边和化学势具有明确项目及协议关联。",
        "domain": {"level1": "semiconductor", "level2": "point_defects", "level3": "defect_thermodynamics"},
    }
    seed["init_ref_tools"] = [
        *_select_tools(tools, DOPED_MODULE_SYMBOLS),
        *_select_tools(snb_tools, SHAKENBREAK_MODULE_SYMBOLS),
    ]
    seed["init_ref_tasks"] = _doped_tasks()
    seed["others"] = {
        "python_source_extraction": {
            "strategy": "static_ast", "source_version": "3.2.1",
            "requested_modules": [*DOPED_MODULE_SYMBOLS, *SHAKENBREAK_MODULE_SYMBOLS],
            "source_files": [*source_files, *snb_source_files],
            "selection": "public doped and ShakeNBreak classes and functions used across the complete semiconductor point-defect workflow",
            "excluded": "private helpers, plotting-only APIs, compatibility shims, and APIs unrelated to point-defect generation, parsing, corrections, thermodynamics, or carrier equilibrium",
        },
        "application_evidence": {
            "l2": "01.04 半导体点缺陷",
            "package_relationships": [
                {"package": "doped", "role": "anchor", "relationship": "缺陷生成、VASP 输入、结果解析、有限尺寸修正、形成能和浓度分析"},
                {"package": "pymatgen-analysis-defects", "role": "dependency", "relationship": "提供 doped 使用和扩展的缺陷对象与基础修正能力"},
                {"package": "ShakeNBreak", "role": "upstream", "relationship": "为缺陷电荷态生成局部畸变并搜索低能结构"},
                {"package": "py-sc-fermi", "role": "compatible_downstream", "relationship": "与费米能级及缺陷浓度求解互操作；doped 也提供自身 FermiSolver"},
            ],
            "real_applications": [
                {"title": "doped: Python toolkit for charged defect supercell calculations", "url": "https://doi.org/10.21105/joss.06433", "relevance": "说明从缺陷生成到热力学分析的完整真实计算工作流。"},
                {"title": "doped public CdTe and Cu2SiSe3 projects", "url": "https://github.com/SMTG-Bham/doped/tree/v3.2.1/examples", "relevance": "提供多材料结构、缺陷计算、化学势和热力学 JSON 等可下载工作文件。"},
            ],
            "data_directions": [
                "优先取得同一公开计算项目内的母体结构、缺陷超胞、多电荷态 vasprun.xml/OUTCAR/LOCPOT、介电与带边数据，保留 material-defect-charge-run 关联。",
                "取得目标相和竞争相结构及能量、具名化学势边界、形成能和热力学 JSON，使相稳定性、缺陷形成能和浓度求解可以连成一条工作流。",
                "补充同一缺陷的未畸变与 ShakeNBreak 畸变构型、成功与失败计算状态，用于低能结构比较和恢复任务。",
            ],
        },
    }
    return seed


def build_layout_seed(template: dict[str, Any], klayout_stub: Path, gdstk_stub: Path) -> dict[str, Any]:
    extracted = extract_file(klayout_stub, "klayout.db")
    klayout_by_name = {tool["name"]: tool for tool in extracted}
    tools = [_trim_class_methods(klayout_by_name[name], methods) for name, methods in KLAYOUT_CLASS_METHODS.items()]

    gdstk_extracted = extract_file(gdstk_stub, "gdstk")
    for tool in gdstk_extracted:
        name = str(tool.get("name"))
        if name in GDSTK_CLASS_METHODS:
            tools.append(_trim_class_methods(tool, GDSTK_CLASS_METHODS[name]))
        elif name in GDSTK_FUNCTIONS:
            tools.append(deepcopy(tool))

    seed = deepcopy(template)
    seed["global_id"] = "pypi_klayout_7"
    seed["environment"] = {
        "basic_info": {
            "source": "pypi", "name": "KLayout", "version": "v0.30.12", "index": 7,
            "url": ["https://pypi.org/project/klayout/", "https://github.com/KLayout/klayout", "https://www.klayout.de/doc-qt5/programming/python.html", "https://www.klayout.de/doc-qt5/manual/drc_basic.html", "https://github.com/IHP-GmbH/IHP-Open-PDK"],
        },
        "description": "这是一个真实半导体 PDK 与芯片版图的几何检查、DRC、修复和签核准备环境。KLayout 负责读取 GDS/OASIS、理解 cell/layer/hierarchy、执行真实规则 deck、保存 marker 并做最终复核；gdstk 作为直接协作的几何后端，完成布尔、offset、slice、层变换和候选版图写出。数据以 IHP SG13G2 开放 PDK 的 FEOL/BEOL/density DRC deck、QA cells、标准单元和测量版图为主要连贯上下文，并可扩展到其他具有真实 PDK 规则与设计文件的半导体项目。环境不把 gdspy 或 PICwriter 等替代框架强行并入同一工作流。",
        "domain": {"level1": "semiconductor", "level2": "gds_geometry_drc", "level3": "layout_verification_and_repair"},
    }
    seed["init_ref_tools"] = tools
    seed["init_ref_tasks"] = _layout_tasks()
    seed["others"] = {
        "python_source_extraction": {
            "strategy": "official_type_stubs", "source_version": "KLayout 0.30.12 + gdstk 1.0.1",
            "requested_modules": ["klayout.db", "gdstk"],
            "source_files": ["klayout/dbcore.pyi", "gdstk/_gdstk.pyi"],
            "selection": "public classes, functions, and methods used for layout I/O, hierarchy inspection, geometry checks, repair, round-trip comparison, and delivery",
            "excluded": "GUI-only APIs, unrelated extraction/schematic APIs, private bindings, and alternative gdspy/PICwriter frameworks",
        },
        "application_evidence": {
            "l2": "03.02 GDS / Geometry / DRC",
            "package_relationships": [
                {"package": "KLayout", "role": "anchor_and_verifier", "relationship": "版图读取、层级与图层检查、真实 DRC deck 执行、marker 输出和回归验证"},
                {"package": "gdstk", "role": "geometry_backend", "relationship": "针对 marker 定位的图形执行布尔、offset、slice、变换并写回 GDS/OASIS"},
                {"package": "gdspy", "role": "alternative", "relationship": "与 gdstk 能力重叠，不作为默认协作依赖"},
                {"package": "PICwriter", "role": "specialized_alternative", "relationship": "偏光子版图生成，不纳入通用芯片 DRC 环境"},
            ],
            "real_applications": [
                {"title": "IHP Open Source PDK", "url": "https://github.com/IHP-GmbH/IHP-Open-PDK", "relevance": "真实 130 nm BiCMOS PDK，包含 KLayout 技术文件、完整 DRC deck、QA GDS、标准单元和测量版图。"},
                {"title": "KLayout DRC manual", "url": "https://www.klayout.de/doc-qt5/manual/drc_basic.html", "relevance": "明确 Region 运算、宽度/间距等规则和 marker 输出工作方式。"},
            ],
            "data_directions": [
                "优先取得同一 PDK 版本中的 layer map、完整 KLayout DRC deck、规则配置和 QA GDS，使规则、工艺层及预期违规可以直接关联。",
                "取得真实标准单元、器件、测量结构或小型设计 GDS/OASIS，以及已知 DRC marker/report，用于层级审计、规则运行、违规定位与比较。",
                "保留可修改的原始版图、修复候选及前后版本关系，使 gdstk 几何修改、KLayout 重验和 LayoutDiff 可形成闭环。",
            ],
        },
    }
    return seed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doped-source-root", type=Path, required=True, help="Directory containing doped/")
    parser.add_argument("--shakenbreak-source-root", type=Path, required=True, help="Directory containing shakenbreak/")
    parser.add_argument("--klayout-stub", type=Path, required=True, help="KLayout dbcore.pyi")
    parser.add_argument("--gdstk-stub", type=Path, required=True, help="gdstk _gdstk.pyi")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    existing = _read_seeds(EXISTING_SEEDS)
    if len(existing) != 2:
        raise ValueError(f"Expected two existing core Seeds in {EXISTING_SEEDS}")
    template = existing[0]
    doped = build_doped_seed(
        template,
        args.doped_source_root.resolve(),
        args.shakenbreak_source_root.resolve(),
    )
    layout = build_layout_seed(template, args.klayout_stub.resolve(), args.gdstk_stub.resolve())

    _write(OUTPUT_DIR / "doped_semiconductor_point_defects_v3.2.1.json", [doped])
    _write(OUTPUT_DIR / "klayout_gdstk_semiconductor_drc_v0.30.12.json", [layout])
    _write(args.output, [*existing, doped, layout])
    print(f"Wrote four workflow Seeds to {args.output}")


if __name__ == "__main__":
    main()
