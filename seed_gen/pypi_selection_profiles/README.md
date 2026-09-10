# Python 包 API 筛选清单

这里的 JSON 清单把全量源码 API 索引收敛为能力均衡的参考 API。全量索引保留在
`seed_gen/pypi_outputs/ori_all/`，精选产物写入 `seed_gen/pypi_outputs/` 根目录，逐项决策报告写入
`seed_gen/pypi_outputs/selection_reports/`。

筛选采用显式符号清单，而不是不稳定的全局 Top N。每个能力族记录选择理由、验证等级和证据：

- `public_api`：包级公开入口或稳定公共 API。
- `official_docs`：README、教程或官方 API 文档明确使用。
- `core_capability`：属于当前环境声明边界内的核心能力。
- `deterministic_local`：无需网络或外部科学计算程序即可确定性执行或构建。
- `composable_io`：输入输出适合序列化，并能与其他候选组成调用链。

执行命令：

```powershell
python -m seed_gen.scripts.select_python_ref_tools `
  --input seed_gen/pypi_outputs/ori_all/devsim_v2.11.0.rc5.json `
  --profile seed_gen/pypi_selection_profiles/devsim_v2.11.0.rc5.json `
  --output seed_gen/pypi_outputs/devsim_v2.11.0.rc5.json `
  --report seed_gen/pypi_outputs/selection_reports/devsim_v2.11.0.rc5.json
```

Profile 固定原始 JSON 的规范化 SHA-256。重新解析或升级包后必须重新审查 Profile；缺失的类、函数、
方法都会使筛选失败，不能静默替换为另一个候选。默认拒绝空描述；为遵守“没有的填空”，
新清单可对单个符号填写非空 `missing_description_reason`，显式保留该符号及其所选方法的原始空说明。
该例外及具体缺失名单写入报告，不填造说明，也不等于通过严格发布校验。

DEVSIM、nextnanopy、Sesame、Solcore 四份清单声明 `target_all_func: {"min": 50, "max": 100}`；
筛选器会重算并强制检查。完整依据、能力范围及复现命令见
[semiconductor_selection.md](semiconductor_selection.md)。证据分数为静态选择辅助，不代表实验效果。

器件与缺陷组的 9 个新增发布包见 [整理报告](../pypi_outputs/器件与缺陷包整理.md)，
批量运行 `python -m seed_gen.scripts.select_release_python_seeds --manifest seed_gen/pypi_device_defect_sources.json`。
meshio 和 ShakeNBreak 按核心能力范围分别保留 21、42 个调用，profile 中的
`desired_all_func` 保存一般目标，`target_all_func` 保存本次检查范围，`target_exception_reason` 说明差异。
新增清单的验证等级均表示计划或必要依赖，未执行目标 API 数值验证。

`atomate2` 的 `construction_only` 表示只验证 Job/Flow 构建、序列化和依赖图，不代表已经执行 VASP。
真正运行 VASP 需要额外的二进制、赝势、运行配置和独立验收环境。
