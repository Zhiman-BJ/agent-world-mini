# Tool Graph worktree 修改工作报告

## 1. 对照范围

本报告比较以下两个代码状态：

- 修改前基线：`a7fab119`（创建独立 worktree 时的 `main`）
- 当前分支：`feat/tool-graph-semantic-stability`
- 当前提交：`0e8b451`
- worktree：`.worktrees/tool-graph-semantic-stability`

基线之后共有 10 个提交，其中 8 个实现或修正建图逻辑，1 个实验报告提交，1 个测试精度修正提交。

代码改动集中在：

- `task_gen/tool_graph/contracts.py`
- `task_gen/tool_graph/step_1_graph_build.py`
- `task_gen/tool_graph/step_2_chain_sample.py`
- `scripts/export_tool_graph_viewer.py`
- `task_gen/tool_graph/README.md`
- `tests/test_tool_graph_steps.py`
- `tests/test_tool_graph_viewer.py`

相对基线，上述生产代码、文档和测试合计增加 1421 行、删除 402 行。LLM 调用留档功能已经存在于基线，不属于本 worktree 新增内容；本轮实验只是使用它保存了完整 prompt、answer、step、模型、耗时和调用状态。

## 2. 修改前的核心问题

修改前 Step 1 对每个目标工具 B 只调用一次模型。模型同时完成以下工作：

1. 判断候选工具 A 与 B 是否存在直接关系。
2. 判断关系属于 1、2、3 中哪一级。
3. 编写判定理由。

模型只返回：

```json
{
  "dependencies": [
    {
      "from_tool": "A",
      "weight": 3,
      "reason": "..."
    }
  ]
}
```

最终 `tool_graph` 也只是一个边数组。这个结构无法区分：

- A 完成后，B 是否适合作为直接下一跳。
- B 在执行前是否必须拥有 A 产生的动态数据或状态。
- 随机采样时希望多大概率选择该边。

Step 2 又将“存在 weight=3 入边”解释为“目标工具不能作为起点”，实际把 3 级边当成了硬前置。

例如：

- `update_bug_report -> get_bug_report` 是合理的强直接关系，因为更新后立即读取可以检查新状态。
- 但 `get_bug_report` 本身不要求先更新，完全可以直接读取一个已有缺陷。
- 旧代码仍会因为这条 3 级入边禁止 `get_bug_report` 作为起点。

旧结构也无法表达复合前置。假设目标 C 同时需要 A 产生的 `bug_id` 和 B 产生的 `test_case_id`，两条 3 级边无法说明 A、B 必须同时出现；同样无法表达 D 可以单独提供两类信息，因而能够替代 A+B。

## 3. 当前图的数据契约

`BuildGraphOutput.tool_graph` 已从边数组改为：

```json
{
  "edges": [
    {
      "from_tool": "A",
      "to_tool": "B",
      "weight": 1,
      "reason": "..."
    }
  ],
  "prerequisites": [
    {
      "to_tool": "C",
      "any_of": [
        {
          "all_of": ["A", "B"],
          "reason": "A 和 B 分别准备 C 所需的两项运行时信息"
        },
        {
          "all_of": ["D"],
          "reason": "D 可以单独准备 C 所需的全部运行时信息"
        }
      ]
    }
  ]
}
```

各字段职责如下：

- `edges`：只保存可以直接相邻调用的关系，不保存 `weight=0`，不生成传递闭包。
- `weight`：只表示直接下一跳关系强度，不表示硬前置、采样概率或模型置信度。
- `prerequisites`：只表示执行目标工具前必须已经满足的调用历史。
- `any_of`：其中任意一个方案满足即可。
- `all_of`：一个方案中的全部来源工具都必须在历史中出现。
- `reason`：边和每个完整前置方案都必须说明具体依据。

当前等级定义是：

- 3：A 产生或改变的具体值、实体或状态被 B 直接使用。
- 2：没有强数据交接，但两个工作块存在明确、直接、常规的业务衔接。
- 1：A 的确定结果会弱地影响 B 的输入、范围、判断或验证方式。
- 0：无直接关系，只用于表明模型已经审查该候选，不写入最终图。

## 4. 当前 Step 1 完整执行逻辑

当前 `build_graph()` 的运行路径是：

```text
完整 environment
  -> 公开工具紧凑视图
  -> 环境公开上下文
  -> 第一轮：提取每个候选对的事实
  -> 本地校验第一轮结果
  -> 第二轮：决定边权并组合 prerequisite
  -> 本地校验第二轮结果
  -> 去重、稳定排序、组装 tool_graph
```

### 4.1 构造公开工具视图

`_compact_tool_view()` 只复制：

- `name`
- `description`
- `inputSchema`
- `outputSchema`

它不复制 `tools[].internal`，因此模型看不到工具实现代码。

输入 Schema 被压缩为参数名称、类型、是否必填、枚举值和一层嵌套字段名。数组或对象输入保留一层内部字段，是为了避免丢失 `account_id`、`test_case_id` 等真正影响依赖判断的信息。

输出 Schema 只提取成功分支的 `data`，并扁平化为类似：

```text
items[].run_id
items[].test_case_id
count
```

展开深度最多为 3。失败分支不会发送给建图模型。

`_environment_context()` 只发送环境的：

- `name`
- `description`
- `resources`
- `rules`

当前 Step 1 不读取 workspace 初态，不执行工具，也不分析 `internal.code`。

### 4.2 按目标工具覆盖全部候选

每个工具轮流作为目标 B，其余所有工具作为候选 A。BugAgent 有 25 个工具，因此每一轮需要判断：

```text
25 个目标 x 每个目标 24 个候选 = 600 个有向候选对
```

Prompt 要求每个候选恰好返回一次。正关系返回相应内容，无关系也必须显式返回。这样代码可以区分“模型审查后认为没有关系”和“模型根本漏审了这个候选”。

## 5. 第一轮：只提取事实

第一轮不允许模型分配 weight，也不允许模型组合 prerequisite。每个候选必须输出以下 8 个字段：

```json
{
  "from_tool": "A",
  "immediate_next": true,
  "intermediate_tool_required": false,
  "connection": "required_input",
  "value_origin": "selected",
  "input_availability": "runtime_only",
  "evidence": "A 返回的动态标识可直接填入 B 的必填输入",
  "condition": null
}
```

### 5.1 `immediate_next`

判断 A 成功后，不调用其他工具时，B 是否能够成为合理的下一次调用。

它判断的是“是否存在公开契约支持的直接路径”，不是 B 是否为最常见或最推荐的下一步。从 A 返回的列表中选择一个元素，以及填写普通人类可决定的文本，不算调用中间工具。

### 5.2 `intermediate_tool_required`

判断 A 与 B 之间是否必须调用另一个工具完成查询、验证、转换、解析或目标对象发现。

如果 A -> B -> C，而 A 的结果必须经过 B 处理后才能用于 C，那么 A -> C 应判断为非直接关系。

### 5.3 `connection`

只能从以下关系机制中选择一项：

- `required_input`：A 的结果可填入 B 的必填输入。
- `optional_input`：A 的结果可填入 B 的可选输入。
- `required_state`：A 创建或建立了 B 本次执行所需的实体或状态。
- `state_observation`：B 直接读取、核验或呈现 A 刚改变的状态。
- `workflow_transition`：没有字段交接，但工作块存在明确直接衔接。
- `semantic_influence`：A 的具体结果会影响 B 的参数、范围或验证方式。
- `none`：以上均不成立。

### 5.4 `value_origin`

记录 A 提供的值或实体怎样产生：

- `generated`：A 新生成。
- `selected`：从 A 新返回的候选集合中选择。
- `derived`：根据 A 的结果计算或推导。
- `echoed`：A 只是原样返回调用 A 时已有的输入。
- `not_applicable`：当前关系不涉及值来源。
- `unknown`：公开契约无法确认来源。

这个字段解决了“查询工具回显 ID 被误判为新动态值”的问题。查询一个指定缺陷后返回同一个 `bug_id`，属于 `echoed`；列出多个缺陷后从结果中选择一个 `bug_id`，属于 `selected`。

### 5.5 `input_availability`

记录 B 所需信息是否必须运行工具才能取得：

- `runtime_only`：动态标识、句柄、实体或状态只能在工具运行后取得。
- `task_input`：名称、标题、描述、分类、组件、目标状态、筛选条件等普通业务信息，可以自然写进任务描述。
- `not_applicable`：当前关系不涉及输入或所需状态。
- `unknown`：公开契约无法判断。

这里判断信息的固有可见性，不判断当前路径恰好怎样取得信息。即使某个名称是从 A 的结果中选择的，只要它本质上可以作为自然任务信息提供，仍应是 `task_input`。

### 5.6 `evidence` 和 `condition`

`evidence` 必须指出 A 的具体输出或状态怎样被 B 使用，不能只写字段同名、类型相同、共享资源或主题接近。

`condition` 只在关系需要额外业务条件时填写，否则为 `null`。当前代码只校验其类型，没有在 Step 2 中执行该条件。

## 6. 第一轮本地校验

`_validate_assessments()` 不依赖模型自律，执行以下硬校验：

1. 每项字段必须与 8 字段集合完全一致。
2. `from_tool` 必须来自候选集合，不能重复，也不能是目标自身。
3. 两个直接性字段必须是真正的布尔值。
4. `immediate_next=true` 与 `intermediate_tool_required=true` 不能同时出现。
5. 三个分类字段必须使用代码中列出的合法枚举。
6. `evidence` 必须是非空字符串。
7. `condition` 必须是字符串或 `null`。
8. 所有候选必须完整覆盖，漏审一个即失败。

对 `state_observation` 另有固定约束：

```text
immediate_next = true
intermediate_tool_required = false
value_origin = not_applicable
input_availability = not_applicable
```

原因是状态观察边的依据是“A 改变了状态，B 随后读取这个状态”，不是 A 输出了或回显了某个输入值。

## 7. 第二轮：边权和 prerequisite

第二轮再次接收公开环境、目标和候选工具，同时接收第一轮已经通过校验的事实表。

输出分为：

```json
{
  "decisions": [
    {
      "from_tool": "A",
      "weight": 3,
      "reason": "..."
    }
  ],
  "prerequisite_alternatives": [
    {
      "all_of": ["A", "D"],
      "reason": "..."
    }
  ]
}
```

第二轮先根据第一轮事实决定是否连边：

```text
immediate_next=false             -> weight=0
intermediate_tool_required=true  -> weight=0
connection=none                  -> weight=0
```

其他候选再判断 1、2、3 级。

### 7.1 当前代码执行的权重上限

`_validate_decisions()` 当前执行：

```text
semantic_influence            <= 1
workflow_transition           <= 2
optional_input                <= 2
value_origin=unknown          <= 1
required/optional + echoed    <= 1
state_observation             == 3
```

因此：

- 上游只回显已有 ID 时，不能仅凭这个 ID 判为强交接。
- 状态写入后立即读取必须保持 3 级，不能因定位 ID 是回显而降级。
- 模糊语义影响不能被提升成 2 或 3 级。

但是当前验证器主要设置“上限”，没有为 `required_input + selected/generated/derived` 设置确定性等级。相同第一轮事实仍可能被第二轮判成 1、2 或 3，这是目前边权漂移的主要来源。

### 7.2 prerequisite 的准入条件

一个来源工具只有同时满足以下条件，才能进入 prerequisite 方案：

```python
connection in {"required_input", "required_state"}
input_availability == "runtime_only"
value_origin in {"generated", "selected", "derived"}
```

同时还必须：

- 来源到目标已经存在 `weight > 0` 的直接边。
- `all_of` 是非空且无重复的工具名数组。
- 所有工具名合法。
- 每个方案理由非空。
- 重复方案必须在完整校验后再去重。

这个设计明确排除：

- 只回显输入的工具。
- 普通任务信息。
- 可选输入。
- 语义影响和工作流转移。
- 仅用于观察 A 刚完成变更的 `state_observation`。

## 8. 模型失败与重试

`_run_round()` 统一执行两轮请求：

1. 按目标工具批量调用 LLM。
2. 保留每个目标对应的结果顺序。
3. 解析失败、调用异常或本地校验失败的目标记为 invalid。
4. 只把 invalid 目标批量重试一次。
5. 第二次仍失败时，带目标工具名终止整个 Step 1。

当前重试只存在于一次进程运行内部。它不会把每个目标成功结果独立持久化为可恢复检查点，因此长时间服务拥塞时，第二次失败仍会丢掉同轮其他目标已经完成的工作。

LLM 调用本身会写入 `llm_calls.jsonl`，包含完整 prompt、answer、step、model、status、duration 和 usage。这个留档能力在创建 worktree 前已经实现，本轮没有修改。

## 9. 图的最终装配

`_assemble_graph()` 只负责：

1. 按 `(from_tool, to_tool)` 去重。
2. 将边按权重降序、来源工具名和目标工具名稳定排序。
3. 按环境工具顺序组装 prerequisite。

它不执行：

- 传递闭包生成。
- 传递边自动删除。
- 无环检查。
- 连通性修补。
- 根据图形态强行补边。

图允许出现环，因为状态更新、读取以及并列工作块之间可能合理地存在双向直接关系。

## 10. 当前 Step 2 如何使用图

修改前 `_graph()` 返回邻接表和每个工具的 3 级入边集合。修改后它返回邻接表和标准化 prerequisite。

### 10.1 起点选择

修改前：

```python
roots = 没有 weight=3 入边的工具
```

修改后：

```python
roots = 没有 prerequisite 的工具
```

因此，拥有强状态观察入边但本身可以独立执行的查询工具仍可作为起点。

### 10.2 下一跳过滤

每次随机游走选择下一工具前，除了检查单工具访问次数，还会检查：

```python
not options or any(option.issubset(visited) for option in options)
```

也就是：目标没有 prerequisite 时直接允许；否则只要某个 `all_of` 方案已经完全包含在历史调用集合中，就允许进入。

例如：

```json
{
  "to_tool": "link_test_case_to_bug",
  "any_of": [
    {"all_of": ["create_bug_report", "create_test_case"]},
    {"all_of": ["list_bug_reports", "list_test_cases"]}
  ]
}
```

历史中只有 `create_bug_report` 时不会放行。`create_bug_report` 和 `create_test_case` 都出现后，第一个方案满足；或者两个 list 工具都出现后，第二个方案满足。

### 10.3 权重与采样概率仍然分开

配置保持：

```yaml
edge_sampling_probabilities:
  "1": 0.2
  "2": 0.3
  "3": 0.5
```

这些值是同一节点多条合法出边之间的相对采样权重。代码不会把 `weight=3` 直接解释成 100% 选择，也不会用它判断硬前置。

链分数仍等于相邻边权之和，用于候选排序；多样性选择仍使用共享边相似度惩罚。

### 10.4 LLM review 后重新校验

链 review 允许模型在有充分公开契约依据时调整工具顺序，甚至加入图中没有的相邻边。修改后，review 结果必须再次从头检查 prerequisite；如果修改后的链违反历史约束，则放弃修改并保留原链。

## 11. 可视化兼容

可视化导出脚本现在：

- 接受新的 `{edges, prerequisites}` 图结构。
- 继续读取历史 bundle 中的旧边数组，并将其 prerequisite 视为空。
- 在节点详情中显示该工具的 prerequisite 历史约束。
- 将图例从“直接依赖”改为“直接下一跳”，避免再次把边和硬前置混为一谈。

生产采样代码只接受新结构，不提供静默兼容，防止旧图进入新流程时悄悄丢失硬前置语义。

## 12. 修改过程中的几次回归及修复

### 12.1 所有 echoed 关系被统一降级

第一版验证器只要看到 `value_origin=echoed` 就把权重限制为 1，导致 `update_bug_report -> get_bug_report` 这类写后读取也被误伤。

修正后，echoed 上限只作用于 `required_input` 和 `optional_input`；`state_observation` 的强度来自状态变化，必须为 3。

### 12.2 业务信息被误判为运行时信息

模型一度把“当前路径从列表中选出的名称”都标记为 `runtime_only`，从而将名称、组件等普通信息加入硬前置。

后续新增 `input_availability`，并明确该字段判断信息本身能否自然写入任务，而不是判断当前路径恰好从哪里取得它。

### 12.3 状态观察被加入 prerequisite

模型曾把 `link_test_case_to_bug -> list_test_case_links` 同时判断成状态观察边和硬前置。实际上 list 工具可以直接查看已有关系，不要求历史中必须先新建关系。

Prompt 和验证器均已明确：`state_observation` 可以是 3 级边，但不能进入 prerequisite；只有 `required_state` 可以作为状态型硬前置。

### 12.4 状态观察非法组合未被第一轮拒绝

复审发现模型可以同时返回 `state_observation`、`value_origin=selected` 和 `input_availability=runtime_only`，使第二轮面对无法一致解释的事实。

当前第一轮校验已要求状态观察使用两个 `not_applicable`，并要求直接相邻。

### 12.5 重复 prerequisite 可能吞掉非法理由

原实现可能先识别重复组合，再跳过后续字段校验。当前实现先校验完整方案及理由，再执行规范化去重。

## 13. 提交记录

| 提交 | 内容 |
| --- | --- |
| `8852499` | 将图拆成 `edges + prerequisites`；Step 1 改为两轮；Step 2 执行 DNF 前置约束；更新可视化和测试 |
| `1cb515b` | 在 Prompt 中显式给出 `value_origin` 合法枚举 |
| `48a0ca6` | 增加关系等级上限、新实体规则和文本语义边界；改为批量重试非法目标 |
| `7d088a5` | 避免 echoed 限制误伤真实状态观察关系 |
| `5c06cd7` | 新增 `input_availability`；限制 prerequisite 只能使用真实运行时来源 |
| `3e09bc1` | 按信息本质判断可见性；按是否需要技术中间步骤判断直接性 |
| `d702535` | 禁止 `state_observation` 进入 prerequisite |
| `bdbce11` | 拒绝非法状态观察组合；重复前置先校验后去重；修正 Prompt 字段计数 |
| `3c2abda` | 提交 BugAgent 稳定性实验报告 |
| `0e8b451` | 将状态观察测试改为从合法基线逐项破坏，排除假阳性 |

## 14. 实验结果

实验环境为 BugAgent，25 个工具，每轮覆盖 600 个候选对，使用 `gpt-5.6-terra`。

| 指标 | 修改前双跑 | 中间修订双跑 | 修订后可用图 A/B |
| --- | ---: | ---: | ---: |
| 图 A / B 边数 | 146 / 138 | 152 / 145 | 163 / 156 |
| 全边集合 Jaccard | 0.821 | 0.868 | 0.899 |
| 共同边同权率 | 77.3% | 87.0% | 86.8% |
| 3 级边 Jaccard | 0.739 | 0.811 | 0.778 |
| prerequisite 目标交/并 | 9 / 11 | 9 / 11 | 9 / 9 |
| prerequisite 方案 Jaccard | 0.927 | 0.783 | 0.940 |
| 四项事实完整一致率 | 66.3% | 69.2% | 88.2% |

修订后图 A 的权重分布为 `1:74、2:19、3:70`；图 B 为 `1:72、2:26、3:58`。修改前两图分别有 94 和 99 条 3 级边，说明新版确实减少了大量被错误提升的强边，但没有消除全部错误。

上述最后两张可用图并非当前最终 Prompt 在同一提交下完成的严格双跑：图 A 在 `3e09bc1` 后完整生成；图 B 主体也在最后两项澄清前生成，唯一不合规目标在 `d702535` 后定点恢复。后续完整重跑受到共享 Terra 服务持续超时影响。因此这些数据可以说明主要修改方向有效，不能证明当前提交的最终重复稳定性正好等于表中数值。

## 15. 已确认的效果

### 15.1 关系等级、硬前置和采样概率已经在代码中分开

这是本轮最重要且已经完成的结构修正。Step 2 不再从 3 级入边推导起点或执行资格。

### 15.2 回显 ID 不再自动成为强交接或硬前置

例如：

- `get_bug_report -> update_bug_report` 在两张可用图中均为 1。
- `get_test_case -> mark_test_case_review_flags` 在两张可用图中均为 1。

### 15.3 写后读取仍保留为强关系

例如以下关系在两图中均为 3：

- `update_bug_report -> get_bug_report`
- `classify_bug -> get_bug_report`
- `link_test_case_to_bug -> get_bug_report`

### 15.4 普通业务信息不再进入硬前置

最后两份图中 `create_test_case` 和 `create_test_suite` 均没有 prerequisite。名称、描述、组件、分类等没有再被当成必须先查询才能取得的全局前置。

### 15.5 非传递直接关系保持

两图都没有 `list_test_runs -> classify_bug`。测试运行列表必须经过失败详情、缺陷创建等中间处理才能用于缺陷分类，因此没有因为同属一个长任务而直接跨阶段连边。

## 16. 最新语义审核发现的问题

三组子代理分别审核了业务合理性、边权等级和 prerequisite。它们没有修改代码。

### 16.1 当前 Step 1 看不到初态和可达状态

`list_test_case_review_candidates` 在当前 BugAgent 初态中为空，且现有工具只能增加 link、不能删除 link，也不能创建新的失败运行，因此它在所有可达状态中持续为空。

两张图仍围绕它保留了 7 条依赖 `items[]` 的出边，并在 5 个 prerequisite 方案中把它当作 `test_case_id` 来源。原因不是模型忽略了已发送的信息，而是 Step 1 根本没有把 workspace 初态和工具实际读写行为发送给模型。

`list_comments` 也存在类似问题：初态为空，但 prerequisite 只检查工具是否调用过，不检查结果是否非空，因此空查询也可能错误解锁后续工具。

### 16.2 prerequisite 只证明调用历史，不证明数据流

当前 Step 2 使用工具名集合判断 `all_of` 是否满足。它不知道：

- 来源工具是否成功返回了非空实体。
- 来源工具返回的具体对象是否被后续参数使用。
- 两个来源是否提供了同一个业务对象上下文。
- 某个返回枚举值是否被目标 Schema 接受。

所以 prerequisite 是结构级历史约束，不是参数级执行证明。

### 16.3 仍有 37 条边存在或权重不一致

两张可用图共同存在 151 条边，其中 131 条同权；另有 20 条共同边不同权，12 条只在图 A，5 条只在图 B。

主要原因是第二轮仍重复解释第一轮事实。例如第一轮同样判断为 `required_input + selected`，第二轮可以将它判成 1、2 或 3。当前校验器只有上限，没有统一映射或下限。

### 16.4 三条指向 `create_test_case` 的 3 级边错误

以下三条在两张图中均为 3：

- `get_test_reports_failures -> create_test_case`
- `list_test_cases -> create_test_case`
- `list_test_case_review_candidates -> create_test_case`

模型第一轮并没有简单声称旧 `test_case_id` 可以复用。它的实际判断是：

```text
失败记录或已有测试用例中的 name/component
  -> 可以填写 create_test_case 的必填 name/component
  -> connection=required_input
  -> value_origin=selected
  -> input_availability=task_input
```

第二轮随后把“可以填写一个必填字段”错误升级成“3 级强交接”，尽管理由中明确承认新的 `test_case_id` 和其他字段仍由调用方提供。

当前代码之所以接受，是因为：

```text
required_input + selected + task_input
```

没有权重上限。`task_input` 当前只负责阻止该来源进入 prerequisite，没有阻止它成为 3 级边。

这说明当前实现只解决了“普通信息不能成为硬前置”，尚未解决“普通信息不能成为强直接交接”。

### 16.5 部分 reason 与内部实现不一致

`create_test_case` 的 `test_case_id` 实际来自调用参数并原样返回，重复 ID 会失败，但多条 reason 错写成该工具“生成 test_case_id”。

图 A 的 4 个 `link_test_case_to_bug` prerequisite reason 还把两个来源各自提供的字段写反；图 B 已修正这些文本。

### 16.6 当前数据与 Schema 存在模型不可见的冲突

当前已有 link 的 `relation` 为 `reported_by`，但 `link_test_case_to_bug` 只接受 `reproduced_by`、`regression_for` 和 `related`。模型只看 Schema、不看当前数据，因此误认为已有 link 可以作为新建 link 的完整直接材料。

## 17. 尚未实现的修改

以下内容已经提出，但当前代码中不存在：

### 17.1 `task_input` 权重上限

建议增加通用校验：

```python
if assessment["input_availability"] == "task_input":
    maximum_weight = min(maximum_weight, 1)
```

它会保证可以自然写入任务的名称、描述、组件等信息，最多形成一级语义来源，不能成为三级强交接。

### 17.2 创建类目标的事实分类规则

建议第一轮明确：已有同类实体的名称、描述、组件等只是新实体的参考或模板时，应分类为 `semantic_influence`，而不是 `required_input`。只有目标契约明确支持克隆、导入、派生或关联已有实体时，才属于强输入或状态交接。

### 17.3 当前初态和可达性信息

尚未决定 Step 1 应构建抽象工具契约图，还是当前环境可执行图。如果要求后者，仅修改 Prompt 不够，输入还需要提供经过结构化提取的初态摘要、工具读写集合、可能为空的结果条件和状态变化能力。

### 17.4 参数级 prerequisite

当前 prerequisite 没有记录“来源输出字段 -> 目标输入字段”的绑定，也不检查非空结果。尚未实现参数级数据流证明。

### 17.5 Step 1 断点恢复

当前只有进程内单次批量重试，没有按目标和 Prompt 哈希保存可复用检查点。共享推理服务持续拥塞时仍可能浪费已成功调用。

## 18. 测试和当前状态

当前建图与 pipeline 相关测试：

```text
94 passed
```

Python 编译检查、`git diff --check` 和实验归档 SHA256 校验均通过。

全仓测试在收集阶段仍有 7 个与本分支无关的既有错误：系统 `jsonschema` 版本缺少 `Draft202012Validator`、缺少 `task_gen.program_form`，以及 seed 脚本已有语法错误。

当前分支已提交到 `0e8b451`。新增的问题清单和本报告尚未提交；另有两个既存未跟踪报告文件保持原样。

## 19. 当前结论

本 worktree 已经完成的是底层语义拆分：边权、硬前置和采样概率不再互相代替；Step 1 有可校验的两轮中间事实；Step 2 会真正执行 DNF 历史约束。

当前没有完成的是全部业务边的正确性。最新审核证明两类问题仍存在：

1. 公开契约不足以反映当前初态和工具可达状态，导致空结果工具仍被当成数据来源。
2. 第二轮仍可把 `task_input` 或部分必填字段复用错误提升为 3 级，导致 `create_test_case` 等创建类目标出现稳定误判。

因此当前版本适合作为继续修正的结构基础，但不应把现有 BugAgent 图直接视为最终高质量图。
