# Step3 双会话拆分与配套控制逻辑

日期：2026-09-19。状态：已接入代码，未进行模型试跑或测试；不能据此宣称真实会话恢复和多轮运行已验证。

讨论稿见 [main-prompt.md](main-prompt.md) 和 [executor-prompt.md](executor-prompt.md)。运行正文位于 `task_gen/tool_graph/prompts/step3_main.txt` 和 `step3_executor.txt`；长度与轮次占位符由 execution 配置替换。文档标题及说明不发送给模型。

## 职责与现状

主会话探索初态、搜索参考、落实目标、接收执行证据、决定修复/深化/接收。执行会话只完成一轮给定目标，独立探索和实际操作。程序管理会话、快照、轮数和最终记录。程序启动的独立执行会话称为执行 subagent，不依赖 Codex 自主派生子代理。

`CodexAgentClient.run()` 新增可选 persistent_session，保存 thread.started 的精确 ID，用 `codex exec resume <ID>` 续接；其他调用默认仍使用 `--ephemeral`。超时也尝试保存已创建的会话ID，缺失时禁止在已经运行的状态上偷偷新开会话。此处 CLI 能力确认和代码接入不等于实际会话恢复已验证。

`execution_agent.py` 从最终轮环境日志生成链，`meaningful_calls` 只作契约/返回筛选，不能判定是否业务冗余。MCP 服务启用 resume_trace 时重放方案选择日志恢复 active_choices，并累计已经发生的调用，防止续接清零预算。每个会话按累计运行时间约束超时；每次请求、回答、错误、session ID 和日志单独保留。

## 输入输出与防复用

主会话初始输入：原 objective、original_chain、Step2 原始 design_basis、公开环境和工具信息。环境原始实现只给 MCP 执行器，不给模型。

执行会话输入严格由程序组装：

```json
{
  "objective": "主会话落实后的完整业务目标，包含用户可见的必要条件",
  "original_chain": ["从原候选读取的工具名"],
  "design_basis": "从Step2原候选读取，不能替换为前置探索总结",
  "tools": [],
  "environment": {}
}
```

tools/environment 使用现有公开投影。不能整体转发主会话输出、reason、搜索资料、前置观察、方案 basis、旧轮 answer、工具参数或内部 ID。用户真实可指定的实体名称和条件允许进入 objective，不禁止所有具体值。

字段白名单能保证没有直接转发隐藏记录，不能数学保证 objective 自由文本绝不夹带内部信息。prompt 明确约束；若发现泄露，回传主会话修改后再执行。不能宣称靠字段过滤已完全解决语义泄露，也不引入正则猜测所有 ID 的伪保证。

主会话输出固定五字段：action/ objective/ reason/ feedback/ score。执行输出固定三字段：reason/ completed/ answer。解析复用 parse_json_object；严格检查字段、类型、非空文本及 action 允许范围。结构错误给原会话回传具体错误，有限重试，不猜测修补业务意义。

主会话收到的执行报告由程序生成，包含 round、当前 objective、remaining_rounds、运行错误、执行者 completed/reason/answer、完整可用的调用参数与结果、基础过滤后的 call_count。统计值由程序计算，不接受模型自报覆盖。大结果使用已有带标记的裁剪，原始结果完整留档；不能用一段模型总结代替调用证据。证据不够时通过 repair 要求执行者实际查询，不给主会话 shell 读取权限。

## 控制流程

```text
启动主会话（原始状态的独立只读副本）
  → 主会话探索、搜索并返回决策
execute → 核验轮数及交接内容 → 新会话、新原始状态副本 → 执行
执行结果/错误 → 保存完整轨迹 → 续接原主会话复核
repair  → 目标不变 → 续接当前执行会话和当前状态 → 累加本轮轨迹
execute → 目标变化 → 旧轮完整留档 → 从原始初态开始下一轮
accept  → 程序核验接收条件 → 发布该轮结果
reject  → 保留失败理由、各轮记录 → 候选结束
```

- 首次仅允许 execute/reject；已有轮次后允许全部动作，但 repair/accept 必须对应存在的执行会话和日志。
- repair 的 objective 必须与本轮字符串一致；修改了目标只能走 execute。repair 反馈只指出任务要求和本轮证据中的问题，不夹带前置探索才能知道的值；让执行者自行查询缺失事实。
- 新 execute 消耗一轮，最多3轮。相同目标的纠错续接不新开轮，但继承本轮剩余工具调用和时间预算，不能借恢复重置安全限制无限修复。
- accept 要求本轮无未恢复的运行异常、执行 completed=true、主会话确认完整且有价值、基础过滤调用数至少10。20是期望值，不能把20作为新的硬最低门槛。
- 10–19且尚有轮数：主会话在 reason 说明无自然深化空间才接收，否则请求新 execute；到第3轮仍可按至少10次接收。少于10程序拒绝 accept 并回传错误，尚有轮数可深化，没有轮数则候选失败。
- 数量过关不能证明有业务贡献；语义冗余由执行者和主会话核查，不按同工具同参数机械去重，不删有价值的负面探索。
- completed=false 的可修复业务问题可 repair；能力阻断可改目标后 execute；超过轮数也不能把未完成候选当作完成。
- 空回答/超时/服务错误作为运行错误回传主会话；续接恢复时保留此前状态与调用，防止重复写入。缺失会话 ID 无法安全续接则显式失败，不偷偷新建并声称恢复。

## 会话、权限与文件

主会话与每轮执行会话分别保存准确 session_id；续接使用该 ID，禁止 --last（候选并发会串会话）。主会话等待时无需持续占用 CLI 进程，但上下文必须由真实会话恢复保留，不以摘要冒充完整上下文。

客户端默认行为保持不变，仅为新流程显式启用持久会话；不删除其他调用方的 --ephemeral。新会话与 resume 的参数分支必须按 CLI 支持分别构造，不能把原启动参数直接全部拼到 resume 后。续接继续绑定原 MCP 配置及访问限制。

主会话探索用独立只读状态副本，允许全部工具契约可见，实际写操作不允许改变初态。使用已有状态边界检查；缺少 sideEffects 声明不能被当成确定只读。执行者绑定新副本和公开环境工具，不开放网络参考、方案抽样、文件或 shell 访问。仅主会话配置 review_choice_seed。

恢复主会话时 review_select_plan 的历史和随机状态必须延续；复用已有 ReviewChoices，从已保存的方案调用日志确定性重放，或保持服务进程，不能只保留模型上下文却清空工具状态。优先持久化初始 seed 并重放原始调用；校验 selected/active_choices 与历史返回一致，不一致时报错。

每轮恢复 MCP 时，累计调用预算从现有日志计算，不能每次进程重启就重置 max_tool_calls。时间预算同理累计；已存在的调用/时间配置继续作为安全上限，不把“不设有效链长上限”误解成删除资源保护。结构修正重试复用 execution.retry_count（代码默认3），不为格式错误新建业务轮。

```text
tasks/<id>/
  initial/                  不变的原始快照
  preparation/state/        主会话只读副本
  preparation/              主会话ID、逐次prompt/answer、MCP日志、选择状态
  rounds/01/initial/        本轮原始快照
  rounds/01/final/          本轮运行状态
  rounds/01/                执行会话ID、逐次prompt/answer、工具日志、结果
  rounds/02/ ...
  decisions.jsonl           动作、依据、轮次、解析错误和恢复记录
  agent_result.json         与下游兼容的候选结果
```

所有路径由程序按候选ID/轮次生成，模型不得指定日志、会话或快照路径。所有LLM调用继续进入现有llm_calls留档，补充phase/round/session关联，不覆盖旧记录。

## 下游映射

不增加 pipeline step，保持 Step3 Input/Output 的 tasks 结构。

- objective：接收轮目标。
- chain/tool_calls/raw_tool_calls：仅接收轮的真实记录，repair 续接累计在该轮，不混入前置或旧轮。
- execution.initial_state/final_state：接收轮相对路径，IO 与独立评测按该路径读取，不要求固定 final 目录；须核查硬编码消费者。
- execution.answer：接收轮最后一份完整回答；主会话不能自行重写它。
- execution.attempts：各轮索引、目标、状态和记录路径；原来为空，现在留档，不复制整套大日志进每项。
- llm_review.reason/logic_reason：主会话最终交付依据，明确区分前置、旧轮和最终轮；附最终执行 reason 的来源，防止下游把前置发现当完成证据。
- logic_score：主会话接收时评分，保留原有完成后筛选方式。
- 全部失败或未入选候选仍保存原始快照、日志、回答和拒绝理由，不删除。

## 实施位置与顺序

1. `codex.py`：为新流程增加显式持久会话及按ID续接能力，保留旧调用默认行为；`review_agent.py` 保持环境工具访问限制。
2. `task_eval_mcp.py`、`review_choices.py`：续接恢复方案选择状态、累计预算，继续将工具错误返回模型，不新增业务回退默认值。
3. `execution_agent.py`：加载拆分prompt、校验两个输出契约、落实控制循环、隔离各轮状态、选取最终链，复用 meaningful_calls、日志留档和现有结果筛选。
4. `contracts.py`：更新Step3字段说明，不把内部会话调度对象塞进各阶段输入；`config/tool_graph*.yaml` 增加 max_rounds=3、target_tool_calls=20、min_tool_calls=10，代码同样保留默认值；网络默认开启、执行会话显式关闭。
5. 更新主会话使用的方案选择说明：只在主会话使用，并与主prompt规则合并去重，避免“强制替用户选择”旧措辞重新注入；不在执行prompt重复拼接。
6. 核对 run_io、Step4/5、可视化、独立评测消费者对状态路径和attempts的读取；不重写不相关模块。kimi 的 runtime/binding 适配尚属另一支线，实施时保留其接入点，不自行合并整个分支。

本轮仅准备文案与协议。尚不能在现有代码上直接启用这些prompt：缺少会话续接、动作解析、状态重置和轮次控制时，模型输出的新契约会被旧解析器拒绝。既有用户要求不跑测试，本轮未运行任何测试或模型。
