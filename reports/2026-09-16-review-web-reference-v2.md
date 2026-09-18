# 新版真实任务搜索 prompt：单候选试跑

代码提交：`378559f`。仅替换开启搜索时的新增段落为用户确认的版本，其他 prompt 未改。

复用上一轮 OpenZeppelin task10 的 Step2 输入，sol / low、方案种子 42、单候选、搜索开关开启。旧 /tmp 环境已不存在，改用仓库 artifacts/upstream_20260911 中同版本环境；运行前比较 state 签名，与上一轮初态完全一致。

## 结果

- Step3 执行完成，程序 success=true；未运行 Step4/5 或独立 verifier。
- 耗时 740.427 秒，约 12 分 20 秒。
- 原始轨迹 28 次：方案抽样 5 次（其中 4 次参数错误），业务调用 23 次（其中 1 次读取行范围失败）；模型报告有效业务调用 22 次。
- 已确认实际留档 prompt 包含新版“请自行使用网络搜索”要求，但全程没有 web_search 事件，也没有网络来源引用。环境工具 search_governance_proposals/search_governance_votes 是本地记录查询，不是网络搜索。
- 因本轮未发起搜索，不能把本轮未搜索归因于搜索端点的 503，也不能声称采用了外部参考。确切原因尚未确定。
- input_tokens=536542，其中 cached_input_tokens=466176；output_tokens=3163，其中报告 reasoning_output_tokens=288。不能把整轮成本当作搜索增量成本，也不能据两轮差值归因于 prompt。

最终 objective 原文（尚非 Step4 用户任务文本）：

> 审阅 ENS Snapshot 提案“[7.1] [Social] SPP3: Marketplace RFP”及其关联投票，核验选项与聚合计票一致性，统计参与、选择分布、投票权集中度和理由覆盖率，重算计票并报告异常，同时附带独立的 ERC-4337 账户抽象源码流程观察，最终生成可追溯 Markdown 和 JSON 审阅材料。

模型完成了本地治理统计、计票复核、源码观察以及两份产物导出。但目标仍然并列组合治理审阅与独立账户抽象源码观察，没有提供二者共同业务动机，不能认为已达到“从真实任务参考中形成自然整体”的预期。也没有证据表明这次 prompt 使任务更真实。

## 产物

- [完整结果与调用](../runs/review_web_reference_v2/20260916_213640_927971_smithery_openzeppelin_25_gpt-5.6-sol/tasks/task10/agent_result.json)
- [实际 prompt、回答、usage、事件](../runs/review_web_reference_v2/20260916_213640_927971_smithery_openzeppelin_25_gpt-5.6-sol/llm_calls.jsonl)
- [配置与阶段时间](../runs/review_web_reference_v2/20260916_213640_927971_smithery_openzeppelin_25_gpt-5.6-sol/run.json)

未继续调整 prompt；保留此轮供讨论。
