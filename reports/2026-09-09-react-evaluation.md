# 独立评测 ReAct 适配与试跑

工作区：`.worktrees/react`，分支：`react`，基线：`c29d2ba`。

## 改动范围

仅替换 `task_gen/task_eval.py` 的最终独立任务执行 Agent。主管线、Step2 review、Step3 固定链执行、contracts 和 verifier 判定逻辑均未修改。

新增 `task_gen/task_eval_react.py`，借鉴 OmniaBench 当前 TaskGen-DAG 版本的 action/finish 循环：

- 模型只收到任务、公开环境与工具契约、已经调用工具的返回；不会注入状态文件内容或 internal.code。
- 固定使用 API 后端，不能因共享配置中的 `backend: codex` 再启动 Codex CLI。
- 工具执行复用 `call_environment_tool`：白名单、输入输出校验、bubblewrap 隔离、失败不提交状态等保持原有实现。
- 工具返回完整回传；最终答案为普通文本，维持现有评测器的答案接口。
- 每次请求只执行一个 action，工具调用预算维持默认 50。格式错误和超预算后仍不结束的响应受总轮数上限约束。
- Agent 原始请求/回答/usage 写入 `<workspace>.agent/llm_calls.jsonl`；工具调用写入同目录 `tool_calls.jsonl`，失败也保留。
- 独立评测增加 verifier 模型调用留档及每项耗时，便于区分执行失败、验证失败和服务失败。

最终工具调用 trace 与答案仍交给原 verifier；Agent 自称完成不代表评测通过。

## 自动验证

```bash
.venv/bin/python -m pytest -q \
  tests/test_task_eval.py tests/test_task_eval_react.py tests/test_pipeline_credentials.py
```

结果：**93 passed**。新增测试实际调用本项目沙箱工具执行，仅模拟外部模型响应，验证：

1. `exec_command` 不在工具列表时被拒绝；合法工具正常执行。
2. 状态文件中的隐藏内容和工具实现代码不进入模型请求。
3. 超过 1000 字符的工具返回完整保留，最终模型响应和 usage 落盘。
4. 即使配置为 Codex，也只能走 API；超预算后不会继续执行工具，失败日志保留。

删除了已经失效的 Codex CLI 启动参数测试，保留现有 verifier 测试。`git diff --check` 通过。
系统 Python 的 jsonschema 版本过旧，因此在此 worktree 创建了独立 `.venv`，没有替换系统依赖。

## 真实试跑

输入为主工作区最新完整产物：

`runs/openalex_v2_terra/20260909_101400_931630_toolgen-reality-openalex-run_gpt-5.6-terra`

该产物有 6 条正式任务。本次选择第一条 task1，模型为 `gpt-5.6-terra`，使用管线专用密钥文件；未使用或修改对话密钥。

| 尝试 | 结果 |
|---|---|
| 完整独立评测 `runs/react_eval/20260909_195229_953197` | 新增 verifier 日志目录未预创建，留档失败；已修复 |
| 修复后 `runs/react_eval/20260909_195345_041369` | verifier 首次模型请求收到 HTTP 503，Agent 尚未启动 |
| 直接调用 ReAct `runs/react_agent_smoke/20260909_195850` | 首次模型请求收到 HTTP 503，耗时 8.53 秒，工具调用数为 0 |

额外用极小请求检查 Chat Completions / Responses 的普通和流式接口，以及专用 Codex 配置使用的根路径，均收到 502 或 503。该证据不足以确定服务端根因，但说明不只是 verifier 的任务内容导致失败。未更换模型、降低验证要求或回退到 Codex。

**没有真实任务通过记录。** 当前能确认适配通过本地测试；无法确认真实模型能顺利完成最新任务，更不能声称 verifier 已通过。Agent 失败请求的原始上下文和错误位于：

`runs/react_agent_smoke/20260909_195850/state.agent/llm_calls.jsonl`

完整评测失败位于：

`runs/react_eval/20260909_195345_041369/results.json`

## 服务恢复后重跑

在此 worktree 执行：

```bash
.venv/bin/python -m task_gen.task_eval \
  --input-root /data1/home/tianfang/agent-world-mini-zhiman/runs/openalex_v2_terra \
  --output-root runs/react_eval \
  --config config/tool_graph.yaml \
  --model gpt-5.6-terra --backend api \
  --limit 1 --max-concurrency 1
```

该命令连 verifier 一起使用现有 API 后端；代码没有改变 verifier 的后端选择逻辑。去掉 `--limit 1` 可评测该输入目录最新产物的全部 6 条任务。
