# 光刻场景 03.12.01–04 执行记录

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

2026-09-17 完成四个场景的正式发布包联合筛选、CPU 固定任务验证和源码重解析核对。每场景两个任务，各重复两次，报告完全一致。最终生成及 `--verify-sources --check` 均退出 0，分别输出 `Generated 4 scenario-first seeds.` 和 `Verified 4 scenario-first seeds.`。

## 产物与计数

| 场景 | 实际主包 / 补充包 | class | function | class_func | all_func | 任务 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 03.12.01 GDS/mask→wafer aerial image | prysm / gdstk | 5 | 19 | 45 | 64 | 2 |
| 03.12.02 mask correction | gdstk / prysm | 5 | 19 | 45 | 64 | 2 |
| 03.12.03 inverse mask optimization | prysm / scipy | 4 | 18 | 24 | 42 | 2 |
| 03.12.04 gradient-based mask optimization | torchoptics / scipy | 10 | 12 | 39 | 51 | 2 |
| 合计 | 各场景独立计数，不跨场景去重 | 24 | 68 | 153 | 221 | 8 |

`all_func = function + class_func`，不是最终 Agent JSON 工具服务器的动作数。03.12.03 的 42 操作为有说明的数量例外：紧凑的相干光学核心与一个约束优化器已覆盖任务链，不添加无关接口凑够 50。03.12.03/04 各保留一个来源缺失说明项；`OptimizeResult` 由 `minimize` 返回，04 的 `ModulationElement` 由所选子类构造获得，报告明确解释构造来源，未伪造构造方法。

- 精选种子：`seed_gen/pypi_outputs/scenario_collection/lithography/03.12.01.json` 至 `03.12.04.json`。
- 本路汇总：同目录 `semiconductor_scenario_lithography.json`、`semiconductor_scenario_03.json`；仅包含本路四场景，不代表整个 L1 03。
- 审计：同目录 `reports/`；`summary.json` 包含计数、空说明、构造说明和运行报告哈希。
- 全量候选：`seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/lithography/`。
- 显式选择：本目录 `profiles/`；场景研究及 4–5 个成功来源/场景：`research/03.12.*.json`。
- 包版本与来源：`lithography_sources.json`、`research/source_checks.json`；HTTP 响应、正文与哈希在 `research/*_web/`。
- 实跑报告：`runtime/stable/03.12.01.json` 至 `03.12.03.json`，`runtime/torchoptics/03.12.04.json`。
- 完整环境锁定：本目录 `requirements.freeze.txt`，共 37 包，与 `runtime/requirements.freeze.txt` 一致；顶层副本随复现配置提交。`runtime/stable/requirements.freeze.txt` 是安装 TorchOptics 前的阶段快照。

主线提升由协调 Agent 串行处理。本路不修改已冻结的 L1 05/06、01.07.01/02 和其他 Agent 的产物。

## 正式版本及排除候选

| 正式包 | 固定版本 | 完整提交 | 本地源码 |
| --- | --- | --- | --- |
| prysm | v0.21.1 / PyPI 0.21.1 | `091b1af9e847d93c6272e3e6e1b4108bfe7012c2` | `seed_pypi_raw/lithography/prysm` |
| torchoptics | v1.0.2 / PyPI 1.0.2 | `34fe9c40f874e53db225e703c5580befe8c523b5` | `seed_pypi_raw/lithography/torchoptics` |
| gdstk | v1.0.1 / PyPI 1.0.1 | `2e468cdab1aa380af559eb7ff4125e1924bbad1f` | `seed_pypi_raw/l1_design_lab/gdstk`，只读复用 |
| scipy | v1.18.1 / PyPI 1.18.1 | `e4e854eaa8f18d807cd3496028e257e36caa93cc` | `seed_pypi_raw/l1_test_fab/scipy`，只读复用 |

prysm 的 GitHub latest Release 页面仍是 v0.21，但官方 v0.21.1 标签、最新稳定 PyPI 分发和 `docs/source/releases/v0.21.1.rst` 一致，因此取明确发布的补丁版，`source_kind=official_stable_tag_and_distribution`。TorchOptics 的官方 GitHub 最新非预发布 Release 与 PyPI 一致，`source_kind=official_stable_release`。gdstk/scipy 来源清单及全量索引从各自原始发布清单复用，逐对象重解析验证；不改写原包索引的说明。SciPy 在线参考文档显示 1.18.0，实际签名以固定源码和运行版 1.18.1 为准。

以下原始候选均核查到 `/releases/latest` 为 404、完整 tags 列表为空，也无经确认匹配的 PyPI 发布。它们全部排除于正式场景接口和任务执行链，仍保留官方仓库固定提交及完整静态候选：

| 排除候选 | 官方仓库 | 固定提交 | 发布差异 |
| --- | --- | --- | --- |
| lithosim | VLSIDA/lithosim | `b3868e025716ba1c236f892345d57709f0cebacd` | 源码 pyproject 声明 0.1.0，不构成已发布版本 |
| OpenILT | OpenOPC/OpenILT | `dabb97c6ca3dfd159362e48273c436444c77353b` | 无 Release/tag/已核实分发 |
| TorchLitho-2.0 | OpenOPC/TorchLitho-2.0 | `c5f46ce8282a90d7ce7e224b5f69a8135fcfc6ff` | setup 名为 pylitho、声明 2.0.0；PyPI pylitho 404，不构成发布 |
| TorchLitho | TorchOPC/TorchLitho | `027cf8a9ec3ebda3b0ae9902313d7c856e67af03` | 无 Release/tag/已核实分发 |

四者统一标记 `source_kind=unreleased_commit_snapshot`。`tag=snapshot-<12位提交>` 仅用于文件索引命名；`ref` 是真实完整提交，`release_url` 和 `release_published_at` 留空。没有把默认分支或 setup 版本号冒充发布版本，也没有修改上游源码。

## 已执行任务与独立验收

`verify_stable.py` 执行前三场景六任务，`verify_torchoptics.py` 执行最后两任务。NumPy 数组、归档、PyTorch 张量/自动微分和 verifier 算术作为显式运行基础设施；任务的包接口引用均属于选集，没有使用基础设施声明隐藏第二套物理求解器。

| 场景 / 故障 | 修复与独立验收 |
| --- | --- |
| 03.12.01 GDS 单位误读 | 将 GDS 微米单位显式换成纳米，活动像素从错误的 1 恢复为 25；独立直接 DFT 矩阵对照 prysm 像强度，最大误差小于 1e-12，归档读回一致 |
| 03.12.01 离焦单位与剂量 | 100 nm 误当 100 mm，修为 1e-4 mm；用独立余弦光栅 Fresnel 强度公式核验，dose=0.8，平均强度为 0.8×(0.25+0.0625/2) |
| 03.12.02 几何偏置 | 对 120 nm 方形比较向外偏置 0/40/80 nm，最优 40 nm，阈值轮廓错像素数 25→4；每候选独立 DFT 核验，修正 GDS 写出/读回一致 |
| 03.12.02 目标图层错误 | 从 layer=9 条带改回 layer=1 方形，验证目标面积 40000 nm²、目标 25 像素和成像错误 4 像素 |
| 03.12.03 迭代预算不足 | maxiter=1 残差 0.020981276862270803；预算改为 30，实际 9 次迭代达 5.050222846454438e-13；独立 DFT 残差 5.05022284715425e-13 |
| 03.12.03 灰度上界过低 | 错误上界 0.35 残差 0.014605535144203458；恢复上界 1 达到目标，归档掩膜由独立 DFT 重放 |
| 03.12.04 detach 断图 | 错误路径必须抛 RuntimeError；修复后方向导数 autograd=19.045970712700807，中央差分=19.045970713956624，相对误差 6.593608709982079e-11；独立 DFT 像强度最大误差 2.9976021664879227e-15，同时验证功率守恒 |
| 03.12.04 梯度反号 | 错号损失 0.02063368151798169；修复后 14 次迭代平均损失 3.2477708580413496e-12；两个过程角的独立 DFT 残差为 2.2935335684324746e-12 / 4.202008147337202e-12，归档重放通过 |

03.12.04 实际后端为 TorchOptics 1.0.2 + PyTorch 2.11.0+cpu，SciPy 1.18.1 使用真实 autograd 梯度。采用 193 nm 波长、200 nm 像素、16×16 网格、`ASM_FRESNEL`、`asm_pad=(0,0)`，两个过程角是 `(z=1µm, dose=0.9)` 和 `(z=5µm, dose=1.1)`。03.12.03 则使用 SciPy 数值梯度，不能宣称它执行了 OpenILT 或 TorchLitho 算法。

## 候选失败证据及适用边界

`research/candidate_checks.json` 单独保留负面研究结果：

- lithosim `anneal` 的 `while True` 退出条件为 `rng.random() >= 9.0 - nc * 0.1`；8 邻域 `nc∈[0,8]`，阈值最小 8.2，而随机值在 `[0,1)`，静态确认不能退出；没有实际运行这个死循环。
- TorchLitho2 自定义 VJP 在固定 16×16 方向导数测试中得到 19.219563184536362，中央差分为 14.667411131491676，相对误差约 31.0%；直接公开 `AbbeSim` 原生图为 14.667444694511751，相对误差 2.2882715821005706e-06。未修补上游源码，正式场景改用发布版 TorchOptics。
- OpenILT 的官方核在 64×64 CPU 上运行 5 步得到 l2=7.356530666351318、PVBand=2、evaluation=(12,2)。这只是被排除研究快照的 smoke，不计入八个已验收任务，也没有宣称独立完整 oracle。
- `verify_differentiable.py` 是未采纳的 TorchLitho2 快照实验，曾在 100 步优化容差断言失败；没有被任何正式 profile 引用，不能当成成功验证入口。`probe_*.py` 和网页发现脚本均为研究过程材料。
- `docs.torchoptics.dev/stable/` 的尝试因 SSL 失败，未计入成功来源；PyPI 指向的实际官方文档 `torchoptics.readthedocs.io` 已读取。waveprop 仅做替代身份调查，未安装、未选择。

四场景验证的是局部 16×16 CPU 标量相干成像/近场 Fresnel 模型。周期边界与不填充是显式设定；反演使用连续灰度透射率。阈值只是工程比较层，未声称有标定光刻胶模型。未覆盖一般 Hopkins 部分相干、二值掩膜可制造性、复杂工艺校准、GPU 或全片规模；03.12.04 不是高 NA 投影系统验收。实际任务只执行了选集的一部分接口，不能据八任务声称全部 221 操作已实跑。后续 Agent 环境仍需实现实体 ID、状态失效、工具参数、reset/step 和隔离 verifier。

## 复现入口

从仓库根目录执行。环境 `.venv-scenario-lithography` 使用 conda base 的 uv、Python 3.12；核心锁定版本为 prysm 0.21.1、gdstk 1.0.1、scipy 1.18.1、numpy 2.5.3、torchoptics 1.0.2 和 torch 2.11.0+cpu。`uv pip check` 已确认 37 包兼容。以下是按当前完整 freeze 复现环境的命令；CPU torch 分发通过 PyTorch 官方索引取得：

```powershell
C:/Apps/anaconda3/Scripts/uv.exe venv --python 3.12 .venv-scenario-lithography
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-lithography/Scripts/python.exe -r seed_gen/scenario_collection/lithography/requirements.freeze.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-lithography/Scripts/python.exe
.venv-scenario-lithography/Scripts/python.exe -X utf8 seed_gen/scenario_collection/lithography/verify_stable.py
.venv-scenario-lithography/Scripts/python.exe -X utf8 seed_gen/scenario_collection/lithography/verify_torchoptics.py
.venv-scenario-lithography/Scripts/python.exe -X utf8 seed_gen/scenario_collection/lithography/record_candidate_checks.py
```

已执行并通过的全量索引、profile 与生成/检查入口：

```powershell
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_lithography_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_lithography_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/lithography/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/lithography --merged-name semiconductor_scenario_lithography.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/lithography/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/lithography --merged-name semiconductor_scenario_lithography.json --group-by-l1 --verify-sources --check
```

来源、研究或运行报告变化后，先人工复核再重建 profile，不能只替换哈希通过检查。最终来源检查包括所有选中与排除候选；这里的通过表示来源/选集/任务证据一致，不表示全局旧种子 Schema 的整个外壳通过，也不表示完整 Agent 环境已建成。
