# Kimi 独立评测接入与试跑说明

分支 `kimi`；主管线不变。独立评测可用 `llm.agent_backend: kimi` 切换，未设置仍用原 ReAct。
Kimi 是执行循环，模型通过 `llm.model` 选择，不要求使用 Kimi 模型。

## 改了什么

- `task_eval.py`：新增后端分流和 `--agent-backend` 参数；输入任务、环境状态、工具执行器及 verifier 逻辑不变。
- `task_eval_kimi.py`：接收原来相同的任务文本、状态目录、工具配置和 trace 路径；启动独立 SDK 进程，返回最终答案字符串。
- `task_eval_kimi.mjs`：使用官方 SDK 管理消息循环，通过现有 MCP 服务执行工具；保存请求、事件、上下文和 usage。
- `config/task_eval_kimi.yaml`：独立配置，使用已有管线凭据，不修改对话的凭据。

工具执行仍由已有的 bubblewrap 沙箱和状态提交逻辑负责。Kimi 工作目录是单独的空目录；工具白名单只允许当前环境的 MCP 工具，内置 Read、Shell、网络、子代理等均不开放。
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
| `llm.kimi.max_context_size` | 显式声明后端上下文预算；不能改变服务端实际限制 |
| `llm.kimi.max_steps_per_turn` | 模型循环上限；默认工具预算 × 2 + 10 |
| `llm.kimi.max_attempts_per_step` | 每步 API 尝试次数，默认 3 |
| `llm.kimi.timeout_seconds` | 整个 agent 执行时限；默认沿用 `llm.timeout_seconds` |
| `llm.kimi.reserved_context_size / compaction_trigger_ratio / compaction_max_attempts` | 压缩预留 token、触发比例和尝试次数；代码默认 50000 / 0.85 / 3 |
| `execution.evaluation_max_tool_calls / evaluation_max_concurrency` | 工具调用预算和任务并发；CLI > YAML > 默认 50 / 1 |

原 ReAct 的 `response_format`、`format_retry_count` 不用于 Kimi。当前模型协议只支持 OpenAI-compatible Chat Completions。

**用户暂缓的长返回值与 Read 方案：** SDK 超过 50,000 字符时会把内容存入文件，返回预览并提示使用 Read/Grep；目前禁用了这些工具，因此模型无法读取被省略的内容。审查用 80,000 字符结果复现了这一点：下一轮仅看到约 2,391 字符，中间的标记丢失。原始内容仍保存在 `tool_calls.jsonl` 中。用户已表示 Read 并非不能开放，但具体访问范围暂不决定，本轮保留现状；工具端 summary 也暂缓。SDK 压缩摘要中的历史文件恢复指针同样受当前工具限制。已验证小窗口压缩并继续执行，尚未做大型任务集或长上下文压力测试。

## 留档与已发现的 SDK 接口差异

每次调用在状态目录旁的 `<目录名>.agent/` 留档：`agent.md`、`llm_requests.jsonl`、
`llm_responses.jsonl`（按 request_id 对应原始 SSE/JSON 响应）、`system_prompts.jsonl`、
`effective_config.json`、`events.jsonl`、`context.json`、`result.json`（含 usage）、
`tool_calls.jsonl` 和 `timing.json`。断流保留已收到的原始片段及错误；超时先取消并保存上下文，10 秒内不能退出则强制终止。
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
python -m pytest tests/test_task_eval.py tests/test_task_eval_react.py tests/test_task_eval_kimi.py -q
```

新增测试运行真实官方 SDK 与真实 MCP/沙箱，仅模型响应使用本地可控 HTTP 服务。
覆盖越权 Read 被拒绝、公开契约与结果回传、usage、循环上限，以及同轮多调用的结果对应关系。
真实 Sol 试跑产物：`runs/kimi_smoke/20260916_171006_114568/report.json`；3 次调用，989 → 996，状态与答案检查通过。
新版完整配置的 Sol 试跑：`runs/kimi_smoke/20260916_181618_613815/report.json`，407 → 414，3 次工具调用，耗时 14.43 秒。
该次 usage：普通输入 5416、缓存输入 13952、输出 176 tokens；未据此推算价格。
小窗口测试产生 `compaction.completed`，上下文从 11572 tokens 压缩为 871 后继续执行。
Terra 基础版试跑返回服务端 503；本机 Qwen 8000 端口无响应。本次不声称完成了 Qwen 或正式任务集评测。
全部要求、参数映射与验收记录见同目录 `KIMI_REQUIREMENTS.md`。
