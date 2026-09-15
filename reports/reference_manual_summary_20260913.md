# 参考链及参考答案验收汇总

按与Qwen相同的任务文本标准验收已执行的参考链、最终reference.answer和真实final产物；不使用旧verifier判定，不将execution.success当作完成。没有补做任务或修改原始证据。

13条参考交付中9 PASS、4 FAIL（69.2%）。声明的参考工具调用全部在对应agent_result.json.execution.tool_calls中找到相同工具名和参数，数量一致；这只证明实际执行，不能替代结果验收。参考答案是tasks.json中的reference.answer，不等于执行阶段最初的last_message。

| 环境 | 任务 | 参考调用数 | 判定 | 依据 |
|---|---|---:|---|---|
| Recreation | task5 | 26 | PASS | 两晚筛选及设施/许可/入口研究完成，正确说明快照缺失 |
| Recreation | task7 | 27 | PASS | Half Dome研究各主题及覆盖边界完成，未误称Wilderness不存在 |
| Recreation | task10 | 25 | PASS | 双许可/营地/活动研究及日期快照限制有据 |
| Recreation | task13 | 28 | PASS | 四人三晚研究、候选比较、逐夜缺失与许可限制完成 |
| Recreation | task16 | 24 | PASS | 两人Upper Pines住宿筛选、Half Dome地址及研究完成 |
| Recreation | task21 | 27 | PASS | 荒野/固定营地比较与证据可核对性排序完成 |
| Hugeicons | task4 | 41 | PASS | 清单及两份SVG正确；路径数据、尺寸、颜色、描边、哈希已核对 |
| OpenZeppelin | task1 | 30 | FAIL | 真实MD/JSON缺要求的持久化分析，与Qwen同hash同缺陷 |
| OpenZeppelin | task6 | 26 | PASS | 计票与独立源码边界正确，无Qwen的虚构时间聚集结论 |
| OpenZeppelin | task8 | 24 | PASS | 三工程静态审阅及六文件归档/逐项哈希正确 |
| OpenZeppelin | task10 | 31 | FAIL | 参考答案错把IERC1155Receiver列为ERC1155继承接口；源码仅import |
| OpenZeppelin | task11 | 21 | FAIL | CSV无要求的分析/对账，与Qwen同hash；答案夸大CSV字段覆盖 |
| OpenZeppelin | task14 | 31 | FAIL | ZIP和哈希正确，但报告只有966字节，缺要求的分析/源码审阅/限制 |

四个失败中，task1/task11/task14属于实际产物内容不足，答案中的长篇分析并没有写入交付文件；task10属于工具提供了源码证据但最终答案仍写错核心关系。参考执行有信息可用，不代表最终交付合格。

恢复后的探索失败不直接判整题失败。日期查询缺快照，对明确要求公开研究并说明未知的任务，可以是正确结论；没有要求真实预订成功或动态编译通过。相反，用户明确要求报告文件包含分析时，聊天说明不能代替文件内容。

分环境详细证据：reference_manual_recreation_20260913.md、reference_manual_hugeicons_20260913.md、reference_manual_openzeppelin_20260913.md。原始运行目录见这些报告。

结论：当前reference不能无条件用作正样本。将其用于校准时，应先确保任务、答案和真实交付物一致；否则一个正确拒绝不完整参考产物的verifier可能被误判为有问题。
