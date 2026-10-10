# Harness 使用说明

本文是当前 Agent-World Harness 的接入手册。它描述已经生效的接口和边界，不要求各阶段了解旧版 TaskGen 内部实现。

## 1. 先记住四件事

1. `execution-state` 是 MCP 和工具执行状态，不能作为 Agent 的工作目录。
2. `model-workspace` 是 Kimi 的 `cwd`，默认为空，只放模型明确产生的模型可读文件。
3. `kimi-home` 保存 Kimi 配置、session、wire 和 `tool-results`，每条任务独立创建。
4. `raw`/`evidence` 保存原始请求、响应、轨迹和 verifier 证据，不能放进 `model-workspace`。

推荐的任务目录如下：

```text
task-run/
  execution-state/      # 当前任务的环境状态副本
  model-workspace/      # Kimi cwd
  kimi-home/            # Kimi 私有 session 数据（通常是临时目录）
  raw/                  # 原始请求、响应、wire、工具调用日志
  evidence/             # verifier 和导出证据（按流程需要创建）
  trajectory.json       # 导出的稳定轨迹
```

不要使用符号链接连接这些目录，也不要让任务之间共享状态、session 或 `tool-results`。

蒸馏 runner 和通用 ToolGen MCP 都从同一个 `binding.json` 读取 `runtime.json`：
`host_python`/`python_profile` 使用本地解释器，`docker` 使用交付包声明的镜像、挂载和命令。任务级 MCP 仍保留独立的 `execution-state`、调用轨迹和资源限制；这只统一进程启动后端，不改变 Kimi Code 的 agent loop。

## 2. 稳定入口

| 用途 | 入口 |
| --- | --- |
| 读取 ToolGen 交付包 | `harness.delivery.load_delivery` |
| 创建任务目录 | `harness.layout.create_task_run_layout` |
| 执行一个环境工具 | `harness.execution.call_environment_tool` |
| 工具沙盒 | `harness.sandbox` |
| 资源引用和 Resources | `harness.resources.ResourceCatalog` |
| Kimi 文件访问边界 | `harness.kimi_file_policy.KimiFileAccessPolicy` |
| MCP 工具 allowlist | `harness.mcp_tools.expected_mcp_tool_names` |

新的调用方只使用这些 Harness 入口，不要直接导入 `step_3_chain_execute` 的私有函数，也不要自己拼接交付包内部路径。

## 3. ToolGen 交付包

下游只接收 `binding.json`：

```text
delivery-root/
  software_profiles/profiles/<profile-id>/
  environments/<package-id>/
    binding.json
    tools/tools.json
    environment/environment.json
    environment/state/
    software/profile.json
    runtime/runtime.json
```

加载方式：

```python
from harness.delivery import load_delivery

delivery = load_delivery("/path/to/binding.json")
package = delivery.package

environment = package.environment
tools = package.tools
initial_state = package.package_root / "state"
software_root = delivery.software_root
python_path = delivery.python_path
runtime = delivery.runtime
```

`load_delivery()` 负责校验 binding、工具、环境、软件 Profile 和 runtime。部署路径是运行时私有信息，不能放入模型 prompt、任务输入或业务工具返回值。

软件 Profile 是共享只读依赖；任务状态必须从 `initial_state` 为每条任务建立独立副本。

## 4. 创建任务运行目录

```python
from harness.layout import create_task_run_layout

paths = create_task_run_layout(
    initial_state=initial_state,
    output_dir="/path/to/task-run",
)

paths.execution_state
paths.model_workspace
paths.raw
paths.trajectory
```

这个函数会：

- 复制初始状态到 `execution-state`；
- 创建空的 `model-workspace` 和 `raw`；
- 拒绝不存在的状态目录；
- 拒绝初始状态中的符号链接；
- 拒绝已经存在的输出目录。

## 5. 执行环境业务工具

所有 TaskGen、MCP 和直接调用方都使用同一个入口：

```python
from harness.execution import call_environment_tool

tool_map = {tool["name"]: tool for tool in tools}
record = call_environment_tool(
    name="query_records",
    arguments={"path": "daily.json"},
    tools=tool_map,
    state_root=paths.execution_state,
    timeout=300,
    memory_limit=2 * 1024 * 1024 * 1024,
    write_limit=256 * 1024 * 1024,
    process_limit=1024,
    environment=environment,
    software=(
        {"root": str(delivery.software_root), "python": str(delivery.python_path)}
        if delivery.software_root else None
    ),
    software_root=delivery.software_root,
)
```

调用边界固定执行：

```text
参数 Schema 校验
  -> Scope/资源参数校验
  -> 状态临时副本
  -> bubblewrap 沙盒执行
  -> 返回 Schema 校验
  -> 检查状态差异和只读资源
  -> 成功提交；失败恢复调用前状态
```

失败分类要保留差异：

- `timeout`：工具超时，通常可重试；
- `runtime_error`：解释器、执行器或沙盒故障；
- `business failure`：工具正常返回 `success=false`；
- `invalid arguments`：参数或资源引用不符合契约；
- `output schema failure`：工具返回值不符合契约。

不要把这些全部改成“环境错误”。

## 6. 文件参数和资源 URI

业务工具的文件参数使用 Scope 内相对路径：

```text
模型调用工具：daily.json
工具代码：    daily.json
实际物理路径：<task-run>/execution-state/filesystem_scopes/reports/daily.json
```

文件字段由 ToolGen 标注 Scope：

```json
{
  "type": "string",
  "x-resource-scope": "reports",
  "x-resource-kind": "file"
}
```

业务工具参数不传宿主机绝对路径、`filesystem_scopes/...` 或 Resource URI。

MCP Resources 使用标准方法：

```text
resources/list  -> 列出资源 URI、scope_id、relative_path
resources/read  -> 使用 URI 读取资源
```

当前服务端生成的 URI scheme 是 `aw://`，它是 opaque 标识，只能交给 `resources/read`，不能由模型或业务工具自行拼接、解析或当作普通文件路径。

`resources/list` 和 `resources/read` 是 MCP JSON-RPC 方法，不会出现在 `tools/list`，也不能通过 `tools/call` 调用。

## 7. Kimi / MCP 接入

每条任务的运行关系是：

```text
Kimi cwd                  -> model-workspace/
MCP server state_root     -> execution-state/
Kimi session/tool-results -> kimi-home/sessions/.../
原始证据                  -> raw/
```

模型公开工具只有：

```text
Read
Grep
Glob
mcp__<server_name>__<business_tool>
```

`mcp__<server_name>__...` 是 Kimi 客户端给 MCP 工具加的命名空间，不是业务工具自身的名字。原始 wire 和请求日志保留这个名字；导出训练轨迹可以再做结构化规范化。

### 7.1 原生 Read/Grep/Glob

- `Read`：读取已知文本文件，或分页读取 Kimi 保存的长工具结果；
- `Grep`：搜索工作区、当前 session 的 `tool-results` 或 `wire.jsonl`；
- `Glob`：按模式寻找文件。

这三个工具不是环境 Scope 浏览器。Scope 内业务文件必须通过环境 MCP 工具或 MCP Resources 访问；原生文件工具只能读取当前模型工作区、当前 session 的长工具结果和当前 session 的 wire 日志。

这三个工具不属于环境 MCP，不会出现在 MCP `tools/list`。

它们通过 Kimi `PreToolUse` hook 接入 `harness.kimi_file_policy`，使用 realpath 白名单：

允许：

```text
model-workspace/**
当前 session 的 agents/main/tool-results/**
当前 session 的 agents/main/wire.jsonl
当前 session 的 kimi-file:// attachment
```

拒绝：

```text
execution-state/**
raw/**、evidence/**、verifier/**
其他任务或其他 session
~/...
符号链接逃逸和 .. 越界
```

每次允许或拒绝都记录到 `native_file_access.jsonl`。

### 7.2 长工具结果和上下文压缩

MCP 业务工具返回完整结果。Kimi 对超过约 50,000 字符的文本自动保存到当前 session 的 `tool-results`，模型使用 `Read` 或 `Grep` 找回内容。

上下文接近窗口上限时，Kimi 自动 compaction，生成 summary，并提供当前 Agent 的 `wire.jsonl` 路径和历史行范围。模型使用 `Grep` 定位记录，再使用 `Read` 分页恢复旧信息。

因此当前 Harness 不再注册自定义 `read_tool_result`，也不在 MCP 层提前分页。

## 8. Verifier 和证据

Verifier 使用任务最终状态的独立副本运行：

- 不读取模型私有 session 来判断答案；
- 不把 verifier 文件写入 `model-workspace`；
- 输出关联 `task_id`、trajectory 和具体 tool call；
- 区分模型错误、工具错误、依赖缺失和契约错误。

建议保留：

```text
trajectory.json
raw/wire.jsonl
raw/model_requests.jsonl
raw/model_responses.jsonl
raw/model_io/
raw/environment_tool_calls.jsonl
raw/native_file_access.jsonl
raw/session_state.json
raw/kimi_session.zip
raw/harness_policy.json
```

## 9. 当前变更边界

本次 Harness 适配只改变了以下与用户明确要求直接相关的内容：

- 把统一工具执行、目录布局、资源解析和文件访问策略收口到 `harness/`；
- 让 MCP 业务工具使用 Scope 相对路径；
- 移除旧的自定义长结果读取协议，改用 Kimi 原生 `tool-results`；
- 开放 Kimi 原生 `Read/Grep/Glob`，并增加真实生效的路径 hook；
- 将 MCP `resources/list/read` 保留为协议方法，不伪装成普通 function tool；
- 让 TaskGen、MCP 和蒸馏调用方使用上述统一入口。

以下能力没有被重新设计：

- ToolGen 业务工具生成方式；
- bubblewrap 工具执行后端；
- 软件 Profile/依赖准备方式；
- 工具输入输出 Schema 契约；
- Kimi 的模型循环、流式请求和 session 持久化机制；
- Verifier 的业务判定逻辑。

## 10. 验收命令

使用仓库实际 Python 环境运行：

```bash
python -m pytest -q
```

当前重点验收包括：

- `execution-state` 与 `model-workspace` 分离；
- Read 越权访问被拒绝并记录；
- 长 MCP 结果由原生 Read 恢复；
- MCP 资源方法和业务工具目录不混淆；
- 工具失败不提交状态；
- 所有现有测试保持通过。

当前仍需单独补充的生产验收是将“长结果 + compaction + wire 恢复”合并成一条真实任务；这不是 Harness 基础接口缺失，而是端到端数据验收项。
