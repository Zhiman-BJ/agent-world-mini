# 当前任务执行验证流程（2026-09-08）

本文件说明本次合并版本。旧设计、工作日志和实验分析保留为历史记录；其中多轮审核、校准和重试流程并非当前默认行为。

## 当前实现

`task_gen/task_eval.py` 独立读取任务生成结果，为每项任务准备 verifier，再运行任务 Agent，最后根据实际执行证据验证。没有把测试插入任务生成 step 或调整其 pipeline/contracts。

verifier 在 Agent 执行前经四次模型调用生成：

1. specification：拆解任务要求。只有存在具体高风险破坏性或冲突性副作用时，才可额外增加一个 execution_integrity；不是每个任务强制添加。
2. evidence_plan：规划证据来源及对象关联方式。
3. implementation：生成 verify(ctx)，初稿不承受静态语法禁令；任务是唯一验收标准，参考执行的可选操作、参数和产物不能自动成为要求。
4. implementation_review：检查代码约束并进行同义替换，不重新设计业务判断。随后执行静态校验。

程序从 spec 组装 verifier.requirements，模型只生成 source。运行时必须为所有已列出的 requirement 返回结果。缓存版本为 15，旧缓存不会作为本版生成结果复用。

验证证据包括初末 workspace、实际工具调用及返回结果、Agent 回答。verify(ctx) 可以读状态文件和在终态副本中调用工具。确定性结果直接记录；semantic_requirement 将指定证据交给 LLM 判断。当前默认流程没有额外的失败结果 LLM 复核，也没有自动多轮校准重试。

## 运行前提

`task_gen/tool_graph/codex.py` 是本模块的 Codex 客户端；保留 utils 的共享实现。默认配置使用 Terra 和独立 `~/.codex-task-eval`。该目录及其中 provider 所需凭据须在运行环境提供；当前机器配置要求 TASK_EVAL_API_KEY。凭据不会提交到仓库。

## 最近真实试跑

`runs/task_eval_v53_optional_integrity/20260908_100656_965958` 保存四轮完整输入输出、新 spec、证据地图、生成代码和验证结果。使用 Terra，四次模型调用总计约 197 秒。

重新生成的 spec 只有三个任务要求。复用的 Agent 终态和参考终态分别在不同父目录完成任务，两者均通过；未执行任务的初态被拒绝。任务 Agent 没有重新执行，旧实际调用轨迹及最终回答因先前超时缺失，本例仅依赖状态。

已知局限：补充临时反例把目标转写标记删除但保留路径和标题时，生成 verifier 仍放行。用户已接受将其记为漏检边界，不以覆盖所有反例为完成标准。当前结果不能证明所有任务、所有模型生成都稳定正确。

## 提交检查

评测、工具图执行、生成、IO、集成和 Codex 客户端相关测试在分支上通过，共 166 项。全库测试仍有基线故障，包含 DataGen 测试数据与规则不一致及缺少 task_gen.program_form；系统 Python 3.10 还存在 jsonschema 版本与 Seed 脚本语法兼容问题。这些不纳入本次模块改动。

工作树的 runs 被 Git 忽略，保留在原位置；本次合并不删除工作树或试跑产物。
