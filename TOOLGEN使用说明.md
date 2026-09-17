# ToolGen 使用说明

ToolGen 的输入是 DataGen 已生成的环境包，输出是可执行工具、环境状态和对应的软件运行环境。批量运行时，上游目录保持不变，所有中间产物和最终结果写入单独的工作目录。

## 首次准备

```bash
cd /data1/agent_world/toolgen
python3 -m venv .venv
.venv/bin/pip install -e .
codex --version
```

`codex` 使用当前服务器账号已经配置的模型 API。

## 启动

```bash
SOURCE_ROOT=/data1/agent_world/env_without_tools/semiconductor_0917/rich
WORKSPACE_ROOT=/data1/agent_world/runs/semiconductor_0917_toolgen

mkdir -p "$WORKSPACE_ROOT/logs"
nohup /data1/agent_world/toolgen/.venv/bin/agent-world-tool-gen-batch \
  "$SOURCE_ROOT" "$WORKSPACE_ROOT" \
  --workers 8 \
  --model gpt-5.6-sol \
  >"$WORKSPACE_ROOT/logs/batch.stdout.log" \
  2>"$WORKSPACE_ROOT/logs/batch.stderr.log" &
echo $! >"$WORKSPACE_ROOT/logs/batch.pid"
```

## 查看进度

```bash
jq '{state,summary,updated_at}' "$WORKSPACE_ROOT/logs/status.json"
```

每个环境的完整日志在 `logs/<环境名>.log`，工具级进度在 `environments/<环境名>/tool_generation/progress.json`。

## 停止和继续

```bash
kill -TERM "$(cat "$WORKSPACE_ROOT/logs/batch.pid")"
```

停止时会保存状态并结束本批次启动的子进程。再次执行相同启动命令即可继续：完整交付的环境直接跳过，未完成环境沿用已有中间产物和下载缓存。

## 结果位置

```text
<workspace_root>/delivery/environments/<环境名>/
├── binding.json
├── environment/
├── tools/tools.json
└── software/profile.json
```

下游从 `binding.json` 开始加载。完整参数、状态说明和镜像配置见 [`env_gen/tool_gen/BATCH_RUN_ZH.md`](env_gen/tool_gen/BATCH_RUN_ZH.md)。
