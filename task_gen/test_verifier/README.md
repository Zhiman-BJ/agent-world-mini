# Verifier 四种方法

| 方法 | 独立目录 | 生成与应用 |
|---|---|---|
| Program | `program_verifier/` | 从任务生成主程序、rubric 函数和必要模型调用，再运行程序 |
| Codex + 静态工具 | `codex_tool_verifier/` | 从任务生成 rubric 和工具，再由主 Codex 调用工具并评分 |
| Codex + rubric（工具框架） | `codex_rubric_verifier/` | 从任务独立生成 rubric，再由工具框架的主 Codex 自主检查；不生成静态工具 |
| Codex + rubric（原始调查框架） | `direct_codex_rubric/` | 从任务独立生成 rubric，再由原始 Codex 调查流程检查并评分 |

后两种方法分别发起自己的生成会话，没有源 verifier 参数，不读取工具版包。共享代码只负责接口校验、运行、计量和报告，不共享生成结果。生成沙箱隐藏其他实验目录，仅开放当前任务证据与本次工作目录。生成会话内允许自测修正；交付后冻结，正式检查不修改包，不自动重生成。

四种方法现在默认均在冻结前增加独立 Codex 复查会话：核对原任务与评分条件、修改当前包、执行自测和本方法应用端真实试跑，宿主再独立复验。复查/实测出错会在预算内交回该会话修复，不用回退掩盖错误；最终失败记生成失败，不冻结。零分或 fail 本身不代表验收失败，不能为了满分放宽标准。复查日志、修改前备份和全部调用计量保留在 `generation/`，计入生成成本。历史实验不补做复查，新实验须用新目录。

新应用的三种 Codex 主验收方法同时返回 `outcome`（`pass` / `fail`）、`summary` 和逐项 `rubrics` 分数；宿主按冻结权重生成 `score`（0–1），不按总分阈值推导判定。核心未完成或实质错误判 fail，非阻断性缺陷可为 pass，须解释依据。缺少整体判定或有效摘要属于验证输出错误，沿用整任务回退；原始纯 Codex 回退仍只有判定，数值分数记 null。历史结果不补写判定、不修改评分标准。

旧的两组 rubric 实验及其 smoke 产物已删除，不能再引用其结果作为独立方法比较。`codex_tool_verifier` 中历史 `--without-static-tools` 仅是固定包的工具开关，不是新的独立 rubric 方法。

新实验：同一 `runs/random20_20261015/task_ids.tsv` 的20个任务，每种方法独立生成20份 verifier，每份应用两次；模型 `gpt-6.1-sol` / `high`。任务已有运行结果作为候选，生成失败、正常低分和整任务回退分别记录。费用若 API 未返回金额且无确认价格，显示 unknown，保留完整 usage，不填零。

新代码、配置、提示和新运行产物分别放在各自目录。公共 `runs/` 保留原始抽样及 program/工具版历史记录；公共批处理和计量模块不属于某一生成包。

<details><summary>历史实验说明（非本次独立 rubric 实验）</summary>

# 手写 verifier 试验

- `manual_npz_verifier.py`：S59 verifier。
- `test_manual_npz_verifier.py`：关键测试。
- `manual_s59/`：固定标准、模型配置、输入样本、真实运行结果及详细说明。
- `verifier_two_task_data_inventory.json`：S59/S66 数据盘点。

在仓库根目录运行：

```bash
python -m pytest task_gen/test_verifier/test_manual_npz_verifier.py -q
python -m task_gen.test_verifier.manual_npz_verifier --help
```

历史模型调用日志保留原始内容。目录迁移只更新代码引用、说明和标准数组路径。原件保护范围仍待按讨论收窄至指定的18份数组。
# 最终修复与 20 任务重复应用

两种方法默认在生成尾部增加一个 Codex 会话，直接修复任务生成包、测试，再交付。
完整试跑和修复均计入生成成本。冻结后对同一份证据独立应用两次：

```bash
.venv/bin/python -m task_gen.test_verifier.final_review_experiment \
  --config task_gen/test_verifier/final_review_run_config.json \
  --output task_gen/test_verifier/runs/final_review_20x2_20261016 \
  --concurrency 4
```

默认使用 `runs/random20_20261015/task_ids.tsv` 的 20 任务、6.1sol/high、两种方法。
新增目录保留全部 API 请求计量、生成与最终修复、两次应用结果及 `RESULTS.html`。
同目录重启会复用已完成结果并核对冻结包；未完成的应用留档报错，不静默覆盖。
费用无供应方确认价格时显示 unknown，原始 API usage 和返回的费用字段保留。

本轮报告：[全部指标](runs/final_review_20x2_20261016/RESULTS.html)、[问题核实](runs/final_review_20x2_20261016/ISSUES.html)。问题页可展开任务原文和被验证回答；完整依据与基础设施恢复记录见同目录 `ANALYSIS.md`。

已完成两种方法各 20 任务：Program 生成成功 19/20，工具版 18/20，37 份冻结包各应用两次，共 74 次。原生逐项重复一致分别为 16/17、12/14；回退单列。155 项宿主测试通过；最终哈希与计量核对见报告目录 `audit.json`。

</details>

### 2026-10-19 原环境与宿主复验对齐

四版共享原任务工具/软件 profile 入口；新证据包保存相对运行绑定和回退路径。
宿主可用 `delivery_root` 指定原交付环境安装根；生成包无需保存该机器路径。
Program SDK 提供 `call_environment_tool` / `run_environment_code`；生成静态工具提供 `context.call_environment_tool`；Codex 使用 scratch 内的 `environment_tool` CLI。
宿主复验失败会在本阶段剩余预算内反馈 Codex 修复同一目录，每轮单独留档；应用故障仍整题回退。
验证和限制见 [本轮报告](runs/pipeline_alignment_20261019/REPORT.md)。
