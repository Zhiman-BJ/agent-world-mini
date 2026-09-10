"""Materialize explicitly reviewed device/defect API selections.

This is a one-time, release-pinned profile authoring aid, not a ranking algorithm.
Existing profiles are never silently overwritten or re-pinned to changed sources.
Routine regeneration uses select_release_python_seeds --manifest instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from seed_gen.scripts.select_python_ref_tools import _canonical_sha256, select_seed


def capability(cid, description, reason, symbols, evidence, verification):
    return dict(id=cid, description=description, selection_reason=reason,
                definitions=symbols, evidence_sources=evidence,
                evidence=["public_api", "core_capability", "composable_io"],
                verification_tier=verification)


def funcs(module, names):
    return [(module + "." + name, None) for name in names.split()]


def cls(module, name, methods):
    return (module + "." + name, methods.split())


def selections():
    C = capability
    result = {}
    result["gdsfactory"] = (
        "版图构造、器件、截面/路径、布线、工艺层与 GDS 闭环。仅直接定义接口；ComponentBase 方法可用于 Component 实例，kfactory 继承构造和引用变换需要后续适配。排除样例、GUI、服务与外部仿真插件。",
        [
            C("layout_objects", "组件编辑、区域查询和 GDS 保存", "组件与基础类共同构成建模、端口、查询和持久化链；不重复展开继承方法。", [
                cls("gdsfactory.component", "ComponentBase", "add_port add_label get_ports_list write_gds to_dict get_netlist"),
                cls("gdsfactory.component", "Component", "dup add_ref absorb area get_polygons get_region get_polygons_points extract remove_layers remap_layers add_polygon"),
                *funcs("gdsfactory.read.import_gds", "import_gds"),
                *funcs("gdsfactory.boolean", "boolean"),
                *funcs("gdsfactory.grid", "grid"), *funcs("gdsfactory.pack", "pack"),
            ], ["seed_pypi_raw/gdsfactory/gdsfactory/component.py", "seed_pypi_raw/gdsfactory/gdsfactory/__init__.py"], "native_geometry_roundtrip_planned"),
            C("parametric_devices", "代表性光子和电学器件", "覆盖直波导、弯曲、过渡、分束耦合、干涉谐振、光栅及电连接，避免同类器件的长尾变体。", [
                *funcs("gdsfactory.components.waveguides.straight", "straight"),
                *funcs("gdsfactory.components.bends.bend_euler", "bend_euler"),
                *funcs("gdsfactory.components.bends.bend_circular", "bend_circular"),
                *funcs("gdsfactory.components.tapers.taper", "taper"),
                *funcs("gdsfactory.components.shapes.rectangle", "rectangle"),
                *funcs("gdsfactory.components.shapes.circle", "circle"),
                *funcs("gdsfactory.components.mmis.mmi1x2", "mmi1x2"),
                *funcs("gdsfactory.components.mmis.mmi2x2", "mmi2x2"),
                *funcs("gdsfactory.components.couplers.coupler", "coupler"),
                *funcs("gdsfactory.components.rings.ring_single", "ring_single"),
                *funcs("gdsfactory.components.mzis.mzi", "mzi"),
                *funcs("gdsfactory.components.grating_couplers.grating_coupler_elliptical", "grating_coupler_elliptical"),
                *funcs("gdsfactory.components.pads.pad", "pad"),
                *funcs("gdsfactory.components.vias.via_stack", "via_stack"),
            ], ["seed_pypi_raw/gdsfactory/gdsfactory/components/__init__.py"], "fixed_pdk_geometry_planned"),
            C("paths_and_cross_sections", "路径、截面与挤出", "由可查询的几何路径和截面构造器生成版图；不将挤出等同于物理网格生成。", [
                cls("gdsfactory.path", "Path", "__init__ append length curvature offset extrude copy mirror invert"),
                *funcs("gdsfactory.path", "arc euler straight smooth transition"),
                cls("gdsfactory.cross_section.base", "CrossSection", "copy mirror validate_radius get_xmin_xmax"),
                *funcs("gdsfactory.cross_section.utils", "cross_section"),
                *funcs("gdsfactory.cross_section.presets", "strip rib metal_routing"),
                *funcs("gdsfactory.cross_section.pn_junction", "pn"),
            ], ["seed_pypi_raw/gdsfactory/gdsfactory/path.py", "seed_pypi_raw/gdsfactory/gdsfactory/cross_section/base.py"], "native_geometry_fixture_planned"),
            C("routing_and_technology", "布线、工艺层与栅格检查", "单线/束线及电布线配合 PDK 和层堆栈查询，为网格适配提供层厚、材料和 z 坐标。", [
                *funcs("gdsfactory.routing.route_single", "route_single route_single_electrical"),
                *funcs("gdsfactory.routing.route_bundle", "route_bundle"),
                *funcs("gdsfactory.routing.route_fiber_array", "route_fiber_array"),
                *funcs("gdsfactory.pdk", "get_active_pdk get_component get_cross_section get_layer get_layer_stack"),
                cls("gdsfactory.technology.layer_stack", "LayerLevel", "bounds"),
                cls("gdsfactory.technology.layer_stack", "LayerStack", "__init__ get_layer_to_thickness get_layer_to_zmin get_layer_to_material get_layer_to_mesh_order to_dict filtered"),
                *funcs("gdsfactory.snap", "snap_to_grid is_on_grid assert_on_grid"),
            ], ["seed_pypi_raw/gdsfactory/gdsfactory/technology/layer_stack.py", "seed_pypi_raw/gdsfactory/gdsfactory/routing/route_single.py"], "fixed_pdk_routing_planned"),
        ], None)
    result["klayout"] = (
        "独立 Python 包的无界面版图与几何核心，公开命名空间 klayout.db；排除 GUI、显式内存管理、调试与专用长尾格式。重载完整定义见全量索引 others，精选签名只是一个真实重载。dbu、图层属性等数据字段需未来封装显式暴露。",
        [
            C("layout_hierarchy", "版图库、图层与单元层次", "构造/读写 Layout、按层查询 Cell 与插入子单元形成可保存的版图。Cell 对象优先由 Layout 创建。", [
                cls("klayout.db", "Layout", "__init__ create_cell cell top_cell top_cells layer layer_infos layer_indexes get_info read write each_cell"),
                cls("klayout.db", "Cell", "cell_index bbox bbox_per_layer shapes insert each_inst begin_shapes_rec flatten transform"),
                cls("klayout.db", "CellInstArray", "__init__ bbox size transformed"),
                cls("klayout.db", "LayerInfo", "__init__ to_s is_equivalent"),
                cls("klayout.db", "Shapes", "insert each size is_empty"),
                cls("klayout.db", "Shape", "area bbox is_box is_polygon"),
            ], ["seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/db/__init__.py", "seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/dbcore.pyi"], "native_gds_roundtrip_planned"),
            C("geometry_primitives", "整数几何与坐标变换", "只选一套整数几何，统一单位并减少浮点/整数重复表面；保留必要构造、面积和变换。", [
                cls("klayout.db", "Point", "__init__ distance moved"),
                cls("klayout.db", "Box", "__init__ area center contains overlaps enlarged transformed"),
                cls("klayout.db", "Polygon", "__init__ area bbox insert_hole holes each_point_hull transformed"),
                cls("klayout.db", "Trans", "__init__ inverted trans is_mirror"),
            ], ["seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/dbcore.pyi"], "exact_integer_geometry_planned"),
            C("region_operations", "区域布尔运算、间距与宽度检查", "区域对象支持组合和量化几何验证，输出可重新插入版图；检查结果不是完整工艺 DRC。", [
                cls("klayout.db", "Region", "__init__ insert area bbox count each is_empty merged and_ or_ not_ xor sized interacting inside width_check space_check"),
            ], ["seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/dbcore.pyi"], "boolean_area_and_drc_fixture_planned"),
        ], None)
    result["gmsh"] = (
        "无界面的几何构建、物理分组、网格尺寸场、生成、节点/单元查询与保存。优先 OCC 内核，补充基本 geo 线面入口；排除 GUI、ONELAB、插件与底层有限元基函数接口。静态命名空间按函数计数。",
        [
            C("session_and_groups", "会话、模型、物理标签与文件", "生命周期和显式分组支持器件区域、接触与界面映射；尺寸单位和 MSH 版本需在后续任务中固定。", [
                *funcs("gmsh", "initialize isInitialized finalize open merge write clear"),
                *funcs("gmsh.option", "setNumber getNumber"),
                *funcs("gmsh.model", "add remove list getCurrent setCurrent getEntities getPhysicalGroups addPhysicalGroup setPhysicalName getPhysicalName getEntitiesForPhysicalGroup getPhysicalGroupsForEntity getBoundary getBoundingBox getDimension"),
            ], ["seed_pypi_raw/gmsh/api/gmsh.py", "seed_pypi_raw/gmsh/tutorials/python/t1.py"], "native_session_and_msh_roundtrip_planned"),
            C("geometry_construction", "基本线面与 OCC 实体建模", "布尔分割和同步用于层状及多区域器件；选择核心几何原语而非全部 CAD 导入接口。", [
                *funcs("gmsh.model.occ", "addPoint addLine addCurveLoop addPlaneSurface addRectangle addDisk addBox addSphere addCylinder extrude fuse cut intersect fragment translate rotate copy remove synchronize getEntities getMass getCenterOfMass"),
                *funcs("gmsh.model.geo", "addPoint addLine addCurveLoop addPlaneSurface synchronize"),
            ], ["seed_pypi_raw/gmsh/api/gmsh.py", "seed_pypi_raw/gmsh/tutorials/python/t16.py"], "native_small_geometry_fixture_planned"),
            C("mesh_generation", "网格生成、局部尺寸与质量查询", "覆盖生成及节点/单元/质量检查，保留局部尺寸场和背景场；材料参数由下游求解器定义。", [
                *funcs("gmsh.model.mesh", "generate clear setSize getSizes refine optimize getNodes getNode getElements getElement getElementTypes getElementProperties getElementQualities setOrder getNodesForPhysicalGroup setTransfiniteCurve setTransfiniteSurface setRecombine"),
                *funcs("gmsh.model.mesh.field", "add setNumber setNumbers setString setAsBackgroundMesh remove"),
            ], ["seed_pypi_raw/gmsh/api/gmsh.py", "seed_pypi_raw/gmsh/tutorials/python/t10.py"], "native_mesh_quality_and_label_fixture_planned"),
        ], None)
    result["meshio"] = (
        "统一网格对象、点/单元字段和集合转换、通用文件读写及 XDMF 时间序列。通过通用 read/write 的 file_format 覆盖格式，不重复收录数十套格式实现；不凑足 50。",
        [
            C("mesh_data", "网格和标签数据结构", "保留公开重导出的 Mesh/CellBlock 及字段/集合操作，支持几何与物理标签的数组级比较。", [
                cls("meshio._mesh", "CellBlock", "__init__"),
                cls("meshio._mesh", "Mesh", "__init__ copy get_cells_type get_cell_data cells_dict cell_data_dict cell_sets_dict cell_sets_to_data point_sets_to_data cell_data_to_sets point_data_to_sets"),
            ], ["seed_pypi_raw/meshio/src/meshio/__init__.py", "seed_pypi_raw/meshio/src/meshio/_mesh.py"], "array_and_label_fixture_planned"),
            C("mesh_files", "格式转换与时间序列", "通用读写避免实现模块重复；时间序列类须用 context manager 管理 HDF5 文件，__enter__/__exit__ 按现行口径不计入工具。", [
                *funcs("meshio._helpers", "read write write_points_cells"),
                cls("meshio.xdmf.time_series", "TimeSeriesReader", "__init__ read_points_cells read_data"),
                cls("meshio.xdmf.time_series", "TimeSeriesWriter", "__init__ write_points_cells write_data"),
            ], ["seed_pypi_raw/meshio/README.md", "seed_pypi_raw/meshio/src/meshio/xdmf/time_series.py"], "msh_vtu_xdmf_roundtrip_planned"),
        ], "统一 read/write 已覆盖多种格式；追加格式内部 read/write 或 XML/CLI 辅助函数只会重复或降低抽象层次，合理核心范围为 21 个调用。")
    result["pymatgen"] = (
        "补充已有 pymatgen-core 的高级分析：计算能量兼容性、局部配位、衍射/光学、表面与扩散过渡态。相图、组成和结构在 core 中，不重复采集。排除在线数据库、GUI、CLI、电子结构程序执行及长尾算法辅助函数。",
        [
            C("energy_compatibility", "能量条目修正与分组", "修正入口和兼容性基类配合使用；输入条目必须保留计算参数，不承诺任意能量可直接混合。", [
                cls("pymatgen.analysis.compatibility", "Compatibility", "process_entry process_entries"),
                cls("pymatgen.analysis.compatibility", "MaterialsProject2020Compatibility", "__init__ get_adjustments"),
                cls("pymatgen.analysis.compatibility.computed_entries", "GibbsComputedStructureEntry", "__init__ from_entries gf_sisso as_dict from_dict"),
                cls("pymatgen.analysis.compatibility.entry_tools", "EntrySet", "__init__ ground_states get_subset_in_chemsys as_dict"),
                *funcs("pymatgen.analysis.compatibility.entry_tools", "group_entries_by_structure group_entries_by_composition"),
            ], ["seed_pypi_raw/pymatgen/src/pymatgen/analysis/compatibility/__init__.py"], "metadata_preserving_energy_fixture_planned"),
            C("local_environment", "晶体局部配位与原型识别", "提供结构到局部配位及标签结果的高层入口，裁去底层置换、权重和图搜索细节。", [
                cls("pymatgen.analysis.chemenv.coordination_environments.coordination_geometry_finder", "LocalGeometryFinder", "__init__ setup_parameters setup_structure compute_structure_environments compute_coordination_environments"),
                cls("pymatgen.analysis.chemenv.coordination_environments.chemenv_strategies", "SimplestChemenvStrategy", "__init__"),
                cls("pymatgen.analysis.chemenv.coordination_environments.structure_environments", "LightStructureEnvironments", "from_structure_environments get_statistics site_contains_environment as_dict from_dict"),
                cls("pymatgen.analysis.prototypes", "AflowPrototypeMatcher", "__init__ get_prototypes"),
            ], ["seed_pypi_raw/pymatgen/src/pymatgen/analysis/chemenv/coordination_environments/coordination_geometry_finder.py"], "fixed_crystal_coordination_planned"),
            C("diffraction_and_optics", "衍射、介电响应与吸收", "由结构或给定电子结构结果计算可数值比较的谱，不调用外部 DFT；太阳能评估保留顶层入口。", [
                cls("pymatgen.analysis.diffraction.xrd", "XRDCalculator", "__init__ get_pattern"),
                cls("pymatgen.analysis.diffraction.neutron", "NDCalculator", "__init__ get_pattern"),
                cls("pymatgen.analysis.optics", "DielectricAnalysis", "__init__ from_vasprun"),
                *funcs("pymatgen.analysis.solar.slme", "absorption_coefficient slme"),
            ], ["seed_pypi_raw/pymatgen/src/pymatgen/analysis/diffraction/xrd.py", "seed_pypi_raw/pymatgen/src/pymatgen/analysis/optics.py"], "spectrum_fixture_planned"),
            C("interfaces_and_surfaces", "界面反应、表面热力学与形貌", "将给定相图或表面能映射为反应路径和形貌指标；基础相图由 core 提供。", [
                cls("pymatgen.analysis.interface_reactions", "InterfacialReactivity", "__init__ get_kinks get_dataframe minimum products"),
                cls("pymatgen.analysis.surface_analysis", "SlabEntry", "__init__ surface_energy surface_area as_dict"),
                cls("pymatgen.analysis.surface_analysis", "WorkFunctionAnalyzer", "__init__ from_files is_converged"),
                cls("pymatgen.analysis.wulff", "WulffShape", "__init__ volume surface_area area_fraction_dict weighted_surface_energy anisotropy"),
            ], ["seed_pypi_raw/pymatgen/src/pymatgen/analysis/interface_reactions.py", "seed_pypi_raw/pymatgen/src/pymatgen/analysis/wulff.py"], "surface_energy_geometry_fixture_planned"),
            C("barriers_and_thermal_properties", "迁移势垒和热性质", "分析已有 NEB 结果与体积能量曲线；不包含 NEB/DFT 执行。", [
                cls("pymatgen.analysis.transition_state", "NEBAnalysis", "__init__ from_outcars get_extrema as_dict"),
                cls("pymatgen.analysis.quasiharmonic", "QuasiHarmonicDebyeApprox", "__init__ vibrational_free_energy debye_temperature thermal_conductivity get_summary_dict"),
            ], ["seed_pypi_raw/pymatgen/src/pymatgen/analysis/transition_state.py", "seed_pypi_raw/pymatgen/src/pymatgen/analysis/quasiharmonic.py"], "precomputed_energy_fixture_planned"),
        ], None)
    result["pymatgen-analysis-defects"] = (
        "缺陷表示和生成、超胞、FNV/eFNV 修正及形成能/费米能级分析。保留来源定义与必要基类，dataclass 自动构造不伪造为显式 __init__；排除可选 SOAP 搜索、底层积分和复合系数细节。",
        [
            C("defect_objects", "缺陷对象与超胞", "基类承载继承方法，具体类型提供实际缺陷结构；下游可由生成器获得缺陷。", [
                cls("pymatgen.analysis.defects.core", "Defect", "__init__ get_charge_states get_supercell_structure centered_defect_structure"),
                cls("pymatgen.analysis.defects.core", "Vacancy", "name defect_structure defect_site_index element_changes"),
                cls("pymatgen.analysis.defects.core", "Substitution", "__init__ name defect_structure element_changes"),
                cls("pymatgen.analysis.defects.core", "Interstitial", "__init__ name defect_structure element_changes"),
                cls("pymatgen.analysis.defects.core", "DefectComplex", "__init__ defect_structure element_changes name"),
                *funcs("pymatgen.analysis.defects.supercells", "get_sc_fromstruct get_matched_structure_mapping get_closest_sc_mat"),
            ], ["seed_pypi_raw/pymatgen-analysis-defects/pymatgen/analysis/defects/core.py"], "fixed_structure_and_supercell_planned"),
            C("defect_generation", "不等价缺陷的生成", "覆盖空位、替位、反位及间隙候选，避免把抽象生成器当可直接实例化入口。", [
                cls("pymatgen.analysis.defects.generators", "VacancyGenerator", "__init__ generate"),
                cls("pymatgen.analysis.defects.generators", "SubstitutionGenerator", "__init__ generate"),
                cls("pymatgen.analysis.defects.generators", "AntiSiteGenerator", "__init__ generate"),
                cls("pymatgen.analysis.defects.generators", "InterstitialGenerator", "__init__ generate"),
                cls("pymatgen.analysis.defects.generators", "VoronoiInterstitialGenerator", "__init__ generate"),
                cls("pymatgen.analysis.defects.generators", "ChargeInterstitialGenerator", "__init__ generate"),
                *funcs("pymatgen.analysis.defects.generators", "generate_all_native_defects"),
            ], ["seed_pypi_raw/pymatgen-analysis-defects/pymatgen/analysis/defects/generators.py"], "symmetry_and_charge_density_fixture_planned"),
            C("corrections_and_thermodynamics", "电荷修正、形成能与平衡", "覆盖结构和能量条目到修正、形成能、浓度和费米能级链，使用本地预计算结果。", [
                *funcs("pymatgen.analysis.defects.corrections.freysoldt", "get_freysoldt_correction"),
                *funcs("pymatgen.analysis.defects.corrections.kumagai", "get_structure_with_pot get_efnv_correction"),
                cls("pymatgen.analysis.defects.thermo", "DefectEntry", "get_freysoldt_correction corrected_energy get_ediff get_summary_dict"),
                cls("pymatgen.analysis.defects.thermo", "FormationEnergyDiagram", "with_atomic_entries chempot_limits competing_phases get_transitions get_formation_energy get_concentration as_dataframe get_chempots"),
                cls("pymatgen.analysis.defects.thermo", "MultiFormationEnergyDiagram", "with_atomic_entries solve_for_fermi_level"),
                *funcs("pymatgen.analysis.defects.thermo", "group_defect_entries group_formation_energy_diagrams"),
                *funcs("pymatgen.analysis.defects.plotting.thermo", "plot_formation_energy_diagrams get_plot_data"),
            ], ["seed_pypi_raw/pymatgen-analysis-defects/pymatgen/analysis/defects/thermo.py"], "precomputed_correction_and_charge_neutrality_planned"),
        ], None)
    result["doped"] = (
        "缺陷生成、电荷态、VASP 输入写出、本地结果解析、有限尺寸修正、化学势与热力学。排除在线数据库查询、运行外部程序、旧修正实现和辅助缓存/日志。只选择必要属性及高层分析入口。",
        [
            C("defect_generation", "缺陷生成与序列化", "从结构生成缺陷和电荷态，保存可重放输入；Defect 基类提供共同操作。", [
                cls("doped.generation", "DefectsGenerator", "__init__ add_charge_states remove_charge_states as_dict from_dict to_json from_json"),
                *funcs("doped.generation", "get_defect_entry_from_defect guess_defect_charge_states get_ideal_supercell_matrix get_interstitial_sites"),
                cls("doped.core", "Defect", "__init__ get_supercell_structure get_charge_states as_dict"),
                cls("doped.core", "Vacancy", "__init__"), cls("doped.core", "Substitution", "__init__"), cls("doped.core", "Interstitial", "__init__"),
                *funcs("doped.core", "doped_defect_from_pmg_defect"),
            ], ["seed_pypi_raw/doped/doped/generation.py", "seed_pypi_raw/doped/doped/core.py"], "fixed_structure_generation_planned"),
            C("calculation_io", "输入和已完成计算结果", "VASP 输入与现成输出文件构成工作流边界；不自动运行 VASP，不下载赝势。", [
                cls("doped.vasp", "DefectDictSet", "__init__ write_input incar nelect"),
                cls("doped.vasp", "DefectsSet", "__init__ write_files"),
                cls("doped.analysis", "DefectsParser", "__init__ get_defect_thermodynamics"),
                cls("doped.analysis", "DefectParser", "__init__ from_paths apply_corrections"),
                *funcs("doped.analysis", "defect_from_structures defect_entry_from_paths shallow_dopant_binding_energy"),
            ], ["seed_pypi_raw/doped/doped/vasp.py", "seed_pypi_raw/doped/doped/analysis.py"], "local_vasp_fixture_and_potcar_configuration_required"),
            C("entry_corrections", "单缺陷能量、浓度与电荷修正", "DefectEntry 保留输入恢复和核心物理结果；独立修正函数用于显式控制数据。", [
                cls("doped.core", "DefectEntry", "as_dict from_dict to_json from_json get_ediff corrected_energy formation_energy equilibrium_concentration"),
                *funcs("doped.corrections", "get_freysoldt_correction get_kumagai_correction"),
            ], ["seed_pypi_raw/doped/doped/core.py", "seed_pypi_raw/doped/doped/corrections.py"], "precomputed_electrostatic_fixture_planned"),
            C("chemical_potentials", "化学势稳定范围与采样", "使用本地能量条目，不选联网 competing phase 搜索。", [
                cls("doped.chemical_potentials", "CompetingPhasesAnalyzer", "__init__ calculate_chempots get_formation_energy_df as_dict from_dict"),
                cls("doped.chemical_potentials", "ChemicalPotentialGrid", "__init__ get_grid get_constrained_grid"),
                *funcs("doped.chemical_potentials", "get_doped_chempots_from_entries get_X_rich_limit get_X_poor_limit"),
            ], ["seed_pypi_raw/doped/doped/chemical_potentials.py"], "local_phase_entries_fixture_planned"),
            C("defect_thermodynamics", "形成能、跃迁能级、浓度与费米能级", "高层对象和 FermiSolver 扫描构成缺陷到热力学链；后端和冻结约束需固定。", [
                cls("doped.thermodynamics", "DefectThermodynamics", "__init__ as_dict from_dict add_entries get_formation_energies get_formation_energy get_transition_levels get_equilibrium_concentrations get_equilibrium_fermi_level get_fermi_level_and_concentrations get_dopability_limits get_doping_windows plot"),
                cls("doped.thermodynamics", "FermiSolver", "__init__ scan_temperature scan_dopant_concentration scan_chempots scan_chemical_potential_grid"),
                *funcs("doped.thermodynamics", "get_fermi_dos get_e_h_concs scissor_dos"),
            ], ["seed_pypi_raw/doped/doped/thermodynamics.py"], "charge_neutrality_and_frozen_concentration_planned"),
        ], None)
    result["shakenbreak"] = (
        "缺陷识别、可控畸变/随机扰动、各支持后端的输入文件、弛豫结果解析与基态比较。排除重复 Click CLI、作业运行和纯格式打印；随机过程需固定种子，外部弛豫结果使用固定样例。",
        [
            C("distortion_generation", "缺陷识别和候选结构", "从原结构及缺陷对象生成可复现候选；保留键长/二聚体/局部扰动选择。", [
                *funcs("shakenbreak.input", "identify_defect generate_defect_object distort_and_rattle_defect_entry apply_snb_distortions"),
                *funcs("shakenbreak.distortions", "distort get_dimer_bond_length apply_dimer_distortion rattle distort_and_rattle local_mc_rattle"),
                cls("shakenbreak.input", "Distortions", "__init__ from_structures apply_distortions write_distortion_metadata write_vasp_files write_espresso_files write_cp2k_files write_castep_files write_fhi_aims_files"),
            ], ["seed_pypi_raw/shakenbreak/shakenbreak/input.py", "seed_pypi_raw/shakenbreak/shakenbreak/distortions.py"], "fixed_rng_structure_and_input_fixture_planned"),
            C("relaxation_result_analysis", "解析弛豫结果和结构比较", "比较给定结果的能量、结构、键和磁性，不执行电子结构求解器。", [
                *funcs("shakenbreak.io", "parse_energies read_vasp_structure read_structure_w_ase parse_structure parse_qe_input parse_fhi_aims_input"),
                *funcs("shakenbreak.analysis", "get_gs_distortion analyse_defect_site analyse_structure get_structures get_energies calculate_struct_comparison compare_structures get_homoionic_bonds get_site_magnetizations"),
            ], ["seed_pypi_raw/shakenbreak/shakenbreak/io.py", "seed_pypi_raw/shakenbreak/shakenbreak/analysis.py"], "fixed_relaxation_output_fixture_planned"),
            C("ground_state_outputs", "低能结构、复测输入与结果图", "形成候选生成到结果选择/输出闭环；输出目录需隔离，复测仅写输入。", [
                *funcs("shakenbreak.energy_lowering_distortions", "read_defects_directories get_energy_lowering_distortions compare_struct_to_distortions write_retest_inputs write_groundstate_structure"),
                *funcs("shakenbreak.plotting", "plot_all_defects plot_defect plot_datasets"),
            ], ["seed_pypi_raw/shakenbreak/shakenbreak/energy_lowering_distortions.py", "seed_pypi_raw/shakenbreak/shakenbreak/plotting.py"], "isolated_output_and_headless_plot_planned"),
        ], "全量仅 55 个调用；去除 11 个 Click CLI 入口、bold_print 和 plot_colorbar 后为 42。保留实际 Python 工作流，不以 CLI 别名或显示辅助凑数。")
    result["py-sc-fermi"] = (
        "输入、DOS、缺陷电荷态/种类、平衡与指定固定浓度下的自洽费米能级及结果字典。保留模型必要属性，排除 CLI、打印报告和溢出警告装饰器；输入单位和电荷中性条件需显式验证。",
        [
            C("density_of_states", "态密度及载流子", "DOS 输入、归一化及载流子积分与缺陷电荷配合构成电中性方程。", [
                cls("py_sc_fermi.dos", "DOS", "__init__ dos edos bandgap spin_polarised nelect from_vasprun from_dict as_dict sum_dos normalise_dos emin emax carrier_concentrations"),
            ], ["seed_pypi_raw/py-sc-fermi/py_sc_fermi/dos.py"], "dos_normalisation_and_carrier_fixture_planned"),
            C("defect_species", "缺陷与电荷态", "保留模型状态、形成能、转变能级和固定浓度约束，支持数值断言与序列化。", [
                cls("py_sc_fermi.defect_charge_state", "DefectChargeState", "__init__ energy charge degeneracy fixed_concentration from_dict as_dict fix_concentration get_formation_energy get_concentration"),
                cls("py_sc_fermi.defect_species", "DefectSpecies", "__init__ fix_concentration name nsites charge_states charges fixed_concentration from_dict as_dict min_energy_charge_state get_formation_energies tl_profile get_transition_level_and_energy get_concentration fixed_conc_charge_states variable_conc_charge_states charge_state_concentrations defect_charge_contributions"),
            ], ["seed_pypi_raw/py-sc-fermi/py_sc_fermi/defect_charge_state.py", "seed_pypi_raw/py-sc-fermi/py_sc_fermi/defect_species.py"], "analytic_formation_energy_and_concentration_planned"),
            C("self_consistent_solver", "电中性求解与结果", "使用相同能量参考和每晶胞/体积单位；q_tot 残差是后续数值验证入口。", [
                cls("py_sc_fermi.defect_system", "DefectSystem", "__init__ defect_species_names from_input_set from_yaml from_dict defect_species_by_name get_sc_fermi total_defect_charge_contributions q_tot get_transition_levels concentration_dict site_percentages as_dict"),
                cls("py_sc_fermi.inputs", "InputSet", "from_yaml from_sc_fermi_inputs"),
                *funcs("py_sc_fermi.inputs", "volume_from_unitcell read_input_fermi read_dos_data volume_from_structure read_volume_from_structure_file"),
            ], ["seed_pypi_raw/py-sc-fermi/py_sc_fermi/defect_system.py", "seed_pypi_raw/py-sc-fermi/py_sc_fermi/inputs.py"], "charge_neutrality_and_serialisation_planned"),
        ], None)
    return result


def main():
    manifest = Path("seed_gen/pypi_device_defect_sources.json")
    decisions = selections()
    for source in json.loads(manifest.read_text(encoding="utf-8")):
        name, tag = source["name"], source["tag"]
        raw_path = Path("seed_gen/pypi_outputs/ori_all") / f"{name}_{tag}.json"
        profile_path = Path("seed_gen/pypi_selection_profiles") / raw_path.name
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        index = {t["module"] + "." + t["name"]: t for t in raw[0]["init_ref_tools"]}
        boundary, capabilities, exception = decisions[name]
        for cap in capabilities:
            cap["symbols"] = []
            for qualified, methods in cap.pop("definitions"):
                tool = index[qualified]
                symbol = {key: tool[key] for key in ("module", "name", "type")}
                fields = [tool]
                if methods is not None:
                    symbol["methods"] = methods
                    available = {m["name"]: m for m in tool["function"]}
                    fields += [available[m] for m in methods]
                if any(not f.get("description", "").strip() for f in fields):
                    symbol["missing_description_reason"] = (
                        f"{cap['description']}所需的公开入口或状态查询；源码无独立说明的类/方法字段保留为空。"
                        "依据本能力 evidence_sources 和选择名单保留，不补造说明，不表示运行已验证。"
                    )
                cap["symbols"].append(symbol)
        profile = dict(profile_id=name.replace("-", "_") + "_balanced_v1", package=name, version=tag,
                       source_sha256=_canonical_sha256(raw), boundary=boundary,
                       target_all_func={"min": 50, "max": 100}, capabilities=capabilities)
        if exception:
            profile["desired_all_func"] = {"min": 50, "max": 100}
            profile["target_exception_reason"] = exception
            profile["target_all_func"]["min"] = 20 if name == "meshio" else 40
        _, report = select_seed(raw, profile, input_label=raw_path.as_posix(), profile_label=profile_path.as_posix())
        if profile_path.exists():
            if json.loads(profile_path.read_text(encoding="utf-8")) != profile:
                raise ValueError(f"Existing profile differs; review changes before explicit replacement: {profile_path}")
        else:
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(name, report["summary"])


if __name__ == "__main__":
    main()
