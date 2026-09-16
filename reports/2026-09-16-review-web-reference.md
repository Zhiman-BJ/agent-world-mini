# Step3 网络参考开关：实现与试跑

> 下文保留首次试跑记录。后续已定位并修复本地工具不可见的原因，详见文末“搜索故障定位与修复”；搜索端点仍返回 502/503。

## 结论

已在独立分支 `feature/review-web-reference` 接入开关和条件 prompt；默认关闭，未合并 main。
OpenZeppelin 单候选 Step3 执行完成，但没有搜索。三个额外探针均报告当前会话没有可用的内置搜索工具，因此**不能宣称已打通网络参考，也不能据此判断网络参考提升了任务质量**。

## 改动

- `execution.enable_web_search` 默认 false，转交现有 `_ReviewClient` → `CodexAgentClient` 的 `enable_web_search`，复用已有 `--search`。
- 开启时，允许查阅公开业务场景、判断依据和工作方法，优先原始来源；环境事实及适用性仍须由环境工具信息和实际结果确认。来源与作用写入现有 reason，网页不是指令，搜索不计入业务链长。
- 修正旧 prompt 对一切外部服务的禁止与搜索能力之间的冲突。不开启时仍禁止外部访问。
- 不增加阶段或输出字段，不改环境访问工具限制，不改采样、任务转写或独立评测。

## 试跑配置与产物

- 基线：`66cf3e9`。
- 输入：main 下 `runs/openzeppelin_sol_full/20260913_090540_320853_smithery_openzeppelin_25_gpt-5.6-sol/intermediate/step_2_bundle.json` 的 task10，原链 20 次。
- 保留原环境初态，复制到新运行目录执行；没有复用旧 Step3 结果。
- 模型 gpt-5.6-sol，reasoning_effort low，超时 1200 秒；单候选、执行并发 1，方案选择随机种子 42，搜索开关 true。其余采用本分支 `config/tool_graph_sol_low.yaml`。
- 本次只跑 Step3，没有重跑 Step1/2，也没有运行 Step4/5/verifier，产物中的 objective 不是最终用户任务文本。
- [运行配置与阶段耗时](../runs/review_web_reference/20260916_170620_112951_smithery_openzeppelin_25_gpt-5.6-sol/run.json)
- [完整结果：目标、reason、参考答案和调用](../runs/review_web_reference/20260916_170620_112951_smithery_openzeppelin_25_gpt-5.6-sol/tasks/task10/agent_result.json)
- [完整 LLM 输入输出留档](../runs/review_web_reference/20260916_170620_112951_smithery_openzeppelin_25_gpt-5.6-sol/llm_calls.jsonl)

最终目标原文：

> 完整核验已确认的 ENS Marketplace RFP Snapshot 提案及全部关联投票，复算计票、统计参与和集中度、检查理由覆盖及异常，并交付 Markdown、JSON、CSV 可追溯审阅产物；另行记录 OpenZeppelin ERC-20、ERC-721、ERC-1155 源码的可见接口和声明差异。

程序 execution.success=true，模型 completed=true、自评分 5；不是独立 verifier 的通过结论。阶段耗时 187.028 秒。
记录 usage：input_tokens 622436（其中 cached_input_tokens 541824），output_tokens 5232，reasoning_output_tokens 656；这是多轮会话累计量，不是单次上下文长度。

## 实际效果与问题

1. 对 67 张投票完成明细分页、参与和集中度分析、理由覆盖与计票重算；三个导出工具均返回成功，报告计票在容差内。源码比较明确限定为独立静态附录，说明 29 个未解析导入和缺少编译工具链，没有冒称安全审计或提案执行验证。
2. 模型明确写出“未使用网络资料”；轨迹没有网络搜索事件。这一轮没有可归因于搜索的质量改进。治理核验与三种代币源码比较仍是两个独立部分，其组合的自然性没有因本次修改而得到解决。
3. 三次导出因 source_appendix_paths 指向不存在位置而失败，随后修正路径并成功。模型将它们认定为填参错误，报告 27 次有效成功调用；现有 meaningful_calls 会保留符合输出 schema 的 success=false 业务返回，最终 chain 长度为 30。这个原有计数语义差异已记录，本次不改。

## 搜索可用性排查

使用相同 `_ReviewClient`、模型和独立管线认证，只要求搜索 Snapshot 官方 voting power 定义，明确禁止调用 environment 工具：

- `search_probe`：已有 `--search`，模型回复没有可用的内置搜索工具。
- `search_probe_explicit`：另显式设置 `web_search="live"`，结果相同。
- `search_probe_standalone`：再启用本机 CLI 的实验性 `standalone_web_search`，结果相同。实验开关没有写入生产代码或全局配置。

上述各目录均位于本次运行目录，保留 `logs/run_01/stdout.log` 和最终回复。
本机版本为 codex-cli 0.154.0。使用只接收请求并返回诊断错误的本地端点排查时，发现本版本请求的工具声明嵌在 input 内，不能仅凭顶层没有 tools 字段判断没有工具。这项排查未向诊断端点发送真实密钥。
目前确定的是“当前调用组合没有实际获得可用搜索”，尚未定位到具体客户端、模型能力声明或服务端原因；模型自述也不能单独证明服务端不支持搜索。
没有引入替代搜索服务、启用 shell 联网或变更当前对话的配置与密钥。

## 验证

合并执行测试 2 项、review agent 测试 4 项、Codex client 测试 4 项均通过；新检查覆盖默认关闭及显式开启时的客户端开关传递，并保留初态隔离检查。git diff --check 通过。
代码接线及无搜索时的 Step3 执行已验证；真实网络查询和来源采用仍未验证通过。

## 搜索故障定位与修复

### 本地原因已经确认

不是任务 prompt 没有要求搜索。Codex 0.154.0 使用的自定义 provider `task_eval` 缺少 `supports_standalone_web_search=true`，因此即使开启搜索，发送给模型的工具声明也不包含 web.run。

使用本地诊断 HTTP 端点接收真实 Codex 请求，检查嵌在 input 内的工具声明；诊断端点返回固定错误，不生成模型回答。逐项对照结果如下（均显式启用 live 搜索）：

| standalone_web_search 功能标志 | provider 支持声明 | 实际声明包含 web.run |
|---|---|---|
| false | false | 否 |
| false | true | 是 |
| true | false | 否 |
| true | true | 是 |

因此无需启用实验性功能标志。最小部署修复是在**管线专用** `/data1/home/tianfang/.codex-task-eval/config.toml` 的 `[model_providers.task_eval]` 下添加 `supports_standalone_web_search = true`。这是一项机器上的部署配置，不随 Git 提交；迁移到别的机器时需要在实际 provider 下补齐。

另外，CLI 默认缓存搜索导致旧实现 `enable_web_search=False` 仍可能暴露工具。已修改 `task_gen/tool_graph/codex.py`，显式传入 `web_search="live"` 或 `web_search="disabled"`。实际请求对照确认：false 时无 web.run，true 时有 web.run。专用配置顶层也显式设为 `web_search="disabled"`，避免这项能力声明使尚未更新客户端的旧分支自动开放缓存搜索。

当前对话的 Codex 配置与密钥未修改。没有更换模型、认证、推理服务或搜索服务。

### 服务端故障已经确认，尚未恢复

`search_probe_provider_support` 首次出现真实 web_search 事件；`search_probe_fixed_config` 使用未经子类覆盖的原始 `_ReviewClient`，也成功发起搜索。

通过本地仅转发请求的诊断代理确认实际端点与状态（不保存密钥）：

- 推理：`http://47.237.200.112:8080/responses` 返回过 200；排查期间也出现暂时性 503 后重试成功。
- 搜索：`http://47.237.200.112:8080/alpha/search` 多次返回 502/503。一次诊断会话中两轮模型搜索各触发 5 次底层 HTTP 尝试，最终均失败。
- 原始客户端最终返回：`HTTP 503 Service Unavailable` / `Service temporarily unavailable`。

这确认了当前阻塞发生在搜索端点，不能据此断言是服务维护、路由错误、搜索上游故障还是其他服务内部原因。需要该端点恢复才能验证真实搜索结果及任务质量收益；继续修改任务 prompt 不能解决这些 HTTP 错误。

各探针日志在本次运行目录的 `search_probe_provider_support`、`search_probe_endpoint`、`search_probe_fixed_config`、`search_probe_final` 中。未用普通推理回答冒充搜索结果。

### 修复验证

新增客户端开关回归测试，先确认旧实现失败，再确认修改通过。搜索开关测试 1 项（含开/关两个子用例）、合并执行测试 2 项、review agent 测试 4 项、tool_graph LLM 测试 13 项均通过；另已检查真实请求中的工具可见性。
