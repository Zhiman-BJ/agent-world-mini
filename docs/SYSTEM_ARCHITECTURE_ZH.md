# Agent-World Mini 分阶段架构

## 1. 总流程

本文只说明 `agent_world_mini/`，不包含训练代码。

```text
SeedGen
  -> EnvGen/DataGen
  -> EnvGen/ToolGen（包含工具执行验证）
  -> EnvGen/Assembler
  -> Runtime
  -> TaskGen/DAG-form 或 Program-form
  -> TaskGen/Validation
  -> TaskGen/Export
```

命令行入口是：

```bash
python -m agent_world_mini
```

它调用 `agent_world_mini/run_pipeline.py`。入口只负责串联阶段，不应继续承载数据抓取、工具实现或任务验证算法。

## 2. 最终目录

```text
agent_world_mini/
├── schemas/
│   ├── env_seeds.schema.json
│   ├── environment.schema.json
│   ├── tool.schema.json
│   ├── complete_environment.schema.json
│   ├── 环境种子契约-v1.0.md
│   ├── 环境契约-v1.0.md
│   ├── 工具契约-v1.0.md
│   ├── 完整环境契约-v1.0.md
│   └── validation/
├── utils/
│   ├── config.py
│   ├── io.py
│   ├── llm.py
│   └── search_agent/
│       ├── web.py
│       └── deepseek_harness.py
├── seed_gen/
│   ├── themes.py
│   ├── catalog.py
│   ├── data/
│   │   ├── theme_sources.json
│   │   └── prepared_environments.json
│   └── scripts/
├── env_gen/
│   ├── data_gen/
│   │   ├── run_pipeline.py
│   │   ├── config.py
│   │   ├── steps/
│   │   │   ├── step0_prepare_run.py
│   │   │   ├── step1_research_scenario.py
│   │   │   ├── step2_collect_data.py
│   │   │   ├── step3_integrate_data.py
│   │   │   ├── step4_freeze_environment.py
│   │   │   ├── integration/
│   │   │   └── common/
│   │   └── analysis/
│   │       ├── seed.py
│   │       ├── scenario_research.py
│   │       ├── collection_analysis.py
│   │       ├── artifact_integrity.py
│   │       ├── v2_validator.py
│   │       ├── filesystem_scopes.py
│   │       ├── file_formats.py
│   │       └── checkpoint_schemas/
│   ├── tool_gen/
│   │   ├── compiler.py
│   │   └── designer.py
│   └── assembler.py
├── runtime/
│   ├── engine.py
│   └── sessions.py
├── task_gen/
│   ├── common/
│   │   └── composition.py
│   ├── dag_form/
│   │   ├── graph.py
│   │   └── synthesizer.py
│   ├── program_form/
│   │   ├── steps/
│   │   │   ├── step1_prepare_environment.py
│   │   │   ├── step2_gen_task_solution.py
│   │   │   ├── step3_debug_solution_jsonl.py
│   │   │   ├── step4_ground_truth_jsonl_in_jsonl_out.py
│   │   │   ├── ...
│   │   │   └── step12_final_output.py
│   │   ├── utils/                # 加载、JSONL、受限执行和状态隔离
│   │   ├── schemas/              # Step 2 候选 Schema
│   │   └── run_pipeline.py
│   ├── validation/
│   │   ├── five_run.py
│   │   └── luna_rollout.py
│   └── export/
│       └── batch.py
└── run_pipeline.py

config/
├── api_keys.env.example
└── api_keys.env              # 本地文件，被 Git 忽略
```

`program_form/` 已按 OmniaBench Runner 对齐为 Step 1--12。唯一差异是
Step 1 冻结 DataGen 的真实 `state/`，不再让模型生成 `init_config`。
Step 2--12 依次负责任务/参考解生成、参考解调试、Ground Truth、
Verifier、多 Agent 一致性、公开任务改写、轨迹/状态语义检查、Rubric、
难度评测和最终发布。主要 Prompt 和处理顺序均在对应 Step 文件；
`utils/` 只保留通用机械能力。

## 3. 每个阶段的输入和输出

| 阶段 | 读取 | 输出 | 当前权威实现 |
| --- | --- | --- | --- |
| SeedGen | 人工主题、内置主题或 MCP 目录 | `ThemeSeed` | `seed_gen/themes.py`、`catalog.py` |
| DataGen | Seed JSON、环境契约、Research Agent 配置 | 不带工具的环境数据包 | `env_gen/data_gen/run_pipeline.py` |
| ToolGen | 当前仍是旧 `ResearchBundle`；待适配 DataGen 环境包 | 通过执行验证的 `ToolSpec[]` 和验证报告 | `env_gen/tool_gen/` |
| Assembler | Seed、数据、已验证工具 | `environment_manifest.json` | `env_gen/assembler.py` |
| Runtime | 数据包、工具内部实现、调用参数 | 工具结果、状态快照、outcome | `runtime/` |
| DAG-form | 完整环境、可执行工具 | 候选图、walk、`Task[]` | `task_gen/dag_form/` |
| Program-form | v2 环境包、可执行工具 | 参考程序、Ground Truth、Verifier、Rubric、难度与最终任务 | `task_gen/program_form/` |
| Validation | Task、Runtime、求解模型 | 通过/拒绝/基础设施失败 | `task_gen/validation/` |
| Export | 多环境任务和轨迹 | 数据集与统计文件 | `task_gen/export/` |

`schemas/*.schema.json` 是四类跨阶段 JSON 数据的契约结构示例；它们展示实际输出的字段和嵌套关系，不是环境目录中的业务数据。`schemas/validation/` 中是发布前使用的 Draft 2020-12 校验 Schema，允许在协议结构之上加入代码入口、路径和格式等验证约束。人类可读的字段含义以 `schemas/*契约-v1.0.md` 为准，二者共同构成最终协议：

```text
schemas/*.schema.json                 契约结构
schemas/validation/*.schema.json      发布校验规则
schemas/*契约-v1.0.md                 字段语义和跨字段规则
```

## 4. 阶段职责

### 4.1 `utils/search_agent`

这里存放“如何调用某种调研 Agent”的适配器：

- `web.py`：使用配置的 LLM 和网页工具获取真实数据。
- `codex.py`：对本机 `codex exec` 的最小非交互调用封装；不包含 Agent-World 业务协议。
- `deepseek_harness.py`：启动本机 `dsh`，要求它写出研究数据文件。

它们不决定流水线阶段，也不生成工具。DataGen 只通过统一的 `run()` 接口调用调研 Agent。

### 4.2 `seed_gen`

只负责确定要构建什么主题：

- `ThemeSeed` 保存主题、来源、原始能力线索和数据方向。
- `catalog.py` 获取、整理、查重并选择 MCP 目录项。
- 两个 JSON 文件是内置种子和预处理目录数据。

SeedGen 不抓取业务实体，也不定义最终工具。

### 4.3 `env_gen/data_gen`

`run_pipeline.py` 是 DataGen 唯一权威编排入口。Step 0 选择并锁定完整 Seed；Step 1 从 Seed URL
切入现实场景，同时完善环境描述、实体、工具、任务和数据方向，输出 `scenario_research.json`；
Step 2 根据 Seed 和 Step 1 尚未覆盖的主体承担全部下载。一个 Agent 检查 URL/SHA-256 账本后
直接下载并查看文件；无用候选立即淘汰，归档确有必要时才展开到 Prepared。有效数据填写简单文件卡，
记录用途、覆盖主体和限制。
下载器只验证非空、基础可读性并统计大小、格式、记录数或成员数；Python 自动生成来源报告和 inventory，
不再调用独立画像 Agent或进行字段级 Raw 画像。只有 `supported` 计入退出覆盖率。
领域文件、源码和数据库只在真实任务需要时采集。字段统一、关系发现、全局质量画像、最终 Record Set
和 Filesystem Scope 全部属于 Step 3。

Step 3 让一个 Agent 根据真实 Raw 直接编写最终 `environment.json` 和统一 `provenance/build.py`，
一次物化 `records.sqlite` 与必要的文件 Scope。Python 只返回 Schema、键、关系、路径和重放错误，
Agent 原地修复。Step 4 独立重放、冻结并原子发布；不再有单独的 Step 5。下载收据、硬校验、
阶段收口、哈希和发布均由 Python 完成，不接受 Agent 自报结果。

环境语义不能由字段名启发式决定。Python 负责路径、格式、哈希、类型、数量和
引用覆盖率等可验证事实；声明 Agent 负责资源含义、实体边界和业务关系声明；
Validator 再核对这些声明。`analysis/` 只保留各步骤实际调用的 Seed、场景、文件卡、摘要和最终状态
校验函数，不再保留旧的全局画像、候选推断或计划式集成模块，也不会生成或覆盖最终
`environment.json`。

### 4.4 `env_gen/tool_gen`

这里目前还是旧实现，`compiler.py` 和 `designer.py` 直接读取 `ResearchBundle`，尚未
接入新的 `environment.json + workspace`。因此 DataGen 初版可以独立生成和校验
环境，但不能把“DataGen 已完成”误写成“ToolGen 已经接通”。下一步需要新增明确的
DataGen 环境包加载器，再按工具契约生成和执行验证工具。

- `compiler.py`：把资源、可变实体蓝图和特殊 Python 操作编译为候选工具。
- `designer.py::ToolDesigner`：根据实际实体和关系生成检索、读取、排序、统计、关系查询等候选工具，并做模型筛选。
- `designer.py::ToolValidator`：在 Runtime 中执行测试，删除运行失败的工具以及依赖已失效工具的工具。

工具验证属于 ToolGen 的内部步骤，不是独立流水线阶段。

当前 `ToolSpec` 仍混合公开协议、Runtime 配置、图依赖和测试材料。目标公开协议只包含：

```text
name / description / inputSchema / outputSchema
```

Runtime 内部协议仍待结合最终生成代码确定。

### 4.5 `env_gen/assembler.py`

Assembler 只组装完整环境的交接说明，不生成数据或工具。当前输出 `environment_manifest.json`，包含主题来源、状态契约、保留工具数量、Agent 可见边界和 reset 规则。

### 4.6 `runtime`

Runtime 是 EnvGen、TaskGen 和后续 Evaluation 共用的执行底座：

- `engine.py` 管理 source records、local overlay、workspace 文件和事件。
- `sessions.py` 保证同一 rollout 的多次调用共享一份状态。
- ToolGen 用它执行验证；TaskGen 用它重放参考链；求解评测用它隔离每次 rollout。

Runtime 不是环境生成步骤，不能放进 `env_gen/`。

### 4.7 `task_gen/dag_form`

- `graph.py` 根据工具输出、输入、读写集合建立依赖边并采样 walk。
- `synthesizer.py` 绑定真实参数、执行候选链、裁剪无因果作用的步骤、去重并生成任务文本。

当前 `graph.py` 仍直接读取旧 `ToolSpec` 的内部字段。重构工具协议前，不能单独重写图层。

### 4.8 `task_gen/validation` 和 `task_gen/export`

任务验证属于 TaskGen 的产出门槛：

- `five_run.py` 让求解模型独立尝试任务并判定通过、拒绝或基础设施失败。
- `luna_rollout.py` 为外部求解 Agent 提供 list/start/call/finish/aggregate 命令。
- `export/batch.py` 汇总最终任务、成功轨迹和批量统计。

## 5. API Key

统一配置文件是：

```text
config/api_keys.env
```

创建方式：

```bash
cp config/api_keys.env.example config/api_keys.env
```

读取逻辑集中在 `utils/config.py`。优先级如下：

```text
系统/进程环境变量
  > config/api_keys.env
  > 旧 .env 和 .deepseek-harness.env（仅兼容）
```

OpenRouter 与 DeepSeek Harness 均调用同一个加载器。`config/api_keys.env` 已加入 `.gitignore`，示例文件不包含真实 Key，可以提交。

## 6. 当前仍需继续重构的地方

这次完成的是目录和阶段归属，不是数据协议重写。后续应按这个顺序继续：

1. 冻结四份跨阶段契约结构示例及其 validation/ 校验 Schema。
2. 把 `ToolSpec` 拆成公开工具契约、Runtime 内部实现和 ToolGen 验证材料。
3. 以 OmniaBench 的 Schema/事务检查为基础增强 Runtime，同时保留当前 workspace、fork 和 outcome 能力。
4. 让 DAG 图只读取标准化依赖，不再推断工具实现细节。
5. 用真实 ToolGen `tools.json` 运行 Program-form Step 1--12，并根据实际通过率调整任务生成策略。
