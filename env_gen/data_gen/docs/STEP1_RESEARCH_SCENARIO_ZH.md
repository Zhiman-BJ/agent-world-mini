# Step 1：环境场景调研

入口：`env_gen/data_gen/steps/step1_research_scenario.py`

## 1. 目标

Step 1 读取 Step 0 固化的完整 Seed，通过互联网调研把简短描述扩展为连贯的环境简报。简报说明
环境是什么、有哪些稳定业务实体、参考工具处理什么，以及用户会完成哪些典型任务。它为后续来源
调查提供语义方向，不提前下载数据或设计最终数据库。

Step 1 的公开调用接口只有：

```python
run_scenario_research(run_dir=run_dir, agent_runner=agent_runner)
```

Seed、Schema、契约、超时和重试次数全部从 Step 0 的运行上下文读取。
函数直接返回 `(scenario_research, agent_calls)`，不额外定义只保存这两个值的结果类。
Guide、Prompt、输入保护、正式保存、收据检查和恢复循环都位于同一个 Step 1 文件中；只有通用的
`scenario_research` 确定性校验及其 JSON Schema 保留在 `analysis/`。

## 2. 执行树

```text
run_scenario_research
├── A. 读取准备结果
│   ├── .datagen/run_config.json
│   └── .datagen/selected_seed.json
├── B. 建立调研任务
│   ├── 写 .datagen/RESEARCH_GUIDE.md
│   ├── 构造简短入口 Prompt
│   └── 冻结 Step 1 的输入、Schema 和校验代码
├── C. Agent 场景调研
│   ├── 从 Seed 自带 URL 开始理解领域和产品
│   ├── 结合新信息持续完善环境、实体、工具和任务
│   ├── 记录后续数据方向、资料来源和待确认问题
│   └── 写 .datagen/drafts/scenario_research.json
├── D. Python 收口
│   ├── 注入 Seed global_id 和 SHA-256
│   ├── 执行 JSON Schema 与语义校验
│   ├── 写 provenance/scenario_research.json
│   └── 写 .datagen/scenario_research_receipt.json
└── E. 有限恢复
    ├── 保存未通过草稿为 scenario_research.invalid.json
    ├── 把具体校验问题交给第二次 Agent 调用
    └── 在总时间和最大尝试次数内完成修正
```

## 3. Agent 输入与输出

Agent 只需要读取：

```text
.datagen/selected_seed.json
.datagen/RESEARCH_GUIDE.md
```

Agent 只写调研草稿：

```text
.datagen/drafts/scenario_research.json
```

身份注入、校验、正式保存和收据均由 Python 完成。

## 4. 正式字段树

```text
scenario_research.json
├── schema_version                  Python 注入
├── seed_global_id                  Python 注入
├── seed_sha256                     Python 注入
├── environment
│   ├── summary                     1-2 句环境简述
│   └── description                 背景、参与者、主要内容和范围
├── entities[]
│   ├── name
│   ├── description                 业务含义、用途和重要联系
│   └── key_attributes[]            对实际工作重要的属性及含义
├── tools[]
│   ├── name
│   └── description                 处理对象、动作、所需信息和结果
├── tasks[]
│   ├── name
│   └── description                 用户目标、过程、涉及内容和结果
└── research_notes
    ├── data_directions[]           后续应寻找的数据、状态或领域文件
    ├── sources[]
    │   ├── url
    │   └── description             该来源支持了什么理解
    └── open_questions[]            仍需真实来源确认的问题
```

## 5. 程序检查

Python 检查以下确定性要求：

- 结构符合 `scenario_research.schema.json`；
- Seed global_id 和 SHA-256 与 Step 0 一致；
- 实体、工具和任务内部没有重复名称；
- Seed 中每个参考工具都使用原名称形成独立说明；
- 任务数量足以分别覆盖 Seed 中已有参考任务；
- 正式文件与保存收据中的 SHA-256 一致；
- Agent 执行期间 Step 1 输入、Schema 和校验程序没有被改写。

这些检查只保证简报结构完整且忠于 Seed。来源是否可访问、数据是否足够、实体能否真正物化，由后续
真实调查和数据集成决定。

## 6. 停止条件

环境简述能够快速识别场景，详细描述足以解释使用者、内容和范围；参考工具与任务已经覆盖；核心
实体边界和属性明确；关键结论具有来源支持；后续要寻找的数据和仍待确认的问题已经列出；继续搜索
只会重复现有语义时，Step 1 结束。
