# Python 包 API 筛选清单

这里的 JSON 清单把全量 AST API 索引收敛为能力均衡的参考 API。全量索引仍保留在
`seed_gen/pypi_outputs/`，精选产物写入 `seed_gen/pypi_outputs/selected/`，逐项决策报告写入
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
  --input seed_gen/pypi_outputs/pymatgen-core_v2026.8.30.json `
  --profile seed_gen/pypi_selection_profiles/pymatgen-core_v2026.8.30.json `
  --output seed_gen/pypi_outputs/selected/pymatgen-core_v2026.8.30.json `
  --report seed_gen/pypi_outputs/selection_reports/pymatgen-core_v2026.8.30.json
```

Profile 固定原始 JSON 的规范化 SHA-256。重新解析或升级包后必须重新审查 Profile；缺失的类、函数、
方法和空描述都会使筛选失败，不能静默替换为另一个候选。

`atomate2` 的 `construction_only` 表示只验证 Job/Flow 构建、序列化和依赖图，不代表已经执行 VASP。
真正运行 VASP 需要额外的二进制、赝势、运行配置和独立验收环境。
