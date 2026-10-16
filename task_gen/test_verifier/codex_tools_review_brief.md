# 首批实现审查
按 task_gen/test_verifier/codex_static_tools_verifier_prd.md 审查当前 codex_tool_verifier/ 所有源码、prompt、tests，以及 program_verifier/generation/model.py 本次新增 design_files/check_module 的默认兼容性。
这些文件未跟踪，直接读文件而不是 git diff。不要修改，不委派、不提交推送。
核心：固定rubric先设计；主Codex优先调用静态工具，工具完整覆盖rubric可直接评分；所有模型判断只有主Codex；预料外错误立即停止主Codex及在途工具，整任务现有verify_execution回退；正常missing/not_applicable按契约。证据/包/标准/宿主日志只读，只有scratch可写。设计和实现会话自测，宿主复跑，不外部生成修复。
检查接口一致、并发/停止/日志竞态、统计真实性、实际文件路径/调用形式是否能跑。预算默认900秒，四例S21 S24 S91 S81。允许首轮生成失败、低分，不要求提高准确率，不生成任务负例。暂未实跑模型。
运行需要的机制测试。只报具体影响正确性或PRD的问题，不发散。报告 task_gen/test_verifier/codex_tools_review_report.md，列严重度/文件行号/原因/最小修复。返回简短结论。
