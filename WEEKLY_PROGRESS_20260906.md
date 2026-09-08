# 本周进展：建图、Verifier、生成优化与真实任务参考

2026.08.31–2026.09.06 · 3 页 / 约 5 分钟

## 第 1 页｜建图已定稿：以实际工作线判断连边

**关键判断：工具图服务于任务采样。强边表示同一工作线连续推进，工具之间不必有必需的参数依赖；但“同属测试领域”也不能证明它们处理同一份数据。**

三级边连接同一工作线，二级边连接不同子任务，一级边用于任务层面的探索。必须先执行哪些工具单独记录，不由三级边推导。每个目标工具一次 LLM 调用，读取公开工具说明与压缩后的输入输出结构。

**实际改正的一条边**

`get_test_reports_failures → parse_junit_report`

| 产物 | 判定及理由原文 |
| --- | --- |
| v2 | `weight=3`：“已取得某运行的失败用例与证据路径后，再解析对应的 JUnit 报告可补足该失败上下文中的整体测试统计。” |
| 工具的实际契约 | `parse_junit_report` 无参数，只解析固定参考 XML；无法接收前一步运行的报告。 |
| v4 两轮 | 分别为一级、无边，均未再判为三级。 |

Prompt 收敛为：“判断每条 A -> B 前，必须先关注并依据两个工具在公开上下文和工具契约中的具体信息”。没有为这两个工具单设禁止连边规则。

**同时保留合理组合**：`list_test_cases → create_test_case` 两轮均为三级，第二轮理由原文：

> 列出现有用例可避免重复 ID；随后创建新的唯一稳定用例连续推进同一登记簿维护工作。

25 个工具的两轮建图产出 **208 / 213 条边，耗时 130 / 191 秒**；后续链审查与评分选出以下 8 步链，逻辑评分 5：

列运行 → 查失败 → 读用例 → 建缺陷 → 关联用例 → 分类 → 评论 → 读缺陷详情

来源：[v2 图](runs/first_principles_prompt_iterations/20260904_102344_590606_v2_bugagent_gpt-5.6-terra/intermediate/step_1_bundle.json)、[v4 第一轮](runs/first_principles_prompt_iterations/20260904_143214_900320_v4_specific_tool_info_bugagent_gpt-5.6-terra/intermediate/step_1_bundle.json)、[v4 第二轮及链](runs/first_principles_prompt_iterations/20260904_143913_169869_v4_repeat2_specific_tool_info_bugagent_gpt-5.6-terra/intermediate/step_2_bundle.json)。建图已合入主线；这条链的评分属于执行前评估。

---

## 第 2 页｜Verifier 探索：拆分检查后，仍要证明同一对象完成任务

**关键判断：每个子要求分别找到一个满足对象，不等于存在同一个对象满足全部要求。这个信息必须保留在检查结果和聚合逻辑中。**

任务拆分 → 证据方案 → 分别生成检查代码 → 校准 → 实际执行验证。拆分降低单次生成复杂度；本周进一步加入共享对象绑定，检查项失败时局部重试。

**真实产物暴露的结构问题**

9 月 6 日 Bugagent 证据方案将目标定义为“新增缺陷记录集合”，审核原文：

> R1 只要求存在见证成员，后续要求却可由集合中的不同成员分别满足。这样会把多个缺陷拼接成一次完成，未保持同一缺陷身份。

因此改为：先给定一组具体对象，各项检查使用同一组对象，再判断是否有一组全部通过。任务要求一条评论同时包含多项内容时，也不能用不同评论拼接。

**新版本实际生成的证据方案**

任务原文：

> 为“AMI meeting EN2002a”项目中的 EN2002a 转写节选生成保留原始时间戳和英文内容的 WebVTT 字幕文件。

| 检查项 | 方案中的 `binding_ids` |
| --- | --- |
| R1：有效 WebVTT，且对应目标转写 | `["B1", "B2"]` |
| R2：保留源时间戳 | `["B1", "B2"]` |
| R3：保留英文内容 | `["B1", "B2"]` |

B1 为源转写，B2 为同一份字幕交付物。R3 证明条件原文节选：

> 允许 cue 拆分、相邻源文本合并、重复 cue 和额外 cue；每个源分段实例均须有自己的对应文本实例。

该方案审核返回 `{"approved": true, "issues": []}`：约束对象和业务结果，同时保留合法的不同实现方式。最新试跑仍停在检查代码生成与审核，尚未完成端到端验证。

来源：[旧方案及拒绝理由](.worktrees/task-execution-verifier/runs/task_eval_v23_first_principles/20260906_003950_202858/results.json)、[新方案及审核结果](.worktrees/task-execution-verifier/runs/task_eval_v32_compact_verifier_context/20260906_222359_804085/results.json)。

---

## 第 3 页｜生成与真实任务参考：目标驱动改链，参考融入仍在探索

### Step 2–4：按目标补足调用

**关键判断：目标以任务质量为标准，可包含多个独立子任务；review 负责调整链来完成目标。不能因为原链只查一次，就把需要覆盖两个对象的任务缩成一个。**

采样链 → 初态探索作参考 → 生成并冻结目标 → review 补链 → 执行 → 转写与反思

**实际 review 对照**：目标要求盘点所有失败运行，初态观察到 `run_junit_01`、`run_junit_05`。

| 调用 | 原链 | review 后 |
| --- | ---: | ---: |
| 查失败 `get_test_reports_failures` | 1 次 | 2 次 |
| 读缺陷 `get_bug_report` | 1 次 | 2 次 |
| 读用例 `get_test_case` | 1 次 | 2 次 |
| 总链长 | 8 | 11 |

返回 `accepted=true`，理由原文节选：“原链缺少对两个失败运行的完整失败证据获取，以及逐缺陷、逐关联用例的验证，已按依赖补全。”这是链补全产物，尚未验证这条新链的完整执行。[原始记录](.worktrees/step234/runs/objective_review_scope/20260906_183621_140136_bugagent_gpt-5.6-terra/run.json)

### 真实任务参考：已取得样本并试验注入 Step 4

结合环境、工具、图和官方来源，产出 4 条参考。例如 GitLab 的 `Trace loses scalar status/goal history, keeps only the first value`，提炼出“测试通过但生产异常，补充真实形态回归覆盖并关联缺陷”的任务模式。[参考池](.worktrees/真实任务参考/runs/real_task_probe/bugagent_real_task_references_v2.json)

**注入参考池后，Step 4 的实际任务文本：**

> 为“JUnit failure: testApp”缺陷标记为功能缺陷，责任组件为 io.olamy.AlwaysFailTest；将 testApp 测试用例作为复现用例关联，并添加审计评论记录该用例因“built to fail”失败及相关关联情况。

[单样本输出](.worktrees/真实任务参考/runs/real_task_probe/bugagent_step4_with_references.json)呈现了分类、关联和审计三个业务结果。当前为手动注入试验，尚不能归因于参考池带来的稳定提升。

**尚未解决：图采样与真实任务是两种任务来源。** 采样后匹配真实任务，链未必覆盖要求；采样前按真实任务规划，工具可能不全，图也会从任务来源变为规划约束。当前只试验将真实任务作为 LLM 的场景与表达参考，尚无定稿的匹配和融合方案。
