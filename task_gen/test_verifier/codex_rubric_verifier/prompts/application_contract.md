沿用工具框架中的单个主 Codex 检查方式，但不提供预生成静态工具，也不接收其他方法的评分标准。
最终仅输出 {"outcome":"pass 或 fail","summary":"决定性依据","rubrics":[{"rubric_id":"R1","score":1,"reason":"依据固定标准的理由","evidence":[{"path":"evidence/actual/answer.txt","locator":"行或记录","finding":"观察事实"}],"tool_call_ids":[]}]}。
证据路径以 evidence 根或应用 scratch 为基准，实际可用路径由宿主提供；不存在的文件不能作为证据，证明缺失时先保存目录清单并引用。
每项恰好一次；binary 为整数 0/1，ordinal_5 为整数 0..5。权重由宿主汇总。意外验证错误立刻停止并返回 {"verification_error":"原因"}，由宿主整任务回退；正常零分继续检查。

主 Codex 根据原任务和核实事实给出整体 outcome，并用 summary 解释：核心未完成或实质错误为 fail，非阻断性局部缺陷可为 pass。不设总分门槛、不改变固定 rubric 分数；总分由宿主汇总。
