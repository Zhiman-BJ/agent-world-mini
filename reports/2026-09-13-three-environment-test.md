# 三环境测试与 JSON 尾部解析检查点

本次提交保留公共 JSON 解析修改及测试，并记录三环境运行现场位置和 verifier 问题。运行产物保存在本机 runs/，被 Git 忽略，未纳入本提交；仅凭 Git 不能恢复这些产物。

## 运行位置

- Recreation：`runs/recreation_sol_retry_2/20260913_085426_497767_smithery_recreation_gov_93_gpt-5.6-sol`
- Hugeicons：`runs/hugeicons_sol_full/20260913_090540_302041_smithery_hugeicons_mcp_server_67_gpt-5.6-sol`
- OpenZeppelin：`runs/openzeppelin_sol_full/20260913_090540_320853_smithery_openzeppelin_25_gpt-5.6-sol`

每个运行下的 `sol_verifier_qwen/task*/` 保存评分结果、LLM 日志及成功生成的 verifier。SOL 负责 verifier 生成和语义评分，Qwen 负责独立执行。共保留 6、1、6 条任务；11 份已生成 verifier 均未命中缓存。运行采用 verifier 提交 `7cc103d`，当前流程只有模型审查，没有实际执行校准。

## 结果与待处理问题

| 环境 | 任务 | 结果与原因 |
|---|---|---|
| Recreation | task5、task10、task21 | verifier 已生成；Qwen 执行因输入加请求输出超过服务上下文限制而中断 |
| Recreation | task7、task13 | 非法证据引用导致整单 indeterminate；包括写死参考调用编号、从 1 开始编号 |
| Recreation | task16 | indeterminate；检查器只认指定工具，遗漏旅行简报工具已经返回的区域、设施和 Hiking/Camping 活动证据 |
| Hugeicons | task4 | evidence_plan 生成两次服务端上下文超限，未执行 Qwen |
| OpenZeppelin | task1 | 写死参考文件路径；Qwen 已在其他路径导出文件，检查器引用不存在的路径导致整单 indeterminate；实际内容仍需独立核验 |
| OpenZeppelin | task10 | 写死参考调用编号导致整单 indeterminate；另有多项未经实际数据判断直接通过的严重问题 |
| OpenZeppelin | task11 | 调用编号从 1 开始，与运行时从 0 开始的约定冲突，导致整单 indeterminate |
| OpenZeppelin | task14 | subtask_plan 首次 JSON 格式错误，重试后条款覆盖校验失败 |
| OpenZeppelin | task6 | fail；包含回答中的数据错误，例如低票权钱包数量及投票距截止时间的表述 |
| OpenZeppelin | task8 | fail；将禁止重复初始化误述为幂等；另有四项 indeterminate，涉及工具路径限制和第三个工程依赖证据缺口 |

整体六个 indeterminate 中，五个由证据引用错误触发。当前外层捕获异常后将 requirements 清空，其他检查结果随之丢失。11 份已生成 verifier 的规格均无 preservation 或 execution_integrity 条目，取消副作用检查不能解决这些主要问题。

后续应优先检查：依据实际执行动态定位证据、接受等价证据、禁止未经判断直接通过、隔离单项检查错误，以及恢复适当的可执行验证。以上问题本次仅记录，未修改 verifier 实现或放宽质量要求。

## JSON 解析调整

公共 `parse_json_object` 在对象未闭合时，仅尝试追加末尾缺失的引号及配对括号，再由标准 JSON 解析器校验。不补字段、值、逗号；错配括号及未完成转义仍报错。原始 LLM 回复日志保持原样。

这是语法恢复，不能证明被截断的业务内容完整；下游契约和内容检查仍然必要。
