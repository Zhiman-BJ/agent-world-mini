"""Build explicit profiles for PyOPUS and angler."""

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
    "PyOPUS_v0.12": {
        "id": "pyopus_circuit_optimization_v1", "target": (50, 100),
        "boundary": "外部 SPICE 仿真器协议、性能度量、数值优化、Monte Carlo、灵敏度和最坏情况设计；排除 GUI、绘图、测试问题集和 MPI 内部。",
        "caps": [
            cap("simulation", "仿真器协议与 ngspice 适配", "通用 Simulator 和 Ngspice 覆盖任务分组、输入参数、运行与结果向量访问。", [
                cls("pyopus.simulator.base", "Simulator", ["__init__", "findSimulator", "cleanup", "startSimulator", "stopSimulator", "setJobList", "jobGroupCount", "jobGroup", "setInputParameters", "runJobGroup", "cleanupResults", "readResults"]),
                cls("pyopus.simulator.base", "SimulationResults", ["__init__", "driverTable", "evalEnvironment", "param", "var", "result", "vector"]),
                *funcs("pyopus.simulator.ngspice", ["save_all", "save_voltage", "save_current", "an_op", "an_dc", "an_ac", "an_tran"]),
                cls("pyopus.simulator.ngspice", "Ngspice", ["__init__", "findSimulator", "writeFile", "cleanupResults", "runFile", "runJobGroup", "readResults"]),
                cls("pyopus.simulator.ngspice", "NgspiceSimulationResults", ["__init__", "vectorNames", "vector", "scale", "v", "i"]),
            ], "external_ngspice_not_run", ["seed_pypi_raw/pyopus/pyopus/simulator/base.py", "seed_pypi_raw/pyopus/pyopus/simulator/ngspice.py"]),
            cap("performance", "角点性能评估与波形度量", "PerformanceEvaluator 编排仿真角点，代表性 DC/AC/瞬态度量生成优化目标。", [
                cls("pyopus.evaluator.performance", "PerformanceEvaluator", ["__init__", "evaluateComponentNames", "availableCornerListsForMeasures", "setParameters", "setVariables", "setActiveMeasures", "getComputedMeasures", "simulators", "generateJobs", "evaluateMeasure", "processJob", "collectResults", "formatResults"]),
                *funcs("pyopus.evaluator.measure", ["DCgain", "ACgain", "ACbandwidth", "ACugbw", "ACphaseMargin", "ACgainMargin", "TriseTime", "TsettlingTime"]),
            ], "synthetic_waveform_fixture_planned", ["seed_pypi_raw/pyopus/pyopus/evaluator/performance.py", "seed_pypi_raw/pyopus/pyopus/evaluator/measure.py"]),
            cap("optimization", "数值优化器", "通用 Optimizer、Hooke-Jeeves 和 Nelder-Mead 提供目标评估、插件和局部优化入口。", [
                cls("pyopus.optimizer.base", "Optimizer", ["__init__", "check", "installPlugin", "getEvaluator", "fun", "updateBest", "reset", "run"]),
                cls("pyopus.optimizer.hj", "HookeJeeves", ["__init__", "check", "reset", "run"]),
                cls("pyopus.optimizer.nm", "NelderMead", ["__init__", "check", "reset", "run"]),
            ], "analytic_objective_fixture_planned", ["seed_pypi_raw/pyopus/pyopus/optimizer/base.py", "seed_pypi_raw/pyopus/pyopus/optimizer/hj.py", "seed_pypi_raw/pyopus/pyopus/optimizer/nm.py"]),
            cap("robust_design", "Monte Carlo、灵敏度与最坏情况", "三种高层分析对象覆盖变异采样、参数筛选和角点最坏情况优化。", [
                cls("pyopus.design.mc", "MonteCarlo", ["__init__", "jobGenerator", "jobProcessor", "jobCollector", "compute", "formatResults"]),
                cls("pyopus.design.sensitivity", "Sensitivity", ["__init__", "preparePerturbations", "jobGenerator", "jobProcessor", "jobCollector", "diffVector"]),
                cls("pyopus.design.wc", "WorstCase", ["__init__", "jobGenerator", "jobProcessor", "jobCollector", "initialEvaluation", "opWorstCase", "sensitivityAndScreening", "wcOptimization", "compute", "formatResults"]),
            ], "robust_design_mock_planned", ["seed_pypi_raw/pyopus/pyopus/design/mc.py", "seed_pypi_raw/pyopus/pyopus/design/sensitivity.py", "seed_pypi_raw/pyopus/pyopus/design/wc.py"]),
        ],
    },
    "angler_0.0.15": {
        "id": "angler_nonlinear_inverse_design_v1", "target": (50, 100),
        "boundary": "二维线性/非线性电磁仿真、模式源、伴随梯度、设计滤波和优化；排除绘图辅助与重复场分量梯度。",
        "caps": [
            cap("simulation", "仿真域、模式源和场求解", "Simulation 和 mode 覆盖材料、源、线性/非线性场、功率流与设计区域。", [
                cls("angler.simulation", "Simulation", ["__init__", "setup_modes", "add_mode", "compute_nl", "add_nl", "eps_r", "solve_fields", "solve_fields_nl", "flux_probe", "init_design_region", "compute_index_shift", "plt_abs", "plt_re", "plt_diff", "plt_eps"]),
                cls("angler.source.mode", "mode", ["__init__", "setup_src", "compute_normalization", "insert_mode"]),
                cls("angler.nonlinearity", "Nonlinearity", ["__init__"]),
                *funcs("angler.nonlinear_solvers", ["born_solve", "newton_solve", "nl_eq_and_jac", "newton_krylov_solve"]),
                *funcs("angler.linalg", ["grid_average", "dL", "construct_A", "solver_eigs", "solver_direct"]),
            ], "fixed_grid_fixture_planned", ["seed_pypi_raw/angler/angler/simulation.py", "seed_pypi_raw/angler/angler/source/mode.py", "seed_pypi_raw/angler/angler/nonlinear_solvers.py"]),
            cap("adjoint_optimization", "目标函数、伴随梯度和优化循环", "Objective、Optimization 及线性/非线性伴随梯度形成更新设计的闭环。", [
                cls("angler.objective", "Objective", ["__init__", "is_linear", "J"]),
                cls("angler.optimization", "Optimization", ["__init__", "compute_J", "compute_dJ", "check_deriv", "run", "plot_it", "plt_objs", "scan_frequency", "scan_power", "plot_transmissions"]),
                *funcs("angler.adjoint", ["adjoint_linear_Ez", "adjoint_linear_Hz", "adjoint_kerr_Ez"]),
                *funcs("angler.gradients", ["grad_linear_Ez", "grad_linear_Hx", "grad_linear_Hy", "grad_kerr_Ez", "grad_kerr_Hx", "grad_kerr_Hy"]),
            ], "gradient_check_planned", ["seed_pypi_raw/angler/angler/objective.py", "seed_pypi_raw/angler/angler/optimization.py", "seed_pypi_raw/angler/angler/adjoint.py", "seed_pypi_raw/angler/angler/gradients.py"]),
            cap("design_parameterization", "结构参数化和滤波", "密度滤波、阈值映射、代表性端口结构与二值化指标约束设计变量。", [
                *funcs("angler.filter", ["get_W", "rho2rhot", "rhot2rhob", "rhob2eps", "eps2rho", "rho2eps", "drhot_drho", "deps_drhob"]),
                *funcs("angler.structures", ["get_grid", "apply_regions", "three_port", "two_port", "ortho_port"]),
                cls("angler.utils", "Binarizer", ["__init__", "density", "density_exp", "smoothness"]),
            ], "parameterization_fixture_planned", ["seed_pypi_raw/angler/angler/filter.py", "seed_pypi_raw/angler/angler/structures.py", "seed_pypi_raw/angler/angler/utils.py"]),
        ],
    },
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, rule in RULES.items():
        payload = json.loads((RAW / f"{stem}.json").read_text(encoding="utf-8"))
        seed = payload[0]
        available = {(item["module"], item["name"], item["type"]): item for item in seed["init_ref_tools"]}
        for group in rule["caps"]:
            for symbol in group["symbols"]:
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
                    lacks_docs = lacks_docs or any(method["name"] in selected and not str(method.get("description") or "").strip() for method in source.get("function", []))
                if lacks_docs:
                    symbol["missing_description_reason"] = "该接口属于发布版公开能力链；源码缺少独立说明，保留原空字段，不根据名称补造，也不表示运行已验证。"
        profile = {"profile_id": rule["id"], "package": seed["environment"]["basic_info"]["name"], "version": seed["environment"]["basic_info"]["version"], "source_sha256": _canonical_sha256(payload), "boundary": rule["boundary"], "target_all_func": {"min": rule["target"][0], "max": rule["target"][1]}, "capabilities": rule["caps"]}
        destination = OUT / f"{stem}.json"
        destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    main()
