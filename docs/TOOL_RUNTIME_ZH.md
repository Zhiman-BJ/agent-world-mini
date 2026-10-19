# 工具运行时与隔离

## 1. 架构：先区分两个执行对象

### 1.1 业务工具执行

```text
Task MCP / ToolGen 通用 MCP / Program Runtime
  → harness.execution.call_environment_tool
  → 参数、资源边界与状态事务
  → 选定的工具进程后端
  → 返回校验 → 成功提交或失败恢复
```

这里执行的是交付工具中的业务代码。后端同时取决于 binding 中的 runtime 和调用方传入的状态布局，
不能仅凭 `host_python` 或 `python_profile` 名称断言使用哪种隔离。

### 1.2 Verifier 调查

```text
求解结束 → 准备验收证据 → bubblewrap → Codex Verifier Agent → verdict
```

这里执行的是能够读取文件、运行调查命令的审核 Agent，不是前面那个业务工具进程。
两个沙箱处于不同阶段，不是一次工具调用套两层 bubblewrap。

## 2. 业务工具：通用沙箱究竟在哪里用

### 2.1 Harness 默认分支

`harness/execution.py` 的 `call_environment_tool` 先判断是否为任务工作区布局：
传入非 None 的 `record_database` 或 `scope_mounts`，即 `workspace_mode=True`。
没有显式提供 `call_tool_fn` 时，分支如下：

| 调用条件 | 执行路径 | 使用 bubblewrap |
| --- | --- | --- |
| 任务工作区 + 非 Docker | `run_tool_direct`，使用当前或交付 Profile 解释器 | 否 |
| 任务工作区 + Docker | `run_tool_in_docker`，使用交付镜像/会话 | 此层不加 |
| 非任务工作区 | `harness.sandbox.call_tool`，再按下表选择 | 取决于 runtime |

非任务工作区的 `harness.sandbox.call_tool` 分支：

| 调用条件 | 执行路径 | 使用 bubblewrap |
| --- | --- | --- |
| 非 Docker runtime，包括传入软件 Profile 的默认路径 | `run_tool_in_sandbox` | 是 |
| Docker runtime，调用方不在指定交付 Docker runtime 内 | `run_tool_in_docker` | 此层不加 |
| Docker runtime，已在指定交付 Docker runtime 内 | `run_tool_direct` | 否 |

### 2.2 对应的实际调用方

| 调用方 | 状态与分支 | 结果 |
| --- | --- | --- |
| `task_gen/task_eval_mcp.py`，正式任务求解/轨迹采集 | 转发私有记录库和 Scope mounts；正常任务工作区进入 workspace 分支 | 非 Docker 直接受限进程；Docker 使用交付后端 |
| `env_gen/tool_gen/mcp_server.py`，通用交付 MCP | 使用 `runtime.root/state`，不传任务工作区数据库与 mounts | 非 Docker 默认进入 bubblewrap；Docker 按绑定执行 |
| `task_gen/program/utils/tool_runtime.py`，v2 Program Runtime | 显式提供 `_program_v2_call_tool`，覆盖默认分支 | host Python 用 bubblewrap；Python Profile 用 profile worker |
| 直接调用 Harness 或 `task_gen.tool_runtime.call_tool` | 若未传工作区布局/自定义执行函数，使用通用后端选择 | 非 Docker 默认进入 bubblewrap |

因此，**通用沙箱不是废弃代码，但也不是正式任务 MCP 的每次调用必经路径**。
旧格式 Program 等其他路径按默认分支及实际参数判断，不把 v2 Profile 的直接执行结论推广到全部调用。

### 2.3 三种机制分别保证什么

- Harness 事务：检查输入输出、授权资源和状态恢复，不等于操作系统隔离。
- `run_tool_direct`：对已验证交付代码提供路径约束、超时、内存/写入限制和独立输出；不提供 bubblewrap 的文件系统与 namespace 隔离，不能视作任意不可信代码的安全沙箱。
- `run_tool_in_sandbox`：用 bubblewrap 限定可见挂载、namespace 与工作区，并应用工具资源限制。

## 3. Verifier：为什么单独使用 bubblewrap

### 3.1 需要保护的边界

Verifier 不经过业务工具 MCP 来做全部调查，它可以调用 Codex 的命令执行能力。
业务工具的 Harness 权限不能自动约束这个独立进程；仅在提示词中要求“不修改证据”也不能形成文件权限边界。

`eval/verifier.py:sandbox_command` 因此为调查进程建立文件系统允许列表：

- `evidence/`、`request.json`、`prompt.txt` 只读挂载，避免调查过程改写判分依据。
- 指定系统目录与运行依赖只读可见，不把整个宿主数据目录挂入。
- 验收运行目录可写，用于 scratch、日志与 verdict；临时认证目录也可写，并非严格只允许写 scratch/logs。
- 创建 PID/UTS/IPC namespace 并移除 capabilities；保留模型 API 所需网络，不宣称网络隔离。

证据可能通过硬链接准备；只读挂载尤其重要，防止审核进程写坏关联原件。
这也不是完整的多租户安全边界：例如部分系统目录会被挂载，临时认证可见，部署仍需最小权限。

### 3.2 为什么不是重复一层

当前 `run_verification_agent` 为 Codex 客户端设置 `bypass_approvals_and_sandbox=True`，
同时用 `command_prefix` 把进程放入上述 bubblewrap。
也就是说，Verifier 当前使用的是项目自己建立的外层隔离，不是“Codex 内层沙箱再套一层”。
求解器的工作区和业务工具进程权限也不会自动延续到验收阶段。

### 3.3 是否必须永远使用它

不是业务验收算法必须依赖 bubblewrap，而是**当前实现选择它作为隔离后端**；找不到 `bwrap` 会拒绝启动。
若将来改为专用容器，须同样保证证据只读、可写范围、依赖/认证可见性和进程生命周期，再替换此处实现。
只删调用或文档名称会失去现有边界。本次文档整理不改变运行时行为。

## 4. 使用与部署检查

### 4.1 检查依赖

先确定实际调用方和后端，再检查对应依赖：

```bash
# 通用非 Docker 工具沙箱和当前正式 Verifier 使用
command -v bwrap

# 仅交付绑定为 Docker 时检查；还需确认指定镜像与挂载可用
docker info
```

binding 应由 `harness.delivery.load_delivery(binding)` 在实际 executor 所见文件系统中加载。
Python Profile 必须使用交付解释器、native 库和证书，不能静默换成当前 venv。
直接调用示例见[Harness 使用案例](HARNESS使用案例.md)，验收命令见[测评说明](EVALUATION_ZH.md)。

### 4.2 容器部署注意事项

非 hostPID Kubernetes 中按部署设置 `AGENT_WORLD_KUBERNETES_RUNTIME=1`；
对应 Docker 部署使用 `AGENT_WORLD_DOCKER_RUNTIME=1`。通用工具沙箱据此复用容器 procfs。
Verifier 也有 Kubernetes procfs 适配，但使用自己的 namespace 配置，不能假设与工具沙箱参数完全相同。
这些变量不提供 Docker daemon，也不代表自动跳过 bubblewrap。

Docker daemon 与 executor 所见项目路径不同，可使用 `AGENT_WORLD_DOCKER_PROJECT_ROOT`，两端源码版本须一致。
临时状态副本应有足够磁盘容量；缺解释器、库、镜像或隔离权限记录为环境问题，不静默切换后端。

## 5. 代码定位

| 职责 | 位置 |
| --- | --- |
| 默认执行分支与事务 | `harness/execution.py:call_environment_tool` |
| 通用沙箱、直接进程、Docker | `harness/sandbox.py` |
| Program Profile 与 v2 适配 | `task_gen/program/utils/tool_runtime.py`、`profile_tool_worker.py` |
| Verifier 隔离及调查进程 | `eval/verifier.py:sandbox_command`、`run_verification_agent` |
| 交付加载与启动 | `harness/delivery.py`、`env_gen/tool_gen/runtime_launch.py` |
