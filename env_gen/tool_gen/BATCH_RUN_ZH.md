# ToolGen 批量运行说明

这套批量入口用于处理一个目录中的多个 DataGen v2 环境包。每个环境单独运行、单独记日志；已经交付完成的环境再次启动时会直接跳过，未完成的环境沿用已有能力清单、软件环境和工具草稿继续处理。

## 目录准备

输入目录的下一层是环境包：

```text
<source_root>/
├── environment_a/
│   ├── environment.json
│   ├── validation.json
│   ├── state/
│   └── provenance/
└── environment_b/
    └── ...
```

为每一批数据新建一个工作目录。工作目录保存环境副本、中间产物、日志、共享下载缓存和最终交付；上游输入目录不会被修改。

```text
<workspace_root>/
├── environments/   各环境的工作副本和 tool_generation 中间产物
├── runtime/        uv 下载缓存和共享 Python 解释器
├── logs/           总状态与每个环境的完整日志
└── delivery/       可以交给下游的正式结果
```

## 安装

在代码目录创建一次 Python 环境：

```bash
cd /data1/agent_world/toolgen
python3 -m venv .venv
.venv/bin/pip install -e .
```

服务器还需要能够直接运行 `codex`，并在当前账号下配置好模型 API。可以先检查：

```bash
codex --version
.venv/bin/python -m env_gen.tool_gen --help
```

## 启动一批环境

下面示例使用 8 路并发。`source_root` 换成上游环境目录，`workspace_root` 换成本批次的独立工作目录。

```bash
SOURCE_ROOT=/data1/agent_world/env_without_tools/semiconductor_0917/rich
WORKSPACE_ROOT=/data1/agent_world/runs/semiconductor_0917_toolgen

mkdir -p "$WORKSPACE_ROOT/logs"
nohup /data1/agent_world/toolgen/.venv/bin/agent-world-tool-gen-batch \
  "$SOURCE_ROOT" \
  "$WORKSPACE_ROOT" \
  --workers 8 \
  --model gpt-5.6-sol \
  >"$WORKSPACE_ROOT/logs/batch.stdout.log" \
  2>"$WORKSPACE_ROOT/logs/batch.stderr.log" &
echo $! >"$WORKSPACE_ROOT/logs/batch.pid"
```

单次 Agent 会话默认最多运行 1800 秒。一个环境失败会在自己的日志中留下原因，其他环境继续运行；同一工作目录不能同时启动两份批次。

只运行指定环境时重复使用 `--environment`：

```bash
agent-world-tool-gen-batch "$SOURCE_ROOT" "$WORKSPACE_ROOT" \
  --workers 2 \
  --environment environment_a \
  --environment environment_b
```

## 查看进度

总状态文件：

```bash
jq '{state,summary,updated_at}' "$WORKSPACE_ROOT/logs/status.json"
```

查看每个环境当前阶段：

```bash
jq '.environments | to_entries[] | {
  environment: .key,
  state: .value.state,
  attempt: .value.attempt,
  progress: .value.progress
}' "$WORKSPACE_ROOT/logs/status.json"
```

环境日志位于：

```text
<workspace_root>/logs/<environment_name>.log
```

工具生成的细分进度位于：

```text
<workspace_root>/environments/<environment_name>/tool_generation/progress.json
```

`status.json` 中常见状态：

- `pending`：等待并发槽位；
- `running`：正在处理，`heartbeat_at` 会持续更新；
- `retrying`：本环境正在进行一次完整续跑；
- `succeeded`：已经形成完整交付；
- `failed`：重试后仍未完成，日志摘要写在 `log_excerpt`；
- `interrupted`：批次收到停止信号，已有文件保留。

## 停止和继续

使用普通终止信号停止：

```bash
kill -TERM "$(cat "$WORKSPACE_ROOT/logs/batch.pid")"
```

批量入口会停止自己启动的 ToolGen 和 Codex 子进程，并把状态写为 `interrupted`。再次执行原启动命令即可继续：

- `delivery/` 中已经完整交付的环境直接跳过；
- 其余环境从 `tool_generation/` 中已有的能力清单、软件计划和工具草稿继续；
- 下载完成的软件包和 Python 解释器继续使用 `runtime/` 中的缓存。

## 软件包下载

Python 依赖默认先使用阿里云 PyPI 镜像，官方 PyPI 作为后备；Node 依赖先使用 npmmirror，失败后切换到 npm 官方源。所有并发环境共享下载缓存，但每个环境仍保留自己的可执行软件环境。

需要更换镜像时在启动前设置：

```bash
export TOOLGEN_PYPI_INDEX_URL=https://your-mirror.example/simple
export TOOLGEN_PYPI_FALLBACK_URL=https://pypi.org/simple
export TOOLGEN_NPM_REGISTRY=https://your-npm-mirror.example
```

## 最终结果

成功环境位于：

```text
<workspace_root>/delivery/environments/<environment_name>/
├── binding.json
├── environment/
├── tools/tools.json
└── software/profile.json
```

下游从 `binding.json` 开始加载，它记录了环境状态、工具文件和软件 Profile 的对应路径。共享软件位于 `delivery/software_profiles/profiles/`。
