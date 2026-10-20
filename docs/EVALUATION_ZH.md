# 测评与独立验收

本页说明当前可复用流程。源码位于 `eval/`，职责见[测评代码](EVAL_CODE_ZH.md)，历史批次协议不因本页更新而改变。

## 新版 Direct verifier 与旧入口兼容

`eval.verifier.verify_execution()` 现在默认使用新版 `direct_codex_rubric`。
`from task_gen.task_eval_verifier import verify_execution`、
`python -m task_gen.task_eval_verifier`、`python -m eval verify` 和
`python -m eval verify-existing` 继续有效，原有证据参数无需改名。运行需要 Python 3.11+、
Codex CLI、自己的认证配置和可用的 bubblewrap；不满足运行条件会报 `VerificationError`。

原来的扁平 `config`（`codex_home`、`model`、`reasoning_effort`、`timeout_seconds`、
`attempts`、`runtime_paths`、`submission_preflight`、`orchestration_only` 等）继续传递给调查阶段。
也接受实验引擎的嵌套 `codex` 配置；同名扁平字段优先。
`timeout_seconds` 是每次调查调用的预算；评分标准设计、最终复查另有预算，整个流程可能更长。
可分别设置 `generation_call_timeout_seconds`、`final_review_timeout_seconds` 和
`application_timeout_seconds`，详见 `task_gen/test_verifier/direct_codex_rubric/`。

默认先从**题文、工具定义和初态**独立生成并复查评分标准，再冻结应用于被测执行。
设计输入中的被测回答和调用均为空、终态替换为初态，且不传参考解。
每层 rubric/subcriterion 必须声明必要性、满足/否决条件及精确原文引用。
调查同时输出固定标准评分与旧 API 的逐项事实判断；宿主不按总分阈值或分数推算通过与否。

返回值及 `<run_dir>/verdict.json` 保留原来的六个字段：
`task_id`、`requirements`、`non_blocking_issues`、`summary`、`failure_causes`、`outcome`。
`request.json`、`evidence/`、`scratch/investigation.md`、`logs/` 的位置不变。
完整分数及 rubric 证据另存 `direct_result.json`，生成和复查材料位于 `direct/`。
生成或调查故障抛出 `VerificationError`，不伪造业务 FAIL，也不悄悄换用另一种方法。

同一题重复验收时，可复用首次生成且未修改的评分标准，避免重复设计：

```python
verdict = verify_execution(
    run_dir=Path("/path/to/new-review"),
    config={"codex_home": "/path/to/.codex",
            "verifier_dir": "/path/to/first-review/direct/generation/verifier"},
    **evidence,
)
```

需保留该目录上一级的 `frozen_manifest.json` 和 `generation/result.json`，
入口会检查任务 ID、原文摘要、Direct 方法、递归字段结构及文件哈希。
CLI 对应 `--verifier-dir /path/to/verifier`。不同任务或已修改的包会被拒绝。
迁移原工具解释器时，可在 API config 中提供
`runtime={"binding_path": "/path/to/binding.json", "tools_path": "/path/to/tools.json"}`
及 `delivery_root`；工具实现会与证据逐项核对。未提供绑定时使用宿主运行时。

需要原始纯 Codex 基线时，显式设置 `config={"method": "codex", ...}`，
CLI 使用 `--method codex`；新方法也可显式指定 `--method direct_codex_rubric`。
已有 `frozen_requirements_v1` 验收清单模式默认继续走原始 Codex，保持冻结的要求和输出校验；
不能同时显式选择 Direct。已有实验方法的故障回退仍明确使用纯 Codex，避免递归生成。

## 1. 架构与验收口径

### 1.1 参与者与执行流程

| 参与者 | 工作 | 不代表什么 |
| --- | --- | --- |
| 求解 Agent | 从干净初态读题，选择并调用公开工具，交付回答与状态 | 进程成功退出不代表完成任务 |
| Harness / MCP | 提供工具契约、隔离状态、执行与原始记录 | 工具 success 不代表业务成果正确 |
| Verifier Agent | 调查初末状态、回答、调用及实现，按题文逐项验收 | 不按参考答案相似度判分，不替模型补交成果 |

主链：任务集合 → 冻结选择与协议 → 干净初态 → Kimi Code 求解 → 保留终态与轨迹 → 独立 Verifier → 汇总。
参考解可以缺省，也可能出错；题文是验收要求的来源。方法不同但满足要求应通过。
参考列表可重放只证明执行可行，不保证参考答案正确或任务质量合格。

### 1.2 证据与目录

```text
output-root/<timestamp>/
  selection.json
  summary.json
  <case>/
    <model-workspace>/
    private-state/
    trajectory.json
    raw/
  verifiers/<case>/
    request.json
    prompt.txt
    evidence/
      initial/
      actual/final/
      actual/answer.txt
      actual/tool_calls.jsonl
      actual/execution.json
      environment.json
      tools.json
    scratch/investigation.md
    logs/
    verdict.json
```

目录是按职责展示，不是所有运行都有完整产物。中断任务可能只有 raw 或部分终态。
`raw` 中保存模型请求、响应/事件、业务工具调用和原生工具审计；具体文件名以该版本导出器为准。
`trajectory.json` 适合查看，调查丢事件、重复调用和协议时仍要对照 raw，不能用修订后的展示轨迹覆盖原件。
不传 `--delete-task-state` 时保留运行工作区；传入后会删除模型工作区和私有状态，须先确认所需现场已完整导出。

### 1.3 判分和失败分类

`verdict.json` 只有 `pass` / `fail`，逐项列出原文要求、证据、事实与原因；不能正常完成调查属于 `verification_error`，不是一份 FAIL verdict。
Verifier 在独立证据视图中调查；证据只读，运行目录及临时认证目录可写。隔离细节见下节。

| 情况 | 记录与处理 |
| --- | --- |
| 已验收 PASS / FAIL | 正式内容判分，失败仍保留原因和证据 |
| 达到工具预算 | 任务失败，和 verifier FAIL 分列 |
| cancelled / 执行异常 | 保留实际中断；预先约定计失败或单列，不能混称内容错误 |
| Verifier 超时、格式错误、启动失败 | verification_error，保留未决和重试记录 |
| 尚未完成、完成未验收、尚未派发 | 未决，不用已完成小样本宣告整批达标 |
| 生成失败、参考错误、环境不可用 | 任务/环境质量问题，单独记录，不能悄悄换题缩分母 |

### 1.4 Verifier 的隔离边界

求解结束后启动的是独立调查 Agent，不是让业务工具继续求解。
它可执行调查命令，因此业务 MCP 的权限不能替代对审核进程本身的限制。
当前实现以 bubblewrap 只读挂载证据、限制可见文件；Codex 自身的内层沙箱在此路径被关闭，
由项目提供这层外部隔离，并非在同一工具调用上重复套壳。

这是当前隔离实现的依赖，不是判分算法固有要求；替换时需要保留证据只读等边界。
实际挂载、可写范围和通用工具沙箱的区别见[运行时与隔离](TOOL_RUNTIME_ZH.md)。

## 2. 使用前固定协议

运行前记录：任务选择及版本、题文/工具/binding 指纹、初态、源码版本、Kimi CLI 版本、
上游真实模型 ID、服务地址、权重来源、provider、推理强度、上下文/输出预算、读取上限、
压缩阈值、共享工具预算、模型/Verifier 并发、Verifier 模型及验收配置、重试和中断统计策略。
记录地址与权重身份，不记录 key。

双模型比较必须使用同题文、工具、初态和相同业务预算，分别建立状态和输出目录。
任务生成或作者校验的提示词、参考答案、私有反馈和 Verifier 证据不进入盲解工作区。
不能把模型别名相同当作权重相同，不能把不同协议的两次得分直接合并。

## 3. 运行与查看结果

以下操作沿用前述架构和协议；源码默认参数集中见[配置参考](CONFIGURATION_ZH.md)。

### 3.1 批量运行

先完成[一题试跑](QUICKSTART_ZH.md)，再扩展。以下示例只展示规模参数，其余模型/上下文参数沿用已确认的完整命令：

```bash
python -m eval run \
  --input-root /path/to/taskgen-inputs \
  --output-root /path/to/new-evaluation \
  --model-id your-upstream-model-id --provider-type openai_responses \
  --reasoning-effort high --max-context-size 262144 --max-output-size 32768 \
  --max-tool-calls 85 --mcp-server-name workspace \
  --read-default-max-chars 80000 --read-max-chars 128000 \
  --compaction-trigger-ratio 0.8 \
  --max-concurrency 20 --verifier-concurrency 20 \
  --verify-after-each --verifier-model gpt-6.1-sol \
  --verifier-reasoning-effort high
```

本命令从 `KIMI_BASE_URL`、`KIMI_API_KEY` 和 `KIMI_CODE_BIN` 取服务配置。
`--max-concurrency 20` 是上限，不保证始终有20条任务同时推理。状态复制、I/O、Docker 会话和待测数量都可能限制实际并行。
启用自适应调度时再加 `--adaptive-concurrency --min-concurrency 1 --initial-concurrency 20`；
不要漏掉 min 参数，源码默认25与此处上限20不兼容。

原生文件工具与业务 MCP 共同计入 `--max-tool-calls`。一次 step 可以有多次工具调用，step 数不是调用数。
当前源码默认65、硬上限85；历史65/75次冻结快照不会自动升级。不能靠只改外层 manifest 提高旧执行器的硬上限。

### 3.2 选择、续跑与工具绑定

- `--environment-id`、`--task-id`：缩小范围。
- `--sample-size N --sample-seed SEED`：可重复采样；保留生成的 `selection.json`。
- `--select-summary /path/to/summary.json`：按给定摘要的 case 顺序选择，适合另一模型对照。
- `--skip-summary`：跳过旧摘要中的 completed；不等价于跳过已 PASS。
- `--skip-processed-summary`：跳过旧摘要中记录过的所有 case，包括失败。
- `--skip-selection`：跳过旧批次所有已派发 case；恢复中断前先确认是否需要重跑它们。
- `--runtime-backend` 可重复：只选有对应后端能力的任务；被过滤的环境不计为内容失败。
- `--delivery-root` 会改用指定根目录的交付工具，必须重新记录绑定和版本，不能静默混入旧协议得分。

新一轮、不同模型和授权重试使用新输出目录。不要用清理脚本删掉失败记录来实现“恢复”。

### 3.3 已有现场重新验收

当前有两种 JSON，不能互换：

| 输入 | 结构 | 命令 |
| --- | --- | --- |
| API 输入 JSON | `task`、`environment` 对象，`initial_state`、`actual_state` 路径，`calls`、`answer`、`execution` | `python -m eval verify` |
| 已准备证据索引 | `task_id`、`task_text`，`environment`、`tools`、`initial`、`actual` 等相对路径 | `python -m eval verify-existing` |

路径不靠目录名猜测。`verify-existing` 读取索引的实际执行证据，在新目录准备验收现场；默认不导入可选参考解，
不重跑求解器、不补结果、不修改原现场。冻结的 `verification_profile` 与验收清单会保留。

```bash
python -m eval verify-existing \
  --request /path/to/exported-case/request.json \
  --output /path/to/new-verification --prepare-only

python -m eval verify-existing \
  --request /path/to/exported-case/request.json \
  --output /path/to/another-new-verification \
  --codex-home /path/to/codex-home \
  --model gpt-6.1-sol --reasoning-effort high --timeout 1800 --attempts 3
```

`--prepare-only` 不产生模型费用、不输出 PASS/FAIL；正式验收才调用模型。
新目录必须不存在，并且位于证据目录外。证据可能使用同盘硬链接，始终视为只读，不能手动编辑。
部分科学任务的冻结协议要求审核者也仅做简单计算，须使用 `--orchestration-only`；
默认通用 Verifier 允许独立库复验，这两种配置不是同一个协议。`--submission-preflight` 是提交格式自检，不是放宽业务标准。
中断现场可用于诊断；验收 Agent 对部分状态的调查不能把已取消执行自动改成正式成功。

### 3.4 汇总准确率

```bash
python -m eval report \
  --summary /path/to/run/summary.json --expected 20 \
  --interruption-policy fail --output /path/to/new-report.json
```

此处 `fail` 对应将 cancelled/执行失败计失败的协议。报告同时给出固定分母通过比例、已判通过率和未决数，
工具预算失败始终计失败。不同数据结构的历史协调器仍用原冻结统计程序，不猜测转换。

### 3.5 导出轨迹可视化

```bash
python -m eval visualize --trajectory /path/to/trajectory.json --output /path/to/one.html
python -m eval visualize --run-directory /path/to/run --combined --output /path/to/all.html
```

静态 HTML 直接用浏览器打开，不需要启动 Web 服务。页面不替代环境终态；分析文件交付或状态修改须一起查看证据。
ReAct/旧 Kimi SDK 仍可从 `python -m task_gen.task_eval --help` 运行，但它与本页官方 CLI 的上下文、工具暴露和循环可能不同，成绩须注明协议。
