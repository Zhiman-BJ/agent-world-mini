# Tool Graph 语义稳定性修改与实验复盘

## 结论先行

- 当前代码：`d702535`。
- 已完成双跑且一致率最强的版本：`5c06cd7`。
- 该版本两轮共同边的同权率为 95.5%，3 级边 Jaccard 为 0.886。
- 该版本的全部正边 Jaccard 只有 0.810，波动主要集中在 1 级探索边。
- `5c06cd7` 仍把部分普通业务信息误判为 `create_test_case` 的 prerequisite；后续
  `3e09bc1` 已修正这一点，但尚无两轮完整成功结果，不能声称最终一致性已经验证。

## 第一轮：拆分关系语义与模型推理

提交：`8852499`、`1cb515b`

修改：

1. 将 `tool_graph` 从边数组改为 `{edges, prerequisites}`。
2. `weight` 只表示直接下一跳关系强度，不再兼任 prerequisite 或采样概率。
3. prerequisite 按目标工具保存，并用 `any_of` / `all_of` 表达任选方案与共同前置。
4. Step 1 拆成两次 LLM 调用：第一轮提取事实，第二轮决定边权和 prerequisite。
5. 第一轮增加 `connection`、`value_origin`、`immediate_next` 和
   `intermediate_tool_required` 等结构化字段。
6. Step 2 采样和 LLM 审查开始真实执行 prerequisite 约束。
7. 补齐 `value_origin` 的固定枚举，解决模型自由改写枚举值导致的批量失败。

第一次双跑结果：

| 指标 | Run 1 | Run 2 | 双跑一致性 |
| --- | ---: | ---: | ---: |
| 正边 | 146 | 138 | Jaccard 0.821 |
| 1/2/3 级边 | 43/9/94 | 21/18/99 | - |
| 共同边同权率 | - | - | 77.3% |
| 3 级边 | 94 | 99 | Jaccard 0.739 |
| prerequisite 目标 | 9 | 11 | 共同 9 / 合计 11 |

运行审查发现：

1. 大量回显 ID 被判成 3 级，强边明显膨胀。
2. `get_bug_report -> update_bug_report` 等读取后操作关系没有区分“新发现 ID”和
   “查询输入原样回显”。
3. `get_test_reports_failures -> create_test_case` 等边把已有实体 ID 错当成新实体 ID。
4. 普通语义影响有时判 1 级、有时判无边，`semantic_influence` 边波动较大。
5. `create_test_case` 偶尔被错误添加 prerequisite，说明边强度和硬前置仍被模型混用。

## 第二轮：校准等级边界

提交：`48a0ca6`、`7d088a5`

修改：

1. 为关系类型设置等级上限：`semantic_influence <= 1`，
   `workflow_transition <= 2`，`optional_input <= 2`。
2. 仅靠回显输入形成的输入传递最多为 1 级；来源不明确最多为 1 级。
3. 明确创建新实体时，已有实体 ID 不能充当新实体 ID。
4. 明确只有 A 的确定结果对 B 有具体用途时，才允许弱语义边。
5. 保留写入后读取的强状态关系，不因定位 ID 是回显值而统一降级。
6. 将非法目标的重试从逐个串行改成一次批量重试。

第二次双跑结果：

| 指标 | Run 1 | Run 2 | 双跑一致性 |
| --- | ---: | ---: | ---: |
| 正边 | 152 | 145 | Jaccard 0.868 |
| 1/2/3 级边 | 61/26/65 | 49/27/69 | - |
| 共同边同权率 | - | - | 87.0% |
| 3 级边 | 65 | 69 | Jaccard 0.811 |
| prerequisite 目标 | 10 | 10 | 共同 9 / 合计 11 |

运行审查发现：

1. 全边一致性和权重一致性均明显提高，3 级边数量也从约 95 条降到约 67 条。
2. `state_observation` 虽允许为 3 级，但第二轮模型仍会主观降成 1 级。
3. 名称、标题、描述、组件等可自然写入任务的业务信息仍可能进入 prerequisite。
4. `create_test_case` 的 prerequisite 两轮分别为 6 个和 0 个，是最明显的不稳定点。
5. 根因是 `value_origin` 只能描述值从哪里来，不能描述该信息是否必须在运行时取得。

## 第三轮：显式区分信息可用性

提交：`5c06cd7`

修改：

1. 第一轮新增 `input_availability`：`runtime_only`、`task_input`、
   `not_applicable`、`unknown`。
2. prerequisite 只允许来自 `required_input` 或 `required_state`。
3. prerequisite 来源必须为 `runtime_only`，且值由 A 生成、选择或推导。
4. `state_observation` 被固定为 3 级，防止写入后读取关系再次被降级。
5. 明确查询结果中的原定位键属于 `echoed`，而不是 `selected`。
6. 明确无参数且行为固定的查询不能被前一步结果“语义影响”。

第三次双跑结果，也是目前一致率最强的完整双跑：

| 指标 | Run 1 | Run 2 | 双跑一致性 |
| --- | ---: | ---: | ---: |
| 正边 | 157 | 138 | Jaccard 0.810 |
| 1/2/3 级边 | 76/17/64 | 61/9/68 | - |
| 共同边同权率 | - | - | 95.5% |
| 2 级及以上边 | 81 | 77 | Jaccard 0.795 |
| 3 级边 | 64 | 68 | Jaccard 0.886 |
| prerequisite 目标 | 10 | 10 | 目标完全一致 |

进步：

1. 3 级核心边 Jaccard 从最初的 0.739 提升到 0.886。
2. 共同边同权率从 77.3% 提升到 95.5%。
3. 查询后操作的回显 ID 边稳定为 1 级。
4. 写入后读取状态的边稳定为 3 级。
5. prerequisite 的目标集合两轮完全一致。

退步和遗留问题：

1. 全部正边 Jaccard 从第二轮的 0.868 回落到 0.810，波动集中在 1 级探索边。
2. 第一轮事实分析仍会按“本次路径从 A 取得”判断 `runtime_only`，而不是按信息本身的
   固有可见性判断。
3. 因此两轮都给 `create_test_case` 保留了 4 个不应存在的 prerequisite；稳定但错误不算进步。
4. `immediate_next` 仍会在“技术上可直接相邻”和“业务上是否常见”之间摇摆。

## 第四轮：修正第三轮退步

提交：`3e09bc1`

修改：

1. `input_availability` 改为按信息的固有性质判断。
2. 名称、标题、描述、分类、组件、目标状态、筛选条件始终属于 `task_input`，即使本条
   路径恰好从 A 的结果中选出。
3. `runtime_only` 只表示不透明 ID、句柄、令牌或工具必须建立的状态。
4. `immediate_next` 明确为“是否需要技术中间步骤”，不再以“是否为最常见下一步”判断。
5. 要求覆盖所有有明确具体用途的弱路径，同时禁止只凭同领域或共享资源补边。

运行结果：

- 定点测试中 `create_test_case` 和 `create_test_suite` 均不再产生 prerequisite。
- 一次完整运行成功：163 条边，1/2/3 级为 74/19/70，prerequisite 目标降为 9 个。
- 第二次完整运行在 `list_test_case_links` 处被验证器拒绝：模型把
  `state_observation` 同时写进 prerequisite，暴露出提示词中的最后一个语义缺口。

## 第五轮：排除状态观察型硬前置

提交：`d702535`，当前代码版本。

修改：

1. 明确 `state_observation` 只表示 B 可以观察 A 刚完成的变更，不表示 B 全局依赖 A。
2. 只有 `required_state` 才表示 A 建立了 B 本次执行不可缺少的状态。
3. 因此 `state_observation` 不得进入 prerequisite。

验证状态：

- `link_test_case_to_bug -> list_test_case_links` 定点验证通过：边为 3 级，prerequisite 为空。
- 相关单元测试通过。
- 尚未取得两轮完整成功图。之后的完整运行受到 Codex/Terra 调用服务长尾影响，多次在
  300 秒和 600 秒超时；这些失败不是语义校验失败，不能用作一致率计算。

## 当前判断

1. 已经稳定下来的部分是 3 级直接关系、回显值降级和状态观察强边。
2. 第三轮的最高一致性数据可证明结构拆分和等级约束有效，但不能证明当前最新提交的
   全图一致性。
3. 第四、第五轮解决的是第三轮中“稳定但错误”的 prerequisite，方向正确，定点结果正常。
4. 下一步只需要在推理服务恢复后，对 `d702535` 做两次完整运行；不应继续修改语义规则，
   否则会再次混淆代码变化与服务波动。
