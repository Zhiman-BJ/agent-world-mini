# Step 4：独立验收、冻结与发布

阶段入口：`env_gen/data_gen/steps/step4_freeze_environment.py`

Step 4 是流程的最终发布门。它不调用 Agent、不联网、不补采，也不修改业务模型；它只独立重放
Step 3 的统一构建、冻结来源证据并原子发布。

## 1. 流程

```text
读取 Step 3 finalization
        |
        v
独立执行 provenance/build.py
        |
        +--> 与候选 state 的逻辑摘要一致
        +--> environment.json 与重放 state 同时通过 v2 Validator
        |
        v
冻结来源与构建证据
        +--> provenance/raw/
        +--> source_manifest.json
        +--> integration_receipt.json
        +--> generation_audit.json
        +--> freeze_manifest.json
        |
        v
删除 workspace/ 与 .datagen/
        |
        v
写 validation.json 并原子移动到正式目录
```

## 2. 紧凑集成收据

`provenance/integration_receipt.json` 由 Python 自动生成，包含：

- `environment.json` 和 `provenance/build.py` 的 SHA-256；
- 每个 Raw 的路径、来源、大小和 SHA-256；
- 每张最终表的记录数和逻辑摘要；
- 每个 Scope 的文件数和文件树摘要；
- 独立重放得到的 state 摘要；
- Step 2 已确认的 Seed 与 Step 1 覆盖统计。

它用于回答“哪些真实输入通过哪个构建脚本形成了哪个最终状态”，不重复保存字段频次、样本值、
逐表转换计划或长篇质量结论。

## 3. 发布包

```text
<environment_root>/
├── environment.json
├── environment.md
├── validation.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/<scope_id>/
└── provenance/
    ├── scenario_research.json
    ├── source_research.json
    ├── source_inventory.json
    ├── source_manifest.json
    ├── integration_receipt.json
    ├── build.py
    ├── generation_audit.json
    ├── freeze_manifest.json
    └── raw/
```

`workspace/` 是运行期目录，`.datagen/` 是控制目录，发布成功后都不会暴露给任务侧。

## 4. 失败返回

| 问题 | 返回阶段 |
|---|---|
| Raw、下载证据或 Step 2 覆盖失效 | Step 2 |
| build.py 失败或重放结果不同 | Step 3 |
| SQLite、关系、文件引用或 Scope 不合法 | Step 3 |
| environment.json 与最终 state 不一致 | Step 3 |
| 冻结或原子发布失败 | Step 4 |

Step 4 不通过修改说明、放宽键约束或生成补位记录来让环境过门。
