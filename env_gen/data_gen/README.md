# DataGen

DataGen 把一个简略环境 Seed 转换为可离线使用、可审计的 Agent-World 环境数据包。主流程包含
一个准备阶段和四个业务步骤，详见 [`docs/PIPELINE_OVERVIEW_ZH.md`](docs/PIPELINE_OVERVIEW_ZH.md)。

## 流程

```text
Seed
  ↓
Step 0 选择 Seed 并准备运行上下文
  ↓ selected_seed.json + run_config.json
Step 1 场景研究
  ↓ scenario_research.json
Step 2 Agent 自主下载与文件卡
  ↓ source_research.json + source_inventory.json + workspace/raw/
Step 3 Agent 直接建模、清洗并统一构建
  ↓ environment.json + build.py + records.sqlite + Filesystem Scopes
Step 4 独立重放、冻结并原子发布
```

Step 1 把 Seed 具象化，但不下载数据；Step 2 由 Agent 自主循环下载真实文件并记录简单文件卡；
Step 3 由 Agent 直接完成字段、关系、Record Set 和 Filesystem Scope 的全局集成；Step 4 只做最终复验和发布。

## 目录结构

```text
env_gen/data_gen/
├── config.py
├── run_pipeline.py
├── analysis/
│   ├── scenario_research.py
│   ├── collection_analysis.py
│   ├── seed.py
│   ├── artifact_integrity.py
│   ├── v2_validator.py
│   ├── filesystem_scopes.py
│   ├── file_formats.py
│   └── checkpoint_schemas/
├── steps/
│   ├── step0_prepare_run.py
│   ├── step1_research_scenario.py
│   ├── step2_collect_data.py
│   ├── step3_integrate_data.py       # Agent 集成工作流与 Prompt
│   ├── step4_freeze_environment.py
│   ├── integration/                    # Step 3 机械构建与验收命令
│   └── common/
│       ├── constants.py
│       ├── control_io.py
│       ├── download.py
│       └── workspace_files.py
└── docs/
```

## Step 1 产物

`provenance/scenario_research.json` 包含：

- 可快速识别场景的环境简述和详细说明；
- 稳定业务实体及其关键属性；
- Seed 参考工具和调研补充工具的语义说明；
- 典型任务及其目标、过程和结果；
- 后续数据方向、关键 HTTP(S) 来源和待确认问题。

Step 1 使用轻量语义结构。身份、名称唯一性、参考工具与任务覆盖及来源 URL 格式由程序检查；
最终字段、关系和文件工作区要等真实数据取得后再确定。

## Step 2 自主采集

Step 2 不创建最终 Entity 或 SQLite 模型。一个 Agent 在一次会话中读取 Seed 和 Step 1 调研报告，自行
循环选择来源、下载、检查实际内容并更新简单文件卡。原件固定放在 `workspace/raw/`，必要的解包或轻量
预处理结果放在 `workspace/prepared/`；Agent 可以自由使用 curl、wget、API 请求或脚本，不需要调用
专用下载或提交命令。

Agent 优先从同一来源取得相互关联的真实业务数据，只有同源不足时才扩展来源。每个文件卡记录路径、
URL、用途、业务摘要、覆盖主体和限制。只有 `supported` 计入 Seed 整体 90% 和 Step 1 调研整体 75%
最低验收线。这两个数字不是采集目标：达到后 Agent 仍会处理有明确来源的高价值缺口，直到继续搜索
已没有明显收益，再写为 `ready` 并自行结束；低于底线时如实写 `partial` 或 `insufficient_data`。

Python 只核对最终路径，补充哈希、大小、格式和粗略数量，并派生 Step 3 兼容产物。字段规范化、关系
发现和全局质量判断全部留给 Step 3。

## Step 3 直接集成

Step 3 读取 Step 2 的实际样本，由 Agent 直接维护 `environment.json` 和唯一的
`provenance/build.py`。控制器只提供三个命令：

```text
build      无网络运行统一 build.py，一次生成全部 state
assess     校验 Schema、SQLite、键、关系、Scope，并独立重放
finalize   验收通过后记录 Step 3 收口
```

Step 2 用文件卡把 Step 1 的实体、工具和任务连接到真实来源。Step 3 不重新下载，也不生成单独的
integration plan；最终结构由 `environment.json` 表达，所有转换由一个可重放脚本表达。Agent 的
完整集成工作流和 Prompt 都定义在 `step3_integrate_data.py`，`integration/` 目录只保留无业务语义的
`build / assess / finalize` 命令实现。

## 运行态与发布数据

```text
<environment_root>/
├── environment.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/<scope_id>/
└── provenance/
    ├── raw/
    ├── build.py
    ├── integration_receipt.json
    └── source_manifest.json
```

采集期间只有 `workspace/raw/`；它在发布时冻结到 `provenance/raw/`，不会暴露给任务。结构化记录
统一进入 `records.sqlite`，需要工具直接处理的文件或项目目录进入命名 Scope。Record 可以用声明了
`filesystem_path` 的顶层字段保存 Scope 相对路径。v2 不再使用 `workspace/entities/`、
`workspace/derived/` 或 `reports/` 作为最终资源类型。

## 验收边界

Step 2 负责真实业务数据的丰富度和主体覆盖，只有达到 Seed 90% 与 Step 1 75% 最低线的 `ready`
结果才能自动进入 Step 3。Step 3 不再重复制作全局画像，而是检查最终 state 的 SQLite 完整性、
表列与字段类型、唯一键、关系、文件引用、Scope 文件树以及统一构建重放。Step 4 再独立重放一次，
并将覆盖统计、Raw 哈希、build.py 哈希和最终状态摘要写入紧凑的 `integration_receipt.json`。

## 运行

```bash
PYTHONPATH=. python -m env_gen.data_gen \
  --seed-path seed_gen/data/smithery_140_v1_0824.json \
  --global-id smithery_sidneybissoli_ibge_br_mcp_8 \
  --schema-path schemas/environment.schema.json \
  --output-dir /tmp/ibge-env \
  --overwrite
```

Web Search 默认开启，可用 `--no-enable-web-search` 关闭。Step 2 Agent 可直接使用网络下载公开数据。

## 验证

```bash
PYTHONPATH=. python -m compileall -q env_gen/data_gen
PYTHONPATH=. pytest -q
```
