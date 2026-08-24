# Agent-World Mini

一个小型、可检查的 Agent-World 环境生成复现项目。

它从一个主题或 MCP 目录项出发，搜索真实公开数据，根据数据生成可调用工具，再从工具之间真实可执行的数据依赖中生成任务。项目当前仍是研究原型，重点是把每一步留下来，方便检查数据从哪里来、工具为什么存在、任务能不能真正执行。

## 先看懂三种运行方式

这个仓库有三种 Research Agent 使用方式：

1. **内置 Research Agent**：Python pipeline 自己完成网络研究，再继续生成工具、图、任务和可选的 5-run。
2. **外部 Research Agent**：Codex 或 Luna 子智能体只负责网络研究并写出 `research_bundle.json`，Python pipeline 通过 `--research-bundle` 接过这个文件，继续完成后半段。
3. **DeepSeek Harness Research Agent**：Python CLI 自动调用本机 `dsh` 完成同一个研究文件，再直接继续后半段。

三种方式共用同一套工具生成、执行验证、图采样和任务生成代码，区别只在 Research Agent。需要让 Codex 深入研究几个工业主题时，阅读 [Codex Research Agent 用法](docs/CODEX_WORKFLOW_ZH.md)，并把 [Codex 执行提示词](CODEX_PROMPT.md)交给 Codex。模型训练、GRPO 和正式评测见 [训练与评测说明](training/README_ZH.md)。

后半段的工具筛选和任务语义审核也可以交给 Luna 子智能体，不再调用配置的 DeepSeek API。Python 仍负责生成工具、构图、执行候选链和参考答案，Luna 只从已有工具和真实执行候选中做选择。

## Pipeline 做了什么

```text
本地环境目录
  -> 选择尚未运行的主题
  -> Research Agent 搜索并提取真实公开数据
  -> 根据数据生成和筛选工具
  -> 实际执行每个工具
  -> 构建经过真实值检查的工具依赖图
  -> 沿连通关系生成候选任务
  -> 语义审查并去重
  -> 可选：让求解模型独立运行 5 次
```

这里没有预先规定任务必须有几步。一个环境能产生多长的任务，取决于数据里是否真的存在可连接的关系。Research Agent 也不会因为记录数增长就一直抓取同一种叶子数据；后续扩展必须改善实体多样性、关系覆盖或可达深度。

## 安装

需要 Python 3.10 或更高版本。项目运行代码只使用 Python 标准库。

```powershell
git clone https://github.com/shelter951/agent-world-mini.git
cd agent-world-mini
python -m pip install -e .
Copy-Item config/api_keys.env.example config/api_keys.env
```

在 `config/api_keys.env` 中填写模型配置：

```dotenv
LLM_API_KEY=your-key
LLM_MODEL=deepseek/deepseek-v4-flash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_TIMEOUT_SECONDS=90
```

`config/api_keys.env` 已被 Git 忽略，不会正常进入提交。系统环境变量优先于本地文件；不要把真实密钥写进 README、命令示例或源码。

模型服务只要兼容 OpenAI Chat Completions 即可。设置 `LLM_STREAM=true` 后，普通文本和 JSON 调用都会通过 SSE 流式读取；完整配置见 [中文使用手册](docs/USAGE_ZH.md)。内置网络研究仍使用 OpenRouter 专属搜索工具，改用其他服务时应使用独立的 Codex 或其他 Research Agent。

使用 DeepSeek Harness 时，再安装官方 CLI，并在同一个 `config/api_keys.env` 中填写 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`：

```powershell
npm install -g @deepseek-ai/dsh
```

`DEEPSEEK_BASE_URL` 填 OpenAI-compatible API 的 `/v1` 基址，不要带 `/chat/completions`。

## 最快跑一次

先使用一个内置的结构化数据主题：

```powershell
python -m agent_world_mini --theme-id openalex-publication-research --slug openalex-demo
```

结果会写入 `runs/openalex-demo/`。没有配置模型密钥时，内置适配器仍能收集数据、生成工具和执行候选链，但不会把机械模板当成最终训练任务，因此 `tasks.json` 中不会有语义审查后的任务。

使用一个自然语言主题：

```powershell
python -m agent_world_mini `
  --theme "研究公开的城市共享单车运营数据" `
  --slug city-bikes
```

自然语言主题依赖启用了网页搜索和抓取能力的模型配置。

## 从本地目录批量运行

仓库包含预处理后的环境目录 `agent_world_mini/seed_gen/prepared_environments.json`。批量运行只读这个本地文件，不会在每次启动时重新访问 MCP 网站。

先只看会选中什么，不开始研究：

```powershell
python -m agent_world_mini --batch-size 3 --selection-seed 42 --dry-run
```

正式构建 3 个此前没有成功产出任务的环境：

```powershell
python -m agent_world_mini --batch-size 3 --selection-seed 42
```

选择和运行结果记录在 `runs/catalog_batch.json`。pipeline 会跳过已经成功完成的环境；如果某个环境没有足够真实数据或构建失败，会继续尝试候选目录中的下一个环境。

## 用 Codex 子智能体替换 Research Agent

第一步，在 Codex 中打开仓库，把 [CODEX_PROMPT.md](CODEX_PROMPT.md) 交给主智能体并填入要研究的主题。子智能体只负责生成：

```text
runs/codex-<slug>/research_bundle.json
```

第二步，让现有 pipeline 从这个文件继续：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-genomics/research_bundle.json `
  --slug codex-genomics
```

这条命令会跳过内置 Web Research Agent，从工具生成开始执行后续步骤。若要同时做 5-run，在末尾加 `--verify-five-runs`。

这里不是让 Codex 手写 `tool_specs.json`、`tool_graph.json` 或 `tasks.json`；这些仍由 pipeline 统一生成。

## 用 Luna 接管后半段审核

先让 Python 完成工具执行验证和候选链执行，并导出 Luna 交接文件：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-genomics/research_bundle.json `
  --output-root runs `
  --slug codex-genomics `
  --luna-review-export
```

然后让 Luna 子智能体读取 `runs/codex-genomics/luna_review_packet.json`，按文件中的格式写出 `runs/codex-genomics/luna_reviews.json`。最后导入审核结果：

```powershell
python -m agent_world_mini `
  --output-root runs `
  --slug codex-genomics `
  --luna-reviews runs/codex-genomics/luna_reviews.json
```

一次 Luna 审核同时选择可用工具并审查任务。Luna 不能增加工具、修改参数或编造调用链；导入时 Python 会按原候选重新执行，并据此生成 `tasks.json` 和参考答案。

## 用 DeepSeek Harness 自动执行 Research Agent

从准备好的本地目录选择一个尚未运行的环境，并自动完成研究和后半段：

```powershell
python -m agent_world_mini --batch-size 1 --deepseek-harness
```

也可以对人工指定的主题运行：

```powershell
python -m agent_world_mini `
  --theme "公开药品监管与不良事件数据" `
  --source-url "https://example.com/starting-page" `
  --deepseek-harness `
  --slug deepseek-pharma
```

Harness 只写 `research_bundle.json`。工具、图、链和任务仍由 Python pipeline 生成。当前配置使用 Harness 的 PowerShell 工具访问真实网页和公开 API。

## 可选的 5-run

```powershell
python -m agent_world_mini `
  --theme-id openalex-publication-research `
  --slug openalex-verified `
  --verify-five-runs
```

5-run 会让求解模型独立尝试每个任务 5 次。当前规则是至少 2 次给出有依据的正确结果才保留。网络或模型服务故障会记为基础设施问题，不会伪装成模型答错。

这一步消耗最多 API 调用，建议先检查普通运行的数据和任务，再决定是否执行。

Luna 文件审核目前不替代 5-run。5-run 是模型在看不到参考链的情况下现场调用工具解题，需要逐步交互；把静态审核伪装成 5-run 会使验证失真。

## 输出文件

每个环境位于 `runs/<slug>/`：

| 文件 | 内容 |
| --- | --- |
| `research_bundle.json` | 真实结构化数据、可选文件资源、本地状态蓝图、来源 URL 和研究过程 |
| `theme_registry.json` | 使用的主题目录项或来源页面 |
| `environment_manifest.json` | 环境状态、对智能体可见的内容和重置方式 |
| `tool_specs.json` | 最终工具定义 |
| `tool_validation.json` | 每个候选工具的实际执行结果 |
| `tool_graph.json` | 工具依赖边和严格路径 |
| `walk_synthesis.json` | 原始游走、执行、去重和语义审查记录 |
| `luna_review_packet.json` | 等待 Luna 选择工具和审查真实候选的交接文件 |
| `luna_review_result.json` | Luna 审核导入后的接受与拒绝统计 |
| `tasks.json` | 最终任务与隐藏参考调用链 |
| `summary.json` | 本次运行的简要统计 |

`runs/` 和 `tmp/` 是实验产物，默认不提交到 Git。

## 常用参数

```text
--complexify-rounds N       Generic Research Agent 的扩展轮数，默认 2
--max-candidates N          每个环境的候选任务绝对上限，默认 128
--max-semantic-reviews N    仅调试用；0 表示审查全部已执行候选
--max-tasks N               仅调试用；0 表示不设保留数量上限
--react-max-steps N         5-run 中单次求解的工具调用预算
--output-root PATH          输出根目录，默认 runs
--research-bundle PATH      读取 Codex 产出的研究数据并跳过内置网络研究
--luna-review-export        不调用后端模型，导出 Luna 审核交接文件
--luna-reviews PATH         导入 Luna 审核结果并重放生成最终任务
```

完整说明见 [中文使用手册](docs/USAGE_ZH.md)，代码模块和数据流见 [系统架构说明](docs/SYSTEM_ARCHITECTURE_ZH.md)，目标数据边界见 [环境契约](docs/环境契约界定-v1.0.md) 和 [工具契约](docs/工具契约补充界定-v1.0.md)。设计取舍见 [工具设计与筛选](TOOL_DESIGN_AND_FILTERING.md)，实验记录和已知问题见 [实验记录](EXPERIMENTS.md)。

## 测试

```powershell
python -m unittest discover -s tests -q
```

## 当前边界

- 这是 Agent-World 思路的紧凑复现，不是原论文代码的逐行重实现。
- Generic Research Agent 的效果取决于模型的搜索能力和公开数据质量。
- “参考调用链能执行”只说明环境内部答案存在，不等于任意模型都能完成任务。
- Codex 子智能体通过 `research_bundle.json` 接入 Research Agent 阶段；子智能体调度仍发生在 Codex 会话中，不是 Python CLI 自动创建的。
- DeepSeek Harness 可以由 `--deepseek-harness` 自动启动，但必须先安装官方 `dsh` 并配置可用的模型接口。
