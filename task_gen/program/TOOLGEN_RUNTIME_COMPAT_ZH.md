# ToolGen 交付运行时兼容说明

本文面向需要让其他 TaskGen 流程消费 ToolGen `binding.json` 的开发者，说明当前
Program TaskGen 对两类历史交付的兼容方式，以及基础设施失败的停止和记录规则。

## 1. 适用范围

TaskGen 始终以 ToolGen 交付的 `binding.json` 为入口，并服从其 `runtime.json` 选择的
运行后端：

- `python_profile`：使用 ToolGen 随环境交付的 Python Profile；
- `docker`：使用 ToolGen 声明的 Docker 镜像；
- `host_python`：使用当前 Harness Python。

兼容层不改写 ToolGen 交付目录，不替换工具代码，也不把失败工具伪装成成功。它只修复
历史交付中已经失效的启动路径，使工具有机会按原契约执行。

实际调用关系如下：

```text
Solution 的 call_tool
  -> CompleteEnvironmentRuntime
  -> Harness call_environment_tool（事务、Schema、资源边界、提交/回滚）
  -> 按 binding/runtime.json 选择后端
       -> python_profile：标准 bwrap；特定 execvp 失败时才启用 Profile 回退
       -> docker：标准容器启动；兼容模式修正容器命令和模块路径
       -> host_python：标准 Harness Python
  -> ToolGen 工具 internal.code
```

另一个 TaskGen 接入时应保留 `call_environment_tool` 这一外层，不能直接把工具代码当作
普通 Python 函数执行。兼容逻辑替换的只是已经无法启动的内层执行器。

## 2. 遇到的两类兼容问题

### 2.1 Python Profile 的绝对符号链接

部分 Profile 的 `python/bin/python` 是绝对符号链接。该路径在宿主机上可以执行，但在
bubblewrap 的挂载命名空间中无法解析，表现为包含 `bwrap` 和 `execvp` 的启动失败。

启用兼容开关后，只有同时满足以下条件才回退：

1. 正常 Harness 沙箱调用已经失败；
2. 错误同时包含 `bwrap` 和 `execvp`；
3. ToolGen 提供了明确的 Profile Python 和 software root；
4. `AGENT_WORLD_ALLOW_UNSANDBOXED_PROFILE_FALLBACK=1`。

回退时由 Profile Python 执行
`task_gen.program.utils.profile_tool_worker`。工具仍在 Harness 为本次调用创建的事务状态
副本上运行，外层仍负责输入输出 Schema、资源写权限、状态差异、成功提交和失败回滚。

实现位置：

- `utils/tool_runtime.py::_program_v2_sandbox_call_tool()`
- `utils/tool_runtime.py::execute_profile_tool()`
- `utils/profile_tool_worker.py`

### 2.2 历史 Docker 回执中的过期宿主路径

部分旧 Docker `runtime.json` 保存了生成机器上的绝对 Python 路径，并且镜像内没有
TaskGen 当前使用的模块路径或第二层 `bwrap`。启用兼容开关后，TaskGen 在内存中为本次
启动做以下映射，不写回交付文件：

- `python_command` 改为镜像内的 `python`；
- `software_root` 映射为 `/opt/tool-software`；
- 注入 `PYTHONPATH=/opt/agent-world`；
- 当镜像没有第二层 bubblewrap 时，在 Docker 容器内直接执行工具 `internal.code`。

Docker 容器仍是进程和依赖边界，Harness 外层仍保留事务状态、Schema、资源边界和回滚
检查。

实现位置：

- `utils/tool_runtime.py::_docker_call_tool()`
- `utils/harness_docker_worker.py::_direct_compat_call()`

## 3. 如何启用

从仓库根目录运行：

```bash
export PYTHONPATH="$PWD/agent-world-mini${PYTHONPATH:+:$PYTHONPATH}"
export AGENT_WORLD_ALLOW_UNSANDBOXED_PROFILE_FALLBACK=1
export AGENT_WORLD_DOCKER_COMPAT_PATCH=1

python -m task_gen.program.run_pipeline \
  --binding /path/to/delivery/environments/ENV_ID/binding.json \
  --output-dir /path/to/output/ENV_ID/attempt_01 \
  --model gpt-6-sol \
  --task-count 10 \
  --minimum-effective-tool-calls 15 \
  --execution-timeout-seconds 2000 \
  --agent-timeout-seconds 2000
```

两个开关彼此独立。只使用 Python Profile 交付时无需打开 Docker 开关，反之亦然。不设置
开关时保持 Harness 的标准行为。

建议把 `TMPDIR` 指向容量足够的数据盘。每个并发环境会复制或创建执行状态，高并发使用
系统 `/tmp` 很容易耗尽根分区：

```bash
export TMPDIR=/data1/agent_world/task_program/BATCH_ID/_tmp
mkdir -p "$TMPDIR"
```

## 4. 什么应该跳过，什么可以修复

兼容补丁只处理“交付内容存在，但启动地址已经失效”的问题。以下属于环境基础设施不可用：

- 动态库缺失，例如 `libGLU.so.1`、`libtorch_cpu.so`；
- Python 包或外部可执行程序在交付运行时中不存在；
- 工具明确返回 `backend_unavailable`、`dependency_unavailable` 或同类错误；
- Docker 镜像不存在或无法启动；
- 工具实现本身在合法输入下无法运行。

这些问题不能通过改写任务或 Solution 修复。Step 2 会把最终状态写为：

```json
{
  "status": "blocked_infrastructure",
  "requested": 10,
  "accepted": 0,
  "infrastructure_failure": ["包含失败工具和原始错误的信息"]
}
```

批处理器应将其记录为 `skipped_infrastructure`，保存以下信息，然后直接处理下一个环境：

- `environment_id`；
- 失败工具；
- 原始错误；
- 对应的 `run_dir`；
- 已启用的兼容开关。

不要把以下任务生成问题误判为环境问题：任务正文和 Solution 不一致、有效调用数不足、
参数无法溯源、答案错误、Semantic Review 未通过、重放不一致。这些仍应交给同一个生成
Agent 在限定轮数内修复。

## 5. 推荐的批处理判定

批处理器以 `step2_validation.json` 为准，不要只看进程退出码。Step 2 在未达到请求数量时
会以非零状态退出，包括已经正确记录的基础设施阻塞。

```python
validation = json.loads((run_dir / "step2_validation.json").read_text())

if (
    validation.get("status") == "passed"
    and int(validation.get("accepted", 0)) >= task_count
    and (run_dir / "tasks.json").is_file()
):
    status = "passed"
elif validation.get("status") == "blocked_infrastructure":
    status = "skipped_infrastructure"
    reason = validation.get("infrastructure_failure")
else:
    status = "failed_generation"
```

每个已确认的基础设施失败只执行一次兼容尝试。不要在下一轮批处理中再次自动重试；只有
ToolGen 重新发布 Profile、镜像或依赖后，才应显式清除跳过记录并重跑。

## 6. 安全边界和限制

这两个开关是历史交付的任务生成兼容模式，不是新的正式运行协议：

- Python Profile 回退绕过的是失败的内层 bubblewrap 进程隔离；
- Docker 回退绕过的是容器内不存在的第二层 bubblewrap；
- 两者仍保留外层 Harness 的状态事务、Schema 和资源边界检查；
- 最终评测应优先使用 ToolGen 修复后的标准交付运行时，不应默认依赖这些开关；
- 缺库、缺包、工具实现错误不会被兼容层吞掉，仍会作为基础设施问题暴露。

## 7. 接入自检

接入另一个 TaskGen 后，至少验证：

1. 用一个 Python Profile 环境完整生成 1 条任务并 clean replay；
2. 用一个 Docker 环境确认容器能加载 TaskGen worker；
3. 故意选择一个缺依赖环境，确认被记为 `skipped_infrastructure` 且不重试；
4. 检查通过任务同时存在 `tasks.json` 和 `step2_validation.json`；
5. 检查 `accepted` 等于请求任务数，而不是只依据命令退出码；
6. 并发运行时监控 `$TMPDIR` 和数据盘容量。
