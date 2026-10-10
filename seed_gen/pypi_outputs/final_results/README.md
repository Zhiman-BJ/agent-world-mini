# 半导体场景正式产物

## 2026-09-29 逐场景调研 Seed

`semiconductor_researched_scenarios_20260929.json` 来自仓库根目录
`半导体场景调研 - Sheet1 (1).csv`，按“CSV 每条业务场景对应一个 Seed”生成，共 524 条：
480 条 `accepted`、34 条 `needs_revision`、10 条 `rejected`。三种状态都保留原始场景、包边界、
可合成任务和评语，防止静默丢失；批量生成环境前应优先选择 `accepted`，其余 44 条先补充或修正。

新 Seed 复用下文 160 个 L3 Seed 已核实的包、源码版本、来源和 API 作为参考锚点。每个锚点包都保留，
但每包最多选择 2 个顶层参考 API、每个类最多 6 个方法。CSV 提到而旧锚点尚未核实的包、外部软件、
服务和仪器只保留在 `others.scenario_design.package_or_combination_raw` 中，不伪装成已验证工具。
该文件不覆盖原来的 L3 汇总文件。

CSV 末尾新增 `是否接受` 列：只有评审通过的 480 条写入 `√`，其余场景保留为空。批量生成使用
`semiconductor_researched_scenarios_accepted_20260929.json`，它只包含这 480 条，不需要运行时再猜测
评审状态。

```bash
python -m seed_gen.scripts.build_researched_scenario_seeds
python -m seed_gen.scripts.build_researched_scenario_seeds --check
python -m seed_gen.scripts.build_researched_scenario_seeds --accepted-only
python -m seed_gen.scripts.build_researched_scenario_seeds --accepted-only --check
```

`semiconductor_scenario_collection.json`合并全部160个L3；`semiconductor_scenario_01.json`至`_08.json`提供8份L1分组；`l3/`集中保存160份单场景JSON。三个粒度用于不同读取方式，均来自同一组L1内容，不额外保留worker副本。

01及03–08共138项沿用2026-09-18完成的来源/静态/固定任务验证；02共22项来自0916快照，没有重跑或提升验证口径。2026-09-18统一场景契约时，仅将全部记录规范为`schema_version=scenario-1.1`和`source=deep_research`，把02的index移至未占用的28–49，并统一其L1标签；包、工具、任务和计数不变。当前02分片SHA256为`ee7c16623a70e485d9e94f8cf7937c000da49804c8962bf6c13050a5065946fb`。

| L1 | L3数 |
| --- | ---: |
| 01 | 27 |
| 02 | 22 |
| 03 | 24 |
| 04 | 7 |
| 05 | 11 |
| 06 | 30 |
| 07 | 28 |
| 08 | 11 |

2026-09-18整理范围：移入原138份单场景文件，并由02分组补出22份；核对内容一致后移出六个worker输出目录（种子、局部汇总及重复审计）和三场景试点输出，共7个目录、255个文件、422.97 MiB。永久删除被自动审批审核以`blocked by policy`拒绝，实际采用可恢复清理：暂存到仓库内已忽略的`tmp/semiconductor-output-cleanup-20260918/`。因此已清理正式产物目录，但未释放这部分磁盘空间。可从该目录恢复，也可按保留脚本重建。

保留但不上传：`../scenario_collection/reports/`及coverage、原始包源码/全量候选、筛选profiles、网页正文、数值运行结果、虚拟环境、后续重新生成的worker/试点输出。它们可能仍被来源哈希和验证流程引用，因此不按“临时文件”一概删除。可复用脚本、来源清单、研究URL、依赖锁及本目录正式产物继续上传。

`../selection_reports/`原先已在ignore中，但两份旧报告仍被Git跟踪；本次对atomate2与pymatgen-core报告执行了`git rm --cached`，只停止跟踪，保留本地文件。其余变更未批量暂存，也未提交。

整理验证：160个唯一场景、160个唯一index、各L1分片与总汇总内容一致、工具计数一致；覆盖口径仍为138项已验证和22项保留。场景Schema和DataGen加载回归通过。本次未重新运行277条数值任务。

仅合并与检查（不重新验证底层包/物理任务）：

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.merge_scenario_results
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.merge_scenario_results --check
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.report_scenario_collection --skip-freeze
```

完整源码再解析及重生成命令见 [采集执行记录](../../scenario_collection/README.md)。从干净checkout执行来源/运行复核前，须按记录恢复被ignore的原始源码、候选索引、网页快照、profiles与固定任务结果。
