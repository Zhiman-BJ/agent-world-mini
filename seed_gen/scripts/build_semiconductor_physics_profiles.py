"""Build reviewed profiles for semiconductor transport, RF, plasma, and PDE packages."""

from __future__ import annotations

import json
from pathlib import Path

from seed_gen.scripts.select_python_ref_tools import _canonical_sha256


RAW = Path("seed_gen/pypi_outputs/ori_all")
OUT = Path("seed_gen/pypi_selection_profiles")


def capability(identifier, description, reason, symbols, verification, sources):
    return {
        "id": identifier,
        "description": description,
        "selection_reason": reason,
        "symbols": symbols,
        "evidence_sources": sources,
        "evidence": ["public_api", "core_capability", "composable_io"],
        "verification_tier": verification,
    }


def cls(module, name, methods):
    return {"module": module, "name": name, "type": "class", "methods": methods}


def fn(module, name):
    return {"module": module, "name": name, "type": "function"}


def funcs(module, names):
    return [fn(module, name) for name in names]


RULES = {
    "mobilitypy_v1.0.2": {
        "profile_id": "mobilitypy_carrier_transport_v1",
        "boundary": "二维和三维半导体载流子迁移率、散射率、片电阻与结果绘图；该小包公开 API 少于通用数量目标，保留全部公开计算入口。",
        "target": (15, 25),
        "exception": {
            "desired_all_func": {"min": 50, "max": 100},
            "target_exception_reason": "mobilitypy v1.0.2 的发布源码仅定义 17 个公开调用；全部保留仍低于 50，不使用私有实现或重复接口凑数。",
        },
        "capabilities": [
            capability(
                "mobility_models",
                "材料参数、二维和三维迁移率模型",
                "保留材料参数读取、各散射机制组合、总迁移率、片电阻和品质因数主链。",
                [
                    cls("mobilitypy.mobility", "AlloyParams", ["__init__", "get_alloy_params"]),
                    cls("mobilitypy.mobility", "Mobility2DCarrier", ["__init__", "calculate_sheet_mobility", "sc_rate_2_mobility", "calculate_sheet_resitance", "calculate_figure_of_merit"]),
                    cls("mobilitypy.mobility", "Mobility3DCarrier", ["__init__", "calculate_3D_mobility", "Dislocation_3D_Tc_Tq_ratio"]),
                ],
                "parameter_fixture_planned",
                ["seed_pypi_raw/mobilitypy/mobilitypy/mobility.py"],
            ),
            capability(
                "result_plotting",
                "迁移率曲线与准三维结果绘图",
                "保留包提供的公开绘图类，支持对计算结果做可检查的图形输出。",
                [
                    cls("mobilitypy.mobility", "Plottings", ["__init__", "plot_2d", "plot_2d_carrier_mobilities"]),
                    cls("mobilitypy.utilities._quasi3d_plot_fns", "PlotQuasi3DFuns", ["__init__", "InterPolation", "CreateColorbarMapableObject", "Plotq3D"]),
                ],
                "plot_fixture_planned",
                ["seed_pypi_raw/mobilitypy/mobilitypy/utilities/_quasi3d_plot_fns.py"],
            ),
        ],
    },
    "scikit-rf_v2.1.0": {
        "profile_id": "scikit_rf_network_calibration_v1",
        "boundary": "频率轴、N 端口网络、参数转换、网络组合、校准、去嵌入和 Touchstone I/O；排除绘图长尾、仪器厂商驱动和大量专用校准变体。",
        "target": (50, 100),
        "capabilities": [
            capability(
                "network_core",
                "频率轴与网络数据模型",
                "Frequency 和 Network 覆盖测量数据构造、S/Z/Y/T 参数、质量指标、插值及时域变换。",
                [
                    cls("skrf.frequency", "Frequency", ["__init__", "from_f", "start", "stop", "npoints", "center", "step", "span", "f", "f_scaled", "w", "overlap"]),
                    cls("skrf.network", "Network", ["__init__", "from_z", "s", "y", "z", "t", "z0", "frequency", "nports", "passivity", "reciprocity", "stability", "max_stable_gain", "max_gain", "group_delay", "is_reciprocal", "is_passive", "copy", "read_touchstone", "write_touchstone", "interpolate", "extrapolate_to_dc", "crop", "renormalize", "se2gmm", "gmm2se", "impulse_response", "step_response", "s_active", "z_active"]),
                    *funcs("skrf.network", ["connect", "cascade", "cascade_list", "de_embed", "stitch", "overlap", "concat_ports", "average", "s2z", "s2y", "s2t", "z2s", "y2s", "t2s", "renormalize_s"]),
                ],
                "touchstone_fixture_planned",
                ["seed_pypi_raw/scikit-rf/skrf/frequency.py", "seed_pypi_raw/scikit-rf/skrf/network.py"],
            ),
            capability(
                "network_collections_circuits",
                "网络集合统计与电路组合",
                "NetworkSet 处理批量网络，Circuit 将端口和子网络组合为可查询电路。",
                [
                    cls("skrf.networkSet", "NetworkSet", ["__init__", "from_dir", "to_dict", "copy", "filter", "mean_s_db", "std_s_db", "to_dataframe"]),
                    cls("skrf.circuit", "Circuit", ["__init__", "connections", "update_networks", "Port", "SeriesImpedance", "ShuntAdmittance", "Ground", "Open", "network", "s_external"]),
                ],
                "network_collection_fixture_planned",
                ["seed_pypi_raw/scikit-rf/skrf/networkSet.py", "seed_pypi_raw/scikit-rf/skrf/circuit.py"],
            ),
            capability(
                "calibration_deembedding_io",
                "校准、去嵌入和持久化",
                "保留通用校准基类、常用一端口/SOLT/TRL 入口、两种去嵌入模型及基本文件读写。",
                [
                    cls("skrf.calibration.calibration", "Calibration", ["__init__", "run", "apply_cal", "apply_cal_to_list", "apply_cal_to_network_set", "embed", "coefs", "residual_ntwks", "caled_ntwks", "write"]),
                    cls("skrf.calibration.calibration", "OnePort", ["__init__", "run", "apply_cal", "embed"]),
                    cls("skrf.calibration.calibration", "SOLT", ["__init__"]),
                    cls("skrf.calibration.calibration", "TRL", ["__init__", "run"]),
                    cls("skrf.calibration.deembedding", "OpenShort", ["__init__", "deembed"]),
                    cls("skrf.calibration.deembedding", "SplitPi", ["__init__", "deembed"]),
                    *funcs("skrf.io.general", ["read", "write"]),
                ],
                "calibration_fixture_planned",
                ["seed_pypi_raw/scikit-rf/skrf/calibration", "seed_pypi_raw/scikit-rf/skrf/io/general.py"],
            ),
        ],
    },
    "plasmapy_v2026.2.0": {
        "profile_id": "plasmapy_process_plasma_v1",
        "boundary": "粒子与离化态、碰撞尺度、等离子体频率/长度/速度、介电响应和色散关系；排除开发辅助、联网数据下载、绘图和高成本模拟。",
        "target": (50, 100),
        "capabilities": [
            capability(
                "particles_ionization",
                "粒子、离子与离化态",
                "粒子对象和离化态为所有带单位公式提供一致物种输入与电荷信息。",
                [
                    cls("plasmapy.particles.particle_class", "Particle", ["__init__", "symbol", "element", "isotope", "ionic_symbol", "charge_number", "charge", "mass", "atomic_number", "mass_number", "is_ion", "ionize", "recombine", "ionization_energy", "electron_binding_energy"]),
                    cls("plasmapy.particles.ionization_state", "IonizationState", ["__init__", "ionic_fractions", "normalize", "n_e", "number_densities", "T_e", "T_i", "element", "charge_numbers", "Z_mean", "average_ion", "summarize"]),
                    cls("plasmapy.particles.particle_collections", "ParticleList", ["__init__", "append", "extend", "charge", "charge_number", "mass", "symbols", "average_particle"]),
                    *funcs("plasmapy.particles.atomic", ["atomic_number", "mass_number", "particle_mass", "charge_number", "electric_charge", "reduced_mass", "ionic_levels", "stopping_power"]),
                ],
                "particle_fixture_planned",
                ["seed_pypi_raw/plasmapy/src/plasmapy/particles"],
            ),
            capability(
                "plasma_scales",
                "等离子体特征频率、长度、速度和无量纲数",
                "保留工艺等离子体状态估算最常用的局部公式，可串联压力、温度、密度和磁场输入。",
                [
                    *funcs("plasmapy.formulary.frequencies", ["gyrofrequency", "plasma_frequency", "lower_hybrid_frequency", "upper_hybrid_frequency"]),
                    *funcs("plasmapy.formulary.lengths", ["Debye_length", "gyroradius", "inertial_length"]),
                    *funcs("plasmapy.formulary.dimensionless", ["Debye_number", "Hall_parameter", "beta"]),
                    *funcs("plasmapy.formulary.speeds", ["thermal_speed", "Alfven_speed", "ion_sound_speed"]),
                    *funcs("plasmapy.formulary.ionization", ["Saha", "ionization_balance"]),
                    *funcs("plasmapy.formulary.dielectric", ["cold_plasma_permittivity_SDP", "cold_plasma_permittivity_LRP"]),
                    *funcs("plasmapy.formulary.drifts", ["diamagnetic_drift", "ExB_drift", "force_drift"]),
                    *funcs("plasmapy.formulary.densities", ["critical_density", "mass_density"]),
                ],
                "quantity_formula_fixture_planned",
                ["seed_pypi_raw/plasmapy/src/plasmapy/formulary"],
            ),
            capability(
                "collisions_dispersion",
                "碰撞、平均自由程与色散关系",
                "碰撞类和公式给出散射率与输运区间，色散函数提供波动传播检查。",
                [
                    cls("plasmapy.formulary.collisions.frequencies", "SingleParticleCollisionFrequencies", ["__init__", "momentum_loss", "transverse_diffusion", "parallel_diffusion", "energy_loss", "Lorentz_collision_frequency", "x", "phi"]),
                    cls("plasmapy.formulary.collisions.frequencies", "MaxwellianCollisionFrequencies", ["__init__", "Lorentz_collision_frequency", "Maxwellian_avg_ei_collision_freq", "Maxwellian_avg_ii_collision_freq"]),
                    *funcs("plasmapy.formulary.collisions.coulomb", ["Coulomb_logarithm", "Coulomb_cross_section"]),
                    *funcs("plasmapy.formulary.collisions.dimensionless", ["coupling_parameter", "Knudsen_number"]),
                    *funcs("plasmapy.formulary.collisions.frequencies", ["collision_frequency", "fundamental_electron_collision_freq", "fundamental_ion_collision_freq"]),
                    *funcs("plasmapy.formulary.collisions.lengths", ["impact_parameter_perp", "impact_parameter", "mean_free_path"]),
                    *funcs("plasmapy.dispersion.dispersion_functions", ["plasma_dispersion_func", "plasma_dispersion_func_deriv"]),
                    fn("plasmapy.dispersion.analytical.stix_", "stix"),
                    fn("plasmapy.dispersion.analytical.two_fluid_", "two_fluid"),
                ],
                "collision_regime_fixture_planned",
                ["seed_pypi_raw/plasmapy/src/plasmapy/formulary/collisions", "seed_pypi_raw/plasmapy/src/plasmapy/dispersion"],
            ),
        ],
    },
    "cantera_v3.2.0": {
        "profile_id": "cantera_process_chemistry_v1",
        "boundary": "气相/表面相状态、反应速率、输运性质和理想反应器网络；排除格式转换 CLI、火焰求解长尾和开发扩展接口。",
        "target": (50, 100),
        "capabilities": [
            capability(
                "thermo_kinetics_transport",
                "热力学状态、反应动力学和输运",
                "Solution 组合相对象，Species、ThermoPhase、Kinetics 和 Transport 提供从机理输入到速率及输运结果的主链。",
                [
                    cls("cantera.composite", "Solution", []),
                    cls("cantera.composite", "Interface", []),
                    cls("cantera.thermo", "Species", ["__init__", "from_dict", "from_yaml", "list_from_file", "list_from_yaml", "name", "composition", "charge", "molecular_weight", "input_data"]),
                    cls("cantera.thermo", "ThermoPhase", ["report", "basis", "equilibrate", "n_elements", "element_names", "n_species", "species_names", "species", "molecular_weights", "charges", "mean_molecular_weight", "Y", "X", "concentrations", "set_equivalence_ratio", "set_mixture_fraction", "equivalence_ratio", "mixture_fraction", "P", "T", "density", "enthalpy_mass", "entropy_mass", "gibbs_mass", "sound_speed"]),
                    cls("cantera.kinetics", "Kinetics", ["kinetics_model", "n_reactions", "reaction", "reactions", "modify_reaction", "add_reaction", "set_multiplier", "reaction_equations", "forward_rates_of_progress", "reverse_rates_of_progress", "net_rates_of_progress", "equilibrium_constants", "net_production_rates", "heat_release_rate", "heat_production_rates"]),
                    cls("cantera.transport", "Transport", ["transport_model", "viscosity", "species_viscosities", "electrical_conductivity", "thermal_conductivity", "mix_diff_coeffs", "binary_diff_coeffs", "mobilities"]),
                ],
                "mechanism_fixture_planned",
                ["seed_pypi_raw/cantera/interfaces/cython/cantera/thermo.pyx", "seed_pypi_raw/cantera/interfaces/cython/cantera/kinetics.pyx", "seed_pypi_raw/cantera/interfaces/cython/cantera/transport.pyx"],
            ),
            capability(
                "reaction_models",
                "反应、速率模型和等离子体电子碰撞",
                "保留反应对象的 YAML 构造、可修改属性和代表性速率模型，包括电子碰撞等离子体速率。",
                [
                    cls("cantera.reaction", "Reaction", ["__init__", "from_dict", "from_yaml", "list_from_file", "list_from_yaml", "equation", "reactants", "products", "orders", "rate", "reversible", "input_data"]),
                    cls("cantera.reaction", "Arrhenius", ["__init__", "pre_exponential_factor", "temperature_exponent", "activation_energy"]),
                    cls("cantera.reaction", "ElectronCollisionPlasmaRate", ["__init__", "energy_levels", "cross_sections"]),
                ],
                "reaction_fixture_planned",
                ["seed_pypi_raw/cantera/interfaces/cython/cantera/reaction.pyx"],
            ),
            capability(
                "reactor_network",
                "理想反应器网络与边界流量",
                "Reactor、FlowDevice 和 ReactorNet 支持状态初始化、推进、稳态求解及结果查询。",
                [
                    cls("cantera.reactor", "Reactor", ["__init__", "kinetics", "chemistry_enabled", "energy_enabled", "component_index", "get_state"]),
                    cls("cantera.reactor", "FlowDevice", ["__init__", "upstream", "downstream", "mass_flow_rate"]),
                    cls("cantera.reactor", "ReactorNet", ["__init__", "add_reactor", "advance", "step", "solve_steady", "initialize", "get_state", "solver_stats"]),
                ],
                "reactor_fixture_planned",
                ["seed_pypi_raw/cantera/interfaces/cython/cantera/reactor.pyx"],
            ),
        ],
    },
    "fipy_4.0.3": {
        "profile_id": "fipy_diffusion_reaction_pde_v1",
        "boundary": "结构化/Gmsh 网格、场变量、扩散/瞬态/源项、边界约束、方程求解和时间步进；排除绘图器、测试辅助及特定并行求解器。",
        "target": (50, 100),
        "capabilities": [
            capability(
                "mesh_variables",
                "网格与场变量",
                "网格工厂和 Variable/CellVariable/FaceVariable 提供 PDE 状态构造、约束和派生量。",
                [
                    *funcs("fipy.meshes.factoryMeshes", ["Grid1D", "Grid2D", "Grid3D", "CylindricalGrid1D", "CylindricalGrid2D", "SphericalGrid1D"]),
                    cls("fipy.variables.variable", "Variable", ["__init__", "copy", "inBaseUnits", "inUnitsOf", "tostring", "put", "constraints", "constrain", "release", "setValue", "numericValue", "dtype", "itemsize", "any", "all", "dot", "ravel", "sum", "max", "min", "std", "take", "allclose", "allequal", "mag"]),
                    cls("fipy.variables.cellVariable", "CellVariable", ["__init__", "copy", "globalValue", "setValue", "cellVolumeAverage", "grad", "gaussGrad", "leastSquaresGrad", "arithmeticFaceValue", "minmodFaceValue", "harmonicFaceValue", "faceGrad", "old", "updateOld", "constrain", "release"]),
                    cls("fipy.variables.faceVariable", "FaceVariable", ["copy", "globalValue", "setValue", "divergence"]),
                ],
                "structured_mesh_fixture_planned",
                ["seed_pypi_raw/fipy/fipy/meshes/factoryMeshes.py", "seed_pypi_raw/fipy/fipy/variables"],
            ),
            capability(
                "equation_solve",
                "方程项、边界条件和时间步进",
                "代表性项类与 Term 求解协议覆盖扩散-反应方程的组装、sweep 和残差检查。",
                [
                    cls("fipy.terms.diffusionTerm", "DiffusionTerm", []),
                    cls("fipy.terms.transientTerm", "TransientTerm", []),
                    cls("fipy.terms.implicitSourceTerm", "ImplicitSourceTerm", ["__init__"]),
                    cls("fipy.terms.term", "Term", ["__init__", "copy", "solve", "sweep", "justResidualVector", "residualVectorAndNorm", "justErrorVector", "cacheMatrix", "matrix", "cacheRHSvector", "RHSvector", "getDefaultSolver"]),
                    cls("fipy.boundaryConditions.fixedValue", "FixedValue", []),
                    cls("fipy.boundaryConditions.fixedFlux", "FixedFlux", ["__init__"]),
                    cls("fipy.steppers.stepper", "Stepper", ["__init__", "sweepFn", "successFn", "failFn", "step"]),
                ],
                "diffusion_reaction_fixture_planned",
                ["seed_pypi_raw/fipy/fipy/terms", "seed_pypi_raw/fipy/fipy/boundaryConditions", "seed_pypi_raw/fipy/fipy/steppers"],
            ),
            capability(
                "gmsh_interchange",
                "Gmsh 网格输入输出",
                "保留 MSH 读写以及二维、三维 Gmsh 网格构造入口，与既有 gmsh/meshio 环境组合。",
                [
                    cls("fipy.meshes.gmshMesh", "MSHFile", ["__init__", "read", "write", "makeMapVariables"]),
                    cls("fipy.meshes.gmshMesh", "Gmsh2D", ["__init__"]),
                    cls("fipy.meshes.gmshMesh", "Gmsh3D", ["__init__"]),
                ],
                "gmsh_roundtrip_planned",
                ["seed_pypi_raw/fipy/fipy/meshes/gmshMesh.py"],
            ),
        ],
    },
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, rule in RULES.items():
        raw_path = RAW / f"{stem}.json"
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        seed = payload[0]
        available = {(item["module"], item["name"], item["type"]): item for item in seed["init_ref_tools"]}
        selected = []
        for group in rule["capabilities"]:
            for symbol in group["symbols"]:
                key = (symbol["module"], symbol["name"], symbol["type"])
                if key not in available:
                    raise ValueError(f"{stem}: missing symbol {key}")
                source = available[key]
                if symbol["type"] == "class":
                    methods = {item["name"]: item for item in source.get("function", [])}
                    missing = [name for name in symbol.get("methods", []) if name not in methods]
                    if missing:
                        raise ValueError(f"{stem}: missing methods {key}: {missing}")
                selected.append(symbol)

        for symbol in selected:
            source = available[(symbol["module"], symbol["name"], symbol["type"])]
            lacks_docs = not str(source.get("description") or "").strip()
            if symbol["type"] == "class":
                chosen = set(symbol.get("methods", []))
                lacks_docs = lacks_docs or any(
                    method["name"] in chosen and not str(method.get("description") or "").strip()
                    for method in source.get("function", [])
                )
            if lacks_docs:
                symbol["missing_description_reason"] = (
                    "该接口是所选能力闭环所需的发布版公开入口；对应源码定义缺少独立说明，"
                    "保留原空字段，不根据名称补造，也不表示运行已验证。"
                )

        profile = {
            "profile_id": rule["profile_id"],
            "package": seed["environment"]["basic_info"]["name"],
            "version": seed["environment"]["basic_info"]["version"],
            "source_sha256": _canonical_sha256(payload),
            "boundary": rule["boundary"],
            "target_all_func": {"min": rule["target"][0], "max": rule["target"][1]},
            "capabilities": rule["capabilities"],
        }
        profile.update(rule.get("exception", {}))
        destination = OUT / f"{stem}.json"
        destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    main()
