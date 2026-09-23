# 01、03–08 场景种子采集执行记录

用户要求保留已完成的 02，继续其他 L1。本批 **138 个 L3** 已全部生成并通过来源/静态/固定任务构建门槛，277条完整固定任务。2026-09-18最终全量源码重解析及重生成一致性检查实际输出`Verified 138 scenario-first seeds.`，退出码0；研究版本核查日期仍保留2026-09-17。机器可读状态以 [coverage.json](../pypi_outputs/scenario_collection/coverage.json) 为准；其完成字段代表种子与限定固定样例门槛，不代表完整工业后台或Agent环境已实现。

## 分类覆盖

| L1 | 原文L3数 | 本批已验证 | 待处理 |
| --- | ---: | ---: | ---: |
| 01 材料与器件研发 | 27 | 27 | 0 |
| 02 器件与物理仿真 | 22 | 原产物保留，不重跑 | — |
| 03 芯片设计与流片 | 24 | 24 | 0 |
| 04 实验室表征与设备控制 | 7 | 7 | 0 |
| 05 晶圆测试与良率 | 11 | 11 | 0 |
| 06 Fab 制造运营 | 30 | 30 | 0 |
| 07 Inspection / Defect / Metrology | 28 | 28 | 0 |
| 08 Enterprise / Quality / Reliability | 11 | 11 | 0 |

[inventory.json](inventory.json) 覆盖全部 160 个编号，保存原文字段和行号。20 条只有标题的自由列表已补充具体应用、任务及候选包线索，标记 `explicit_editorial_supplement`；后续来源与运行验证以对应种子为准，编辑补充本身不是库功能证据。其余来源表格的名称和应用字段保持原意。02的22个场景仍保留0916的包、工具、任务和验证口径；正式分片只做了`scenario-1.1`契约、唯一index和L1标签规范化。

## 已完成种子

| 场景 | 主包/补充包 | class | function | class_func | all_func | 固定任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 01.01.01 晶体结构构建与转换 | pymatgen-core + ASE；排除JARVIS重复状态 | 9 | 2 | 68 | 70 | 3 |
| 01.01.02 材料数据库检索 | JARVIS + pymatgen-core；显式本地固定缓存 | 6 | 5 | 46 | 51 | 2 |
| 01.01.03 材料特征与筛选 | matminer + pymatgen-core | 15 | 0 | 58 | 58 | 2 |
| 01.01.04 机器学习势 | CHGNet + pymatgen-core + ASE | 11 | 0 | 56 | 56 | 2 |
| 01.02.01 Space Group / 标准晶胞 | spglib + pymatgen-core；不重复暴露SpacegroupAnalyzer | 5 | 6 | 48 | 54 | 2 |
| 01.02.02 Brillouin Zone / k-path | seekpath + pymatgen-core；spglib仅作为硬依赖安装 | 5 | 4 | 38 | 42 | 2 |
| 01.03.01 材料工作流 | atomate2 + jobflow + pymatgen-core | 10 | 5 | 54 | 59 | 2 |
| 01.03.02 错误恢复 | custodian + pymatgen-core；排除atomate2重复封装 | 13 | 0 | 50 | 50 | 2 |
| 01.03.03 计算溯源 | AiiDA；排除pyiron重复项目状态 | 17 | 5 | 72 | 77 | 2 |
| 01.03.04 多后台计算 | pyiron-workflow-atomistics + ASE | 12 | 4 | 37 | 41 | 2 |
| 01.04.01 缺陷枚举 | pymatgen-analysis-defects + pymatgen-core；排除doped重复生成 | 14 | 0 | 60 | 60 | 2 |
| 01.04.02 缺陷重构 | ShakeNBreak + doped + pymatgen-core | 9 | 9 | 44 | 53 | 2 |
| 01.04.03 形成能/电荷转变 | doped + pymatgen-core；PAD只补实际继承属性 | 11 | 0 | 51 | 51 | 2 |
| 01.04.04 费米能/浓度 | py-sc-fermi 3.0.0；排除doped浓度包装 | 6 | 0 | 57 | 57 | 2 |
| 01.05.01 谐波声子 | phonopy；排除phono3py非谐能力 | 6 | 1 | 55 | 56 | 2 |
| 01.05.02 非谐热输运 | phono3py + phonopy结构；排除hiPhive拟合 | 4 | 1 | 57 | 58 | 2 |
| 01.05.03 原子扩散 | pymatgen-analysis-diffusion + pymatgen-core | 8 | 6 | 53 | 59 | 2 |
| 01.06.01 能带/DOS | Sumo + pymatgen-core；排除PyProcar重复/冲突 | 9 | 8 | 54 | 62 | 2 |
| 01.06.02 有效质量 | effmass + ASE数据桥 | 10 | 8 | 47 | 55 | 2 |
| 01.06.03 材料参数 | openbandparams | 11 | 0 | 77 | 77 | 2 |
| 01.07.01 DFT/电子结构 | ASE + GPAW | 3 | 4 | 48 | 52 | 2 |
| 01.07.02 ABINIT工作流 | AbiPy | 9 | 0 | 93 | 93 | 2 |
| 01.08.01 多带k·p | kdotpy | 6 | 5 | 47 | 52 | 2 |
| 01.08.02 紧束缚建模 | PythTB；排除TBmodels/pybinding重复实现 | 3 | 1 | 60 | 61 | 2 |
| 01.09.01 静态量子输运 | Kwant | 16 | 2 | 57 | 59 | 2 |
| 01.09.02 含时量子输运 | tkwant + Kwant | 14 | 4 | 45 | 49 | 2 |
| 01.09.03 Berry/拓扑 | Z2Pack + TBmodels；排除WannierBerri重复 | 9 | 6 | 31 | 37 | 2 |
| 03.08.01 波形分析 | vcdvcd + pyvcd | 7 | 1 | 37 | 38 | 2 |

除上表材料27个与波形1个场景外，已汇总设计23个、实验室7个、晶圆测试11个、Fab30个、可靠性/质量11个及计量28个；逐场景统计见各子组README和主coverage。共 **1,465个类、664个顶层函数、7,402个类方法、8,066个参考操作、277条完整任务**（跨场景累计，可能包含重复API）。`all_func=function+class_func`，类容器不另加。每场景13–93操作，低于50的条目记录完整性优先的明确理由；其中PDK版本/安装管理仅13操作，不为凑数纳入不相关功能。每场景3–6个成功读取且内部去重的参考页面。类数和构造/属性沿用全量来源统计；这不是已实现的Agent JSON动作数。

正式结果集中在 [final_results](../pypi_outputs/final_results)：`semiconductor_scenario_collection.json`汇总160场景，`semiconductor_scenario_01.json`至`_08.json`为8份L1分组，单个场景集中在`l3/`。02来自既有0916快照，文件已改名为`semiconductor_scenario_02.json`，内容及验证口径不变；其22项不计入本批138项新验证结果。逐场景审计仍保存在本地`../pypi_outputs/scenario_collection/reports/`，该目录不上传。源码空说明保留且单列；不修改全局种子契约。目录与清理规则见 [产物说明](../pypi_outputs/final_results/README.md)。

## 发布版与来源

来源清单为 [materials_structure_sources.json](materials_structure_sources.json)、[materials_workflow_physics_sources.json](materials_workflow_physics_sources.json)、[materials_anharmonic_sources.json](materials_anharmonic_sources.json)、[materials_electronic_sources.json](materials_electronic_sources.json) 和 [materials_backend_sources.json](materials_backend_sources.json)，完整候选池在 `../pypi_outputs/ori_all_funcs/scenario_collection/`。子组版本记录在各自README。旧全量索引、旧包选集保留在本地；02快照现位于`../pypi_outputs/final_results/semiconductor_scenario_02.json`。

| 包 | 固定正式发布标签 | 完整提交 |
| --- | --- | --- |
| pymatgen-core | v2026.8.30 | 73af4e53f5f24e1dcf11e0d94ca13be10ea956ad |
| ASE | 3.29.0 | f27c0005ae6a67ea419f996e728668865bfc1f86 |
| spglib | v2.7.0 | 12355c77fb7c505a55f52cae36341d73b781a065 |
| JARVIS-Tools | v2026.4.2 | add61f2ad1f7020d23ffc2a815f604071ac03c86 |
| matminer | v0.10.1 | 25cd8f2778d73e54f80b38ee1983a28643368118 |
| seekpath | v2.2.1 | 7b18ca9ad1038e4380d605b2d6a94f01a2b96ce7 |
| atomate2 | v0.1.5 | 0b61cf6365c8cfb5e74792f48d20b37dbb2916a6 |
| jobflow | v0.3.1 | 3c10cd96401d90ea18ca1e80ab2c4a859050472a |
| custodian | v2025.12.14 | 4591934b5bc4ae90a11037c3f95f1eb2023ca571 |
| phonopy | v4.5.0 | 6683c731a54b3aed2a739138a54138a6b2782013 |
| phono3py | v4.5.0 | 21fa8f3817fbcc603254656f525bb5aec113afb6 |
| py-sc-fermi | 3.0.0 | cc2abcabd0ceb75136a4fcacc558337bc3df6721 |
| doped | 3.2.1 | 93c8b99b70b7a4e59b46da4870f23b885d0a6991 |
| pymatgen-analysis-defects | v2026.3.20 | 12c1f80562e69e285073eafdce58281fe0c67618 |
| ShakeNBreak | v3.4.4 | 832979e22aa15edc869427b77c3730c2a634b7ca |
| hiPhive | 1.5 | 1c4dbc710081e9128e657ffa2d64f67abe71d078 |
| pymatgen-analysis-diffusion | v2025.11.14 | 6f9fe6a7df1f66074a8c5407646aa17a3eb7d4a2 |
| Sumo | v3.0.0 | 00b907454bb0df49049f4b21f6b6aed7cb8b70a5 |
| PyProcar（候选排除） | v6.5.0 | 4a2ec9049af78fdd35b6214eef68fe40e5f356ed |
| effmass | v2.3.0 | e6c209928d6660b3c43ca27f5d0ac9cbc740f2b3 |
| openbandparams | v1.0 | e115fccda355e344d5913fcd4f92c3cc2964c14e |

JARVIS和seekpath新克隆；其他目录复用。隐藏目录 `seed_pypi_raw/pymatgen-core` 的tag/HEAD/remote和clean状态已核对，原先普通PowerShell列表未显示该目录；克隆尝试报 `destination path ... already exists` 后查明并直接复用，未覆盖。core/ASE/spglib/matminer的新旧全量工具对象逐对象一致，新索引补上本批正式来源元数据。

JARVIS当前PyPI主页跳转至 `atomgptlab/jarvis`，旧 `usnistgov/jarvis` 不作为当前发布身份。核查时PyPI版本为2026.6.12，GitHub最新非预发布Release为v2026.4.2；依指南采用后者并保留差异。JARVIS只进入第一场景候选审计，未选入运行依赖。

`research/materials_package_discovery.json` 已记录01候选的41个PyPI身份/版本线索；其中尚未逐个核对官方Release、克隆和解析的仍标记 `identity_and_official_release_review_pending`。该发现表不能证明这些包已完成采集。

失败来源保留在 `research/*web/index.json`：ASE旧文档链接404；matminer主站和旧GitHub Pages重定向后均403。matminer改用成功读取的官方v0.10.1文档源码页面。pymatgen在线文档标注2026.7.27，但签名和工具对象始终取core v2026.8.30源码。成功网页正文保存为带哈希的快照，构建时核对。

## 实跑证据

隔离环境 `.venv-scenario-materials` 使用Python3.12.4，由conda base的uv创建和安装。后续添加matminer时pandas由3.0.5调整为其兼容的2.3.3，并安装pymatgen2026.5.4；核心三个包版本保持固定，原结构任务已重新执行通过。`uv pip check` 检查48个安装包通过；完整依赖在 [requirements-materials.freeze.txt](requirements-materials.freeze.txt)。

1. GaAs常规胞8原子变64原子超胞，体积1445.196640616 Å³，正确掺杂Ga31Al1As32。错误替换As得到Ga32Al1As31被同一组成oracle拒绝。ASE extxyz往返最大分数坐标误差1.11e-16；JSON恢复通过。
2. GaAs/AlAs共格几何界面32原子，实际z间隙2 Å；错误0.2 Å间隙被拒绝并修复。验证分组元素计数，未声称界面能量稳定。
3. 对称标准化8→2原子，原胞体积45.16239501925 Å³，空间群216。单原子偏移后空间群1；恢复坐标后回到216，symprec始终1e-5，未放宽容差。
4. GaAs/AlAs/Si的Z均值/最小值/最大值分别为[32,31,33]/[23,13,33]/[14,14,14]，批处理与单条一致。错误Ga2As均值31.6667被固定GaAs目标32拒绝；修复后通过。GaAs/Ga2As2分数特征相同，p2计量特征为sqrt(0.5)。初次ElementProperty默认NaN填充发出警告，随后显式禁用填充并重跑通过。
5. Si金刚石路径空间群227；核验ABᵀ=2πI、|X|=2π/a和|L|=sqrt(3)π/a。粗采样最大步长1.227089745 Å⁻¹失败，修复后252点、最大步长0.021527890 Å⁻¹，通过固定0.05阈值。分段处理避免把不连续路径的跳跃当作采样间距。

具体报告：`runtime/materials_structure/01.01.01.json`、`01.02.01.json`，`runtime/materials_features_path/01.01.03.json`、`01.02.02.json`。结构文件、特征CSV和k点NPZ保存在对应运行目录。

```powershell
.venv-scenario-materials/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_structure.py
.venv-scenario-materials/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_features_path.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_structure_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_features_path_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/profiles --output-dir seed_gen/pypi_outputs/final_results --scene-dir seed_gen/pypi_outputs/final_results/l3 --reports-dir seed_gen/pypi_outputs/scenario_collection/reports --include-seeds seed_gen/pypi_outputs/final_results/semiconductor_scenario_02.json --merged-name semiconductor_scenario_collection.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/profiles --output-dir seed_gen/pypi_outputs/final_results --scene-dir seed_gen/pypi_outputs/final_results/l3 --reports-dir seed_gen/pypi_outputs/scenario_collection/reports --include-seeds seed_gen/pypi_outputs/final_results/semiconductor_scenario_02.json --merged-name semiconductor_scenario_collection.json --group-by-l1 --check
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.report_scenario_collection
C:/Apps/anaconda3/python.exe -X utf8 -m unittest tests.test_scenario_inventory tests.test_joint_scenario_selection
```

19项回归测试通过，涵盖全编号覆盖、自由列表显式补充、缺失应用不能静默跳过、规范化文本偏离原文、候选哈希漂移、截断全量池、类方法/构造裁剪、排除包泄漏与任务引用等。运行报告发生变化时应先复核，再刷新profile和重生成，不能仅更新哈希绕过检查。

## 工作流、缺陷与声子批次

新增8个场景、444个参考操作和16条完整任务。新增 profile 的显式选择与说明在 `prepare_materials_workflow_physics_profiles.py`、`prepare_materials_defect_profiles.py`、`prepare_materials_anharmonic_profile.py`；共同序列化逻辑为 `scenario_collection_support.py`。没有调用LLM评分，不按包内排名截断候选。

三个独立环境的 `uv pip check` 均通过：`.venv-scenario-workflow` 88个包、`.venv-scenario-phonons` 20个包、`.venv-scenario-defects` 101个包。依赖快照分别为 `requirements-workflow.freeze.txt`、`requirements-phonons.freeze.txt`、`requirements-defects.freeze.txt`。atomate2的phonons额外依赖要求phonopy<4，因此工作流与phonopy4.5单独安装，不宣称可以直接混装。

版本核查的差异：py-sc-fermi GitHub最新稳定Release已是3.0.0（2026-09-11），PyPI仍是2.2.2；另克隆到 `seed_pypi_raw/py-sc-fermi_3.0.0` 并从源码安装，旧目录保留。3.0的DefectSpecies使用电荷态序列，DefectSystem是固定温度快照，result按每晶胞存浓度并用1e24/volume提供cm^-3视图。phonopy/phono3py官方`releases/latest`为404，依据PyPI4.5.0与官方远程同名标签固定版本，发布时间明确来自PyPI。hiPhive官方身份是GitLab，最新Release1.5与PyPI一致，完整候选245个操作；404的接口教程仍保留失败证据，不计有效来源。

| 验证脚本 | 已通过的固定判据 | 未验证边界 |
| --- | --- | --- |
| `verify_materials_workflow.py` | 5个作业名/先驱依赖及ENCUT200→520；反序Flow得到2条存储和目标2；SCF回放Fast→Normal，2次尝试1次修复，预算1正确拒绝 | 无VASP、POTCAR、能带/总能计算；回放不能证明物理SCF收敛 |
| `verify_materials_defects.py` | GaAs两类空位各等价数4；Ga空位Ga31As32，Al替位Ga31Al1As32，H间隙Ga32As32H，碰撞点被拒绝；形成能1.5及0.5+EF，转变1 eV | 间隙坐标显式给定；形成能输入为人工总能，未计算FNV/eFNV或稳定化学势区域 |
| `verify_materials_reconstruction.py` | 四邻距2.447820804→1.958256643 Å，原结构不变；种子23扰动可重放；meV/eV修复后已给候选表最低能降低0.8 eV | 没有执行电子结构弛豫；候选表最小值不是全局物理基态 |
| `verify_materials_fermi.py` | 本征400/800 K的EF=1 eV且n=p；体积10→100 Å³恢复n≈1e18 cm^-3；电中性误差1.28e-15，独立Fermi积分与保存恢复通过 | 对称模型DOS，不是某一真实材料DFT结果 |
| `verify_materials_phonon.py` | 弹簧链频率0/5.908832865/8.356351576 THz，DOS积分3；力常数符号错误产生虚频、修复恢复；YAML往返通过 | Si为标签，质量指定28 u，不是硅的第一性原理声子谱 |
| `verify_materials_anharmonic.py` | 官方Si-PBEsol力fixture实算：3³网格10.934，修为9³后107.716；同位素散射97.139 W/(m K)，均符合发布测试±0.5与立方张量对称 | 未运行新DFT或hiPhive拟合；9³是回归目标，不是已证实网格收敛 |

运行报告和生成文件位于 `runtime/materials_workflow/`、`materials_defects/`、`materials_reconstruction/`、`materials_fermi/`、`materials_phonon/`、`materials_anharmonic/`。每个场景的源网址、正文哈希、任务步骤、单位、状态失效和包去留均进入JSON。

处理过的实际错误：

- jobflow执行会原地解析OutputReference，运行后断言`child.input_uuids`失败。依赖关系改在执行前检查，执行后检查真实存储/输出。
- py-sc-fermi独立积分最初从E=2截断，漏掉DOS边界的半个梯形，误差约9.6%。保留 `runtime/materials_fermi/attempt_01_integration_failure.json`；将零DOS禁带点纳入积分，固定容差未放宽。
- doped接口是`return_sites=True`且返回三项；混用PAD的`return_site`报`unexpected keyword argument 'return_site'`，已根据发布源码修复桥接后实跑通过。
- `doped latest`文档包含3.2.1没有的`plot_transition_levels`，未选入；phono3py在线页标4.4.0，方法仍以4.5.0源码为准。
- hiPhive来源提取首次缺少历史字段`github_prerelease`而失败；GitLab来源填null，不冒称GitHub Release，重新全量提取通过。
- PowerShell转换core JSON时因`P`与`p`同名大小写键报错，改用Python标准JSON读取，不改数据。

```powershell
.venv-scenario-workflow/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_workflow.py
.venv-scenario-defects/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_defects.py
.venv-scenario-defects/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_reconstruction.py
.venv-scenario-defects/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_fermi.py
.venv-scenario-phonons/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_phonon.py
$env:OMP_NUM_THREADS='2'
.venv-scenario-phonons/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_anharmonic.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_workflow_physics_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_defect_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_anharmonic_profile
```

随后执行上一节构建、`--check`和覆盖统计命令。源码重新解析一致、选集/任务引用/统计检查以及原3场景试点`--check`已通过；不等同于所有参考API都实跑，也没有运行外部CI。

## 扩散与电子后处理批次

`verify_materials_electronic.py` 的四场景八任务均实际通过；`prepare_materials_electronic_profiles.py` 联合筛选新增253个参考操作。环境 `.venv-scenario-electronic` 的63个依赖兼容检查通过，依赖快照见 `requirements-electronic.freeze.txt`。

| 场景 | 固定任务实跑结果 | 边界 |
| --- | --- | --- |
| 01.05.03 | step_skip=1得到D=1e-4，修成5后D及三分量均2e-5 cm²/s；框架漂移扣除、MSD=6Dt与恢复通过；摄氏输入失败，K温度恢复Ea=.3eV、D0=.01，400K外推1.66022e-6 | 制造sqrt(t)轨迹与Arrhenius数据；无MD/NEB |
| 01.06.01 | 分段解析能带错误EF判金属，修复后间接隙1.2/直接隙1.7eV；官方Cs2SnBr6 SOC DOS的2000点、单自旋、投影聚合和E-EF导出通过 | 官方预计算XML与解析能带，无新DFT/应变预测 |
| 01.06.02 | ASE→DataASE桥接，质量0.1999993/0.3999986/-0.4999983 m0；四次项宽窗口0.099822失败，窄窗口0.198012通过固定2%目标 | 负值为价带曲率；无真实材料DFT或一般拟合收敛证明 |
| 01.06.03 | GaAs300K带隙1.422482142857eV符合Varshni式；800K晶格匹配从错误偏差.0161577Å修复，Ga分数.4716414173；超范围拒绝及JSON恢复通过 | 经验表/插值；未运行TCAD、k·p或全部应变接口 |

实际版本/接口问题与处理：

- diffusion GitHub正式版2025.11.14而PyPI2025.11.15，仓库重定向到materialyzeai；effmass GitHub2.3.0而PyPI2.3.1。初次安装 `effmass==2.3.0` 无可用PyPI分发，改从核对后的完整Git提交安装。没有改用更新的未审查版本。
- PyProcar6.5要求NumPy<2，Sumo3要求NumPy>=2，故前者在能带/DOS场景排除。Windows源文件plotBands.py/plotbands.py为同一个Git blob `97d24d2617cd6151a4faee218ff3c02690974fbb`；提取器验证blob及工作区字节后补齐第二模块身份，未改源码。
- effmass2.3把零能量极值当作False，初始VBM=0时 `ValueError: max() iterable argument is empty`。共同平移能量/EF -1eV后桥接通过，质量目标不变。初始窄窗口k≤.02Å⁻¹仍有3.86%误差，进一步细化采样、缩到.01Å⁻¹后通过原定2%，未放宽容差。
- openbandparams总Eg没有直接引用，首次引用非空断言失败；查源码后改为如实保留总Eg空值、追踪Gamma依赖文献。预置材料是数据对象，组分匹配真实调用类的`__call__`；新增按包显式开启的提取选项，全量165个操作，其他包默认边界不变。
- 三份失败页面/错误尝试继续保留在research/runtime。GitHub notebook页面只返回导航时不算正文证据；改用成功读取的发布源码/测试及教程。

```powershell
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-electronic/Scripts/python.exe sumo==3.0.0 openbandparams==1.0 pymatgen-core==2026.8.30 ase==3.29.0 git+https://github.com/lucydot/effmass.git@e6c209928d6660b3c43ca27f5d0ac9cbc740f2b3 git+https://github.com/materialsvirtuallab/pymatgen-analysis-diffusion.git@6f9fe6a7df1f66074a8c5407646aa17a3eb7d4a2
.venv-scenario-electronic/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_electronic.py
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_materials_electronic_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_electronic_profiles
```

随后运行前述统一构建/`--check`/覆盖统计。当前17场景与原3场景试点重生成检查通过。33项相关回归通过，含调用协议显式启用、大小写源别名与UTF-8 BOM；BOM问题由并行QCoDeS采集暴露，解码修复不改变原始字节哈希。

## 按L1并行与统一提升

2026-09-17用户明确授权三个subagent：`l1_design_lab`负责03/04，`l1_test_fab`负责05/06，`l1_metrology_quality`负责07/08；主代理负责01及验收汇总。各组独占sources/research/profiles/runtime、输出子目录和虚拟环境；`Collection(base=...,raw=...)`复用主分类清单，不互相写索引。

03.08.01已复核并提升：正常/错误/修复VCD由真实pyvcd生成并由vcdvcd读取；30ns首处分歧和1us→1ns时间单位修复通过，未执行RTL或GUI。详情见 [l1_design_lab/README.md](l1_design_lab/README.md)。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.promote_scenario_collections --lane l1_design_lab --scene 03.08.01
```

提升前重解析源代码、核对profile/网页/运行报告哈希、包去留/任务引用，并确认子组输出与重新生成完全一致；全部复核后才把原样profile复制到主profiles，再运行主构建/检查/覆盖统计。研究中或尚未生成子组最终输出的profile不能进入完成统计。中间产物按用户要求加入`.gitignore`，最终JSON、脚本、来源/URL配置及文档保留。

各组详细任务、版本、错误和边界见 [设计/实验室](l1_design_lab/README.md)、[测试/Fab](l1_test_fab/README.md)、[计量/质量](l1_metrology_quality/README.md)。当前已进主汇总的可靠性和调度任务均以独立手算/枚举目标验收；实验室使用实际软件接口加虚拟DUT/官方设备模拟；椭偏包含独立Fresnel、参数反演及严格不相交保留谱验证，均不声称真实硬件或产线准确率。

## 计算溯源与多后台批次

AiiDA 2.9.2固定 `0d71dc732350784d2a18f04268f2d42a1a4ef163`，本地SqliteTempBackend运行真实calcfunction/workfunction及来源查询。任务手算目标 `-10/4+.25=-2.25 eV/atom`：成功但数值错误的247.5被拒绝；count=0产生真实excepted子计算，QueryBuilder反查后修复重跑，失败历史仍在；归档组保留6节点。临时数据库退出销毁，UUID只作本次证据。没有运行DFT、RabbitMQ、HPC或checkpoint续跑。

pyiron-workflow-atomistics 0.2.1固定 `b7484cb94064eb9cf1cac8674789c71b61a22d17`，依赖强制pyiron-workflow0.19.0；最新候选0.20.0排除，不混用API。pyiron-atomistics0.8.12、pyiron-base0.15.19完整候选已核查，因重复状态/依赖成本排除。本地ASEEngine切换LJ/Morse，Ar2在1.3Å时能量分别-.32576873636/-.42259093913eV，与独立公式误差<1e-12；错误epsilon500导致1000倍偏差，修复.5通过。零预算不收敛，100步预算修复后距离1.1224620482948964Å、fmax4.14e-10eV/Å，通过解析极小点与文件恢复。两次运行指标一致；无外部DFT、LAMMPS、MD或真实半导体势校准。

```powershell
.venv-scenario-provenance/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_provenance.py
.venv-scenario-backend/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_backend.py
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_materials_backend_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_provenance_profile
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_backend_profile
```

独立provenance环境78包、backend环境74包依赖检查通过，依赖快照由report脚本保存。第一次裸导入AiiDA自动建立个人`.aiida`空子目录；清理动作被自动审批以`blocked by policy`拒绝，未删除或重试。正式脚本在导入前设置项目内AIIDA_PATH。ASE旧文档404继续保留，来源改为成功读取的固定发布代码和完整官方案例；数据类自动构造和ASE私有基类继承缺口明确记入边界，未伪造函数。

## GDSTK 原生来源适配

布局分支的GDSTK1.0.1包含C++原生方法，普通AST不够。新增 `scripts/extract_gdstk_refs.py`，manifest使用`adapter="gdstk"`；按实际PyTypeObject/PyMethodDef导出表绑定PyDoc_STRVAR，与发布stub合并。得到12类、21函数、174方法、195操作，81数据属性仅作为状态元数据保留。Cell.remap/Library.remap不在stub，使用原生文档显式签名；注释掉的Repetition.copy不导出。共享get_property文档按C++绑定定位，不按名字猜；没有Returns段的output仍为空。35项相关回归通过，未编译或导入GDSTK作为来源证据。

共享Python提取器现按源码正文、模块名和调用协议选项复用进程内解析结果，返回独立深拷贝；每次仍重新读文件，并照常核对Git发布引用、源码列表和字节哈希。新增内容变化、模块变化及消费者修改隔离回归后，36项相关测试通过。缓存不绕过来源验证，未在检查结束前提前计入后续批次。

## 本地材料数据库固定样例

`verify_materials_database.py` 已两次实际通过，使用JARVIS v2026.4.2和pymatgen-core2026.8.30，44包兼容。Figshare两个下载域名及文章API均403，失败证据在`research/materials_database_web/`；任务使用独立 `synthetic_material_query_v1.json.zip` 与fixture-* ID，明确不是官方数据库副本。原生ZIP缓存loader在禁止网络请求条件下成功加载5记录；50meV/atom被误当50eV引入003，修成.05后精确选001/004，缺失带隙排除、重复ID拒绝、配方哈希往返一致。JARVIS→pymatgen的错误cartesian标志导致距离.4330127Å，修复后2.351258971274751Å、体积40.02575175Å³、Si2组成及JSON恢复通过。人工带隙/凸包能只检验流程逻辑，不等同于材料预测。

```powershell
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-database/Scripts/python.exe git+https://github.com/atomgptlab/jarvis.git@add61f2ad1f7020d23ffc2a815f604071ac03c86 pymatgen-core==2026.8.30
.venv-scenario-database/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_database.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_database_profile
```

该场景已进入59场景主汇总，单独及统一构建的源码重解析均通过（6类、5函数、46方法、51操作）。

## 机器学习势与量子模型批次

这批采用独立环境和固定小输入，每个场景两条任务、重复运行两次。包/文档版本差异和失败页面均记录在研究文件；下表的模型结论仅适用于各自样例。

| 场景 | 实际包与固定结果 | 边界 |
| --- | --- | --- |
| 01.01.04 机器学习势 | CHGNet0.4.2发布自带0.3.0模型；2原子Si扰动，力-.913656与独立总能差分-.909138eV/Å符合.01容差；把每原子能当总能会失败；FIRE修复预算后fmax=.008373 | CPU、模型SHA256固定；未证明真实材料误差或运行MatGL/MACE |
| 01.07.01 DFT | ASE/GPAW26.7.0真实WSL Si2 SCF与GPW，pulay混合；100eV/Gamma/LDA | 低成本回归，不是平面波/网格收敛或实验精度；完整记录见materials_solver/README |
| 01.07.02 ABINIT工作流 | AbiPy1.0.0真实输入生成及发布自带ABINIT8.0.6 Si GSR，能量-241.2364703eV | 读取既有结果，未新跑ABINIT；tag/source1.0与pyproject0.9.8差异保留 |
| 01.08.01 k·p | kdotpy1.4.1实际8带CdTe Gamma谱，0K带隙1606meV；300K错误值1528.762081；120维HgTe量子阱均匀20meV势使8子带精确平移20 | 2/3/2nm层栈、.5nm粗网格；不声称III-V、应变、Hartree或实验拟合 |
| 01.08.02 紧束缚 | PythTB2.0.2实际SSH，周期隙1；8胞开放端态±.0029299446，端点权重.7501173；独立16×16矩阵/残差/配置恢复 | 无DFT或真实材料标定；发布源码__version__仍2.0.0差异保留 |
| 01.09.03 拓扑 | TBmodels1.4.3→Z2Pack2.2.1两带模型，质量3的C≈0不能满足目标，改-1后C=-1；独立Dirac质量公式及31×31plaquette一致；HDF5恢复通过 | 合成模型；m=0闭隙不接受不变量；未验证时间反演Z2或Wannier/DFT |

CHGNet模型文件SHA256为`d14ab7c0f093efe64b60a7bcd540bca10e74fb7f46c86108a079af60524659d1`，安装文件与发布源码相同。kdotpy材料显式加载发布表并代入T，避免`initialize_config`写个人目录；漏传T的初次尝试触发上游`AstParameter.get_undefined_variables`错误，未改上游源码。简并本征矢允许换基，只比较谱和残差。Z2Pack没有2.2.1同名tag，PyPI发布全部Python文件与`a82c83afbdac8e43a593c22c06de19cfe752d354`逐字节一致；非法Windows测试路径通过稀疏checkout排除，实际源码保持clean。

```powershell
.venv-scenario-mlpot/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_mlpot.py
.venv-scenario-tightbinding/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_tightbinding.py
.venv-scenario-kdotpy/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_kdotpy.py
.venv-scenario-topology/Scripts/python.exe -X utf8 seed_gen/scenario_collection/verify_materials_topology.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_ml_tb_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_kp_topology_profiles
```

## 静态与含时量子输运

`verify_materials_transport.py`四条任务均两次实际通过。Kwant v1.5.0固定`de315f171270d62ee2412c4084260c912cc4d58f`；tkwant最新官方v1.1.1固定`b2040b88d56b90288f1b6617e7f2070ec1db7ef5`，内部version仍1.1.0。WSL从该完整commit原生编译，运行时核验direct_url，不用conda旧rc版本代替。独立prefix为`/home/zjs32/.local/share/semiconductor-transport-20260917/env`。python-mumps0.0.6声明NumPy>=2，锁定0.0.4后与NumPy1.26.4/SciPy1.13.1共同通过`pip check`。

| 任务 | 错误/修复与独立目标 |
| --- | --- |
| 势垒与电导 | 15格点链，U=2000的T(0)≈1e-6；修复U=2后T(0)=.5，13能点满足T=(4-E²)/(4-E²+U²)，最大误差7.8e-16；S†S、通道守恒、-2cos(k)色散与JSON重建通过 |
| 键与局域流 | 中央hopping=-.25的T(0)=.221453；修成-1后14键电流均1，与独立2Im(psi_i*Hij psi_j)误差<1e-12，密度/源项通过 |
| 脉冲 | 两位点h(t)=-(1+A sin t)，A=.3不能满足.7目标，最大概率误差.4683；修复后sin²[t+.7(1-cos t)]与概率流误差分别3.8e-10/1.1e-9；反向时间拒绝、保存重放通过 |
| 开放边界 | 7格点中心delta初态；闭链误差.7113，automatic_boundary在tmax6下仅6缓冲格、密度误差.00225354；refl_max从1e-6收紧到1e-12无改善。改显式32格SimpleBoundary后对无限链i^nJ_n(2t)的密度/流误差2.9e-9/3.1e-9，原3e-6容差未改；中心区剩余概率.1927147 |

这是单粒子、跃迁和hbar均为1的模型，不是含费米海、相互作用或材料标定的器件。自动边界返回成功不能替代实际误差检查，中央区域概率流出也不能被误判为不守恒。参考add_voltage/ScatteringStates/多项式吸收等尚未全部实跑。49个瞬态参考操作覆盖当前闭环，未加入多体求解凑数；静态59个。

```powershell
wsl --cd /mnt/d/Desktop/agent-world-mini /home/zjs32/.local/share/semiconductor-transport-20260917/bin/micromamba run --prefix /home/zjs32/.local/share/semiconductor-transport-20260917/env python seed_gen/scenario_collection/verify_materials_transport.py
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_materials_transport_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_transport_profiles
```

`extract_transport_cython_refs.py`根据发布pyx真实Python可调用声明补充def/cpdef/property及明确导出继承，保留Cython签名/来源；不将C-only函数或__cinit__伪造成Python工具。Kwant算子__call__/act/bind及hamiltonian_submatrix实际运行验证。21项提取回归通过；全量Kwant641与tkwant373操作候选都重新从源码核对。首次Cython数组类型`ndim=1`误拆默认值的问题已修复并有回归。输运pip和conda精确环境清单由报告脚本保存。

## 最后设计/光刻批次与验证边界

三个worker按03/04、05/06、07/08起始分工，完成后转去支援独占的DFT、analog_bridge和lithography目录，始终最多三个worker并行。各组来源/构建/`--check`通过后，主线程串行复核并提升。复用配置和全部来源经过统一再解析；没有调用LLM评分。

- [设计/实验室](l1_design_lab/README.md)：25场景1491操作50任务；包括真实Icarus/cocotb/pyuvm/AXI、FuseSoC/Edalize、版图读写和电学连接。03.09.01仅Slang前端调度/参数传递与独立Icarus验证，未跑综合/CTS/布线/GDS；03.11.02仅ALIGN真实拓扑和mock PDK原语/DRC/GDS子链，未做完整模拟器件自动版图或代工厂签核。laygo2同版本PyPI代码与tag不同导致bbox错误，运行改用固定发布源码。
- [模拟桥接](analog_bridge/README.md)：2场景122操作4任务。HDL21+KLayout+VLSIRtools真实GDS→电阻提取→ngspice42：1k/2k分压2/3V，布局长度误设得到1.5k/.6V，修复后回到目标。skillbridge1.8.0只验证真实客户端协议编码/错误响应/先check后save，手写返回值不当作Cadence执行，未实现BAG完整生成器。
- [光刻](lithography/README.md)：4场景221操作8任务，每条重复两次。正式prysm0.21.1/GDSTK1.0.1/SciPy1.18.1/TorchOptics1.0.2用于标量相干小网格闭环；可微方向差分误差6.59e-11，过程窗口优化残差3.25e-12。原lithosim/OpenILT/TorchLitho仓库无正式发布，只作为明确排除快照；lithosim无界退出条件和TorchLitho2自定义VJP约31%差分错误均保留研究证据。未验证工业光刻胶、二值可制造性、一般Hopkins或全片规模。

最终检查包括138编号与分类清单一一对应、277任务引用/运行报告、每场景来源去重、nums重算和原02哈希。21项提取器回归与20项联合筛选/分类/来源别名回归通过，`git diff --check`通过。最终源码重建一致性状态见文首；没有运行外部CI。

当前结果是参考种子和固定脚本，尚未实现稳定实体ID、JSON工具参数、reset/step、状态失效、隔离和verifier接口的Agent服务器。固定任务通过不代表全部参考API均运行，也不等于真实仪器/商业EDA/工业模型验证。
