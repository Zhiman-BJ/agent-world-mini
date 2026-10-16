# SDD ledger — plan: task_gen/test_verifier/codex_tools_implementation_plan.md

Ruling: 继续现有 feat/program-verifier-20261014 实验分支，不提交推送；已有文件未跟踪，另建 worktree 不会包含这些输入。
Ruling: 最新 PRD 优先于旧计划；工具可直接评分，模型仅主 Codex，实跑四例。

| 接口/任务 | 核对与决定 |
|---|---|
| 契约 → 生成/应用 | 四个设计文件；固定 rubric；static/codex 两种 method |
| 工具 → 应用 | CLI/socket 调用；宿主日志；首错停止；明确正常缺失 |
| 生成 → 应用 | 私有文件实现；标准冻结；宿主复跑自测 |
| CLI → 统计 | generate/apply/validate-reference；四例；保留失败与真实成本 |
| 契约/工具任务 | 雏形已有；补齐最新语义和边界测试 |
| 生成任务 | 新增四文件设计检查，保留旧生成器默认行为 |
| 应用任务 | 新建主 Codex worker 与宿主监督；失败完整 fallback |
| 交付任务 | 四案例实跑，不新增负例、不宣称准确率 |

Task 1: complete — 契约更新 static/codex，包编译与调用引用检查；含坏JSON回归测试。
Task 2: complete — CLI/socket 宿主解析参数、独立日志、超时、证据只读与线程收尾。
Task 3: complete — 唯一主Codex、只读包/证据/日志、错误停止和完整fallback；真实沙箱机制测试通过。
Task 4: complete — 四文件设计冻结、逐工具私有生成、自测和宿主复跑；旧GenerationModel仅增加两个可覆盖属性。
Task 5: complete — CLI/README/config就绪，独立review完成，3项问题已修复；四例真实生成和应用均完成，结果与成本已留档。
Run: task_gen/test_verifier/runs/codex_tools_v1_20261015；exec session 93432；S21/S24/S91/S81，case_concurrency=2。
Validation: 最终新旧77项统一通过（38.67s）；审查56项及增量统计回归通过；编译和三CLI帮助检查通过。
Supplement: S91参考文件由其他用户持有且无读取权限，保留证据准备失败；补跑S56，目录runs/codex_tools_v1_20261015_supplement，exec session 96943。准备失败从生成尝试/失败中单独统计。

Final: S21=0.980，S24=1.000，S81=0.516，S56=0.948；生成4/4，主流程4/4，满分1/4，回退0；静态工具1个、调用2次（2.55s），另3例零工具。
Report: codex_tools_first_run_results.md；合并原始统计 runs/codex_tools_v1_20261015_combined/。未测负例或成本对照；非满分未调整门槛/重生成。
