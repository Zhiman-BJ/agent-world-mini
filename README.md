# Agent-World Mini

Agent-World Mini 用真实公开数据构建可复用的 Agent 环境，再基于环境中的工具生成可执行、
可重放的任务。项目当前重点不是一次生成一条任务，而是先构造内容丰富、关系闭合、来源
可审计的环境，使同一个环境能够支持大量不同参数、不同实体和不同调用链的任务。

整个系统以四类正式协议为边界：

- 环境种子：描述业务范围、参考工具和初始任务线索；
- 环境：描述 Raw、Entity、Derived、Output 资源及实体关系；
- 工具：描述公开输入输出 Schema 和环境内部实现；
- 完整环境：把环境资源与工具组合成 ToolGen、TaskGen 可读取的包。

`schemas/*.json` 是给 Agent 阅读的结构示例，`schemas/*契约*.md` 解释字段语义，
`schemas/validation/*.json` 才是程序执行的 JSON Schema。三者职责不同，但表达的正式数据
结构必须一致。

## 当前实现状态

| 模块 | 状态 | 作用 |
| --- | --- | --- |
| `seed_gen/` | 可用 | 保存和校验最终 Smithery Seed，提供正式 `global_id` |
| `env_gen/data_gen/` | 可用 | 调用 Codex 采集真实数据，画像、冻结、声明、校验并发布环境 |
| `env_gen/tool_gen/` | 待完善 | 预留从环境数据生成并验证工具的阶段 |
| `task_gen/program_form/` | 可用 | 从已经包含工具的完整环境生成并重放 Program-form 任务 |
| `task_gen/dag_form/` | 待完善 | 预留 DAG-form 任务生成阶段 |
| `dashboard/` | 可用 | 查看环境的资源层次、文件内容、实体、关系、能力和来源 |

因此，当前最完整的链路是“Seed -> DataGen 环境包”；Program-form TaskGen 也已实现，但它
要求输入环境已经包含通过工具契约的 `tools[]`。当前仓库还没有把 ToolGen 自动接到两者
之间。

## 项目职责图

```text
最终 Seed
   |
   v
DataGen
   职能：获取真实数据，整理实体与关系，验证并发布环境
   产物：environment.json + workspace + provenance
   |
   v
ToolGen（待完善）
   职能：根据环境资源生成工具接口与内部实现，并执行验证
   产物：包含 resources + tools 的完整环境
   |
   v
TaskGen
   职能：生成业务任务与隐藏参考程序，并在隔离环境中重放
   产物：tasks.json + validation.json
```

## DataGen 的检查边界

DataGen 不把“Codex 已经完成”当成通过依据。Agent 负责理解业务、寻找来源、选择数据来源和
整理语义；Python 负责可以从文件事实确定的硬检查。

| 阶段 | 主要检查 | 目的 |
| --- | --- | --- |
| 场景研究 | Seed 身份、主题化完整综合、候选引用、来源线索、保存收据和输入哈希 | 在下载前把简略 Seed 具象化，同时允许公开前景未知或不可能的数据需求被如实保留 |
| 深调与采集 | 精确 URL、Raw 路径、下载完整性、空间预算、下载收据 | 继续验证场景假设，保证 Raw 来自本次真实访问且可追溯 |
| 受控导入 | Entity 结构、字段集合、标量类型、Raw 血缘、导入收据 | 防止程序文件、脏结构或无来源数据进入业务目录 |
| 独立评估 | 文件事实、记录量、字段变化、数据需求、必要领域文件和闭合关系 | 判断环境是否具有可验证的数据充分性；操作组合只作诊断 |
| 冻结声明 | checkpoint 哈希、独立复核、业务文件与采集证据快照 | 防止声明阶段偷偷修改数据来迎合契约 |
| 最终校验 | 资源覆盖、实体 Schema、来源映射、关系闭合、参考工具覆盖、冻结完整性 | 保证最终声明与真实落盘文件一致 |
| 发布 | 最终 assess、质量分层、清理控制文件、目录原子重命名 | 防止半成品出现在正式环境目录 |

DataGen 的完整流程、每项检查和失败语义见
[DataGen README](env_gen/data_gen/README.md)。

## 目录结构

```text
agent-world-mini/
├── schemas/                    四类正式结构示例、契约和 validation Schema
├── seed_gen/                   Seed 数据、读取和维护脚本
├── env_gen/
│   ├── data_gen/               真实数据环境生成管线
│   └── tool_gen/               ToolGen 预留目录
├── task_gen/
│   ├── program_form/           Program-form 任务生成与执行验证
│   └── dag_form/               DAG-form 预留目录
├── utils/search_agent/         Codex CLI 调用封装
├── dashboard/                  三个环境的静态可视化页面
├── scripts/                    环境维护、统计和 Dashboard 快照脚本
└── tests/                      DataGen、TaskGen 和 Seed 脚本测试
```

## 安装

需要 Python 3.10 或更高版本。DataGen 还需要本机能够直接运行并已经认证的 Codex CLI；
默认沿用 `~/.codex/config.toml`。

```bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini
python -m pip install -e .
codex --version
```

## 生成一个数据环境

下面的命令直接读取最终 Seed 数组，不生成 `research_request.json`：

```bash
python -m env_gen.data_gen \
  --seed-path seed_gen/data/smithery_140_v1_0824.json \
  --global-id smithery_theagenttimes_news_1 \
  --schema-path schemas/environment.schema.json
```

默认使用 `gpt-5.6-terra` 和 `high` 推理强度，输出分类发布到：

```text
/mnt/oss-bucket/sunshuo/AgentWorld/environment/data_gen_v3/
├── rich/<global_id>/
├── not_rich/<global_id>/
├── failed/<global_id>-<timestamp>/
└── .building/
```

本地调试时显式指定最终目录：

```bash
python -m env_gen.data_gen \
  --seed-path seed_gen/data/smithery_140_v1_0824.json \
  --global-id smithery_theagenttimes_news_1 \
  --schema-path schemas/environment.schema.json \
  --output-dir /tmp/agent-world/example
```

## 生成 Program-form 任务

输入必须是已经包含 `tools[]` 和 `workspace/` 的完整环境：

```bash
python -m task_gen.program_form \
  --environment-package /path/to/complete_environment \
  --output-dir /tmp/program_tasks \
  --task-count 2 \
  --min-tool-calls 6 \
  --min-distinct-tools 3
```

TaskGen 会隐藏 `tools[].internal.code`，执行候选参考程序，校验每次工具调用的输入输出
Schema，并在全新 workspace 上重复重放。详细说明见
[Program-form README](task_gen/program_form/README.md)。

## 查看环境

```bash
python scripts/build_environment_dashboard.py
python -m http.server 8765 --directory dashboard
```

访问 `http://127.0.0.1:8765/`。页面逐项解释 Raw、Entity、Derived、Output、非实体文件、
实体字段、闭合关系和来源血缘。使用方法见 [Dashboard README](dashboard/README.md)。

## 测试

```bash
python -m pytest -q
```
