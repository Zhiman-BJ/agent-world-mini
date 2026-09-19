# 半导体场景正式产物

`semiconductor_scenario_collection.json`合并全部160个L3；`semiconductor_scenario_01.json`至`_08.json`提供8份L1分组；`l3/`集中保存160份单场景JSON。三个粒度用于不同读取方式，均来自同一组L1内容，不额外保留worker副本。

01及03–08共138项沿用2026-09-18完成的来源/静态/固定任务验证；02共22项来自0916快照，仅移动改名为`semiconductor_scenario_02.json`，没有重跑或提升验证口径。该文件SHA256保持`8a56d7b6d204a0a135a791f7c22a5e374ee41a29dae88552305e91bfaf7bbbef`。合并保留各记录的原schema_version、字段、工具、任务和计数。

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

整理验证：160个唯一场景、各L1/L3与总汇总内容一致、工具计数一致、02原文件哈希未变；覆盖报告仍为138项已验证和22项保留。16项目录生成及联合筛选回归通过，`git diff --check`通过。本次未重新运行277条数值任务。

仅合并与检查（不重新验证底层包/物理任务）：

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.merge_scenario_results
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.merge_scenario_results --check
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.report_scenario_collection --skip-freeze
```

完整源码再解析及重生成命令见 [采集执行记录](../../scenario_collection/README.md)。从干净checkout执行来源/运行复核前，须按记录恢复被ignore的原始源码、候选索引、网页快照、profiles与固定任务结果。
