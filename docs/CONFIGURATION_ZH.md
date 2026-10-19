# 配置参考

本页默认值核对自当前源码：`distill/__main__.py`、`distill/runner.py`、`eval/verifier.py`。
`python -m eval run --help` 使用同一解析器；环境变量可覆盖的值还须检查启动环境。
这些是源码默认，不是某个正在运行的任务的实测配置。

## 求解器与模型

| 参数 / 配置 | 当前默认 | 说明 |
| --- | --- | --- |
| `--kimi-bin` | `KIMI_CODE_BIN` 或 PATH 中的 kimi | Kimi Code 可执行文件 |
| `--base-url` | `KIMI_BASE_URL` | 上游 API；求解器启动前必需 |
| `--api-key-env` | `KIMI_API_KEY` | 指定保存 key 的环境变量名，不是 key 本身 |
| `--model-id` | `kimi-k3` | 上游请求 ID，接其他模型必须显式指定 |
| `--model-alias` | `evaluation` | 本地配置别名，不证明权重身份 |
| `--provider-type` | `kimi` | 支持 `kimi` / `openai_responses`；按接口选择 |
| `--reasoning-effort` | `max`，可由 `KIMI_REASONING_EFFORT` 覆盖 | 当前 CLI 选项为 low/high/max；不是训练参数 |
| `--mcp-server-name` | `agent_world_distill` | 新实验可用 workspace；整轮一致，不静默修补模型返回的裸工具名 |

模型身份至少核对请求中的 ID、服务启动命令、权重路径与 revision/hash、tokenizer 和 chat template。
不要从日志标签、目录名或别名推断是官方基模还是微调权重。

## 上下文和读取

| 参数 | 当前默认 | 单位与边界 |
| --- | --- | --- |
| `--max-context-size` | 1048576，可由 `KIMI_MAX_CONTEXT_SIZE` 覆盖 | token，当前支持262144与1048576 |
| `--max-output-size` | 131072，可由 `KIMI_MAX_OUTPUT_SIZE` 覆盖 | 单请求输出 token 上限，和 context 分开设置 |
| `--compaction-trigger-ratio` | 未显式指定 | 指定时0.5–0.99；实验常用0.8，不是所有历史运行的默认 |
| `--read-default-max-chars` | 80000 | UTF-16 码元，不是 token |
| `--read-max-chars` | 128000 | 同上，Read 输出硬上限，不是整个会话上限 |
| `--max-tool-calls` | 65，源码硬上限85 | 业务 MCP + Read/Grep/Glob 共享 |

上下文包含消息、工具定义和返回等，不只是题文。累计接近阈值可触发压缩；单次大读取或大工具返回仍可能先把请求撑爆。
不能用“已经开压缩”替代逐次检查原始请求。压缩的内部推理、摘要正文、最终会话使用的摘要是不同内容，复盘时核对实际请求记录。
32K 输出是可选试跑配置；131072 是当前源码默认。历史冻结协议若写131072，应按原值复现，不把新试跑默认追认到历史批次。

## 批量与资源

| 参数 | 当前默认 | 说明 |
| --- | --- | --- |
| `--max-concurrency` | 1 | 模型任务调度上限 |
| `--verifier-concurrency` | 25 | 独立于模型并发；20并发实验需显式传20 |
| `--adaptive-concurrency` | 关闭 | 动态调度，不改变单题预算 |
| `--min-concurrency` | 25 | 启用自适应且最大并发20时必须显式降低 |
| `--initial-concurrency` | 未指定，使用最大并发 | 自适应初始目标 |
| `--tool-timeout` | 300 | 单次业务工具秒数 |
| `--tool-memory-limit` | 137438953472 | bytes，128GiB虚拟内存限制，不是预留物理内存 |
| `--tool-write-limit` | 268435456 | bytes，256MiB |
| `--tool-process-limit` | 1024 | 旧隔离后端限制；任务工作区直接执行不施加 UID 级 RLIMIT_NPROC |
| `--max-environment-errors-per-task` | 1 | 执行层错误停止受影响任务，不是业务查询无结果次数 |
| `--max-environment-errors` | 未设置 | 默认不因一个坏环境停止整批 |
| `--max-upstream-retries` | 4，可由 `KIMI_MAX_UPSTREAM_RETRIES` 覆盖 | 有界传输重试，不是业务重做次数 |
| `--verify-after-each` | 关闭 | 显式打开才逐题正式验收 |
| `--delete-task-state` | 关闭 | 打开前确认现场保留策略 |

`KIMI_STATE_COPY_CONCURRENCY` 默认1，只控制状态准备，不是模型并发。
Docker MCP 启动和存活会话另有限流，不能只提高模型并发就假设机器能吃满。

## Verifier

批量 `eval run` 的参数：

| 参数 | 当前默认 |
| --- | --- |
| `--verifier-codex-home` | `~/.codex` |
| `--verifier-model` | `gpt-6.1-sol` |
| `--verifier-reasoning-effort` | `high` |
| `--verifier-timeout` | 1800秒 |
| `--verifier-attempts` | 3 |

底层 Python API `verify_execution(config=...)` 使用 `codex_home`、`model`、`reasoning_effort`、
`timeout_seconds`、`attempts`。未指定模型/强度时可从 Codex home 读取，不能假设所有入口都等于批量 CLI 默认。
`orchestration_only` 限制审核计算权限；`submission_preflight` 增加 JSON 提交自检。
已冻结的 `verification_profile` / `acceptance_contract` 必须原样传递，不能为了通过率更换标准。

## 环境、集群和凭据

后端和依赖来自 `binding.json` 绑定的 `runtime.json`，不由模型类型决定。
`AGENT_WORLD_KUBERNETES_RUNTIME=1` 表示使用已有 Pod 的 procfs 等部署适配，不等于开启 Docker daemon。
`AGENT_WORLD_DOCKER_PROJECT_ROOT` 供 Docker daemon 与客户端代码挂载位置不同的部署做路径映射，须保证源码一致。
不要将已经失效的历史兼容开关视为当前通用修复；见[运行时说明](TOOL_RUNTIME_ZH.md)。

集群访问用受保护的 `KUBECONFIG` 文件，模型和 Verifier key 用环境变量/密钥注入，文件权限建议600。
文档不存 kubeconfig 内嵌证书私钥、登录密码或 API key。
节点、服务和模型权重部署详见外部运维记录：
[集群使用说明](../../agent_train/CLUSTER_INFERENCE_GUIDE.md)、
[推理访问记录](../../agent_train/INFERENCE_ACCESS_20261013.md)。其中的当时地址不保证当前仍在线。
