# 文件式生成任务
阅读 task_gen/test_verifier/codex_static_tools_verifier_prd.md，尤其生成流程、rubric/工具契约、prompt 要求。负责实现：
- codex_tool_verifier/generation.py、check.py、prompts/design.md、prompts/implement_tool.md、tests/test_generation.py。
- 如需复用旧 GenerationModel，只在 program_verifier/generation/model.py 增加可覆盖的设计交付文件集合和 check module 两个类属性，默认保持原行为；新 generation 使用子类。
- 新包已有 contracts.validate_package(root,task_text,design_only=False)、validate_tool_file(root,tool)、DESIGN_FILES；返回 rubrics/tools/coverage。
- generate(evidence_dir,output_dir,config=None,model_call=None) 接口与旧一致；生成器冻结四个设计文件，逐工具私有实现，只复制目标；支持零工具。不生成 main.py。所有模型判断主 Codex，工具可以对完整 rubric 直接评分。
- check.py 由 Codex 和宿主复跑：设计阶段验证四文件、设计自测；实现阶段实际 profile 目标函数已执行，再校验输出 schema（由自测负责实际规则）。自测不放进交付包。每次生成允许会话内修复，宿主不外部重试。
- prompt 给 Codex 目标让其直接写文件、自测修复，不要求一次回复JSON。明确路径、数据身份、schema、输出空间、缺失政策，避免过度静态化自然语义。任务原文 exact quote，固定标准；工具不能硬编码参考答案评分。参考可用于恰当对照。
- tests 有意义验证设计冻结、逐目标、零工具、失败不重试、真实 profile 自测；运行新旧生成相关测试。
不得编辑 contracts.py/tool_runner.py/application.py/CLI。不得委派子代理。不要 commit，不要 push。结果报告 task_gen/test_verifier/codex_tools_generation_report.md，回复只给摘要和测试证据。
