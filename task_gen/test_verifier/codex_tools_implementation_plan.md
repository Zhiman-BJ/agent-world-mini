# Codex 静态工具 verifier 实施计划

> 执行方式：本会话按 executing-plans 逐项完成；现有 feat/program-verifier-20261014 分支继续承载实验，不提交、不推送。

**Goal:** 实现 codex_static_tools_verifier_prd.md 的独立生成、应用和四例参考验收。

**Architecture:** 复用已有证据准备、Codex 客户端和固定聚合；生成任务专属 rubric 与工具。主 Codex 通过 CLI 向宿主 socket 请求工具调用，宿主隔离执行、独立记录并监督首错停止，失败整任务回退。

**Tech Stack:** Python、现有 jsonschema、bubblewrap、Codex CLI。

**Spec:** task_gen/test_verifier/codex_static_tools_verifier_prd.md。

## 共同约束

- 不调用或导入 task_gen.program；不修改默认 verifier。
- 工具执行固定规则，不调用模型、不改证据；完整覆盖 rubric 时可直接评分，部分覆盖只返回事实；语义由主 Codex 判断。
- 模型在单会话内写文件、自测、修正；宿主独立复跑。无外部自动修复轮次。
- 证据和标准只读；异常整任务回退；零分正常汇总；参考不是唯一答案。
- 不生成任务负例；测试宿主机制。所有文件位于 task_gen/test_verifier/。

## 文件与调用关系

新建 codex_tool_verifier/{contracts,generation,check,tool_runner,application,validate_reference,__main__}.py、prompts/、tests/、README.md。
生成器交付 verifier/{rubrics.json,tools.json,instructions.md,coverage.json,tools/T*.py}，不生成 main.py。
现有 program_verifier/generation/model.py 仅增加可覆写的交付文件列表与检查模块，默认行为不变；新生成器继承其文件交付与隔离验收机制。

## 1. 契约与聚合

- [x] tests/test_contracts.py：设计原文引用、schema、覆盖、工具关联、分数和跨会话调用引用。
- [x] 运行 pytest，观察缺模块失败。
- [x] contracts.py：validate_package(root, task_text, design_only=False)；validate_results(plan, results, calls, evidence, scratch)。复用原固定加权与证据校验。
- [x] 复跑 tests/test_contracts.py，确认权重/缺项/非法引用会被拒绝。

## 2. 工具分发、隔离与监督

- [x] tests/test_tools.py：真实 CLI/socket 调用；可读证据、不可写证据；非法输入、错误输出、超时和取消。
- [x] 运行 pytest，观察缺实现失败。
- [x] tool_runner.py：ToolServer(session, config)、CLI 客户端、隔离工具 worker；宿主持有调用日志和错误事件，静态工具无模型凭据、无网络。
- [x] 复跑 tests/test_tools.py，检查调用记录与首错停止。

## 3. 主 Codex 应用

- [x] tests/test_application.py：替换模型边界，保留真实监督和工具进程；成功、零分、工具错即停、整任务回退失败。
- [x] 运行 pytest，观察缺实现失败。
- [x] application.py：apply(verifier_dir,evidence_dir,output_dir,config)，启动主 Codex worker、监督工具、验证结果与原始调用引用；fallback 使用现有 verify_execution。
- [x] 复跑测试，确保无局部评分混入 fallback，成本含失败调用。

## 4. 文件式生成

- [x] tests/test_generation.py：设计四文件、逐目标私有实现、冻结契约、自测复跑。
- [x] 运行 pytest，观察缺实现失败。
- [x] generation.py、check.py 与两个 prompt：继承现有文件交付客户端；设计明确 schema、成本理由和缺失政策。工具逐文件生成，不允许工具互相依赖。
- [x] 复跑新旧生成测试，确认默认行为不变。

## 5. CLI、参考运行与交付

- [x] __main__.py 提供 generate/apply/validate-reference；validate_reference.py 记录所选四例原始结果、静态实际调用、模型 usage、耗时和失败。
- [x] README.md 给出命令；PRD 消除“静态工具内调用模型”的矛盾，更新实现状态。
- [x] 新旧机制测试 + compileall + 三个 CLI --help。
- [x] 运行四例参考验收，保留失败，不调门槛迎合参考；输出 RESULTS.md/summary.json。真实工具未被调用不宣称加速。

未测任务负例，不评价错误识别准确率。模型供应方未报告的成本记 unknown。
