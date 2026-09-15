"""Build explicit selection profiles for SPICE, circuit, and PIC packages."""

from __future__ import annotations

import json
from pathlib import Path

from seed_gen.scripts.select_python_ref_tools import _canonical_sha256


RAW = Path("seed_gen/pypi_outputs/ori_all")
OUT = Path("seed_gen/pypi_selection_profiles")


def cap(identifier, description, reason, symbols, verification, sources):
    return {
        "id": identifier, "description": description, "selection_reason": reason,
        "symbols": symbols, "evidence_sources": sources,
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
    "spicelib_1.6.3": {
        "id": "spicelib_automation_v1", "target": (50, 100),
        "boundary": "SPICE 网表编辑、参数步进、外部仿真任务、波形/日志读取和统计分析；排除 GUI、客户端服务器和特定厂商文件细节。",
        "caps": [
            cap("netlist_edit", "网表与元件参数编辑", "保留元件值/参数修改和编辑器控制段、库路径及网表运行入口。", [
                cls("spicelib.editor.spice_components", "SpiceComponent", ["reset_attributes", "rewrite_lines", "set_value", "set_parameters", "set_parameter", "write_lines"]),
                cls("spicelib.editor.spice_editor", "SpiceEditor", ["__init__", "reset_netlist", "get_control_sections", "add_control_section", "remove_control_section", "run", "add_library_search_paths"]),
            ], "netlist_fixture_planned", ["seed_pypi_raw/spicelib/spicelib/editor"]),
            cap("runner_sweeps", "仿真调度与参数扫描", "SimRunner 管理外部进程，SimStepper 组合参数、元件值和模型扫描。", [
                cls("spicelib.sim.sim_runner", "SimRunner", ["__init__", "set_simulator", "clear_command_line_switches", "add_command_line_switch", "run", "run_now", "active_threads", "update_completed", "wait_completion", "cleanup_files", "file_cleanup", "tasks", "create_raw_file_with", "export_sim_log"]),
                cls("spicelib.sim.sim_stepping", "SimStepper", ["__init__", "add_instruction", "add_instructions", "set_parameters", "set_parameter", "set_component_values", "set_component_value", "set_element_model", "add_param_sweep", "add_value_sweep", "add_model_sweep", "total_number_of_simulations", "run_all"]),
            ], "mock_runner_planned", ["seed_pypi_raw/spicelib/spicelib/sim"]),
            cap("results_analysis", "波形、日志与统计结果", "RawRead 和 LogfileData 提供固定样例读取、查询与导出，分析类覆盖 Monte Carlo、灵敏度和最坏情况流程。", [
                cls("spicelib.raw.raw_read", "RawRead", ["__init__", "plots", "nVariables", "nPoints", "flags", "steps", "get_raw_property", "get_plot_names", "get_trace_names", "get_trace", "get_wave", "get_time_axis", "get_axis", "get_len", "get_steps", "export", "to_dataframe", "to_csv"]),
                cls("spicelib.log.logfile_data", "LogfileData", ["__init__", "has_steps", "get_step_vars", "get_measure_names", "get_measure_value", "get_measure_values_at_steps", "max_measure_value", "min_measure_value", "avg_measure_value", "export_data"]),
                cls("spicelib.sim.tookit.montecarlo", "Montecarlo", ["prepare_testbench", "run_analysis", "analyse_measurement"]),
                cls("spicelib.sim.tookit.quick_sensitivity_analysis", "QuickSensitivityAnalysis", ["__init__", "prepare_testbench", "get_sensitivity_data", "run_analysis"]),
                cls("spicelib.sim.tookit.worst_case", "WorstCaseAnalysis", ["prepare_testbench", "run_analysis", "get_min_max_measure_value", "make_sensitivity_analysis"]),
            ], "raw_log_fixture_planned", ["seed_pypi_raw/spicelib/spicelib/raw", "seed_pypi_raw/spicelib/spicelib/log", "seed_pypi_raw/spicelib/spicelib/sim/tookit"]),
        ],
    },
    "PySpice_v1.5": {
        "id": "pyspice_circuit_simulation_v1", "target": (50, 100),
        "boundary": "电路/网表构造、分析配置、ngspice/Xyce 适配和波形结果；排除大量元件快捷类、单位运算细节和安装脚本。",
        "caps": [
            cap("netlist", "电路、网表、模型与子电路", "Circuit/Netlist 提供对象化网表构造，DeviceModel 和 SubCircuit 覆盖可复用模型。", [
                cls("PySpice.Spice.Netlist", "Circuit", ["__init__", "clone", "include", "lib", "parameter", "str", "str_end", "simulator"]),
                cls("PySpice.Spice.Netlist", "Netlist", ["__init__", "copy_to", "gnd", "nodes", "node_names", "elements", "element_names", "models", "model_names", "subcircuits", "subcircuit_names", "element", "model", "node", "get_node", "subcircuit"]),
                cls("PySpice.Spice.Netlist", "DeviceModel", ["__init__", "clone", "name", "model_type", "parameters"]),
                cls("PySpice.Spice.Netlist", "SubCircuit", ["__init__", "clone", "name", "external_nodes", "parameters", "check_nodes"]),
            ], "netlist_render_fixture_planned", ["seed_pypi_raw/pyspice/PySpice/Spice/Netlist.py"]),
            cap("analysis", "仿真分析配置与后端", "CircuitSimulation 生成 OP/DC/AC/TRAN 等分析，CircuitSimulator 及两种后端类标出外部求解边界。", [
                cls("PySpice.Spice.Simulation", "CircuitSimulation", ["__init__", "circuit", "options", "temperature", "nominal_temperature", "initial_condition", "node_set", "save", "save_currents", "reset_analysis", "analysis_iter", "operating_point", "dc", "ac", "transient", "noise", "transfer_function"]),
                cls("PySpice.Spice.Simulation", "CircuitSimulator", ["factory", "operating_point", "dc", "dc_sensitivity", "ac", "transient", "polezero", "noise", "distortion", "transfer_function"]),
                cls("PySpice.Spice.NgSpice.Simulation", "NgSpiceSharedCircuitSimulator", ["__init__", "ngspice"]),
                cls("PySpice.Spice.Xyce.Simulation", "XyceCircuitSimulator", ["__init__", "str_options"]),
            ], "external_solver_mock_planned", ["seed_pypi_raw/pyspice/PySpice/Spice/Simulation.py", "seed_pypi_raw/pyspice/PySpice/Spice/NgSpice", "seed_pypi_raw/pyspice/PySpice/Spice/Xyce"]),
            cap("parse_results", "网表解析与波形结果", "Parser 支持已有网表转对象，Analysis/WaveForm 提供节点、支路、频率和时间结果。", [
                cls("PySpice.Spice.Parser", "SpiceParser", ["__init__", "is_only_subcircuit", "is_only_model", "build_circuit", "netlist_to_python", "to_python_code"]),
                cls("PySpice.Spice.Library", "SpiceLibrary", ["__init__", "subcircuits", "models", "search"]),
                cls("PySpice.Probe.WaveForm", "WaveForm", ["from_unit_values", "from_array", "name", "abscissa", "title", "str_data"]),
                cls("PySpice.Probe.WaveForm", "Analysis", ["__init__", "simulation", "nodes", "branches", "elements", "internal_parameters"]),
                cls("PySpice.Probe.WaveForm", "DcAnalysis", ["__init__", "sweep"]),
                cls("PySpice.Probe.WaveForm", "AcAnalysis", ["__init__", "frequency"]),
                cls("PySpice.Probe.WaveForm", "TransientAnalysis", ["__init__", "time"]),
            ], "parser_waveform_fixture_planned", ["seed_pypi_raw/pyspice/PySpice/Spice/Parser.py", "seed_pypi_raw/pyspice/PySpice/Probe/WaveForm.py"]),
        ],
    },
    "lcapy_V1.26": {
        "id": "lcapy_symbolic_circuit_v1", "target": (50, 100),
        "boundary": "符号网表、电路分析、表达式域转换、极点零点和一/二端口等效；排除绘图、原理图排版和大量具体元件派生类。",
        "caps": [
            cap("circuit_netlist", "符号电路与分析", "Netlist 和 NetlistMixin 覆盖电路读取、MNA/节点/回路分析、等效和结果查询。", [
                cls("lcapy.circuit", "Circuit", ["__init__"]),
                cls("lcapy.netlist", "Netlist", ["__init__", "cpts", "symbols", "get_I", "get_Vd", "ac", "dc", "laplace", "time", "noise", "transient", "matrix_equations", "modified_nodal_analysis", "nodal_analysis", "loop_analysis", "select", "open_circuit", "short_circuit", "branch_currents", "branch_voltages"]),
                cls("lcapy.netlistmixin", "NetlistMixin", ["analysis", "components", "elements", "is_causal", "is_connected", "is_passive", "analyse", "check", "circuit_graph", "copy", "draw", "kill", "netlist", "subs", "state_space_model"]),
            ], "symbolic_netlist_fixture_planned", ["seed_pypi_raw/lcapy/lcapy/circuit.py", "seed_pypi_raw/lcapy/lcapy/netlist.py", "seed_pypi_raw/lcapy/lcapy/netlistmixin.py"]),
            cap("expressions", "表达式变换和系统响应", "Expr 与 Laplace/Time 表达式保留简化、求解、极点零点、频率响应及逆变换主链。", [
                cls("lcapy.expr", "Expr", ["__init__", "as_time", "as_laplace", "as_phasor", "as_fourier", "simplify", "subs", "differentiate", "integrate", "solve", "roots", "zeros", "poles", "ZPK", "evaluate", "is_stable"]),
                cls("lcapy.sexpr", "LaplaceDomainExpression", ["__init__", "from_zeros_poles_gain", "inverse_laplace", "time", "impulse_response", "step_response", "frequency_response", "response", "differential_equation", "lti_filter"]),
                cls("lcapy.texpr", "TimeDomainExpression", ["__init__", "laplace", "fourier", "frequency_response", "response", "initial_value", "final_value"]),
                *funcs("lcapy.expr", ["expr", "equation", "symbol", "symbols"]),
            ], "symbolic_transform_fixture_planned", ["seed_pypi_raw/lcapy/lcapy/expr.py", "seed_pypi_raw/lcapy/lcapy/sexpr.py", "seed_pypi_raw/lcapy/lcapy/texpr.py"]),
            cap("network_equivalents", "一端口与二端口等效", "OnePort 和 TwoPort 提供阻抗/导纳、Thevenin/Norton、传输与 S/Z/Y 参数及级联。", [
                cls("lcapy.oneport", "OnePort", ["admittance", "impedance", "isc", "voc", "chain", "norton", "thevenin", "parallel", "series"]),
                cls("lcapy.twoport", "TwoPort", ["__init__", "Aparams", "Sparams", "Yparams", "Zparams", "Vgain", "Igain", "Vresponse", "Iresponse", "chain", "cascade", "series", "terminate", "parallel", "load"]),
                *funcs("lcapy.oneport", ["series", "parallel", "ladder"]),
            ], "network_equivalent_fixture_planned", ["seed_pypi_raw/lcapy/lcapy/oneport.py", "seed_pypi_raw/lcapy/lcapy/twoport.py"]),
        ],
    },
    "sax_0.18.2": {
        "id": "sax_differentiable_smatrix_v1", "target": (50, 100),
        "boundary": "S 参数表示、网表归一化、电路求解、Touchstone、代表性 PIC/RF 模型和拟合；排除内部类型转换与后端实现细节。",
        "caps": [
            cap("smatrix_netlist", "S 矩阵、端口和网表", "保留三种 S 参数表示、互易补全、端口查询和网表规范化。", [
                *funcs("sax.s", ["sdict", "scoo", "sdense", "reciprocal", "block_diag", "get_ports", "get_modes", "get_mode", "get_port_combinations"]),
                *funcs("sax.netlists", ["netlist", "flatten_netlist", "remove_unused_instances", "rename_instances", "rename_models", "convert_nets_to_connections", "expand_probes", "extract_port_probes"]),
                cls("sax.ports", "PortNamer", ["__init__", "is_input_port", "is_output_port", "is_input_port_idx", "is_output_port_idx"]),
                *funcs("sax.ports", ["set_port_naming_strategy", "get_port_naming_strategy"]),
            ], "smatrix_roundtrip_planned", ["seed_pypi_raw/sax/src/sax/s.py", "seed_pypi_raw/sax/src/sax/netlists.py", "seed_pypi_raw/sax/src/sax/ports.py"]),
            cap("circuit_models", "电路组合与器件模型", "circuit 构建可调用模型；耦合器、直波导、MMI、分支和 RF 传输线形成代表模型集。", [
                *funcs("sax.circuits", ["circuit", "get_required_circuit_models", "resolve_array_instance", "resolve_array_instances", "patch_netlist_array_instances"]),
                *funcs("sax.models.couplers", ["coupler_ideal", "coupler", "grating_coupler"]),
                *funcs("sax.models.straight", ["straight", "attenuator", "phase_shifter"]),
                *funcs("sax.models.mmis", ["mmi1x2_ideal", "mmi2x2_ideal", "mmi1x2", "mmi2x2"]),
                fn("sax.models.splitters", "splitter_ideal"),
                *funcs("sax.models.rf", ["resistor", "capacitor", "inductor", "transmission_line_s_params", "coplanar_waveguide", "microstrip"]),
            ], "mzi_fixture_planned", ["seed_pypi_raw/sax/src/sax/circuits.py", "seed_pypi_raw/sax/src/sax/models"]),
            cap("io_fit", "Touchstone、插值和模型拟合", "文件 I/O 与插值支持固定 S 参数样例，拟合接口支持后续可微模型任务。", [
                *funcs("sax.parsers.touchstone", ["parse_touchstone", "write_touchstone"]),
                *funcs("sax.interpolation", ["interpolate_xarray", "to_xarray", "to_df"]),
                *funcs("sax.fit", ["neural_fit", "eval_neural_fit", "create_network", "train_network"]),
            ], "touchstone_fit_fixture_planned", ["seed_pypi_raw/sax/src/sax/parsers/touchstone.py", "seed_pypi_raw/sax/src/sax/interpolation.py", "seed_pypi_raw/sax/src/sax/fit.py"]),
        ],
    },
    "simphony_v0.7.3": {
        "id": "simphony_photonic_circuit_v1", "target": (50, 100),
        "boundary": "经典/量子 PIC 仿真、理想和 SiPANN 模型、矩阵/波长辅助；排除 Lumerical 在线插件和 SiEPIC 大型文件库适配。",
        "caps": [
            cap("classical", "经典光子电路仿真", "Simulation 与 ClassicalSim 组织器件、激光器、探测器和结果。", [
                cls("simphony.simulation", "SimDevice", ["__init__"]),
                cls("simphony.simulation", "Simulation", ["__init__", "run"]),
                cls("simphony.classical", "Laser", ["__init__"]),
                cls("simphony.classical", "Detector", ["__init__", "set_result", "plot"]),
                cls("simphony.classical", "ClassicalSim", ["__init__", "add_laser", "add_detector", "run"]),
            ], "classical_circuit_fixture_planned", ["seed_pypi_raw/simphony/simphony/simulation.py", "seed_pypi_raw/simphony/simphony/classical.py"]),
            cap("models", "理想器件与 SiPANN 模型", "代表性耦合器、波导和环形器件模型提供电路 S 参数输入。", [
                *funcs("simphony.libraries.ideal", ["coupler", "waveguide"]),
                *funcs("simphony.libraries.sipann", ["gap_func_symmetric", "gap_func_antisymmetric", "half_ring", "straight_coupler", "standard_coupler", "double_half_ring", "angled_half_ring", "waveguide", "racetrack", "premade_coupler"]),
            ], "model_fixture_planned", ["seed_pypi_raw/simphony/simphony/libraries/ideal.py", "seed_pypi_raw/simphony/simphony/libraries/sipann.py"]),
            cap("quantum", "高斯量子态和量子电路", "量子态、相干/压缩/热态和 QuantumSim 覆盖状态构造、幺正变换与结果。", [
                cls("simphony.quantum", "QuantumState", ["__init__", "to_xpxp", "to_xxpp", "modes", "plot_mode"]),
                cls("simphony.quantum", "CoherentState", ["__init__"]),
                cls("simphony.quantum", "SqueezedState", ["__init__"]),
                cls("simphony.quantum", "TwoModeSqueezedState", ["__init__"]),
                cls("simphony.quantum", "ThermalState", ["__init__"]),
                cls("simphony.quantum", "QuantumSim", ["__init__", "add_qstate", "to_unitary", "run"]),
                *funcs("simphony.quantum", ["compose_qstate", "plot_quantum_result"]),
            ], "quantum_state_fixture_planned", ["seed_pypi_raw/simphony/simphony/quantum.py"]),
            cap("utilities", "矩阵、波长和插值辅助", "保留模型输入输出转换与结果重采样的通用函数。", [
                *funcs("simphony.utils", ["rect", "polar", "add_polar", "mul_polar", "mat_mul_polar", "mat_add_polar", "freq2wl", "wl2freq", "wlum2freq", "interpolate", "xxpp_to_xpxp", "xpxp_to_xxpp", "dict_to_matrix", "validate_model", "resample"]),
            ], "numeric_utility_fixture_planned", ["seed_pypi_raw/simphony/simphony/utils.py"]),
        ],
    },
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
                selected = set(symbol.get("methods", []))
                lacks_docs = lacks_docs or any(
                    method["name"] in selected and not str(method.get("description") or "").strip()
                    for method in source.get("function", [])
                )
            if lacks_docs:
                symbol["missing_description_reason"] = "该接口属于发布版公开能力链；源码缺少独立说明，保留原空字段，不根据名称补造，也不表示运行已验证。"
        profile = {
            "profile_id": rule["id"], "package": seed["environment"]["basic_info"]["name"],
            "version": seed["environment"]["basic_info"]["version"],
            "source_sha256": _canonical_sha256(payload), "boundary": rule["boundary"],
            "target_all_func": {"min": rule["target"][0], "max": rule["target"][1]},
            "capabilities": rule["caps"],
        }
        destination = OUT / f"{stem}.json"
        destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    main()
