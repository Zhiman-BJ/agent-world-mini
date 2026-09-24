# ToolGen 下游对接说明

ToolGen 每个环境交付一个 `binding.json`。它指出环境初始数据、正式工具、软件依赖和运行方式。下游只需选定一个环境的 `binding.json`，两条后续流程都从这里开始。

例如，170 上现有的一个环境入口是：

```text
/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments/semiconductor_circuit_level_s_matrix_1/binding.json
```

```text
                         ┌→ TaskGen：生成任务、参考解和验证数据
环境交付 binding.json ──────┤
                         └→ Kimi + MCP：执行任务、收集工具调用轨迹 → 蒸馏或训练
```

## 路线一：交给 TaskGen 继续造任务数据

TaskGen 读取 `binding.json`，取得环境初始数据和工具；生成任务时，工具执行必须使用该环境绑定的软件运行方式。建议直接复用 ToolGen 的加载入口：

```python
from pathlib import Path
from env_gen.tool_gen.delivery_contract import load_delivery

delivery = load_delivery(Path("/path/to/environments/<package_id>/binding.json"))
environment_root = delivery.package.package_root  # environment.json 和初始 state/
tools = delivery.package.tools                    # 正式工具
python = delivery.python_path                     # Python Profile 环境时使用
backend = delivery.runtime["backend"]             # host_python / python_profile / docker
```

TaskGen 的 Step 0 用 `environment_root` 和 `tools` 冻结一份任务生成基线。每条任务从这份基线复制独立状态；执行工具时沿用 `delivery.python_path`、`delivery.software_root` 和 `delivery.runtime` 指定的运行方式，同一条任务中的多次调用使用同一份状态。这样任务生成和工具执行都对应同一个环境包。

**TaskGen 需要改的地方：**`task_gen/program/step_0_environment_load.py` 目前自己推算交付根目录、软件目录和 Python 路径。其中，仅传 `--binding` 时交付根目录会少退一级；它还要求共享软件目录中有 `profile.json`，并固定寻找 `python/bin/python`。现有交付包的实际 Python 路径写在各环境的 `software/profile.json` 中，例如 `python-3.11/bin/python`。我用上面的真实环境试过：即使明确传交付根目录，当前 Step 0 仍会在查找共享 `profile.json` 时停止。

下游将 Step 0 的路径解析改为 `load_delivery(binding_path)`，把得到的环境、工具和运行方式传给后续步骤；任务执行层按绑定的后端调用工具。改完用一个真实环境跑通 Step 0 和一次正式工具调用。

## 路线二：通过 Kimi Code 的 MCP 衔接层调用工具

这条路线用于让 Kimi 等模型通过 MCP 操作环境，收集蒸馏或在线强化学习所需的调用轨迹。**每条任务使用一个新的运行目录和 MCP 会话**。Kimi 调用 MCP 工具；MCP 在独立的状态副本上执行。如果环境绑定 Python Profile，就在对应 Python 中运行；如果绑定 Docker，就启动该镜像中的临时容器。

如果任务从交付环境的原始状态开始，在能访问交付包和 ToolGen 代码的机器上生成 MCP 配置：

```bash
cd /data1/agent_world/toolgen
BINDING=/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments/semiconductor_circuit_level_s_matrix_1/binding.json
RUN_DIR=/data1/agent_world/toolgen_runs/task_001
mkdir -p "$RUN_DIR"
.venv/bin/python -m env_gen.tool_gen.kimi_mcp "$BINDING" \
  --server-name agent_world \
  --trace "$RUN_DIR/tool_calls.jsonl" \
  --session-root "$RUN_DIR/sandbox" \
  --print-kimi-config > "$RUN_DIR/mcp.json"
```

在 Kimi Code 中加载这份 `mcp.json`；使用 Kimi SDK 时，将 `mcpServers.agent_world` 作为名为 `agent_world` 的 stdio MCP server 注册，再开始模型会话。配置已包含启动命令、参数和环境变量；运行器与交付包须在同一台机器上，或能访问相同路径。Kimi 可先调用 `get_environment_overview`、`list_environment_resources` 寻找资源，再调用业务工具。文件在工具接口中用 `aw://<scope_id>/<相对路径>` 表示，而不是主机上的绝对路径。

一次任务结束后，运行目录中有：

| 文件 | 内容 |
| --- | --- |
| `tool_calls.jsonl` | 每次工具调用的参数、返回值和状态变化 |
| `sandbox/state/` | 这条任务结束时的环境状态 |
| `sandbox/session.json` | 调用次数及最终状态摘要，文件以 SHA-256 和字节数记录 |

蒸馏运行器还需要保存 Kimi 的完整对话（任务输入、模型回复和工具选择），与上面的工具轨迹一起组成训练样本。如果只希望模型使用这套环境工具，运行器需要将可用工具限制为相应的 MCP 工具。做下一条任务或下一次强化学习 rollout 时，换一个新的 `RUN_DIR`，从环境初始状态重新开始。

**如果执行的是 TaskGen 已生成的任务**，应使用该任务自己的初始状态，而不是上面 `binding.json` 对应的原始状态。现有 `task_gen/task_eval_kimi_mcp.py` 是任务级入口：它接收任务状态目录，再通过同一套环境工具执行；`task_gen/task_eval.py --agent-backend kimi` 会调用它。蒸馏或训练运行器接入时也要传入任务初态、`binding.json` 和独立轨迹路径。Docker 环境的任务级执行后端还需由下游接通。

## 当前可用性

- MCP 会话已在 170 上通过真实 Python Profile 环境调用；Docker 后端也已通过包含文件修改的真实容器会话测试。
- 现有 160 环境是较早的 Python Profile 交付，`binding.json` 尚无 `runtime_path`；`load_delivery()` 会据其软件映射识别为 `python_profile`。TaskGen 需按上文对齐 Step 0 的软件路径。
- 170 当前未发现 `kimi` 命令；MCP 进程与配置已经验证，Kimi 模型回合由下游的 Kimi Code/SDK 运行器接入并验收。
