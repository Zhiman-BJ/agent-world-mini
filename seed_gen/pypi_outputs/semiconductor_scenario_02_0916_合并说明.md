# semiconductor_scenario_02_0916 可替代包精简说明

后续格式调整：`environment` 仅保留 `description`、`domain`、`nums`；`basic_info`、`scenario`、`pypi_package`、`package_relation` 移至对象类型的 `others`，原 `others` 包来源数组原样保存为 `others.package_metadata`，仍与 `others.basic_info` 顺序对应。原 `environment.possible_task` 移至 `init_ref_tasks: [{"possible_task": "原任务文本"}]`。下文筛选说明中的原字段位置以此迁移为准；22 个场景均通过逆向还原校验，内容无损。这些任务文本仍为设计方向，不代表已实现或执行的任务。

处理范围：指定合并文件中的 4 个可替代场景。其余 18 个场景逐对象校验未变；源 scenario_seeds、包级选集和筛选规则不在本次修改范围。

保留工具沿用原模块、名称、签名、描述、输入输出。按功能角色与对象依赖判断重叠，不按同名函数盲目合并，也不改写为不存在的统一 API。

| 场景 | 主包 / 补充包 | 类 | 函数 | 类方法 | all_func（前 → 后） |
| --- | --- | ---: | ---: | ---: | ---: |
| 02.01.01 Poisson / Drift-Diffusion | devsim | 0 | 66 | 0 | 113 → 66 |
| 02.04.01 Netlist Execution | spicelib | 5 | 0 | 55 | 142 → 55 |
| 02.07.01 Circuit-level S Matrix | sax | 1 | 40 | 5 | 81 → 45 |
| 02.10.01 Geometry Inverse Design | ceviche + legume-gme | 14 | 37 | 60 | 151 → 97 |

22 个场景的 all_func 累加：2243 → 2019，减少 224 个操作。此处允许跨场景复用同一接口，非全文件全局去重数。

basic_info、pypi_package、others 按保留包同步过滤并保持顺序；environment.nums 重新计算。others 内原包级筛选数字仍是来源快照，不代表当前场景数量。

## 02.01.01 Poisson / Drift-Diffusion

DEVSIM 的现有接口覆盖网格—模型/方程—求解—结果查询，适合 PN/MOS/diode 场景。Sesame 的缺陷、复合分析虽有细分能力，但 Analyzer 依赖 Sesame 的 sys/data 对象，无法作为 DEVSIM 结果的直接补充；本次连同其 Builder/Solver 一起移除。

移除的顶层条目（类连同其原方法移除）：

- `sesame.builder.Builder`
- `sesame.builder.Scaling`
- `sesame.solvers.Solver`
- `sesame.analyzer.Analyzer`
- `sesame.observables.get_n`
- `sesame.observables.get_p`
- `sesame.observables.get_jn`
- `sesame.observables.get_jp`
- `sesame.observables.get_bulk_rr`
- `sesame.utils.save_sim`
- `sesame.utils.load_sim`
- `sesame.utils.get_indices`
- `sesame.utils.get_xyz_from_s`
- `sesame.utils.get_dl`
- `sesame.utils.get_point_defects_sites`
- `sesame.utils.get_line_defects_sites`
- `sesame.utils.check_equal_sim_settings`

## 02.04.01 Netlist Execution

当前任务从已有网表出发，spicelib 的 SpiceEditor、SimRunner、RawRead、LogfileData 与任务一致，也与 02.04.02 批量扫描场景统一。PySpice 的电路对象、解析器和波形对象属于另一套完整前端；其独有的网表转 Python 能力不是当前任务所需，不单独拼入。

移除的顶层条目（类连同其原方法移除）：

- `PySpice.Spice.Netlist.Circuit`
- `PySpice.Spice.Netlist.Netlist`
- `PySpice.Spice.Netlist.DeviceModel`
- `PySpice.Spice.Netlist.SubCircuit`
- `PySpice.Spice.Simulation.CircuitSimulation`
- `PySpice.Spice.Simulation.CircuitSimulator`
- `PySpice.Spice.NgSpice.Simulation.NgSpiceSharedCircuitSimulator`
- `PySpice.Spice.Xyce.Simulation.XyceCircuitSimulator`
- `PySpice.Spice.Parser.SpiceParser`
- `PySpice.Spice.Library.SpiceLibrary`
- `PySpice.Probe.WaveForm.WaveForm`
- `PySpice.Probe.WaveForm.Analysis`
- `PySpice.Probe.WaveForm.DcAnalysis`
- `PySpice.Probe.WaveForm.AcAnalysis`
- `PySpice.Probe.WaveForm.TransientAnalysis`

## 02.07.01 Circuit-level S Matrix

SAX 已包含波导、耦合器、MMI、相移器、网表与电路组合。Simphony 的 SiPANN 模型虽返回 SAX SDict、物理模型有补充价值，但 simphony 0.7.3 的 pyproject.toml 明确要求 sax < 0.15.0，与本产物 sax 0.18.2 冲突，且需要额外 SiPANN 依赖；不在此固定版本环境合并。保留实际 45 个操作，不为达到 50 补入重复接口。

移除的顶层条目（类连同其原方法移除）：

- `simphony.simulation.SimDevice`
- `simphony.simulation.Simulation`
- `simphony.classical.Laser`
- `simphony.classical.Detector`
- `simphony.classical.ClassicalSim`
- `simphony.libraries.ideal.coupler`
- `simphony.libraries.ideal.waveguide`
- `simphony.libraries.sipann.gap_func_symmetric`
- `simphony.libraries.sipann.gap_func_antisymmetric`
- `simphony.libraries.sipann.half_ring`
- `simphony.libraries.sipann.straight_coupler`
- `simphony.libraries.sipann.standard_coupler`
- `simphony.libraries.sipann.double_half_ring`
- `simphony.libraries.sipann.angled_half_ring`
- `simphony.libraries.sipann.waveguide`
- `simphony.libraries.sipann.racetrack`
- `simphony.libraries.sipann.premade_coupler`
- `simphony.utils.rect`
- `simphony.utils.polar`
- `simphony.utils.add_polar`
- `simphony.utils.mul_polar`
- `simphony.utils.mat_mul_polar`
- `simphony.utils.mat_add_polar`
- `simphony.utils.freq2wl`
- `simphony.utils.wl2freq`
- `simphony.utils.wlum2freq`
- `simphony.utils.interpolate`
- `simphony.utils.dict_to_matrix`
- `simphony.utils.validate_model`
- `simphony.utils.resample`

## 02.10.01 Geometry Inverse Design

ceviche 与 angler 在当前线性二维 FDFD、源、梯度和优化任务上重叠，保留 ceviche。angler 的滤波/投影有补充价值，但混用 NumPy/SciPy，接入 ceviche 自动微分需要额外适配及梯度校验，不能直接拼接为已可用链，因此本次不保留。legume 的周期光子晶体及导模展开属于不同物理模型，保留几何、GuidedModeExp、辐射损耗和可视化；Minimize 只留 __init__/lbfgs，Adam 统一由 ceviche 提供。

移除的顶层条目（类连同其原方法移除）：

- `angler.simulation.Simulation`
- `angler.source.mode.mode`
- `angler.linalg.grid_average`
- `angler.linalg.dL`
- `angler.linalg.construct_A`
- `angler.linalg.solver_eigs`
- `angler.linalg.solver_direct`
- `angler.objective.Objective`
- `angler.optimization.Optimization`
- `angler.adjoint.adjoint_linear_Ez`
- `angler.adjoint.adjoint_linear_Hz`
- `angler.gradients.grad_linear_Ez`
- `angler.gradients.grad_linear_Hx`
- `angler.gradients.grad_linear_Hy`
- `angler.filter.get_W`
- `angler.filter.rho2rhot`
- `angler.filter.rhot2rhob`
- `angler.filter.rhob2eps`
- `angler.filter.eps2rho`
- `angler.filter.rho2eps`
- `angler.filter.drhot_drho`
- `angler.filter.deps_drhob`
- `angler.structures.get_grid`
- `angler.structures.apply_regions`
- `angler.structures.three_port`
- `angler.structures.two_port`
- `angler.structures.ortho_port`
- `angler.utils.Binarizer`

另外移除类方法：`legume.minimize.Minimize.adam`。

## 证据与验证边界

- Sesame 对象依赖：`seed_pypi_raw/sesame/sesame/analyzer.py` 的 `Analyzer.__init__(sys, data)` 及 `sys` 材料、网格、标度字段。
- Simphony 版本限制与可选依赖：`seed_pypi_raw/simphony/pyproject.toml` 的 `sax < 0.15.0`、`sipann = ["SiPANN"]`；模型返回格式见 `simphony/libraries/sipann.py`。
- angler 滤波实现：`seed_pypi_raw/angler/angler/filter.py` 使用 NumPy/SciPy 稀疏矩阵，未实施跨库可微适配；独有非线性求解原本未选入此场景。
- legume 优化器：`seed_pypi_raw/legume-gme/legume/minimize.py`；保留 L-BFGS，移除公开 Adam 工具入口不修改第三方源码。
- 静态验证：22 个场景 ID 唯一、4 处定向变更、18 个场景原样保留、包元数据对齐、工具和类方法无重复、保留文档/签名无损、nums 一致。
- 未安装或执行求解器；spicelib 仍需要外部 SPICE、后端配置和继承接口。ceviche/legume 按物理模型分别使用，未验证跨包运行兼容性，也未实现制造约束封装或可执行任务。
- 本文件是 0916 汇总快照的定向精简记录；重新合并原 scenario_seeds 会恢复原选集，不会自动应用本次决策。

原输入 SHA-256：`c79ee6b7048eebbc2b6fd46168d88fc9fa02a57b2bacd6843496722e859a0bd1`。
