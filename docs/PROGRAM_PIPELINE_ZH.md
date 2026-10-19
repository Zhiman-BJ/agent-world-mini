# Program 三阶段管线

本页描述从 ToolGen 交付包生成任务的正式管线。它与[单作者任务设计](TASK_DESIGN_ZH.md)共享工具执行契约，
但参考解形态不同：本管线使用受限 Python solution，单作者使用业务调用列表。两种入口不能混作历史与新版互相替代。

## 阶段和代码

| 阶段 | 工作 | 实现 |
| --- | --- | --- |
| Step0 | 校验 binding，冻结环境、工具、运行时和基线，整理公开摘要 | `task_gen/program/step_0_environment_load.py` |
| Step1 | 研究现实需求，确认公开数据和工具可支撑的任务原型 | `task_gen/program/step_1_task_research.py` |
| Step2 | 生成题文、solution与答案，同作者修正、真实执行、质量检查及干净重放 | `task_gen/program/step_2_solution_generate.py` |

阶段必须使用同一交付绑定，专业运行时按 [Tool Runtime](TOOL_RUNTIME_ZH.md) 启动，不把缺库或 Docker 故障当作模型业务判断错误。
工具执行统一经 Harness，不直接调用 `internal.code` 绕过状态事务与资源边界。

## 设计和执行要求

任务服务真实用户成果，难度来自领域理解、工具选择、业务判断和结果驱动的后续工作。
正文给目标、背景、输入和验收，不给参考路线和中间答案；工具已有的筛选或计算不能冒充执行者的判断。
公开资料和工具能力足以完成任务，不靠参考程序独有的批处理、私有数据或额外专业计算。

Solution 使用允许的 Python 子集组织工具调用，可做变量传递、分支、循环和简单计算；
专业计算调用领域工具，参数与答案需可追溯到公开输入、工具契约或真实返回。
最终答案覆盖题文交付，任务要求文件或状态变更时核对实际内容，而非仅检查 success 或文件存在。

Step2 同一作者提交候选，收到结构、执行、来源和语义反馈后修改当前方案。
语义检查复用该候选的实际 trace、答案和状态；通过后再做干净重放，不在同一检查中无目的地重复执行。
可解释的业务错误交回作者修正；确定的环境缺失应记录和终止相应路径，不伪造成功。
拒绝与失败保留分类，不能根据结果替换验收环境以提高成功率。

## 运行入口

```bash
python -m task_gen.program.run_pipeline \
  --step all --binding /path/to/delivery/environments/ENV_ID/binding.json \
  --output-dir /path/to/new-program-run --task-count 3 --model gpt-6.1-sol
```

`run_pipeline` 支持0/1/2、区间或 all，复用完整阶段产物；`--fresh` 显式重建。
`python -m task_gen.program` 是配置式入口，管理 `--config`、`--resume` 和 `--stop-after`，共用同一组阶段实现。

当前显式阶段 CLI 默认：模型 `gpt-6.1-sol`、任务数1、生成尝试8、干净重放2次、修复6轮、作者3600秒；
完整 Solution 的执行预算与工具单次超时分别配置。完整默认值以 `run_pipeline --help` 和 `utils/contracts.py` 为准。
`--minimum-effective-tool-calls` 可显式设置调用数下限，默认不设置；15次是当前单作者设计规则，不是本入口的隐式默认。

## 产物与下游

输出包括 `step0_environment.json`、`baseline_environment/`、`step1_task_research.json`、
`step2_task_solution.jsonl`、`step2_validation.json`、`tasks.json`、`rejected.json`、
`intermediate/step_5_bundle.json` 和各题 `tasks/<task-id>/initial/`、`final/`。

下游通过 `task_gen.eval_cases.load_cases` 加载完整集合，不能只复制题文或混用另一环境的工具与初态。
生成期检查和参考重放证明不了模型盲解表现；正式求解与独立 Verifier 按[测评流程](EVALUATION_ZH.md)另行执行。
