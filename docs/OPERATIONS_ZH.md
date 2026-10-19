# 运维与排错

本页帮助定位现象属于服务、环境、求解、验收还是数据导出。先保留现场，不用换题或改评分掩盖问题。

## 启动前检查

1. 确认源码版本与工作区改动、Python/Kimi/Codex 版本，输出目录不存在或明确是批次根目录。
2. 确认题集可加载、binding 引用可见、初态完整，Profile Python 或 Docker daemon 真正可用。
3. 确认求解 API 和 Verifier provider 分别有凭据，不输出凭据内容做诊断。
4. 先跑一题，核对实际请求模型、工具表、调用、终态和 verdict，再提高并发。
5. 在容量足够的位置设置临时目录。复制状态可能远大于最终轨迹，检查存储、inode 与 I/O 压力，而不只看 GPU。

```bash
export TMPDIR=/path/to/large-disk/tmp
mkdir -p "$TMPDIR"
python -m eval run --help
df -h "$TMPDIR"
df -i "$TMPDIR"
```

不要对有大量缓存、环境和运行副本的共享目录反复递归扫描。定位问题先读目标批次的 manifest/summary 和具体失败日志。

## 常见现象

| 现象 | 先检查 | 下一步 |
| --- | --- | --- |
| 模型调用裸工具名或不存在的工具 | raw 模型请求中的 tools、实际调用名、namespace、tokenizer/template | 保留真实错误，不在评分前自动补 MCP 前缀 |
| context overflow / HTTP 400 | 最后请求的输入、工具定义、单次返回、输出预算、压缩事件 | 区分输入已超限与剩余输出预算不足；缩小单次读取/查询范围再做新协议实验 |
| 压缩结束但摘要为空 | 请求输出上限、length finish reason、摘要正文和实际下一请求 | 不把压缩思考当作已写入摘要；保留原失败链 |
| Read 输出超预期 | 80000/128000 是否实际传给该版 CLI，单位是否误当 token | 查 native 工具事件和 raw，不只看外层计划文件 |
| MCP 启动失败 | `raw/mcp_startup_failure.json`、stderr、binding、Profile Python、Docker daemon | 先修环境启动，不归因于模型不会做题 |
| 工具业务失败 | 参数 Schema、Scope、真实返回和工具内部实现 | 区分合法“无结果”与执行层异常 |
| 科学库缺失或 native 动态库冲突 | 绑定 Profile 与 LD_LIBRARY_PATH/PYTHONPATH | 使用交付解释器，避免继承另一 Python 的 torch/native 库 |
| Verifier 卡住/没有 verdict | logs、attempts、scratch、Codex home、网络、bwrap | 记 verification_error，重新验收用新目录，不重跑求解器 |
| bwrap Can't mount proc | 是否在非 hostPID 的 Pod 中，是否按部署设置 `AGENT_WORLD_KUBERNETES_RUNTIME=1` | 使用对应容器 procfs 适配；不直接去掉证据只读隔离 |
| 20并发但实际低 | 待测数量、状态准备、I/O、Docker 限流、服务容量、Verifier 队列 | 区分模型并发、工具并发、验收并发，报告实际值 |
| 任务 completed 但实际失败 | verification 与最终产物内容 | completed 只描述执行结束；正式以 verdict 和证据判定 |

## 运行恢复

按[测评说明](EVALUATION_ZH.md)选择 skip/select 参数。先确认要跳过的是已派发、已结束还是已通过，三者不同。
保留原失败与重试关系；工具调用开始后不能把流式传输断开简单当作“无副作用请求”无限重发。
重验用 `eval verify-existing`，新模型或新协议用新的求解目录，不原地覆盖旧产物。
如需停止任务，只操作已确认属于该批次的进程或 Job，不按共享节点上的所有 Python/GPU 进程批量 kill。

## 集群访问和模型来源

仓库不内置集群认证。管理员提供 kubeconfig 后放在私有路径，通过环境变量选用：

```bash
export KUBECONFIG=/path/to/private/kubeconfig
kubectl get nodes -o wide
kubectl get pods -n your-namespace -o wide
```

不要把内嵌 client-key、密码或管理员凭据提交到 Git/文档。服务地址、节点是否空闲和显存占用属于实时状态，
不能从旧文档推断。访问现有服务前确认命名空间、服务路由和对应模型进程。

模型身份核验记录服务启动参数、模型真实目录/revision/hash、tokenizer/template、接口 model ID，
再对照 `raw/model_requests.jsonl`；本地 `kimi-k3`、`evaluation` 等别名不证明来自哪个官方权重。
GPU 节点内外通信、推理 TP、训练 Megatron 并行参数是模型服务/训练层配置，不是 MCP 配置。

部署细节查阅工作区外部说明：

- [集群使用与推理指南](../../agent_train/CLUSTER_INFERENCE_GUIDE.md)。
- [推理访问记录](../../agent_train/INFERENCE_ACCESS_20261013.md)。
- [训练工程](../../agent_train/portable-release-v2-20261005/README.md)。

这些链接属于本服务器工作区，不随独立安装的 Python 包分发；历史地址和并行参数须到实际部署再确认。

## 最小交接清单

提供源码版本和安装方式、无密钥运行命令、固定题集和协议、模型/工具/binding 指纹、各模型原始轨迹、
初末状态和回答、正式 verdict 与调查记录、固定分母汇总、失败分类及授权重试关系。
交接现有材料时不改变其目录和内容；新验收输出写到别处。文档、浏览用 HTML 和原始证据分别说明用途。
