# DataGen

DataGen 使用研究 Agent 从公开来源构建不带工具的环境。业务记录必须来自
实际访问的来源。Agent 负责调研、下载和解释业务语义；Python 负责提取文件事实、
计算丰富度、验证 Agent 声明和分类发布。

## 默认输出

未显式指定 `--output-dir` 时，生成现场和最终环境都写入 OSS：

```text
/mnt/oss-bucket/sunshuo/AgentWorld/environment/data_gen_v2/
├── rich/<seed_id>/
├── not_rich/<seed_id>/
├── failed/<seed_id>-<timestamp>/
└── .building/
```

不会再把新下载的数据写入仓库下的 `AgentWorldData`。`--output-dir` 只用于测试或
临时调试，此时不自动增加质量分类目录。

## 代码结构

```text
data_gen/
├── __main__.py                         CLI 入口
├── pipeline.py                         阶段顺序、循环、超时和失败现场
├── internal_schemas/                   DataGen 内部检查点，不是跨阶段契约
│   ├── research_request.schema.json
│   ├── data_checkpoint.schema.json
│   ├── source_inventory.schema.json
│   ├── research_report.schema.json
│   └── quality_profile.schema.json
├── steps/
│   ├── step01_build_research_request.py
│   ├── step02_collect_source_data.py
│   ├── step03_profile_collected_data.py
│   ├── step04_evaluate_data_richness.py
│   ├── step05_expand_source_data.py
│   ├── step06_describe_environment.py
│   ├── step07_validate_environment.py
│   ├── step08_repair_environment.py
│   └── step09_publish_environment.py
└── analysis/
    ├── entity_profiling.py
    ├── capability_extraction.py
    ├── task_space_estimation.py
    └── record_extraction.py
```

每个 Agent Prompt 都在使用它的步骤文件内。`generator.py`、`profiling.py`、
`capabilities.py`、`composition.py` 和 `metadata.py` 只保留旧导入兼容，不是权威实现。

## 生成流程

```text
Step 01  Seed + 环境契约 -> research_request.json             Python
Step 02  发现来源并下载首轮真实数据                          Agent 1 次
Step 03  读取文件并生成 data_profile.json                    Python
Step 04  计算 quality_profile.json 和数据缺口                 Python
Step 05  按缺口追加真实数据，再回到 Step 03                  Agent 0～3 次
Step 06  基于冻结文件声明 environment/sources/report          Agent 1 次
Step 07  校验契约、路径、来源、字段和关系                     Python
Step 08  只修复环境声明，再回到 Step 07                      Agent 0～2 次
Step 09  发布到 rich/not_rich/failed                          Python
```

扩展轮只能新增文件。已经下载的 raw/entity/derived 文件不能被删除或改写，新增
分页必须保存为新 raw 文件。Step 06 以后业务文件完全冻结，环境描述和修复 Agent
只能修改 `environment.json` 以及 `provenance` 下的声明文件。

正常情况下会调用 Agent 2～5 次；理论上最多 7 次。这里的一次是一次 Agent 运行，
Agent 在运行内部可以执行多次搜索、下载和文件检查。

## 语义边界

Python 可以可靠得到文件路径、格式、哈希、字段基础类型、记录数量、唯一值数量，
也可以验证一个已声明外键的实际覆盖率。但 Python 不能仅凭字段名可靠判断文件的
业务含义、实体边界或业务关系。

因此 `data_profile.json` 中的关系是候选事实，不能直接成为最终契约。Step 06 的
Agent 结合 Seed、来源和实际内容生成最终环境描述；Step 07 再用真实文件验证它。
`analysis/record_extraction.py` 只为画像和校验提供兼容辅助，不再覆盖 Agent
写出的 `environment.json`。如果 Step 06 失败，流水线会失败或进入声明修复，不会
用启发式代码悄悄生成一份替代环境。

## 采集规则

默认 `AcquisitionPolicy`：

- 官方结构化数据面总量不超过 50,000 条时尽量完整下载。
- 更大的核心数据面按时间、类别、地域和数值区间稳定分层，目标为 25,000 条；
  这个数字是下载策略，不是环境合格线。
- 关系数据最多保留 100,000 条真实边，并优先取得全部被引用目标。
- lookup、类别和定义表尽量完整下载；时间序列保留选中对象的完整时间范围。
- 单个核心文件不超过 256 MiB 时完整下载。
- raw 上限 512 MiB，完整 workspace 上限 768 MiB，raw 文件最多 200 个，
  来源最多 50 个。
- 默认最多四轮，总时限 1,800 秒；连续两轮没有新增能力、关系或组合链，且任务
  容量增长低于 5% 时停止扩展。

Agent 不能因为达到某个记录数就宣布完成。`collection_status=complete` 必须在
`source_inventory.json` 中提供游标结束、官方总量取完、仓库枚举完毕或分层采集
完成的证据。

## 数据面清单

`provenance/source_inventory.json` 记录每个已发现的数据面：

- URL、数据面类型和核心/扩展优先级；
- 实体和相关数据面；
- page/offset/cursor/download 分页方式；
- 官方总量、已采集页数和记录数；
- pending/partial/complete/blocked 状态；
- 实际 raw 文件及完成证据。

清单由 `data_gen/internal_schemas/source_inventory.schema.json` 校验。已采集 URL 和
raw 文件必须同时出现在 checkpoint 中，相关数据面 ID 必须真实存在。这些 Schema
只约束 DataGen 内部检查点，不属于四个跨阶段正式产物。

## 丰富度判定

环境不再按实体总行数判定 rich。程序从实际数据抽取能力原子：

```text
资源或实体 + 操作族 + 证据字段/格式特征 + 支持数据范围
```

随后按输入输出实体连接能力，枚举长度 2-5 的组合链。默认 rich 必须同时满足：

- 独立能力原子不少于 24；
- 至少 6 类操作族；
- 至少 12 个独立业务字段或格式特征；
- 至少 3 种可组合转换；
- 至少 30 种工具链形状，其中长度至少为 3 的不少于 10 种；
- 估算可实例化任务不少于 1,000 个；
- 所有核心数据面都有带证据的 complete 状态。

每种链最多贡献 100 个估算任务，避免一个巨大字段通过组合数爆炸绕过质量门。
同一个数值字段可以支持 rank/compare/aggregate 三种操作，但丰富度中仍只算一个
独立数据证据。

详细结果写入 `provenance/quality_profile.json`，逐轮画像保存在
`provenance/quality_history/`。`not_rich` 表示环境包本身合法，但不足以作为批量复杂
任务的正式输入；它不会被伪装成 rich。

## 最终目录

```text
<environment>/
├── environment.json
├── workspace/
├── provenance/
│   ├── research_request.json
│   ├── source_inventory.json
│   ├── data_checkpoint.json
│   ├── data_profile.json
│   ├── quality_profile.json
│   ├── quality_history/
│   ├── acquisition_rounds/
│   ├── sources.json
│   └── research_report.json
└── validation.json
```

`environment.json` 仍然只保存业务资源描述，不保存调研过程、质量分数或工具。

## 运行

```bash
python -m env_gen.data_gen \
  --seed-path seed_gen/theme_sources.json \
  --seed-id openalex-publication-research \
  --schema-path schemas/environment.schema.json
```

`schemas/environment.schema.json` 是给 Agent 阅读的契约结构示例；运行时会自动使用同目录
`schemas/validation/environment.schema.json` 做机器校验。若直接传入一个带 `$schema` 的自定义
JSON Schema，则按传入文件校验。

默认使用 `gpt-5.6-terra` 和 `high` 推理强度，并允许 Agent 在 workspace-write
沙箱中通过网络命令访问公开来源。调试输出到本地临时目录时可以显式传：

```bash
--output-dir /tmp/agent-world/openalex
```
