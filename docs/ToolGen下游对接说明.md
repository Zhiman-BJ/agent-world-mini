# ToolGen 产物对接说明

## 临时环境怎样工作

交付包里有一份原始环境。每跑一条任务，就复制一份到任务专属目录；如果 TaskGen 已为这条任务准备了环境，就复制 TaskGen 准备的那份。工具只操作复制出来的环境：同一条任务的后续调用能看到前一步的修改，下一条任务则使用新副本。

软件与任务数据分开。Python 工具使用环境绑定的软件 Profile（已安装依赖的目录）；需要 Docker 的工具复用已准备好的镜像，每次 MCP 会话启动一个临时容器，会话结束即退出。调用轨迹和最终数据保存在任务目录，供检查和制作训练数据。

```text
交付的原始环境 / TaskGen 准备的环境 → 复制一份 → 调用工具 → 保存调用记录和执行后的环境
```

## 交付入口

ToolGen 交付的是“一个环境及其工具”。下游无论是继续生成任务，还是让模型调用工具，都从该环境的 `binding.json` 开始。这个文件相当于交付包的目录：它告诉程序环境数据、工具清单、软件依赖分别在哪里。

170 上的一个现成例子：

```text
/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments/semiconductor_circuit_level_s_matrix_1/binding.json
```

| 交付内容 | 用途 |
| --- | --- |
| `environment/` | 环境说明和原始数据；`state/` 是工具要读取、修改的数据 |
| `tools/tools.json` | 正式工具的说明、参数和执行代码；模型只看到公开说明 |
| `software/profile.json` | 指向这个环境所需的软件和 Python 解释器 |
| `binding.json` | 把以上内容绑定在一起，是下游入口 |

软件包本体放在交付根目录的 `software_profiles/`。运行工具时，程序根据 `binding.json` 找到它；模型不需要知道软件安装在哪个目录。

## 继续生成任务数据：TaskGen 怎么接

目标是让 TaskGen 基于交付环境生成任务和参考解，并在**这个环境的软件条件下**实际执行工具。

1. 选一个环境的 `binding.json`，读取环境原始数据、正式工具和软件运行方式。
2. TaskGen 为每条任务准备一份独立的环境。同一条任务的多次工具调用使用这一份；下一条任务再准备新的一份。
3. 执行工具时使用该环境绑定的 Python 或 Docker 后端，不能仅加载工具名称而忽略软件依赖。

ToolGen 已提供统一加载入口，TaskGen 可以直接使用：

```python
from pathlib import Path
from env_gen.tool_gen.delivery_contract import load_delivery

delivery = load_delivery(Path("/path/to/environments/<package_id>/binding.json"))
environment_root = delivery.package.package_root  # 环境说明和原始 state/
tools = delivery.package.tools                    # 正式工具
python_path = delivery.python_path                 # Python 环境的解释器
software_root = delivery.software_root             # 安装好的软件依赖
backend = delivery.runtime["backend"]             # 运行方式
```

**TaskGen 当前要调整的是加载入口。**`task_gen/program/step_0_environment_load.py` 还在自行拼交付路径，并假设软件目录里有 `profile.json`、解释器固定在 `python/bin/python`。现有交付包把真实路径写在各环境的 `software/profile.json`，解释器可能位于 `python-3.11/bin/python`。用上面的真实环境测试，当前 Step 0 会在寻找共享目录的 `profile.json` 时停下；只传 `--binding` 时，交付根目录也会推算错一级。

下游把 Step 0 的路径解析统一改为 `load_delivery(binding_path)`，再将得到的运行方式传给工具执行层。接好后，用一个真实环境跑通 Step 0 和一次工具调用，就能确认“生成任务”和“执行任务”用的是同一套环境。

## 让 Kimi 通过 MCP 调用工具

MCP 衔接层的作用很直接：Kimi 发起工具调用，MCP 在这条任务的环境副本中执行，再把结果返回给 Kimi。下游需要先确定**这条任务用哪份环境**：

| 要做的事 | 从哪份环境开始 | 接入方式 |
| --- | --- | --- |
| 探索交付环境，或执行一条新任务 | 交付包里的原始环境 `environment/state/` | 下方的 ToolGen MCP 命令 |
| 执行 TaskGen 已生成的具体任务 | TaskGen 为该任务准备的环境 | TaskGen 的任务级 MCP 入口 |

### 从交付的原始环境开始

在能访问交付包的机器上，每条任务建立一个新的运行目录，并生成 MCP 配置：

```bash
cd /data1/agent_world/toolgen
BINDING=/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments/semiconductor_circuit_level_s_matrix_1/binding.json
RUN_DIR=/data1/agent_world/toolgen_runs/task_001
mkdir -p "$RUN_DIR"
.venv/bin/python -m env_gen.tool_gen.kimi_mcp "$BINDING" \
  --trace "$RUN_DIR/tool_calls.jsonl" \
  --session-root "$RUN_DIR/sandbox" \
  --print-kimi-config > "$RUN_DIR/mcp.json"
```

这一步只生成配置；Kimi 开始会话时才会启动 MCP 进程。把 `mcp.json` 中的 `mcpServers.agent_world` 以 `agent_world` 为名称注册到 Kimi Code 或 Kimi SDK。配置中已有启动命令、参数和环境变量。Kimi 运行器必须能访问这些交付包路径；最简单的方式是在 170 上运行。

模型可以先调用 `get_environment_overview`、`list_environment_resources` 找到数据，再调用业务工具。工具返回的文件引用形如 `aw://<scope_id>/<相对路径>`：这是**环境内部**的文件地址，不是服务器磁盘的绝对路径。

一条任务结束后，`RUN_DIR` 中保存：

| 产物 | 可以用来做什么 |
| --- | --- |
| `tool_calls.jsonl` | 查看每次调用了哪个工具、传了什么参数、返回了什么 |
| `sandbox/state/` | 查看工具执行后的环境数据 |
| `sandbox/session.json` | 查看调用次数和最终状态摘要 |

做蒸馏时，还要由 Kimi 运行器保存任务输入、模型回复和工具选择；这些对话与 `tool_calls.jsonl` 合在一起才是完整训练轨迹。做下一条任务或下一次在线强化学习 rollout 时，换一个新的 `RUN_DIR`，再从原始环境复制一份。

### 执行 TaskGen 已生成的任务

这时要用 TaskGen 为该任务准备的环境。现有任务级入口是 `task_gen/task_eval_kimi_mcp.py`；`task_gen/task_eval.py --agent-backend kimi` 会启动它。下游先在 `config/task_eval_kimi.yaml` 的 `llm.kimi.binding_path` 填入该环境的 `binding.json`，再用安装了 TaskGen 依赖的 Python 3.11+ 运行：

```bash
python -m task_gen.task_eval \
  --config config/task_eval_kimi.yaml \
  --input-root /path/to/taskgen/run \
  --agent-backend kimi --limit 1
```

任务级 MCP 使用该任务的环境副本，执行工具后保存轨迹。这里的 `/path/to/taskgen/run` 是 TaskGen 的任务产物目录。170 上 ToolGen 自带的 `.venv` 是 Python 3.10，不能直接用它启动这段 TaskGen 代码；TaskGen 运行环境还需安装自己的依赖。

TaskGen 的 Python 环境任务已有这条接入路径；Docker 环境的任务级工具执行还需按绑定后端接通。上面直接生成 `mcp.json` 的命令适用于“从交付的原始环境开始”的场景。

## 交接时核对

- TaskGen：用真实 `binding.json` 跑通加载、任务状态复制和一次正式工具调用。
- Kimi/MCP：用一条任务确认模型能看到工具、工具结果返回、`tool_calls.jsonl` 与最终状态落盘。

截至 2026 年 9 月 24 日，170 上已验证交付包的 Python MCP 调用和 Docker 测试镜像中的会话及文件修改；Kimi 模型完成整条任务的验收由接入方继续进行。
