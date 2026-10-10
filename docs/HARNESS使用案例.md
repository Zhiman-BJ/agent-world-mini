# Harness 使用案例

这份文档用四个可复用的 Case 说明当前代码怎样串起来。示例中的路径、环境 ID 和工具名是占位符；真正运行时只需要替换成对应交付包中的值。文档只覆盖当前稳定入口，不要求接入方了解旧版 task_gen 内部函数。

## 0. 统一约定

所有 Case 都从一个已经通过 ToolGen 验收的 binding.json 开始。一次任务运行的目录建议如下：

~~~text
task-run/
  execution-state/      # MCP/业务工具的可变状态
  model-workspace/      # Kimi cwd；默认为空
  kimi-home/            # Kimi 私有 session、wire、tool-results（概念目录）
  raw/                  # 原始请求、响应、wire、工具调用记录
  evidence/             # verifier 或导出阶段的证据（按需创建）
  trajectory.json       # 稳定轨迹（蒸馏结束后生成）
~~~

四个目录不使用符号链接互相连接。execution-state 和 model-workspace 是两个不同概念：业务工具读写前者，Kimi 原生 Read/Grep/Glob 默认只读后者和当前 session 允许的 tool-results、wire.jsonl。

当前 distill runner 实际把 kimi-home 创建为任务独享的临时目录，任务结束后导出 kimi_session.zip 再删除临时目录；这仍满足 session 隔离，不能把它改成多个任务共享的全局 Kimi home。

文件参数分两层：

| 场景 | 传入的值 | 谁负责解析 |
| --- | --- | --- |
| 业务工具参数 | Scope 内相对路径，例如 daily.json | Harness 根据工具 Schema 的 x-resource-scope 解析 |
| MCP resources/read | MCP Resource URI，例如当前服务返回的 aw://reports/daily.json | MCP Server 的 resources catalog |
| Kimi 原生文件工具 | 相对 model-workspace 的路径，或当前 session 的允许路径 | PreToolUse realpath hook |

业务工具不接收宿主机绝对路径，也不把 Resource URI 当作业务参数。

四个 Case 的输入和交付物如下：

| Case | 主要输入 | 主要输出 |
| --- | --- | --- |
| A. Harness 直调 | binding、工具名、arguments、任务状态 | 调用记录和提交后的任务状态 |
| B. ToolGen | DataGen 环境包、可选 hints/seed | tools、runtime、软件 Profile、binding |
| C. TaskGen | binding、生成策略、可选 fixture | tasks.json、参考 Solution、Ground Truth、最终状态 |
| D. Kimi/MCP | prompt、任务初态、MCP 配置、模型配置 | 最终状态、raw 证据和 trajectory.json |

## Case A：Harness 直接调用一个已发布工具

这个 Case 用于新的直接调用方、Verifier 或离线回归测试。它不启动 Kimi，也不经过 MCP JSON-RPC，但和环境级 MCP、任务评测 MCP 使用完全相同的 harness.execution.call_environment_tool()。

### A.1 输入

~~~text
<delivery-root>/environments/<package-id>/binding.json
<delivery-root>/environments/<package-id>/environment/state/
<delivery-root>/software_profiles/profiles/<profile-id>/   # 如果工具需要专业依赖
~~~

案例不预设任何固定工具名。先从当前交付包读取真实工具名和 Schema：

~~~python
import json
from pathlib import Path

from harness.delivery import load_delivery

delivery = load_delivery(Path("/path/to/binding.json"))
for tool in delivery.package.tools:
    print(tool["name"])
    print(json.dumps(tool["inputSchema"], ensure_ascii=False, indent=2))
~~~

只有真实 Schema 中标有 x-resource-scope 的字段才使用 Scope 内相对路径。例如，某字段声明的 Scope 是 reports，真实文件在该 Scope 根下的 daily.json，那么参数值传 daily.json。不要传 filesystem_scopes/reports/daily.json，也不要传 /data/...。x-resource-scope 和 x-resource-kind 是 Agent-World 的 JSON Schema 扩展注解，不是一个额外工具，也不是 MCP 标准字段。

### A.2 调用代码

在 agent-world-mini 目录中运行：

~~~python
from pathlib import Path

from harness.delivery import load_delivery
from harness.execution import call_environment_tool
from harness.layout import create_task_run_layout


binding = Path("/path/to/delivery/environments/demo/binding.json")
delivery = load_delivery(binding)

# 每条任务只复制一次基线状态，后续调用共享这份任务状态。
paths = create_task_run_layout(
    initial_state=delivery.package.package_root / "state",
    output_dir=Path("/path/to/runs/demo-task"),
)

tools = {item["name"]: item for item in delivery.package.tools}
software = None
if delivery.software_root is not None and delivery.python_path is not None:
    software = {
        "root": str(delivery.software_root),
        "python": str(delivery.python_path),
    }

# 必须替换为 tools 中实际存在的名字和符合其 inputSchema 的参数。
tool_name = "<real-tool-name>"
arguments = {"<real-argument-name>": "<real-value>"}

record = call_environment_tool(
    name=tool_name,
    arguments=arguments,
    tools=tools,
    state_root=paths.execution_state,
    timeout=300,
    memory_limit=2 * 1024 * 1024 * 1024,
    write_limit=256 * 1024 * 1024,
    process_limit=1024,
    environment=delivery.package.environment,
    software=software,
    software_root=delivery.software_root,
)
print(record)
~~~

load_delivery() 会校验 binding、工具 Schema、环境验收回执、ToolGen 工具验收回执和软件 Profile。create_task_run_layout() 会拒绝已有输出目录和状态中的符号链接，并创建 execution-state/、model-workspace/、raw/。

这段直接 Python 调用适用于 host_python 和 python_profile 交付。runtime/runtime.json 声明为 docker 的交付应通过它的 MCP 启动配置进入镜像执行，不能在宿主 Python 中绕过 Docker runtime。

### A.3 执行链和输出

~~~text
arguments Schema 校验
  -> Scope 相对路径校验和物理路径解析
  -> 复制 execution-state 到临时调用副本
  -> bubblewrap/运行后端执行 internal.code
  -> outputSchema 校验
  -> 检查状态差异
  -> 成功才提交临时副本；失败恢复调用前状态
~~~

成功记录的形状大致是：

~~~json
{
  "tool": "<real-tool-name>",
  "arguments": {"<real-argument-name>": "<real-value>"},
  "result": {"success": true},
  "error": null
}
~~~

失败记录仍保留工具名、参数和失败类型。需要区分 timeout、runtime_error、业务返回 success=false、invalid arguments 和 output schema failure。这些错误不能都改写成“环境错误”。

### A.4 这个 Case 适合哪里

- 新的 TaskGen 或其他阶段直接执行 call_tool(name, arguments)；
- Verifier 在独立状态副本上重放最终状态；
- 对单个工具做 Schema、沙盒、回滚和状态变化回归测试；
- 不需要模型时验证软件 Profile 是否真的可执行。

当前 program-form TaskGen Step 2 仍使用 task_gen.program.utils.tool_runtime.CompleteEnvironmentRuntime 执行参考 Solution，而不是直接调用本节 API；它保持同样的 call_tool(name, arguments) 形态、独立状态副本、Schema 校验和失败回滚。新接入代码应使用 Harness 稳定入口，不再复制一套新的执行器。

## Case B：ToolGen 从环境包生成并发布工具

这个 Case 说明工具从哪里来，以及为什么下游只需要拿 binding.json。

### B.1 输入环境

DataGen 环境目录至少包含经过上游校验的环境文件、初始状态和 provenance，例如：

~~~text
<environment-dir>/
  environment.json
  state/
  validation.json
  provenance/
~~~

可选输入是工具线索数组，或上游 seed 中的一条工具线索。下面是格式模板，不代表仓库中存在同名工具：

~~~json
[
  {
    "name": "<candidate-tool-name>",
    "description": "<来自真实来源的候选能力说明>",
    "source_urls": ["https://example.org/..."]
  }
]
~~~

线索只影响研究和规划，不等于最终工具。ToolGen 仍需根据真实环境生成代码、运行样例并通过验证。

### B.2 运行命令

~~~bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini

python3 -m env_gen.tool_gen /path/to/environment-dir \
  --tool-hints /path/to/hints.json \
  --output-root /path/to/agentworld-toolgen-results
~~~

使用 seed 时：

~~~bash
python3 -m env_gen.tool_gen /path/to/environment-dir \
  --seed-path /path/to/seed.json \
  --seed-id <global_id-or-theme_id> \
  --output-root /path/to/agentworld-toolgen-results
~~~

### B.3 ToolGen 内部阶段

~~~text
读取 DataGen environment
  -> 盘点真实数据、文件 Scope、软件需求和参考工具
  -> 生成 action_plan 和工具草稿
  -> 准备/复用软件 Profile 或 Docker runtime
  -> 在临时状态副本中执行草稿
  -> 修复参数、依赖或实现问题
  -> 生成 tools.json、tool_validation.json、tool_grounding.json
  -> publish() 生成独立交付包和 binding.json
~~~

工具的 internal.code 只用于运行时和生成阶段校验，不会放进给 Kimi 的公开工具描述。公开给 Agent 的是工具名、description、usageConditions、inputSchema 和 outputSchema。

### B.4 输出交付包

~~~text
<delivery-root>/
  environments/<package-id>/
    binding.json
    tools/tools.json
    tools/tool_validation.json
    tools/tool_grounding.json
    environment/environment.json
    environment/validation.json
    environment/state/
    environment/provenance/
    software/profile.json
    runtime/runtime.json
  software_profiles/profiles/<profile-id>/
~~~

下游只加载：

~~~python
from pathlib import Path

from harness.delivery import load_delivery

delivery = load_delivery(Path("/path/to/binding.json"))
~~~

软件 Profile 是共享的只读运行依赖，相同软件计划的环境可以复用；environment/state/ 仍然必须为每条任务独立复制，不能因为复用了软件包而共享任务状态。

### B.5 运行地址如何保持不歧义

ToolGen 生成的工具代码不保存宿主绝对路径。工具运行时收到的是当前调用副本的状态根，Scope 由 Harness 解析：

~~~text
模型参数：daily.json
工具代码：Scope 内的 daily.json
物理路径：<task-run>/execution-state/filesystem_scopes/reports/daily.json
~~~

如果工具在 reports Scope 中输出文件，也返回 result.json 或 exports/result.json 这样的 Scope 内相对路径，不在前面增加 reports/。MCP Resource URI 只在 resources/list/read 层使用，不进入业务工具 Schema。

## Case C：TaskGen 从 binding 生成任务和参考解

TaskGen 程序形式管线是：

~~~text
Step 0：加载并冻结 ToolGen 交付包
Step 1：调研现实工作并判断环境支持范围
Step 2：生成任务、参考 Solution、Ground Truth 并重放验证
~~~

### C.1 Step 0：冻结输入

只跑 Step 0：

~~~bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini
python -m task_gen.program.step_0_environment_load \
  --binding /path/to/delivery/environments/demo/binding.json \
  --output-dir /path/to/taskgen-run
~~~

也可以按完整程序管线运行：

~~~bash
python -m task_gen.program.run_pipeline \
  --binding /path/to/delivery/environments/demo/binding.json \
  --output-dir /path/to/taskgen-run \
  --step all
~~~

Step 0 会把交付包中的公开环境、工具、初始状态、软件映射和 runtime 固化到：

~~~text
taskgen-run/
  baseline_environment/
    environment.json
    validation.json
    tools.json
    binding.json
    delivery.json
    state/
  step0_environment.json
~~~

这一步是“冻结输入”，不是给模型暴露宿主目录。后续生成器使用冻结副本，任务执行再为每个任务建立自己的状态副本。

### C.2 Step 1：生成现实任务原型

Step 1 的 Agent 读取工作目录中程序生成的：

~~~text
environment.public.json
initial_state.summary.json
task_research.schema.json
references/环境契约-v2.0.md
references/工具契约-v1.0.md
~~~

它输出 step1_task_research.json，描述现实工作类型、来源和 environment_support。这里的工具列表是能力映射，不是把工具调用顺序硬编码进任务。

已有 research fixture 时可以离线运行：

~~~bash
python -m task_gen.program.run_pipeline \
  --binding /path/to/binding.json \
  --output-dir /path/to/taskgen-run \
  --step 0-1 \
  --research-fixture /path/to/research_fixture.json
~~~

### C.3 Step 2：生成并验证任务

~~~bash
python -m task_gen.program.run_pipeline \
  --binding /path/to/binding.json \
  --output-dir /path/to/taskgen-run \
  --step 2 \
  --task-count 1 \
  --clean-replays 2
~~~

Step 2 的 Agent 可以读取工具契约，并在临时 probe 状态上了解真实行为；internal.code 仅供任务生成阶段的隔离分析，不能写入公开任务。它提交的参考程序形态如下；尖括号内容必须替换为当前交付包中的真实工具名、参数名和返回字段：

~~~python
first = call_tool("<real-query-tool>", {"<real-argument>": "<real-value>"})
selected = [item for item in first["<real-list-field>"] if item["<real-field>"] == "<value>"]
second = call_tool("<real-follow-up-tool>", {"<real-argument>": selected})
final_answer = {"<answer-field>": second["<real-result-field>"]}
~~~

程序执行器会在全新状态上运行这段 Solution，校验：

1. call_tool 的参数和返回值 Schema；
2. 后一次调用确实依赖前一次结果；
3. final_answer 确实由工具结果推导；
4. 业务失败、超时和状态变化是否符合要求；
5. 多次干净重放结果是否一致。

通过后主要产物是：

~~~text
taskgen-run/
  step2_task_solution.jsonl   # 参考 Solution、执行轨迹和状态摘要
  tasks.json                  # 下游 task_eval 消费的公开任务
  step2_validation.json
  rejected.json
  tasks/<task-id>/final/      # 任务参考最终状态（按当前实现生成）
~~~

公开任务中可以出现业务字段、Scope ID 和 Scope 内相对路径；不能出现 internal.code、宿主路径、verifier 文件或隐藏答案。

## Case D：Kimi 通过 MCP 执行一条任务

这个 Case 是正式 Agent/蒸馏链路。Kimi Code 作为 MCP 客户端，业务工具由 stdio MCP Server 暴露，Server 内部继续调用 Harness。

### D.1 先确认 MCP 工具表

可以直接查看 Kimi 配置：

~~~bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini
python -m env_gen.tool_gen.kimi_mcp \
  /path/to/binding.json \
  --trace /path/to/run/raw/environment_tool_calls.jsonl \
  --session-root /path/to/run/mcp-session \
  --server-name agent_world \
  --print-kimi-config
~~~

这里的 --print-kimi-config 输出的是 Kimi MCP 配置，不会执行任务。正式启动时去掉它，Server 会通过 stdio 接收 MCP JSON-RPC。

独立环境级 MCP 要求 mcp-session 在启动前不存在；Server 创建它，并把实际状态放在 mcp-session/state/。不要把这里的 --session-root 指向已经存在的 Harness execution-state/。TaskGen/Kimi 蒸馏使用另一条任务级 MCP 入口，由 distill runner 直接把已有 execution-state/ 绑定为 state_root。

MCP 层次是：

~~~text
initialize       -> 协议版本、capabilities、instructions
tools/list       -> 业务工具的公开 Schema
tools/call       -> 调用一个业务工具
resources/list   -> 列出当前状态中的资源 URI
resources/read   -> 通过 URI 读取一个资源
~~~

resources/list/read 是 MCP 协议方法，不会变成额外的业务 Function Tool。当前 Resource URI 由 Server 生成，客户端只能原样交给 resources/read；业务工具仍然接收 Scope 相对路径。

### D.2 任务级运行目录

如果直接调用环境级 MCP Server，--session-root 应指向该任务尚不存在的 MCP 会话目录；Server 会在其下创建 state/。正式 Kimi 蒸馏推荐让 distill.runner 创建目录，它会自动把初始状态复制到 execution-state/，并为每条任务创建独立 Kimi home、MCP 进程和证据目录。

对 TaskGen 生成的任务，优先使用批量入口；它会从 tasks.json 和 intermediate/step_5_bundle.json 读取 prompt、环境、任务初态、工具和 runtime：

~~~bash
export KIMI_CODE_BIN=/absolute/path/to/kimi
export KIMI_BASE_URL=https://gateway.example/v1
export KIMI_API_KEY='<secret>'
export KIMI_MAX_CONTEXT_SIZE=1048576

python -m distill taskgen \
  --input-root /path/to/taskgen-runs \
  --output-root /path/to/distill-runs \
  --task-id <task-id> \
  --provider-type kimi \
  --reasoning-effort high \
  --max-concurrency 1
~~~

不要把真实 key 写进脚本或提交到仓库；上面的值应由作业环境安全注入。

使用蒸馏 runner 的 Python 调用形态：

~~~python
from pathlib import Path

from distill.runner import run_k3_distillation
from harness.delivery import load_delivery


delivery = load_delivery(Path("/path/to/binding.json"))
tools = list(delivery.package.tools)
environment = delivery.package.environment

trajectory = run_k3_distillation(
    prompt="请完成任务正文中描述的业务工作，并给出最终结论。",
    initial_state=Path("/path/to/taskgen-run/tasks/<task-id>/initial"),
    server_config={
        "tools": tools,
        "environment": environment,
        "max_tool_calls": 20,
        "timeout": 300,
        "memory_limit": 2 * 1024 * 1024 * 1024,
        "write_limit": 256 * 1024 * 1024,
        "process_limit": 1024,
        # 如果来自 ToolGen 交付包，也可带 binding_path，让运行时从交付契约加载 Profile。
        "binding_path": str(delivery.binding_path),
    },
    output_dir=Path("/path/to/distill-run"),
    kimi_bin="/absolute/path/to/kimi",
    base_url="https://gateway.example/v1",
    api_key="<通过环境变量或安全注入提供>",
    max_context_size=1_048_576,
    reasoning_effort="high",
)
~~~

实际项目中不要把真实 API key 写入源码、Kimi 配置、session、轨迹或 stderr。distill.runner 会把 Kimi 的 cwd 设为 model-workspace/，MCP 的 state_root 设为任务的 execution-state/。

### D.3 模型能看到什么

当前 Harness 的 Agent profile 只公开：

~~~text
Read
Grep
Glob
mcp__agent_world_distill__<业务工具名>
~~~

继续禁用 Write、Edit、Bash、Agent、AgentSwarm、网络工具和 select_tools。MCP 工具名上的 mcp__...__ 是客户端命名空间，不改变交付包中的业务工具名。

Kimi 原生文件调用会先经过 PreToolUse realpath hook：允许 model-workspace/**、当前 session 的 tool-results/** 和 wire.jsonl，拒绝 execution-state/**、raw/**、其他任务/session 以及符号链接逃逸。允许和拒绝都会写入 raw/native_file_access.jsonl。

### D.4 长结果和证据

环境 MCP 返回完整工具结果。超过 Kimi 阈值的长结果由 Kimi 自动保存到当前 session 的 tool-results，模型再用原生 Read/Grep 找回；Harness 不再注册自定义 read_tool_result。

一条成功蒸馏任务通常保留：

~~~text
distill-run/
  trajectory.json
  execution-state/
  model-workspace/
  raw/
    wire.jsonl
    session_state.json
    model_requests.jsonl
    model_responses.jsonl
    model_io/*.request.json
    model_io/*.response.sse
    environment_tool_calls.jsonl
    native_file_access.jsonl
    kimi_session.zip
    harness_policy.json
    run_result.json
~~~

trajectory.json 是导出视图；raw/ 才是调试和复核的原始证据。Verifier 应读取任务最终状态和轨迹，不应读取模型私有 session 来判断答案。

## 5. 四个 Case 的数据流总览

~~~text
DataGen environment/
  ├─ environment.json + state/ + validation.json
  │
  └─ ToolGen
       ├─ tools.json + tool_validation.json
       ├─ software Profile / runtime
       └─ binding.json
              │
              ├─ Harness 直接调用
              │    └─ call_environment_tool -> execution-state
              │
              ├─ TaskGen Step 0/1/2
              │    └─ tasks.json + reference Solution + final state
              │
              └─ Kimi/MCP
                   ├─ tools/list / tools/call
                   ├─ resources/list / resources/read
                   ├─ execution-state（MCP）
                   ├─ model-workspace（Kimi cwd）
                   └─ raw + trajectory（证据）
~~~

## 6. 接入前最小检查

1. load_delivery(binding.json) 能成功，且工具和环境验收回执都是 passed/valid。
2. 用 create_task_run_layout() 为一条任务创建全新目录，不复用其他任务的 state 或 Kimi home。
3. 用一个真实工具跑通一次成功调用和一次失败/回滚调用。
4. 检查业务工具参数和返回值是否都是 Scope 内相对路径，没有宿主绝对路径或 Resource URI。
5. MCP 运行时确认 tools/list 只有业务工具，resources/list/read 只作为协议方法存在。
6. Kimi 运行后检查 native_file_access.jsonl、environment_tool_calls.jsonl 和 wire.jsonl 是否都有记录。
7. 长结果、compaction 和恢复测试单独验收；不要只用“轨迹文件存在”代替真实恢复测试。
