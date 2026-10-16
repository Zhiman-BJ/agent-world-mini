What this change does: 新管线先固定 rubric 和工具契约，再逐工具生成、自测，由唯一主 Codex 调用静态工具验收。宿主负责只读隔离、调用留档、固定聚合及首错停止后整任务回退，不替换原默认 verifier。

审查范围：直接读取未跟踪的 codex_tool_verifier 全部源码、prompts、tests、README、run_config，以及 program_verifier/generation/model.py 和相关调用方。按两个案例并发、每例两个工具并发审查。审查期间将明确问题交回实现者集中修改；本审查者未修改实现、未委派、未提交。

## Must fix（审查中发现，现均已修复并复测）

1. **P1：真实客户端无法写最终回复，正常应用也会回退**（`task_gen/test_verifier/codex_tool_verifier/application.py:22`、`:179`、`:184`）
   - 原因：整个 output 只读，包括真实 `_VerifierClient` 的 `logs/codex/run_01/last_message.txt`；CLI 的 `--output-last-message` 必须写这个文件。旧测试替换客户端，未覆盖真实客户端与沙箱的交界。
   - 具体证据：使用真实客户端、bwrap 和仅写最终回复的假 CLI，复现 `OSError: [Errno 30] Read-only file system: .../logs/codex/run_01/last_message.txt`；无需调用模型。
   - 最小修复：宿主在日志中创建只读符号链接，将最终回复内容写入 scratch；保持 stdout、stderr、工具日志只读。实现者已加入 `_MainClient`，真实基类为 `task_gen.tool_graph.codex.CodexAgentClient`。
   - 复测：新增 `test_real_client_can_write_final_message_without_writable_host_logs` 经过真实客户端、假 CLI、bwrap、CLI/socket 工具调用，成功返回 1 分且不能写宿主日志，usage 原样留档。若不修，正常生成包仍无法完成主验收。

2. **P2：合法满分因浮点运算顺序超出 1，满分统计漏计**（`task_gen/test_verifier/program_verifier/contracts.py:100`；消费者 `task_gen/test_verifier/codex_tool_verifier/validate_reference.py:22`）
   - 原因：旧式 `weight * score / maximum` 在权重 0.11、ordinal_5 分数 5 时产生 `1.0000000000000002`；统计使用 `score == 1`，把全满分记成 0 个满分，且返回分数越出 PRD 的 0–1 范围。
   - 最小修复：先算 `score / maximum`，再乘权重。实现者已修改共用聚合器，没有调整评分门槛。
   - 复测：同样输入现在返回 `1.0`，`reference_full_score=1`；新增 `test_ordinal_full_score_is_exactly_one` 已通过。若不修，会错误报告参考验收成功率。

3. **P2：Codex 尚未启动就失败，仍计作主模型调用**（`task_gen/test_verifier/codex_tool_verifier/application.py:132`、`:165`、`:187`）
   - 原因：旧代码以宿主 worker 的 Popen 是否存在判定 `main_started`。配置不存在的 Codex executable 时，仅 worker 启动，模型没启动，仍得到 `main_started=true` 和一条模型调用成本，污染回退分母。
   - 具体证据：`codex.executable=/definitely/no/codex`、mock 整任务 fallback，复现 `Codex executable not found` 同时 `main_started=true`、`recorded_model_calls=1`。
   - 最小修复：worker 留档模型启动标记，由应用层使用该标记判断统计和成本。实现者已加入 `started`。
   - 复测：新增 `test_missing_cli_does_not_count_as_main_model_call` 已通过，失败仍保留整任务回退，`main_started=false`、`cost.calls=[]`。若不修，启动率、回退率和调用数会失真。

## 验证证据

- 最终复跑：`python -m pytest -q task_gen/test_verifier/codex_tool_verifier/tests task_gen/test_verifier/program_verifier/tests/test_generation_workspace.py task_gen/test_verifier/program_verifier/tests/test_generation.py`：**56 passed in 30.48s**。
- 新管线测试覆盖实际 CLI/socket、输入输出 schema、正常 missing、零分、非法 JSON、只读证据/标准/宿主日志、工具异常/超时、先停止主流程后整任务 fallback、fallback 失败、设计冻结、逐目标交付、自测真实入口和返回值验证、宿主复跑。旧生成机制 18 项通过，`design_files`/`check_module` 默认仍是原两文件与原检查模块。
- 独立并发补测：T1 一个调用进入 20 秒等待，另一个调用抛出意外错误；首错调用后 **0.926 秒**两个调用均结束，均有终态调用记录，等待工具未留下后续写入，没有发现取消/日志竞态。
- 15 个 Python 文件编译检查通过；generate/apply/validate-reference 三个子命令 `--help` 均正常。
- 已确认默认预算 900 秒，首批默认 S21、S24、S91、S81；required_tools 仅用于无条件必调，条件适用/替代证据由 evidence_plan 和主 Codex 判断，正常 missing/not_applicable 不自动回退。

## 增量复核：证据准备失败的统计口径

- 复核 `codex_tool_verifier/validate_reference.py:18`、`:36`、`:47` 与新增 `tests/test_reference_report.py`。`stage=evidence_preparation` 单列 `preparation_failed`，不计入 `generation_attempted` 或 `generation_failed`；普通设计失败、生成成功仍正常计数。原始错误记录保留，报告明确写“证据准备失败，未开始生成”。这项修正符合实际执行阶段，未发现新增问题。
- 仅运行新增测试：`python -m pytest -q task_gen/test_verifier/codex_tool_verifier/tests/test_reference_report.py`：**1 passed in 0.75s**。另用单例准备失败的临时输入执行 `write_report`，确认生成尝试为 0、生成失败为 0、准备失败为 1，报告文字与原始 PermissionError 保留；断言通过。
- 本次增量未复跑大套测试、未调用模型；不将实现者另行报告的 76/77 项总数算作本审查者的实测结果。

Verdict: Ship. 上述三项必要修复已落地并通过机制回归，目前未发现其他需阻断首轮实验的问题。

Not checked: 未发起真实模型调用，未评价四例生成质量或错误识别准确率；实际首轮仍可生成失败、回退或低分，需保留原始结果。
