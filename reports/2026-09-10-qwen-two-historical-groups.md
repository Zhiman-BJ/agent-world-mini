# Qwen 两组历史任务实测

产物目录：`runs/qwen_existing_verifiers/20260910_132108`。

按任务生成目录时间降序选择最近两组：
1. `20260831_102215_777094_happyscribe_gpt-5.6-terra`，9条。
2. `20260831_102215_613144_finstat_gpt-5.6-terra`，2条。

任务来自旧 verifier 工作区所引用的归档 taskgen 目录。队列交错排列为
Happyscribe task2、Finstat task6、Happyscribe task3、Finstat task7，再继续 Happyscribe。
使用两个执行线程，因此同时启动的两个任务日志行及完成顺序不保证与提交顺序一致。

模型为本地 Qwen3.8-27B，BF16、medium、temperature=1.0，编译/CUDA Graph 已开启。
ReAct 每项最多50次工具调用；没有 shell 或直接文件访问能力。
每项使用独立初态副本，运行后核对来源初态未变。
仅复用旧工作区各任务最新保存的 verifier，未生成或修改 verifier。
其语义判定也使用本地 Qwen，故这是 Qwen 执行加旧 verifier/Qwen 语义裁决的结果，
不是独立更强模型给出的质量保证。

## 结果

| 环境 | 任务 | 结果 | 执行加初次验证耗时（秒） |
|---|---|---|---:|
| Happyscribe | task2 | 执行完成；没有完整 verifier，未评分 | 103.9 |
| Happyscribe | task3 | pass | 42.9 |
| Happyscribe | task4 | pass | 55.5 |
| Happyscribe | task5 | pass | 48.1 |
| Happyscribe | task6 | 执行完成；没有完整 verifier，未评分 | 81.2 |
| Happyscribe | task7 | pass | 256.5 |
| Happyscribe | task8 | pass | 373.3 |
| Happyscribe | task9 | pass | 341.7 |
| Happyscribe | task10 | pass | 82.7 |
| Finstat | task6 | 模型无正文导致中断；对实际中断状态验证为 fail | 240.3 |
| Finstat | task7 | 模型无正文导致中断；对实际中断状态验证为 fail | 98.1 |

总计11条：7条已有 verifier 判 pass，2条中断且实际产物判 fail，2条缺少 verifier 未评分。
没有用旧的通过记录替代本次结果，也没有替模型补做未完成操作。

## 具体问题

Finstat 两条在成功执行若干工具后，API调用没有返回正文，客户端抛出
`RuntimeError: LLM 没有返回文本内容`。task6留下10条工具调用，task7留下9条。
当前失败留档未保存原始 reasoning、finish_reason 和 usage，因此尚不能断言服务端的具体原因，
不能把它说成余额不足、超时或输出达到上限。没有自动重试并隐藏首次失败。

task6 的旧 verifier 检出缺少符合要求的应计分录（R4），以及损益报告/回答不足（R7）。
task7 已完成对账调整、审计备注和试算平衡表；损益表判 fail（R6），
资产负债表及其平衡结果因证据不足判 indeterminate（R7/R8），整体 fail。
后两项语义检查的“无工具调用”描述是针对 verifier 筛选后的证据，不代表整项任务没有调用工具。

Happyscribe task2、task6 未找到现成完整 verifier，仍运行以保留这两组的全部任务覆盖，
但不能给通过结论。其余7条通过只代表所复用旧 verifier 的判定，不证明不存在漏检。

## 查看文件

- `manifest.json`：提交顺序、每条任务使用的原 verifier 路径。
- `results.json`：原始批量结果，Finstat保留执行error，不改写为成功或覆盖错误。
- 每任务 `result.json`、`agent_result.json`：最终回答、用时及初次判定。
- `state.agent/llm_calls.jsonl`：逐轮ReAct请求、回答和可获得的usage。
- `tool_calls.jsonl`、`state/`：真实工具调用及终态。
- `verifier.json`：原 verifier 的直接副本。
- `llm_calls.jsonl`：复用 verifier 时的语义调用。
- Finstat 的 `interrupted_state_verification.json`：中断状态补充验证结果。
- `evidence.json` 或 `interrupted_evidence.json`：对应真实证据。

运行入口为 `scripts/run_qwen_existing_verifiers.py`；中断产物验证入口为
`scripts/score_interrupted_qwen_runs.py`。两个入口均无 verifier 生成调用。

## 中断点续跑

用户要求从中断处继续，产物另存 `runs/qwen_finstat_resume/20260910_145757`。
复制原中断终态，重放失败调用保存的 system_prompt、history、prompt，
保留此前已使用的工具预算，不重做已完成操作，不覆盖首次失败。
`raw_responses.jsonl` 保存完整 API 响应，包括 reasoning、finish_reason 和 usage。

task6 增加5次工具调用（应计分录、对账、复核清单、损益表、验收），
正常提交最终回答，原 verifier 判 pass，续跑加验证112.1秒。
task7 增加3次工具调用（损益表、资产负债表、诊断），正常提交最终回答，
验证结果以该目录的 result.json 为准。
两条续跑均未复现空正文：只能证明同一中断点能够继续，不能倒推首次空正文的根因。
原失败响应未保存完整字段，历史故障仍无法确定归因。
