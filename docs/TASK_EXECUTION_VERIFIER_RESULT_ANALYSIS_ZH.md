# 任务真实执行审核：实现与试跑分析

## 1. 当前结论

独立评测流程已经完成。它不进入 task generation pipeline，也不修改 step、contracts 或现有
任务生成结果。评测以任务文本为唯一成功标准，让 Codex Agent 在任务初态副本中真实调用环境
工具，再依据调用轨迹、最终 workspace 和最终回答逐项审核。

最终全量运行 `20260903_143133_576928` 的原始结果为 7 pass、1 fail、4 indeterminate、
2 infrastructure_error。两个基础设施错误在低并发补跑中均一次通过；唯一 fail 在改进 Agent
执行提示后补跑通过。因此按每个任务的最新有效尝试合并：

| 结果 | 数量 | 含义 |
| --- | ---: | --- |
| pass | 10 | 参考可校准，Agent 的实际执行满足全部原子要求 |
| indeterminate | 4 | 参考执行本身不能满足任务，无法冻结可信 verifier |
| fail | 0 | 补跑后没有未解决的 Agent 任务失败 |
| infrastructure_error | 0 | 两个 503 均通过低并发补跑排除 |

这不是把失败条件放宽后的 10/14。四个任务没有进入 Agent 执行，因为参考执行未通过同一套
质量标准；系统没有把参考或审核器问题算作 Agent 失败。

## 2. 已实现的审核流程

1. 每个环境只读取最新一轮正式 `tasks.json`，为每个任务复制独立初态 workspace。
2. Codex Agent 可直接读取 workspace，但业务状态修改必须经过临时 MCP 工具，调用会写入 trace。
3. 根据任务文本、公开工具契约和参考执行生成任务专用 verifier，将任务拆成 required 原子要求。
4. verifier Python 代码先做 AST 白名单检查，再在现有受限工具执行器中运行。
5. 冻结前依次校准参考执行、逐项语义结果、反事实 ID/字段表示和空执行。
6. 实际执行只收集工具调用、初末 workspace 和最终回答；所有 required 项通过才算任务通过。
7. 只有自然语言含义交给 LLM 判断；路径、哈希、金额、状态和结构等事实优先由代码检查。
8. 校准成功的 verifier 按任务、环境和参考证据指纹缓存，后续运行不再重新生成审核标准。

评测产物逐任务即时写盘。Agent 执行失败、verifier 无法冻结、verifier 运行错误和基础设施错误
分别归因，不再压成一个真假分数。

## 3. 主要问题及修正

### 3.1 旧评测的高通过率不可信

旧运行 `20260831_130710_395150` 为 11 pass、3 fail，但整体 LLM judge 把 Happyscribe
task2/task8 的引文堆叠和空 `Open actions` 标题判为通过；同时又因报表文件没有落盘而误拒
Finstat task6/task7，而这些报表工具本来只计算并返回结果。

新版不比较实际回答与参考回答的字面相似度，也不再把 `resource_constraints.should_modify`
直接当成业务成功条件。交付文件中的空标题不能由最终回答补足；纯计算型工具的返回值和回答
则可以共同证明报告已经生成。

### 3.2 verifier 对参考实现过拟合

试跑中出现过三类过拟合：要求动态 ID 与参考值一致；要求业务标识必须写进
`source_reference`；把参考分录日期错误传播给任务的另一个分句。当前通过以下校准限制：

- 反事实修改参考执行中新生成的动态 ID，并改变技术字段的承载方式，但保留业务含义。
- 业务名称忽略不影响语义的大小写、标点、空白和下划线差异。
- 日期、数值和名称只约束其明确修饰的业务对象。
- 实际代码判 fail 时，再依据原任务和相同 evidence refs 做一次窄范围语义裁决，忽略 verifier
  偶然加入而原任务没有要求的格式、字段或日期限制。

该兜底已用两份真实失败结果重放验证：Finstat task7 的合法 `2019-10-31` 分录和
Happyscribe task9 的中文“未提供明确行动项”都由 raw fail 修正为 pass。

### 3.3 校准机制自身的退步

迭代中保留并分析了几个明显退步：

| 版本/运行 | 结果 | 问题 | 处理 |
| --- | --- | --- | --- |
| 全局 coverage reviewer | 0 pass / 14 indeterminate | reviewer 把合理拆分也当成遗漏 | 整体删除 |
| 携带上一个 verifier 重试 | 2 pass / 3 fail / 8 indeterminate / 1 infra | 后续候选锚定前一份坏代码 | 重试只反馈精简错误 |
| 过宽反事实 ID 修改 | 9 pass / 1 fail / 4 indeterminate | 改坏初态已有稳定 ID | 初态 ID 保持不变 |
| v3 | 10 pass / 0 fail / 4 indeterminate | task8 被弱化为回答说“0 项”即可 | 校准时同时提供原任务与内容载体规则 |
| v4 | 8 pass / 2 fail / 4 indeterminate | 固定日期、固定中英文短语误杀 | 失败项按原任务做语义裁决 |
| 逐原子契约完整性审核 | 多个稳定任务变 indeterminate | 错把原子项未覆盖整项任务视作删减 | 撤销该判法；原子项允许只覆盖一个目标 |

这里最重要的结论是：参考必须通过，但“让参考通过”本身不够。最终保留了反事实和空执行
校准；没有保留会系统性拒绝合理 verifier 的全局审查。

### 3.4 基础设施与 Agent 行为混淆

16 路全量缓存复跑时，5 个任务在 0 次工具调用、workspace 未变化的情况下收到审批服务 503。
旧逻辑把它们记为 Agent fail。现在只有同时满足“0 调用、0 状态变化、回答明确包含 503 和服务
不可用”才自动重启 Agent，连续 3 次仍失败则归为 infrastructure_error。

低并发补跑的两个基础设施失败均一次通过，证明原失败不应归因给 Agent。另一个真实 Agent
问题是遇到工具 429 后仍有调用额度却不重试，以及只给计划后请求确认。执行提示现已明确：
任务本身就是授权；不得再次请求确认；429/503 在额度允许时至少重试一次。Finstat task7
随后以 13 次调用通过。

## 4. 最终任务结果

| 环境 | 任务 | 最新结果 | 说明 |
| --- | --- | --- | --- |
| Bugagent | task1 | pass | 新建高严重度缺陷、关联复现、评论和调查状态均完成 |
| Bugagent | task9 | pass | 全量轮 503；低并发 9 次调用补跑通过 |
| Bugagent | task10 | pass | 状态、复现关联和审计评论均完成 |
| Finstat | task6 | indeterminate | 任务写 `78154807RG994149`，初态和参考均为 `78154807RG994149F` |
| Finstat | task7 | pass | 授权提示修正后 13 次调用补跑通过 |
| Happyscribe | task2 | indeterminate | 参考简报主要罗列引文，且 Open actions 为空 |
| Happyscribe | task3 | pass | 三份转写统一整理，内容和原始证据未变 |
| Happyscribe | task4 | pass | 目标说话人的三份转写归档且内容不变 |
| Happyscribe | task5 | pass | 重命名、建子目录、移动和 WebVTT 导出均完成 |
| Happyscribe | task6 | indeterminate | 参考简报含转写外陈述，且 Open actions 为空 |
| Happyscribe | task7 | pass | 标题、研究备注、证据引用和 WINDOW 规范通过 |
| Happyscribe | task8 | indeterminate | 参考简报是引文堆叠，Open actions 为空且未说明无 |
| Happyscribe | task9 | pass | 五条引文、身份、软删除和行动项说明均完成 |
| Happyscribe | task10 | pass | 全量轮 503；低并发 8 次调用补跑通过 |

## 5. 参数评估

- `max_concurrency=16`：verifier 语义请求吞吐较高，但同时启动真实 Codex Agent 会触发审批服务
  503。当前机器更适合 4-8 路执行 Agent；16 路可用于纯 LLM 审核，不宜作为稳定默认值。
- `max_tool_calls=20`：当前应保留。Happyscribe task9 曾用满 20 次；Finstat task7 的失败轮在
  第 15 次遇到 429，修正提示后用 13 次完成。降低额度会直接损害复杂任务。
- `timeout_seconds=1800`：本轮没有任务因该上限超时，足以覆盖复杂工具链。
- `tool_result_max_bytes=65536`：偏小。三份长转写同时进入证据时，第三份会被截断，导致部分
  引用完整性判断变为 indeterminate，并增加 verifier 重试。后续应改为按 requirement 定向加载
  文件，或单独提高评测证据预算；不建议盲目扩大整个 pipeline 的工具返回上限。
- `verifier attempts=3`：可用但仍有生成波动。缓存后 10 个有效任务全部复用冻结标准，首次成本
  不再影响后续重复执行；当前缓存总目录包含各版本 53 个条目，约 836 KB。

## 6. 剩余问题

1. 四个 indeterminate 首先是任务生成/参考执行质量问题，不应在 verifier 中放宽。需要回到任务
   生成阶段修正错误标识，并让参考简报真正形成总结且在正文明确行动项状态。
2. Happyscribe task7 中“中文窗口”是否违反英文术语 `WINDOW` 的统一要求存在语义边界；当前冻结
   verifier 将术语规范理解为英文写法规范，中文普通词不视作英文变体。任务文本若要求中文也必须
   写 `WINDOW`，应直接明确说明。
3. Agent 执行仍有随机性。缓存只固定审核标准，不保证 Agent 每次成功；因此报告同时保留单次结果
   和补跑结果，不用 `pass@k` 替代单次归因。
4. 目前仅处理已观察到的 503 启动故障。Agent 已经产生部分状态后再遇到基础设施异常时不能自动
   重跑，否则可能重复写入；这类情况应依靠幂等工具或从初态重新创建 workspace 后再试。

## 7. 验证状态

与本功能直接相关的 `task_eval`、Codex client 和 LLM 辅助测试共 65 项，全部通过；Python
文件也通过语法编译和 diff whitespace 检查。

全仓 `pytest -q` 和 `uv run pytest -q` 均在测试收集阶段被 7 个既有问题阻断：系统加载的
`jsonschema` 不提供 `Draft202012Validator`、仓库缺少 `task_gen.program_form`，以及
`seed_gen/scripts/fetch_smithery_servers.py:55` 存在未闭合字符串语法错误。本分支没有修改这些
文件，因此没有把无关修复混入当前提交。

## 8. 证据位置

- 最终全量：`runs/task_eval/20260903_143133_576928/results.json`
- v7 首次全量：`runs/task_eval/20260903_140250_980062/results.json`
- 503 低并发补跑：`runs/task_eval/20260903_143133_576928/infrastructure_retries/`
- Finstat 执行提示补跑：`runs/task_eval/20260903_143133_576928/agent_prompt_retry_v2/result.json`
- 每项 verifier、调用轨迹摘要、workspace 和 requirement 结果均在对应 run 子目录中。
