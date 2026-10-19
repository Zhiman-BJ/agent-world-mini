# 代码与文档维护

## 目录职责

| 目录 | 维护内容 |
| --- | --- |
| `docs/` | 当前架构、任务生成、Harness/MCP、蒸馏、测评、配置和操作说明 |
| `eval/` | 通用测评命令、正式 Verifier、已有现场验收、摘要统计 |
| `distill/` | Kimi CLI 编排、模型传输、轨迹采集与训练数据构建 |
| `harness/` | 共享工具执行、资源与状态事务、协议及权限 |
| `task_gen/program/` | 正式三阶段、当前单作者设计、同会话方案校验与提交 |
| `runs/` 或外部实验目录 | 每批任务、协议、日志、状态、冻结源码和结果，不充当正式源码 |

模块 README 只作入口导航，使用说明集中在 `docs/`；机器 Schema 和提交格式仍随实现放置。
文档只写当前行为，默认值与源码同步，不堆积旧方案、旧计划、补丁过程或历史成绩。
接口或流程改变时更新现有说明，不额外复制一份“最新版”并保留多份互相冲突的规则。

## Program 目录分层

`task_gen/program/` 保留以下现行模块，按调用职责阅读，不按文件名的新旧判断能否删除：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 管线入口 | `__main__.py`、`pipeline.py`、`run_pipeline.py` | 配置运行与接续、显式阶段选择；共用同一组三阶段 |
| 三阶段实现 | `step_0_environment_load.py`、`step_1_task_research.py`、`step_2_solution_generate.py` | 交付冻结、场景调研、任务与参考程序生成及校验 |
| 作者入口 | `single_agent_hardening.py` | 当前单作者任务设计会话；名称保留，不能当作废弃加难实现删除 |
| 作者校验 | `solution_validation.py`、`proposal_sources.py`、`author_submission.py` | 干净状态执行列表、参数来源校验、绑定成功提交 |
| 证据适配 | `reference_evidence.py` | 来源指纹、协议和已有任务的测评环境适配；仍被当前作者与测评调用 |
| 管线支持 | `contracts.py`、`run_io.py`、`llm.py` | 管线配置和阶段类型、运行记录、模型调用上下文 |
| 公共实现 | `utils/` | 领域策略与任务字段、交付环境、Profile worker、运行时、I/O、Schema 文档 |
| 机器契约 | `schemas/`、`AUTHOR_SOLUTION_FORMAT.json` | 结构约束与作者提交格式 |
| 包入口与导航 | `__init__.py`、`README.md` | 公共 API 与集中说明链接 |

其中根目录 `contracts.py` 定义管线配置/阶段类型，`utils/contracts.py` 定义生成策略/任务字段，
不是同一份契约的两个副本。`reference_evidence.py` 也不是已删除的旧参考重放执行器。
当前文件保留原导入和 CLI 路径，避免只为整理目录而改动冻结批次或外部调用方。

批次补跑监督器、固定环境基准、旧批次收集/报告和固定题数审计不再放在正式 Program 源码中。
已有实验产物和冻结源码保持不变；后续批次调度与一次性报告放在对应运行目录。

## 删除与保留边界

已废弃的加难方案、宿主参考重放驱动及其旧测试直接删除，不继续维护归档副本或兼容跳转页。
当前参考解校验统一走作者会话中的 `solution_validation.py`，提交由 `author_submission.py` 绑定。
Program 三阶段仍是有效入口，不因参考解形态不同而误删；它的干净重放属于该管线验收，不是旧加难的宿主重放。

删除前检查源码、CLI、动态导入和测试引用；通用小函数移到现有公共模块，不为保留一行工具函数留下整套旧实现。
不可重建的轨迹、环境状态、原始证据和模型权重不随源码清理删除。已冻结批次使用自己的源码与协议，不原地替换。
工作区中他人的有效改动不回退，涉及当前行为的变更必须补回归验证。

## 最小回归

```bash
python -m eval --help
python -m task_gen.program.single_agent_hardening --help
python -m pytest -q \
  tests/test_program_jsonl.py \
  tests/test_single_agent_hardening.py \
  tests/test_reference_evidence.py \
  tests/test_solution_validation.py \
  tests/test_eval_entrypoints.py
```

测试和文档命令检查不调用真实模型。Linux namespace / procfs 相关测试需要相应权限，
在 Pod 中按部署设置容器适配，不能为了通过测试移除证据只读隔离。
模型实际试跑是独立实验，应另有明确输入、输出、协议和成本记录。
