# MCP 与工具调用代码地图

本文只描述当前代码。做正式测评、定位工具问题或增加新调用方时，先按这张地图找入口，不要从历史 TaskGen 私有函数反推主链。

## 1. 正式测评时模型能调用什么

Kimi Code 的模型请求中只有两类工具：

```text
Read
Grep
Glob
mcp__agent_world_distill__<business_tool>
```

- `Read/Grep/Glob` 是 Kimi 原生文件工具。
- `<business_tool>` 来自当前 ToolGen 交付包的 `tools.json`。
- `mcp__agent_world_distill__` 是 Kimi MCP Client 添加的命名空间，不是业务工具名，也不写入 ToolGen Schema。
- `initialize`、`tools/list`、`tools/call`、`resources/list`、`resources/read` 是 MCP JSON-RPC 方法，不是额外的模型 function tools。
- 当前没有自定义 `read_tool_result`；长结果由 Kimi 自己写入 session 的 `tool-results/`。

## 2. 正式测评主链

```text
distill.runner.run_k3_distillation
  -> harness.layout.create_task_run_layout
  -> 启动官方 Kimi Code CLI
  -> task_gen.task_eval_kimi_mcp（stdio 入口和 Kimi Schema 适配）
  -> task_gen.task_eval_mcp.TaskEvalMcpServer（任务级 MCP）
  -> harness.execution.call_environment_tool（唯一业务执行事务）
  -> harness.sandbox / Docker delivery runtime
  -> ToolGen internal.code
```

模型发起原生文件调用时走另一条链：

```text
Kimi Read/Grep/Glob
  -> PreToolUse hook
  -> harness.kimi_file_policy
  -> realpath 白名单与共享工具预算
  -> Kimi 原生工具执行
```

## 3. 一条任务的实际目录

正式 `distill.runner` 调用 `create_task_run_layout(..., environment=...)` 后，文件 Scope 会被复制到 Kimi 的真实工作目录：

```text
<task-run>/
  <environment-id>_<task-id>/       # model_workspace，也是 Kimi cwd
    <scope files>                    # 单 Scope
    <scope-name>/<scope files>       # 多 Scope
  private-state/
    records.sqlite                   # 模型不可见的 Record Set 状态
    path-map.json                    # scope_id -> workspace mount
  raw/                               # 模型请求、wire、工具 trace、审计
  trajectory.json
```

Kimi home 是每条任务独享的临时目录。任务结束时，其 session 被导出到 `raw/kimi_session.zip`；长工具结果运行期间位于：

```text
<kimi-home>/sessions/<workDirKey>/<sessionId>/agents/main/tool-results/
```

同一 Agent 的完整 wire 位于相邻的 `wire.jsonl`。

`TaskRunLayout.execution_state` 在新布局中只是指向 `private-state` 的兼容属性，不是文件 Scope 的物理根。新代码应使用：

- `model_workspace`：Kimi cwd 和文件 Scope；
- `record_database`：私有数据库；
- `scope_mounts`：Scope 到工作目录的映射；
- `raw`：证据。

## 4. 路径契约

模型看到并传给业务工具的是工作目录路径：

- 单 Scope：`mobility_models/source/mobility.py`
- 多 Scope：`reports/daily.json`、`designs/main.kicad_sch`
- 也接受位于当前 `model_workspace` 内的绝对路径。

ToolGen Schema 上的 `x-resource-scope` 和 `x-resource-kind` 告诉 Harness 该字段属于哪个 Scope、应是文件还是目录。`ResourceCatalog.normalize_arguments()` 会把模型路径转换为工具代码原有的 Scope 内相对路径；`externalize_result()` 再把工具返回路径转换为模型可见的 workspace 路径。

`aw://scope/path` 只属于 MCP Resources 标识空间：

```text
resources/list -> 返回 aw://... URI 和 workspace-relative relative_path
resources/read -> 接收上一步 URI 并读取对应资源
```

业务工具参数和返回值不得包含 `aw://`。正式 Kimi 工具表也不会把 `resources/list/read` 注册成 function tool，因此 URI 不进入正常任务调用链。

## 5. 代码归属

| 层 | 当前唯一职责 | 代码 |
| --- | --- | --- |
| 任务布局 | 建立 workspace、私有 DB、路径映射和 raw | `harness/layout.py` |
| 资源路径 | Scope、workspace 路径与 Resource URI 转换 | `harness/resources.py` |
| MCP wire | JSON-RPC、公开 Tool Schema、调用结果格式 | `harness/mcp_protocol.py` |
| MCP Resources | 两套 Server 共用的 list/read 投影与 50 MiB 限制 | `harness/mcp_resources.py` |
| 业务事务 | 参数/结果 Schema、状态副本、提交和回滚 | `harness/execution.py` |
| 沙盒后端 | bubblewrap/直接进程、资源限制、Context | `harness/sandbox.py` |
| 文件权限 | Read/Grep/Glob realpath 白名单 | `harness/kimi_file_policy.py` |
| 共享预算 | MCP 与原生文件工具的原子计数 | `harness/tool_budget.py` |
| 测评 MCP | 任务状态、trace、review choice、预算 | `task_gen/task_eval_mcp.py` |
| Kimi MCP 适配 | stdio 启动参数和 Kimi Schema 兼容 | `task_gen/task_eval_kimi_mcp.py` |
| 正式测评编排 | Kimi CLI、relay、证据与 trajectory | `distill/runner.py` |

## 6. 为什么仍有两套 MCP Server

它们面向不同生命周期，不是两套业务执行器：

- `env_gen/tool_gen/mcp_server.py::ToolMcpServer`：运行一个独立 ToolGen 交付包，自建 session 状态并在关闭时写 receipt。
- `task_gen/task_eval_mcp.py::TaskEvalMcpServer`：运行一条已有任务状态，接收 task workspace、私有数据库、工具预算、trace 和可选 review choice。

两者共用 `harness.mcp_protocol`、`harness.mcp_resources` 和 `harness.execution.call_environment_tool`。不应把二者生命周期强行合成一个大类。

## 7. 其他调用方

- `task_gen/task_eval_react.py`：ReAct 评测，直接调用 `call_environment_tool`。
- `task_gen/program/utils/tool_runtime.py`：Program-form TaskGen 的参考解/重放包装器，业务调用最终委托 `call_environment_tool`。
- `env_gen/tool_gen/runtime.py`：ToolGen 生成和交付验收的 Runtime，保留自己的 package/session 生命周期。
- `task_gen/tool_graph/step_3_chain_execute.py`：历史 fixed-chain replay；不是正式 Kimi 测评入口，不应被新代码依赖。
- `task_gen/tool_graph/state_runtime.py`：沙盒内 Context 和状态快照实现，不是 MCP Server。

## 8. 增加新调用方的规则

1. 从 `binding.json` 用 `harness.delivery.load_delivery()` 加载工具与 runtime。
2. 用 `create_task_run_layout(..., environment=environment)` 创建任务目录。
3. 直接执行业务工具时只调用 `harness.execution.call_environment_tool()`。
4. 需要 MCP 时复用 `TaskEvalMcpServer`；不要复制 JSON-RPC 分发和 Resource 读取代码。
5. 不直接调用 `harness.sandbox.call_tool()`，除非正在实现 Harness 本身。
6. 不把宿主路径、`private-state`、`raw`、verifier 文件或 Resource URI写进任务 prompt。
7. 新测试至少覆盖 `tools/list`、成功调用、失败回滚、路径转换和越权文件读取。

## 9. 测评前检查

```bash
python -m pytest -q \
  tests/test_tool_resources.py \
  tests/test_resource_paging.py \
  tests/test_mcp_integration.py \
  tests/test_harness.py \
  tests/test_kimi_mcp.py \
  tests/test_task_eval.py \
  tests/test_task_eval_react.py \
  tests/test_program_form_v2.py
```

正式批量前再用一条真实任务验证：MCP 调用、长结果 externalization、compaction、`Read/Grep` 恢复、最终答案、verifier 和证据导出。
