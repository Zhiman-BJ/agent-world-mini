# BugAgent 连边质量问题清单

审核对象：`bugagent_final3.json`、`bugagent_final4_recovery.json`。

## 高严重度

1. `list_test_case_review_candidates` 在当前初态及现有工具允许的状态变化下始终返回空列表，但两张图仍保留了 7 条依赖其 `items[]` 的出边，并在 5 个 prerequisite 方案中把它当作 `test_case_id` 来源。这些链无法取得后续调用所需的实际标识。

2. `list_comments` 初态返回空列表，但 prerequisite 只检查该工具是否执行过，不检查结果是否非空。两张图中 `classify_bug`、`add_comment`、`get_bug_report`、`update_bug_report` 的单工具方案，以及 `link_test_case_to_bug` 的 4 个组合方案，都可能被一次空查询错误解锁。

3. 以下 3 级边把既有测试用例当成新建测试用例的直接材料，可能导致重复 `test_case_id` 或复制已有定义：
   - `get_test_reports_failures -> create_test_case`
   - `list_test_cases -> create_test_case`
   - `list_test_case_review_candidates -> create_test_case`

4. 以下边明显过强或缺少真实的数据、状态交接：
   - `list_test_cases -> classify_bug`：没有提供 `bug_id` 或 classification。
   - `list_bug_reports -> create_test_case`：最多提供描述或组件语义，不是实体或动态标识交接。
   - `get_security_results -> create_bug_report`：SARIF 结果不提供目标要求的测试运行来源。
   - `get_security_results -> create_test_case`：不提供新测试标识或完整测试定义。

5. `get_bug_report -> link_test_case_to_bug` 和 `list_test_case_links -> link_test_case_to_bug` 复用了已有 link。当前已有关系值是 `reported_by`，但目标工具只接受 `reproduced_by`、`regression_for`、`related`，因此现有结果不能按理由描述直接复用。

## 中严重度

6. 两张图共有 37 条边存在“只出现一次”或权重不同。验证器主要限制权重上限，除 `state_observation` 外没有确定性下限；同一个 `required_input + selected` 仍可能被第二轮判成 1、2 或 3。

7. 图 B 将两条明确的写后读取状态边降级：
   - `create_bug_report -> list_bug_reports`：图 A 为 3，图 B 为 2。
   - `update_bug_report -> list_bug_reports`：图 A 为 3，图 B 为 1。
   第一轮把状态观察误写成可选输入后，当前验证器无法识别并纠正。

8. 图 B 漏掉 3 个图 A 已包含的有效 prerequisite 替代来源：
   - `get_bug_report -> get_test_reports_failures`
   - `list_bug_reports -> create_bug_report`
   - `get_bug_report -> create_bug_report`

9. prerequisite 当前只记录“历史中执行过哪些工具”，不记录工具是否返回所需实体，也不记录具体输出和后续参数是否为同一对象。结构合法的链仍可能在实际执行时没有可用数据。

## 低严重度

10. 多个 reason 错称 `create_test_case` 会生成 `test_case_id`；内部实现实际使用调用参数中的 `test_case_id` 并原样返回。创建实体本身可以形成状态关系，但理由中的值来源不正确。

11. 图 A 有 4 个 `link_test_case_to_bug` prerequisite reason 把组合中两个工具的职责写反：
    - `get_test_reports_failures + list_bug_reports`
    - `create_test_case + list_bug_reports`
    - `get_test_reports_failures + list_comments`
    - `create_test_case + list_comments`

12. `[get_bug_report] -> link_test_case_to_bug` 的 reason 声称它能独立提供两个动态标识，但 `bug_id` 实际是调用 `get_bug_report` 时已有输入的回显；理由省略了更早的 `bug_id` 来源。

13. 当前 8 个 bug 的状态值为 `opened`，而 `list_bug_reports.status` 不接受该值。依赖状态筛选值直接回填的相关理由与实际 Schema 不完全一致。

14. 大量指向 `create_test_suite` 的 1 级边只依据“可以把上游文本写入 purpose”，业务信息增量很低，边界过宽且容易形成投机链。
