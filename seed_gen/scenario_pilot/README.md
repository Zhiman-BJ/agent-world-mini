# 场景驱动种子流程：三场景试点（2026-09-17）

本轮按照 [场景指南](../半导体场景种子搜集整理指南.md) 完成网页/发布来源核查、源码复用与全量重解析、跨包联合筛选、实体设计和本地任务小样例。未启动全部分类采集，也未实现完整 Agent 工具服务器。

## 结果入口

2026-09-18清理：三场景试点输出及其审计副本已移出产物目录，整个`pypi_outputs/scenario_pilot/`不再上传；恢复位置见 [正式产物说明](../pypi_outputs/final_results/README.md)。保留研究记录、验证脚本、依赖锁和本地运行证据，按下方命令可重建。本试点不替换正式02快照。以下统计记录原试点执行结果。

- 重建后合并种子：`../pypi_outputs/scenario_pilot/semiconductor_scenario_pilot.json`
- 重建后计数与验证摘要：`../pypi_outputs/scenario_pilot/reports/summary.json`
- [联合生成器](../scripts/build_joint_scenario_seeds.py)、[试点 profile 归一化脚本](../scripts/prepare_scenario_pilot_profiles.py)
- `work/*_research.json`：原始研究记录，包括实际请求网页、发布版本、实体/属性、去重决策和具体任务配方。
- `profiles/*.json`：审核后的显式符号/方法清单、来源哈希、能力与任务引用。
- `../pypi_outputs/scenario_pilot/reports/02.*.json`：每个候选工具及方法的保留/排除审计。

| 场景 | 候选包 → 实际组合 | 类 | 函数 | 类方法 | all_func | 网站 | 固定任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 02.01.01 基础器件 | DEVSIM / Sesame → DEVSIM | 0 | 83 | 0 | 83 | 4 | 2 |
| 02.03.03 FEM/PDE | meshio + scikit-fem → 两包互补 | 12 | 10 | 55 | 65 | 4 | 2 |
| 02.07.01 光子电路 | SAX / Simphony → SAX | 0 | 51 | 0 | 51 | 4 | 2 |

合计 199 个参考操作、6 条完整任务配方和 12 条实际 HTTP 200 来源。类容器不另加进 all_func；mesh 中保留 4 个没有直接定义方法的具体类型，其构造由继承/object/dataclass提供，不能因 methods 为空而丢掉对象入口。SAX/DEVSIM 不以类为主，也能定义稳定实体状态。

六个候选包的最新正式发布与已有源码相同，因此复用 clean 的标签 checkout，没有重复 clone：DEVSIM `v2.11.0.rc5`（官方 prerelease=false，对应分发2.11.0）、Sesame `2.0.3`、meshio `v5.3.5`、scikit-fem `12.0.2`、SAX `0.18.2`、Simphony `v0.7.3`。六份全量索引均重解析后逐对象相等。

此次不是旧包级选集的合并。TCAD 补入物理方程高层辅助；Mesh 检查构造/继承方法并补齐桥接；PIC 对内部薄包装、重复模型与不兼容候选作了联合取舍。实际主包为4个，另两个包仍有完整候选审计记录。

## 实体和任务粒度

| 场景 | 实体/状态示例 | 本次运行的任务链与验收 |
| --- | --- | --- |
| TCAD | 网格、区域材料、接触电压、模型/方程、节点场、偏压扫描 | 465节点PN：构造→平衡→DD→0–0.3 V扫描→接触电流连续→保存恢复；介质电容：错误介电常数→解析电荷目标失败→修复→场/电荷通过。 |
| FEM/PDE | 网格拓扑/标签、边界、源项、有限元基、系统矩阵、解场/误差 | 固定VTU→meshio→scikit-fem→边界/弱式→求解→加密误差→结果VTU往返；错误零源项→解析误差失败→恢复源项。 |
| PIC | 器件参数、端口、连接网表、实例设置、波长扫描、S参数响应 | MZI错误工作点→修改上臂电压→目标透射/功率平衡；环形损耗单位换算→反馈电路求解→独立复数解析谱验证。 |

任务目标不是“调用函数没有报错”。固定输入、动作限制、不可修改的目标常数和 verifier 应分离。原始工具的 NumPy数组/callable/求解器全局状态仍需后续用对象ID、任务目录、版本和失效规则包装。

## 运行证据与本轮发现

1. [TCAD报告](runtime/tcad/report.json)：PN电流连续、载流子/场与原生保存恢复通过；修复电容后的电势最大误差 `1.11e-16 V`、两端电荷平衡误差 `1.01e-28 C/cm²`。DEVSIM使用本机 oneAPI MKL 2023.1 的 `mkl_rt.2.dll`，原生依赖不在 pip freeze 中。
2. [Mesh报告](runtime/mesh/verification.json)：12条断言通过；细网格L2误差 `0.001350436`，细化误差比 `0.25113`。错误源项的残差恰为零，但解析误差为 `0.5`，仍被拒绝。证明“求解器收敛”不足以充当任务完成判据。
3. [PIC报告](runtime/pic/validation.json)：MZI功率平衡最大误差 `8.88e-16`；全通环实际错误输入 `loss_dB_cm=0.1` 的101/101点被同一个固定解析oracle拒绝，最大误差 `0.50153`，改为 `1000.0` 后最大误差约 `4.19e-14`。仅覆盖固定经典电路、无损相移器与指定反馈后端。

执行中修复/记录了以下真实问题：

- DEVSIM首次PN零偏报 `AssertionError: Contact current conservation failed at 0`，电流不平衡约 `4.92e-8 A/cm²`；[原失败报告](runtime/tcad/attempt_01_double_precision_failure.json)保留。按官方float128算例启用 `extended_solver/extended_model/extended_equation` 后通过，没有放宽电流阈值。`solve.absolute_error` 是更新范数，不称为已验证的方程残差。
- `skfem.io` 不直接重导出 `to_meshio`；改用真实定义 `skfem.io.meshio.to_meshio`。已校验输入拓扑、区域标签和输出点数据的固定路径，未推定所有格式均能保留任意标签。
- SAX中 `get_settings` 的嵌套 `wl` 会覆盖顶层扫描；维数断言识别后，先全局 `update_settings(..., wl=...)` 再调上臂电压。
- Simphony 0.7.3 的 `sax<0.15.0` / `lark~=1.1.5` 与主包冲突，未拼入。phase_shifter 的非零loss实现与文档语义还需核对，本次固定loss=0。
- 原生参数、物理单位、隐式构造与返回callable是种子到动作契约的实际边界，已写入 profile。原始空说明按源码保留：工具 Schema 有33处空 description 例外，其他工具结构错误为0；未修改旧全局种子契约。

## 重现命令

以下安装命令只在首次建立环境时使用。使用 conda base 中的 uv，Python 3.12.4；三个独立目录均由 uv 自带 `.gitignore` 排除，不污染项目 `.venv`。freeze 固定了本次 Python 分发版本，不等于跨平台带原生依赖的锁文件。

```powershell
C:/Apps/anaconda3/python.exe -m uv venv --python 3.12.4 .venv-scenario-tcad
C:/Apps/anaconda3/python.exe -m uv pip install --python .venv-scenario-tcad/Scripts/python.exe -r seed_gen/scenario_pilot/requirements-tcad.freeze.txt
C:/Apps/anaconda3/python.exe -m uv venv --python 3.12.4 .venv-scenario-mesh
C:/Apps/anaconda3/python.exe -m uv pip install --python .venv-scenario-mesh/Scripts/python.exe -r seed_gen/scenario_pilot/requirements-mesh.freeze.txt
C:/Apps/anaconda3/python.exe -m uv venv --python 3.12.4 .venv-scenario-pic
C:/Apps/anaconda3/python.exe -m uv pip install --python .venv-scenario-pic/Scripts/python.exe -r seed_gen/scenario_pilot/requirements-pic.freeze.txt

.venv-scenario-tcad/Scripts/python.exe -X utf8 seed_gen/scenario_pilot/verify_tcad.py
.venv-scenario-mesh/Scripts/python.exe -X utf8 seed_gen/scenario_pilot/verify_mesh.py
.venv-scenario-pic/Scripts/python.exe -X utf8 seed_gen/scenario_pilot/verify_pic.py
```

实跑结果变化后先查看报告并审阅研究记录，再刷新 profile 中绑定的证据哈希。源码或API变化必须重审选择，不可只更新哈希绕过校验：

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_scenario_pilot_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --check
C:/Apps/anaconda3/python.exe -X utf8 -m unittest tests.test_joint_scenario_selection
```

本轮13项回归测试覆盖完整来源漂移、截断索引即便重写哈希仍被重解析拒绝、互补选择、替代包泄漏、方法/构造裁剪、任务引用、基础设施绕过、返回callable、缺失文档、能力分组与分类继承。能力分组非语义完整性证明；这里的闭环判断还依赖研究和上述实跑证据。

## 全量前仍需确认的边界

- 本轮只处理3个试点，保留已有 `semiconductor_scenario_02_0916.json`；未将全部分类或旧场景批量替换。
- 六条任务是原生API脚本与固定verifier，没有Agent策略调用轨迹、正式工具JSON协议或可复用的reset/step服务。
- 未逐个执行全部199个参考操作。MOS/BJT/3D、真实热器件、多物理场、图形/文件长尾、梯度、继承长尾和跨平台运行都不在本次通过范围。
- TCAD依赖本机原生后端和扩展精度；其他机器须重新验证。Mesh是无量纲制造解，不是校准过的热器件数据。
- 待审阅实体粒度与任务设计后，再把规则推广到新的L1/L2；外部许可证、付费计算、仪器及数据库凭证场景需要另设计离线fixtures与真实运行边界。
