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
│   └── models.py
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
│   │   └── generator.py
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

`program_form/` 当前只是扩展位置。现有代码只有 DAG/工具图驱动的任务生成，不能把同一实现复制一份后称为 Program-form。

## 3. 每个阶段的输入和输出

| 阶段 | 读取 | 输出 | 当前权威实现 |
| --- | --- | --- | --- |
| SeedGen | 人工主题、内置主题或 MCP 目录 | `ThemeSeed` | `seed_gen/themes.py`、`catalog.py` |
| DataGen | `ThemeSeed`、Research Agent 配置 | `ResearchBundle` | `env_gen/data_gen/generator.py` |
| ToolGen | `ResearchBundle` | 通过执行验证的 `ToolSpec[]` 和验证报告 | `env_gen/tool_gen/` |
| Assembler | Seed、数据、已验证工具 | `environment_manifest.json` | `env_gen/assembler.py` |
| Runtime | 数据包、工具内部实现、调用参数 | 工具结果、状态快照、outcome | `runtime/` |
| DAG-form | 完整环境、可执行工具 | 候选图、walk、`Task[]` | `task_gen/dag_form/` |
| Validation | Task、Runtime、求解模型 | 通过/拒绝/基础设施失败 | `task_gen/validation/` |
| Export | 多环境任务和轨迹 | 数据集与统计文件 | `task_gen/export/` |

`schemas/models.py` 目前保存旧流水线正在使用的 Python 数据类。新的四份跨阶段 JSON Schema 尚未冻结，因此这里不能被误解为最终协议已经完成。待冻结对象是：

```text
SeedPackage
DataPackage
EnvironmentPackage
TaskPackage
```

## 4. 阶段职责

### 4.1 `utils/search_agent`

这里存放“如何调用某种调研 Agent”的适配器：

- `web.py`：使用配置的 LLM 和网页工具获取真实数据。
- `codex.py`：对本机 `codex exec` 的最小非交互调用封装；不包含 Agent-World 业务协议。
- `deepseek_harness.py`：启动本机 `dsh`，要求它写出研究数据文件。

它们不决定流水线阶段，也不生成工具。`DataGenerator` 选择使用哪个适配器，并把结果统一成 `ResearchBundle`。

### 4.2 `seed_gen`

只负责确定要构建什么主题：

- `ThemeSeed` 保存主题、来源、原始能力线索和数据方向。
- `catalog.py` 获取、整理、查重并选择 MCP 目录项。
- 两个 JSON 文件是内置种子和预处理目录数据。

SeedGen 不抓取业务实体，也不定义最终工具。

### 4.3 `env_gen/data_gen`

`DataGenerator` 接收一个 Seed，调用指定 Research Agent，输出带来源的实体、关系、资源和 overlay 初始数据。

外部 Codex 生成的 `research_bundle.json` 也从这里加载。该路径会跳过调研调用，但不会跳过后续 ToolGen。

### 4.4 `env_gen/tool_gen`

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

1. 冻结四份跨阶段 JSON Schema。
2. 把 `ToolSpec` 拆成公开工具契约、Runtime 内部实现和 ToolGen 验证材料。
3. 以 OmniaBench 的 Schema/事务检查为基础增强 Runtime，同时保留当前 workspace、fork 和 outcome 能力。
4. 让 DAG 图只读取标准化依赖，不再推断工具实现细节。
5. 在独立协议确定后实现真正的 Program-form 任务生成。
