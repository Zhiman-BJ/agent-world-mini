# Final Codex Repair Implementation Plan

**Goal:** 两条生成管线增加一次 Codex 整包修复会话，并完成同一批 20 任务各应用两次。

**Spec:** `verifier_final_review_prd.md`。

复用现有 GenerationModel、隔离执行、应用器、API metering；不添加设计/实现修复路由。

- [x] 提交现有代码和文档基线；运行既有宿主测试。
- [x] 先写失败测试：最终验收可改整个包、低分可交付、异常不回退、验收失败不交付、失败实现可交最终会话补全。
- [x] 新增 `final_review.py` 和简短 prompt；扩展生成会话以支持整包修改和真实试跑；宿主独立验收并保存哈希。
- [x] 两个 generate 入口接入最终会话；保留修改前快照、所有失败与成本；更新说明并通过相关测试。
- [x] 新增可恢复批跑入口；同一包生成一次、应用两次，生成与应用分别计量，保留错误。
- [ ] 用既有 20 个任务跑两方法；完成汇总 HTML 和问题说明。

测试命令：`.venv/bin/python -m pytest task_gen/test_verifier/program_verifier/tests task_gen/test_verifier/codex_tool_verifier/tests task_gen/test_verifier/tests -q`。
