# 蒸馏与训练数据构建

本项目中的蒸馏是让教师模型在工具环境中真实执行，再导出可审计的多轮轨迹。
`distill/` 不更新模型权重，不设置 TP/CP/DP，也不负责训练损失掩码。

## 1. 架构与数据契约

### 1.1 从教师执行到训练数据

```text
任务 + ToolGen 绑定 + 初态
  -> Kimi Code 教师执行（Harness / MCP）
  -> 原始模型请求、事件、工具调用、终态
  -> 标准 trajectory.json
  -> 正式 Verifier 和 canonical PASS 索引
  -> 数据清理、工具定义补全、路径处理、压缩窗口重建
  -> 训练 JSONL + manifest + 审计记录
  -> 外部 SFT 训练
```

### 1.2 构建模块

| 模块 | 工作 |
| --- | --- |
| `distill.dataset.training` | 对齐轨迹、初始请求、通过索引和 delivery，生成记录与审计 |
| `distill.dataset.paths` | 转换运行路径，检查主机私有路径暴露 |
| `distill.dataset.merge` | 合并完整轨迹与已有压缩续接 |
| `distill.dataset.windows` | 按真实压缩边界还原模型可见窗口、检查调用和结果配对 |
| `distill.dataset.append` | 按 canonical PASS 索引和真实 verdict 增补 |

### 1.3 必须保留的原件

- 每条实际模型请求与对应响应/事件：确认工具表、模型协议和真实上下文。
- 环境工具原始记录、原生文件工具审计、wire 和 session：检查配对、次序、缺失及压缩。
- 任务及初末状态、binding、工具和 runtime：证明模型做了什么。
- `trajectory.json`、正式 verdict、样本筛选索引与来源指纹：用于可复现的数据选择。

标准轨迹便于消费，但不是删除原始请求的理由。不能将 verifier 的调查记录、作者方案或参考答案混入求解模型的公开上下文。
工具定义和返回要对应模型当时实际看到的版本，不能事后用当前工具表覆盖旧轨迹。

## 2. 采集、导出与构建

### 2.1 采集入口

`python -m distill taskgen` 与 `python -m eval run` 共用采集实现；前者按蒸馏用途命名，后者是测评统一入口。
单条已准备任务使用 `python -m distill run --help`，需要 prompt、初态和 server config。
批量配置、工具预算、并发和正式验收见[测评说明](EVALUATION_ZH.md)，无需维护两份不同的运行协议。

### 2.2 导出与查看

保留原始材料后可重新导出轨迹：

```bash
python -m distill export --help
python -m eval visualize --trajectory /path/to/trajectory.json --output /path/to/trajectory.html
```

导出器读取 raw、task、environment，按真实事件还原调用，不把修补后的名字伪装成模型原始输出。
一个 trajectory 是一次执行，不一定是一个独立任务；同一任务可有重试、多个模型或多个上下文窗口。
统计训练分布时分别列出环境数、独立任务数、执行数和训练窗口数，不能把 JSONL 行数直接称为独立任务数。

### 2.3 构建训练数据

各模块分别处理来源对齐、路径、轨迹合并、窗口与增补，先按上表确认所需输入，再查看参数。

```bash
python -m distill.dataset.training --help
python -m distill.dataset.paths --help
python -m distill.dataset.merge --help
python -m distill.dataset.windows --help
python -m distill.dataset.append --help
```

各模块的输入顺序、ID 和来源必须一致，输出使用新目录；详见[构建器说明](../distill/dataset/README.md)。
压缩后的摘要续接不等于完整原始历史，不能简单拼接成更长文本假装模型一直看见所有内容。

## 3. 进入训练前的核对

确认独立任务划分，避免同题的重试、不同窗口或同源改写跨训练/验证集泄露。
逐类检查 assistant 正文、思考、tool call、tool response、终止标记和工具 Schema 的实际 chat template。
思考是否参与监督、有效 token 分母、padding mask、packing attention 隔离、CP 路径及 token accuracy 都由训练端实现，
不能根据蒸馏 JSONL 看起来合理就宣称这些已经正确。

本仓库不在本文规定某个模型的训练并行参数或 checkpoint 路径。训练工程见
[portable-release 说明](../../agent_train/portable-release-v2-20261005/README.md)，
集群推理与权重来源见[运维说明](OPERATIONS_ZH.md)。
