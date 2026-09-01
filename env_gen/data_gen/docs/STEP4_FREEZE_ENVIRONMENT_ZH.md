# Step 4：独立重算、环境导出与事实冻结

阶段入口：`env_gen/data_gen/steps/step4_freeze_environment.py`

Step 4 只处理 Step 3 已经收口的最终数据。它不联网、不补采、不修改 Record Set 或
Filesystem Scope；主要职责是独立重算关键事实、导出 v2 环境声明，并为 Step 5 建立
可验证的冻结包。

## 1. 完整流程树

```text
freeze_environment
├── A. 复核阶段收据
│   ├── run_config / Seed 身份和 Schema 路径
│   ├── source_plan 保存收据
│   ├── integration_plan 保存收据
│   └── integration_finalization 已正式收口
├── B. 独立重算事实
│   ├── 从 Raw 和下载收据重建 source_inventory
│   ├── 复核 integration_plan 的字段、来源、关系和 Scope
│   ├── 复核每个资产的物化收据
│   ├── 从 records.sqlite 和 filesystem_scopes 重建 integration_profile
│   ├── 复核 field_review 与当前 plan/profile/Raw 一致
│   └── 重建 quality_profile
├── C. 确定性导出环境
│   ├── environment.json
│   ├── environment.md
│   └── v2 环境包校验
├── D. 独立重放转换
│   ├── 无网络执行每个 Record Set 转换脚本
│   ├── 重建临时 SQLite 表和 Scope
│   ├── 比较输出 SHA-256 与表/目录摘要
│   └── reproducibility_report.json
└── E. 冻结发布包
    ├── workspace/raw -> provenance/raw
    ├── 删除运行期 workspace
    ├── source_manifest.json
    └── freeze_manifest.json
```

## 2. 为什么要独立重算

Step 3 的 Agent 可以编写转换、保存集成计划并调用画像命令，但 Step 4 不直接信任
Agent 提交的 profile。Python 必须从当前磁盘事实再计算一次：

- Raw 是否与下载收据、来源归属和哈希一致；
- SQLite 表的记录数、字段画像和关系是否与计划一致；
- 文件引用是否真的指向 Scope 成员；
- 所有选中来源是否有最终资产使用；
- rich/not_rich 是否由当前数据重算得到。

只有 `integration_tier=integrated` 才能继续。质量画像可以是 `rich` 或 `not_rich`，但不能
是结构无效或未计算状态。

## 3. 最终两个视角

### 3.1 environment.json：机器视角

`environment.json` 由 `integration_plan.json` 确定性导出，主要供 ToolGen、Validator 和运行时
使用。它只包含：

- 环境身份与简短说明；
- Record Set、字段、键和访问方式；
- Filesystem Scope 及其层级结构；
- Record Set 关系和文件路径引用。

它不声明 Raw、运行控制文件或 Agent 中间结论。

### 3.2 environment.md：交互 Agent 视角

`environment.md` 是任务侧的环境导航，不应只是字段清单。它需要基于 Step 1 的场景轮廓
与 Step 3 的最终事实说明：

- 这是什么业务环境；
- 交互 Agent 在这里处理什么对象和文件；
- 哪些工作流有真实最终资产支持；
- 哪些能力只有部分证据或当前不可用；
- 如何通过工具访问 Record Set 和 Scope。

场景候选不能直接写成已实现能力；必须以最终 need/capability 绑定和实际资产为准。

## 4. 来源与可复现性产物

### source_manifest.json

记录每个最终 Raw 证据文件的：

- 所属 `source_id`；
- 请求 URL 和最终 URL；
- 文件字节数和 SHA-256；
- 是否复用了已有文件。

### reproducibility_report.json

为每个 Record Set 和 Scope 保存：

- 转换 ID、脚本路径和脚本哈希；
- 输入 Raw 及其哈希；
- 沙箱类型；
- 输出哈希、当前状态摘要和重放状态摘要；
- 记录数或文件数。

状态摘要与重放摘要不同时，Step 4 直接失败。

### freeze_manifest.json

对发布包中除 `.datagen`、`workspace` 和临时发布文件外的所有文件记录路径、字节数
和 SHA-256。Step 5 发布前必须逐个重算比对。

## 5. 冻结后的包结构

```text
<run_dir>/
├── environment.json
├── environment.md
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/
└── provenance/
    ├── scenario_research.json
    ├── source_plan.json
    ├── source_inventory.json
    ├── integration_plan.json
    ├── integration_profile.json
    ├── field_review.json          # 有字段复核时存在
    ├── quality_profile.json
    ├── source_manifest.json
    ├── reproducibility_report.json
    ├── freeze_manifest.json
    ├── transformations/
    └── raw/
```

`.datagen/` 在 Step 4 后仍保留，用于 Step 5 最终复验和审计归档；发布成功后才从
任务侧包中移除。

## 6. 失败应回到哪一步

| 问题 | 返回阶段 |
|---|---|
| 来源身份、URL 或 Raw 收据错误 | Step 2 |
| 转换不可重放、表或 Scope 错误 | Step 3 |
| 关系、文件引用或需求绑定不闭合 | Step 3 |
| environment.json 与 integration_plan 不同 | Step 4 确定性导出逻辑 |
| 冻结后文件发生变化 | 拒绝发布，重新执行 Step 4 |

Step 4 不通过 Agent “修说明”遮盖数据事实问题。任何业务数据修改都必须回到 Step 3。
