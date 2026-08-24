# Agent-World Mini 中文使用手册

这份文档只讲怎么运行和怎么看结果。项目原理可先看仓库根目录的 `README.md`。

## 1. 准备环境

在 PowerShell 中运行：

```powershell
python --version
python -m pip install -e .
Copy-Item config/api_keys.env.example config/api_keys.env
```

Python 版本需要不低于 3.10。项目本身没有第三方运行依赖。

打开 `config/api_keys.env`，填写模型服务：

```dotenv
LLM_API_KEY=your-key
LLM_MODEL=deepseek/deepseek-v4-flash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_TIMEOUT_SECONDS=90
LLM_STREAM=false
```

`LLM_BASE_URL` 可以写到 `/v1`，也可以填写完整的 `/chat/completions` 地址。当前 Web Research Agent 会使用 OpenRouter 的 `web_search` 和 `web_fetch` 工具，因此运行通用主题的网络研究时仍需配置支持这些工具的 OpenRouter 服务；其他工具筛选和任务生成只要求兼容 Chat Completions。

代码中也可以直接初始化：

```python
llm = LLMClient(
    model="your-model",
    base_url="https://api.openai.com/v1",
    api_key="your-key",
    stream=True,
)
```

`LLM_STREAM=true` 会让客户端以 SSE 流式读取响应。也可以向 `complete_text()` 或 `complete_json()` 传入 `on_delta` 回调实时接收文本增量。

`CodexAgentClient()` 默认使用本机 `~/.codex/config.toml`。需要覆盖时，直接给它传入自己的 `model`、`base_url` 和 `api_key`；这部分配置与 `LLMClient` 无关。

先确认命令可用：

```powershell
python -m agent_world_mini --help
python -m unittest discover -s tests -q
```

## 2. 选择一种入口

### 内置结构化主题

这是最容易复现的方式。可用主题以 `--help` 显示的 `--theme-id` 列表为准。

```powershell
python -m agent_world_mini `
  --theme-id world-bank-development `
  --slug world-bank-demo
```

内置适配器直接读取已经选定的公开 API，来源较稳定。模型主要负责工具筛选和任务语义审查。

### 自然语言主题

```powershell
python -m agent_world_mini `
  --theme "公开的航空事故调查与飞机型号数据" `
  --slug aviation-safety
```

Research Agent 会自行搜索公开网页和 API。它只保留实际抓取到的数据，不允许用模型补造记录。

### 指定一个 MCP 或工具页面

```powershell
python -m agent_world_mini `
  --source-url "https://example.com/a-public-mcp-server" `
  --slug example-mcp
```

页面只负责告诉 Research Agent 这个环境大致做什么。最终工具仍然根据挖到的数据重新生成，不要求照搬 MCP 页面里的工具。

### 从本地环境目录批量选择

```powershell
python -m agent_world_mini --batch-size 5 --selection-seed 42 --dry-run
python -m agent_world_mini --batch-size 5 --selection-seed 42
```

`--dry-run` 只做选择和查重。正式运行时，pipeline 会从本地 `agent_world_mini/seed_gen/data/smithery_140_v1_0824.json` 选择此前未成功完成的环境，不会临时自己编 5 个主题。

### 使用 Codex Research Agent 的结果

Codex 子智能体先生成 `research_bundle.json`，然后运行：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-genomics/research_bundle.json `
  --slug codex-genomics
```

这会跳过内置网络研究，只替换 pipeline 的 Research Agent。后面的工具生成、验证、构图和任务生成仍由同一套 Python 代码完成。具体见 `docs/CODEX_WORKFLOW_ZH.md`。

## 3. Pipeline 实际执行顺序

一次普通运行依次完成：

1. 解析主题或读取本地目录项。
2. 搜索并抓取真实公开数据。
3. 根据当前数据生成候选工具。
4. 用本地数据实际执行每个候选工具，失败的工具不进入环境。
5. 检查具体 ID 能否沿工具输出传到下一个工具，再构建依赖图。
6. 从图中扩展连通拓扑，不预设 1～8 或 5～14 这样的目标长度。
7. 给候选链绑定真实参数并执行。
8. 删除不影响答案的步骤，再做全局执行去重。
9. 让模型把每 4 条已执行候选作为一组进行语义审查和任务表述。
10. 新任务产出率连续两批过低，或达到候选预算后停止。

默认候选预算是 128。它是防止单个环境无限运行的绝对上限，不是要求每个环境必须生成 128 条。

## 4. 怎么判断结果是否真的好

先打开 `summary.json`，但不要只看平均步数。建议按下面顺序检查。

### 数据

- `research_bundle.json` 中每条记录是否有真实来源。
- 数据是否覆盖多种实体与关系，而不是一个父节点下面挂大量同类叶子。
- 关系字段指向的 ID 是否确实存在。

### 工具

- `tool_validation.json` 中保留工具是否都通过执行。
- 最终工具是否对应真实工作流，而不是为了增加数量生成许多相似查询。

### 图与候选链

- `tool_graph.json` 中的边是否表示真实数据流。
- `walk_synthesis.json` 中执行成功且不重复的候选有多少。
- 长链是否来自真实关系和有意义的比较分支，而不是来回查询同一个实体。

### 最终任务

- 用户问题是否像一个人真的会提出的问题。
- 题目中点名的对象是否与 `reference_calls` 实际调用的对象一致。
- 每个调用是否对回答问题有贡献。
- 问题是否只是用分号把互不相关的子问题拼在一起。
- 请求和执行链唯一不代表业务目标不重复，还要检查是否反复比较同一组对象。
- `reference_plan_executed` 只能证明参考路径能运行，不能代替模型求解测试。

## 5. 什么时候运行 5-run

先完成普通运行并抽查任务。如果数据图很浅或题目本身不自然，直接运行 5-run 只会增加费用，不能修复环境。

确认任务值得验证后运行：

```powershell
python -m agent_world_mini `
  --theme-id openalex-publication-research `
  --slug openalex-verified `
  --verify-five-runs `
  --react-max-steps 12
```

每个任务最多有 5 次有效求解结果。至少 2 次通过才保留。临时断网或模型服务错误记录为 `inconclusive_infrastructure`，不会把整个环境静默丢弃。

## 6. 常见问题

### 最终没有任务

最常见的三种原因：

- 没有配置可用模型，因此语义任务生成没有执行。
- Research Agent 只找到了介绍性网页，没有可操作的结构化数据。
- 候选工具或候选链无法使用真实 ID 执行。

依次查看 `summary.json`、`research_bundle.json`、`tool_validation.json` 和 `walk_synthesis.json`，不要只看终端最后一行。

### 任务平均只有两三步

先看原始数据图。若数据只有“一个模型到很多文件”这样的星形结构，强行提高目标长度只会制造绕路。应该补充论文、作者、版本、提交、组织或其他真实关系，而不是调整成固定长链。

### 运行时间太长

优先减小 `--max-candidates` 做调试。不要长期使用 `--max-semantic-reviews` 或 `--max-tasks` 作为正式质量策略；这两个参数只适合快速确认代码能跑通。

### 能否直接用 Codex 子智能体替代 Research Agent

可以。让 Codex 子智能体生成 `research_bundle.json`，再通过 `--research-bundle` 交给 pipeline。Python CLI 不会自己启动 Codex 子智能体，具体操作见 `docs/CODEX_WORKFLOW_ZH.md`。
