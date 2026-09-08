# Objective 纯生成与多子任务修正

用户澄清的阶段顺序是：采样链 -> 生成 objective -> review 审查。objective 不承担审查、接受或拒绝职责，一个目标可以组合多项独立业务子任务。

## 代码变化

- 目标生成只返回 `{"objective":"非空任务目标"}`，删除 accepted、reason 和语义拒绝分支；调用或输出契约失败单独计入 objective_error_count。
- 每条成功生成的 objective 原样进入 review，并保持冻结。统计使用 objective_generated_count，移除 objective_accepted_count 和 objective_rejected_count。
- review 允许各子任务涉及不同业务和对象，独立查询结果也可以是交付物。按各项要求能否完成审查，而非要求它们必须共享实体或相互依赖。
- 逻辑评分、任务转写、反思及最终校验同步接受多项独立子任务，仍检查事实依据、处理范围、调用贡献和执行覆盖。
- 沿用原有链长、采样、多样性和最小修补机制；未修改用户 skill 文件。

## 验证

`python -m unittest discover -s tests -p 'test_tool_graph*.py'`：84 项通过。检查 objective-only 输出进入 review、多子任务目标原样传递、旧拒绝格式记为契约错误、review 仍可拒绝不可行链及冻结目标不能被替换。

真模型对照目录：`runs/objective_generation_only/20260906_180556_850157_bugagent_gpt-5.6-terra`。复用上轮相同的 20 条原始链及初态观察，不重新采样或探索。只运行 objective 生成，后续 review 和执行质量不在本次实验的结论范围内。完整回复保存于 llm_calls.jsonl，逐候选目标与错误保存于 run.json。

最终结果：20 次请求中 17 条成功生成 objective，3 条 Codex 请求超过 180 秒超时；无语义拒绝，也无已返回结果的格式错误。旧轮仅 2 条生成目标、18 条语义拒绝的路径已移除。超时属于调用失败，不计为链审查拒绝。

已观察到生成器将 SARIF 分析、JUnit 缺陷处理、测试资产创建分别写成子任务，无须虚构其间的对象关系。部分目标仍带有过宽的“逐项”范围、过多执行细节或按需执行条件，这些应由后续 review 对照固定调用链判断，不能以“生成成功”代替可行性结论。
