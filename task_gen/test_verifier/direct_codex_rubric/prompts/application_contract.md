沿用原始 task_eval_verifier 的调查流程：先阅读 request，再独立调查，将逐项证据和结论记录在 scratch/investigation.md，然后按本方法独立生成且固定的 rubric 评分。不提供预生成静态工具。
最终必须输出 {"task_id":"与 request 一致","outcome":"pass 或 fail","summary":"决定性依据","rubrics":[{"rubric_id":"R1","score":1,"reason":"依据固定标准的理由","evidence":[{"path":"evidence/actual/answer.txt","locator":"行或记录","finding":"观察事实"}]}]}。
task_id、outcome、summary 和 rubrics 都是必需字段；不要使用其他方法的仅 rubrics 输出格式。证据必须是调查目录下 evidence/ 或 scratch/ 中实际存在文件或目录的相对路径；证明缺失时保存目录清单或引用已搜索的目录。
每项恰好一次；binary 为整数 0/1，ordinal_5 为整数 0..5。权重由宿主汇总。可修正自己的调查脚本，不改候选产物和标准；无法可靠继续时输出 {"verification_error":"原因"}，由宿主整任务回退。

主 Codex 根据原任务和核实事实给出整体 outcome，并用 summary 解释：核心未完成或实质错误为 fail，非阻断性局部缺陷可为 pass。不设总分门槛、不改变固定 rubric 分数；总分由宿主汇总。
