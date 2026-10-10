"""Build explicit profiles for FDTD, FEM, EME, and inverse-design packages."""

from __future__ import annotations

import json
from pathlib import Path

from seed_gen.scripts.select_python_ref_tools import _canonical_sha256


RAW = Path("seed_gen/pypi_outputs/ori_all")
OUT = Path("seed_gen/pypi_selection_profiles")


def cap(identifier, description, reason, symbols, verification, sources):
    return {"id": identifier, "description": description, "selection_reason": reason,
            "symbols": symbols, "evidence_sources": sources,
            "evidence": ["public_api", "core_capability", "composable_io"],
            "verification_tier": verification}


def cls(module, name, methods):
    return {"module": module, "name": name, "type": "class", "methods": methods}


def fn(module, name):
    return {"module": module, "name": name, "type": "function"}


def funcs(module, names):
    return [fn(module, name) for name in names]


RULES = {
"meep_v1.34.0": {"id": "meep_fdtd_v1", "target": (50, 100), "boundary": "高层几何、材料、源、Simulation、DFT 监视器和伴随优化；排除输出函数长尾与未静态枚举的 SWIG 原生 API。", "caps": [
 cap("geometry_sources", "材料、几何、边界和源", "核心对象覆盖仿真域输入、材料分布、边界层和连续/脉冲/模式源。", [
  cls("meep.geom", "Vector3", ["__init__", "scale", "dot", "cross", "norm", "unit", "close", "rotate"]),
  cls("meep.geom", "Medium", ["__init__", "transform", "rotate", "epsilon", "mu"]),
  cls("meep.geom", "MaterialGrid", ["check_weights", "__init__", "update_weights"]),
  cls("meep.geom", "GeometricObject", ["__init__", "shift", "info"]),
  cls("meep.geom", "Sphere", ["__init__", "radius"]), cls("meep.geom", "Cylinder", ["__init__", "radius", "height"]),
  cls("meep.geom", "Block", ["__init__"]), cls("meep.geom", "Prism", ["__init__"]),
  cls("meep.simulation", "PML", ["__init__", "R_asymptotic", "mean_stretch"]),
  cls("meep.simulation", "Volume", ["__init__", "get_vertices", "get_edges", "pt_in_volume"]),
  cls("meep.source", "Source", ["__init__", "add_source"]),
  cls("meep.source", "GaussianSource", ["__init__", "fourier_transform"]),
  cls("meep.source", "EigenModeSource", ["__init__", "eig_lattice_size", "eig_lattice_center", "component", "eig_band", "eig_resolution", "eig_tolerance", "eig_power", "add_source"]),
 ], "geometry_fixture_planned", ["seed_pypi_raw/meep/python/geom.py", "seed_pypi_raw/meep/python/source.py", "seed_pypi_raw/meep/python/simulation.py"]),
 cap("simulation_results", "FDTD 运行与频域结果", "Simulation 主链、DFT 通量对象和结果函数覆盖初始化、运行、场/通量/模式提取及保存。", [
  cls("meep.simulation", "Simulation", ["__init__", "set_materials", "dump_structure", "load_structure", "dump_fields", "load_fields", "init_sim", "meep_time", "timestep", "get_field_point", "get_epsilon_point", "set_epsilon", "add_sources", "add_dft_fields", "get_dft_data", "add_near2far", "add_energy", "get_farfield", "add_flux", "add_mode_monitor", "get_flux_data", "solve_cw", "solve_eigfreq", "get_array", "get_dft_array", "get_eigenmode_coefficients", "reset_meep", "run"]),
  cls("meep.simulation", "DftFlux", ["__init__", "flux", "normal_direction", "freq"]),
  *funcs("meep.simulation", ["after_sources", "at_beginning", "at_end", "at_every", "stop_when_fields_decayed", "stop_when_dft_decayed", "get_flux_freqs", "get_fluxes", "get_eigenmode_freqs", "get_energy_freqs"]),
 ], "small_fdtd_fixture_planned", ["seed_pypi_raw/meep/python/simulation.py"]),
 cap("adjoint", "伴随目标与设计更新", "OptimizationProblem 和最小滤波集合支持设计变量更新、正向/伴随运行及梯度计算。", [
  cls("meep.adjoint.optimization_problem", "OptimizationProblem", ["__init__", "prepare_forward_run", "forward_run", "prepare_adjoint_run", "adjoint_run", "calculate_gradient", "update_design", "get_objective_arguments"]),
  *funcs("meep.adjoint.filters", ["conic_filter", "gaussian_filter", "tanh_projection", "heaviside_projection"]),
 ], "adjoint_fixture_planned", ["seed_pypi_raw/meep/python/adjoint/optimization_problem.py", "seed_pypi_raw/meep/python/adjoint/filters.py"]),
]},
"tidy3d_v2.12.0": {"id": "tidy3d_cloud_fdtd_v1", "target": (50, 100), "boundary": "本地 FDTD 数据模型、几何/材料/源/监视器、结果对象和显式云任务接口；排除插件、MCP、材料库长尾和企业专用功能。", "caps": [
 cap("geometry_medium", "几何与材料", "Geometry 变换/GDS 互换、基本形状和色散材料形成结构输入链。", [
  cls("tidy3d.components.geometry.base", "Geometry", ["inside", "inside_meshgrid", "intersects", "contains", "bounds", "bounds_intersection", "bounds_union", "bounding_box", "plot", "volume", "surface_area", "translated", "scaled", "rotated", "reflected", "from_gds", "from_shapely", "to_gdstk", "to_gds_file"]),
  cls("tidy3d.components.geometry.base", "Box", ["from_bounds", "surfaces", "inside", "padded_copy", "bounds"]),
  cls("tidy3d.components.geometry.primitives", "Sphere", ["to_triangle_mesh", "inside", "intersections_plane", "bounds"]),
  cls("tidy3d.components.geometry.primitives", "Cylinder", ["to_polyslab", "center_axis", "length_axis", "inside", "bounds"]),
  cls("tidy3d.components.medium", "Medium", ["n_cfl", "eps_model", "from_nk"]),
  cls("tidy3d.components.medium", "PoleResidue", ["eps_model", "from_medium", "to_medium", "lo_to_eps_model", "from_lo_to", "imag_ep_extrema", "loss_upper_bound", "from_admittance_coeffs"]),
 ], "local_model_fixture_planned", ["seed_pypi_raw/tidy3d/tidy3d/components/geometry", "seed_pypi_raw/tidy3d/tidy3d/components/medium.py"]),
 cap("source_simulation", "源、监视器与仿真配置", "脉冲/平面波/点偶极源、三种监视器和 Simulation 验证/网格/导出接口覆盖提交前配置。", [
  cls("tidy3d.components.source.time", "SourceTime", ["plot_spectrum", "frequency_range", "frequency_range_sigma", "end_time"]),
  cls("tidy3d.components.source.time", "GaussianPulse", ["peak_time", "offset_time", "amp_time", "end_time", "amp_freq", "peak_frequency", "from_amp_complex", "from_frequency_range"]),
  cls("tidy3d.components.source.current", "PointDipole", ["sources_from_angles"]),
  cls("tidy3d.components.source.field", "PlaneWave", ["frequency_grid"]),
  cls("tidy3d.components.monitor", "FieldMonitor", ["storage_size", "supports_parallel_adjoint", "parallel_adjoint_bases"]),
  cls("tidy3d.components.monitor", "FluxMonitor", ["storage_size"]),
  cls("tidy3d.components.monitor", "ModeMonitor", ["storage_size", "supports_parallel_adjoint", "parallel_adjoint_bases"]),
  cls("tidy3d.components.simulation", "Simulation", ["validate_pre_upload", "monitors_data_size", "mediums", "medium_map", "background_structure", "intersecting_media", "intersecting_structures", "monitor_medium", "to_gdstk", "to_gds", "to_gds_file", "frequency_range", "dt", "tmesh", "num_time_steps", "all_structures", "num_cells", "num_computational_grid_points", "get_refractive_indices", "n_max", "wvl_mat_min", "complex_fields", "from_scene", "padded_copy", "uniformly_padded_copy"]),
 ], "preupload_validation_planned", ["seed_pypi_raw/tidy3d/tidy3d/components/source", "seed_pypi_raw/tidy3d/tidy3d/components/monitor.py", "seed_pypi_raw/tidy3d/tidy3d/components/simulation.py"]),
 cap("results_cloud", "结果查询与云任务边界", "SimulationData 提供归一化和衰减检查；webapi 明确列出上传、启动、监视、下载、运行与加载。", [
  cls("tidy3d.components.data.sim_data", "SimulationData", ["field_decay", "final_decay_value", "source_spectrum", "renormalize"]),
  *funcs("tidy3d.web.api.webapi", ["upload", "start", "monitor", "download", "run", "load"]),
 ], "cloud_calls_not_executed", ["seed_pypi_raw/tidy3d/tidy3d/components/data/sim_data.py", "seed_pypi_raw/tidy3d/tidy3d/web/api/webapi.py"]),
]},
"femwell_v0.1.12": {"id": "femwell_waveguide_multiphysics_v1", "target": (50, 100), "boundary": "截面网格、Maxwell 波导模式、热/静电/半导体辅助与本征求解器；排除测试、绘图细节和低层弱形式辅助。", "caps": [
 cap("waveguide_modes", "波导模式与场后处理", "Mode/Modes 和 compute_modes 覆盖本征模、有效折射率、功率、重叠、损耗和限制因子。", [
  cls("femwell.maxwell.waveguide", "Mode", ["omega", "k0", "wavelength", "n_eff", "poynting", "Sx", "Sy", "Sz", "te_fraction", "tm_fraction", "transversality", "calculate_overlap", "calculate_coupling_coefficient", "calculate_effective_area", "calculate_propagation_loss", "calculate_power", "calculate_confinement_factor", "calculate_pertubated_neff", "calculate_intensity", "plot", "plot_component", "show", "plot_intensity"]),
  cls("femwell.maxwell.waveguide", "Modes", ["sorted", "n_effs"]),
  *funcs("femwell.maxwell.waveguide", ["compute_modes", "calculate_hfield", "calculate_energy_current_density", "calculate_overlap", "calculate_scalar_product", "plot_mode", "eval_error_estimator"]),
 ], "mode_fixture_planned", ["seed_pypi_raw/femwell/femwell/maxwell/waveguide.py"]),
 cap("mesh_multiphysics", "截面网格与多物理场", "MeshTracker/mesh_from_polygons 构造标记网格，热、静电、磁静态和连续性求解器连接模式问题。", [
  cls("femwell.mesh", "MeshTracker", ["__init__", "get_point_index", "get_xy_segment_index_and_orientation", "get_gmsh_points_from_label", "get_gmsh_xy_lines_from_label", "get_gmsh_xy_surfaces_from_label", "xy_channel_loop_from_vertices", "add_get_point", "add_get_xy_segment", "add_get_xy_line", "add_xy_surface"]),
  *funcs("femwell.mesh", ["break_line", "mesh_from_polygons"]),
  fn("femwell.waveguide", "mesh_waveguide"), fn("femwell.thermal", "solve_thermal"), fn("femwell.thermal_transient", "solve_thermal_transient"),
  fn("femwell.laplace", "laplace_equation"), fn("femwell.magnetostatic", "solve_magnetostatic_2D"),
  *funcs("femwell.tcad", ["solve_coulomb", "solve_continuity_equations"]),
 ], "multiphysics_fixture_planned", ["seed_pypi_raw/femwell/femwell/mesh", "seed_pypi_raw/femwell/femwell/thermal.py", "seed_pypi_raw/femwell/femwell/tcad.py"]),
 cap("pn_solvers", "PN 近似和本征求解后端", "解析 PN 结函数和显式求解器入口支持光电/热电联合任务。", [
  *funcs("femwell.pn_analytical", ["dn_carriers", "dalpha_carriers", "alpha_to_k", "k_to_alpha", "built_in_voltage", "depletion_width", "depletion_width_n_side", "depletion_width_p_side", "hole_concentration_depletion_approx", "electron_concentration_depletion_approx", "index_pn_junction"]),
  *funcs("femwell.solver", ["solver_dense", "solver_eigen_scipy_operator", "solver_eigen_scipy_invert", "solver_eigen_slepc", "solver_cached"]),
 ], "analytic_pn_fixture_planned", ["seed_pypi_raw/femwell/femwell/pn_analytical.py", "seed_pypi_raw/femwell/femwell/solver.py"]),
]},
"emepy_v1.2.2": {"id": "emepy_eigenmode_expansion_v1", "target": (50, 100), "boundary": "截面几何、模式求解、分层 EME 网络、场传播和监视器；排除 ANN、Lumerical/Tidy3D 专用细节和优化器内部。", "caps": [
 cap("eme", "分层 EME 求解与传播", "EME 主对象覆盖添加层、求模、构网、传播、监视和 S 参数。", [cls("emepy.eme", "EME", ["__init__", "add_layer", "add_layers", "reset", "solve_modes", "propagate_layers", "build_network", "field_propagate", "get_sources", "s_parameters", "add_monitor", "draw", "propagate", "batch_scatter", "batch_gather"])], "eme_fixture_planned", ["seed_pypi_raw/emepy/emepy/eme.py"]),
 cap("modes_geometry", "本征模与截面几何", "有限差分求解器、EigenMode 和参数化波导几何提供层输入及场查询。", [
  cls("emepy.fd", "ModeSolver", ["__init__", "solve", "clear", "get_mode"]), cls("emepy.fd", "MSEMpy", ["__init__", "solve", "clear", "get_mode", "plot_material"]),
  cls("emepy.mode", "EigenMode", ["__init__", "plot", "get_confined_power", "zero_phase", "plot_material", "save", "get_fields", "get_H", "get_E", "get_neff", "get_Hx", "get_Hy", "get_Hz", "get_Ex", "get_Ey", "get_Ez", "get_wavelength", "change_fields", "inner_product", "check_spurious", "normalize"]),
  cls("emepy.geometries", "Params", ["__init__", "get_solver_rect", "get_solver_index"]), cls("emepy.geometries", "DynamicRect2D", ["__init__", "set_design", "get_n", "set_layers"]),
  cls("emepy.geometries", "Waveguide", ["__init__"]), cls("emepy.geometries", "BraggGrating", ["__init__"]), cls("emepy.geometries", "DirectionalCoupler", ["__init__"]),
 ], "mode_geometry_fixture_planned", ["seed_pypi_raw/emepy/emepy/fd.py", "seed_pypi_raw/emepy/emepy/mode.py", "seed_pypi_raw/emepy/emepy/geometries.py"]),
 cap("network_monitor", "界面散射、层模型与监视器", "层激活、单/多模界面、模型工具和 Monitor 完成传播网络及结果可视化。", [
  cls("emepy.models", "Layer", ["__init__", "begin_activate", "finish_activate", "activate_layer", "get_activated_layer", "clear"]),
  cls("emepy.models", "InterfaceSingleMode", ["__init__", "s_parameters", "solve", "get_values", "clear"]),
  cls("emepy.models", "InterfaceMultiMode", ["__init__", "s_parameters", "solve", "get_t", "get_r", "clear"]),
  cls("emepy.models", "ModelTools", ["purge_spurious", "get_sources", "get_source_system", "make_copy_model", "compute", "layers_task"]),
  cls("emepy.monitors", "Monitor", ["__init__", "reset_monitor", "get_z_list", "normalize", "get_array", "get_source_visual", "get_xy_monitor_visual", "visualize"]),
  cls("emepy.source", "Source", ["__init__", "get_label", "match_label"]),
 ], "network_monitor_fixture_planned", ["seed_pypi_raw/emepy/emepy/models.py", "seed_pypi_raw/emepy/emepy/monitors.py"]),
]},
"ceviche_v0.1.3": {"id": "ceviche_inverse_em_v1", "target": (50, 100), "boundary": "FDFD/FDTD、模式与源、自动微分 Jacobian、优化和数值辅助；排除绘图与随机测试工具长尾。", "caps": [
 cap("solvers", "频域/时域电磁求解", "FDFD/FDTD 类与模式、源、线性求解函数构成小型可微仿真主链。", [
  cls("ceviche.fdfd", "fdfd", ["__init__", "eps_r", "solve"]), cls("ceviche.fdfd", "fdfd_ez", ["__init__"]), cls("ceviche.fdfd", "fdfd_hz", ["__init__"]), cls("ceviche.fdfd", "fdfd_mf_ez", ["__init__", "solve"]), cls("ceviche.fdfd", "fdfd_3d", ["__init__"]),
  cls("ceviche.fdtd", "fdtd", ["__init__", "dL", "npml", "eps_r", "forward", "initialize_fields"]),
  *funcs("ceviche.modes", ["get_modes", "insert_mode", "solver_eigs", "filter_modes", "normalize_modes", "Ez_to_H"]), *funcs("ceviche.sources", ["b_TFSF", "compute_Q", "compute_f"]), fn("ceviche.solvers", "solve_linear"),
 ], "small_em_fixture_planned", ["seed_pypi_raw/ceviche/ceviche/fdfd.py", "seed_pypi_raw/ceviche/ceviche/fdtd.py", "seed_pypi_raw/ceviche/ceviche/modes.py"]),
 cap("autodiff", "Jacobian、优化和微分算子", "保留正向/反向 Jacobian、Adam 和稀疏算子原语。", [
  *funcs("ceviche.jacobians", ["jacobian", "jacobian_reverse", "jacobian_forward", "jacobian_numerical"]), *funcs("ceviche.optimizers", ["adam_optimize", "step_adam"]),
  *funcs("ceviche.primitives", ["sp_mult", "grad_sp_mult_entries_reverse", "grad_sp_mult_x_reverse", "sp_solve", "grad_sp_solve_entries_reverse", "grad_sp_solve_b_reverse", "spsp_mult", "grad_spsp_mult_entries_a_reverse", "sp_solve_nl", "grad_sp_solve_nl_parameters"]),
  *funcs("ceviche.derivatives", ["curl_E", "curl_H", "compute_derivative_matrices", "createDws", "make_Dxf", "make_Dxb", "make_Dyf", "make_Dyb", "create_S_matrices", "create_sfactor"]),
 ], "gradient_fixture_planned", ["seed_pypi_raw/ceviche/ceviche/jacobians.py", "seed_pypi_raw/ceviche/ceviche/primitives.py", "seed_pypi_raw/ceviche/ceviche/derivatives.py"]),
 cap("postprocess", "网格与频谱后处理", "必要数组整形、网格坐标和频谱函数支持结果验证。", [*funcs("ceviche.utils", ["make_sparse", "make_IO_matrices", "der_num", "grad_num", "jac_num", "grid_center_to_xyz", "grid_xyz_to_center", "vec_zz_to_xy", "float_2_array", "reshape_to_ND", "get_value", "get_shape", "measure_fields", "get_spectrum", "get_max_power_freq"])], "postprocess_fixture_planned", ["seed_pypi_raw/ceviche/ceviche/utils.py"]),
]},
"legume-gme_v1.0.3": {"id": "legume_gme_photonic_crystal_v1", "target": (50, 100), "boundary": "光子晶体几何、GME/PWE 求解、场与辐射损耗、优化和代表性可视化；排除底层矩阵元及激子专用长尾。", "caps": [
 cap("photonic_crystal", "晶格、层、形状和光子晶体", "Lattice/Layer/Shape/PhotCryst 构成参数化结构输入。", [
  cls("legume.phc.lattice", "Lattice", ["__init__", "xy_grid", "bz_path"]), cls("legume.phc.layer", "Layer", ["__init__", "eps_eff", "compute_ft", "compute_exc_ft", "get_eps"]), cls("legume.phc.layer", "ShapesLayer", ["__init__", "add_shape", "compute_ft", "compute_exc_ft", "get_eps"]),
  cls("legume.phc.phc", "PhotCryst", ["__init__", "z_grid", "add_layer", "add_qw", "add_shape", "get_eps", "get_eps_bounds"]),
  cls("legume.phc.shapes", "Shape", ["__init__", "compute_ft", "is_inside"]), cls("legume.phc.shapes", "Circle", ["__init__", "compute_ft", "is_inside"]), cls("legume.phc.shapes", "Ellipse", ["__init__", "compute_ft", "is_inside"]), cls("legume.phc.shapes", "Ring", ["__init__", "compute_ft", "is_inside"]), cls("legume.phc.shapes", "Poly", ["__init__", "compute_ft", "is_inside", "rotate"]),
 ], "geometry_fixture_planned", ["seed_pypi_raw/legume-gme/legume/phc"]),
 cap("gme_pwe", "导模展开与平面波展开", "GME/PWE 覆盖频率、本征矢、辐射损耗和场切片结果。", [
  cls("legume.gme.gme", "GuidedModeExp", ["__init__", "freqs", "freqs_im", "eigvecs", "kpoints", "gvec", "compute_eps_inv", "set_run_options", "run", "run_im", "compute_rad", "compute_rad_sp", "get_eps_xy", "ft_field_xy", "get_field_xy", "get_field_xz", "get_field_yz"]),
  cls("legume.pwe.pwe", "PlaneWaveExp", ["__init__", "freqs", "eigvecs", "kpoints", "gvec", "run", "get_eps_xy", "ft_field_xy", "get_field_xy"]),
 ], "solver_fixture_planned", ["seed_pypi_raw/legume-gme/legume/gme/gme.py", "seed_pypi_raw/legume-gme/legume/pwe/pwe.py"]),
 cap("optimization_visualization", "优化和结果可视化", "Minimize 提供 Adam/L-BFGS，代表绘图函数检查能带、介电常数和场。", [
  cls("legume.minimize", "Minimize", ["__init__", "adam", "lbfgs"]), *funcs("legume.viz", ["bands", "eps", "eps_xz", "eps_xy", "structure", "reciprocal", "field", "wavef"]),
 ], "optimization_fixture_planned", ["seed_pypi_raw/legume-gme/legume/minimize.py", "seed_pypi_raw/legume-gme/legume/viz.py"]),
]},
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, rule in RULES.items():
        payload = json.loads((RAW / f"{stem}.json").read_text(encoding="utf-8"))
        seed = payload[0]
        available = {(item["module"], item["name"], item["type"]): item for item in seed["init_ref_tools"]}
        symbols = [symbol for group in rule["caps"] for symbol in group["symbols"]]
        for symbol in symbols:
            key = (symbol["module"], symbol["name"], symbol["type"])
            if key not in available:
                raise ValueError(f"{stem}: missing symbol {key}")
            source = available[key]
            if symbol["type"] == "class":
                methods = {method["name"]: method for method in source.get("function", [])}
                missing = [name for name in symbol.get("methods", []) if name not in methods]
                if missing:
                    raise ValueError(f"{stem}: missing methods {key}: {missing}")
            lacks_docs = not str(source.get("description") or "").strip()
            if symbol["type"] == "class":
                chosen = set(symbol.get("methods", []))
                lacks_docs = lacks_docs or any(method["name"] in chosen and not str(method.get("description") or "").strip() for method in source.get("function", []))
            if lacks_docs:
                symbol["missing_description_reason"] = "该接口属于发布版公开能力链；源码缺少独立说明，保留原空字段，不根据名称补造，也不表示运行已验证。"
        profile = {"profile_id": rule["id"], "package": seed["environment"]["basic_info"]["name"], "version": seed["environment"]["basic_info"]["version"], "source_sha256": _canonical_sha256(payload), "boundary": rule["boundary"], "target_all_func": {"min": rule["target"][0], "max": rule["target"][1]}, "capabilities": rule["caps"]}
        destination = OUT / f"{stem}.json"
        destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    main()
