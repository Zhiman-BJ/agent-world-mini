# 三个参考链漏检案例的 pipeline 溯源

调查范围：只读现有运行、工具轨迹与当前代码；未改代码、未重跑 LLM、未联网。以下按**首次实质性错误**归因，一个 case 只计一次。三个案例都是 Step 3 检索未恢复、将查询覆盖不足提升为归档缺失；Step 4 继承并固化，Step 5 未拦截。不能因此统计成 9 次独立失败，也不能据这三个定向选出的案例估计全体运行失败率。

## 路径与证据口径

- `F` = `runs/food_safety_sol_full/20260913_183040_233172_smithery_twohalves_food_safety_48_gpt-5.6-sol`
- `M` = `runs/medical_sol_full/20260913_204215_876768_smithery_sidneybissoli_medical_terminologies_mcp_10_gpt-5.6-sol`
- `R#` = `tasks/taskN/agent_result.json.execution.tool_calls` 的从 1 开始序号。原始 `tasks/taskN/tool_calls.jsonl` 还包含未进入有效链的调用，**其物理行号不总等于 R#**，下文分别给出。
- `Q#` = food 的 `qwen_trial_retry/taskN/state.agent/tool_calls.jsonl` 或 medical 的 `qwen_trial_final/taskN/state.agent/tool_calls.jsonl` 物理行号。
- 根目录 `llm_calls.jsonl` 下文行号是文件物理行号，定位依靠 prompt 中的完整 objective/task_text，未把并发 batch_index 当 task 编号。

| Case | Step 3 日志 | Step 4 初稿/反思/答案日志 | Step 5 日志 | 首次错误归因 |
|---|---:|---|---:|---|
| F task11 | 181 | 193 / 204 / 206 | 218 | Step 3，JECFA 名称检索未恢复 |
| F task21 | 185 | 195 / 197 / 210 | 220 | Step 3，EU 单编号过滤遗漏组条款 |
| M task15 | 89 | 114 / 117 / 133 | 137 | Step 3，未使用已观察到的显示码 |

## F task11：E150d 的 Class IV 与 ADI

Step 2 原始 objective（`F/intermediate/step_2_bundle.json`，task11.objective）：

> 同时识别配料中有明确名称或编号依据的添加剂，确认其JECFA身份并整理历年评估、ADI相关原文和归档缺口

目标在执行前已要求 JECFA 身份与 ADI；并不是 Step 4 后加要求。Step 3 最终 objective 仍要求“追溯标签明确声明的焦糖色和磷酸相关JECFA证据及归档缺口”。对象从未指定商品落实为 Coca-Cola 本身合理；问题在执行证据覆盖。

实际轨迹：R40（原始工具日志第41行）`screen_archived_food_label` 取得 E150d、欧盟名称 `Sulphite ammonia caramel`，JECFA 自动匹配为零；R41（第42行）`search_jecfa_additives({"query":"sulphite ammonia caramel","limit":20,"offset":0})` 返回 items=[]、total=0。其后只查询磷酸 R42–43（第43–44行），没有把 `caramel` 作为较宽名称搜索，没有读取/追踪1668。

已经可恢复的线索是返回名称里的 **caramel**；实际轨迹未返回 Class IV/1668，不能说 reference 看到了1668却抄漏。原始名称搜索零结果只说明该词组未命中。

首次语义扩大出现在 Step 3 最终交付（`F/llm_calls.jsonl:181` answer；亦见 agent_result.execution.answer）：

> 不能在本归档中确认其JECFA目录身份、ADI或历年评估；这属于证据覆盖缺口

`logic_reason` 同时将其包装为“E150d仅取得欧盟快照中的同编号名称证据，JECFA目录名称检索为空，不能补造身份”。保守地不补造身份本来正确，但没有完成可恢复检索，不能替代交付目标。

Step 4 最终 task_text 仍要求“追溯…E150d焦糖色和磷酸相关JECFA证据，并说明缺失的详情页、评估记录、ADI等归档缺口”。答案日志第206行继承为：

> 不能确认 E150d 的 JECFA 目录身份；其 JECFA 详情页、评估记录和 ADI 均未归档或未检得。

这里“未归档或未检得”把环境事实与检索事实并列，仍未补齐真正存在的目录与 ADI。Step 4 的反思（204）还将原稿“列明数值、差值和比例”精简为营养比较；这与本案添加剂漏检无关，不另计一个根因。

现有独立反证：Q12 `search_jecfa_additives(query="Caramel",limit=30)` 返回4类，其中1668 `CARAMEL COLOUR, Class IV / 8028-89-5 / 0-200 mg/kg bw`；Q13追踪1668显示目录存在、详情页0、事件0。**缺的是详情/事件，并非目录 ADI。** 另一个更强的同模型正例就在 F task21：R12同样完整名称零结果，R14（原始日志15行）改 `caramel`，R15（16行）读取1668，R23追踪历史。无需新工具或修改环境即可恢复。

## F task21：普通调味饮料 E338–452 条款

Step 2 objective 已要求“整理其适用的欧盟食品类别条件、限制和脚注”；Step 3 最终 objective 仍是为 Coca-Cola 的 E150d/磷酸整理欧盟条件与证据。因此缺口早于 Step 4。

R5（原始工具日志第6行）以 `e_number="E 338"` 查询，得到名称表、特殊食品、制剂条款；R7（第8行）以 `e_number="E 338",food_category="Flavoured drinks"` 查询返回 groups=[]、total=0。随后读取的是 R10–11 的婴儿/幼儿条款及 R19–20 的制剂条款，没有按已经取得的名称 **Phosphoric acid** 去掉单编号过滤，也没有仅按饮料类别展开。

实际 `F/tasks/task21/server.json` 的 `tools[name=search_eu_additive_conditions].internal.code` 对 E-number 栏执行：

```python
normalized_e(header_value(item[0], ('e-number',)) or '') == needle
```

`normalized_e` 去除非字母数字，所以 E338→e338、E338-452→e338452，整串不等；名称过滤另用 casefold 子串包含。该实现解释零结果，但**零结果不是完整类别无条款的证据**。返回的名称足以提供普通可用的替代检索入口；reference 轨迹没有拿到 `eu:38:3841`，并非拿到后漏写700。

首次扩大在 Step 3 reason（`F/llm_calls.jsonl:185`）：

> 欧盟快照未找到普通调味饮料类别的直接条件行，故只整理实际返回的名称表、特殊食品及制剂条件，并把产品类别适用性列为待确认。

同一 answer 又说“快照中带类别的行均不是普通软饮料类别”；限定单编号查询的结果被扩大成快照层面的覆盖判断。Step 4 第210行生成参考答案，继续把普通饮料条款当缺口；最终 task 仍要求 EU 食品类别、限量及使用条件，没有合理删除这项核心义务。

反证：Q23以 `additive_name="Phosphoric",limit=100` 返回 `eu:38:3841`，食品类别上下文 `eu:38:3808` 的原文为 `["14.1.4","Flavoured drinks"]`；条款原值为 `["E 338-452","Phosphoric acid — phosphates — di-, tri- and polyphosphates","700","(1) (4)",""]`。Qwen最终答案交付了700及脚注，仍保留完整法规适用性限制。此处不把编号组成员关系直接升级成最终合法使用结论，但已有条款应进入证据档案。

## M task15：E110 显示码 E11.0

Step 2 原始 objective 要求为“指定 DataSUS CID-10 V2008 诊断代码列出 WHO 2025-01 过渡表中的 ICD-11 候选及其分类位置”。Step 3 在业务选择中确定 E11/E110，最终 objective 保留候选与分类位置；最终 task_text 更明确要求同时提供 E11 和 E110 的 WHO 候选。不是另行要求整个疾病家族迁移。

R14（原始工具日志第21行）`get_cid10_entry(entry_type="subcategory",code="E110")` 已明确返回 `code="E110",display="E11.0",category_code="E11"`。R15（第22行）又返回 E11 的子类别，E110 的 title_short 为 `E11.0 C/coma`。R16查询E11得到3行，但 R25（第32行）仍以 `icd10_code="E110"` 查询，items=[]、total=0、has_more=false，此后只有目录统计R26，没有查询已知显示码。

`M/tasks/task15/server.json` 的 `tools[name=query_icd_revision_mappings].internal.code` 直接令 `filters[field]=arguments[field]`，交给 `context.records.list('icd11_mapping',filters=filters,...)`；不补点或去点。现有工具结果已经给出转换依据，无需模型自行推测编码格式。

Step 3 的 answer（`M/llm_calls.jsonl:89`）先正确写“E110 精确映射查询：0 行”，但末尾扩大为：

> 不确定/资料缺失：…E110 没有独立 WHO 过渡行。

其 reason 对 E11 的3行与层级详述，却没有指出 E110 仍未完成显示码核验。Step 4 第133行答案继续称：

> 对 E110，精确查询返回 0 条，本地 WHO 2025-01 过渡资料中未找到候选。

同一最终答案已写“DataSUS CID-10 E110（显示形式 E11.0）”，因此 Step 4/5 的输入自身包含可疑的编码范围不一致，不能只说审查阶段完全缺乏线索。

反证：Q10实际调用 `query_icd_revision_mappings(direction="icd10_to_icd11",icd10_code="E11.0")`，返回3行：`10To11MapToMultipleCategories.txt:2579`、`10To11MapToOneCategory.txt:2104` 两条MMS目标5A11；`foundation_10To11MapToOneCategory.txt:2104` 为 Foundation URI entity/119724091、Subclass、precedence=0、MMS码null。原文源码标题都是 `Type 2 diabetes mellitus with coma`。这些是应报告的候选，不是自动重编码许可。Qwen本题最终答案另有截断失败，所以这里只将其作为**查询恢复正例**，不把整题算PASS。

## Step 5 究竟检查了什么，为何全真

三个实际 prompt 都定义 `execution_matches_task` 为实际执行满足全部适用要求、`answer_matches_task` 为准确完整且无重要遗漏，并明确“初态摘要不保证完整或准确”“区分查询范围、已有事实和推断”。但三条实际 answer 均原样为：

```json
{"execution_matches_task":true,"answer_matches_task":true,"task_is_usable":true,"errors":[]}
```

对应 F:218、F:220、M:137；各自 `intermediate/step_5_bundle.json` 的 validation 也为 passed=true 且三个判断true。不是只看到 prompt 中全真示例而误认为结果通过。

当前 `task_gen/tool_graph/step_5_task_validate.py:146` 构造输入：environment公开描述、全部public tools、Step 3 reason作为review_guidance、最终task_text/reference_answer、chain及真实tool_calls。它不执行新的工具查询，也不读取final数据库全文；因此未查询到的1668/3841/三条E11.0记录本身不会凭空进入审查上下文。Step 5 有能力审查“负查询能否支持全库缺失”，特别medical有显示码显式线索，但模型此次没有执行这种范围核对。

Step 4 答案生成（`step_4_task_compose.py:192`、`:203`）基于 task_text、tool_calls、review_guidance，要求不足时如实说明缺口；这能避免凭空补答案，却无法自行弥补检索覆盖。Step 3 错误 reason 继续作为 Step 4/5 的 guidance，是可见的信息传播路径。不能证明模型内心一定被锚定，但可确认它没有获得独立查询反证，并接受了已有缺口叙事。

当前 `execution_agent.py:123` 的 success 判据是运行无异常、agent自报completed=true、有calls；它没有业务语义证明。`execution_agent.py:29` prompt虽要求完整交付才completed=true，仍依赖agent自行判断。这三个案例的真实成功状态不应等同人工验收PASS。

## 当前代码与历史 prompt 的核对

为避免以当前代码替代当时行为，本次使用运行保存的 Step 2 environment/candidate 与 Step 4 candidate，调用当前纯 prompt 构造函数，只在内存重建字符串，不发出模型请求：

- `execution_prompt` 加当前 review-plan-selection 技能前缀，与 F:181、F:185、M:89 的完整历史 prompt **逐字相等**。
- 当前 Step 4 `_build_prompt` 以各历史 prompt 的输入context重建，九条初稿/反思/答案 prompt **逐字相等**。
- 当前 Step 5 `_build_review_prompt` 使用运行保存对象与全部public tools重建，F:218、F:220、M:137 **逐字相等**。

因此本报告对这些 prompt 的代码解释有实测一致性支持；不延伸声称整个执行器代码版本相同。尤其注意 Step 4 context 虽构造 execution_answer 字段，最终 `_build_prompt` 只选取 task_text/tool_calls/review_guidance 传给参考答案阶段，历史日志的data keys也证实没有execution_answer；不能误说它直接复制了该字段。

## 去重频率与最小通用建议

| 首次失败步骤 | 本次3个case数量 | 角色 |
|---|---:|---|
| Step 2 | 0 | 目标已要求对应证据；未发现这三项失败由采样目标首先引入 |
| Step 3 | 3 | 未恢复负查询，错误归档缺失判断首先在此出现 |
| Step 4 | 0个新增case | 继承/固化3个既有错误，medical在已知显示码下仍未识别范围问题 |
| Step 5 | 0个新增case | 3/3漏拦；属于守门失败指标，不与根因数量相加 |

最小通用改进应针对**负查询的证明范围**：对任务必需对象，一个窄字符串查询的0结果只能支持“此查询未命中”；在判缺档或自报完整前，利用已经返回的名称、显示码、类别上下文完成可用的替代检索，仍未解决就明确保留未完成，而不是认定环境无资料。该规则能统一解释三案，无需加入 Caramel/E338/E11.0 专项补丁，也不要求无界穷搜。

Step 5 独立检查最终答案的每个“归档没有/缺失”断言是否超出实际筛选条件；发现现有别名/显示码线索未核实，按证据不足或未完成处理。仅再加一句泛泛的“完整检查”不够——现有 prompt 已有这些原则。优先让失败断言明确绑定查询范围，再考虑需要独立工具核验的升级；本次调查不实施任何改动。
