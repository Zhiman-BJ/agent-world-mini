# TaskGen 流程图文字稿

## 完整示例｜9 月 6 日发布质量复核

`initial_probe_final / task1`，北京时间 9 月 6 日 00:56:30 完成，复用旧图并从 Step 2 检查点继续运行，以下均取自这一份产物，不包含新版 Verifier 验证

**选边示例**

| 本次使用的边 | 边权 | 图中理由节选 |
| --- | ---: | --- |
| `get_bug_report → get_test_reports_failures` | 3 | 返回的 bug.source_run_id 可直接填入 B 的 run_id，目标可立即查询该缺陷来源运行的失败用例 |
| `add_comment → get_bug_report` | 3 | 已为缺陷写入审计评论，B 会直接在 comments 中读取该新状态 |

**最终执行链｜15 次调用，按实际顺序保留重复调用**

```text
get_security_results → classify_bug → list_comments → get_bug_report
→ get_test_reports_failures → link_test_case_to_bug → list_test_case_links
→ link_test_case_to_bug → get_test_case → get_test_case → classify_bug
→ list_comments → add_comment → get_bug_report → get_test_reports_failures
```

链审查将第一次失败证据查询移到建立关联之前，确保所选用例与缺陷的来源运行一致

**生成的任务文本｜保留全文内容，句号改为分段**

> 对缺陷 BUG-JUNIT-D9DA18B87CFC（“JUnit failure: testApp”）完成发布质量复核：查看安全扫描结果作为风险背景；确认分类为 functional、责任组件为 io.olamy.AlwaysFailTest，并核验缺陷完整记录及审计历史
>
> 针对来源测试运行 run_junit_01，确认 testApp（TC-BE8D1CF77EE1）因“built to fail”失败，证据轨迹为 always_fail.xml
>
> 核验该缺陷已有的 reported_by 关联；仅当 reproduced_by 关联缺失时，建立该用例的 reproduced_by 关联，且复现、回归或相关关联总数不得超过两条
>
> 核验关联和该用例执行结果后，追加一条引用上述失败证据、分类、组件和关联结论的审计评论，最后复核缺陷记录、测试关联和失败证据的一致性

**执行与导出结果**

15 次调用一次执行成功，确认 `reported_by` 与 `reproduced_by` 两种关联，新增审计评论 `comment_001`，最后回读缺陷和失败报告

资源边界为必须修改 `quality_registry`，允许修改列表为空，其余 6 项资源禁止修改，Step 5 返回 `passed=true`、`errors=[]`

[选边、调用链与完整执行结果][sep6-bundle] · [正式任务与参考答案][sep6-tasks] · [运行时间与来源][sep6-run]

## Step 0｜读取并检查环境

只读取环境和工具阶段的产物、合规性检查

<details>
<summary>示例｜Bugagent 环境包</summary>

9 月 3 日 `task-grounded-pipeline` 试跑读取的环境为 `bugagent_open_quality_triage_001`，包含 25 个工具

| 输入产物 | 实际内容节选 |
| --- | --- |
| 验证结果 | `status=passed`、`schema_valid=true`、`resource_valid=true` |
| 质量登记簿资源 | `resource_id=quality_registry`、`path=entities/quality_registry.json`、`writable=true` |

[环境输入][env-example] · [验证结果][env-validation]

</details>

## Step 1｜建立工具图

由 LLM 逐个目标工具判断其他工具能否与它直接衔接

边权分为三级：3 同一工作线连续推进、2 不同子任务衔接、1 任务层面的探索关联

判断目标工具所必需的前置调用

<details>
<summary>示例｜同一张图中的三级连边</summary>

9 月 4 日 v4 第二轮建图结果，理由取自实际输出

| 连边 | 边权 | 判定理由节选 |
| --- | ---: | --- |
| `add_comment → get_bug_report` | 3 | 评论写入返回 bug_id，紧接着读取缺陷可取得包含该审计评论的完整上下文 |
| `get_bug_report → list_bug_reports` | 2 | 读取单个缺陷的可追溯上下文后，浏览缺陷列表可将该缺陷放回整体风险队列 |
| `get_bug_report → get_stats` | 1 | 查看单个缺陷的关联和评论后汇总全局登记簿，可推进审计任务但不延续同一记录操作 |

[原始工具图][graph-example]

</details>

## Step 2｜采样与确定目标

按边权和链间重复惩罚筛选采样调用链

探索环境初态，结合候选链生成并冻结任务目标

审查调用链，按目标补足必要调用，以任务目标为核心不可变约束

按调用链逻辑性质量筛选过滤，得到最终候选链

<details>
<summary>示例｜目标覆盖两个失败运行，调用链从 8 步补到 11 步</summary>

9 月 6 日 `objective_review_scope` 的目标节选

> 对测试运行清单中执行失败的所有运行进行发布质量风险盘点：逐个提取失败用例及证据，核对其在缺陷登记簿中的对应缺陷、关联测试用例的执行记录和审计评论，并结合全量缺陷统计形成可追溯的风险清单

已观察到 `run_junit_01`、`run_junit_05` 两个失败运行，目标保持不变

| 调用 | 审查前 | 审查后 |
| --- | ---: | ---: |
| 查失败 `get_test_reports_failures` | 1 次 | 2 次 |
| 读缺陷 `get_bug_report` | 1 次 | 2 次 |
| 读用例 `get_test_case` | 1 次 | 2 次 |
| 总链长 | 8 | 11 |

审查返回 `accepted=true`，理由节选

> 原链缺少对两个失败运行的完整失败证据获取，以及逐缺陷、逐关联用例的验证，已按依赖补全

[补链记录][chain-example]，本次只做链审查，未执行补全后的链

</details>

## Step 3｜执行调用链

固定目标和调用链，由 LLM 根据工具契约与前序返回值逐步生成参数

每条候选链在独立环境副本中执行

保存真实调用轨迹与失败历史，成功候选保留初末状态，供任务生成和后续验证使用

<details>
<summary>示例｜更新缺陷后，再次读取确认状态和评论</summary>

以下 Step 3–5 使用同一次试跑的 `task2`，来自 9 月 6 日 `initial_probe_diagnostic_final`

目标为选择一个失败用例，找到同一运行中已关联的缺陷，更新为 `investigating` 并追加审计评论

实际更新调用

```json
{
  "tool": "update_bug_report",
  "arguments": {
    "bug_id": "BUG-JUNIT-D9DA18B87CFC",
    "status": "investigating"
  }
}
```

`entities/quality_registry.json` 中同一缺陷的初末状态

| 字段 | 执行前 | 执行后 |
| --- | --- | --- |
| `status` | `open` | `investigating` |
| 关联评论 | 空 | `comment_001`，作者为 `quality-triage` |

最后一次 `get_bug_report` 同时返回更新后的状态和新增评论，`execution.success=true`

[调用轨迹][execution-example] · [初态登记簿][registry-initial] · [末态登记簿][registry-final]

</details>

## Step 4｜生成任务与答案

仅对执行成功的候选生成任务文本

单独调用 LLM 反思文本，逐项对照目标的对象、动作、范围和条件，只修正表达

根据真实调用结果生成参考答案，未完成或证据不足时返回失败

按任务要求划分必须修改、允许修改和禁止修改的资源

<details>
<summary>示例｜同一任务的文本、反思、答案与资源边界</summary>

任务文本节选

> 审查发布候选版本的失败测试，任选一个失败用例，找到同一测试运行中已登记且与该用例存在可追溯关联的缺陷
>
> 将该缺陷状态更新为 investigating，并在该缺陷下追加审计评论，记录失败证据及确认关联关系的复核结论

反思返回 `need_revision=false`，分析节选

> 失败证据、关联复核结论和归属验证均为必要业务条件；“任选”与存在多个失败运行的初态相符

参考答案节选

> 最终读取缺陷详情确认状态为 investigating，评论 comment_001 已归属 BUG-JUNIT-D9DA18B87CFC

资源边界：必须修改 `quality_registry`，允许修改列表为空，`junit_reports`、`test_runs` 等 6 项资源禁止修改

[生成产物][compose-example] · [反思原始回答][compose-calls]

</details>

## Step 5｜组装校验与导出

执行是否完成目标

任务文本是否忠实于目标

任务是否信息充分且可执行可核验

同时检查格式并组装

<details>
<summary>示例｜通过与拒绝的实际输出</summary>

上述 `task2` 的检查结果

```json
{
  "passed": true,
  "execution_matches_objective": true,
  "task_matches_objective": true,
  "task_is_usable": true,
  "errors": []
}
```

同一试跑的 `task1` 虽然工具执行成功，但参考答案生成失败，最终被拒绝，保留的原因节选

> 任务未完成：未获取 run_junit_05 的失败测试用例、错误信息及来源证据；未核对其对应缺陷详情、测试关联、全部执行记录和审计评论

[完整检查结果][validation-example]

</details>


## Verifier｜生成检查程序并验证任务完成情况

将任务拆成可分别验收的子任务，明确每项要求的对象、范围和完成条件，减小后续步骤负担

<details>
<summary>示例｜将字幕任务拆成独立验收要求</summary>

以下三步使用 9 月 6 日 v32 的字幕任务，任务原文节选

> 为“AMI meeting EN2002a”项目中的 EN2002a 转写节选生成保留原始时间戳和英文内容的 WebVTT 字幕文件

生成的验收要求节选

| 要求 | 实际拆分结果 |
| --- | --- |
| R1 | 存在对应“AMI meeting EN2002a”项目中 EN2002a 转写节选的有效 WebVTT 字幕文件 |
| R2 | 目标转写节选中每个要求保留的原始时间戳均被字幕文件覆盖并保持一致 |
| R3 | 目标转写节选中的每项英文内容均被字幕文件覆盖并保持一致 |

[拆分结果][verifier-example]，另有 R4 检查无关、破坏性或冲突副作用

</details>

### 2｜寻找对应文件证据

为每项子任务定位初末状态中的相关文件和字段，明确用哪些证据判断完成、未完成或证据不足

<details>
<summary>示例｜源转写片段与字幕产物对应</summary>

证据方案定位 `derived/transcripts/` 中的源转写、`entities/meeting_catalog.json` 中的项目关联，以及 `exports/` 中的字幕文件

这份任务的参考执行产物中，源文件 `derived/transcripts/ami_en2002a_0.json` 第一段节选

```json
{
  "speaker": "FEO070",
  "start_seconds": 12,
  "end_seconds": 16,
  "text": "SO WHAT DO WE NEED TO TALK ABOUT"
}
```

对应 `exports/ami_en2002a_0.vtt` 的第一条字幕

```vtt
WEBVTT

1
00:00:12.000 --> 00:00:16.000
FEO070: SO WHAT DO WE NEED TO TALK ABOUT
```

R2 对照 `12–16` 秒的时间范围，R3 对照英文文本，方案中的 R1–R3 共同引用同一源转写 B1 与同一字幕文件 B2

[证据方案][verifier-example] · [源转写][transcript-example] · [字幕文件][subtitle-example]

</details>

### 3｜生成对应代码

按各项子任务的证据方案分别生成检查代码，再组装为完整 Verifier

<details>
<summary>示例｜实际生成的时间戳检查代码</summary>

v32 第 2 次代码生成中，R2 收集未匹配的源片段后返回以下结果，节选自 `check_2`

```python
if unmatched:
    return {'status': 'fail', 'reason': 'B1 segment timestamp pairs lack matching B2 cues at indices %s.' % unmatched, 'evidence_refs': evidence_refs}
return {'status': 'pass', 'reason': 'Every B1 segment timestamp pair is covered by a B2 cue, allowing deterministic integer rounding.', 'evidence_refs': evidence_refs}
```

该轮证据方案已通过审核，但完整检查代码未通过审核，不能作为端到端通过的例子

[代码与审核记录][verifier-example]

</details>

最终调用codex在沙盒中实际执行并评判

<details>
<summary>示例｜历史版本的实际执行评判</summary>

9 月 3 日旧版 Verifier 的 Bugagent `task10`，任务为推进缺陷至调查中、关联复现用例、追加审计评论

| 检查项 | 实际结果 |
| --- | --- |
| R1 状态更新 | `pass`，目标缺陷最终状态为 `investigating`，且不同于初始状态 |
| R2 复现关联 | `pass`，最终登记簿存在目标缺陷与失败用例的 `reproduced_by` 关联 |
| R3 审计评论 | `pass`，新增 `comment_001`，记录已核实失败证据并建议开展调查 |

整条任务的 `outcome=pass`，证据指向初末 `entities/quality_registry.json` 和实际工具调用

[历史执行结果][historical-eval]，与上面的 v32 三阶段生成试跑分开记录

</details>

[env-example]: .worktrees/task-grounded-pipeline/runs/taskgen-grounded/seed42-retry/20260903_003859_534867_bugagent_gpt-5.6-terra/intermediate/step_0_bundle.json
[env-validation]: .worktrees/task-grounded-pipeline/artifacts/mcp_quality_3env_20260825/bugagent/validation.json
[graph-example]: runs/first_principles_prompt_iterations/20260904_143913_169869_v4_repeat2_specific_tool_info_bugagent_gpt-5.6-terra/intermediate/step_1_bundle.json
[chain-example]: .worktrees/step234/runs/objective_review_scope/20260906_183621_140136_bugagent_gpt-5.6-terra/run.json
[execution-example]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/intermediate/step_3_bundle.json
[registry-initial]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/tasks/task2/initial/entities/quality_registry.json
[registry-final]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/tasks/task2/final/entities/quality_registry.json
[compose-example]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/intermediate/step_4_bundle.json
[compose-calls]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/llm_calls.jsonl
[validation-example]: .worktrees/step234/runs/initial_probe_diagnostic_final/20260906_005620_622011_bugagent_gpt-5.6-terra/intermediate/step_5_bundle.json
[verifier-example]: .worktrees/task-execution-verifier/runs/task_eval_v32_compact_verifier_context/20260906_222359_804085/results.json
[transcript-example]: .worktrees/task-execution-verifier/runs/task_eval_smoke_input/happyscribe_simple/tasks/task1/initial/derived/transcripts/ami_en2002a_0.json
[subtitle-example]: .worktrees/task-execution-verifier/runs/task_eval_smoke_input/happyscribe_simple/tasks/task1/final/exports/ami_en2002a_0.vtt
[historical-eval]: .worktrees/task-execution-verifier/runs/task_eval/20260903_143133_576928/results.json
[sep6-bundle]: .worktrees/step234/runs/initial_probe_final/20260906_004909_146549_bugagent_gpt-5.6-terra/intermediate/step_5_bundle.json
[sep6-tasks]: .worktrees/step234/runs/initial_probe_final/20260906_004909_146549_bugagent_gpt-5.6-terra/tasks.json
[sep6-run]: .worktrees/step234/runs/initial_probe_final/20260906_004909_146549_bugagent_gpt-5.6-terra/run.json
