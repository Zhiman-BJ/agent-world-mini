# 本地 Qwen 部署与独立评测

## 正确任务范围与 ReAct 实测结果

用户要求的是部署 Qwen，用它运行 ReAct，测试现有任务的完成能力；不要求重新生成 verifier。
此前启动 verifier 生成属于我的理解偏差。已有产物按用户要求全部保留，不再继续生成。

ReAct 已独立完成最新 OpenAlex 产物的 task1：
`runs/qwen_agent_smoke/20260910_110300`。
直接使用正式 `run_react_agent`，未提供参考答案、参考调用链或隐藏初态内容。
没有生成新 verifier，也没有自动评测通过结论。

- 墙钟时间：414.17 秒（6 分 54 秒）；其中模型请求累计 409.61 秒。
- 19 次模型请求，18 次环境工具调用，无调用错误，最后一次请求提交最终答案。
- 累计输入 571661 token，输出 4814 token（含推理 2545 token）。输入是每轮完整上下文累加，不能当成不同内容的数量；服务启用了前缀缓存。
- 完成论文定位、主题相关性和层级查询、同名作者辨别、作者画像与指标比较、来源与发表状态查询，以及 JSON/CSV 导出。
- 最终答案：该目录 `result.json` 的 `answer`；逐轮上下文与回答在 `state.agent/llm_calls.jsonl`，实际调用在 `tool_calls.jsonl`。
- 三份导出文件在 `state/filesystem_scopes/research_exports/`：`pfr_oa_analysis.json`、`pfr_oa_analysis.csv`、`pfr_oa_analysis_reusable.json`。

发现一个具体问题：第 16 次调用 `verify_research_export_against_records` 为核验
`work_count`，选择了 `source` 记录集的 count。这里应该核对来源关联的作品数量，
而不是来源自身的条数。本例两者恰好都是 1，所以返回 valid=true 不能证明核验方式正确。
模型最终回答也把这次核验描述为作品量核验成功。未修改输出或替模型补做工具调用。

## 部署

部署目录：`/data1/home/tianfang/qwen-service`。
服务：`http://127.0.0.1:8000/v1`，模型名：`Qwen/Qwen3.8-27B`。
使用 GPU 0、1，BF16，文本模式；本服务不使用其余 6 张卡。
18 份官方权重均已核对 SHA-256。模型固定 revision 为
`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`。

Python 3.12.13、vLLM 0.29.0、PyTorch 2.13.0+cu130、Transformers 5.16.1
及下载/编译缓存均位于部署目录内。停止服务用该目录的 `stop.sh`，启动用 `start.sh`。
服务启动后两卡各约占 43GB 显存，包含权重与缓存。

首次真实 API 测试返回“本地服务正常”，97 token，3.3 秒。
当前配置：128K 上下文、medium 推理、评测输出预算 32768 token，JSON 格式约束。
评测输入为 9 月 9 日 OpenAlex 最新完整运行中的 task1。

## 已完成试跑与发现

1. `runs/qwen_eval/20260910_005529_930945`：verifier 的 subtask_plan 请求
   持续 1513.7 秒，xhigh 推理触及 16384 token 输出上限，没有最终正文。
   vLLM 的 length 结束计数确认截断。管线记录为 indeterminate，Agent 没有启动。
2. `runs/qwen_eval_medium/20260910_103439_137657`：medium 推理返回完整正文，
   耗时 521.3 秒，输入 27279 token、输出 7286 token，其中推理 5160 token。
   正文内部引号未转义，JSON 解析失败，Agent 仍未启动。
3. 随后仅在本地配置开启 response_format=json_object，通过现有 LLM 接口传给服务。
   该约束保证 JSON 语法，不替代业务校验，不自动修补内容，不改变 verifier 判定规则。
   104 项评测与 LLM 相关测试通过。

第二轮输出还显示潜在质量问题：把“比较相关性”强化为必须包含排序及分差，
把“主题专长画像”强化为必须包含作品占比。这些内容尚未经过后续审查，不能当成
最终 verifier 的判断，也不能通过修改任务或降低校验要求来掩盖。

3. `runs/qwen_eval_json/20260910_104544_473882`：314.9 秒后返回合法 JSON，
   但 `requirements` 内混入字符串，若干 requirement 字段落在根对象，任务条款也被截断。
   输入 27279 token、输出 4039 token，其中推理 3542 token。
   `validate_verification_spec` 正确拒绝，结果仍为 indeterminate，Agent 未启动。
   这说明 JSON 语法约束不能保证内容满足契约。

随后把本地配置温度从 0.6 调整为模型随附 README 推荐的 1.0，
其余采样参数沿用官方 generation_config.json（top_p=0.95、top_k=20），
新一轮输出目录为 `runs/qwen_eval_official`。没有修改任务、verifier prompt 或校验标准。
该轮 `20260910_105229_935882` 共 946.23 秒：subtask_plan 通过，
evidence_plan 因返回结构不满足契约而失败。进程已自然结束，所有产物保留。

服务/试跑日志保存在部署目录 `logs/` 中，逐次 LLM 调用在各评测产物目录中。
