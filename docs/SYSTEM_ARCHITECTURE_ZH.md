# 系统架构

## 1. 系统目标与范围

本项目构建可执行的领域任务环境，考察模型理解需求、选择工具和根据实际结果组织调用的能力。
同一环境既可用于模型测评，也可用于采集教师轨迹。测评关注任务是否完成，蒸馏关注哪些真实执行可转为训练数据。
梯度训练、TP/CP/DP 配置和模型服务部署属于外部 `agent_train` 工程，不属于 MCP 或 Harness。

## 2. 总体数据流

```text
场景与真实数据
  │ seed_gen / env_gen.data_gen
  ▼
环境 + 初始状态 + 领域工具
  │ env_gen.tool_gen：生成、测试、依赖准备与发布
  ▼
交付 binding.json
  ├── 环境契约、公开工具表、基线状态
  └── Python Profile 或 Docker runtime 等执行绑定
  │
  ▼
任务设计（task_gen）
  │ 题文、独立初态、参考解与验证记录
  ▼
模型求解（eval → distill runner → Kimi Code）
  │ MCP → Harness → 实际业务工具
  ▼
回答 + 终态 + 原始调用记录
  ├── 独立验收（eval.verifier）→ verdict → 测评统计
  └── 轨迹导出与筛选（distill.dataset）→ 训练 JSONL → 外部 SFT
```

## 3. 分层职责

### 3.1 环境与工具交付

`env_gen/data_gen/` 获取数据、构建记录集合和文件区域，并冻结来源。
`env_gen/tool_gen/` 生成和测试工具，准备依赖，将通过的工具发布为交付包。
`binding.json` 关联环境、工具、验收回执、软件和 runtime；下游通过 `harness.delivery.load_delivery` 加载，
不自行猜测内部路径。`tools.json` 只包含通过工具，不代表所有规划能力均已实现。

### 3.2 任务设计

`task_gen/` 消费已交付环境，产出题文、参考解和验证记录。
Program 三阶段使用参考程序；单作者流程使用业务 MCP 调用列表；Tool-graph 使用关系图与采样链。
三者是不同设计入口，共享环境契约，但不互相替代。详见[任务生成](TASK_GENERATION_ZH.md)。

### 3.3 模型执行与 MCP

`eval run` 复用 `distill/runner.py` 启动 Kimi Code Agent 循环。
Kimi Code 负责模型请求、工具选择与上下文管理；上游可以是不同模型，身份取决于实际请求与部署权重。
MCP 负责传递公开工具定义、参数和返回，不自行实现业务逻辑。

任务 MCP 只提供环境业务工具；公开文件交由受限原生 Read/Grep/Glob 访问。
ToolGen 通用 MCP 另支持资源方法，供交付环境的通用客户端使用。

### 3.4 Harness 与工具后端

Harness 是业务工具的执行契约，负责参数与结果 Schema、资源路径、状态事务和失败恢复。
工具执行后端根据实际调用路径选择：直接受限子进程、bubblewrap 通用沙箱或交付 Docker。
**Harness 不等于 bubblewrap，也不是所有 MCP 调用都经过 bubblewrap。**
完整分支见[运行时与隔离](TOOL_RUNTIME_ZH.md)。

### 3.5 独立验收

求解结束后，Verifier 使用另一份只读证据视图调查回答、初末状态、调用及工具实现，按题文逐项验收。
它不继续求解、不向求解器补结果，也不按参考答案相似度打分。
当前 Verifier 自身运行在 bubblewrap 中，隔离对象是调查 Agent，而不是重新运行业务工具。

### 3.6 蒸馏与训练数据

`distill/` 保留原始请求与事件，导出标准轨迹；`distill/dataset/` 根据正式验收与来源索引构建训练记录。
一次执行可以产生多个上下文窗口，轨迹行数不等于独立任务数。训练监督、packing 和损失计算由外部训练端实现。

## 4. 状态与访问边界

```text
每条任务的运行目录
  ├── model-workspace/  公开文件；求解器 cwd
  ├── private-state/    记录库与路径映射；通过业务工具访问
  ├── raw/              原始请求、事件与调用；不向求解器公开
  └── trajectory.json   执行轨迹

独立验收目录
  ├── evidence/         初态、终态、回答、调用、工具实现；只读
  ├── scratch/          Verifier 调查笔记和复验产物
  ├── logs/             调查请求、事件和错误
  └── verdict.json      验收判定
```

不同任务、不同模型和重试均使用独立状态与输出目录。
作者材料、参考答案和 Verifier 私有反馈不进入求解工作区。
工具返回成功、参考解可执行、Agent 退出和 Verifier PASS 是不同层的结果，不能互相替代。

## 5. 代码地图

| 层 | 主要源码 | 详细说明 |
| --- | --- | --- |
| 交付加载 | `harness/delivery.py`、`env_gen/tool_gen/delivery_contract.py` | [Harness 接入](HARNESS阶段接入说明.md) |
| 任务设计 | `task_gen/program/`、`task_gen/tool_graph/` | [任务生成](TASK_GENERATION_ZH.md) |
| 求解与轨迹 | `distill/runner.py`、`distill/export.py` | [蒸馏](DISTILLATION_ZH.md) |
| 任务 MCP | `task_gen/task_eval_mcp.py` | [MCP 代码地图](MCP与工具调用代码地图.md) |
| 通用交付 MCP | `env_gen/tool_gen/mcp_server.py` | [MCP 代码地图](MCP与工具调用代码地图.md) |
| 工具事务与后端 | `harness/execution.py`、`harness/sandbox.py` | [运行时与隔离](TOOL_RUNTIME_ZH.md) |
| 状态与资源 | `harness/layout.py`、`harness/state.py`、`harness/state_snapshot.py`、`harness/resources.py` | [Harness 接入](HARNESS阶段接入说明.md) |
| 测评与验收 | `eval/` | [测评代码](EVAL_CODE_ZH.md) |
| 训练数据构建 | `distill/dataset/` | [蒸馏](DISTILLATION_ZH.md) |

## 6. 从架构到使用

已有题集直接按[快速上手](QUICKSTART_ZH.md)试跑，无需重新生成环境。
开发工具调用端先看[Harness 接入](HARNESS阶段接入说明.md)，排查部署后端先看[运行时与隔离](TOOL_RUNTIME_ZH.md)。
正式源码修改先回归再部署；活动批次按已冻结源码与协议复现，不因文档更新自动切换。
