# Agent-World 正式训练与评测

这套实验只回答一个核心问题：用真实公开数据构建的可执行工具环境，能否提升 Qwen3-8B 在未见环境中的多步工具调用能力，同时尽量保持原有通用能力。

## 数据到底怎么用

三批 Luna 数据共包含 120 个环境和 935 个任务。环境整体按 90/15/15 划成 train/dev/test，同一环境绝不会跨分区。

旧 5-run 留下了 1,873 条候选轨迹，但旧判定只要求命中部分参考实体，会把“找到一个相关记录但没有完成整题”也算成功。所有候选轨迹仍原样保存，不过正式 SFT 不把它们当标准答案。每个任务本身已有一条完整执行成功的参考链，因此正式 SFT 使用 935 条参考链：每个任务一条，工具结果来自真实环境，最终 JSON 合并同一实体的重复观察。训练分区 701 条参考链平均调用 6.97 次工具，中位数 6，最长 14；链长来自任务本身，没有人为指定目标长度。

| 分区 | 环境 | 任务/SFT 参考轨迹 | 旧 5-run 成功轨迹 | 用途 |
| --- | ---: | ---: | ---: | --- |
| train | 90 | 701 | 1,405 | SFT 与 GRPO |
| dev | 15 | 117 | 234 | 选 checkpoint、看训练是否跑偏 |
| test | 15 | 117 | 234 | 最终隐藏评测，绝不训练 |

四个环境没有任务。它们仍在 120 环境清单中，但没有可转换的训练样本。旧 5-run 候选轨迹按同一环境分区单独导出，只用于复核，不进入主训练；dev/test 参考轨迹只用于隐藏评测。

SFT 使用 Qwen3 原生多轮工具格式。模型学习 assistant 的工具调用与最终答案；system、user 和工具返回只作为上下文，不计算 loss。这样不会把数据库返回误教成“模型应该凭空生成的内容”。

通用回放来自 `allenai/tulu-3-sft-mixture` 的许可可追踪子集。配比按 assistant/function-call token 计算，而不是按样本条数：主设置为 70% Agent-World、30% 通用回放。通用回放中约五分之一附带无关工具但仍直接回答，用于训练“不该调用时不调用”。下载报告会记录具体来源和许可证；Tulu 总集为 ODC-BY-1.0，各子集条款仍需分别遵守。

## 正式实验矩阵

| 实验 | 训练数据 | 作用 |
| --- | --- | --- |
| Qwen3-8B 原模型 | 无 | 所有指标的共同基线 |
| LoRA SFT tool-only | 701 条完整参考工具轨迹 | 测量纯工具数据的增益和遗忘 |
| LoRA SFT mixed | 工具轨迹 + 30% 通用回放 | 主 LoRA 结果 |
| LoRA SFT + GRPO | 从 mixed LoRA 开始，701 个训练任务现场 rollout | 测量可执行奖励的额外收益 |
| Full SFT mixed | 与主 LoRA 相同的数据 | 判断 LoRA 容量是否限制效果 |

LoRA 使用 `r=16, alpha=32, dropout=0.05`，学习率 `5e-5`，2 epoch。全参 SFT 使用 BF16、ZeRO-3、学习率 `2e-6`，1 epoch。两者 effective batch size 都是 16，最大上下文 16K。这样 701 条长轨迹经过 packing 后仍有约二十到三十次 LoRA 参数更新，不会因为全局 batch 过大而只更新三四次。

GRPO 不模仿固定参考路径。每个任务生成 8 条独立轨迹，工具现场执行；701 个训练任务完整跑 1 epoch。奖励是“最终答案中各参考实体的实际字段覆盖率 × 工具证据中的实体覆盖率”，非法调用和执行失败自然拿不到证据；完全相同的重复调用最多扣 0.1。奖励不包含调用步数，所以不会为了七步、十步而无意义地绕路。

## 评测口径

内部主指标是在 15 个完全未见环境上的 pass@1；另报采样 5 次后的 pass@5，并按参考任务长度 1-3、4-6、7+ 分桶分析。它回答“模型能否解决我们生成的新环境”，但不能代替公开 benchmark。

公开主评测直接沿用 Agent-World 的三套工具 benchmark：BFCL V4、MCP-Mark 和 τ²-Bench。BFCL V4 正式运行 `all_scoring`，覆盖 Web Search、Memory、多轮、Non-live、Live、Relevance 和 Irrelevance；MCP-Mark 覆盖 Filesystem、GitHub、Notion、Playwright 和 Postgres；τ²-Bench 使用 Retail、Telecom、Airline 的 `base` split。正式比较采用 Agent-World 的采样设置：`temperature=1.0`、`top_p=1.0`、8 次独立运行。

同时加入 EnvFactory 使用的 MCP-Atlas 和 VitaBench。MCP-Atlas 按论文口径先筛出 291 题，报告 pass rate 与 mean coverage；VitaBench 运行 Delivery、In-store、OTA 各 100 题。Qwen3 属于 thinking 模型，这两套评测按 EnvFactory 设置使用 `temperature=0.7`；VitaBench 的模拟用户和滑窗评估器使用固定 DeepSeek 模型。

公开 benchmark 都直接调用官方 harness 和官方判分，不把 smoke test 当正式结果。当前 `one_run` 比较完整任务但只运行一次：BFCL 排除两个需要 SerpAPI 的 Web Search 类别；MCP-Mark 运行无需在线凭据的 Filesystem、Playwright、Postgres；MCP-Atlas 运行 291 题中完全不依赖 `mcp-atlas.env` 的 23 题。缺少凭据的类别明确记为未运行，不用少量样题代替。IFEval 和 MMLU-Pro 只用于观察通用能力保持情况，不作为工具能力主结果；GSM8K、MATH-500 不再进入默认正式流程。

### 运行公开工具评测

五套官方工具放在 `$AGENTWORLD_ROOT/benchmarks` 或独立 Python 环境中：

```bash
bash training/setup_tool_benchmarks.sh all
```

先在一张空闲 GPU 上对基座模型跑 smoke test：

```bash
CUDA_VISIBLE_DEVICES=5 BENCHMARK=mcpmark BENCHMARK_MODE=smoke \
  RESULT_TAG=base MODEL_PATH=$AGENTWORLD_ROOT/models/Qwen3-8B \
  bash training/serve_and_run_tool_benchmark.sh
```

各套 smoke test 都通过后，按“一个 benchmark 内依次比较五个模型”的顺序运行正式矩阵：

```bash
BENCHMARK_MODE=formal BENCHMARK_GPU=5 \
  bash training/run_tool_benchmark_matrix.sh
```

正式结果写入 `$AGENTWORLD_ROOT/results/official`，每完成一套 benchmark 都会更新其中的 `summary.md`。τ²-Bench 的固定用户模拟器和 MCP-Atlas 的固定 judge 从 `$AGENTWORLD_ROOT/secrets/eval.env` 读取；MCP-Atlas 的服务凭据单独放在 `$AGENTWORLD_ROOT/secrets/mcp-atlas.env`，密钥不进入仓库和命令行。

MCP-Mark 官方 harness 默认给每一轮预留 32K 输出，这会让 40K 上下文的 Qwen3 在几轮工具返回后无法继续。本项目只加了一项可配置的输出上限，正式值为单轮 8K，并使用 MCP-Mark 原生的 32K 历史压缩；任务、工具、最大轮数和官方 verifier 均保持不变。

## 目录

```text
training/
  prepare_data.py              环境级切分并导出 SFT/GRPO 数据
  fetch_general_replay.py      下载通用回放候选池
  build_mixed_sft.py           按 Qwen token 精确混合 70/30
  verl_agentworld_tool.py      veRL 调用本地可执行环境
  reward.py                    简单、可解释的 grounded reward
  evaluate_internal.py         未见环境正式评测
  configs/                     LoRA、全参 SFT 和 DeepSpeed 配置
  run_nightly.sh               顺序启动全部训练
  run_all_evals.sh             评测全部 checkpoint 并汇总
  run_tool_benchmark_matrix.sh 按 benchmark 依次比较五个模型
  summarize_official_tools.py  汇总五套官方工具评测
```

生成数据位于 `/data1/models/sunhenghui/agentworld-training/data/agentworld_120`，模型和输出分别位于 `/data1/models/sunhenghui/agentworld-training/models` 与 `/data1/models/sunhenghui/agentworld-training/outputs`。

## 在 170 上执行

仓库和已经转换的 Agent-World 数据上传后，先安装环境、下载 Qwen3-8B、下载通用回放并生成 parquet：

```bash
cd /data1/models/sunhenghui/agentworld-training/repo
bash training/setup_server.sh
bash training/setup_eval.sh
```

正式实验前运行一次真实 SFT 更新和一次真实 GRPO 更新，确认数据、工具、奖励、DeepSpeed、veRL 和显存可以一起工作。预检输出与正式输出隔离，不计入实验结果：

```bash
bash training/run_preflight.sh
```

正式实验（依次运行两组 LoRA、全参 SFT、GRPO 和全部评测）：

```bash
nohup bash training/run_nightly.sh > /data1/models/sunhenghui/agentworld-training/nightly.log 2>&1 &
```

只需要重跑正式评测时：

```bash
nohup bash training/run_all_evals.sh > /data1/models/sunhenghui/agentworld-training/evals.log 2>&1 &
```

结果总表会写到 `/data1/models/sunhenghui/agentworld-training/results/summary.md`，内部逐任务轨迹、lm-eval 原始输出和 BFCL 官方 CSV 都会保留，方便之后复核和写论文。

## 主要实现依据

- Qwen3 官方函数调用说明：https://qwen.readthedocs.io/en/stable/framework/function_call.html
- veRL 官方多轮 Agent Loop：https://verl.readthedocs.io/en/latest/advance/agent_loop.html
- Tulu 3 通用后训练数据与配方：https://allenai.org/tulu
- Berkeley Function Calling Leaderboard：https://gorilla.cs.berkeley.edu/leaderboard

这些来源决定的是训练协议和评测口径，不替代本项目自己的未见环境评测。预检目录、单批调试结果和数据构建期的 5-run 都不会混入论文中的正式模型结果。
