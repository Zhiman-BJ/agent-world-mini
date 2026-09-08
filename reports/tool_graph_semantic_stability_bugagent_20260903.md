# BugAgent 工具图语义稳定性实验报告

## 结论

本轮修改解决了最重要的两个问题：`weight` 不再同时承担关系强度、硬前置和采样概率三种语义；模型也不再一次同时完成事实发现、边权分类和前置组合。最后两份可用图的全边集合 Jaccard 为 `0.899`，高于修改前双跑的 `0.821`；事实判断完整一致率由 `66.3%` 提升到 `88.2%`，硬前置方案一致率由 `92.7%` 提升到 `94.0%`。

质量上的改进比数字更重要：回显 ID 不再被误认为新产生的动态值；写入后读取新状态稳定为 3 级；自然任务中可以提供的名称、标题、描述、分类、组件和目标状态不再被当作必须先调用工具才能取得的全局前置；`create_test_case` 的伪硬前置在最后两份可用图中都已消失。

仍有一个未解决的核心问题：第二轮模型仍会对第一轮已经归类为直接必填输入的边，在 2 级和 3 级之间重复推理。最后两份可用图的 3 级边 Jaccard 为 `0.778`，高于修改前的 `0.739`，但低于中间版本的 `0.811`。下一步应讨论是否由程序根据 `connection + value_origin` 确定边权，只让第二轮模型组合 prerequisite；本轮没有擅自做这个架构决定。

这些数字不是当前最终 prompt 在同一提交下完成的严格双跑：图 A 在 `3e09bc1` 后完整生成；图 B 的主体也在最后两行 prerequisite 澄清提交前生成，仅不合规目标在 `d702535` 后重新请求。它们可以衡量本轮主要语义修改，但不能证明当前 prompt 的整图重复稳定性。后续完整重跑受到共享 Terra 服务持续超时影响，没有伪装成成功数据。

## 实验范围

- 环境：`bugAgent open-source quality triage workspace`
- 环境目录：`/data1/users/tianfang/project/agentworld_20260901_175404/agent-world-mini-zhiman/artifacts/mcp_test3/bugagent`
- 工具数：25，因此每轮事实判断覆盖 600 个有向候选对
- 模型：`gpt-5.6-terra`
- 初始对照与主要实验：`max_concurrency=16`、`timeout_seconds=300`
- 服务拥塞复查：尝试过 `max_concurrency=4`、`timeout_seconds=600`
- 最终代码提交：`bdbce11`

这里比较的是同一环境、同一模型下的重复建图，不用边数多少代替质量判断。指标分别观察边是否存在、边权是否一致、事实分类是否一致，以及 prerequisite 的目标和完整方案是否一致。

## 版本与修改

| 提交 | 修改 |
| --- | --- |
| `8852499` | 将图改为独立的 `edges` 与 DNF `prerequisites`；Step 1 拆成事实分析和关系决策两轮；Step 2 真正执行前置约束 |
| `1cb515b` | 在 prompt 中显式列出 `value_origin` 的合法枚举，消除模型自造枚举值 |
| `48a0ca6` | 给关系类型设置等级上限，补充新实体和文本语义规则，并将非法目标批量重试 |
| `7d088a5` | 修正“所有 echoed 都降级”的过度约束，保留真正的状态观察强关系 |
| `5c06cd7` | 新增 `input_availability`，把运行时内部引用与自然任务信息分开；验证器拒绝软关系冒充硬前置 |
| `3e09bc1` | 明确信息可见性按信息本质判断，直接性按是否需要技术中间步骤判断 |
| `d702535` | 明确 `state_observation` 不能进入 prerequisite，定点复测通过 |
| `bdbce11` | 在第一轮拒绝不可分类的状态观察组合；重复前置先校验再去重；修正八字段提示 |

## 一致性结果

| 指标 | 修改前双跑 | 中间修订双跑 | 修订后可用图 A/B |
| --- | ---: | ---: | ---: |
| 图 A / 图 B 边数 | 146 / 138 | 152 / 145 | 163 / 156 |
| 全边集合 Jaccard | 0.821 | 0.868 | **0.899** |
| 共同边同权率 | 77.3% | 87.0% | **86.8%** |
| `weight >= 2` Jaccard | 0.803 | 0.781 | 0.784 |
| `weight = 3` Jaccard | 0.739 | **0.811** | 0.778 |
| prerequisite 目标交/并 | 9 / 11 | 9 / 11 | **9 / 9** |
| prerequisite 方案 Jaccard | 0.927 | 0.783 | **0.940** |

修订后图 A 的边权分布为 `1:74, 2:19, 3:70`；图 B 为 `1:72, 2:26, 3:58`。修改前两轮有 94 和 99 条 3 级边，显著偏多。修订后版本没有简单删边，而是将大量仅回显输入、可选输入和语义影响关系降到合适等级，同时保留 1 级探索边。

### 事实层稳定性

| 事实比较 | 修改前 | 中间修订 | 最终 |
| --- | ---: | ---: | ---: |
| `immediate_next + intermediate_tool_required` 一致 | 69.2% | 74.5% | **91.2%** |
| `connection + value_origin` 一致 | **92.7%** | 91.2% | 91.8% |
| 上述四项全部一致 | 66.3% | 69.2% | **88.2%** |
| 加上 `input_availability` 的三项事实一致 | 不适用 | 不适用 | 89.8% |

新增字段没有让原有关系类型明显变得不稳定。`connection + value_origin` 相比修改前下降 0.9 个百分点，但完整事实一致率提高了 21.9 个百分点。提升主要来自固定了 `immediate_next` 的观察视角：只判断是否存在无需技术中间步骤的直接路径，不再混入“是不是最常见下一步”的主观判断。

## 已确认的改进

### 1. 回显 ID 与新动态值分开

以下两条在最后两份可用图中都为 1 级：

- `get_bug_report -> update_bug_report`：查询结果中的 `bug_id` 是查询输入的回显，不是查询新产生的前置值。
- `get_test_case -> mark_test_case_review_flags`：同理，`test_case_id` 只是定位键回显。

修改前它们都曾被模型判为 3 级，并进入硬前置候选。现在验证器会拒绝 `required_input/optional_input + echoed` 超过 1 级。

### 2. 状态改变后读取保持强关系

以下关系在最后两份可用图中都为 3 级：

- `update_bug_report -> get_bug_report`
- `classify_bug -> get_bug_report`
- `link_test_case_to_bug -> get_bug_report`

这里虽然定位 ID 可能来自写工具的输入，但强关系来自 A 已经改变的状态被 B 直接读取，不来自 ID 回显。第一版修正曾错误地把这类边降到 1 级；现在 `state_observation` 被明确固定为 3 级，并且不能进入全局 prerequisite。

### 3. 去除自然任务信息导致的伪硬前置

中间版本曾出现：为了创建测试用例，必须先调用失败查询、缺陷列表或测试用例列表，因为这些工具能提供 `name` 或 `component`。这不符合我们的任务生成场景：名称、组件、标题、描述、分类、目标状态等信息本来就可以自然地写在任务中。

最后两份可用图中 `create_test_case` 都没有 prerequisite。`create_test_suite` 也没有 prerequisite。真正的运行时标识前置仍然存在，例如：

- `get_test_reports_failures` 需要运行时取得的 `run_id`。
- `get_bug_report`、`update_bug_report`、`add_comment`、`classify_bug` 需要运行时选择或生成的 `bug_id`。
- `get_test_case`、`mark_test_case_review_flags` 需要运行时选择或生成的 `test_case_id`。
- `link_test_case_to_bug` 的方案正确表达为同时准备 `bug_id + test_case_id` 的 `all_of`，不同来源组合之间用 `any_of`。

### 4. 非传递直接边保持正确

最后两份可用图都没有 `list_test_runs -> classify_bug`。测试运行列表不能跳过失败详情、缺陷创建等中间步骤直接分类缺陷。另一方面，若目标同时需要两个独立来源，两个来源到目标的直接边仍可同时存在，并由 prerequisite 的 `all_of` 表达共同满足。

## 发现退步后的修正

### 退步一：所有 echoed 关系一律降级

最初的验证器只看 `value_origin=echoed`，导致写入后读取状态也被降级。原因是把“定位键来源”和“关系成立的依据”混为一谈。修正后只有 `required_input` 和 `optional_input` 受 echoed 上限约束，`state_observation` 独立按状态变化判断。

### 退步二：新增的 `runtime_only` 被按当前路径而非信息性质判断

模型一度认为“本次从列表中选出的名称”就是运行时信息，从而再次把名称加入 prerequisite。原因是字段定义只描述了取得时机，没有强调业务信息本身是否可以自然地出现在任务中。最终 prompt 明确要求：即使当前路径从 A 选择，只要它是名称、标题、描述、组件等普通业务信息，仍然是 `task_input`。

### 退步三：状态观察被模型写入 prerequisite

`link_test_case_to_bug -> list_test_case_links` 是有效的 3 级状态观察边，但 `list_test_case_links` 并不要求历史上必须先建立一次新关联。第二轮模型连续两次把它同时写进 prerequisite。验证器正确拒绝，随后 prompt 增加“state_observation 不能进入 prerequisite，required_state 才能进入”的明确边界；定点恢复响应返回空 prerequisite。

### 退步四：弱探索边一度大量漂移

中间版本在 `semantic_influence` 与 `none` 之间仍反复变化，例如失败详情是否能影响新套件 purpose、安全结果是否能影响缺陷描述。最终 prompt 规定只有 A 的确定结果明确改变 B 的参数、范围或动作内容时才能连边，并强调不能因“不常见”否定契约支持的路径。最终全边 Jaccard 提升到 0.899，说明这项修正有效。

## 仍存在的问题

### 1. 边权第二次推理仍有重复负担

最后两份可用图共同出现 151 条边，其中 131 条同权，20 条发生权重变化。主要模式是：第一轮都已识别为 `required_input`，第二轮有时按字段直接交接判 3 级，有时按业务工作流判 2 级。例如多种 ID 来源到 `link_test_case_to_bug` 在 2/3 级间变化。

这不是再加一句 prompt 就能彻底解决的问题。当前两轮模型实际上重复回答“关系有多强”：第一轮通过 `connection/value_origin` 回答一次，第二轮通过 `weight` 再回答一次。建议下一步讨论确定性映射：

- `state_observation`、`required_state` 固定为 3。
- `required_input + generated/selected/derived` 是否固定为 3，需要确认“只提供一个必填字段”是否也算强交接。
- `required_input/optional_input + echoed` 固定为 1。
- `workflow_transition` 固定为 2。
- `semantic_influence` 固定为 1。

若接受该映射，第二轮模型只需要决定 prerequisite 组合，权重由代码从第一轮事实确定，可进一步消除 2/3 级漂移。这个决定会改变等级定义，故本轮只记录、不实施。

### 2. `input_availability` 仍有 10.2% 漂移

最后两份可用图的 `connection + value_origin + input_availability` 一致率为 89.8%。主要残余来自内部引用与业务标识的边界：某些环境 ID 既可由任务按业务名称指明，又可能只能由工具运行后选出。仅靠 schema 字段名无法完全判断。长期方案应由工具契约提供“用户可见业务标识 / 内部运行时标识”的元数据，而不是让建图模型猜测。

### 3. prerequisite 来源存在少量漏判

最后两份可用图的 prerequisite 目标完全一致，方案 Jaccard 为 0.940。差异集中在：

- `create_bug_report`：4 个方案对 2 个方案，差异是是否把已有缺陷中的 `source_run_id` 视为新缺陷来源。
- `get_test_reports_failures`：4 个方案对 3 个方案，差异是是否从完整缺陷记录取得 `source_run_id`。

这些不是错误方案被加入，而是有效替代来源偶尔漏掉。若下游更重视完整覆盖，可按目标输入字段先由程序枚举所有动态来源，再让模型只判断语义合法性。

### 4. 运行稳定性受外部推理服务影响

| 运行 | 墙钟时间 | 调用状态 |
| --- | ---: | --- |
| 修改前 A | 533.5 秒 | 51 成功 |
| 修改前 B | 706.6 秒 | 51 成功 |
| 中间修订 A | 1061.3 秒 | 52 成功，5 超时 |
| 中间修订 B | 902.6 秒 | 51 成功，4 超时 |
| 修订后图 A | 804.8 秒 | 50 成功，3 次初始超时后重试成功 |
| 修订后图 B 主轮 | 677.4 秒 | 52 成功；一个目标两次语义校验失败 |
| 修订后图 B 单目标恢复 | 1092.7 秒调用总时长 | 两次成功响应，第一次仍违反规则，第二次通过 |

在后续复查期间，甚至出现并发 16、300 秒超时和并发 4、600 秒超时都无法完整结束的情况。降低并发没有稳定解决问题，因此不能简单认为并发越低越可靠。当前批量重试能处理偶发失败，但整轮只允许一次重试，在持续拥塞时仍会丢弃大量已成功结果。

建议下一步将 Step 1 的每个目标结果按 prompt 哈希落盘并支持断点恢复；这比继续增大超时更有效，也不会改变语义质量。当前实验已用调用留档手工恢复验证了该方案的可行性。

Codex 后端在这些调用中返回的 `usage` 为空，因此本次无法给出 token 或金额成本；不能用调用时长推算金额。

## 最终判断

当前代码已通过静态契约和单元测试，定点实测也覆盖了最后修正的状态观察场景；但由于没有取得 `bdbce11` 下两次完整成功图，当前不能宣称最终 prompt 的整图重复稳定性已经验证。最关键的语义错误已经修正，现有证据也不支持回退两轮推理、关系类型或独立 prerequisite 结构。

不建议继续靠追加 prompt 细则追求边权完全一致。最有价值的下一项工程改进是把可确定的权重从模型决策移到代码映射，并给工具契约增加信息可见性元数据；最有价值的运行改进是按目标断点恢复。

## 结果文件

所有图、完整 prompt/answer、失败 trace 和校验文件位于：

`runs/semantic_stability/bugagent_20260903/`

关键文件：

- `bugagent_run1.json` / `bugagent_run2.json`：修改前双跑。
- `bugagent_after1.json` / `bugagent_after2.json`：中间修订双跑。
- `bugagent_final3.json`：主要语义规则下的完整图 A。
- `bugagent_final4_recovery.json`：主体调用加最终 prerequisite 澄清后单目标恢复得到的图 B。
- 同名 `*_calls.jsonl`：每次模型调用的完整 prompt、answer、状态和耗时。
- `*_failed_calls.jsonl`、`*_congested_calls.jsonl`：失败与服务拥塞证据。
- `SHA256SUMS`：归档文件校验值。

图 A 在最后两行 prerequisite 澄清加入前完成，但其全部输出能通过当前验证器；图 B 的主体调用也早于该澄清，只针对唯一不合规目标在澄清后重新请求并组装。报告没有把失败轮或不完整图纳入语义指标，也不把这两份图冒充为当前提交的严格完整双跑。
