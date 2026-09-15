# 半导体 Python 包覆盖报告

核查日期：2026-09-15。本报告由 `seed_gen.scripts.audit_semiconductor_python_packages` 从目标范围、来源清单、发布源码、全量索引、筛选 profile 和精选产物重算生成。

## 结论

目标表包含 22 个 L3 场景和 29 个不同 backend；其中 26 个已完成发布版源码、全量 API、显式筛选 profile、精选 JSON 和筛选报告，3 个按边界记录。全部 L3 场景均有已采集 Python 包或明确的外部边界。

26 个 Python 包的 remote、发布 ref、HEAD 和干净工作区检查通过；26 个精选结果可从全量 JSON 与 profile 原样重算。严格 Schema 通过 3/26；其余仅包含源码缺失 docstring 导致的空 `description`，共 538 项，未补造文本。

精选数量通常为 50–100；低于 50 的有据例外为：meshio、mobilitypy。

## 场景覆盖

| L3 | 应用场景 | Backend 状态 | 关系/组合 | 可合成任务 |
| --- | --- | --- | --- | --- |
| 02.01.01 Poisson / Drift-Diffusion | PN/MOS/diode 基础器件 | devsim（collected）；sesame（collected） | 主要 A | 设置 doping/contact；跑 IV；修 solver |
| 02.01.02 Heterostructure / Quantum Device | QW/QCL/RTD/HEMT | nextnanopy（collected）；nextnano（external_solver） | Python client + external solver | 改 layer thickness；找目标 transition energy |
| 02.01.03 Solar / Optoelectronic Device | solar cell / QW solar cell | solcore（collected） | 独立综合环境 | 改 layer/材料，使 efficiency 最大 |
| 02.02.01 Mobility / Scattering | HEMT/2DEG mobility | mobilitypy（collected） | 可接 openbandparams → TCAD | 找 dominant scattering mechanism |
| 02.03.01 Geometry Construction | TCAD/FEM 几何 | gmsh（collected）；pygmsh（collected） | pygmsh → Gmsh H/I | 建 MOS cross-section |
| 02.03.02 Mesh Interchange | solver 间 mesh 传递 | meshio（collected） | Gmsh → meshio D | 格式转换、检查 physical group |
| 02.03.03 Generic FEM/PDE | 自定义 electrothermal/PDE | scikit-fem（collected） | meshio → scikit-fem I/D | 边界条件/PDE 调试 |
| 02.04.01 Netlist Execution | DC/AC/transient | spicelib（collected）；PySpice（collected）；ngspyce（no_confirmed_release） | 三者多数 A；底层 ngspice/Xyce | 修 netlist；运行仿真 |
| 02.04.02 Parameter Sweep / Monte Carlo | process/device variation | spicelib（collected）；ngspice/Xyce（external_solver） | + ngspice/Xyce | 调 R/C/W/L 到 spec |
| 02.05.01 Symbolic Analysis | transfer function、impedance | lcapy（collected） | 可与 SPICE C | 推导极点/零点并 cross-check |
| 02.05.02 Design Optimization | corner/performance optimization | PyOPUS（collected） | + SPICE C | 多 corner 参数优化 |
| 02.06.01 S-parameter Network Analysis | RF device/VNA | scikit-rf（collected） | - | cascade、S↔Z、gain、matching |
| 02.06.02 Calibration / De-embedding | wafer probe/VNA | scikit-rf（collected） | + measurement backend C | 从 raw standards 完成校准 |
| 02.07.01 Circuit-level S Matrix | PIC network | sax（collected）；simphony（collected） | 多为 A | 连接 MZI/MMI，优化 spectrum |
| 02.08.01 Open-source FDTD | waveguide/cavity | meep（collected） | gdsfactory integration I | geometry→FDTD→spectrum |
| 02.08.02 Cloud FDTD | 大规模/优化 | tidy3d（collected） | gdsfactory integration I | 同类任务，但有云成本 |
| 02.09.01 Eigenmode | waveguide mode | femwell（collected） | gdsfactory integration I | 调 width 到目标 neff |
| 02.09.02 EME / propagation | sectional waveguide propagation | emepy（collected） | - | 分段结构 transmission |
| 02.10.01 Geometry Inverse Design | grating/coupler inverse design | ceviche（collected）；angler（collected）；legume-gme（collected） | 多为 A | optimize pixels/geometry |
| 02.11.01 Plasma / Etch Physics | plasma properties、etch chamber physics | plasmapy（collected） | 与 Cantera/FiPy C | 从 pressure/power/composition 推 plasma regime |
| 02.11.02 Reaction / Gas Chemistry | CVD/etch gas-phase chemistry | cantera（collected） | C | 调 gas ratio/temperature，满足 species target |
| 02.11.03 Diffusion / Reaction PDE | diffusion、surface reaction、electrodeposition | fipy（collected） | geometry/mesh 可配合 C | 调 boundary/transport 参数，复现 profile |

## 包级计数

| 包 | 版本 | 全量 class/function/class_func/all_func | 精选 class/function/class_func/all_func | Schema |
| --- | --- | ---: | ---: | --- |
| angler | 0.0.15 | 8/60/39/99 | 6/31/37/68 | 空 description 52 |
| cantera | v3.2.0 | 186/85/992/1077 | 12/0/95/95 | 空 description 7 |
| ceviche | v0.1.3 | 6/81/14/95 | 6/51/14/65 | 空 description 16 |
| devsim | v2.11.0.rc5 | 6/167/31/198 | 0/75/0/75 | 通过 |
| emepy | v1.2.2 | 38/17/203/220 | 15/0/89/89 | 空 description 26 |
| femwell | v0.1.12 | 4/78/47/125 | 3/32/36/68 | 空 description 34 |
| fipy | 4.0.3 | 265/79/413/492 | 13/6/70/76 | 空 description 30 |
| gmsh | gmsh_4_15_2 | 0/377/0/377 | 0/75/0/75 | 通过 |
| lcapy | V1.26 | 788/265/2746/3011 | 8/7/93/100 | 空 description 14 |
| legume-gme | v1.0.3 | 23/73/96/169 | 12/8/65/73 | 空 description 9 |
| meep | v1.34.0 | 78/218/483/701 | 16/14/86/100 | 空 description 35 |
| meshio | v5.3.5 | 15/147/50/197 | 4/3/18/21 | 空 description 21 |
| mobilitypy | v1.0.2 | 5/0/17/17 | 5/0/17/17 | 空 description 4 |
| nextnanopy | v1.3.2 | 46/93/307/400 | 15/5/67/72 | 空 description 59 |
| plasmapy | v2026.2.0 | 120/194/451/645 | 5/44/47/91 | 空 description 5 |
| pygmsh | v7.1.17 | 31/7/96/103 | 7/4/46/50 | 空 description 39 |
| PyOPUS | v0.12 | 404/253/1861/2114 | 11/15/83/98 | 空 description 33 |
| PySpice | v1.5 | 275/38/677/715 | 15/0/94/94 | 空 description 70 |
| sax | 0.18.2 | 9/172/6/178 | 1/50/5/55 | 空 description 6 |
| scikit-fem | 12.0.2 | 121/112/336/448 | 7/18/44/62 | 空 description 18 |
| scikit-rf | v2.1.0 | 210/418/1698/2116 | 10/17/81/98 | 空 description 8 |
| sesame | 2.0.3 | 20/36/104/140 | 4/16/34/50 | 空 description 14 |
| simphony | v0.7.3 | 20/44/54/98 | 11/29/24/53 | 空 description 10 |
| solcore | v5.10.1 | 63/310/237/547 | 17/27/56/83 | 空 description 15 |
| spicelib | 1.6.3 | 114/44/554/598 | 9/0/79/79 | 空 description 13 |
| tidy3d | v2.12.0 | 885/624/2126/2750 | 15/6/94/100 | 通过 |

## 未采集边界

- `nextnano`：nextnano 是需许可的外部求解器，不是本表要静态解析的 Python 包；已整理其官方 Python 客户端 nextnanopy。
- `ngspice/Xyce`：ngspice 和 Xyce 是 SPICE 求解器可执行程序/共享库，不作为 Python API 包索引；其 Python 调用边界由 spicelib、PySpice 和 PyOPUS 表示。
- `ngspyce`：官方仓库没有标签或 Release，PyPI 无同名项目，无法确认可复现的发布版本；按指南不把默认分支冒充发布版，因此不生成源码发布索引和精选 JSON。

## 验证边界

本报告验证的是官方发布来源、源码身份、静态 API 提取、精选子集和数量一致性。外部求解器、许可、云服务、原生库编译、数值精度和端到端科研结果均未由此次采集验证。表中的任务是后续任务合成方向，不代表现有任务已执行通过。
