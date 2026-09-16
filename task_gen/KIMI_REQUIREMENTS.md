# Kimi 适配要求、参数对照与执行清单

基线提交：`1b61ee2`。本文件记录当前已讨论的要求，不把 Kimi 自带行为当成我们的隐含需求。
用户已批准在 `kimi` 工作区逐项适配并自动试跑；Read 与长返回值方案明确暂缓。

## 要求清单

| 编号 | 要求 | 实现位置／处理方式 | 验收 |
|---|---|---|---|
| R1 | 可替换原 ReAct，保留原入口 | `task_eval._run_agent` 按 agent_backend 分流 | 原有评测测试通过 |
| R2 | 只用环境工具，不能直接读初态或 internal code | SDK profile + 全局白名单 + 请求检查；Read 是否受限开放暂缓 | 强制请求 Read，不能读取隐藏内容 |
| R3 | 输入格式对齐 | 原任务文本和公开环境信息；公开 usageConditions、输入输出契约 | 请求包含公开契约、不包含 internal.code |
| R4 | 输出格式提取与适配 | 原生 tool calls；提取最终 assistant 文本，返回原字符串接口 | 非空完成、工具 trace 原格式 |
| R5 | 同一轮多工具调用可配置 | OpenAI `parallel_tool_calls` 请求参数；环境工具仍串行执行 | true/false 请求生效，两个不同调用均回传 |
| R6 | system prompt 可抓取、可修改 | YAML `llm.kimi.system_prompt`；保存 profile、实际 system 消息 | 自定义文本实际到达模型 |
| R7 | 工具返回消息队列正确 | SDK 维护 assistant tool_calls → 对应 tool_call_id 的 tool 消息 → 下一轮 | 多调用结果对应、不丢失、不串任务 |
| R8 | loop 配置可控、含义清晰 | 最大模型步数、每步 API 尝试数、工具调用预算分开 | 达步数上限不认作完成；API 重试不重放已执行工具 |
| R9 | 上下文上限明确 | SDK models.maxContextSize；不能提高服务端实际上限 | effective_config 留档、压缩入口测试 |
| R10 | 上下文压缩参数可调 | 预留 token、触发比例、压缩尝试数 | 小窗口触发压缩，压缩请求不被白名单误拦截 |
| R11 | 工具结果过长的配置与处理 | 已定位 SDK 固定 50,000 字符外存机制 | **用户暂缓**；不擅自开放 Read |
| R12 | 是否需要工具端 summary | 需结合 R11 决定；目前不增加工具或摘要调用 | **用户暂缓** |
| R13 | 模型与凭据独立 | 复用管线 api_key_file；SDK 用临时 home | 不修改当前对话配置，日志无 API key |
| R14 | prompt、answer、阶段与 usage 留档 | 实际请求、原始 HTTP 响应、SDK 事件、上下文、usage、有效配置 | 每个 HTTP 尝试有 request_id；失败也留档 |
| R15 | 超时、错误、空答案可区分 | 超时先取消保存上下文，再硬终止；非正常结束抛错 | 超时保留记录、空答案不通过 |
| R16 | 任务并发与工具并发区分 | 任务沿用 run_evaluation 线程池，session/home/trace 独立；工具仍串行 | YAML 和 CLI 优先级，任务隔离测试 |
| R17 | 不改变 verifier 和任务生成流程 | 仅独立评测执行器适配；结果交给已有验收接口 | 原回归测试；真实小任务试跑 |
| R18 | SDK 隐含策略透明 | 同步内调用去重、重复调用提醒/熔断等记录下来，不擅自重写 | 文档指出与旧 ReAct 差异 |

## 参数对照（YAML 为可编辑入口）

| YAML 字段 | Kimi／实际作用 | 来源 |
|---|---|---|
| `llm.agent_backend` | `react` / `kimi` 入口 | 我们的适配层 |
| `llm.model / base_url / api_key_file` | models / providers；key 仅送到临时配置 | SDK 已有接口 |
| `llm.temperature / max_tokens` | 请求 temperature / models.maxOutputSize | 温度由适配层传入；输出上限用 SDK 接口 |
| `llm.timeout_seconds` | 整次执行秒数，可被 kimi.timeout_seconds 覆盖 | 适配层进程管理 |
| `llm.kimi.sdk_path / node` | 官方 SDK 构建入口、Node 命令 | 适配层；sdk_path 也可用 KIMI_CODE_SDK |
| `llm.kimi.system_prompt` | 默认 agent 的完整正文，不继承编程助手 prompt | 适配层加载；SDK extraAgentDirs |
| `llm.kimi.parallel_tool_calls` | 发往 Chat Completions 的同轮多调用开关 | 适配层传给标准 API 字段；不是工具执行并发 |
| `llm.kimi.max_context_size` | models.evaluation.maxContextSize | SDK 已有接口 |
| `llm.kimi.max_steps_per_turn` | loopControl.maxStepsPerTurn | SDK 已有接口 |
| `llm.kimi.max_attempts_per_step` | loopControl.maxAttemptsPerStep，包含首次尝试 | SDK 已有接口 |
| `llm.kimi.reserved_context_size` | loopControl.reservedContextSize | SDK 已有接口 |
| `llm.kimi.compaction_trigger_ratio` | loopControl.compactionTriggerRatio | SDK 已有接口 |
| `llm.kimi.compaction_max_attempts` | loopControl.compactionMaxAttempts | SDK 已有接口 |
| `execution.evaluation_max_tool_calls` | MCP 强制工具执行预算；CLI --max-tool-calls 优先 | 我们现有工具服务 |
| `execution.evaluation_max_concurrency` | 同时执行的任务数；CLI --max-concurrency 优先 | 我们现有线程池 |
| `execution.tool_timeout_seconds / tool_max_memory_bytes / tool_max_write_bytes` | 单次工具的时限、内存与写盘限制 | 我们现有工具沙箱 |

没有加入无效的 `tool_result_max_chars` 或 `summary` 配置。当前 SDK 无相应公共开关，不能写进 YAML 假装已经生效。
默认参数集中列在 `config/task_eval_kimi.yaml`；代码保留同样的合理默认值，参数拼写错误应报错。

## 执行计划

按本清单在当前工作区顺序实现，使用已批准的设计，不再请求选择执行方式。

- [x] 提交当前基础版并跑 29 项回归测试。
- [x] 参数与 prompt：修改 `task_eval_kimi.py`、YAML；在原集成测试加入配置注入断言，校验请求里的温度、max_tokens 和 system prompt。
- [x] 多调用与压缩：修改 `.mjs` 请求适配；检查额外工具但允许无工具的辅助请求；补两个工具结果与压缩测试。当前 SDK 的压缩请求也带工具表，通过 prompt 要求只生成摘要。
- [x] 日志与失败：按 `request_id` 保存 request/HTTP response，保存脱敏有效配置；SIGTERM 取消后导出上下文，再由 Python 超时兜底；补 API 重试、空答案与超时测试。断流原始片段同样保存。
- [x] CLI：已有参数改为 `None` 时才回退 YAML，再回退 50/1；补 CLI 覆盖测试，保留原 ReAct 默认行为。
- [x] 验证：`KIMI_CODE_SDK=... python -m pytest tests/test_task_eval.py tests/test_task_eval_react.py tests/test_task_eval_kimi.py -q`：42 passed，35.94 秒；真实 Sol 试跑通过。
- [x] 更新每项验收证据、未完成项与试跑结果；本文件随适配版本提交，保留 kimi 分支，不合并 main。

R11/R12 为用户主动暂缓，不计作本轮必须实现的项；其余项必须有实现或明确的原生接口对应。

## 验收证据

测试文件：`tests/test_task_eval_kimi.py`，运行真实 SDK、真实 MCP 与真实沙箱，模型 HTTP 响应可控。

| 覆盖要求 | 测试或产物 |
|---|---|
| R1/R17 | 原有 `test_task_eval.py`、`test_task_eval_react.py` 回归 |
| R2/R3/R6/R9/R13 | `test_kimi_rejects_builtin_preserves_public_contract_and_observation`：Read 拒绝、公开契约、真实请求参数、脱敏配置 |
| R4/R15 | 正常返回测试、`test_empty_final_answer_is_not_success`、`test_timeout_preserves_completed_tool_and_partial_context` |
| R5/R7 | `test_multiple_calls_return_both_results_before_next_model_step`：不同参数调用真实执行两次，结果按 ID 对应 |
| R8 | `test_kimi_step_limit_does_not_accept_intermediate_text`、`test_api_retry_does_not_reexecute_tool` |
| R10 | `test_small_context_triggers_compaction_without_tool_allowlist_error`：11572 → 871 tokens，压缩后继续完成 |
| R14 | 原始 SSE/HTTP 状态检查、`test_broken_response_preserves_received_bytes` |
| R16 | `test_concurrent_sessions_keep_tools_and_logs_separate`、`test_eval_cli_config_precedence` |
| R18 | `KIMI_EVALUATION.md` 记录官方同轮去重及连续重复提醒/熔断行为；未擅自改动 |

真实模型：`runs/kimi_smoke/20260916_181618_613815/report.json`，Sol 将未知初值 407 增加为 414，重新读取确认，14.43 秒，3 次工具调用。本轮没有做正式任务集效果评测。
