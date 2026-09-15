# Qwen Recreation 六任务独立人工核查

核查时间：2026-09-13。标准仅为各任务的 `task_text`，不使用参考执行链、参考答案或生成 verifier 的成功条件/判定。读取 Qwen 最终回答、实际工具输出、完整 agent 日志，并以 SQLite `mode=ro` 核查记录；未更改原始证据，未补做 Qwen 任务。

运行根目录 R：`/data1/home/tianfang/agent-world-mini-zhiman/runs/recreation_sol_retry_2/20260913_085426_497767_smithery_recreation_gov_93_gpt-5.6-sol`。
下文 Q(t) 表示 `R/sol_verifier_qwen/task<t>`。任务原文在 `R/tasks.json` 对应 task_id 的 task_text；最终回答在 `Q(t)/result.json` 的 agent_answer。日志行号按 JSONL 的物理行计数。

| 任务 | 判定 | 决定性结论 |
|---|---|---|
| task5 | fail | 上下文超限中断，无最终交付 |
| task7 | fail | 明确把快照中存在的 Wilderness 许可及资源说成未收录，快照覆盖说明错误 |
| task10 | fail | 上下文超限中断，无最终交付 |
| task13 | pass | 提交简报，覆盖兴趣、设施/许可/事件、候选类型比较、三晚缺失及限制 |
| task16 | pass | 两人 Upper Pines 三晚筛选正确，地址/许可/行程及边界均有交付；不因换工具降分 |
| task21 | fail | 上下文超限中断，无最终交付 |

总体为 **2 pass / 4 fail**；其中 3 个 fail 是 API 上下文配置/执行中断，不应统计成已经观察到的模型任务能力失败。task7 是已交付内容的实质错误。

## task5 — fail

任务要求：“筛选出容量至少为4人且在7月15日和7月16日两晚均显示为‘Available’的营位”，并“逐日列出筛选结果的状态”，核对设施、许可、入口与快照限制。

已运行的第 3 次工具调用确实按 4 人、2026-07-15 入住、07-17 退房进行住宿筛选；随后继续研究许可与资源。这些中间查询不是用户要求的行程交付。

最小确凿反例：`Q(5)/state.agent/llm_calls.jsonl` 共 10 行，第 1—9 行 answer 均为 action 对象，没有 finish/final_answer；第 10 行 status=failed、answer=null。`Q(5)/result.json` 仅有 task_id 和 error，没有最终回答。最后错误原文：`maximum context length is 131072 tokens`、`32768 output tokens`、`at least 98305 input tokens`，合计至少 131073。故设施/许可/入口总结、逐日结果、覆盖限制均未向用户完成交付。

归因：上下文容量/输出预留配置导致 API 拒绝，属于基础设施/执行中断；不是据此断言 Qwen 不会筛选。

## task7 — fail

任务要求 Half Dome 行前研究涵盖管理机构、许可规则、地址、分区、活动、官方链接、媒体、相关营地和 2026-07-15 公园活动，并“明确所用资料快照的覆盖范围及研究结果中的信息缺失和不确定性”。

已覆盖：回答指定两名旅行者与日期，列出 National Park Service、Half Dome 安全规则、Yosemite/CA/95389 地址、4 个分区、Hiking、7 条相关链接、5 项媒体、多个营地、31 项覆盖访问日事件，并说明坐标占位、远端内容未验证和实时名额未知。数据库核查 Half Dome 对应地址/分区/入口/活动/链接/媒体/营位数量分别为 1/4/0/1/7/5/0，事件覆盖该日 31 条，与这部分报告一致。

最小确凿反例为最终回答对快照覆盖的明确否认（`Q(7)/result.json:agent_answer`，第 8 节和第 10 节第 3 条）：

> 本快照未收录该类许可或其营位。
>
> 如需过夜攀登 Half Dome，需单独申请荒野许可，本快照未收录该许可，未覆盖其分区/入口/营地/活动。

实际 `Q(7)/state/records.sqlite` 中存在 `permits.permit_id='445859'`，名称为 Yosemite National Park Wilderness Permits；且 `campsites` 有 **704 条** `permit_id='445859'` 记录。该许可属于 recarea_id 2991。不能由 Half Dome 自身的 0 个 campsites 推导整个快照未收录 Wilderness 许可及营位。任务明确要求准确说明覆盖边界，该错误直接违反此要求，并使相关露营研究遗漏已有资源。

可复核 SQL（在该数据库只读连接中运行）：

```sql
SELECT permit_id, name, recarea_id FROM permits WHERE permit_id='445859';
SELECT COUNT(*) FROM campsites WHERE permit_id='445859';
```

最终交付也可见 `Q(7)/state.agent/llm_calls.jsonl:8`。`result.json` 中 verifier 的 evidence_refs 错误不作为本次判错依据；本次 fail 完全由上述独立数据反例成立。

## task10 — fail

任务要求研究 Half Dome、Wilderness、Upper Pines 等住宿的规则/资源/访问选项/日期可用性，整理活动、事件、目的地背景，并解释快照与未知限制。

已开始区域查询、区域简报和两种许可研究，但未形成最终研究结果。

最小确凿反例：`Q(10)/state.agent/llm_calls.jsonl` 第 1—4 行仅 action；第 5 行 failed、answer=null，报相同 131072 上限与 98305+32768 超限错误。`Q(10)/result.json` 仅 task_id/error，无最终交付。不存在把前述公共研究与未知限制提交给用户的 final_answer。

归因：基础设施上下文错误中断，而非已经验证的模型能力失败。

## task13 — pass

任务要求 4 人、2026-07-10 至 13 的徒步/攀登/露营简报，汇总活动、事件、设施、营位和许可，比较可预订候选，逐日核对住宿，并说明覆盖与资格/名额等未知。

完成证据：`Q(13)/result.json:agent_answer`；对应实际最终交付为 `Q(13)/state.agent/llm_calls.jsonl:8`。

| 实质要求 | 已交付及核查结果 |
|---|---|
| 4 人、日期及三项兴趣 | 回答开头明确 4 人和 07-10—13；列出 Hiking/Climbing/Camping |
| 活动、事件、设施、许可 | 列出兴趣活动、17 个设施并分组、Half Dome 与 Wilderness 规则、24 项覆盖到访日的常规事件；只读数据库确认 07-10、11、12、13 各有 24 项符合日期且未取消事件 |
| 可预订住宿/体验候选比较 | 明确比较设施/营位/导览/活动/事件五种候选：16 个设施只有静态 reservable 标志，全部 not_date_specific；其余四类在明确可预订条件下均为 0。此处接受任务所需的候选类型/证据等级比较，不额外要求未写明的偏好评分或最优营地推荐 |
| 容量与整个住宿区间 | 实际筛选参数为 4 人、07-10 入住、07-13 退房、16 个相关设施；候选 235，完整覆盖 0，缺失排除 235，匹配 0 |
| 逐日核对 | 回答明确“7 月 10–12 每一晚都没有可用性快照记录”，等价覆盖三个住宿夜；无需强制三行表格 |
| 快照和未知限制 | 指出数据仅 2026-10-01—31、7 月记录 0，不把缺失认作满房；明确资格、quota、发放窗口、当日场次和实时预订未知 |

`Q(13)/state.agent/tool_calls.jsonl:3` 的比较结果含上述 16/0/0/0/0 与 not_date_specific；第 4 行住宿筛选结果确认 235/0/235/0。只读数据库 `availability_records` 的 MIN/MAX/COUNT 为 2026-10-01 / 2026-10-31 / 7285，与回答一致。

回答将普通营地组称为“车辆”，其中 Camp 4 实际为 walk-in，此分类用词不精确；本任务未提出车辆住宿需求，回答亦未据此选定 Camp 4 或承诺车辆进入，不作为本任务未完成的决定性反例。`result.json` 的旧 verifier evidence_refs 错误不影响已有完整交付的人工判定。

## task16 — pass

任务要求两人 2025-06-15 入住、06-18 退房，研究徒步/露营区域设施活动及 Half Dome，核对地址，以 Upper Pines 筛选全住宿区间逐晚完整且均 Available 的营位，并说明快照、实时库存、资格和签发限制。

完成证据：`Q(16)/result.json:agent_answer`；对应 `Q(16)/state.agent/llm_calls.jsonl:14` 的 finish/final_answer。

| 实质要求 | 已交付及核查结果 |
|---|---|
| 两人、日期及行程 | 明确两人和入住/退房日，并给出 06-15 谷内徒步、16/17 条件式 Half Dome、18 退房安排 |
| 区域、设施、兴趣活动 | 提供 Yosemite/NPS、多个营地、Hiking/Camping，以及日期覆盖的 4 项常规事件；Upper Pines 的设施和附近步道说明可追溯实际工具输出 |
| Half Dome 信息与地址核对 | 安全/路线规则、分区及资源摘要；地址 Yosemite, CA 95389，source_address_id 100，与许可关联明确；工具日志第 11 行资源返回支持 |
| 两人 Upper Pines 营位筛选 | 第 4 行工具调用明确 facility_ids=[232447]、2025-06-15 至 18、party_size=2、night_count=3；235 个候选、完整覆盖 0、排除 235、匹配 0 |
| 原始记录独立核查 | 数据库有 235 个 Upper Pines 营位，max_num_people 均 6；availability 只有 2026 年 10 月 7285 条，不存在 2025-06-15/16/17 记录；Reserved/Closed/Available 数量为 7197/87/1 |
| 覆盖及边界 | 回答明确缺失不等于无库存，解释 0 匹配原因，不保证预订、不判断资格或许可签发 |

不因工具选择降分：任务并未要求专用地址验证工具，直接读取 permit 关联地址即能满足核对要求；聚合连续住宿筛选也能完成逐夜全覆盖判断，不能要求机械复刻参考链。

记录到一处工具返回口径差异：数据库顶层字段和第 3 行 build_recreation_area_trip_brief 的 Half Dome reservable 为 null，但 **第 10 行实际 get_permit 输出为 true**。进一步读工具实现，get_permit 的 _effective_reservable 在顶层字段为空时从 source_records 补值；实际原始记录中两条 234652_permit 的 reservable 均为 true。因此这个值有真实来源，不是模型捏造，也不是已证实的错误事实。Qwen 立即说明快照不执行申请/签发，不影响住宿筛选或实时预订限制。

## task21 — fail

任务要求 Wilderness 徒步与 Yosemite Creek 露营两方案比较；核实规则、地址、区域/入口关联，完整三晚且两人容量筛选；整理设施/活动/导览/事件/媒体，按可核对性排序并体现偏好和各种限制。

仅完成区域、许可、Yosemite Creek 设施发现和 Wilderness 资源调用，没有研究简报交付。

最小确凿反例：`Q(21)/state.agent/llm_calls.jsonl` 第 1—4 行均为 action；第 5 行 failed、answer=null，131072 上限与 98305+32768 超限错误。`Q(21)/result.json` 仅 task_id/error。没有 finish/final_answer，故方案比较、住宿筛选结论及限制未交付。

归因：基础设施上下文错误导致执行中断；不把此中断解读为模型能力失败。
