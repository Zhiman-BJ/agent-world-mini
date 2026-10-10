# main 合并试验

## 最新调整：公共实现以远端为准

用户在初次合并后明确要求：功能相同但实现不同的公共部分采用远端版本，由我们的调用端适配。下文“冲突处理”记录的是首次合并 `d676a0d` 的决策；本节覆盖其中保留本地公共实现的部分。

- `harness/`、`env_gen/tool_gen/`、`task_gen/program/`、`task_gen/task_eval_kimi.py` 已与远端 `8e4dd5a` 逐文件完全一致。包括字节及换行，不仅逻辑一致。
- 删除本地共享沙箱的另一套Docker执行、镜像源和启动修复；不再通过修改上游来满足DAG调用端。
- 撤销上一轮Program参数异常兼容修改：Harness恢复原来的抛错契约后，Program无需修改。
- 新增下游 `task_gen/tool_runtime.py`：将Harness抛出的参数异常转换为MCP/ReAct可见的错误记录；Docker通过上游Program已有的 `_docker_call_tool` 执行，使用其binding和worker协议，不另写容器执行器。超时转换为timeout记录，外层事务仍负责失败回滚。
- MCP、ReAct及历史固定链执行入口改用该适配层；Step3传入binding路径。没有修改任务设计、链长门槛、复核策略或最终正文规则。
- 此适配层依赖上游Program的内部Docker函数签名，未来上游调整该接口时需要同步检查。未启用上游的Docker兼容旁路环境开关。

### 本轮验证结果

第一组：`test_taskgen_runtime_adapter test_harness test_step3_single_session test_tool_graph_execution test_tool_resources test_task_eval_react test_program_form_v2 test_tool_delivery_runtime test_tool_software test_software_profile_versions test_mcp_integration`：115通过、3失败。

第二组：`test_execution_resources test_task_eval_kimi test_tool_result_reader test_codex_agent_client`：13通过、23跳过。

3个失败在纯远端 `8e4dd5a` 工作区同样复现：

1. `test_profile_native_library_paths_reach_tool_sandbox`：沙箱未传递profile原生库路径。
2. `test_tool_sandbox_resolves_its_user_id`：缺少系统用户记录，getpwuid失败。
3. `test_native_shutdown_logs_do_not_extend_tool_response`：退出日志追加到JSON后导致Extra data。

这是采用远端后重新暴露的既有问题，相比第一次整合版确实失去了对应本地修复。测试没有删除、放宽或标记为预期失败。后续如果要修，宜在共享实现中单独讨论修复，不能宣称当前版本全绿或与实测f030985完全等效。

新增适配测试覆盖非法资源参数回传后继续成功调用、复用上游Docker执行器且失败回滚、Docker超时记录。Docker测试使用模拟worker验证接线，未实际启动交付镜像；Kimi/镜像等外部依赖测试有跳过。没有运行付费模型、没有更新main或推送。

本地基线：`f030985`（GPT6sol 十环境实测的 Harness 适配版）。
远端基线：`8e4dd5a`（2026-10-02，ShuoSun-CN）。
整合分支：`integration/main-20261010`。本次只在独立工作区整合，不更新 main、不推送。

## 冲突处理

Git 报告14个冲突文件。共同接入 Harness 造成多数重复实现；主要处理如下：

- 保留本地单会话 Step3、执行复核及链长逻辑。核心 `execution_agent.py`、`step3_single.txt`、`step3_review.txt` 及 Step2、4、5与本地基线一致。
- 保留本地 binding 必需约束、Docker runtime 传递、无密码环境之外独立的 Kimi 文件访问策略、Codex 持久会话修复。
- 保留本地 Docker 镜像源配置、用户信息挂载、Python profile解释器路径与原生库退出处理。
- 采用远端软件环境管理与场景种子schema、发布入口、包发现配置及Scope相对路径文档。
- 合入远端容器内部procfs处理，保留本地Docker直接执行路径。
- 历史固定链执行器同时保留本地runtime参数和远端process_limit、资源路径校验，避免自动合并留下未定义变量或丢失参数。
- 整合MCP测试：保留单会话应答结构，采用远端真实venv夹具；去掉双会话测试的1次调用配置，遵循实际单会话最低11次要求。
- Program运行时保留非法资源参数的ValueError接口：共享Harness现在将这类异常转为invalid_arguments记录，Program需将其恢复为原先调用接口预期的异常类型。修改2行，原有资源测试覆盖。
- 其余Program实现从远端原样合入，不修改生成策略。

## 验证

使用已有Python虚拟环境，串行运行，无模型调用、无新任务生成。

第一组：`test_harness test_mcp_integration test_task_eval_kimi test_tool_result_reader test_step3_single_session test_tool_graph_execution test_execution_resources test_state_runtime test_tool_software test_software_profile_versions test_docker_runtime test_tool_delivery_runtime test_codex_agent_client test_program_pipeline_skeleton`。

结果：**108通过、28跳过**。跳过项不能计入已验证能力，包括外部运行环境依赖项。

第二组：`test_tool_resources test_record_store_contract test_tool_validation_contract test_program_form_v2 test_tool_delivery`。

结果：**25通过、5失败**。五个失败在独立检出的远端 `8e4dd5a` 上全部复现（两个对应测试文件：1通过、5失败），不是本次合并新增：

1. `test_shared_record_behavior[<lambda>0]`：测试使用limit=10000，ToolGen记录接口限制为1..1000。
2. `test_tool_requires_success_and_checked_result`：测试期待缺少成功测试时拒绝，现有实现返回无错误。
3. `test_write_checks_actual_record`：测试期待终态记录不匹配时拒绝，现有实现返回无错误。
4. `test_normalized_chain_preserves_record_assertions`：expected_records未保留，触发KeyError。
5. `test_container_plan_calls_image_preparation`：测试期待build_image调用，现有路径未调用。

以上测试/实现分歧没有在合并中擅自修复，应由相关模块确认期望行为。不能宣称全仓测试通过。

Git冲突索引已清空。远端历史文件含CRLF和尾随空格，完整diff --check因此有已有格式告警；不为消除告警重写大量远端文件。本次手动兼容修改单独检查无空白错误。

## 结论与边界

可以合并，已得到可提交的整合版本；主要难点是保留两侧不同调用接口，而非任务生成prompt冲突。当前检查未发现待修复的合并新增失败，但不等于完整生产回归通过。

未重跑真实环境、未使用付费LLM、未完整执行全部仓库测试、未验证所有Docker/Kimi实机路径。main和远端均未被此试验改动。
