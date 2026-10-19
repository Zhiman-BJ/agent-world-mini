# 任务生成与参考解

任务生成的输入是已有真实环境和已发布工具，产物应能被求解器从干净初态独立完成。
本文只说明入口和职责，不启动新生成，也不替换当前作者提示词。

## 1. 架构与设计边界

### 1.1 三种入口

| 入口 | 输入 | 参考解形态 | 用途 |
| --- | --- | --- | --- |
| Program 三阶段 | ToolGen `binding.json` | 受限 Python solution，经公开工具执行 | 从一个交付环境生成业务任务 |
| 单作者设计 | 已有任务、同环境、显式运行协议 | 按顺序执行的业务 MCP 调用列表 | 以旧题为参考重新设计任务并验证方案 |
| Tool-graph | 已交付工具和环境 | 工具关系图、采样链及实际执行 | 图驱动的另一种任务生成策略 |

不要因单作者改用列表，就删除 Program Step2 的参考程序实现；两者仍是不同入口。
正式三阶段见 [Program 管线](PROGRAM_PIPELINE_ZH.md)，当前任务设计见[单作者流程](TASK_DESIGN_ZH.md)。

### 1.2 Program 三阶段

1. Step0：校验交付 binding，冻结环境、工具和基线状态，保留运行时绑定。
2. Step1：结合公开能力做现实任务调研，形成任务原型与业务依据。
3. Step2：生成题文、参考解和答案，实际执行、反馈修正、质量检查与干净重放，输出接受与拒绝记录。

### 1.3 单作者会话

1. 宿主冻结来源和协议，为作者准备 `.task-authoring/` 只读材料：工具、公开文件索引、格式、原任务与原调用。
2. 一个作者 Agent 在同一会话中设计任务并求解，参考列表只含业务 MCP；必要的材料读取属于作者探索，不写入最终列表凑调用数。
3. 作者把完整方案提交 `validate_solution_calls`。代码在独立干净副本上执行列表并检查参数来源，逐次反馈实际结果与错误；作者可继续修正。
4. `submit_validated_solution` 将最终完整方案与成功校验记录绑定。最终仅提交成功 `validation_id`，宿主核对一致性。
5. 独立 GPT / Qwen 盲解与正式 Verifier 在另一个流程运行，不看到作者材料或参考解。

这里的“一次 Agent 会话”可以有多次模型请求、工具调用和校验，不是一次 completion。
校验复用正式 MCP 执行器；不是自行模拟工具，也不为每次列表校验重启一套模型 Agent。

格式以 [AUTHOR_SOLUTION_FORMAT.json](../task_gen/program/AUTHOR_SOLUTION_FORMAT.json) 为准。
职责详见[单作者说明](TASK_DESIGN_ZH.md)。
`reference_validated_pending_semantic_verification` 表示执行和来源校验通过，不是正式任务 PASS。

### 1.4 质量边界

当前设计重点是考察领域理解、工具选择、使用与结果驱动的编排，不是增加题长或复核栏目。
题文给出目标、背景、业务输入和验收条件，不给工具名、调用顺序、实现算法、中间选择或分支路线。
对象 ID、路径、坐标、单位和阈值作为业务输入可以出现，不应与方法泄露混为一谈。

业务专业计算由现有领域工具完成；求解者组织调用、读数、比较、简单计算和传递结果。
公开信息必须足以支撑参数和答案，不能靠作者私有数据库或审核者的额外计算能力弥补不可解任务。
有效业务调用数下限属于具体作者/批次规则，不是所有 Program 入口的全局默认，也不等同于真实难度。

作者自查、可重放、来源校验、正式业务验收、双模型难度测量各有证据，任何一项都不能代替其余各项。
历史版本的门槛、拒绝记录和成绩只约束原实验，不自动升级成今天的生成规则。

## 2. 运行入口

### 2.1 Program

```bash
python -m task_gen.program.run_pipeline \
  --step all --binding /path/to/delivery/environments/ENV_ID/binding.json \
  --output-dir /path/to/new-program-run --task-count 3 --model gpt-6.1-sol
```

`--step 0`、`--step 1`、`--step 2` 或区间用于复用完整上游产物；缺失或部分完成不代表可安全跳过。
`--fresh` 是显式重建，不是评测续跑选项。`--dry-run` 用于检查阶段计划，不是完整可解性证明。
另一入口 `python -m task_gen.program --config ...` 负责运行目录、配置、`--resume` 和 `--stop-after`，
共用相同的三个阶段，并非额外一套生成算法。

### 2.2 单作者设计

入口：`python -m task_gen.program.single_agent_hardening --help`。
默认只准备；增加 `--execute` 才启动作者会话。必需 `--input-root`、`--case`、`--protocol`、`--output-root`。

## 3. Program 产物与下游

常见输出：

```text
program-run/
  step0_environment.json
  baseline_environment/
  step1_task_research.json
  step2_task_solution.jsonl
  step2_validation.json
  tasks.json
  rejected.json
  intermediate/step_5_bundle.json
  tasks/<task-id>/initial/
  tasks/<task-id>/final/
```

`tasks.json` 与 bundle、初态、工具绑定共同构成下游输入，不能只复制题文给评测器。
