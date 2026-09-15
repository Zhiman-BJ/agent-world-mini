# Codex 自主调查 verifier：实现与验收

## 实现范围

在 `新verifier` 分支中原位替换 `task_gen/task_eval_verifier.py`。旧静态 verifier 保存在提交 `dd43b76`（静态verifier留档）。任务生成、参考执行及 ReAct 求解权限没有改变；替换的是任务执行之后的独立验收。

| 文件 | 职责 |
| --- | --- |
| `task_gen/task_eval_verifier.py` | 准备调查目录、只读证据沙箱、调查 prompt、Codex 调用、判定结构校验与重试、独立复核 CLI |
| `task_gen/task_eval.py` | 保留求解现场，调用新 verifier，分别记录任务判定与验证基础设施错误 |
| `task_gen/tool_graph/codex.py` | 为既有客户端增加可选命令前缀，以接入 bubblewrap；其他调用默认行为不变 |
| `scripts/run_codex_verifier_regression.py` | 将六个历史执行现场交给新 verifier，不重新运行求解器，不将预期标签提供给模型 |
| `tests/test_codex_verifier.py` | 检查沙箱、输出边界、重试、异常现场保留及错误归因 |

移除了依赖旧静态 verifier API 的三个历史一次性脚本及其过时测试；均可从留档提交恢复。`program_form` 中的另一套流程没有修改。

## 证据与权限

每个任务建立一个独立调查目录：

```text
request.json                 任务原文与证据索引
prompt.txt                   通用验收规则
evidence/
  initial/                   实际执行前状态
  actual/final/              实际执行后状态
  actual/answer.txt          实际最终回答
  actual/tool_calls.jsonl    完整原始调用记录
  actual/execution.json      执行错误与状态检查错误
  reference/final/           参考执行后状态（可选）
  reference/initial/         不同的参考初态（可选）
  reference/answer.txt
  reference/tool_calls.jsonl
  environment.json
  tools.json                 含工具实现
  state_runtime.py           供复制状态后的实验使用
scratch/
  investigation.md          逐项直接核查记录
  ...                       独立查询、实验脚本及结果
logs/
  prompt_NN.txt              每次实际发出的 prompt（含重试反馈）
  attempts.json              各次耗时及错误
  usage_NN.json              模型、token usage、执行命令次数
  run_NN/                   Codex 原始事件、最终响应与 stderr
verdict.json                 结构校验后的判定
```

原始运行目录不交给调查 agent。bubblewrap 仅挂载系统运行依赖、独立认证目录和调查目录；证据、请求、prompt 为只读。agent 可以在 scratch 复制状态、访问 SQLite、读取工具源码、用真实库检验产物。沙箱不能看到测试中放在宿主旁边的文件，也不能改写证据。网络保留供模型 API 使用，未实现网络目的地址白名单；网页搜索和 MCP 插件均关闭。

认证及 provider 从用户 `.codex` 读取到临时隔离目录，实际调用使用其中的 API；不修改原 `.codex`，不继承交互会话的插件、hooks 等配置。当前实测模型为 `gpt-6-astra`，独立于 ReAct 的 Qwen/Sol 配置。认证文件不放进调查产物，临时目录结束后清理。

## 判定与错误处理

任务文本是唯一验收要求来源；参考答案和参考链只是可能有错的证据。调查须区分工具宣称与直接观测，核查实际交付和答案是否满足任务。读取工具返回的 `success`、`all_valid` 或确认文件存在，不能替代检查要求的实际性质。

先识别要求，再查原始状态与交付物，写 `scratch/investigation.md`，最后提交逐要求判定。调查记录要求包含被测声明、检查方法、观察结果、证据位置与任务关系。代码检查该记录存在且覆盖输出中的要求编号，同时检查任务原文引用、证据路径、字段类型、总体结论与逐项结果一致。代码不声称能自动验证证据的语义正确性。

正常判定只有 `pass` / `fail`。环境或工具使任务未完成也属于任务 `fail`，在原因中说明；API、配置、证据准备或输出格式失败属于 `verification_error`，不会伪装成任务失败，也不是 `indeterminate`。

默认单次调查超时 1800 秒，最多 3 次尝试。错误反馈给下一次调用，保留先前日志与 scratch。求解器异常退出仍保留调用记录、部分状态和错误，再交给 verifier 调查。最终状态数据库损坏也不直接跳过验收。

## 实际回归中发现并修正的问题

第一批六例中，五例与历史人工复核一致，OpenZeppelin task1 被误判 PASS。模型已经查到文件缺项，但把“聊天里给出分析”与“另建报告文件”割裂验收。调整为结合完整任务判断工作与持久化交付的关系，同时避免要求每种格式都重复所有内容。独立复跑后 task1 判 FAIL。

同 prompt 再跑六例时，pymatgen task19 和医学 task15 出现新的漏判。原始命令记录确认：前者只列目录、读请求/回答/调用日志；后者也主要读调用日志。它们接受了工具自己的有效性和空查询声明，没有核查对象重建性质或原始映射数据。

因此将“调查—记录直接证据—判定”明确组织为必经流程，并增加调查记录的结构检查。规则没有包含材料科学、医学或特定字段的判定答案。此修改后重新运行完整六例，而不是只保留此前碰巧正确的结果。

## 最终六例验收

最终运行目录：`/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915`。

最终 **6/6 与历史人工复核一致**：2 PASS、4 FAIL，无验证执行错误，每例一次调用即提交有效判定。预期标签未进入模型输入。下列链接中的调查记录包含方法、原始证据位置及实际观察；同目录上级保存 `verdict.json`、只读快照与完整事件日志。

| 历史现场 | 被验收执行 | 最终判定 | 实际核查及没有重演的旧问题 | 耗时 |
| --- | --- | --- | --- | --- |
| Recreation task16 | Qwen | PASS | SQL核查235个营位、目标三晚覆盖与许可地址；确认研究任务可以准确交付无匹配和资料限制，不额外要求完成预订。局部类型/统计口径错误单列，不扩大验收要求。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/recreation_sol_retry_2_task16/scratch/investigation.md) | 374秒 |
| OpenZeppelin task1 | Qwen | FAIL | 独立重算67票并读取两种报告全文：聊天统计正确，但两份持久化产物整体缺失集中度等分析。没有用聊天内容补足文件交付。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/openzeppelin_sol_full_task1/scratch/investigation.md) | 241秒 |
| OpenZeppelin task6 | Qwen | FAIL | 独立计算低权重钱包为14个而非约20个，投票簇距截止约9.7小时而非2小时；原始日志含正确数据不能抵消最终异常分析错误。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/openzeppelin_sol_full_task6/scratch/investigation.md) | 394秒 |
| OpenZeppelin task8 | Qwen | PASS | 读取静态审阅内容，检查ZIP及6个成员，对比原件、长度和SHA-256；按任务实际要求接纳静态审阅和明确披露的工具链限制，没有凭额外要求否定完成。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/openzeppelin_sol_full_task8/scratch/investigation.md) | 351秒 |
| pymatgen task19 | Qwen | FAIL | 使用真实pymatgen解析原件、匹配结构并重建对象；CompleteDos.from_dict实际抛出缺少efermi异常。检查源码确认原验证工具未执行重建，拒绝采信all_valid=true。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/pymatgen_sol_full_task19/scratch/investigation.md) | 260秒 |
| 医学术语 task15 | 参考执行本身 | FAIL | 依据本地DataSUS记录明确给出的E110显示形式E11.0，关联WHO数据找到3条过渡候选；参考执行只精确查询E110却报告本地无候选。没有默认参考执行正确，也没有用额外调查补作其交付。[调查记录](/data1/home/tianfang/agent-world-mini-zhiman/runs/codex_verifier_investigation_20260915/medical_sol_full_task15/scratch/investigation.md) | 384秒 |

最终六例共执行 **67次调查命令**；Codex调用耗时累计2003.9秒，3并发运行。API事件累计报告 input_tokens=1,769,856，其中 cached_input_tokens=1,421,056；output_tokens=36,379。这里是多轮调用累计输入，包含反复输入的历史上下文，不是单个请求的上下文长度；缓存输入包含在总输入中，不能重复相加。数字只覆盖最终六例，不包括前述试验。

## 使用

正常运行 `task_gen.task_eval` 时，ReAct 求解后自动接入新 verifier。其配置读取 `llm.verifier`（可选的 `codex_home`、`model`、`reasoning_effort`、`timeout_seconds`、`attempts`）；不设置时沿用 `.codex` 的模型和 API。

独立复核已有现场：

```bash
python -m task_gen.task_eval_verifier \
  --request execution_request.json \
  --output runs/independent_audit
```

请求文件传入 `task`、`environment`、`initial_state`、`actual_state`、`calls`、`answer`、`execution`；参考状态、调用、答案可选。路径相对请求文件解析。输出目录必须是新目录，防止覆盖已有证据。

重跑六例：

```bash
python scripts/run_codex_verifier_regression.py \
  --runs /data1/home/tianfang/agent-world-mini-zhiman/runs \
  --output runs/new_regression \
  --workers 3
```

本次实测 Python：`/data1/home/tianfang/agent-world-mini-zhiman/.venv/bin/python`。系统需要已安装 Codex CLI 与 bubblewrap；没有 bubblewrap 时拒绝无沙箱降级。

## 验证边界

相关自动测试：**52 passed，9 subtests passed**。覆盖真实 bubblewrap 文件隔离、证据只读、缺少调查记录时退回、路径越界、结果不一致、格式重试、求解中断现场、验证服务异常及现有 ReAct/Codex 调用兼容性。

```bash
python -m pytest tests/test_codex_verifier.py tests/test_task_eval.py \
  tests/test_task_eval_react.py tests/test_state_runtime.py \
  tests/test_pipeline_credentials.py tests/test_codex_agent_client.py \
  tests/test_tool_graph_review_agent.py tests/test_tool_graph_llm.py -q
```

六例检验的是已观察到的旧问题，不代表开放任务上永远不会漏判。调查记录与 JSON 校验只能约束过程和结构，不能证明模型没有遗漏任务要求或误解语义。每项结论保留原始材料、独立实验和命令日志，便于继续人工复核。此次复用历史执行现场验证 verifier；没有重新让 Qwen/Sol 求解全部任务。
