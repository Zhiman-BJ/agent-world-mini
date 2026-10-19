# 快速上手

目标是先完成一条已有任务的模型执行和独立验收，不需要重建数据或重新生成题目。
下述命令从仓库根目录执行，`/path/to/...` 须替换为当前机器上的真实路径。

## 1. 先理解本次运行

完整系统见[系统架构](SYSTEM_ARCHITECTURE_ZH.md)。已有题集试跑只涉及以下链条：

```text
任务 + 初态 + binding
  → eval run → Kimi Code 求解 → MCP / Harness 业务工具
  → 回答、终态与轨迹
  → 独立 Verifier 调查 → verdict
```

求解器与 Verifier 是两个 Agent，各自有模型配置和输出；completed 不等于 PASS。
当前 Verifier 使用 bubblewrap 保护只读证据，任务业务工具则按实际后端执行，
两者不应混为一个沙箱。详见[执行与隔离](TOOL_RUNTIME_ZH.md)。

## 2. 实际操作

### 2.1 准备运行环境

推荐 Linux + Python 3.12。包元数据仍声明 Python >=3.10，但当前 Verifier 使用标准库 `tomllib`，
部分工具使用 `hashlib.file_digest`，接手环境应使用 Python >=3.11，避免只按元数据安装旧版本。

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m eval --help
python -m eval run --help
```

另外需要已安装且可启动的 Kimi Code CLI；正式 Verifier 需要 Codex CLI 和 bubblewrap。
科学软件不统一装进这个 venv：它们来自 ToolGen binding 对应的 Python Profile 或 Docker 镜像。
Docker 任务还需要实际可访问的 Docker daemon，仅有客户端不够。

```bash
command -v kimi
command -v codex
command -v bwrap
```

### 2.2 确认输入与凭据

测评输入是可由 `task_gen.eval_cases.load_cases` 加载的任务集合，不是单独一份题文。
必须有任务、初态、工具和对应的交付绑定。常见 Program 任务目录见[任务生成说明](TASK_GENERATION_ZH.md)。

```bash
export EVAL_INPUT=/path/to/taskgen-inputs
export EVAL_OUTPUT=/path/to/new-evaluation
export KIMI_CODE_BIN=/path/to/kimi
export KIMI_BASE_URL=https://your-model-service.example/v1
export EVAL_MODEL=your-upstream-model-id
```

通过受保护的环境变量或密钥管理器注入 `KIMI_API_KEY`，不要在命令历史中填写真实 key。
Verifier 另读 `--verifier-codex-home` 中的 `config.toml` 与认证；其 provider 要求的环境变量也须可用。
求解器凭据与 Verifier 凭据不必相同，不要把二者配置混用。

### 2.3 先跑一题

以下是新实验示例，不是对历史批次协议的修改。示例使用 Responses 接口和 `high`；
只有支持对应协议、上下文和输出预算的上游服务才能直接运行。

```bash
python -m eval run \
  --input-root "$EVAL_INPUT" --output-root "$EVAL_OUTPUT" \
  --limit 1 --max-concurrency 1 --verifier-concurrency 1 \
  --kimi-bin "$KIMI_CODE_BIN" --base-url "$KIMI_BASE_URL" \
  --api-key-env KIMI_API_KEY --model-id "$EVAL_MODEL" \
  --provider-type openai_responses --reasoning-effort high \
  --mcp-server-name workspace \
  --max-context-size 262144 --max-output-size 32768 \
  --max-tool-calls 85 \
  --read-default-max-chars 80000 --read-max-chars 128000 \
  --compaction-trigger-ratio 0.8 \
  --verify-after-each --verifier-model gpt-6.1-sol \
  --verifier-reasoning-effort high
```

本例不传 `--delete-task-state`，以便保留工作区和私有终态。首次运行不要同时改工具包、题文和推理协议。
若上游只支持 Kimi 接口，使用 `--provider-type kimi` 并按服务设置真实模型 ID；不是改一个模型名字就能切换协议。

### 2.4 查看结果

`output-root` 下会创建带时间的批次目录。查看该目录的 `selection.json`、`summary.json`、
各题 `trajectory.json`、`raw/` 和 `verifiers/<case>/verdict.json`。
`summary.json` 的 completed 不等于 PASS，须看 `verification.status` 与 `verification.outcome`。

```bash
python -m eval visualize \
  --run-directory /path/to/the-created-run --combined \
  --output /path/to/trajectories.html
```

确认一题的模型请求、工具、终态和 verdict 都正常，再按[测评说明](EVALUATION_ZH.md)扩展到固定题集、20 并发和双模型对比。
