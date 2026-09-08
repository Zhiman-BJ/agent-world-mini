# Program-form 任务生成管线

本目录按 OmniaBench `TaskGen-Program-En` 的 Runner 步骤重建。唯一不同的是
Step 1：本项目不让模型重新生成 `init_config`，而是冻结 DataGen 已生成并校验的
真实 `state/`。Step 2 到 Step 12 的职责、顺序、主要字段和检查点与 OmniaBench
对齐。

## 1. 源码结构

```text
program_form/
├── steps/
│   ├── step1_prepare_environment.py
│   ├── step2_gen_task_solution.py
│   ├── step3_debug_solution_jsonl.py
│   ├── step4_ground_truth_jsonl_in_jsonl_out.py
│   ├── step5_verifier_code_jsonl_in_jsonl_out.py
│   ├── step6_debug_verifier_jsonl_in_jsonl_out.py
│   ├── step7_consistency_jsonl_in_jsonl_out.py
│   ├── step8_filter_rewrite_jsonl_in_jsonl_out.py
│   ├── step9_filter_trace_state_jsonl_in_jsonl_out.py
│   ├── step10_gen_task_rubric_jsonl_in_jsonl_out.py
│   ├── step11_difficulty_eval_jsonl_in_jsonl_out.py
│   └── step12_final_output.py
├── utils/
│   ├── contracts.py
│   ├── environment.py
│   ├── io.py
│   ├── reference_program.py
│   ├── solver.py
│   ├── solver_mcp.py
│   ├── tool_runtime.py
│   └── verifier.py
├── schemas/candidate.schema.json
└── run_pipeline.py
```

Prompt、业务处理顺序、接受条件和输出组装都在对应 `step*.py` 中。`utils/`
只保留多个步骤共同使用的环境加载、JSONL、执行器和状态隔离能力。

## 2. 输入环境

```text
<environment_package>/
├── environment.json
├── validation.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/<scope_id>/...
└── tools.json
```

- `environment.json` 符合环境契约 v2.0。
- `validation.json.valid` 必须为 `true`。
- `state/` 是 DataGen 生成的权威初始状态。
- 工具包含 `name/description/inputSchema/outputSchema/internal.code`。
- 生成 Agent 和求解 Agent 看不到 `internal.code`。

## 3. 步骤总览

```text
Step 1   冻结已有环境状态                          本项目适配
Step 2   生成 task + output_schema + solution_code   对齐 OmniaBench
Step 3   调试 solution_code                          对齐 OmniaBench
Step 4   干净重放并固化 Ground Truth                 对齐 OmniaBench
Step 5   生成答案 Verifier                           对齐 OmniaBench
Step 6   调试 Verifier                               对齐 OmniaBench
Step 7   多个独立 Agent 一致性检查                   对齐 OmniaBench
Step 8   稳定性过滤并改写公开任务                    对齐 OmniaBench
Step 9   任务、轨迹和状态语义一致性检查              对齐 OmniaBench
Step 10  生成 Rubric                                 对齐 OmniaBench
Step 11  多次求解并评估难度/pass@k                   对齐 OmniaBench
Step 12  最终字段投影和发布                          对齐 OmniaBench Runner
```

### Step 1：冻结已有环境状态

代码：`steps/step1_prepare_environment.py`

这是唯一与 OmniaBench 不同的步骤。它执行：

1. 校验环境声明、DataGen 回执、状态目录和工具 Schema。
2. 将环境、工具和 `state/` 复制到 `baseline_environment/`。
3. 计算初始逻辑状态摘要。
4. 写出后续步骤使用的 `step1_environment.json`。

这里没有 `init_config`。后续的 `environment_package` 和最终任务中的
`initial_state` 取代内联 `init_config`。

### Step 2：生成任务和参考解

代码：`steps/step2_gen_task_solution.py`

输入：`step1_environment.json`。

模型获得公开环境、完整工具输入输出 Schema，以及只读的初始状态副本。每个候选
联合生成：

```text
task_internal
output_schema
solution_code
```

本步骤执行一次真实 preflight，检查候选 Schema、正文泄露、输出 Schema、工具
输入输出、工具调用数量和状态变化。失败原因传入下一轮；只有成功候选进入 Step 3。

输出：`step2_task_solution.json`，生成轮次保存在 `step2_rounds/`。

### Step 3：调试参考程序

代码：`steps/step3_debug_solution_jsonl.py`

输入：`step2_task_solution.json`。

1. 从 Step 1 干净基线执行原始 `solution_code`。
2. 成功时不调用修复模型。
3. 失败时把任务、完整工具契约、当前代码和真实错误交给修复 Agent。
4. 每轮修复后从相同基线重新执行。
5. 保存最终代码、调试历史和工具轨迹。
6. 不根据错误代码反向改写任务目标。

新增字段包括 `solution_code_fixed`、`solution_debug_success`、
`solution_debug_history`、`solution_execution_trajectory` 和 `solution_trace`。

输出：`step3_debug_solution.jsonl` 和 `step3_debug_solution.json`。

### Step 4：固化 Ground Truth

代码：`steps/step4_ground_truth_jsonl_in_jsonl_out.py`

输入：`step3_debug_solution.jsonl`。本步骤不调用模型，也不复用 Step 3 的环境实例。
它从 Step 1 基线重新运行 `solution_code_fixed`，成功后保存：

```text
ground_truth.candidate_answer
ground_truth.init_state
ground_truth.final_state
ground_truth.state_diff
solution_trace
post_solution_state_snapshot
```

输出：`step4_ground_truth.jsonl`。

### Step 5：生成答案 Verifier

代码：`steps/step5_verifier_code_jsonl_in_jsonl_out.py`

输入：`step4_ground_truth.jsonl`。模型生成：

```python
verify(candidate_answer, ground_truth_answer) -> float
```

Verifier 只比较结构化答案。标准答案正例必须为 `1.0`；缺字段、多字段、错值、
错类型均不能为 `1.0`。模型连续失败时使用经过同样自测的确定性逐字段回退实现。

输出：`step5_verifier_code.jsonl`，新增 `verifier_code`。

### Step 6：调试 Verifier

代码：`steps/step6_debug_verifier_jsonl_in_jsonl_out.py`

输入：`step5_verifier_code.jsonl`。它独立重跑全部正负例。原代码通过时不调用模型；
失败时才进行“修复 -> 全套重测”，且不能降低标准。

输出：`step6_debug_verifier.jsonl`，新增 `verifier_debug_success` 和
`verifier_debug_history`。

### Step 7：独立 Agent 一致性检查

代码：`steps/step7_consistency_jsonl_in_jsonl_out.py`

输入：`step6_debug_verifier.jsonl`。本步骤不运行参考程序。默认启动 5 个全新的
求解 Agent；每个 Agent 只看到任务、输出 Schema 和公开工具，并通过临时 MCP
从 Step 1 干净基线调用真实工具。

只有答案 Verifier 为 `1.0` 且最终逻辑状态与 Ground Truth 相同才算成功。

输出：`step7_consistency.jsonl`，新增：

```text
multi_exec_results
consistency_pass_count
consistency_pass_rate
total_runs
consistency_threshold
consistency_keep
```

### Step 8：过滤并改写公开任务

代码：`steps/step8_filter_rewrite_jsonl_in_jsonl_out.py`

输入：`step7_consistency.jsonl`。先要求 Step 3、4、6 成功且 Step 7
`consistency_keep=true`，再只对保留项调用模型生成 `task_public`。改写保留业务
目标和约束，但隐藏工具名、内部字段、调用顺序和参考答案。

输出：`step8_filter_rewrite.jsonl` 和 `step8_filter_rewrite_kept.jsonl`。

### Step 9：任务、轨迹、状态一致性检查

代码：`steps/step9_filter_trace_state_jsonl_in_jsonl_out.py`

输入：`step8_filter_rewrite_kept.jsonl`。默认运行 3 个独立 Judge，检查公开任务
是否能解释参考工具轨迹和状态变化，以及参考执行是否做了额外业务动作。与
OmniaBench 当前实现一致，只有所有 Judge 都否决时才过滤；分歧票会保留并设置
`step9_review_required=true`，供后续复核。

输出：`step9_filter_trace_state.jsonl` 和 `step9_filter_trace_state_kept.jsonl`。

### Step 10：生成 Rubric

代码：`steps/step10_gen_task_rubric_jsonl_in_jsonl_out.py`

输入：`step9_filter_trace_state_kept.jsonl`。模型根据公开任务、输出 Schema、
初始/最终状态、状态差异和参考工具序列生成 `general` 与 `task_specific` 两类评分项。
模型文件严格使用 OmniaBench 的四字段格式：`rubric_count`、`total_score`、
`rubrics_text`、`explanation`；代码再解析评分行，并确定性检查 ID、1/2/3 分值和
两类总分。默认总分为 14，其中 general 为 6。

输出：`step10_rubric.jsonl`，新增 `rubric_items/rubric_count/rubric_total_score`。

### Step 11：难度与 pass@k

代码：`steps/step11_difficulty_eval_jsonl_in_jsonl_out.py`

输入：`step10_rubric.jsonl`。再次运行多个全新求解 Agent。一次运行必须同时满足：

```text
Verifier score == 1.0
State verifier score == 1.0
Rubric judge score == 1.0
```

Rubric 总分由代码根据逐项 `earned` 重算，不能相信模型自行声明的总分。

输出：`step11_difficulty.jsonl`，新增 `difficulty_eval_results_3`、
`difficulty_pass_count_3`、`empirical_pass_rate_3`、`pass_at_k` 和
`difficulty_bucket`。

### Step 12：最终发布

代码：`steps/step12_final_output.py`

输入：`step11_difficulty.jsonl`。本步骤不调用模型、不重新评分，只投影最终字段：

```text
env_id / environment_summary / task_id / task / output_schema
candidate_tools / initial_state / environment_package / rubrics
ground_truth_answer / verifier_code / additional_information
```

`initial_state + environment_package` 是本项目对 OmniaBench `init_config` 的唯一替代。

输出：`final/task_gen_final_english.json`。

## 4. 运行

```bash
cd /home/sunshuo/AgenticDataGeneration/agent-world-mini

python -m task_gen.program_form \
  --step all \
  --environment-package /path/to/environment_package \
  --output-dir /tmp/program_tasks \
  --model gpt-5.6-terra \
  --task-count 2 \
  --min-tool-calls 6 \
  --min-distinct-tools 3 \
  --require-state-change
```

Runner 支持与 OmniaBench 相同的步骤选择：

```bash
python -m task_gen.program_form --step 4 ...
python -m task_gen.program_form --step 3-7 ...
python -m task_gen.program_form --step 2,4,5 ...
```

非 `--fresh` 模式复用已有完整产物组；如果 Step 3、8、9 只存在其中一部分文件，
Runner 会拒绝把半成品当成已完成。`--fresh` 重新执行选中步骤。调试 Step 2 时可用
`--candidates /path/to/candidates.json` 跳过模型生成；其中每个候选只包含
`task_internal`、`output_schema`、`solution_code`。

## 5. 产物顺序

```text
<output_dir>/
├── baseline_environment/
├── step1_environment.json
├── step2_task_solution.json
├── step2_validation.json
├── step2_rounds/
├── step3_debug_solution.json
├── step3_debug_solution.jsonl
├── step4_ground_truth.jsonl
├── step5_verifier_code.jsonl
├── step6_debug_verifier.jsonl
├── step7_consistency.jsonl
├── step8_filter_rewrite.jsonl
├── step8_filter_rewrite_kept.jsonl
├── step9_filter_trace_state.jsonl
├── step9_filter_trace_state_kept.jsonl
├── step10_rubric.jsonl
├── step11_difficulty.jsonl
└── final/task_gen_final_english.json
```

JSONL 使用 OmniaBench 的 checkpoint 包装格式：

```json
{"__idx": 0, "item": {}}
```

同一索引出现多行时，读取逻辑以最后一条合法记录为准。

## 6. 执行边界

- 参考程序只能通过 `call_tool` 使用环境，不能直接读写状态。
- 每次工具调用校验 `inputSchema/outputSchema` 和统一 `success` envelope。
- 修改只读资产、未声明文件或 SQLite Schema 会被拒绝。
- 工具异常或失败后留下的状态修改会回滚。
- SQLite 按规范化记录内容比较，不按数据库文件字节比较。
- Step 7/11 的求解 Agent 看不到参考程序、Ground Truth 或工具内部代码。
- v2 DataGen 环境必须先由 ToolGen 提供 `tools.json`。
