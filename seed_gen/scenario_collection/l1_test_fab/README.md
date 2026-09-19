# 05 晶圆测试与良率 / 06 Fab制造运营 执行记录

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

独占范围：分类清单中的 05（11 个 L3）及 06（30 个 L3），共 41 场景。02 和共享全局汇总不修改。2026-09-17 已完成41/41的调研、固定发布源码候选、联合工具筛选、82条固定任务两遍运行及种子生成；来源重新解析通过，最终41场景 `--check` 已通过（22:26终态 `Verified 41 scenario-first seeds.`）。

最终数量：05为110类/69函数/695类方法/764操作，06为399类/132函数/1637类方法/1769操作；合计509类/201函数/2332类方法/2533操作。操作数按每个场景独立环境累计，跨场景同API会重复计数，不是全项目唯一API数。

## 已验证首批

| 场景 | 主包/补充 | class | function | class_func | all_func | 已执行任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 06.05.01 Fab DES | SimPy | 27 | 0 | 50 | 50 | 2 |
| 06.05.03 Alternative DES | salabim | 6 | 0 | 70 | 70 | 2 |
| 06.05.04 Dispatch | Job Shop Lib + SimPy | 16 | 0 | 75 | 75 | 2 |
| 06.05.05 Constraint Scheduling | PyJobShop | 6 | 0 | 58 | 58 | 2 |
| 06.06.02 PM scheduling | PyJobShop + SimPy | 16 | 0 | 84 | 84 | 2 |
| 06.06.03 Part lifetime | reliability | 8 | 0 | 52 | 52 | 2 |
| 06.06.04 MTBF/MTTR | SimPy + reliability | 17 | 0 | 55 | 55 | 2 |

合计 444 个参考操作、14 条固定任务；每条运行两次，结果完全一致。不是 444 个工具全部运行验证，也不是已部署 Agent 环境。

实体包括 lot、equipment、run、part_history、lifetime_model；更新加工时间/资源日历/寿命数据后，旧排程/结果失效，reset 建新运行。后续工具服务器仍需实现身份存储、JSON 参数、状态权限、任务隔离。

真实桥接：Job Shop Lib `Schedule/ScheduledOperation` → SimPy 分钟事件；PyJobShop `ScheduledTask` → SimPy 单机资源回放；SimPy 故障/恢复日志 → reliability 完整运行区间拟合。固定断言包含容量错误/修复完成 4/5/7、SPT 穷举最优完成和 11、PM 窗口 3–5 时排程 0–3/5–7、右删失指数 MLE 3/100、MTBF 10/MTTR 2/可用率 5/6。

所有数据为人工小规模 fixture，无真实 MES、机台、PLC 或生产绩效结论。数值 oracle 使用独立解析公式、手算时间表或六种排列穷举，没有另藏第二套求解器。

## 包与来源

官方发布来源与完整索引详见 `production_sources.json` 和 `research/production_source_checks.json`。clone 位于 `seed_pypi_raw/l1_test_fab/`；完整索引位于 `seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_test_fab/`。

| 包 | 正式版本 | 完整候选 all_func | 备注 |
| --- | --- | ---: | --- |
| simpy | 4.1.2 | 67 | 官方 GitLab tag，与 PyPI 一致 |
| salabim | 26.0.1 | 793 | GitHub 最新 Release；PyPI 26.0.8，按指南取正式 Release |
| pyjobshop | v0.0.9 | 142 | OR-Tools 为硬依赖，不重复暴露低层求解器 |
| job-shop-lib | v1.7.0 | 316 | 要求 OR-Tools < 9.13 |
| reliability | v0.9.0 | 466 | 完整和右删失；未覆盖左/区间删失 |
| surpyval | v0.18.0 | 2622 | PyPI 0.19.0；本批作为重复生存分析候选排除 |

`research/package_discovery.json` 含 30 个包的身份线索，不能当作发布源码核查。`semiconductor-test-toolkit` 和 `pysemisecs` 的 PyPI 查询为 404；FactorySimPy 当前 PyPI 仅 0.1.0b3 预发布，不能冒称正式版，这些项仍待查证替代来源。

网页快照保留 URL、最终 URL、HTTP 状态、标题、正文与哈希；每场景 3 个已读有效来源。reliability 猜测的 `Working with censored data`、`Mean residual life`、`Repairable systems` 页为 404，保留失败记录并改查实际导航中的有效页面。salabim 文档各页标 23.1.0/26.0.0，与选定 26.0.1 有差异，具体接口按源码及运行核查。

## 可复现命令

从仓库根目录执行：

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_discovery
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_test_fab_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_test_fab/research/production_urls.json --output seed_gen/scenario_collection/l1_test_fab/research/production_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_test_fab/research/production_extra_urls.json --output seed_gen/scenario_collection/l1_test_fab/research/production_extra_web
C:/Apps/anaconda3/Scripts/uv.exe venv --python C:/Apps/anaconda3/python.exe .venv-scenario-fab-production
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-fab-production/Scripts/python.exe simpy==4.1.2 salabim==26.0.1 pyjobshop==0.0.9 job-shop-lib==1.7.0 reliability==0.9.0
.venv-scenario-fab-production/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_test_fab/verify_production.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_production_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_test_fab/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_test_fab --merged-name semiconductor_scenario_test_fab.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_test_fab/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_test_fab --merged-name semiconductor_scenario_test_fab.json --group-by-l1 --check
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-fab-production/Scripts/python.exe
```

安装 41 个包，`uv pip check` 通过。两个调度包共同约束选择 OR-Tools 9.12.4544，避免分别装最新依赖导致冲突。初版回放函数误写 SimPy `env.now()`，报 `TypeError: 'int' object is not callable`；与 salabim 的 `env.now()` 不同，SimPy 应读 `env.now` 属性。修正后 14 条任务两次通过。

最终 JSON 在 `seed_gen/pypi_outputs/scenario_collection/l1_test_fab/`；profiles/research/runtime 为审计和重生成材料，按主线程规则不计最终种子。

## FDC/SPC 第二批

新增 06.02.01–05（特征、变点、规则异常、在线漂移、分类）及 06.03.01–04（控制图、规则、能力、SPC/FDC关联），9 场景各 2 任务，重复运行两次一致。all_func 分别 64/65/78/49/65/56/56/56/70，合计 559；连同首批为 1003 操作、32 条任务。49 操作的在线漂移例外已说明，不为凑 50 加无需求检测器。

发布源：tsfresh v0.21.2、ruptures v1.1.10、sktime v1.1.0、ADTK v0.6.2、River0.26.1、spc-lib1.0.0、pyspc v0.4、sklearn1.9.1、SciPy v1.18.1、pandas v3.0.5。完整索引和版本证据在 `statistics_sources.json`/`research/statistics_source_checks.json`；sktime/pyspc在相关场景作为重复候选排除。pandas/scipy/ruptures源码允许07/08组只读复用，不能修改。

实跑覆盖：tsfresh↔pandas的wafer索引与单位、PELT惩罚/Dynp网格、ADTK Pipeline阈值和DatetimeIndex、River PageHinkley/ADWIN状态、tsfresh→sklearn训练列与标签、IMR基线/数组形状、规则sigma/编号、Cp/Cpk单位、SPC/特征一对一wafer连接。所有数据人工；分类四条测试全对不等于生产准确率，关联不证明因果。

spc-lib正式1.0.0有上游打包缺陷：`import spc_lib`在 `charts/variables.py` 的 `from src.spc_lib.core.base_chart ...` 报 `ModuleNotFoundError: No module named 'src'`。不修改发布源码，验证脚本显式把固定仓库根加sys.path，运行 `src.spc_lib`；来源spec设置module_prefix=`src`并重新解析，种子引用与真实namespace一致。该适配条件在每个SPC场景边界声明，不能声称pip后直接可用。该版本规则5表示三点中两点≥2sigma并标记整窗口，不能直接套用其他WE编号。

统计环境 `.venv-scenario-fab-statistics` 安装43包，依赖检查通过；ADTK在pandas3上的本批Threshold/IQR/Pipeline实际通过，但未推及所有旧接口。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_statistics_sources
C:/Apps/anaconda3/Scripts/uv.exe venv --python C:/Apps/anaconda3/python.exe .venv-scenario-fab-statistics
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-fab-statistics/Scripts/python.exe tsfresh==0.21.2 ruptures==1.1.10 river==0.26.1 adtk==0.6.2 spc-lib==1.0.0 scikit-learn==1.9.1 scipy==1.18.1 pandas==3.0.5
.venv-scenario-fab-statistics/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_test_fab/verify_statistics.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_statistics_profiles
```

网页采集与上节相同命令，将URL清单/输出名替换为 `statistics_urls.json`/`statistics_web` 及 `statistics_extra_urls.json`/`statistics_extra_web`。首次构建发现在线漂移49操作低于50，人工复核链完整后保留明确例外并重生成，未添加凑数API。

第二批与首批16场景已通过 `--check`，共1003操作/32任务。

## 晶圆测试第三批

05全部11个场景固定任务已两遍运行一致，新增764操作/22任务；与前16场景共27场景1767操作54任务。05的all_func按分类顺序为84/84/84/68/63/69/58/64/64/63/63。27场景构建、来源重解析及第二次 `--check` 均已通过。

固定数据覆盖STDF端序/截断、lot/wafer/die重测键、首测/末测良率、hard/soft bin、wafer坐标/图例、空间DBSCAN、人工center/edge/scratch分类、V/F Shmoo、规格guardband、PAT/site偏移、Welch/Fisher和跨域主键关联。每场景两条正常/错误/修复任务；类别图与数值图已视觉检查，矩阵/计数亦独立断言。SECOM只作为调研来源，没有下载或运行；没有WM811K训练/真实良率结论。

来源见 `ate_sources.json` 和 `research/ate_source_checks.json`：Semi-ATE-STDF正式tag0.1.33，安装其commit后元数据报告0.0.0（PyPI最新版0.1.28），因此以tag/commit识别实际源码；pystdf v1.4.0保留全量候选但作为重复解析器排除；wfmap1.0.3保留发布源码。wfmap使用旧版 `pivot(row,col,value)`，pandas3要求关键字参数，且类别replace结果保留object；验证脚本只用实例级WfFrame适配参数和数值矩阵dtype，无源文件改动/全局猴补丁。普通pandas3输入的TypeError实跑确认。STDF截断拒绝由独立record长度检查实现，不能宣称源解析器总会报错。上游test_PRR标记skip，字段仅参考，自己的字节样例另行验证。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_ate_sources
C:/Apps/anaconda3/Scripts/uv.exe venv --python C:/Apps/anaconda3/python.exe .venv-scenario-test-analysis
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-test-analysis/Scripts/python.exe 'git+https://github.com/Semi-ATE/STDF@5bbcbe76b522bb899fcc8d9d966d34b125f6516f' wfmap==1.0.3 pandas==3.0.5 scipy==1.18.1 scikit-learn==1.9.1
.venv-scenario-test-analysis/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_test_fab/verify_test_analysis.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_ate_profiles
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-test-analysis/Scripts/python.exe
```

测试环境26包依赖检查通过。研究网页使用 `ate_urls.json`/`ate_extra_urls.json`；PySTDF两个猜测README路径及wfmap/baseplot.py为404，保留失败证据，没有把404计为已读来源。

## 设备通信/控制/物流第四批

最后14个场景：06.01.01–05、06.04.01–04、06.05.02/06/07、06.06.01/05。28条任务各运行两遍通过，766参考操作；all_func依次52/49/59/47/67/47/47/56/55/50/51/50/71/65。四个47–49操作场景有明确数量下浮理由：配置、构造、运行、观察及重建主链完整，不补无关方法凑50。

来源正式发布：secsgem v0.3.0、asyncua v2.0.1、control0.10.2、do-mpc v5.1.2、OR-Tools v9.15（运行wheel9.15.6755）、Pyomo6.10.1。完整Python候选位于对应raw目录，OR-Tools解析整个ortools下Python源码，不冒称原生C++/构建期导出全部反射。Pyomo作为通用优化替代候选排除。来源/API全量索引和发布证据分别为 `control_sources.json`、`research/control_source_checks.json`。

FactorySimPy无GitHub正式Release，tags只有v0.1.0b3/b2/b1/a1，PyPI也是0.1.0b3；secsgem-driver无Release/tags，未建立PyPI1.0.0与稳定源码的对应。排除证据保留在 `research/control_web/index.json`、`research/rejected_control_leads.json`；不能把这两者计入正式版全量API池。制造组件/AMHS使用稳定SimPy及脚本中明确编写的source/buffer/processor/车辆/单路线模型，不声称包原生包含完整Fab模型。

实际验证范围：

- SECS/HSMS：真实编解码+本地socketpair字节传输，独立网络字节序头/长度oracle；不是全HSMS select/timeout/reconnect协商。
- GEM：真实离线报警set/clear状态，S6F11报告和S2F41/F42命令编解码。配方/online政策是明确工程模型，未调用外部设备；官方compliance文档指出处理状态和recipe管理等未实现，未宣称整体SEMI合规。
- OPC UA：实际localhost客户端/服务器读写Double2.5、错误String类型和不存在NodeId拒绝、browse path修复。NoSecurity只用于localhost，不验证真实PLC/证书/生产权限；订阅接口仅保留参考，未跑重连。
- 动态/反馈：一阶响应1-exp(-t/2)、ZOH极点exp(-.5)、反馈极点+1→-3、R2R递推1-.5^k由独立解析核验。
- MPC/MHE：真实CasADi3.8.1/IPOPT求解。标量MPC首步解析限幅0.5，逐步Simulator核对x_next=.8x+u；MHE常量状态2通过测量/先验权重错误修复。首次Simulator输入(1,)报 `AssertionError: u0 has incorrect shape. You have: (1,), expected: (1, 1)`，改列向量后通过。CasADi新NumPy模式FutureWarning为上游兼容提示，本任务使用其默认legacy模式且数值通过。
- 调度/物流：OR-Tools维护区间遗漏5→7最优makespan独立穷举；不可行域修复；SimPy机器容量2/1与运输秒/分钟错误对固定时间表验证。
- 报警/PdM：SECS置位/清除与PELT时间关联；River首次漂移→SimPy计划维护→reliability同单位SF，预设及时PM避免t150故障是人工政策而非产线收益结论。

```powershell
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_test_fab_control_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_test_fab/research/control_urls.json --output seed_gen/scenario_collection/l1_test_fab/research/control_web
C:/Apps/anaconda3/Scripts/uv.exe venv --python C:/Apps/anaconda3/python.exe .venv-scenario-fab-control
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-fab-control/Scripts/python.exe secsgem==0.3.0 asyncua==2.0.1 control==0.10.2 do-mpc==5.1.2 ortools==9.15.6755 simpy==4.1.2 river==0.26.1 ruptures==1.1.10 reliability==0.9.0 pandas==3.0.5
.venv-scenario-fab-control/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_test_fab/verify_control_integration.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_control_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_test_fab_environment_audit
```

控制环境48包依赖检查通过，与Job Shop Lib环境所需旧OR-Tools隔离。四环境依赖清单和平台版本在 `runtime/environments/`；这些是实际安装快照，非保证未来wheel相同的跨平台lock。浮点求解结果每条独立容差验收、两次报告按7位小数比较；离散字段精确比较。

41场景共2533参考操作/82固定任务，整组生成及 `--check` 已通过，分类清单与profiles/runtime各41份相等，无missing/extra。阶段资料/profiles/源克隆/运行图表不是最终种子；最终各L1及合并JSON由主线程提升。
