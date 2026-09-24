# ToolGen 下游怎么用

## 临时环境怎么运行

以使用 Docker 的专业环境为例：

```text
提前构建并缓存专业软件镜像
            ↓
下游为一条任务创建运行目录
            ↓
自动把原始环境复制到 sandbox/
            ↓
启动一个临时 Docker 容器
            ↓
同一任务的多次工具调用共用 sandbox/ 中的数据
            ↓
MCP 结束：容器自动删除，最终数据和工具调用记录留在运行目录
```

下一条任务重新创建运行目录，从原始环境开始。使用 Python 软件环境的工具也是这个流程，只是直接使用已装好依赖的 Python，不启动 Docker 容器。

## 我们交付什么

每个环境都有一个 `binding.json`。它指向三样东西：原始环境、可以调用的工具、运行工具所需的软件。下游选定这个文件，就能找到该环境的完整交付内容。

170 上的一个例子：

```text
/data1/agent_world/toolgen_semiconductor_160_20260918/delivery/environments/semiconductor_circuit_level_s_matrix_1/binding.json
```

## 继续合成任务数据：TaskGen 怎么接

TaskGen 从 `binding.json` 加载原始环境和工具，生成任务、参考解等数据。需要执行工具时，使用同一文件指定的 Python 软件环境或 Docker 镜像。

具体要改的是 TaskGen 的 `task_gen/program/step_0_environment_load.py`：它目前自己拼软件路径，和真实交付包的目录不一致。改为调用 ToolGen 已有的加载函数，让路径和运行方式都从 `binding.json` 取得：

```python
from pathlib import Path
from env_gen.tool_gen.delivery_contract import load_delivery

delivery = load_delivery(Path(binding_path))
environment = delivery.package.package_root  # 原始环境
tools = delivery.package.tools              # 工具
runtime = delivery.runtime                  # Python 或 Docker 运行方式
python = delivery.python_path                # Python 环境使用的解释器
software = delivery.software_root            # 已安装的专业软件
```

随后用一个真实环境跑通 TaskGen 的环境加载和一次工具调用。当前 TaskGen 代码需要安装好依赖的 Python 3.11+；Docker 环境的任务级执行还需由 TaskGen 按 `runtime` 接通。

## 通过 Kimi/MCP 调用工具

如果要让 Kimi 从交付的原始环境开始做一条任务，先在 170 上为这条任务生成 MCP 配置：

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

把 `mcp.json` 交给 Kimi Code 或 Kimi SDK。Kimi 开始任务时会按配置启动 MCP；工具运行所需的 Python 环境或 Docker 镜像由交付包决定。运行器需要能访问上述交付包路径，因此最方便的是也在 170 上运行。

任务结束后，在 `RUN_DIR` 里看三样东西：`tool_calls.jsonl` 是工具调用记录，`sandbox/state/` 是工具执行后的数据，`sandbox/session.json` 是本次运行的摘要。做蒸馏时，Kimi 的任务输入和模型回答也要由下游一并保存。

如果要执行 **TaskGen 已生成的任务**，则通过 TaskGen 的任务级入口 `task_gen/task_eval.py --agent-backend kimi` 调用 MCP。它使用这条任务准备好的环境；上面的命令使用交付包里的原始环境。
