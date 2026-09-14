# Program-form 五步任务生成管线

这条管线从一个已经完成 DataGen 和 ToolGen 的环境包生成 Program-form benchmark。
它不让模型凭空编造初始数据库，也不相信 Agent 自己声称“任务完成”。所有 Solution、
工具调用、最终答案和最终状态都要由 Python 在隔离环境中实际执行和验证。

## 1. 五个步骤

```text
Step 1  接收并冻结完整环境
   ↓
Step 2  调研这个现实场景中真实存在的工作任务
   ↓
Step 3  生成任务 + Solution，真实执行、修复、重放并固化 Ground Truth
   ↓
Step 4  生成评分项、答案 Verifier 和状态 Verifier，并运行正负例
   ↓
Step 5  多个独立 Agent 困难测试，区分基础设施失败，返工或发布
```

源码中的主流程与步骤一一对应：

```text
program_form/
├── steps/
│   ├── step1_prepare_environment.py
│   ├── step2_research_real_world_tasks.py
│   ├── step3_generate_task_solution.py
│   ├── step4_generate_scoring_criteria.py
│   └── step5_evaluate_difficulty.py
├── schemas/
│   ├── task_research.schema.json
│   ├── candidate.schema.json
│   └── scoring_criteria.schema.json
├── utils/
│   ├── contracts.py
│   ├── environment.py
│   ├── io.py
│   ├── tool_runtime.py
│   ├── verifier.py
│   └── solver_mcp.py
└── run_pipeline.py
```

Prompt、业务顺序、接受条件、失败处理和输出组装都在对应 `step*.py`。`utils/` 不
决定任务应该怎样生成、怎样修复、什么难度可以发布。

## 2. Step 1 的环境输入

输入是一个完整环境目录：

```text
<environment_package>/
├── environment.json
├── validation.json
├── tools.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/
│       └── <scope_id>/...
└── provenance/
    └── scenario_research.json     # 可选，DataGen Step 1 的场景调研
```

### `environment.json`

它是数据和关系声明，不直接存放业务记录。主要层级为：

```text
schema_version
environment_id
name
summary
description
record_sets[]
  record_set_id
  name / description
  access
  key_fields[]
  fields
    <field_name>
      type
      description
      nullable
      enum                  # 可选
relationships[]
  relationship_id
  from_record_set / from_fields
  to_record_set / to_fields
filesystem_scopes[]
  scope_id
  name / description
  access
```

这些字段告诉后续 Agent：环境里有哪些实体集合、每个字段是什么意思、记录怎样关联、
哪些文件范围可以访问。真实记录位于 `state/records.sqlite`，不是嵌套在声明 JSON 中。

### `validation.json`

这是 DataGen 的验收回执。Step 1 要求 `valid=true`；否则不允许用一个未通过数据校验
的环境生成任务。

### `tools.json`

每个工具具有两组信息：

```text
tools[]
  name
  description
  inputSchema
  outputSchema
  internal.code
```

- `name/description/inputSchema/outputSchema` 是公开工具契约，任务作者和求解 Agent 可见。
- `internal.code` 是 Runtime 执行工具所需的内部实现，绝不写入公开环境投影。
- `inputSchema/outputSchema` 可以继续包含 object、array、properties、items、required、
  oneOf 等任意合法 JSON Schema 层级；Runtime 会递归校验，不依赖固定表结构。

### `state/`

- `records.sqlite` 保存 `record_sets` 对应的真实表和记录。
- `filesystem_scopes/<scope_id>/` 保存声明过的文件资源。
- 每次 Solution 或求解 Agent 执行时，Runtime 都复制一份新状态；所有写操作只落在该
  副本中，不修改 Step 1 的基线。

### `scenario_research.json`

这是可选输入。它来自 DataGen 的场景调研，通常包含现实任务线索、来源 URL 和来源说明。
Step 2 优先复用它，缺少证据时才继续网页调研，避免对同一场景重复从零搜索。

## 3. Step 1：冻结环境

代码：`steps/step1_prepare_environment.py`

### 做什么

1. 读取并校验环境声明、DataGen 回执、状态目录和完整工具契约。
2. 将它们复制到输出目录的 `baseline_environment/`。
3. 将工具投影为不含 `internal.code` 的公开版本。
4. 对 SQLite Record Set 和文件 Scope 生成逻辑状态摘要。
5. 可用时把 DataGen 的场景调研一同冻结。

### 为什么不生成 `init_config`

OmniaBench 的原始 Step 1 需要模型生成初始配置；本项目已经由 DataGen 构造了真实
`records.sqlite + filesystem_scopes`，重新生成会产生第二套互相冲突的数据。因此这里
只冻结权威状态。

### 输出

`step1_environment.json` 的关键字段：

```text
step / status / schema_version
env_id
environment_package             # baseline_environment
package_format
public_environment
  environment                   # 不含真实记录的数据声明
  tools[]                       # 不含 internal.code 的工具契约
initial_state                   # 逻辑状态摘要
scenario_research               # 可选冻结路径
```

## 4. Step 2：调研真实任务

代码：`steps/step2_research_real_world_tasks.py`

本步只回答“现实中这个场景的人会做什么工作”，不生成题目和 Solution。

### Agent 输入

- `environment.public.json`：Record Set、关系、文件 Scope 和公开工具。
- `initial_state.summary.json`：初始状态数量与摘要，不提供隐藏答案。
- `scenario_research.json`：已有调研，可选。
- `task_research.schema.json`：固定输出结构。
- `validation_feedback.json`：前一轮的 Python 校验错误。

### Agent 产出

每个 `task_archetypes[]` 描述一种现实任务原型：

```text
archetype_id / name
role                           # 谁执行
trigger                        # 什么情况下开始
business_goal                  # 最终业务目的
workflow[]                     # 现实工作过程，不是工具调用顺序
required_evidence[]
hard_constraints[]
expected_deliverable
common_failure_modes[]
source_urls[]
environment_support
  record_sets[]
  relationships[]
  filesystem_scopes[]
  tools[]
  unsupported_requirements[]
  generatable
  reason
```

### Python 接受条件

1. 整体严格满足 `task_research.schema.json`。
2. `env_id` 与当前环境一致，原型 ID 不重复。
3. 每个 `source_urls` 都在顶层 `sources[]` 登记。
4. 支持映射中的所有 Record Set、关系、Scope 和工具都真实存在。
5. `generatable=true` 时不能仍有未支持的必要能力。
6. 至少存在一个可由当前环境完整执行的任务原型。

输出为 `step2_task_research.json`。失败轮次写入
`step2_task_research_failures.json`，供下一次修正。

## 5. Step 3：生成任务和 Solution

代码：`steps/step3_generate_task_solution.py`

这是主要的任务生成阶段。Solution 的限制、执行器、修复循环和 Ground Truth 固化都在
同一个 Step 文件中。

### 生成 Agent 输入

- Step 1 的公开环境和一份只读初始状态副本。
- Step 2 已验证且 `generatable=true` 的任务原型。
- 任务数量、最少工具调用数、最少不同工具数、是否必须改变状态等策略。
- 前几轮真实失败原因。
- `candidate.schema.json`。

### 每个候选字段

```text
archetype_id
task_internal                  # 内部审计描述
task_public                    # 求解 Agent 最终看到的自然语言任务
output_schema                  # 求解 Agent 最终答案的严格 JSON Schema
solution_code                  # 隐藏参考程序
```

`task_public` 不能出现工具名、内部字段路径、调用顺序、Python、答案或 Solution 提示。
`output_schema` 必须是闭合 object：字段非空、全部字段 required、
`additionalProperties=false`。

Solution 只允许通过下面的接口操作环境：

```python
result = call_tool("tool_name", {"argument": "value"})
```

不能 import、读取 `state/`、访问 Runtime 对象或硬编码最终答案；最后一条语句必须给
`final_answer` 赋值。

### 实际执行与修复

1. Python 从 Step 1 基线创建新环境副本。
2. 校验 Solution 的 Python 语法和禁用操作。
3. 每次调用工具时校验 inputSchema、outputSchema、访问边界和失败回滚。
4. 校验 `final_answer` 是否符合候选的 output Schema。
5. 失败时把真实异常、工具轨迹和当前代码交给 Agent，只允许修复实现，不允许改任务。
6. 成功后从同一基线干净重放至少两次。
7. 多次执行的答案、最终状态和状态差异必须完全稳定。
8. 独立审查 Agent 检查现实调研依据、任务约束、泄露、执行语义和环境支持。

Agent 自己写“success=true”没有作用，只有上述 Python 执行结果决定是否接受。

### 输出

`step3_task_solution.jsonl` 中每条记录新增：

```text
env_id / env_class_name / environment_package
task_id / archetype_id / task_archetype
task_internal / task_public / output_schema
solution_code_original / solution_code / solution_code_fixed
solution_trace[]
ground_truth
  candidate_answer
  init_state
  final_state
  state_diff
solution_validation
  success
  debug_history[]
  clean_replay_count
  tool_call_count / distinct_tools[]
  state_changed
  semantic_review
```

`step3_validation.json` 保存请求数、接受数和所有拒绝原因。

## 6. Step 4：生成完整评分规则

代码：`steps/step4_generate_scoring_criteria.py`

这一步同时生成三种互补的判断，避免只比较一句最终答案：

1. `rubric_items`：自然语言任务要求是否逐项完成。
2. `answer_verifier_code`：结构化答案是否正确。
3. `state_verifier_code`：环境中的实际业务效果是否正确。

### Agent 输入

```text
task_public / output_schema / task_archetype
ground_truth
solution_trace
总分和 general/task_specific 分值要求
scoring_criteria.schema.json
上一轮 Python 校验错误
```

### Python 接受条件

- Rubric ID 唯一，general 使用 `G` 前缀，task_specific 使用 `T` 前缀。
- 每项只能为 1、2、3 分，两类分数和总分必须精确匹配配置。
- 答案 Verifier 的 Ground Truth 正例必须为 1.0；缺字段、多字段、错值、错类型不能满分。
- 状态 Verifier 的 Ground Truth 最终状态必须为 1.0；错误状态和应变化却未变化的初始
  状态不能满分。
- 两段 Verifier 禁止 import、文件访问和动态代码执行，受到执行时间与行数预算限制，
  且只能返回 0..1。
- 当前状态验证固定为 `exact_final_state`；只有逻辑最终状态完整一致才能满分。

输出 `step4_scoring_criteria.jsonl`，关键新增字段为：

```text
scoring_ready / scoring_validation
rubric_items / rubric_count / rubric_total_score
general_rubric_score / task_specific_rubric_score
verifier_code / state_verifier_code / state_verification
rubric_explanation
```

## 7. Step 5：困难测试、返工与发布

代码：`steps/step5_evaluate_difficulty.py`

### 求解 Agent 能看见什么

每一次都是新的 Agent 和新的环境副本。它只获得：

```text
task_public
output_schema
public_environment.environment
public_environment.tools[]
Agent-World MCP 工具
```

它的 Prompt 不包含现实调研、Solution、Ground Truth、Verifier 或 Rubric。

### 一次有效 Rollout 怎样判分

1. 答案 Verifier 比较 Agent 答案和 Ground Truth 答案。
2. 状态 Verifier 比较 Agent 最终状态、Ground Truth 最终状态和初始状态。
3. 独立 Rubric Judge 根据真实答案、工具轨迹和最终状态逐项打分。
4. 三项都为 `1.0`，本次 Rollout 才算通过。

Rubric Judge 只提交每项 `passed` 和证据。Python 使用 Step 4 固定的 points 重新计算
总分，不相信模型声明的总分。

### 基础设施失败和任务失败的区别

- MCP 未启动、最终状态文件缺失、Agent 超时、评分 Agent 未生成合法文件：基础设施失败，
  自动补跑，不进入难度分母。
- Agent 返回错误答案、没有完成状态变化、Rubric 未满足：有效任务失败，计入难度分母。

如果补跑后仍没有收集到配置数量的有效 Rollout，任务进入返工。有效 Rollout 全部失败
也进入返工，不会被标成“极难任务”；至少达到 `minimum_passing_runs` 才能证明任务既可解
又具有可测难度。

### 输出

```text
step5_difficulty.jsonl          # 所有任务、每次评分和基础设施记录
step5_rework.jsonl              # 未发布任务及明确原因
final/task_gen_final.json       # 通过困难测试的正式任务
```

最终任务明确补齐环境摘要、完整公开环境契约、公开工具契约、初始状态、Ground Truth
答案/最终状态/状态差异、答案/状态 Verifier、Rubric 和难度统计。求解时只应向 Agent
投影其中的公开字段。

## 8. 为什么仍保留 `utils/`

是否放进 `utils` 不以“代码长不长”判断，而看它是否是多个步骤或独立进程共同依赖、且
不包含阶段决策。

| 文件 | 保留原因 | 不允许放入的内容 |
| --- | --- | --- |
| `contracts.py` | Step 3--5 共用字段名和运行参数 | 发布策略、修复顺序 |
| `io.py` | 多个 Step 共用 JSON/JSONL checkpoint 格式 | 某一步的输出组装 |
| `environment.py` | Step 1--3、5 共用环境包加载和公开投影 | 任务选择规则 |
| `tool_runtime.py` | Solution 和 MCP 都需要隔离状态、工具执行、Schema 校验和 diff | Solution 接受条件、难度判定 |
| `verifier.py` | Step 4 要测试，Step 5 要执行同一受限代码 | Rubric 内容和发布门槛 |
| `solver_mcp.py` | 必须由 Codex 作为独立 stdio MCP 子进程启动 | Agent Prompt、重试规则、计分规则 |

任务调研、任务生成、Solution 执行合同、修复、重放、评分生成、独立求解编排、困难度和
发布条件全部留在 Step 文件中。原来的 `reference_program.py` 和 `solver.py` 已删除，
因为它们只服务单一步骤，会把主流程藏到 `utils`。

## 9. 运行

```bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini

python -m task_gen.program_form \
  --step all \
  --environment-package /path/to/complete_environment \
  --output-dir /tmp/program_tasks \
  --model gpt-5.6-terra \
  --task-count 2 \
  --min-tool-calls 6 \
  --min-distinct-tools 3 \
  --difficulty-runs 5 \
  --minimum-passing-runs 1
```

步骤选择支持：

```bash
--step 3
--step 2-4
--step 1,3,5
--step all
```

完整产物已存在时会跳过；一组产物只存在一部分时会停止并指出缺失文件。需要重跑所选
步骤时使用 `--fresh`。

## 10. 完整产物目录

```text
<output_dir>/
├── baseline_environment/
├── step1_environment.json
├── step2_research/
├── step2_task_research.json
├── step3_repairs/
├── step3_reviews/
├── step3_task_solution.jsonl
├── step3_validation.json
├── step4_scoring/
├── step4_scoring_criteria.jsonl
├── step5_runs/
├── step5_difficulty.jsonl
├── step5_rework.jsonl
└── final/
    └── task_gen_final.json
```
