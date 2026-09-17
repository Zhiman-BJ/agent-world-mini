# Program-form 三步任务生成管线

`task_gen/program_form` 是 `task_gen/program` 的兼容软链接。当前模块的职责到生成任务、
验证参考 Solution 并保存可重放 Ground Truth 为止，不包含 Verifier 或任务评测。

## 1. 流程

```text
Step 0  读取 ToolGen binding，验收并冻结环境
   ↓
Step 1  调研真实场景中的工作任务，并映射环境能力
   ↓
Step 2  生成任务与参考 Solution，真实执行、修复、重放并导出评测任务包
```

核心工作流直接位于：

```text
program/
├── step_0_environment_load.py
├── step_1_task_research.py
├── step_2_solution_generate.py
├── contracts.py
├── pipeline.py
├── run_pipeline.py
├── schemas/
└── utils/
```

不再使用 `steps/`。`utils/` 只保留 JSON、环境加载、状态快照和工具执行等机械能力。

## 2. Agent 输入白名单

每一步都在临时工作目录中运行 Agent，只复制完成该步所需的文件；`schemas/` 目录的其他
文档不会自动暴露。

Step 1 只接收：

```text
environment.public.json       # 环境声明和公开工具契约
initial_state.summary.json    # 初态数量、摘要和指纹，不含全部记录
scenario_research.json        # DataGen 已有场景调研，可选
task_research.schema.json     # Step 1 唯一输出结构
validation_feedback.json      # 上一轮校验错误
references/环境契约-v2.0.md
references/工具契约-v1.0.md
```

Step 1 不需要完整 `state/`、任务契约或候选 Schema，因为它只判断现实工作和环境能力，
不选择具体对象，也不写任务或 Solution。

Step 2 接收：

```text
environment.public.json       # 公开环境、记录集合、文件区域和工具 Schema
task_research.json            # Step 1 已通过的现实工作原型
state/                        # 选择真实对象、文件和初态条件的只读副本
candidate.schema.json         # 候选输出结构
generation_request.json      # 数量、难度和执行门槛
validation_feedback.json      # 已接受正文和此前拒绝原因
references/环境契约-v2.0.md
references/工具契约-v1.0.md
references/任务契约-v1.0.md
```

Step 2 必须有完整 `state/`，因为生成 Agent 需要确认记录 ID、文件路径、可形成的关联和
输出目录确实存在；它只能读取该副本，不能把数据库文件或其他内部路径写进公开任务。

## 3. Step 0：ToolGen 交付验收

推荐输入是 ToolGen 的 binding，而不是手工组合环境和工具路径：

```bash
python -m task_gen.program.step_0_environment_load \
  --binding /data/agentworld-toolgen-results/environments/pypi_klayout_7/binding.json \
  --output-dir runs/program/example
```

也可以使用稳定的 `delivery_root + package_id`：

```bash
python -m task_gen.program.step_0_environment_load \
  --delivery-root /data/agentworld-toolgen-results \
  --package-id pypi_klayout_7 \
  --output-dir runs/program/example
```

Step 0 按 `ToolGen下游交付契约-v1.0.md` 检查：

1. binding 符合 `toolgen_delivery_binding.schema.json`，所有路径位于 delivery root 内；
2. binding、`environment.json`、`tools.json` 和工具校验回执的 environment ID 一致；
3. DataGen `validation.json.valid=true`；
4. 正式 `tools.json` 中的每个工具在 `tool_validation.json` 中均为 `passed`；
5. 声明软件 Profile 时，Profile ID 一致且 `python/bin/python` 可执行；
6. 公开工具投影保留 `name/description/usageConditions/inputSchema/outputSchema`，隐藏
   `internal.code`。

输出：

```text
step0_environment.json
baseline_environment/
├── binding.json                 # binding 模式存在
├── delivery.json                # 已解析的 Profile 和交付信息
├── environment.json
├── validation.json
├── tools.json
├── tool_validation.json         # binding 模式存在
└── state/
```

为了兼容历史测试，也可以直接使用 `--environment-package` 和 `--tools-path`；正式生产应
优先使用 binding，避免错误组合环境、工具和软件 Profile。

## 4. Step 1：真实任务调研

Step 1 读取冻结后的公开环境、初始状态摘要和可选的 DataGen 场景调研，并参考环境契约与
工具契约文档。Agent 只研究现实中存在的完整工作和必要条件，不生成具体 benchmark 题目。

每个任务原型用一段 `description` 描述完整工作，再用 `requirements` 列出必要条件，并把
所需能力映射到真实存在的：

```text
record_sets
relationships
filesystem_scopes
tools
```

缺少必要数据或工具时必须填写 `unsupported_requirements` 并设置
`generatable=false`。调研以 8--12 种有明显差异的现实工作类型为覆盖目标，同时调查常规
流程、比较或审计、异常恢复、批量处理和产物复核；来源不足时不得为达到数量编造内容。
`environment_support.tools` 是已确认的主要能力映射，不是 Step 2 的封闭工具白名单。输出为
`step1_task_research.json`。

## 5. Step 2：任务、Solution 和 Ground Truth

Step 2 选择一个 `generatable=true` 的任务原型作为主要现实依据，也可以补充与同一工作闭环
直接相关的调研要求和环境能力。生成 Agent 可以读取隔离的初始状态，但
最终公开任务不能泄露参考答案、内部字段路径、工具名或调用顺序。

每个候选必须提供 `task_resources`：

```json
{
  "record_sets": ["orders"],
  "relationships": [],
  "files": [
    {
      "scope_id": "reports",
      "path": "2026/Q3/summary.json",
      "role": "output",
      "path_is_exact": true
    }
  ],
  "allowed_tools": ["list_orders", "write_report"]
}
```

文件位置使用 `scope_id + Scope 内相对路径`，不得写内部物理前缀
`filesystem_scopes/`。输出文件必须给出精确路径；任务正文也必须自然地告诉求解 Agent
输入和输出位于哪里。

参考 Solution 只能通过 `call_tool(name, arguments)` 使用环境。Python 会：

1. 在独立状态副本中真实执行；
2. 校验工具输入输出 Schema 和统一 success envelope；
3. 失败时把真实错误交给 Agent 修复；
4. 从干净初态重复执行，要求回答、逻辑终态和状态差异稳定；
5. 使用独立 Agent 审查任务、Solution 和资源范围；
6. 最后再执行一次发布重放，并保存完整初态和参考终态目录。

难度不设置固定工具调用次数或工具种类门槛。任务需要形成一个有真实因果依赖的闭环，例如
多证据决策、输入到产物再到回读比较、批量关联处理或逐层诊断；重复查询和无关动作不增加
难度。参考程序还必须让至少一个后续工具参数依赖前序工具结果，并让最终答案由真实工具
结果推导。

独立审查会逐一审计实际工具调用中的所有参数叶子。每个取值必须由公开任务明确给出或唯一
确定，或者来自更早的成功工具结果及任务规定的计算；隐藏初态、Schema 示例、参数默认值和
常识不能单独成为取值来源。审查还会检查任务正文的每项要求能否在工具结果、最终状态或
结构化答案中找到直接证据。

binding 声明软件 Profile 时，每次工具调用都由该 Profile 的 Python 工作进程执行。
上下文提供契约规定的 `environment`、`records`、`scope_root()` 和 `software_root`。

## 6. Step 2 输出

Step 2 输出：

```text
step2_task_solution.jsonl         # 含隐藏 Solution 和完整生成审计
step2_validation.json
tasks.json                        # 外部 task_eval 的正式输入
rejected.json
intermediate/step_5_bundle.json   # 外部 task_eval 的环境与参考轨迹
tasks/<task_id>/initial/
tasks/<task_id>/final/
```

`tasks.json` 每项包含：

```text
schema_version / task_id / environment_id / task_text
output_schema / task_resources
difficulty.tool_calls
initial_state
available_tools[]
reference.answer / reference.tool_calls / reference.final_state
```

这些文件是 Step 2 的生成结果和可重放依据。本模块不会继续生成评分规则或运行求解评测。

## 7. 完整运行

```bash
python -m task_gen.program.run_pipeline \
  --step all \
  --binding /data/agentworld-toolgen-results/environments/pypi_klayout_7/binding.json \
  --output-dir runs/program/klayout_tasks \
  --task-count 3
```

单步范围使用 `0`、`1`、`2`、`0-2` 或逗号组合。已有完整产物会跳过；只有部分产物时
会停止并要求补齐或使用 `--fresh` 重跑。
