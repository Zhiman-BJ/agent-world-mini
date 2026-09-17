# 01.07 DFT接口与ABINIT输入/结果场景

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

2026-09-17。本目录独立维护 `01.07.01` 与 `01.07.02`；只读复用共享发布源码和原始候选，未修改上游源码或其他场景。05/06工作已完成并冻结。

| 场景 | 组合 | 实际验证 | 未验证范围 |
| --- | --- | --- | --- |
| 01.07.01 ASE Calculator DFT | ASE 3.29.0 + GPAW 26.7.0 | WSL私有Python环境中真实Si2平面波LDA SCF，错误预算与修复，GPW写入/恢复，电子数、晶胞和独立能量项检查，各重复两次 | 100eV/Gamma只是粗样例；未做生产cutoff/kmesh/力精度收敛、MPI、native `_gpaw`或广泛XC测试 |
| 01.07.02 ABINIT Workflow | AbiPy v1.0.0 | Windows输入生成/变量错误/SCF–NSCF角色修复/JSON恢复；官方Si GSR的总能、每原子能、带隙和占据积分独立核验，各重复两次 | 未安装或执行ABINIT，未新计算SCF/DFPT，未运行队列/集群工作流；参考输出来自ABINIT 8.0.6 |

固定来源见 `solver_sources.json`、`research/source_checks.json`、`research/source_web/index.json`。GPAW PyPI当前稳定版与官方GitLab标签一致；发布日期为PyPI上传时间。ASE和AbiPy的全量索引与共享候选完全一致地复制到本目录独占raw lane，保留其原manifest来源。所有在线成功来源正文保存在 `research/solver_web/`，每场景选4个已检查页面；3个404留作失败证据、不计来源。

| 包 | 固定commit | 源码 |
| --- | --- | --- |
| ASE 3.29.0 | f27c0005ae6a67ea419f996e728668865bfc1f86 | `seed_pypi_raw/ase` |
| GPAW 26.7.0 | 9c6f4ccd94355b3e8c1c418b4b605d7ce7552e30 | `seed_pypi_raw/gpaw` |
| AbiPy v1.0.0 | 414ef88c4523fef2677da0fd954444e17884e536 | `seed_pypi_raw/abipy` |

AbiPy标签中 `pyproject.toml` 仍含旧0.9.8版本字段；发布标签、运行时release与安装包元数据一致为1.0.0。该差异未通过修改上游消除。

## 运行环境与复现

Windows工具来自conda base：`C:/Apps/anaconda3/Scripts/uv.exe`。AbiPy环境为 `.venv-scenario-dft-abipy`，`uv pip check`确认72个安装包兼容。依赖版本冻结在 `runtime/abipy/requirements.freeze.txt`。

```powershell
C:/Apps/anaconda3/Scripts/uv.exe venv --python 3.12 .venv-scenario-dft-abipy
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-dft-abipy/Scripts/python.exe abipy==1.0.0 ase==3.29.0
.venv-scenario-dft-abipy/Scripts/python.exe -X utf8 seed_gen/scenario_collection/materials_solver/verify_abipy.py
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-dft-abipy/Scripts/python.exe
```

GPAW 26.7.0在PyPI仅有源码分发，conda-forge当时最高GPAW为25.7.0，不能用旧版本证明新候选。WSL Ubuntu-24.04有Python3.12但无pip/gcc/libxc，sudo需要密码。本轮未进行apt或全局修改。

该版本源码 `gpaw/cgpaw/__init__.py` 明确在 `GPAW_NO_C_EXTENSION=1` 时加载官方 `gpaw/purepython.py`；使用NumPy/SciPy实现所需内核。本轮采用这一官方路径，在私有WSL环境安装依赖，然后通过显式 `sys.path` 加载固定源码；没有伪造 `_gpaw` 模块，也没有宣称安装了native GPAW wheel。

WSL私有前缀 `/home/zjs32/.local/share/semiconductor-dft-20260917/`。`bootstrap_gpaw.sh` 创建无pip venv并用官方下载的get-pip引导，固定安装NumPy2.5.3/SciPy1.18.1/ASE3.29.0/gpaw-data1.1.0。`runtime/gpaw/requirements.freeze.txt` 记录实际依赖，`pip check`通过。`GPAW 26.7.0`本身来源Git源码，所以不在pip freeze中；运行报告单独记录版本/commit。

```powershell
# 首次准备需要已有官方 https://bootstrap.pypa.io/get-pip.py
# 下载位置：.venv-scenario-dft-bootstrap/get-pip.py
wsl -d Ubuntu-24.04 -- bash -lc 'cd /mnt/d/Desktop/agent-world-mini && bash seed_gen/scenario_collection/materials_solver/bootstrap_gpaw.sh'
wsl -d Ubuntu-24.04 -- bash -lc 'cd /mnt/d/Desktop/agent-world-mini && /home/zjs32/.local/share/semiconductor-dft-20260917/env/bin/python seed_gen/scenario_collection/materials_solver/verify_gpaw.py'
```

GPAW所用 `Si.LDA.gz` 的SHA256为 `ffc770d4d1e27da53074a22b6beb8e0b589c1bd83aa9279d76b94ed1586936f8`。AbiPy的Si赝势和GSR也在运行报告中记录来源路径与SHA256。输出到当前lane的 `runtime/`，没有修改这些参考文件。

## 实际问题与修复

1. 最初计划私有micromamba；官方archive已下载到 `.venv-scenario-dft-bootstrap/micromamba.tar.bz2`，WSL解压报缺少 `bzip2`，因此本lane没有创建micromamba环境。随后使用系统Python venv和官方get-pip，成功。
2. 官方旧测试仍有 `mixer={'backend':'fft'}`；26.7.0新版 `gpaw.dft.Mixer.from_param` 报 `ValueError: Unknown mixer: fft`。按同版本源码改为 `mixer={'name':'pulay'}`，无需上游补丁，SCF成功。
3. 第一次重启电子数断言失败。源码 `get_occupation_numbers(raw=False)` 对内部数组原地执行 `occ_n *= weight`，非自旋Gamma简并因子2会使后续保存的数据再次加倍。改用 `get_occupation_numbers(raw=True).copy()*2`；两轮新SCF、存盘、恢复均保持8电子。该发布行为同时写入工具筛选理由和环境边界，不能让后续任务把默认读操作当无副作用。
4. 4个band只填满价态，Fermi level为inf、不能读带隙；固定样例改为6 bands，仍不宣称其Gamma粗网格给出生产带隙。GPAW任务不使用未经外部标定的绝对能量目标。
5. PowerShell普通 `ConvertFrom-Json` 对Python参数中的大小写不同key失败；只读候选查询改用标准Python JSON，没有改动索引数据。

## 任务与独立验收

GPAW使用a=5.43Å、Si2、100eV、Gamma、LDA、6 bands、pulay、energy1e-4/density1e-3。maxiter1必须触发 `KohnShamConvergenceError`；仅修到80，实际18步收敛，能量5.926361416234121eV。两次结果完全一致。占据积分目标8来自2×4价电子；晶胞目标5.43³/4来自diamond primitive几何。GPW v7的能量项独立求和为5.926361416234119eV；误把已为eV的存储值再次乘Hartree常数得到161.2645095304671eV，必须失败。原子/晶胞、存储能量与恢复能量在固定容差保持。

GPAW最大分力约0.0121eV/Å，本轮只验有限性，**没有**以理想diamond零力或生产弛豫精度作为已通过结论。独立oracle证明接口、守恒和序列化一致，不能代替外部DFT精度基准。

AbiPy输入任务拒绝拼错的ecu、修复NSCF kptopt1→-2/iscf-2、检查2原子/8电子、独立Bohr转Å体积及dataset修改隔离。结果任务从原始NetCDF独立计算Hartree→eV、占据加权8电子和min(conduction)-max(valence)带隙，得到总能约-241.236470313eV、带隙0.5622774098eV。参考GSR来自ABINIT8.0.6，**不声称由本轮生成的输入计算而来**。

## 候选、筛选与生成

候选：`seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/materials_solver/`。
每个场景显式联合筛选，在 `prepare_materials_solver_profiles.py` 固定选择、构造路径、实体、状态生命周期、跨包合同、任务和未验证边界。ASE与GPAW分别负责原子状态和电子态求解；AbiPy单独维护ABINIT状态，不重复加入ASE/pymatgen结构编辑工具。主要只读继承接口按真实定义位置列出。

```powershell
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_materials_solver_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_materials_solver_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/materials_solver/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/materials_solver --merged-name semiconductor_scenario_materials_solver.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/materials_solver/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/materials_solver --merged-name semiconductor_scenario_materials_solver.json --group-by-l1 --verify-sources --check
```

最终单场景/合并/覆盖报告位于 `seed_gen/pypi_outputs/scenario_collection/materials_solver/`。主代理负责向全局正式产物提升；本lane不修改共享profiles和全局合并文件。参考工具集合大于4条实跑任务覆盖，不称所有候选API已执行通过。

最终校验已执行：3个源commit验证和全量候选重提取通过，生成器输出 `Generated 2 scenario-first seeds.`；随后相同参数 `--check` 输出 `Verified 2 scenario-first seeds.`，均exit0。

| 场景 | class | function | class_func | all_func | 固定任务 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 01.07.01 | 3 | 4 | 48 | 52 | 2 |
| 01.07.02 | 9 | 0 | 93 | 93 | 2 |
| 总计 | 12 | 4 | 141 | 145 | 4 |
