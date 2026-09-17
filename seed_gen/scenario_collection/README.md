# 01、03–08 场景种子采集执行记录

用户要求保留已完成的 02，继续其他 L1。本批完整范围为 **138 个 L3**，截至本记录已生成并验证 12 个，剩余 126 个；不是全量完成。机器可读状态以 [coverage.json](../pypi_outputs/scenario_collection/coverage.json) 为准。

## 分类覆盖

| L1 | 原文L3数 | 本批已验证 | 待处理 |
| --- | ---: | ---: | ---: |
| 01 材料与器件研发 | 27 | 12 | 15 |
| 02 器件与物理仿真 | 22 | 原产物保留，不重跑 | — |
| 03 芯片设计与流片 | 24 | 0 | 24 |
| 04 实验室表征与设备控制 | 7 | 0 | 7 |
| 05 晶圆测试与良率 | 11 | 0 | 11 |
| 06 Fab 制造运营 | 30 | 0 | 30 |
| 07 Inspection / Defect / Metrology | 28 | 0 | 28 |
| 08 Enterprise / Quality / Reliability | 11 | 0 | 11 |

[inventory.json](inventory.json) 覆盖全部 160 个编号，保存原文字段和行号。20 条只有标题的自由列表已补充具体应用、任务及候选包线索，标记 `explicit_editorial_supplement`；这些是待研究边界，不能当作已实现的库功能。其余来源表格的名称和应用字段保持原意。

## 已完成种子

| 场景 | 主包/补充包 | class | function | class_func | all_func | 固定任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 01.01.01 晶体结构构建与转换 | pymatgen-core + ASE；排除JARVIS重复状态 | 9 | 2 | 68 | 70 | 3 |
| 01.01.03 材料特征与筛选 | matminer + pymatgen-core | 15 | 0 | 58 | 58 | 2 |
| 01.02.01 Space Group / 标准晶胞 | spglib + pymatgen-core；不重复暴露SpacegroupAnalyzer | 5 | 6 | 48 | 54 | 2 |
| 01.02.02 Brillouin Zone / k-path | seekpath + pymatgen-core；spglib仅作为硬依赖安装 | 5 | 4 | 38 | 42 | 2 |
| 01.03.01 材料工作流 | atomate2 + jobflow + pymatgen-core | 10 | 5 | 54 | 59 | 2 |
| 01.03.02 错误恢复 | custodian + pymatgen-core；排除atomate2重复封装 | 13 | 0 | 50 | 50 | 2 |
| 01.04.01 缺陷枚举 | pymatgen-analysis-defects + pymatgen-core；排除doped重复生成 | 14 | 0 | 60 | 60 | 2 |
| 01.04.02 缺陷重构 | ShakeNBreak + doped + pymatgen-core | 9 | 9 | 44 | 53 | 2 |
| 01.04.03 形成能/电荷转变 | doped + pymatgen-core；PAD只补实际继承属性 | 11 | 0 | 51 | 51 | 2 |
| 01.04.04 费米能/浓度 | py-sc-fermi 3.0.0；排除doped浓度包装 | 6 | 0 | 57 | 57 | 2 |
| 01.05.01 谐波声子 | phonopy；排除phono3py非谐能力 | 6 | 1 | 55 | 56 | 2 |
| 01.05.02 非谐热输运 | phono3py + phonopy结构；排除hiPhive拟合 | 4 | 1 | 57 | 58 | 2 |

共 668 个参考操作、25 条完整任务；每场景3–4个成功读取的参考页面。k-path仅4个高层公开入口，加必要输入/状态/观察接口共42个，profile中记录数量例外，不加入重复包装或内部路径表凑数。类数和构造/属性沿用全量来源统计；这不是已实现的Agent JSON动作数。

结果在 [pypi_outputs/scenario_collection](../pypi_outputs/scenario_collection)，其中 `semiconductor_scenario_01.json` 是当前01的**部分汇总**。逐场景 `reports/` 审计所有候选类、函数及方法，并核验原始源码重新解析一致。源码空说明保留且单列；不修改全局种子契约。

## 发布版与来源

来源清单为 [materials_structure_sources.json](materials_structure_sources.json)、[materials_workflow_physics_sources.json](materials_workflow_physics_sources.json) 和 [materials_anharmonic_sources.json](materials_anharmonic_sources.json)，完整候选池在 `../pypi_outputs/ori_all_funcs/scenario_collection/`。旧全量索引、旧包选集、`semiconductor_scenario_02_0916.json` 均保留。

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
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/profiles --output-dir seed_gen/pypi_outputs/scenario_collection --merged-name semiconductor_scenario_collection.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/profiles --output-dir seed_gen/pypi_outputs/scenario_collection --merged-name semiconductor_scenario_collection.json --group-by-l1 --check
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

## 未完成边界与后续

余下126个场景仍需调研、正式发布版核对、完整候选池、联合选择和固定任务。继续完成01中的数据库检索、ML势、扩散、能带及量子场景，再按03–08分批推进。云服务/API密钥、商业EDA/求解器、原生编译或仪器依赖须按实际证据处理；不得把未运行设计写成任务验证通过。

当前所有结果仍是种子和固定脚本，尚未实现具有稳定实体ID、JSON工具参数、reset/step、状态失效、隔离和verifier接口的Agent服务器。25个固定任务通过不代表668个API均已执行，也不代表138个场景已完成。
