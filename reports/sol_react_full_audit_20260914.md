# 七环境 SOL/ReAct 任务评估总表

判定同时核对任务文本、真实工具调用及返回、最终状态/文件产物、最终答案；零调用、未完成或基础设施失败直接 FAIL。Pymatgen 的 task22 有一次 502，恢复结果单独注明。

## 统计口径

- SOL链长为 `state.agent/tool_calls.jsonl` 中的实际工具调用次数。
- PASS/FAIL 同时检查任务文本、工具返回、最终状态或文件、最终答案；不要求复现参考链顺序。
- 0 调用、未完成、基础设施错误直接 FAIL。Pymatgen task22 的首次 502 与恢复运行合并为同一逻辑任务。
- 表格使用普通 Markdown，便于飞书渲染。

## 环境汇总

| 环境 | 任务数 | SOL通过 | SOL失败 | SOL通过率 |
| --- | ---: | ---: | ---: | ---: |
| Recreation | 6 | 6 | 0 | 100% |
| Hugeicons | 1 | 1 | 0 | 100% |
| OpenZeppelin | 6 | 1 | 5 | 16.7% |
| food_safety | 6 | 5 | 1 | 83.3% |
| atomate2 | 7 | 3 | 4 | 42.9% |
| Medical Terminologies | 8 | 6 | 2 | 75% |
| pymatgen | 5 | 1 | 4 | 20% |
| 合计 | 39 | 23 | 16 | 59.0% |

## 逐任务明细

本次展开沿用已有 SOL 报告的判定；原链长度、判定引用 [七环境任务评估总表](seven_environment_results.md)。格式整理不代表新增了一轮质量复核。

| 环境 | 环境任务数 | 任务 | SOL链长 | SOL判定 | 原链长度 | 原链判定 | 主要问题或复核结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recreation | 6 | task5 | 5 | PASS | 26 | PASS | SOL快照、调用返回与答案一致 |
| Recreation | 6 | task7 | 5 | PASS | 27 | PASS | SOL快照、调用返回与答案一致 |
| Recreation | 6 | task10 | 10 | PASS | 25 | PASS | SOL快照、调用返回与答案一致 |
| Recreation | 6 | task13 | 11 | PASS | 28 | PASS | SOL快照、调用返回与答案一致 |
| Recreation | 6 | task16 | 6 | PASS | 24 | PASS | SOL快照、调用返回与答案一致 |
| Recreation | 6 | task21 | 10 | PASS | 27 | PASS | SOL快照、调用返回与答案一致 |
| Hugeicons | 1 | task4 | 12 | PASS | 41 | PASS | SOL资源文件、SVG与答案一致 |
| OpenZeppelin | 6 | task1 | 0 | FAIL | 30 | FAIL | SOL无工具调用；参考持久化审阅文件缺要求的分析 |
| OpenZeppelin | 6 | task6 | 0 | FAIL | 26 | PASS | SOL无工具调用，未交付任务结果 |
| OpenZeppelin | 6 | task8 | 0 | FAIL | 24 | PASS | SOL无工具调用，未交付任务结果 |
| OpenZeppelin | 6 | task10 | 12 | PASS | 31 | FAIL | SOL源码审查与答案一致；参考答案混淆导入与继承 |
| OpenZeppelin | 6 | task11 | 0 | FAIL | 21 | FAIL | SOL无工具调用；参考CSV缺分析和计票复核交付 |
| OpenZeppelin | 6 | task14 | 11 | FAIL | 31 | FAIL | SOL与参考归档报告均缺完整分析内容 |
| food_safety | 6 | task3 | 10 | PASS | 27 | PASS | SOL归档资料与答案一致 |
| food_safety | 6 | task11 | 12 | PASS | 43 | FAIL | SOL归档资料与答案一致；参考遗漏已有JECFA 1668记录 |
| food_safety | 6 | task13 | 0 | FAIL | 30 | PASS | SOL无工具调用，未交付任务结果 |
| food_safety | 6 | task18 | 16 | PASS | 30 | PASS | SOL归档资料与答案一致 |
| food_safety | 6 | task19 | 21 | PASS | 34 | PASS | SOL归档资料与答案一致 |
| food_safety | 6 | task21 | 16 | PASS | 28 | FAIL | SOL归档资料与答案一致；参考遗漏E338至E452组饮料条件记录 |
| atomate2 | 7 | task1 | 14 | PASS | 33 | PASS | SOL工作流与归档交付、答案一致 |
| atomate2 | 7 | task4 | 0 | FAIL | 26 | PASS | SOL无工具调用，未交付任务结果 |
| atomate2 | 7 | task6 | 0 | FAIL | 33 | PASS | SOL无工具调用，未交付任务结果 |
| atomate2 | 7 | task11 | 0 | FAIL | 25 | PASS | SOL无工具调用，未交付任务结果 |
| atomate2 | 7 | task12 | 22 | PASS | 33 | PASS | SOL输入修改与归档交付、答案一致 |
| atomate2 | 7 | task21 | 8 | PASS | 25 | PASS | SOL依赖清单交付与答案一致 |
| atomate2 | 7 | task23 | 0 | FAIL | 31 | PASS | SOL无工具调用，未交付任务结果 |

| pymatgen | 5 | task1 | 10 | FAIL | 32 | FAIL | CompleteDos声称可重建，但真实pymatgen重建失败 |
| pymatgen | 5 | task10 | 0 | FAIL | 30 | FAIL | SOL无工具调用；参考缺实际POTCAR势文件 |
| pymatgen | 5 | task17 | 14 | PASS | 29 | PASS | SOL结构、运行与输入文件交付一致 |
| pymatgen | 5 | task19 | 17 | FAIL | 30 | FAIL | CompleteDos实际重建报KeyError: efermi |
| pymatgen | 5 | task22 | 15 | FAIL | 27 | FAIL | SOL恢复运行仍未完成真实标准化；首次运行遇到502 |
| Medical Terminologies | 8 | task2 | 11 | PASS | 30 | PASS | SOL代码、层级及版本范围核对与答案一致 |
| Medical Terminologies | 8 | task3 | 16 | PASS | 31 | PASS | SOL映射与版本范围核对、答案一致 |
| Medical Terminologies | 8 | task4 | 26 | PASS | 28 | PASS | SOL多词汇关系核对与答案边界一致 |
| Medical Terminologies | 8 | task8 | 17 | PASS | 31 | PASS | SOL药物疾病关系核对与答案边界一致 |
| Medical Terminologies | 8 | task15 | 0 | FAIL | 26 | FAIL | SOL无工具调用；参考漏查显示码E11.0对应的三条映射 |
| Medical Terminologies | 8 | task17 | 10 | PASS | 24 | PASS | SOL药物关系与版本范围核对、答案一致 |
| Medical Terminologies | 8 | task19 | 14 | PASS | 30 | PASS | SOL文献索引及代码关系核对与答案一致 |
| Medical Terminologies | 8 | task22 | 0 | FAIL | 25 | PASS | SOL无工具调用，未交付任务结果 |

## 已确认汇总

- Recreation：6/6 PASS。
- Hugeicons：1/1 PASS。
- OpenZeppelin：1 PASS，5 FAIL。
- Medical Terminologies：6 PASS，2 FAIL。
- Pymatgen：1 PASS，4 FAIL（task22 恢复运行仍不改变结论）。
- food_safety：5 PASS，1 FAIL。
- atomate2：3 PASS，4 FAIL。

七环境合计：23 PASS，16 FAIL（39 条逻辑任务）。Pymatgen task22 的首次 502 与恢复运行按同一逻辑任务计一次。

## 证据报告

- [Recreation 逐条复核](sol_recreation_audit_20260914.md)
- [医学术语与 Pymatgen 逐条复核](sol_medical_pymatgen_audit_20260914.md)
