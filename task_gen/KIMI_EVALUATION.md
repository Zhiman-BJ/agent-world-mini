# Kimi 独立评测接入与试跑说明

分支 `kimi`；主管线不变。独立评测可用 `llm.agent_backend: kimi` 切换，未设置仍用原 ReAct。
Kimi 是执行循环，模型通过 `llm.model` 选择，不要求使用 Kimi 模型。

## 改了什么

- `task_eval.py`：新增后端分流和 `--agent-backend` 参数；输入任务、环境状态、工具执行器及 verifier 逻辑不变。
- `task_eval_kimi.py`：接收原来相同的任务文本、状态目录、工具配置和 trace 路径；启动独立 SDK 进程，返回最终答案字符串。
- `task_eval_kimi.mjs`：使用官方 SDK 管理消息循环，通过现有 MCP 服务执行工具；保存请求、事件、上下文和 usage。
- `config/task_eval_kimi.yaml`：独立配置，使用已有管线凭据，不修改对话的凭据。

工具执行仍由已有的 bubblewrap 沙箱和状态提交逻辑负责。Kimi 工作目录是单独的空目录；工具白名单只允许当前环境的 MCP 工具和受限 `read_tool_result`，内置 Read、Shell、网络、子代理等均不开放。
这是 SDK 工具权限限制，不是新增了一个包住整个 Kimi 进程的操作系统沙箱。

## 官方 SDK 与启动

已在本机构建：`/data1/home/tianfang/kimi-code/packages/node-sdk/dist/index.mjs`。
测试源码提交：`486dcd26c76f2854fc351515a45d8f0fc2d31ed6`，SDK 版本 `0.20.0`。
构建环境：Node 24.15.0、pnpm 10.33.0；SDK 源码未修改。

```bash
export KIMI_CODE_SDK=/data1/home/tianfang/kimi-code/packages/node-sdk/dist/index.mjs
python scripts/run_kimi_smoke.py --model gpt-5.6-sol
python -m task_gen.task_eval --config config/task_eval_kimi.yaml --input-root /path/to/task/run --model gpt-5.6-sol --limit 1
```

Python 使用项目已有虚拟环境。也可将 SDK 路径写为 `llm.kimi.sdk_path`。
如需重建官方 SDK，在其仓库执行 `npx --yes pnpm@10.33.0 install --frozen-lockfile`，
然后在 `packages/node-sdk` 执行 `../../node_modules/.bin/tsdown`。

## 消息、工具与终止

模型通过原生 function/tool calls 请求工具，不再自行输出 ReAct 的 action/finish JSON。
MCP 返回值由 SDK 转成对应 tool 消息，保留 tool_call_id；下一轮模型看到此前的调用及结果。
同一回答允许多个调用，目前沿用 SDK 对 MCP 工具的串行调度，未增加工具执行并发。
SDK 会对同一步内名称和参数完全相同的调用去重，多个消息可共用一次实际执行结果；不能依靠同一步重复调用实现重复写入。连续相同调用还会触发 SDK 的 3/5/8 次提醒及 12 次重复熔断。此行为暂未修改，原始事件和实际执行 trace 可分别核对。
`usageConditions` 和 `outputSchema` 放入公开工具描述，`internal.code` 不发送给模型。

工具预算仍在 MCP 服务中强制执行；循环上限是另一个独立限制。
只有正常完成且提交非空最终答案才算 agent 正常结束；循环上限、中断、API 错误不会被当作成功。
执行成功与任务完成是两回事，正式评测仍需经过原 verifier。

## 配置对应关系

| 配置 | 含义 |
|---|---|
| `llm.model / base_url / api_key_file` | 复用已有管线模型配置 |
| `llm.temperature / max_tokens` | 采样温度、单次模型输出上限 |
| `llm.kimi.system_prompt` | 可直接在 YAML 修改 system prompt 正文 |
| `llm.kimi.parallel_tool_calls` | 请求模型是否允许同一回答返回多个调用；不是工具执行并发 |
| `llm.kimi.tool_result_page_chars` | 长返回值预览与单页字符上限，默认 6000，允许 1–12000；由我们的适配层实现 |
| `llm.kimi.max_context_size` | 显式声明后端上下文预算；不能改变服务端实际限制 |
| `llm.kimi.max_steps_per_turn` | 模型循环上限；默认工具预算 × 2 + 10 |
| `llm.kimi.max_attempts_per_step` | 每步 API 尝试次数，默认 3 |
| `llm.kimi.timeout_seconds` | 整个 agent 执行时限；默认沿用 `llm.timeout_seconds` |
| `llm.kimi.reserved_context_size / compaction_trigger_ratio / compaction_max_attempts` | 压缩预留 token、触发比例和尝试次数；代码默认 50000 / 0.85 / 3 |
| `execution.evaluation_max_tool_calls / evaluation_max_concurrency` | 工具调用预算和任务并发；CLI > YAML > 默认 50 / 1 |

原 ReAct 的 `response_format`、`format_retry_count` 不用于 Kimi。当前模型协议只支持 OpenAI-compatible Chat Completions。

**长返回值读取：** SDK 原生超过 50,000 字符会外存并指示使用 Read/Grep，外存本身也有保留上限。现在在它之前分页：短返回保持原样，长返回给出 `result_id / total_chars / offset / next_offset / has_more / content`，内容是原始 JSON 文本的一段，不是摘要。模型用 `read_tool_result(result_id, offset, length)` 按需读取后续或任意位置；offset/length 按 Unicode 字符计数，页面可能切在 JSON 字段中间，只有拼接完整后才是完整 JSON。

编号只能查到当前 Kimi 适配进程已有的长结果；实现不接受文件路径、不读取环境状态目录。完整原文在进程内保留到运行结束，永久记录仍是 `tool_calls.jsonl`；`result_index.jsonl` 将结果编号对应到业务调用序号。辅助读取另记 `result_reads.jsonl`，不消耗环境调用预算，但受模型步数和总时限限制。目前内存占用随长结果累计增长，尚未改成磁盘缓存；会话结束后不能续用旧编号。没有添加摘要，也不开放 SDK 历史文件恢复指针。

正式环境 MCP 使用同事的共享协议，保留原业务 outputSchema。仅 Kimi 的展示适配层 `task_eval_kimi_mcp.py` 为了容纳分页信封，不向 SDK 声明结构化 outputSchema，而将它完整放入 description；Python 执行器仍按原 outputSchema 校验并决定状态是否提交。`read_tool_result` 也仅在这层注册，不进入任务 available_tools。这个适配层接收标准 handler 的完整结果再处理，不修改官方 SDK。工具自身的语义分页优先使用，长结果兜底作为补充。

## 共享 MCP 与交付包接入（2026-09-17）

共享代码来自 `/data1/agent_world/kimi-mcp-integration-20260917`：`env_gen/tool_gen/mcp_protocol.py` 原样迁入；`kimi_mcp.py`、`delivery.py`、binding Schema 和测试随同迁入。迁入后修正了虚拟环境 Python 路径不能提前 resolve 的问题，避免跳过 profile 中安装的依赖。`runtime.py` 仅引入 software_root 支持。

新任务 `available_tools` 与 ToolGen、TaskGen 的正式环境 MCP `tools/list` 共用 `public_tools()`，包含名称、合并 usageConditions 后的说明、输入和输出 Schema。旧任务只接受与原环境逐字段一致的旧投影，以便复用历史任务；其他工具差异仍拒绝。业务失败保持原有 `success=false` 对象，不再套一层导致错误内容被客户端遮蔽。审查辅助工具和 Kimi 辅助读取不属于环境工具集合。

在 YAML 中设置 `llm.kimi.binding_path` 即可选用正式交付包。启动时核对任务工具（含执行代码）及环境定义，拒绝不匹配。记录库和文件仍来自每个任务的初态副本，沿用 bubblewrap、只读资源检查、失败回滚以及原 verifier；不把交付包默认初态当成任务初态。依赖 profile 的 Python 用于确定解释器与 site-packages，依赖目录只读挂载，提供 `context.software_root=/software`。没有绑定包时继续支持现有 bundle 内的工具与环境。

直接查看同事标准配置：`python -m env_gen.tool_gen.kimi_mcp /path/to/binding.json --print-kimi-config`。该入口从交付包默认初态启动 ToolGen runtime，是环境级服务；正式任务评测使用上述任务级入口，不能混用初态。原生共享配置和 Kimi 展示适配层的工具表不是同一层，不能将后者附加的辅助工具算进环境工具一致性等式。

依赖包需适配运行主机。venv 启动链接所依赖的基础 Python 必须存在；旧包若把 python_path 写成 base Python，需要修正交付映射或重新发布。这里没有自动猜测错误映射，也没有把依赖或任意主机目录开放给模型。

绑定软件模式限制 OpenBLAS/OMP/MKL 为单线程，避免多核主机上的默认线程池在导入时耗尽 2 GiB 沙箱内存。已用同事实际 calibration profile 的 NumPy 2.4.6 验证导入和数组计算；没有修改该 profile 或其交付包。

本次集成验收：

- 真实 Kimi SDK + MCP 集成测试覆盖 binding 加载、任务专属初态、工具集合一致、原包状态不变；模型响应使用可控 HTTP 服务。
- 真实 Sol 长结果试跑：`runs/kimi_smoke/20260917_205924_174501/report.json`，600083 字符结果中的凭据可读取，380 → 387，3 次业务调用、1 次辅助读取，检查通过。
- 真实 Hugeicons task4：`runs/kimi_mcp_real_task/20260917_205925_545616`，18 次业务调用，Kimi 耗时 228.42 秒，原 verifier 3/3 通过。两次业务失败完整反馈后恢复。verifier 另记录非阻断问题：附加 Markdown 文件不如最终回答完整，以及字体“有效”的措辞超过实际检查范围；这不是整套任务集质量评测。
- 同事已有 calibration 包的 software mapping 指向 base Python，缺少包依赖；源 venv 可用。迁入代码已修复新发布包的 launcher 路径保留，旧包仍需上游修正或重新发布。这项旧产物问题未通过修改同事目录来掩盖。

## 留档与已发现的 SDK 接口差异

每次调用在状态目录旁的 `<目录名>.agent/` 留档：`agent.md`、`llm_requests.jsonl`、
`llm_responses.jsonl`（按 request_id 对应原始 SSE/JSON 响应）、`system_prompts.jsonl`、
`effective_config.json`、`events.jsonl`、`context.json`、`result.json`（含 usage）、
`tool_calls.jsonl`、`result_reads.jsonl` 和 `timing.json`。断流保留已收到的原始片段及错误；超时先取消并保存上下文，10 秒内不能退出则强制终止。
请求日志没有认证头；API key 通过 stdin 传给进程，不放入命令行。临时 SDK home 在结束后清理。
失败时仍保留已有日志；若尚未进入生成阶段，可能没有模型请求或上下文文件。

当前 SDK 声明的 `createSession(agentProfile, agentFiles)` 没有传入引擎；接入改用
`extraAgentDirs` 覆盖临时 home 中的默认 profile，并设置全局工具白名单。
MCP 必须在创建 session 前注册，否则不会进入该 session 的初始工具集合。
每次发送模型请求前检查实际工具表，出现额外工具或非空工具表数量不符就拒绝请求；没有工具的辅助模型请求可以通过。
当前 SDK 的 modelOverrides 温度没有进入实际请求，适配层直接写入标准 API 的 temperature 和 parallel_tool_calls；测试检查最终 HTTP 请求，避免配置看似生效但实际被忽略。

## 验证

```bash
KIMI_CODE_SDK=/data1/home/tianfang/kimi-code/packages/node-sdk/dist/index.mjs \
python -m pytest tests/test_task_eval.py tests/test_task_eval_react.py tests/test_task_eval_kimi.py tests/test_tool_result_reader.py -q
```

新增测试运行真实官方 SDK 与真实 MCP/沙箱，仅模型响应使用本地可控 HTTP 服务。
覆盖越权 Read 被拒绝、公开契约与结果回传、usage、循环上限，以及同轮多调用的结果对应关系。
真实 Sol 试跑产物：`runs/kimi_smoke/20260916_171006_114568/report.json`；3 次调用，989 → 996，状态与答案检查通过。
新版完整配置的 Sol 试跑：`runs/kimi_smoke/20260916_181618_613815/report.json`，407 → 414，3 次工具调用，耗时 14.43 秒。
该次 usage：普通输入 5416、缓存输入 13952、输出 176 tokens；未据此推算价格。
小窗口测试产生 `compaction.completed` 并继续执行；分页后使用 4096 token 测试窗口。
长结果试跑命令：`python scripts/run_kimi_smoke.py --long-result --model gpt-5.6-sol`。
产物 `runs/kimi_smoke/20260917_012857_674755/report.json`：600083 字符返回中取得尾部凭据，157 → 164，3 次业务调用、2 次辅助读取，21.32 秒，检查通过。
另有正式 Hugeicons task4 单例 `runs/kimi_real_task/20260916_194130_051270`，verifier 3/3 通过；尚未完成整套任务集评测。
Terra 基础版试跑返回服务端 503；本机 Qwen 8000 端口上次无响应。本次不声称完成了 Qwen 评测。
全部要求、参数映射与验收记录见同目录 `KIMI_REQUIREMENTS.md`。
