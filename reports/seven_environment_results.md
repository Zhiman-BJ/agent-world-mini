# 七环境任务评估总表

整理日期：2026-09-14。共7个环境、39条任务。

## 统计口径

- Qwen链长为实际工具调用次数，包含失败调用；中断任务统计截至中断时的次数，未启动记0。
- 原链长度为 tasks.json 中 reference.tool_calls 的数量。
- PASS/FAIL按任务文本与实际交付判断，不要求复现参考调用顺序。文件交付不完整、答案核心事实错误、执行未完成均可导致FAIL。
- 全部任务已完成复核，仅保留PASS/FAIL。环境或工具缺陷导致实际交付不满足任务要求，同样记FAIL；PASS不表示每句文字都没有次要错误。
- 本表使用普通Markdown表格，不使用合并单元格、HTML、嵌套列表或单元格内换行，便于导入飞书文档。

## 环境汇总

| 环境 | 任务数 | Qwen通过 | Qwen失败 | Qwen通过率 | 原链通过 | 原链失败 | 原链通过率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recreation | 6 | 2 | 4 | 33.3% | 6 | 0 | 100% |
| Hugeicons | 1 | 0 | 1 | 0% | 1 | 0 | 100% |
| OpenZeppelin | 6 | 1 | 5 | 16.7% | 2 | 4 | 33.3% |
| food_safety | 6 | 5 | 1 | 83.3% | 4 | 2 | 66.7% |
| atomate2 | 7 | 2 | 5 | 28.6% | 7 | 0 | 100% |
| pymatgen | 5 | 1 | 4 | 20% | 1 | 4 | 20% |
| Medical Terminologies | 8 | 0 | 8 | 0% | 7 | 1 | 87.5% |
| 合计 | 39 | 11 | 28 | 28.2% | 28 | 11 | 71.8% |

## 逐任务明细

| 环境 | 环境任务数 | 任务 | Qwen链长 | Qwen判定 | 原链长度 | 原链判定 | 主要问题或复核状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recreation | 6 | task5 | 9 | FAIL | 26 | PASS | Qwen上下文超限，无最终交付 |
| Recreation | 6 | task7 | 7 | FAIL | 27 | PASS | Qwen错称已有Wilderness许可和营位未收录 |
| Recreation | 6 | task10 | 4 | FAIL | 25 | PASS | Qwen上下文超限，无最终交付 |
| Recreation | 6 | task13 | 7 | PASS | 28 | PASS | 研究、筛选与快照限制已交付 |
| Recreation | 6 | task16 | 13 | PASS | 24 | PASS | 地址核对、行程和住宿筛选已交付 |
| Recreation | 6 | task21 | 4 | FAIL | 27 | PASS | Qwen上下文超限，无最终交付 |
| Hugeicons | 1 | task4 | 0 | FAIL | 41 | PASS | verifier准备失败，Qwen未启动 |
| OpenZeppelin | 6 | task1 | 7 | FAIL | 30 | FAIL | 两者持久化审阅文件均缺要求的分析 |
| OpenZeppelin | 6 | task6 | 12 | FAIL | 26 | PASS | Qwen异常分析写错钱包数量和时间窗口 |
| OpenZeppelin | 6 | task8 | 8 | PASS | 24 | PASS | 静态审阅和归档完整性已核对 |
| OpenZeppelin | 6 | task10 | 13 | FAIL | 31 | FAIL | Qwen接口比较错误；参考答案混淆导入与继承 |
| OpenZeppelin | 6 | task11 | 4 | FAIL | 21 | FAIL | 两者CSV均缺分析和计票复核交付 |
| OpenZeppelin | 6 | task14 | 0 | FAIL | 31 | FAIL | Qwen未启动；参考归档报告内容不完整 |
| food_safety | 6 | task3 | 18 | PASS | 27 | PASS | 本地资料审查完成 |
| food_safety | 6 | task11 | 15 | PASS | 43 | FAIL | 参考遗漏已有JECFA 1668记录 |
| food_safety | 6 | task13 | 14 | PASS | 30 | PASS | 本地资料审查完成 |
| food_safety | 6 | task18 | 21 | PASS | 30 | PASS | 本地资料审查完成 |
| food_safety | 6 | task19 | 19 | FAIL | 34 | PASS | Qwen上下文超限，无最终交付 |
| food_safety | 6 | task21 | 30 | PASS | 28 | FAIL | 参考遗漏E338至E452组的饮料条件记录 |
| atomate2 | 7 | task1 | 7 | FAIL | 33 | PASS | Qwen无最终回答 |
| atomate2 | 7 | task4 | 9 | FAIL | 26 | PASS | Qwen上下文超限 |
| atomate2 | 7 | task6 | 7 | FAIL | 33 | PASS | Qwen上下文超限 |
| atomate2 | 7 | task11 | 8 | PASS | 25 | PASS | 工作流核对与归档完成 |
| atomate2 | 7 | task12 | 20 | PASS | 33 | PASS | 输入修改、核对及归档完成 |
| atomate2 | 7 | task21 | 12 | FAIL | 25 | PASS | Qwen仅交付引用汇总和部分示例，未完整交付40项依赖清单 |
| atomate2 | 7 | task23 | 23 | FAIL | 31 | PASS | Qwen上下文超限 |
| pymatgen | 5 | task1 | 11 | FAIL | 32 | FAIL | 实际CompleteDos重建失败；环境校验仅检查字段 |
| pymatgen | 5 | task10 | 19 | FAIL | 30 | FAIL | 要求完整外部VASP输入，实际只有POTCAR.spec，缺实际势文件 |
| pymatgen | 5 | task17 | 14 | PASS | 29 | PASS | 所选SiO2真实重建成功，身份审查与高对称线输入交付成立 |
| pymatgen | 5 | task19 | 16 | FAIL | 30 | FAIL | 新转换的CompleteDos实际报KeyError: efermi |
| pymatgen | 5 | task22 | 12 | FAIL | 27 | FAIL | 标准化工具只复制8位点源结构，真实标准化常规胞为4位点 |
| Medical Terminologies | 8 | task2 | 2 | FAIL | 30 | PASS | Qwen上下文超限 |
| Medical Terminologies | 8 | task3 | 16 | FAIL | 31 | PASS | Qwen把两条同目标映射误说成BA00和BA00.Z的落点歧义 |
| Medical Terminologies | 8 | task4 | 6 | FAIL | 28 | PASS | Qwen上下文超限 |
| Medical Terminologies | 8 | task8 | 10 | FAIL | 31 | PASS | Qwen上下文超限 |
| Medical Terminologies | 8 | task15 | 10 | FAIL | 26 | FAIL | Qwen最终回答残缺；参考只查E110，漏掉显示码E11.0的三条映射 |
| Medical Terminologies | 8 | task17 | 8 | FAIL | 24 | PASS | Qwen上下文超限 |
| Medical Terminologies | 8 | task19 | 2 | FAIL | 30 | PASS | Qwen上下文超限 |
| Medical Terminologies | 8 | task22 | 19 | FAIL | 25 | PASS | Qwen把Subclass分类关系错误提升为权威等价 |

## 对此前汇总的更正

1. atomate2 task21此前逐行判FAIL，但小计仍写3/7通过，存在算术矛盾。本表依据实际回答仅有引用汇总和部分示例，记FAIL，小计改为2/7。先前子代理报告中“有40项清单”的表述与实际回答不符。
2. OpenZeppelin旧总表的Qwen链长数组顺序错误，且包含未启动任务的错误数值。本表按task_id重新读取工具日志，task14为0。
3. 医学环境的3条answered此前未经完整内容审核就计为PASS，现复核均为FAIL；医学参考task15也FAIL。pymatgen经真实库重建及文件复验，两侧均仅task17通过。
4. 原链判定包含最终答案及实际交付物。仅看调用过程时，OpenZeppelin task10的错误在答案归纳，不代表参考链没取得正确源码。

## 本次补充复核依据

- 医学逐任务说明：[new_medical_manual.md](new_medical_manual.md)，包含实际映射行、原回答引用及参考交付逐项依据。
- pymatgen逐任务说明：[new_pymatgen_manual.md](new_pymatgen_manual.md)，包含实际库版本、对象重建错误、标准化对照与各任务验收边界。
- pymatgen的环境缺陷不能全部归咎于Qwen，参考执行同样受影响；本次依照“环境或工具问题也算FAIL”的既定标准计分。

## 数据版本

| 环境 | 运行根目录 | Qwen结果子目录 |
| --- | --- | --- |
| Recreation | runs/recreation_sol_retry_2/20260913_085426_497767_smithery_recreation_gov_93_gpt-5.6-sol | sol_verifier_qwen |
| Hugeicons | runs/hugeicons_sol_full/20260913_090540_302041_smithery_hugeicons_mcp_server_67_gpt-5.6-sol | sol_verifier_qwen |
| OpenZeppelin | runs/openzeppelin_sol_full/20260913_090540_320853_smithery_openzeppelin_25_gpt-5.6-sol | sol_verifier_qwen |
| food_safety | runs/food_safety_sol_full/20260913_183040_233172_smithery_twohalves_food_safety_48_gpt-5.6-sol | qwen_trial_retry |
| atomate2 | runs/atomate2_sol_full/20260913_183040_866328_pypi_atomate2_3_gpt-5.6-sol | qwen_trial_retry |
| pymatgen | runs/pymatgen_sol_full/20260913_204215_909408_pypi_pymatgen_core_2_gpt-5.6-sol | qwen_trial_final |
| Medical Terminologies | runs/medical_sol_full/20260913_204215_876768_smithery_sidneybissoli_medical_terminologies_mcp_10_gpt-5.6-sol | qwen_trial_final |

以上相对路径以主仓库 /data1/home/tianfang/agent-world-mini-zhiman 为根，不以本报告所在worktree为根。
