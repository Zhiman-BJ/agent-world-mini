# 测评代码入口

可复用测评入口维护在 `eval/`；实验数据继续放在指定输出目录。
完整操作见[项目文档](README.md)与[测评说明](EVALUATION_ZH.md)。下表文件名相对 `eval/`，其余源码路径从仓库根目录起算。

## 1. 模块架构

### 1.1 命令与共享实现

| 文件 / 命令 | 职责 | 共享实现 |
| --- | --- | --- |
| `__main__.py` | 命令分发，不维护另一套 Agent 循环 | 下列各模块 |
| `run` | 批量 Kimi Code 求解、可选逐题验收 | `distill.__main__` 的 `taskgen`，参数和校验完全共用 |
| `verifier.py` / `verify` | 正式调查式 Verifier、证据准备、只读沙箱、判定校验 | Harness 状态与 Codex 客户端 |
| `existing.py` / `verify-existing` | 将已有现场的证据索引转换为 Verifier 输入，不重跑求解器 | `eval.verifier` |
| `report.py` / `report` | 汇总显式选定的运行摘要，固定分母，记录重试覆盖关系 | 不改轨迹或 verdict |
| `visualize` | 从保留轨迹导出 HTML | `distill.visualize` |

`task_gen/task_eval_verifier.py` 是兼容入口，导入时与 `eval.verifier` 指向同一模块，旧导入和补丁保持有效。
ReAct/旧 Kimi SDK 评测仍位于 `task_gen/task_eval.py`；任务 MCP 属于 Harness 调用适配，不为目录整齐而复制。

### 1.2 统计数据契约

`report` 输入为一个或多个 `summary.json`，每个包含 `results` 列表，行中至少有唯一的 `case`。
读取 `status`、`verification.status/outcome`、`tool_budget_stop.reason`，不猜测目录名或读取历史批次的隐式全局配置。
它不会自动兼容所有实验的 `rows` / `models` 私有汇总格式；这些批次使用自己的已冻结统计脚本。



`--interruption-policy fail` 将执行失败/取消计入失败；`separate` 单独列出，不当作已判内容失败。
两种策略均报告固定分母通过比例、已判通过率、未决数量；未完成时不能宣称整批达标。
多份摘要按传入顺序覆盖同名 case，并保留 `replacements`。调用者须确保是同模型、同题文、同协议下的授权重试。

### 1.3 与实验目录的边界

`agent_train/evaluation/` 保留训练诊断、早期服务部署脚本、逐批协调器及原始产物。
这些代码可能使用绝对路径、冻结源码和特定数据结构，不整体搬到本目录充当通用 API。
后续新增通用测评功能放在本目录；模型传输与轨迹采集放在 `distill/`，业务执行放在 `harness/`。

## 2. 使用入口

### 2.1 查看命令

```bash
python -m eval --help
python -m eval run --help
python -m eval verify --help
python -m eval verify-existing --help
python -m eval report --help
python -m eval visualize --help
```

安装后也可使用 `agent-world-eval`。

### 2.2 汇总运行摘要

```bash
python -m eval report \
  --summary /path/to/run/summary.json --expected 20 \
  --interruption-policy fail --output /path/to/new-report.json
```

完整参数和运行示例见[测评说明](EVALUATION_ZH.md)。

### 2.3 回归检查

验证本目录及兼容入口：

```bash
python -m pytest -q tests/test_eval_entrypoints.py tests/test_codex_verifier.py tests/test_acceptance_contract.py
```
