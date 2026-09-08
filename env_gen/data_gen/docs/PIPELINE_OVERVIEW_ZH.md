# DataGen 四步流程总览

```text
DataGen
├── Step 0：准备运行上下文
│   ├── 选择并校验 Seed
│   ├── 固化 Schema、协议、预算和覆盖底线
│   └── 输出 selected_seed.json、run_config.json
│
├── Step 1：环境场景研究
│   ├── 从 Seed 和种子 URL 理解现实业务
│   ├── 整理实体、工具、任务和数据方向
│   └── 输出 provenance/scenario_research.json
│
├── Step 2：Agent 自主采集与文件卡
│   ├── Agent 下载、打开、必要预处理并淘汰无用候选
│   ├── 每个有效文件记录 URL、用途、主体和限制
│   ├── Python 补充哈希、大小、格式和粗略数量
│   ├── 检查 Seed 90% 与 Step 1 75% 最低业务覆盖线
│   └── 输出 Raw、source_research、source_inventory、collection_profile
│
├── Step 3：Agent 直接集成
│   ├── 读取全部已确认数据，不再继续采集
│   ├── 直接设计 environment.json 和统一 build.py
│   ├── 一次生成 records.sqlite 与必要的 Filesystem Scope
│   ├── Python 返回 Schema、键、关系、路径和重放错误
│   └── Agent 修复至 finalize
│
└── Step 4：独立验收、冻结与发布
    ├── 无网络重放统一 build.py
    ├── 复核 environment.json 与最终 state
    ├── 生成来源、构建和文件哈希收据
    ├── 清理运行期目录
    └── 原子发布
```

## 阶段边界

| 阶段 | Agent 负责 | Python 负责 |
|---|---|---|
| Step 0 | 不调用 Agent | Seed 身份、配置和输入固化 |
| Step 1 | 现实场景、实体、能力和任务语义 | Schema、Seed 覆盖和来源 URL |
| Step 2 | 下载、检查、预处理和文件卡语义 | URL/哈希去重、基础统计和覆盖率 |
| Step 3 | 业务建模、字段统一、去重、关系和文件组织 | 构建隔离、SQLite、引用与重放校验 |
| Step 4 | 不调用 Agent | 最终复验、哈希、冻结和原子发布 |

流程只在职责发生变化时进入下一步。Step 2 不提前设计最终表；Step 3 不重新联网找数据；Step 4
不修改业务事实。90% Seed 和 75% Step 1 是公开数据难以完全取得时的最低容错线，不是达到后立即
停止或删减数据的目标。

## Step 2 循环

```text
未覆盖主体 + 已下载 URL/hash
        ↓
Agent 下载并检查实际内容
        ↓
无用则删除；有用则必要预处理并写文件卡
        ↓
Python 计算基础统计和 supported 覆盖
        ↓
仍有明确高价值数据则继续，否则 ready/partial
```

领域文件、源码、数据库或归档都依据现实任务需要决定，不是固定配额。官方文档只能作为语义证据，
不能代替真实业务记录。

## Step 3 循环

```text
environment.json + provenance/build.py
        ↓
integratectl build
        ↓
integratectl assess
        ↓
fix blocking_issues ──┐
        ↑              │
        └──────────────┘
        ↓ ready
integratectl finalize
```

这里没有独立 `integration_plan`。最终环境结构已经由 `environment.json` 表达；统一构建逻辑已经由
`provenance/build.py` 表达。Agent 循环、完整 Prompt 和运行指南生成都位于
`step3_integrate_data.py`；`integration/` 子目录只执行机械命令。Python 不再生成大型全局画像，
而是返回可以直接修复的事实错误。

## 运行期与发布产物

```text
<run_dir>/
├── .datagen/
│   ├── selected_seed.json
│   ├── run_config.json
│   ├── collection_profile.json
│   ├── INTEGRATION_GUIDE.md
│   ├── integratectl
│   ├── integration_assessment.json
│   └── integration_finalization.json
├── workspace/raw/
├── environment.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/
└── provenance/
    ├── scenario_research.json
    ├── source_research.json
    ├── source_inventory.json
    └── build.py
```

发布后：

```text
<published_environment>/
├── environment.json
├── environment.md
├── validation.json
├── state/
└── provenance/
    ├── raw/
    ├── build.py
    ├── source_manifest.json
    ├── integration_receipt.json
    ├── generation_audit.json
    └── freeze_manifest.json
```

`.datagen/` 和 `workspace/` 在发布后删除。Raw 冻结到 `provenance/raw/`，不会作为任务侧资源暴露。

## 详细文档

- `STEP0_PREPARE_RUN_ZH.md`
- `STEP1_RESEARCH_SCENARIO_ZH.md`
- `STEP2_COLLECT_DATA_ZH.md`
- `STEP3_INTEGRATE_DATA_ZH.md`
- `STEP4_FREEZE_ENVIRONMENT_ZH.md`
