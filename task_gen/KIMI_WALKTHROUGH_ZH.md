# Kimi 接入讲解：面向熟悉 Python 的使用者

> 2026-09-18 最新决定：system prompt 改为 `${base_prompt}`，直接继承官方默认正文，不再追加自定义正文。工具白名单保持。详见 [重要事项记录及官方 prompt 全文](KIMI_IMPORTANT_NOTES.md)。本文下方关于精简 prompt 的描述是此前实现的历史，不再代表当前默认配置。

本文对应我们的 `kimi` 分支代码 `c1ca388`，官方 Kimi 源码 `486dcd26c76f2854fc351515a45d8f0fc2d31ed6`，Node SDK 0.20.0。更新日期：2026-09-18。

**已经读过旧版，请直接从第 13 节开始。**旧版只覆盖到 `5e55d12`；本次新增共享 MCP、交付 binding、运行依赖、科学库沙箱、流式回答恢复与新实测的实现讲解。它们不只是之前概念的重复。第 1–12 节也修正了失效描述；未提及的新 main 分支功能不自动算作当前 kimi 的功能。

阅读目标：你能解释一次任务怎样跑完、我们修改了什么、哪些能力来自官方，以及“不作弊”这个结论能成立到什么程度。本文中的 Python 对照是理解用的伪代码，不是另一套可执行实现。

## 先回答最重要的问题：能保证不作弊吗？

当前实现对一个具体问题有代码限制和测试支持：**执行任务的模型不能通过已禁用的内置 Read、Shell 等工具绕过环境工具，直接读取隐藏文件或改写状态。**它拥有的是我们注册的环境工具和专门读取既有工具结果的分页工具。

但不能据此承诺“任何环境、任何 SDK 版本、任何攻击下都绝不作弊”。这个更强的结论还取决于公开信息是否泄漏答案、环境工具是否越权、SDK 是否按配置执行，以及 verifier 是否正确。整个 Node/SDK 进程没有新增操作系统级隔离；目前也没有完成所有工具和公开元数据的安全审计。

对外准确的说法是：

> 我们限制并测试了任务执行模型的工具入口，使用既有沙箱执行环境工具，保存可审查的请求、调用和结果。已验证指定越权尝试被拒绝；尚不宣称对任意环境和攻击实现绝对隔离。

后文第 9 节详细解释这个边界。不要将“verifier 通过”当作“执行过程一定没有作弊”的证据，两者检查的对象不同。

## 1. 先认识目录：没有“改过的 Kimi 源码”

我们没有修改官方 Kimi 源码。我们的工作是编写项目侧适配器，通过官方 SDK 使用 Kimi。

实际开发工作区：

```text
/data1/home/tianfang/agent-world-mini-zhiman/.worktrees/kimi
```

实际使用的官方 SDK 构建产物：

```text
/data1/home/tianfang/kimi-code/packages/node-sdk/dist/index.mjs
```

为了方便你在 VS Code 中看差异，另外准备了阅读副本：

```text
/data1/home/tianfang/kimi-code-review/
├── kimi-review.code-workspace
├── kimi-official/          官方源码，没有改动
└── agentworld-adapter/     我们的项目，显示接入前后的差异
```

`agentworld-adapter` 的 HEAD 是接入前 `66cf3e9`，文件内容是实现后 `5e55d12`，所以源代码管理面板可以逐文件对照。新增文件没有旧版，左侧空白是正常现象。这个目录是独立的阅读副本，之后开发区的新改动不会自动同步过去。

你可以先忽略官方源码的大部分文件，按下面的职责看我们自己的代码：

| 文件 | 一句话职责 | 是否本次新增 |
|---|---|---|
| `task_gen/task_eval.py` | 原有独立评测入口，现在可选择 ReAct 或 Kimi | 修改原文件 |
| `task_gen/task_eval_kimi.py` | Python 端准备输入、启动进程、控制超时、取回答案 | 新增 |
| `task_gen/task_eval_kimi.mjs` | JavaScript 端调用官方 SDK，配置会话、提交任务、留档 | 新增 |
| `task_gen/task_eval_mcp.py` | 任务级工具执行服务，核对交付、预算和状态提交 | 修改原文件 |
| `task_gen/task_eval_kimi_mcp.py` | 新拆出的 Kimi 展示层，处理分页与辅助读取 | 后续新增 |
| `env_gen/tool_gen/mcp_protocol.py`、`kimi_mcp.py`、`delivery.py` | 共享协议、正式交付加载及发布 | 同事代码迁入并适配，详见第 13 节 |
| `task_gen/tool_result_reader.py` | 只读取本次会话已经产生的长结果 | 新增 |
| `config/task_eval_kimi.yaml` | 使用者修改模型、参数和 system prompt 的入口 | 新增 |
| `scripts/run_kimi_smoke.py` | 真实模型的小型功能试跑 | 新增 |
| `tests/test_task_eval_kimi.py`、`tests/test_tool_result_reader.py` | SDK 集成与分页边界测试 | 新增 |

第一批接入没有重写状态沙箱、ReAct、任务生成和 verifier。后续交付适配为 Step0、contracts、执行路径和沙箱补充了 runtime/software 传递，并统一公开工具投影；不能继续笼统说主管线没有改过。verifier 的核心实现没有在这些适配中重写。

## 2. 六个词，以及它们在这里的含义

| 词 | 含义 | 本项目中的位置 |
|---|---|---|
| 模型 | 根据输入生成回答和工具调用请求 | Sol、Qwen 等；Kimi 框架不要求必须使用 Kimi 模型 |
| harness | 组织模型、消息、工具和执行生命周期的运行框架 | 官方 Kimi 的执行能力 |
| SDK | 供程序使用的一套开发工具和代码 | 官方 Node SDK |
| API/接口 | 可以调用的函数或服务及其约定 | `session.prompt()`、模型 HTTP API |
| MCP | 查询和调用工具等交互的标准协议 | Kimi 与我们的工具服务之间 |
| CLI | 通过终端命令使用程序的方式 | 我们用 Python 命令启动评测；不需要通过 Kimi 交互终端输入任务 |

SDK 可以提供很多 API；MCP 消息通过传输通道发送。这里的 MCP 使用本机进程的标准输入输出，即 `stdio`，不是通过公网访问一个工具网站。

## 3. 一次任务到底怎样执行

```text
已有任务文本、公开环境描述、初态副本
                    │
          task_eval.py 选择 Kimi
                    │
     task_eval_kimi.py 准备配置并启动 Node
                    │
     task_eval_kimi.mjs 调用官方 Kimi SDK
                    │
           Kimi 会话驱动模型运行
           ├── 模型 API：发送消息，接收回答或工具调用
           └── MCP：请求执行我们提供的工具
                          │
                  工具在既有沙箱中运行
                          │
                  返回结果，按规则提交状态
                          │
                  Kimi 将结果加入消息历史
                          └── 再次请求模型，直到结束
                    │
         最终回答 + 实际状态 + 调用记录
                    │
                 原 verifier
```

举一个已经试跑的例子：任务要求“计数器增加 7，确认保存结果”。模型先读取，得到 157 和更新凭据；再用凭据把值写成 164；最后重新读取并回答。Kimi 管理多轮消息，工具服务执行读写，我们的适配器负责启动、配置、日志和收尾。

### 3.1 主管线交给适配器什么

```python
run_kimi_agent(prompt, workspace, server_config, trace, llm_config) -> str
```

| 参数 | 程序拿它做什么 | 是否直接全部交给模型 |
|---|---|---|
| `prompt` | 提交任务及公开环境信息 | 是，作为任务输入 |
| `workspace` | 指向本次任务的实际环境状态 | 不把整个目录内容自动塞入 prompt |
| `server_config` | 启动工具服务，包含工具代码及执行配置 | 不把完整配置交给模型 |
| `trace` | 保存业务工具调用记录 | 不自动交给模型 |
| `llm_config` | 设置模型、凭据与运行控制 | 不把密钥作为任务文本交给模型 |

函数返回值只是最终回答字符串，但执行过程中另外会修改任务状态、产生调用记录。这三者共同交给下游验收。

`task_eval.py` 的主要入口修改是：

```python
if llm_config.get("agent_backend", "react") == "kimi":
    return run_kimi_agent(...)
return run_react_agent(...)
```

真实代码还会拒绝未知后端名称。原调用约定保持一致，所以下游无需为 Kimi 改写 verifier。

### 3.2 Python 为什么要启动 JavaScript

官方提供的是 Node.js SDK。我们使用 `.mjs` 文件直接调用它，`.mjs` 表示采用 ES Module 格式的 JavaScript 文件。Node 是在服务器上执行 JavaScript 的运行时。

Python 用 `subprocess.Popen` 启动 `node task_eval_kimi.mjs`，再将任务与配置编码为 JSON，通过标准输入发送过去。密钥不放进命令行参数。不同任务分别创建临时 home、工作目录和 SDK 进程，并过滤可能干扰本次配置的部分继承环境变量。

临时 home 用来保存此次 SDK 配置；空工作目录用来启动 agent。真实环境状态由 MCP 工具服务访问，两者不是同一个目录。空目录有助于减少意外上下文加载，但它本身不是文件访问沙箱。

## 4. 只会 Python，怎样看 `.mjs`

先认识这些语法就够阅读主要逻辑：

| JavaScript | 可以怎样理解 |
|---|---|
| `const x = ...` | 给变量赋值，之后不能重新给这个变量赋另一个值；对象内容不一定不可变 |
| `let x = ...` | 可以重新赋值的变量 |
| `{name: value}` | 类似 Python 字典；JavaScript 中常用 `obj.name` 读取属性 |
| `async function f()` / `await f()` | 类似 Python 的 `async def` / `await` |
| `(event) => { ... }` | 定义一个接收 event 的函数，用作回调 |
| `x?.name` | x 为 null/undefined 时不继续访问，结果为 undefined |
| `x ?? fallback` | 仅在 x 为 null/undefined 时使用默认值，不会把 0、false 都替换掉 |
| `[...items]`、`{...config}` | 类似列表展开、字典展开 |
| `.map(f)`、`.filter(f)` | 对每项转换、筛选，类似列表推导式 |
| `.at(-1)` | 取最后一项，类似 `items[-1]` |
| `throw new Error(...)` | 类似 `raise RuntimeError(...)` |
| `try / catch / finally` | 类似 `try / except / finally` |
| `Promise` | 一个未来会完成或失败的异步结果，类似 Future；不是线程 |

### 4.1 接收输入

```javascript
const input = JSON.parse(readFileSync(0, 'utf8'));
```

近似 Python：

```python
input_data = json.loads(sys.stdin.read())
```

数字 0 是标准输入，不是一个工具编号。

### 4.2 创建运行实例、设置参数

```javascript
const { createKimiHarness } = await import(...);
harness = createKimiHarness({ homeDir: input.home_dir, ... });
await harness.setConfig({ providers: ..., models: ..., loopControl: ... });
```

这里从 SDK 取出工厂函数，创建运行实例，设置模型和循环配置。可以理解成：

```python
harness = create_kimi_harness(home_dir=...)
await harness.set_config(model=..., loop_control=...)
```

配置中 `evaluation` 是我们给模型配置起的别名，不是模型名称。该别名再指向 `gpt-5.6-sol` 等真正的模型。

### 4.3 先接工具，再创建会话

```javascript
await harness.addMcpServer(input.mcp);
session = await harness.createSession({ ... });
```

`harness` 管全局配置和会话创建，`session` 管一次会话的消息和运行状态。当前 SDK 会在会话创建时获取初始 MCP 基线，因此我们先注册工具服务。

### 4.4 回调与结束通知

```javascript
session.onEvent((event) => {
  append('events.jsonl', event);
  if (event.type === 'turn.ended') resolveDone(event);
});
await session.prompt(input.prompt);
const ended = await done;
```

近似 Python 思路：

```python
finished = Future()

def on_event(event):
    save(event)
    if event.type == "turn.ended":
        finished.set_result(event)

session.register_event_handler(on_event)
await session.prompt(task)
ended = await finished
```

回调是“事件发生时请执行这个函数”，不是我们不停轮询。先监听再提交，可以接到任务运行中发出的事件。等待 `done` 是等待结束通知，不仅仅是任务提交成功。

### 4.5 最终答案怎么拿

程序通过 `getContext()` 获取上下文，向前收集历史末尾连续的、没有工具调用的 assistant 消息，并按原顺序拼接文本。此前只取最后一条，会丢失流式报错前已生成的回答片段；修复细节在第 17 节。结束原因仍必须为 `completed`，正文必须非空，否则报告失败。

“模型正常结束”不等于“任务已经做好”；只有后续验收才判断任务完成度。

## 5. 按实际推进顺序，我们改了什么

### 第一版：接入执行器，提交 `1b61ee2`

增加 Kimi 后端选择，新增 Python 与 JavaScript 两侧桥接，连接现有 MCP 工具服务。配置专用 agent，限制工具范围，保存基础运行结果。原来的 ReAct 后端仍可使用。

使用官方 SDK 的工具调用与消息循环，没有自写一套重复循环。我们新增的是调用 SDK 的代码，不是修改 SDK。

### 第二版：参数、日志和运行控制，提交 `6d993da`

把已确认的参数放进 YAML；增加参数合法性检查、实际请求和响应留档、超时取消与失败收尾、CLI 配置覆盖。验证同轮多调用、API 重试、压缩和任务隔离。

遇到的 SDK 行为差异：

- 当前路径没有实际加载创建会话参数中的 agent profile，因此改用 `extraAgentDirs` 加载我们生成的配置。
- 温度没有沿原先尝试的参数路径进入请求，因此由桥接程序在实际请求上设置。
- MCP 先注册再创建 session，确保初始工具表正确。

这说明“配置里有一个字段”不代表“该版本已经按预期执行”。我们查看真实请求并测试行为。

### 第三版：正式任务暴露的 MCP 契约兼容，提交 `4b61a51`

有些环境 outputSchema 只在 `oneOf` 中声明对象，MCP 客户端要求顶层对象类型。我们在自己的服务对外声明中添加包装，内部仍按原始 schema 验证。

Hugeicons 正式任务随后跑通，但还遇到工具错误包装和 MCP 二次 schema 校验不一致的情况：真实业务错误被 SDK 的 schema 错误遮住。该次任务最终交付通过，不代表过程没有问题。

### 第四版：长结果分页，提交 `5e55d12`

官方长结果机制提示模型用 Read/Grep 读取外存文件，但我们不开放这些工具，且官方外存本身也有保留长度限制。于是我们在结果到达 SDK 前自行保留原文，返回有限预览，并提供受限的结果读取工具。

Kimi 分页模式下，不再把业务 outputSchema 作为 MCP 传输结果的 schema 声明，因为分页信封本来就不是业务对象；原 schema 仍展示在工具描述中，并由 Python 执行器校验。未启用分页的原调用路径仍保留原有声明方式。

这消除了 Kimi 这一路对业务 schema 的重复传输层校验，但正式 MCP 工具包仍需按其真实返回格式对齐，不能宣称所有上游契约问题都已解决。

## 6. 模型能看到哪些信息

第一轮主要包括：system prompt、任务输入、工具定义。后续会加入模型自己的回答、工具调用和工具返回；长历史还可能被压缩成摘要。

| 信息 | 当前处理 |
|---|---|
| 任务文本 | 传给模型 |
| 公开环境描述 | 由 `_public_environment()` 选取后传给模型 |
| 工具名称、描述和 inputSchema | 经 MCP 工具列表提供 |
| usageConditions、outputSchema | 共享 public_tools 将 usageConditions 组织为说明；Kimi 展示层把 outputSchema 加入 description |
| 工具 internal.code | 不作为工具定义发给模型；保留给服务端执行 |
| 完整任务状态文件 | 不直接全部塞入模型输入；需要通过工具观察 |
| 参考答案、参考执行链、verifier 代码 | 此处的任务 prompt 构造没有主动加入这些内容 |
| 模型 API 密钥 | 给程序发送认证请求使用，不作为任务消息文本 |

**需要特别注意公开信息筛选的实际粒度。**新版环境的 `_public_environment()` 保留 `environment_id`、`name`、`summary`、`description`、`record_sets`、`relationships`、`filesystem_scopes`。它是顶层字段选择，不会递归删除这些对象内部的敏感字段。

所以“只传公开环境”是函数的用途，不是对上游数据已经安全的证明。若这些对象中包含实例记录、答案提示、内部路径或其他不应公开信息，就仍可能进入 prompt。具体是否泄漏要检查具体环境和实际请求；本次核对发现的是这个防护边界，不是在没有证据的情况下断言某次已泄漏答案。

同样，description 和 usageConditions 直接提供给模型；若上游把内部内容写进公开说明，也不会被这里自动识别清理。

## 7. 工具权限和消息怎样落到代码

### 7.1 工具范围

Python 生成的 agent 配置包括：

```yaml
tools: ["mcp__agent_world_eval__*"]
disallowedTools: ["select_tools"]
subagents: []
```

全局 SDK 配置也使用同样的启用前缀并排除 `select_tools`。我们的 MCP 服务名为 `agent_world_eval`，其工具带这个前缀；内置 Read、Shell 不匹配。

这不是只用 prompt 要求模型遵守。模型即使虚构 Read 调用，执行器也必须有对应可用工具才能执行，已测场景中该入口被拒绝。

请求发送前还有检查：非空工具表数量必须符合预期，每个工具名必须以 `mcp__agent_world_eval__` 开头。它是数量与前缀检查，**不是完整名称集合逐一比较，也不是工具 schema 内容哈希检查**。无工具的辅助请求允许通过。

`permission: 'yolo'` 只表示已开放的操作不再等待人工批准。它不等于恢复被禁用的工具。自动任务评测需要这一点，否则每次操作都可能卡在审批上。

### 7.2 模型提出调用后，谁执行

MCP 服务端只接受它已注册的工具名，检查 arguments 类型和 inputSchema，再调用现有工具执行器。执行器操作候选状态副本；调用成功、业务 success 标记和 outputSchema 校验通过后才提交状态。异常或不合约返回不会把候选副本作为成功状态提交。

注意：schema 校验只保证结构符合约定，不保证工具业务逻辑一定正确，也不保证返回内容没有泄漏。

### 7.3 工具结果怎样进入下一轮

以两个不同调用为例，概念上模型 API 消息是：

```python
[
    {"role": "assistant", "tool_calls": [
        {"id": "call_a", "name": "read_counter", "arguments": {}},
        {"id": "call_b", "name": "get_metadata", "arguments": {}}
    ]},
    {"role": "tool", "tool_call_id": "call_a", "content": "...计数器结果..."},
    {"role": "tool", "tool_call_id": "call_b", "content": "...元数据结果..."}
]
```

真实 API 的字段嵌套略有不同，重点是每个工具结果通过调用 ID 对应请求。这由 SDK 管理，我们测试其真实传输结果；不是另建了一个外部消息队列服务。

`parallel_tool_calls: true` 允许模型一轮提出多个工具调用；当前环境 MCP 工具仍串行执行。如果 B 的参数需要 A 的结果，就应先得到 A 的结果再提出 B，不能靠同一轮多调用凭空获取尚未返回的信息。

官方还有同轮相同工具名和参数的去重，以及连续重复调用提醒/熔断。我们沿用这些行为，未重写。模型请求次数和真实工具执行次数因此不一定相等。

## 8. 参数、长结果、压缩和日志

### 8.1 不同预算不能混为一谈

以下是当前 `config/task_eval_kimi.yaml` 的配置，不是模型服务商承诺的能力：

| 配置 | 当前值 | 含义 |
|---|---:|---|
| `llm.temperature` | 0 | 生成随机性参数；不保证逐次完全一致 |
| `llm.max_tokens` | 8192 | 单次输出预算，不是整个任务累计输出上限 |
| `llm.timeout_seconds` | 600 | 一次 agent 执行时限；取消后的清理可能增加时间 |
| `kimi.max_context_size` | 131072 | 告诉 SDK 按多大上下文窗口规划；不能扩大服务端实际上限 |
| `kimi.reserved_context_size` | 50000 | 上下文管理的预留预算 |
| `kimi.compaction_trigger_ratio` | 0.85 | 自动压缩触发参数，与其他上下文预算共同起作用 |
| `kimi.compaction_max_attempts` | 3 | 压缩尝试数 |
| `kimi.max_steps_per_turn` | 110 | SDK 的任务循环步数限制，不等于全部 HTTP 请求数 |
| `kimi.max_attempts_per_step` | 3 | 每步模型 API 尝试数，包含首次尝试 |
| `kimi.tool_result_page_chars` | 6000 | 一页原始 JSON 文本字符数，不是 tokens |
| `execution.evaluation_max_tool_calls` | 50 | 业务工具调用预算，错误调用也可能消耗预算 |
| `execution.evaluation_max_concurrency` | 1 | 同时运行多少个独立评测任务 |
| `execution.tool_timeout_seconds` | 300 | 单次环境工具执行时限 |
| `execution.tool_max_memory_bytes` | 2147483648 | 单次工具配置的内存限制 |
| `execution.tool_max_write_bytes` | 268435456 | 单次工具配置的写入限制 |

当一个模型步骤输出三个工具调用，它可能只占一个模型步骤，却执行三次业务调用；API 失败重试会多发请求，但不会因为这次 API 重试就重放已经完成的工具。更外层的任务重试是另一件事，不应混淆。

配置遵循已接入 CLI 参数优先于 YAML，再到代码默认值。不是 YAML 每一项都对应一个独立 CLI 参数。`llm.kimi.timeout_seconds` 如设置，会覆盖普通 `llm.timeout_seconds`；CLI 的超时覆盖会相应传入 Kimi。

### 8.2 长结果分页

假设某次工具返回 600083 个字符。服务端完整序列化结果并保存，给模型：

```python
{
    "result_id": "随机生成的结果编号",
    "total_chars": 600083,
    "offset": 0,
    "next_offset": 6000,
    "has_more": True,
    "content": "前 6000 个字符",
    "read_tool": "read_tool_result"
}
```

模型可以继续请求：

```python
read_tool_result(result_id="...", offset=6000, length=6000)
```

这个工具在本次服务进程的字典中查编号，再切片。它没有 `open(path)` 这样的读取文件分支，不接受路径参数。偏移和长度必须合法；其他会话的编号不在当前字典中，因此无法读取。

页面是原始 JSON 文本的片段，可能从字符串中间开始，不能假设每页都是独立合法 JSON。字符按 Python 字符串计数，非字节数、非 token 数。默认页长 6000，可配置 1–12000。

业务调用原文保存在 trace；新版独立的 `result_index.jsonl` 将 result_id 对应到业务调用序号，不再直接给业务 trace 附加这个字段。读取行为另存 `result_reads.jsonl`。辅助读取不占业务预算，但继续请求模型仍受模型步数、时间和 token 成本约束。

当前长结果在会话内存中保留，未做磁盘缓存或总量上限；大量长结果会增加内存占用。会话结束后编号不可恢复，但磁盘 trace 留有原文。

### 8.3 分页和上下文压缩不同

分页处理的是“某一次工具返回太大”，不让全文立即进入模型上下文。压缩处理的是“很多轮历史累计过长”，由 SDK 对历史作摘要。

分页保留原文，模型可再读；压缩是摘要，可能丢细节。我们未改压缩算法，也没有开放 SDK 摘要里可能出现的任意历史文件恢复路径。不能把“原始记录在日志里”当作“模型始终能访问全部历史”。

### 8.4 留档怎么看

每次运行状态目录旁有 `<目录名>.agent/`：

| 文件 | 用途 |
|---|---|
| `agent.md` | 我们生成的 agent 配置及 prompt |
| `effective_config.json` | SDK 配置与部分适配参数 |
| `llm_requests.jsonl` | 实际发送给模型的请求，查它到底看到了什么 |
| `llm_responses.jsonl` | 原始 HTTP/SSE 响应与错误，按 request_id 对应 |
| `system_prompts.jsonl` | 单独提取实际 system/developer 消息 |
| `events.jsonl` | SDK 执行事件，包括工具和结束事件 |
| `context.json` | SDK 导出的当前上下文，不等于未经压缩的全部历史 |
| `tool_calls.jsonl` | 真实业务工具执行记录，含完整结果 |
| `result_reads.jsonl` | 辅助分页读取记录 |
| `result_index.jsonl` | 有长结果时产生，将分页编号对应到业务调用序号 |
| `result.json` | 最终答案、结束原因、usage、错误 |
| `timing.json` | 耗时、超时标记、进程退出码 |

JSONL 表示每行一个 JSON 对象，适合逐条追加。请求日志不记录认证头，适配器会处理已知 API key 的脱敏；这不是针对工具业务数据或所有秘密的通用脱敏系统，日志应当作为可能含任务数据的内部文件管理。

遇到超时先发取消信号，给 SDK 机会导出上下文；10 秒内无法退出再终止进程组。已经写入的日志仍保留，但硬终止可能丢失尚在内存中、尚未落盘的响应内容；不能保证所有异常下最后一个字节都已记录。

## 9. “不作弊”逐项分析

首先定义本项目关注的作弊：模型获得任务规则之外的信息或修改能力，例如绕过环境工具读取参考答案、直接改状态文件、访问 verifier 结果来投机满足检查。正常调用一个获准的业务工具查看初态，不属于作弊。

| 路径 | 当前防护和证据 | 仍需承认的边界 |
|---|---|---|
| 模型直接调用内置 Read/Shell | agent 和全局工具范围限制；Read 越权测试拒绝读取隐藏内容 | 测试覆盖具体路径，不证明所有 SDK 漏洞不存在 |
| SDK 意外给模型多加工具 | 实际请求数量与前缀检查 | 没有逐名称、逐 schema 固定校验 |
| 通过分页工具读任意文件 | 只接受当前会话结果 ID；路径、跨会话 ID 和非法偏移测试 | 已有工具结果若本身泄漏，分页会保留该泄漏 |
| 直接篡改环境状态 | 模型没有任意文件写工具；业务工具使用既有执行器 | 获准业务工具若设计过宽，仍可能提供越权能力 |
| 环境工具访问宿主文件或网络 | 既有 bubblewrap 使用独立命名空间、有限挂载、清理环境；无 bwrap 拒绝执行 | 沙箱不是本次重新完整审计；依赖目录等也有只读挂载，并非只看见一份状态 |
| 工具修改只读状态 | 新版状态执行器计算逻辑差异并检查声明的可写资产 | 不等于校验全部业务语义及任务授权范围 |
| 从 prompt 看到隐藏内容 | 构造任务输入时不主动加入答案、参考链和 internal.code | 公开元数据及说明没有递归敏感信息过滤，需要检查上游内容 |
| 从其他任务获取信息 | 独立 SDK 进程、临时 home、工具状态、结果编号空间 | 不是操作系统级多租户强隔离 |
| 通过工具返回的提示注入越权 | system prompt 指示把返回当证据；工具白名单限制可执行范围 | 模型仍可能被误导去滥用合法工具或编造回答 |
| 篡改 verifier | solver 未被提供 verifier 工具，prompt 不主动加入 verifier 内容 | Node 进程仍拥有宿主账户权限；工具包和部署必须避免另开访问渠道 |

### 9.1 SDK 进程与工具沙箱要区分

工具执行器会启动 bubblewrap 子进程，挂载候选任务状态和运行依赖，使用独立命名空间、清理环境，并检查状态变化。这个既有机制降低工具代码越过预定范围的风险。

但 Kimi SDK 的 Node 进程、我们的 Python/MCP 主进程并没有被同一个外层沙箱整体包住。当前安全前提包括：这些可信程序按已检查的逻辑运行，依赖没有被恶意修改，注册工具符合约定。

因此，“模型没有读取文件的工具入口”与“任何进程都没有读取文件的系统权限”是两种不同强度的限制。我们实现了前者及既有的工具子进程隔离，没有实现整个运行系统的强权限收缩。

### 9.2 合法工具也可能成为捷径

假设环境自己提供 `read_any_file(path)` 或 `run_python(code)`，并允许访问答案目录。即使模型完全遵守白名单，只调用这些注册工具，也能得到不该获得的信息。MCP 只约定通信，不会替我们判断这个能力是否合理。

所以移交工具包时，必须审查工具能访问的对象和参数范围。schema 规定 path 是字符串，不能证明这个路径只在允许目录内。

### 9.3 不要混淆三个结果

1. 接入成功：SDK 能连接模型和工具并正常运行。
2. 任务成功：结果符合任务要求，由 verifier 等证据判断。
3. 过程合规：模型没有取得或使用规则以外的信息和能力，需要检查权限、信息输入和执行轨迹。

一个任务 verifier 通过，直接支持第 2 项，不自动证明第 3 项。我们需要把这三件事分别报告。

## 10. 已做的验证，能说明什么

### 10.1 自动化回归

分页版本的已记录回归为 45 项通过，28.17 秒。包含原评测/ReAct 测试，以及 Kimi 的工具越权、消息对应、参数验证、重试、超时、空答案、压缩、并发隔离、断流日志和分页测试。

Kimi 集成测试运行真实 SDK、真实 MCP 和工具沙箱；模型 HTTP 响应由本地测试服务控制。因此能稳定测试“模型故意请求 Read”等路径，但不是 45 个真实模型任务成功。

复跑命令（在实际开发工作区执行）：

```bash
KIMI_CODE_SDK=/data1/home/tianfang/kimi-code/packages/node-sdk/dist/index.mjs \
/data1/home/tianfang/agent-world-mini-zhiman/.venv/bin/python -m pytest \
  tests/test_task_eval.py tests/test_task_eval_react.py \
  tests/test_task_eval_kimi.py tests/test_tool_result_reader.py -q
```

### 10.2 真实正式任务：Hugeicons task4

运行目录 `runs/kimi_real_task/20260916_194130_051270`。任务核对 home-01、notification-02 在目录、字体、免费包和矢量中的一致性，并交付清单和指定样式 SVG。

- 框架为 Kimi，模型为 Sol。
- 28 次工具调用，其中 5 次业务失败，后续继续修正。
- Agent 439.77 秒，含准备和 verifier 共 666.77 秒。
- Verifier 的 3 项要求均通过，检查了实际文件及数据。
- 最终回答对部分失败的归因不够准确；当时 MCP 二次校验遮住了业务反馈。

这次正式任务在分页功能实现之前，不能描述成“最新分页版本已经完整重跑正式任务集”。该运行的 REPORT.md 记录的是当时状态，其中“Read/summary 暂缓”等描述属于历史。

### 10.3 最新分页版本：真实模型合成任务

运行目录 `runs/kimi_smoke/20260917_012857_674755`。工具返回约 60 万字符，更新凭据放在尾部；Sol 分页读取凭据，将计数器 157 改成 164，并回读确认。

3 次业务调用、2 次分页读取，21.32 秒，检查通过。它证明真实模型可以使用分页接口完成这个任务，不证明所有长结果任务都能正确处理，也不是正式任务 verifier 的新一轮评测。

复跑：

```bash
/data1/home/tianfang/agent-world-mini-zhiman/.venv/bin/python \
  scripts/run_kimi_smoke.py --long-result --model gpt-5.6-sol
```

这会调用真实模型并产生费用；需要有效管线凭据与可用服务。SDK路径、Python环境等仍是本机配置，交付给其他机器前需调整。

## 11. 给别人使用之前，还需要什么

以下是剩余工作建议，不是已经实现的功能：

1. **先明确信息边界。**逐类确认公开环境元数据可以包含哪些字段，检查实际请求是否含参考答案、实例状态或内部敏感内容。当前顶层筛选不够支持“所有上游环境自动安全”的结论。
2. **继续核对正式工具包的业务边界。**共享 MCP 与 binding 加载器已迁入，任务级初态、分页和预算分层已接通；剩余重点是工具发现能力、契约与科学结果是否一致，不能继续把整项列为未接入。
3. **扩大真实任务与安全回归。**已完成 Hugeicons 新版复跑和多个科学环境试跑，第 18 节给出结果；仍需覆盖更多越权路径及上游信息泄漏风险，分别报告任务通过与过程合规。
4. **固定并验证 SDK 版本。**升级后重测实际工具表、profile 加载、消息和压缩行为。源码接口存在不代表升级后行为完全一致。
5. **按部署风险决定是否增加整进程沙箱。**若用于不可信工具包、多租户或要求更强反作弊保证，不能只依赖同一宿主账户下的进程隔离。

可以进一步加强精确工具名单检查、关键泄漏回归测试和日志审计，但本文没有擅自实现这些更改。

## 12. 你下次看代码的阅读顺序

第一次只跟一次任务走：`task_eval.py::_run_agent` → `task_eval_kimi.py::run_kimi_agent` → `.mjs` 中 `setConfig/addMcpServer/createSession/prompt/getContext` → Python 收取 `result.json`。

第二次看信息与权限：任务 prompt 的 `_public_environment` → 公开工具描述 → agent profile 和全局工具范围 → 实际请求检查 → MCP 注册工具与执行器。

第三次看异常和证据：模型重试 → 工具错误 → 状态提交 → 步数和时间限制 → 日志 → verifier。

第四次看长结果：`tool_result_reader.py` 的字典保存与切片 → MCP 分页信封 → 单独读取日志 → 上下文压缩的区别。

对别人介绍时，最重要的三句话是：

> 官方 Kimi 源码未修改，我们通过 Node SDK 配置并运行它，以 Python 适配器接入原评测管线。
>
> 我们新增了任务输入输出适配、工具范围控制、请求留档、运行限制和受限结果分页；模型与工具的主循环沿用官方实现。
>
> 当前已验证指定工具越权路径被阻止，但公开元数据、工具自身权限和整进程隔离仍决定最终反作弊强度，不能以单任务通过替代安全审查。

## 13. 新一波改动从哪里开始：接入同事的正式交付

这一波主要提交是 `95fe49b`（共享 MCP 与交付包）、`6ea026a`（运行信息贯通）、`30291dc`/`2658b1d`（科学库沙箱）、`c450842`（流式回答恢复）。还包含交付副本准备、审查和实测记录，以及从 main 同步来的少量改动。

之前我们已经能让 Kimi 调用项目自己的工具服务，但工具和环境主要由已有 bundle 提供。新的目标是：让工具生成同事交付的包，能被任务生成与独立评测共同使用，而且描述同一套工具。

新增文件各解决一件具体的事：

| 文件 | 负责什么 | 不负责什么 |
|---|---|---|
| `env_gen/tool_gen/delivery.py` | 将工具、状态及软件映射发布为交付结构 | 不驱动模型完成任务 |
| `env_gen/tool_gen/kimi_mcp.py` | 加载 binding，校验包，提供环境级 MCP 入口 | 不替任务评测决定用哪个任务初态 |
| `env_gen/tool_gen/mcp_protocol.py` | 统一公开工具描述、MCP 返回与 JSON-RPC 收发 | 不实现工具业务，也不判断任务是否完成 |
| `task_gen/task_eval_mcp.py` | 在指定任务状态上执行工具、预算计数及 trace | 不再承担 Kimi 专属分页展示 |
| `task_gen/task_eval_kimi_mcp.py` | 将标准结果包装成适合 Kimi 的展示形式 | 不修改环境工具业务逻辑 |

这些共享文件来自同事的交付并经过必要修正。官方 Kimi 仓库仍无源码修改；“我们接入的代码”不等于这些共享能力都是我们从零编写的。

### 13.1 binding 是什么

binding 是交付包的定位清单，不是任务答案，也不是模型 prompt。它把三类东西关联起来：

```text
binding.json
  ├── tools_path               哪份工具定义与实现
  ├── environment_path         哪份环境描述及默认初态
  └── software_mapping_path    使用哪个 Python 和依赖包
```

为什么不能只给 environment.json？因为科学工具不仅依赖状态数据，还依赖 NumPy、SciPy 等库。工具代码和初态都相同，换一个缺库的 Python 也跑不动。

`load_delivery(binding_path)` 负责解析相对路径、约束路径仍在交付根内、检查相关校验记录与软件映射。其结果同时包含工具包、软件目录和解释器路径。这些是运行基础设施信息，不需要让模型自行寻找 Python。

可用 `llm.kimi.binding_path` 显式配置；正常新管线也会从 Step0 保存的 runtime 信息继续传递它。手动指定的包和任务不匹配时应报错，不能静默换一套工具。

### 13.2 交付包默认初态，不等于某个任务的初态

这是评测公平性的重要区别：

```text
交付包默认初态 → 用于环境级启动/生成的来源
任务保存的初态 → 用于该任务的独立求解
参考执行后的状态 → 用于验收参考，不能作为求解起点
```

`bind_delivery(config)` 检查传入工具是否与交付包一致，环境定义是否匹配，然后补齐 software 信息；它保留调用方传入的 `workspace`，不把它换成交付包默认状态。

简化成 Python 思路：

```python
delivery = load_delivery(binding_path)
check_tools_and_environment_match(config, delivery)
config = {**config, "tools": delivery.tools, "software": delivery.software}
# config["workspace"] 仍然是这一次任务的状态副本
```

因此，直接运行同事的环境 MCP 与运行我们的任务评测 MCP 是两个入口。两者可以共享协议和工具定义，但不能因为名字里都有 MCP，就认为初态和隔离策略也一样。

## 14. 工具协议统一：为什么又拆出一个 Kimi MCP 文件

### 14.1 同一套环境工具，用同一个公开投影

过去任务的 available_tools 和 MCP tools/list 分别组织字段，可能产生细微差异。现在统一调用 `public_tools()`：每项包含名称、说明、inputSchema 和对象根形式的 outputSchema。

usageConditions 中的目标资源、目标对象、前提和副作用被组织进 description。这样普通 MCP 客户端也能读到这些信息，不要求客户端理解我们特有的字段。

Python 直观理解：

```python
task["available_tools"] = public_tools(environment_tools)
mcp_tools_list = public_tools(environment_tools)
```

新任务用统一格式。历史任务仍允许与原环境逐字段一致的旧格式，以便复用；这不是遇到任何差异都接受。

### 14.2 业务错误不应该被包装丢失

比如工具本来明确返回：

```python
{"success": False, "error": {"code": "not_found", "message": "对象不存在"}}
```

旧网关再包成 `{error: ..., tool_result: 原结果}`，MCP 客户端拿原 outputSchema 校验新包装时可能失败。模型最终只看到 schema 错误，反而看不到应当纠正的对象名称。

现在，符合工具失败契约的 `success=false` 对象保留原形，同时用 MCP 的 `isError` 标记失败。真正执行异常或不合约的返回，另组织 runtime_error。业务 trace 仍保留错误语义，失败状态不会因此提交。

这里修正的是错误信息通路，不是把失败当成功，也不是放宽工具执行校验。

### 14.3 标准工具契约与分页包装不混在一起

分页信封 `{result_id, content, ...}` 显然不是环境原来的业务返回。如果把这个规则写进公共工具服务，所有消费者都会被迫理解 Kimi 专属分页。

于是新增一层：

```text
Kimi 客户端
    ↓
KimiResultAdapter（task_eval_kimi_mcp.py）
    ├── read_tool_result：本层读取已有结果
    └── 环境工具：转给 TaskEvalMcpServer
                          ↓
                    校验、执行、计数、留档
                          ↓
                    标准完整 MCP 返回
                          ↓
              KimiResultAdapter 决定是否分页
```

`KimiResultAdapter.handle(request)` 是普通 Python 方法，类似装饰器：先根据方法名分类，再决定自己处理还是转发。

当请求 tools/list，它复制工具列表，把 outputSchema 移进 description，再添加辅助读取工具。这只改变给 Kimi 的展示，标准环境服务仍保留 outputSchema。环境任务 available_tools 不增加辅助读取工具。

当请求普通 tools/call，先让标准服务完成执行，拿到完整结果后再判断长度。长结果编号写入 `result_index.jsonl`，记录对应的业务调用序号。普通业务 trace 保持原格式。

当请求 read_tool_result，本层直接查自己的结果字典，不进入业务执行器，因此不增加业务预算计数。

这个分层让你能分别回答两个问题：“正式环境到底有哪些工具？”看 public_tools；“Kimi 为方便阅读又得到了什么辅助能力？”看 KimiResultAdapter。

## 15. runtime 为什么要贯穿管线：解决用错 Python 的问题

这一波不只改 Kimi 启动文件，还给管线补充了运行信息传递：

```text
Step0 发现 binding.json
    → 加载工具和环境
    → 保存 runtime：binding_path、initial_state、software
    → bundle 保存这些信息
    → 生成执行与独立评测读取 runtime
    → 工具沙箱使用对应 Python 和依赖
```

其中 environment 描述任务世界；runtime 描述程序怎样启动这个世界。把二者分开，避免模型输入和机器运行路径混成一份业务信息。

旧版没有 binding 的环境仍有回退路径。对应改动散布在 contracts、run_io、Step0、执行 agent、任务评测和共享执行器里：它们主要是在传递同一份信息，不是每层都新增一套执行规划。

### 15.1 为什么 Python 文件存在，却说缺依赖

Python 虚拟环境经常是这样：

```text
profile/python/bin/python → 指向机器的基础 Python 可执行文件
profile/python/lib/.../site-packages → 这个环境单独安装的包
```

若提前对解释器路径调用 `.resolve()`，会追踪符号链接，变成基础 Python 的路径。此时启动的身份可能不再是原 venv，已安装依赖也就找不到。

修复不是临时把包安装到全局 Python，而是保留 venv 的 launcher 路径。加载器解析父目录，并保留最后的解释器文件名。旧交付若已经把错误基础路径写进清单，不能靠这段修复自动恢复，需要修正映射或重新发布。

### 15.2 为什么不能把项目所有依赖混进去

之前通用执行器提供项目侧依赖目录。对绑定了独立科学软件 profile 的工具，混入另一 Python 的依赖可能造成版本和二进制不兼容。

新版探测交付解释器的 base、prefix 和依赖位置，在沙箱中挂载所需路径，并显式提供该 profile 的 import 路径；绑定软件时不再混用通用 `/dependencies`。`context.software_root` 指向只读的 `/software`。

探测启动用 `-I -S -B`：隔离 Python 的部分外部设置、不自动加载 site 初始化、不生成字节码缓存。尤其 `-S` 避免宿主探测阶段自动执行 `.pth`/sitecustomize。**但解释器本身仍被当作可信交付基础设施**，这不等于任意恶意软件包都安全。

## 16. 科学库沙箱：让工具能运行，又不借机开放额外能力

这些改动都在共享工具执行器附近，影响生成执行与评测；不属于官方 Kimi 修改。

### 16.1 缓存写进任务状态，导致“修改未声明资产”

科学库可能在导入时写字体、配置和缓存。旧设置 HOME 指向 `/workspace`，这些副作用会落入任务状态，看起来像工具改变了额外文件。

新版把 HOME、XDG_CACHE_HOME、XDG_CONFIG_HOME 指向沙箱 `/tmp` 下的位置。业务状态仍在 `/workspace`；库缓存不混进交付结果。不是忽略状态差异检查，而是把非业务缓存放到正确位置。

### 16.2 机器核很多，导入库就开大量线程

OpenBLAS、OMP、MKL 可能按宿主核数初始化线程池，在单次工具的内存限制下，尚未计算就失败。绑定软件模式设置相关线程数为 1。

它是运行资源控制，不改变模型 prompt，也不意味着任务只允许一次工具调用。实际数值结果仍需业务核验，不能仅因为库能导入就宣称科学结果正确。

### 16.3 离线工具为什么也可能需要证书文件

某些库导入时就构造 TLS 客户端，找不到信任证书会失败，即使后面不访问网络。执行器只读挂载公开 CA 证书并设置 SSL_CERT_FILE。

这不是挂载用户密钥，也不是开放网络。bubblewrap 的网络隔离仍保留；名字里有 cloud 的环境，也不能据名称断言试跑已经访问真实云服务。

### 16.4 Python redirect_stdout 为什么挡不住所有输出

工具执行器需要 stdout 是一份可解析 JSON。Python 的 `print()` 可以被 `contextlib.redirect_stdout` 接住，但 C/Fortran 科学库可能直接向操作系统文件描述符 1 写日志，不经过 Python 的 sys.stdout。

结果可能是：

```text
solver starting ...
{"result": {...}, "error": null}
```

这不是合法的单个 JSON 对象。新版 `quiet_native_stdout()` 临时用 `os.dup2` 把文件描述符 1 指向临时文件，执行完 flush 并恢复，然后由执行器输出干净 JSON。Python 层重定向仍保留。

该临时输出结束时检查 16 MiB 限制；它不是一边写一边立即按 16 MiB 截断，也不是新增完整科学日志存档功能。这里解决的是协议污染。

### 16.5 哪些修复只针对这批交付

`prepare_semiconductor_delivery.py` 为已核对的八个环境准备修正副本，处理交付映射与校验等问题，不覆盖原上游包；`check_delivery_runtime.py` 用于检查解释器与依赖实际能否启动。

准备脚本有明确适用范围，不是让程序对任意坏包猜一个 Python 路径。未来规范交付应直接提供正确映射，不长期依赖这个补救步骤。上游科学能力不匹配也不会通过这个脚本补成“已支持”。

## 17. 真正修过的一个答案丢失 bug：流式失败后续写

这是 `.mjs` 中最值得逐行理解的新增逻辑，提交 `c450842`。

旧代码只取 `context.history[-1]` 的 assistant 文本。通常一个最终回答只占一条消息，这样能工作。但实际发生了：

```text
第 11 次请求：模型已输出回答前 584 字，然后服务端出错
SDK：保留已生成前缀，重试并请求继续回答
第 12 次请求：模型继续输出后 1233 字，正常结束
```

最终上下文里有两条连续的无工具调用 assistant 消息。旧适配器只拿第二条，导致用户和 verifier 看见一份从半句话开始的答案。模型并非没有生成前文，也不是 max_tokens 太小；是我们提取时丢了它。

新代码可用 Python 解释为：

```python
final_messages = []
for message in reversed(context.history):
    if message.role != "assistant" or message.tool_calls:
        break
    final_messages.insert(0, message)

answer = "".join(
    part.text
    for message in final_messages
    for part in message.content
    if part.type == "text"
)
```

从尾部往前收集，遇到工具调用或非 assistant 消息就停；不是把整个任务过程中所有思考、工具前说明都拼进去。只拼文本部分，也没有按相似度删句子或重新让模型润色。

为什么用空字符串连接？流可能在一句话甚至一个词中间断开，强行加换行会改变原文。这里按 SDK 保存顺序恢复原始尾部片段，依赖它们确实属于这次最终回答。若服务端重新完整重答而非续写，当前算法没有通用语义去重能力；不能把这个修复夸成任意流故障都完美恢复。

已有校准 task3 的工具操作没有重做。从原 context 恢复 1817 字完整回答后单独复验，结果变为通过。旧评测结果保留，新复验放在另一个目录，避免覆盖历史证据。

这件事也解释了为什么需要同时保留 HTTP 原始响应、context 和最终 answer：只看最后 answer，会误以为模型本身漏答。

## 18. 新的实测结果：哪些已经完成，哪些仍失败

本节整理现有已落盘实测报告，不表示写这份文档时重新启动了付费运行。完整依据见同目录 `KIMI_EVALUATION.md`、`SEMICONDUCTOR_E2E_RESULTS_20260918.md` 和 `SEMICONDUCTOR_DELIVERY_AUDIT_20260918.md`。

共享 MCP 接入后，真实 Sol 再跑了：

- 长结果小任务：`runs/kimi_smoke/20260917_205924_174501`，380 → 387，3 次业务调用、1 次辅助读取，通过。
- Hugeicons task4：`runs/kimi_mcp_real_task/20260917_205925_545616`，18 次调用，Kimi 耗时 228.42 秒，verifier 3/3 通过；两次业务失败得到完整反馈后恢复。验收仍指出附加 Markdown 不如最终回答完整、部分字体有效性措辞过强，不能省略这些限制。

随后科学环境实测的当前结论：

| 环境 | 正式任务数 | 独立 Kimi 当前证据 | 应怎样理解 |
|---|---:|---|---|
| 校准/去嵌 | 3 | 1 通过、2 失败 | 通过项是修复答案提取后的 task3 复验；不是新生成第四个任务 |
| 本征模 | 1 | 通过 | 求解与核对完成，并披露模型和数据限制 |
| 云仿真 | 1 | 通过 | 完成现有记录审计，不代表真实云计算已开放 |
| 泛型 FEM | 0 | 无正式独立评测任务 | 5 个生成候选被拒绝，不能算成 Kimi 0/5 |

这批配置使用 Sol 作为模型，Kimi 是运行框架，不是 Qwen 的新一轮结果。独立评测配置为最多 60 次业务调用、并发 3，与通用 YAML 默认 50/1 不同。

报告记录相关回归 89 passed、17 skipped，随后显式启用已安装 SDK 的 Kimi 专项 21/21 通过。这些是已有验收记录，不能把 skipped 当通过，也不能把测试数量当真实任务数量。

## 19. 从新结果理解反作弊：有些失败恰好说明边界在起作用

### 19.1 校准任务找不到对象 ID

工具经常要求内部 ID，但没有足够的列表/搜索入口。独立 agent 未能可靠发现对象，因此两个任务失败。

人类或 verifier 可以直接读数据库看见 ID，不意味着受限 solver 应当也能读。不能为了通过率给 solver 增加隐蔽数据库读取；正确解决方向是上游提供业务可用的发现入口或适当的任务信息。

这里把能力缺口归因于缺少发现路径，比简单说“模型太笨”或“应该重试更多次”更准确。

### 19.2 算出数值，不代表任务要求的链条已经成立

泛型 FEM 暴露了瞬态计算能运行、渲染却依赖另一套模型接口的问题。还有图件重跑默认参数、计算并没有使用前一步生成网格等情况。安装更多库或让文件成功生成，不会自动消除这些逻辑不匹配。

同样，两个结果都来自 Meep，就不能换个名称说成 Tidy3D/Meep 的跨求解器对照。Step5 拒绝这样的任务，是质量检查保留要求，不是 adapter 应去绕过的障碍。

### 19.3 新版增强了什么，没有增强什么

增强的是：工具包和任务定义核对、明确的 task 初态选择、依赖环境一致性、原始错误传递、辅助读取与业务工具分离、完整答案恢复。

没有新增的是：整个 SDK 进程的 OS 沙箱、公开环境元数据的递归敏感信息过滤、对所有业务工具的语义安全证明、对所有 verifier 判断的正确性保证。第 9 节的这些边界仍成立。

特别是软件 profile 是只读，不等于内容保密。它应是运行依赖，不应混入参考答案或无关敏感数据；有路径读取能力的环境工具也需要审查其访问范围。

## 20. 下一步读代码，改用这个新顺序

1. 先看 `task_eval_kimi.py` 中 `bind_delivery(config)` 和 MCP 启动脚本路径：理解入口现在连到什么。
2. 看 `task_eval_kimi_mcp.py::KimiResultAdapter.handle`：约 70 行 Python，最容易理解新增分层。
3. 看 `task_eval_mcp.py::bind_delivery`、`TaskEvalMcpServer._call`：包一致性、任务状态和业务错误怎么保留。
4. 看 `mcp_protocol.py::public_tool/public_tools/tool_call_result`：同一工具怎样公开、结果怎样封装。
5. 看 Step0 的 binding 分支和 contracts/runtime：为什么环境位置与运行依赖需要一路传下去。
6. 看共享 `_run_tool` 的 software 分支：venv、挂载、缓存、线程和原生日志处理，先分段读，不必同时理解全部沙箱参数。
7. 最后看 `.mjs` 的 `finalMessages` 循环：它就是第 17 节 Python 伪代码的 JavaScript 版本。

从 main 同步的 review JSON 序列化优化，以及可视化对被拒绝空链任务的容错，也在分支差异里。前者减少序列化冗余，后者让导出脚本能处理空链；它们不是 Kimi 的工具权限机制，不应混在 SDK 接入能力中介绍。
