# DataGen 准备阶段与五个业务阶段总览

```text
DataGen
├── Step 0：准备运行上下文
│   ├── 输入：Seed 集合和 global_id
│   ├── 验证并选择完整 Seed
│   ├── 解析协议和 checkpoint Schema 路径
│   ├── 固化时间、轮次、文件和质量策略
│   └── 输出：selected_seed.json、run_config.json
│
├── Step 1：环境场景研究
│   ├── 输入：一个简略 Seed
│   ├── 从 Seed URL 开始联网理解领域、产品和场景
│   ├── 同时完善环境简述、详细描述、实体、工具和任务
│   ├── 记录后续数据方向、关键来源和待确认问题
│   ├── Python 校验 Seed 身份、名称和参考工具/任务覆盖
│   └── 输出：provenance/scenario_research.json
│
├── Step 2：来源探索与代表性取样
│   ├── 以场景简报为起点继续调查，不把它当白名单
│   ├── 用 source_plan 记录修订结论、精确 URL 和来源状态
│   ├── 对每个候选来源取得少量、跨结构分支的真实 Raw 样本
│   ├── Python 计算 source_inventory，确认格式、字段、分页和可用性
│   └── finalize 输出：source_plan、source_inventory
│
├── Step 3：来源选择、定向深采与数据集成
│   ├── 基于真实样本制定 integration_plan
│   ├── 选择来源并定向补量，不再盲目扩大来源范围
│   ├── 物化统一 Record Set 和按操作上下文划分的 Filesystem Scope
│   ├── Python 每轮重算关系、文件索引、多源连接和质量缺口
│   ├── 按缺口补采、修转换、拆 Scope 或删除无关资产
│   └── finalize 输出：records.sqlite、Filesystem Scope、integration_profile
│
├── Step 4：冻结事实并导出环境声明
│   ├── 独立复核 Step 3 的物化状态和可复现性
│   ├── 冻结业务文件、采集证据和控制输入
│   └── Python 从 integration_plan 导出 environment.json/environment.md
│
└── Step 5：校验、哈希收口与发布
    ├── 完整 Validator
    ├── 只修声明，不再改业务数据
    ├── 写来源哈希并再次校验
    ├── 归档控制记录
    ├── 原子发布到 rich/ 或 not_rich/
    └── 发布后可执行独立批量审计和 HTML 汇总
```

## 阶段边界

| 阶段 | Agent 可以形成的判断 | 程序强制保证的事实 |
|---|---|---|
| Step 0 | 不调用 Agent | Seed 唯一选择、协议路径和运行策略固化 |
| Step 1 | 环境、实体、工具、任务语义以及调查方向 | Seed 身份、参考能力覆盖、名称、URL、受控保存和输入哈希 |
| Step 2 | 深调修订、来源探索、代表性取样 | 精确 URL、下载收据、Raw 结构画像和来源终态 |
| Step 3 | 来源选择、定向深采、统一建模、缺口修复 | 物化状态、关系闭合、文件索引、多源连接和集成画像 |
| Step 4 | 对冻结事实生成环境语义声明 | 业务文件、计划、来源证据和重放哈希不可变化 |
| Step 5 | 仅复核和发布 | 完整协议校验、来源哈希、原子发布 |

Step 1 有意保持宽松，因为它的职责是把 Seed 场景讲具体，而不是提前编写最终环境协议。
Step 2 开始严格，因为 URL、文件和来源状态已经成为可验证的事实；Step 3 进一步把样本扩充为最终
资产，并在每轮画像后决定是否继续补采。

五个阶段之间传递的是逐渐收紧的事实，而不是五次重复调研：

```text
Seed
└── Step 0：选择 Seed，固化协议与策略
        ↓ selected_seed + run_config
    Step 1：形成场景语义和后续数据方向
        ↓ scenario_research
    Step 2：用代表性真实样本确认、修订或拒绝来源假设
        ↓ source_plan + source_inventory
    Step 3：选择来源、深采、物化并循环修复集成缺口
        ↓ integration_plan + integration_profile + records.sqlite
    Step 4：冻结上述事实并导出环境声明
        ↓ environment.json + environment.md
    Step 5：验证声明与冻结事实一致，然后发布
```

Step 2 内部最重要的顺序是：

```text
source plan / exact URL / representative Raw
        ↓
source inventory + exploration assessment
        ↓
source finalization = ready / insufficient_public_data

Step 3 内部最重要的顺序是：

integration plan / selected sources
        ↓
targeted deep collection + deterministic materialization
        ↓
integration profile + environment quality profile
        ↓
assessment = fix / continue / ready
        ↓
targeted supplement or model repair
        ↓
integration finalization
```

`source_inventory` 只描述探索样本实际长什么样；`integration_profile` 描述最终资产是否闭合；
`environment_quality_profile` 比较最终事实与丰富度门槛；`assessment` 才决定下一步。详细算法和默认
门槛分别见 `STEP2_EXPLORE_SOURCES_ZH.md` 与 `STEP3_INTEGRATE_DATA_ZH.md`。

## 主要产物树

```text
<run_dir>/
├── .datagen/
│   ├── selected_seed.json
│   ├── run_config.json
│   ├── RESEARCH_GUIDE.md
│   ├── scenario_research_receipt.json
│   ├── EXPLORATION_GUIDE.md
│   ├── explorectl
│   ├── INTEGRATION_GUIDE.md
│   ├── integratectl
│   ├── exploration_assessment.json
│   ├── exploration_finalization.json
│   ├── integration_assessment.json
│   ├── integration_finalization.json
│   ├── integration_materialization_receipts.json
│   ├── source_plan_receipt.json
│   ├── download_receipts.json
│   ├── download_attempts.json
│   ├── raw_integrity_snapshot.json
│   ├── round_feedback.json
│   ├── round_history.json
│   └── agent_runs/
├── workspace/
│   └── raw/                 # Step 2 探索样本；Step 4 冻结为 provenance/raw
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/
└── provenance/
    ├── scenario_research.json
    ├── source_plan.json
    ├── source_inventory.json
    ├── integration_plan.json
    ├── integration_profile.json
    ├── quality_profile.json
    ├── source_manifest.json
    ├── reproducibility_report.json
    ├── freeze_manifest.json
    ├── generation_audit.json
    └── raw/
```

发布目录根部还包含：

```text
<published_environment>/
├── environment.json
├── validation.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/
└── provenance/
    └── raw/
```

详细分支分别见：

- `STEP0_PREPARE_RUN_ZH.md`
- `STEP1_RESEARCH_SCENARIO_ZH.md`
- `STEP2_EXPLORE_SOURCES_ZH.md`
- `STEP3_INTEGRATE_DATA_ZH.md`
- `STEP4_FREEZE_ENVIRONMENT_ZH.md`
- `STEP5_VALIDATE_AND_PUBLISH_ZH.md`

## 发布后批量审计树

`scripts/build_step1_audit_report.py` 不参与环境生成，也不改变发布结果。它从多个已发布环境重新
读取磁盘事实，执行一次面向批量结果的独立审计，再生成不依赖服务器的单文件 HTML。

```text
published environments[]
├── A. 场景研究审计
│   ├── 环境、实体、工具、任务和调研备注非空
│   ├── Seed 参考工具和任务具有独立说明
│   └── 来源方向与待确认问题可以交给后续调查
├── B. Step 2 收口审计
│   ├── research_refinements 非空
│   ├── source 不遗留 planned/in_progress
│   └── data_need 不遗留 planned/missing
├── C. state 事实审计
│   ├── validation.valid=true
│   ├── records.sqlite 表和 environment.json 一致
│   ├── Filesystem Scope 文件集合与 structure 一致
│   └── Record.file_path 全部闭合
├── D. 来源审计
│   ├── 每个 Raw 恰好属于一个 source_plan 来源
│   ├── 来源映射不越出 source_inventory
│   └── 从磁盘重算每个 Raw SHA-256
├── E. 最终状态审计
│   ├── validation.valid=true 且 errors=[]
│   ├── quality_tier 为 rich 或 not_rich
│   └── 展示 ready/exhausted、采集轮次、画像次数和质量缺口
└── F. 单文件 HTML
    ├── 内嵌 CSS、JavaScript 和审计数据
    ├── 无外部脚本、样式、fetch 或网络依赖
    ├── 支持按最终环境、实体和文件搜索，并按最终数据形态筛选
    ├── 只展示发布后的环境产物，不展示 Step 1/2 中间推理
    ├── 展示最终 Record Set、记录量、字段画像和闭合关系
    └── 展示 Filesystem Scope、实际文件、file_path 索引、来源和校验
```

## 三环境 v2 独立复算（2026-09-01）

代码收紧后直接从最终 SQLite 和 Scope 重算关系、路径、连通分量和丰富度，不复用发布时保存的
等级。三个环境覆盖 XML 文件操作、地理文件和多源源码/标准文档三种形态：

```text
三个最终环境
├── VASTLint
│   ├── 8 个 Record Set / 3258 条记录 / 核心记录 659 条
│   ├── 7/7 关系实际闭合，1/1 文件索引解析 8 个 XML 路径
│   ├── 8 个 XML，包含 1 个明确允许保留的损坏输入
│   └── integrated + rich
├── IBGE
│   ├── 4 个 Record Set / 733 条记录 / 核心记录 702 条
│   ├── 2/2 关系闭合，2/2 文件索引分别定位 GeoJSON 与 SVG
│   ├── 核心业务分量闭合；发布事件有真实 standalone_reason
│   └── integrated + rich
└── OpenZeppelin
    ├── 9 个 Record Set / 1019 条记录 / 核心记录 905 条
    ├── 8/8 关系闭合，2/2 文件索引定位 3 个 Solidity 与 5 个 Markdown
    ├── 所有最终资产处于一个实际连接分量
    └── integrated + not_rich
        ├── 核心需求 verified_deployment_data 已准确收口为 unavailable
        └── 旧 source_plan 把来源证据 HTML/JSON 误列为任务侧必需格式
```

本次验证产物位于：

```text
/home/sunshuo/AgenticDataGeneration/generated/data_gen_v4_validation_20260901/
├── rich/
│   ├── smithery_aleksander_vastlint_42/
│   └── smithery_sidneybissoli_ibge_br_mcp_8/
└── not_rich/
    └── smithery_openzeppelin_25/
```
