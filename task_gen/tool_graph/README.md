# Tool Graph Task Generation

`tool_graph` 把一个已经生成并验证过的 Agent-World 环境，转换为可以执行、可以
校验、可以交付的任务数据集。流水线的核心原则是：工具链必须先真实执行成功，任务
文本再从真实轨迹中生成，最后由独立校验阶段决定是否进入 `tasks.json`。

当前流水线共有六个阶段：

```text
environment package
        |
        v
Step 0 读取环境与预处理
        |
        v
Step 1 建立工具直接依赖图
        |
        v
Step 2 采样、审查并筛选工具链
        |
        v
Step 3 在隔离 workspace 中真实执行工具链
        |
        v
Step 4 从成功轨迹生成任务文本、参考答案和资源约束
        |
        v
Step 5 组装正式 task，并进行语义与结构校验
        |
        v
tasks.json / rejected.json
```

Step 4 和 Step 5 是两个职责独立的质量门，运行时按 Step 4 再 Step 5 的顺序执行：
Step 4 负责把可执行轨迹转写成自然任务，Step 5 负责判断这个转写是否真的与轨迹
匹配、信息是否足够，并整理为正式数据格式。不能跳过真实执行，也不能仅凭图或任务
文本直接产出正式任务。

## 目录与职责

```text
tool_graph/
├── contracts.py                 # 所有阶段的输入输出契约和公共类型
├── pipeline.py                  # 唯一的阶段编排入口
├── run_io.py                    # Bundle 转换、合并、检查点和最终文件读写
├── llm.py                       # LLM 调用适配、批处理和 JSON 解析
├── step_0_environment_load.py   # 读取并检查环境包
├── step_1_graph_build.py        # 构建直接前置关系图
├── step_2_chain_sample.py       # 采样、去重、多样性筛选和链审查
├── step_3_chain_execute.py      # 填参并隔离执行工具链
├── step_4_task_compose.py       # 生成和反思任务文本等中间字段
├── step_5_task_validate.py      # 语义验收、Schema 校验和 task 组装
├── __init__.py                  # 对外导出包级入口
└── __main__.py                  # 支持 python -m task_gen.tool_graph
```

各 `step_*.py` 只实现本阶段业务逻辑。它们不直接读取整个 Bundle，也不直接写运行
目录；阶段输入由 `run_io` 提取，阶段输出再由 `run_io` 合并和存档。这使得阶段函数
可以单独测试，也能从中间 Bundle 检查点恢复或分析。

## 核心数据模型

### AppendOnlyBundle

`AppendOnlyBundle` 是一次运行中不断增加阶段产物的平铺字典，定义在
`contracts.py` 中：

```python
AppendOnlyBundle = dict[str, Any]
```

它的典型字段会按以下顺序出现：

```text
_step
environment
tool_graph
tasks
sampling_report
cost_report
```

每个阶段的 Bundle 文件保存截至该阶段的完整快照，而不是只保存差量。这样后续阶段
需要 Step 2、Step 3 和 Step 5 的产物时，不必扫描并重新组装多个阶段文件。

`run_io.merge_output` 执行合并规则：

* 普通业务字段只能新增，已有字段重复写入会报错；
* `_step` 始终更新为当前阶段；
* `tasks` 是唯一允许逐阶段更新的业务字段，因为它会依次增加采样、执行、转写和
  验证信息；
* 阶段函数不能绕过该规则直接修改 Bundle。

### contracts.py

`contracts.py` 是阶段之间的唯一数据契约中心，主要内容如下：

| 类型 | 作用 |
| --- | --- |
| `PipelineStep` | 固定的 `step_0` 到 `step_5` 标识，同时用于 Bundle 元数据和检查点文件名 |
| `Config` | 一次运行的完整配置，包含路径、`llm`、`graph`、`planning`、`execution`、`cost` |
| `RunResult` | 流水线完成后的运行目录、合格任务数、拒绝数和成本报告 |
| `*Input` | 各阶段实际需要的输入字段；都包含完整 `Config` |
| `*Output` | 各阶段允许新增或更新的字段 |

各阶段输入只暴露本阶段需要的字段。例如 Step 1 只得到 `config` 和
`environment`，Step 2 得到 `environment` 与 `tool_graph`，Step 3 才得到
`run_dir` 和候选 `tasks`。内部实现细节不进入契约。

### Config

配置覆盖顺序固定为：

```text
代码默认值 < YAML 配置文件 < 命令行参数
```

路径放在 YAML 的 `paths` 下；模型、并发和阶段参数放在对应分组中。常用配置项包括：

```yaml
paths:
  environment_dir: ../artifacts/mcp_test3/bugagent
  schema_dir: ../schemas
  output_root: ../runs/taskgen

llm:
  backend: codex
  model: gpt-5.6-terra
  timeout_seconds: 180
  max_concurrency: 4

planning:
  sample_count: 10000
  review_count: 20
  keep_top_count: 10
  min_chain_length: 8
  max_chain_length: 15
  max_tool_visits: 2
  random_seed: 42
  edge_sampling_probabilities: {"1": 0.2, "2": 0.3, "3": 0.5}
  diversity_lambda: 0.3

execution:
  max_concurrency: 4
  retry_count: 3
  tool_timeout_seconds: 300
```

## pipeline.py 与 run_io.py

### pipeline.py：编排，不做业务判断

`pipeline.run(config_path, overrides)` 是唯一的完整运行入口。它按固定顺序调用：

```python
load_environment
build_graph
sample_chains
execute_chains
compose_tasks
validate_tasks
```

每个阶段由一个统一的内部流程包裹：

1. 用 `run_io.to_*_input` 从 Bundle 提取契约输入；
2. 调用阶段函数得到契约输出；
3. 用 `run_io.merge_output` 合并输出；
4. 用 `run_io.save_bundle` 保存完整检查点；
5. 在 `run.json` 中记录阶段耗时。

`pipeline.py` 不理解工具依赖、任务文本或 workspace 内容。它只负责顺序、失败状态
和阶段计时。

### run_io.py：边界适配和持久化

`run_io.py` 负责所有 Bundle 与文件系统之间的边界工作：

* `load_config`：加载配置并应用默认值、YAML、命令行覆盖；
* `to_environment_load_input`、`to_build_graph_input` 等：从大 Bundle 提取阶段输入；
* `merge_output`：按 append-only 规则合并阶段输出；
* `create_run_dir`、`save_run_meta`、`update_run_meta`：创建和更新运行元数据；
* `save_bundle`、`load_bundle`、`load_latest_bundle`：保存和读取阶段完整快照；
* `append_progress`、`load_progress`：为昂贵批处理提供 JSONL 进度记录；
* `finish_run`：按 Step 5 的 `validation.passed` 机械分流到最终文件。

`run_io` 不执行工具、不调用 LLM、不决定边和任务质量。

## 各阶段规则

### Step 0：读取环境与预处理

文件：`step_0_environment_load.py`

输入只有 `Config`。阶段从 `config.environment_dir` 读取：

* `environment.json`：环境、资源和工具定义；
* `validation.json`：只接受顶层 `status == "passed"`；
* `workspace/`：真实初始文件树。

阶段检查文件是否存在且可解析、环境工具和资源是否为非空数组且 ID 唯一、资源路径
是否安全、资源声明是否能在 workspace 中找到、workspace 是否包含符号链接等。
它不解析专业文件内容，不执行工具；对 `tools[].internal.code` 只检查字段是否存在
且非空，不读取或执行代码内容，也不生成初始 workspace 副本。

输出只新增：

```python
{"environment": <environment.json 的完整 dict>}
```

环境原样保留，包含 `tools[].internal`；后续阶段在生成公开上下文时各自移除内部字段。

### Step 1：建立工具直接依赖图

文件：`step_1_graph_build.py`

阶段针对每个目标工具 `B`，让 LLM 审查其余每个工具 `A` 是否可能是 `B` 的直接
前置调用。模型必须覆盖全部候选并明确返回：

* `weight=3`：强依赖，缺少前置产物或状态时调用不能成立；
* `weight=2`：条件依赖，在明确场景下需要，但并非所有调用都需要；
* `weight=1`：辅助性直接关联，产物可被后续直接消费，但不是成功前提；
* `weight=0`：已审查并判断无依赖，不进入图，但用于完整性检查。

`A -> B` 只表示直接前置关系，不保存传递闭包。若 `A -> B`、`B -> C`，不会因为
可传递性自动增加 `A -> C`。图允许有向环，链中的访问次数和长度限制由 Step 2
处理。

模型只看到环境公开信息、资源、规则以及工具的公开名称、描述和紧凑的输入输出
Schema 投影，不能看到 `tools[].internal` 或 workspace 状态。输出只保存真实存在的
边，每条边形如：

```json
{
  "from_tool": "list_items",
  "to_tool": "create_report",
  "weight": 3,
  "reason": "list_items returns the identifiers required by create_report"
}
```

阶段最后对边做工具名、权重、理由、自环和重复边校验，并稳定排序。

### Step 2：采样、审查并筛选工具链

文件：`step_2_chain_sample.py`

全局初态探索在临时 workspace 副本执行查询；review 的 Codex 可在每条链的独立初态副本中只读探索。处理顺序是：

```text
带权随机游走
  -> 原始链去重
  -> 质量与相似度筛选，最多 review_count（默认 20）
  -> 一次全局初态探索（最多 12 次独立查询、两次模型调用）
  -> LLM 为原始链生成并冻结 objective
  -> Codex 在独立初态副本中探索，按 objective 补全和审查链
  -> review 后链去重
  -> LLM 对 chain + objective 做逻辑性评分
  -> 同逻辑分内按 review 后结构再平衡
  -> 最多 keep_top_count（默认 10）条
```

采样规则：

1. 起点是没有任何 `weight=3` 入边的工具；
2. 后继按配置中的 `edge_sampling_probabilities` 重新归一化采样，权重和概率分开；
3. 单条链中的工具访问次数不能超过 `max_tool_visits`；
4. 到达最大长度或没有合法后继时自然结束，不人为拼接边；
5. 完整有序链去重，记录尝试数、唯一链数和观察到的最长链；
6. 通过共享有向边比例计算相似度，用 `diversity_lambda` 惩罚与已选链过于相似的
   候选，尽量保留不同起点和不同调用关系。

初态探索由模型规划查询，每次在独立副本中运行。只有成功且没有改变 workspace 的结果
进入描述报告；报告同时保留观察和错误，探索失败不阻断流水线，未覆盖状态仍然未知。
`initial_state_report` 在 Bundle 中保存一次，供目标生成、审查、评分、执行和最终校验使用。
报告仅作参考，摘要可能误述且覆盖有限；原始查询和执行结果用于核实事实，不将报告当成状态契约。

目标生成只返回非空 `objective`，不审查、不拒绝、不改链；调用或格式失败记为生成错误。
一个 objective 可以包含多项独立子任务，分别描述结果和范围，不要求共同对象或业务依赖。
唯一优化标准是任务质量、自然性、价值和清晰度，采样链是题材线索，不以原链调用次数限制目标范围。
review 负责补足实现冻结目标的调用，原链够用则保留，否则增补、删除或重排，并尽量减少无关改动；
只返回 `accepted`、`chain`、`reason`，不能替换目标。
只有工具能力或可核实条件使目标无法实现时才拒绝，缺少调用、业务混合本身不构成拒绝理由。
`max_chain_length` 和 `max_tool_visits` 限制采样，不限制 review 为完成任务补全调用；保留规划长度下限。
同工具重复出现可能是处理不同对象或验证新状态，不能据此直接去重；按具体处理和验证用途判断贡献。
无效输出不会回退到未审查的原始链。

review 使用独立的 Codex 入口，不经过禁止工具调用的通用推理包装。它可用只读命令查询副本数据，
仅收集调整链所需的信息，不执行整条链或业务写入。使用 Codex read-only sandbox，并核对副本与源初态签名；
探索日志随该次模型调用保存为 `agent_log`，超时或副本变化会使该候选 review 失败。
objective 并非任务终稿，不必苛求措辞，但必须遵守其最终目标、范围和实质约束，不得随意增删或改换要求。
返回的链仍是逐项执行的固定序列，没有隐含循环或条件跳过；探索信息用于规划，实际执行仍通过公开工具取证。
全局初态调查与具体链无关，目标生成、评分和后续阶段的调用方式不变。

随后由独立 LLM 对修订后的链和目标打 `0–5` 的逻辑性分数。最终选择先按逻辑分从高到低，
同分候选再按共享边相似度平衡 review 后的结构。最终候选包含 `chain`、`objective`、重算的
边权总分、审查结果和逻辑分数；图外边按 0 分计。`sampling_report` 保留目标与审查决策、
探索统计、review 保留/修改/拒绝/失败数、唯一链数及最终边覆盖。

### Step 3：在隔离 workspace 中真实执行

文件：`step_3_chain_execute.py`

对 Step 2 的每条候选链：

1. 从环境源 `workspace/` 复制出任务专属的 `initial/` 和 `final/`；
2. 直接使用 Step 2 的 `objective`，按链顺序为当前工具生成参数；
3. 参数只依据公开环境、目标、初态观察和前序真实结果，并用 `inputSchema` 校验；模型也可返回
   `error` 说明当前依据不足，该判断会携带原因进入参数重试；
4. 在独立子进程中运行 `tools[].internal.code`；
5. 校验返回的成功分支和 `outputSchema`，把真实结果传给后续参数生成；
6. 发生失败时按配置重试，工具失败会从干净的 initial workspace 重新执行整链；
7. 记录成功或失败的完整 attempts，保持候选顺序和原始 `objective`。

执行器提供并发、重试、工具硬超时、结果字节数、写入量和内存限制。超时必须终止
整个进程组，避免工具遗留子进程。成功项保存相对本次运行目录的
`initial_state` / `final_state` 路径；失败项保留错误和尝试记录，不伪造状态。

### Step 4：生成任务文本、参考答案和资源约束

文件：`step_4_task_compose.py`

只处理 Step 3 成功的候选，按四轮 LLM 调用完成：

1. **任务文本**：把 `objective` 和真实成功轨迹转写为自然、明确、结果导向的用户任务；
2. **任务文本反思**：返回 `analyze`、`need_revision`、`task_text`。模型先分析文本
   是否自然、是否保持目标和事实、必要信息是否充分；`need_revision=false` 时忽略返回的
   `task_text`，为 `true` 时才采用不改变目标的非空优化文本；修订须保持对象选择、范围、数量约束和条件的语义，
   不能从本次执行结果反推新需求，不能以完善任务为由扩充义务；反思失败时保留草稿并继续；
3. **参考答案**：只根据最终任务和真实结果描述实际完成的业务结果；
4. **资源约束**：生成 `should_modify`、`can_modify`、`must_not_modify` 三个资源
   ID 列表。

各轮只接收必要上下文：任务初稿和反思接收 `objective`；参考答案接收最终任务与真实结果；
资源约束接收最终任务与公开资源。任务文本保持目标含义和事实边界，描述用户希望得到的
业务结果，不把实现过程或偶然执行结果写成事前要求。

资源列表只能引用环境中已有的 `resource_id`，不能交叉重复；只读资源不能进入
`should_modify` 或 `can_modify`，由代码统一加入 `must_not_modify`。未列出的可写资源保持默认禁止修改。任务初稿、参考答案或资源
约束失败会写入 `compose_error`；反思是增强步骤，失败不阻断候选。

Step 4 输出仍是候选扩充字段，而不是正式 task：

```python
{
    "task_text": str | None,
    "reference_answer": str | None,
    "resource_constraints": {
        "should_modify": list[str],
        "can_modify": list[str],
        "must_not_modify": list[str],
    } | None,
    "compose_error": str | None,
}
```

### Step 5：组装正式 task 并校验

文件：`step_5_task_validate.py`

Step 5 是最后的只读门禁，不重放工具链、不修改 workspace、不修补候选、不去重。它
先将候选组装成固定形状的 `task`，再进行：

1. 前序字段和执行成功状态检查；
2. `tool_calls` 与 `chain` 顺序一致性检查；
3. `objective` 非空检查；
4. 一次独立 LLM 语义审查：
   * `execution_matches_objective`：真实调用和结果是否实现目标；
   * `task_matches_objective`：任务文本是否保持并正确实例化目标；
   * `task_is_usable`：任务是否自然、信息充分，参考答案是否受事实支持，资源约束是否符合目标；
5. 通过前序检查后，用 `task.schema.json` 收集全部结构错误。

只有前序检查、三项 LLM 判断和 Schema 检查全部通过，候选的 `validation.passed`
才为 `true`。语义审查同时接收有限初态观察，未知状态不等于对象不存在。
执行或转写失败只保留根因，失败项不删除，原因写入 `validation.errors`。

正式 `task` 的形状如下：

```json
{
  "schema_version": "1.0",
  "task_id": "task1",
  "environment_id": "example_environment",
  "task_text": "...",
  "difficulty": {"tool_calls": 6},
  "initial_state": "tasks/task1/initial",
  "available_tools": [{"name": "...", "description": "..."}],
  "resource_constraints": {
    "should_modify": [],
    "can_modify": [],
    "must_not_modify": []
  },
  "reference": {
    "tool_calls": [{"tool": "...", "arguments": {}}],
    "answer": "...",
    "final_state": "tasks/task1/final"
  }
}
```

`available_tools` 是环境中所有工具的公开投影，不只包含链上的工具；正式任务不包含
内部代码、工具返回结果、采样分数、执行 attempts 或验证字段。

## 运行产物

一次运行目录位于 `config.paths.output_root` 下，目录名包含时间戳、环境名和模型名：

```text
runs/taskgen/<timestamp>_<environment>_<model>/
├── run.json
├── tasks.json
├── rejected.json
├── tasks/
│   └── task1/
│       ├── initial/
│       └── final/
└── intermediate/
    ├── step_0_bundle.json
    ├── step_1_bundle.json
    ├── step_2_bundle.json
    ├── step_3_bundle.json
    ├── step_4_bundle.json
    └── step_5_bundle.json
```

* `run.json`：配置快照、状态、阶段耗时、最终数量和成本报告；
* `tasks.json`：只包含 Step 5 通过的正式 `TaskArtifact[]`；
* `rejected.json`：保留失败候选的完整中间记录和全部错误；
* `intermediate/step_*_bundle.json`：截至对应阶段的完整平铺 Bundle 快照；
* `tasks/<task_id>/`：Step 3 为该任务保存的初始和最终 workspace。

## 命令行

运行工具代码需要 Linux `bubblewrap`（命令名 `bwrap`）；缺少它时流水线会拒绝执行
未隔离的工具代码。在仓库根目录执行：

```bash
python -m task_gen.tool_graph \
  --config config/tool_graph.yaml \
  --environment-dir artifacts/mcp_test3/bugagent \
  --model gpt-5.6-terra \
  --backend codex
```

可覆盖的命令行选项是 `--environment-dir`、`--schema-dir`、`--output-root`、
`--model` 和 `--backend`。未传入的值继续使用 YAML 或代码默认值。

## 测试与开发约束

运行 `tool_graph` 相关测试：

```bash
python -m unittest discover -s tests -p 'test_tool_graph*.py'
```

新增或修改阶段时应遵守以下边界：

* 先更新 `contracts.py` 中本阶段的输入输出说明，再改实现；
* 阶段函数只处理自己的输入，不直接依赖整个 Bundle 或其他阶段的内部变量；
* 所有文件写入、检查点和 Bundle 合并统一经过 `run_io`；
* LLM 输出在进入后续逻辑前必须做类型、字段和取值校验；
* 公开上下文不得包含 `tools[].internal`、workspace 内容或运行时内部 ID；
* 失败候选要保留原因，不能通过放宽质量标准来换取数量；
* 面向模型的 prompt 可以调整，但契约字段和阶段职责不能靠隐式约定漂移。
