# 单作者任务设计

本流程结合已交付环境与工具设计真实、较难、可解的任务，同时产出实际可执行的参考工具调用列表。
原题只用于认识环境，不限定新题目标。正式求解者需要根据业务需求和领域知识选择工具、解释返回并组织后续工作。

入口是 `task_gen/program/single_agent_hardening.py`；作者提示词唯一来源为该文件的 `AUTHOR_PROMPT`。
本文解释当前流程，不再维护历史增难版本、旧检查清单或过往试跑门槛。

## 设计原则

- 围绕一个真实、明确的成果提出需求。任务难度来自领域理解、必要的业务判断和结果驱动的多阶段工作，不来自长题文、长答案、重复核查或抄录。
- 至少两处关键判断结合领域知识与真实返回，实质影响后续需要的信息、工具、对象、参数或行动；机械传递 ID 不单独算判断。
- 目标和验收标准明确，公开数据与工具足以完成。业务允许时接受不同取证策略、工具组合和等效方案，不按参考路线验收。
- 题文说明“要什么、已知什么、什么算完成”，不出现工具名、调用顺序、查询算法、中间选择、分支动作或参考中间答案。对象 ID、文件名、坐标、单位和业务阈值是正常输入。
- 专业计算使用现有领域工具；执行者负责理解、筛选、比较、简单四则运算与结果传递。工具已经算出的结论不计为执行者的新判断。
- 参考列表至少15次有效业务 MCP 调用，每次服务主要成果；数量是检查下限，不是难度证明。作者材料读取不计入列表。

## 一次会话中的完整流程

1. 宿主选择已有来源，冻结环境、初态、公开工具与运行协议，准备作者材料。
2. 一个作者 Agent 阅读材料、试用工具、设计题文并求解，编写参考答案、调用列表和来源。
3. 作者调用 `validate_solution_calls` 提交完整方案。校验器在干净副本实际执行列表并核对参数来源，返回逐步结果和具体错误；作者在同一会话修改并重验。
4. 作者按题文检查答案和实际产物，调用 `submit_validated_solution` 绑定成功记录，最终只返回 `{"validation_id":"成功ID"}`。
5. 宿主保存该 ID 对应的完整方案和证据，不再启动一套宿主参考重放，不在会话结束后补写来源或答案。
6. 独立模型从干净初态盲解新题，再由正式 Verifier 验收。作者执行通过不代替这一步。

一次会话包含多次模型请求和校验修正，不是一次 completion。校验受作者总预算约束，不另设“只能验证三次”的额度。
支持一次已有判分反馈修订时，通过 `--prior-evaluation` 提供作者私有反馈；题文改变后须重新盲测，不混合新旧版本成绩。

## 作者材料

宿主在公开业务工作区之外的只读 `.task-authoring/` 放置：

| 文件 | 用途 |
| --- | --- |
| `public_tools.json` | 公开工具契约，先理解环境实际能力 |
| `workspace_files.json` | 公开 Scope、文件与挂载索引 |
| `protocol.json` | 本次模型、上下文、输出、读取、压缩和调用预算 |
| `solution_format.json` | 完整方案字段、参考列表和来源格式 |
| `original_task.json`、`original_calls.json` | 参考原需求与调用，不能照搬或向盲解泄露答案 |
| `previous_evaluation.json`（可选） | 同来源的已冻结诊断或修订反馈，仅作者可读 |

求解者不接收这些作者材料，只接收新题和公开环境。
作者探索优先用业务 MCP；Read/Grep/Glob 可用于材料和必要的公开参数查找，最终参考列表只含业务工具。

## 方案与来源校验

机器格式以 [AUTHOR_SOLUTION_FORMAT.json](../task_gen/program/AUTHOR_SOLUTION_FORMAT.json) 为准，不在文档复制另一套 Schema。
`solution_calls` 是唯一执行列表；参数及答案依据来自题文、公开材料或前序实际返回。
Schema 枚举、查询控制和依据已有结果编写的新正文按格式分别声明，不能用技术控制声明代替业务事实证据。

校验返回 `validation_id`、`execution_passed`、`source_check_passed`、实际调用与错误、可只读回查的 `state.public_workspace`。
列表相同且已成功时可复用执行；题文、答案或来源改变仍需完整校验，不能只验调用列表。
执行失败或列表改变时重新使用干净副本，不污染原始环境。

`reference_validated_pending_semantic_verification` 只表示参考执行与来源通过；
`gpt_completed_pending_answer_review` 只表示可选独立求解已结束。两者都不等于正式 PASS、无方法泄露或难度已提高。
真实难度依据固定任务的盲测轨迹和正式 verdict 分析，调用次数只描述工作量。

## 运行与配置

```bash
python -m task_gen.program.single_agent_hardening \
  --input-root /path/to/taskgen-inputs --case SOURCE_CASE \
  --protocol /path/to/author-protocol.json --output-root /path/to/new-author-run
```

默认只准备输入；显式增加 `--execute` 才启动作者，key 从 `--api-key-env`（默认 `OPENAI_API_KEY`）取值。
源码会检查协议，不接受任意 JSON：当前固定 high/Responses、262144上下文、131072输出、
Read 80000/128000、压缩0.8，并以 `protocol_role` 区分65次普通协议、75次 `author_extended`、85次 `evaluation_extended`。
`model` 与 `provider_base_url` 必须显式提供；角色是独立运行的预算声明，不自动改变另一角色预算。
新建协议应按 `reference_evidence.check_protocol` 核对，参数解释见[配置参考](CONFIGURATION_ZH.md)。

主要实现：`single_agent_hardening.py` 编排，`solution_validation.py` 校验执行，
`proposal_sources.py` 检查来源，`author_submission.py` 绑定提交，`reference_evidence.py` 核对真实协议与证据。
正式模型测评与判分见[测评说明](EVALUATION_ZH.md)，本流程不自动宣布任务发布或批次达标。
