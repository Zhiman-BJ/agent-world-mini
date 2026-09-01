# DataGen

DataGen 把一个简略环境 Seed 转换为可离线使用、可审计的 Agent-World 环境数据包。主流程包含
一个准备阶段和五个业务阶段，详见 [`docs/PIPELINE_OVERVIEW_ZH.md`](docs/PIPELINE_OVERVIEW_ZH.md)。

十环境最终产物的独立 HTML 位于
[`reports/step1_environment_audit.html`](../../reports/step1_environment_audit.html)，无需启动服务即可
直接打开。报告只展示发布后的实体、关系、资源文件、文件索引、来源血缘和最终校验，不展示
场景候选或调研过程。生成方式和发布后审计树见总览文档的“发布后批量审计树”。

## 流程

```text
Seed
  ↓
Step 0 选择 Seed 并准备运行上下文
  ↓ selected_seed.json + run_config.json
Step 1 场景研究
  ↓ scenario_research.json
Step 2 来源探索与代表性取样
  ↓ source_plan.json + source_inventory.json
Step 3 定向深采、物化与集成画像
  ↓ integration_plan.json + records.sqlite + Filesystem Scopes
Step 4 冻结事实并导出 environment.json
  ↓
Step 5 独立校验、哈希收口、原子发布
```

Step 1 把 Seed 具象化，但使用宽松 Schema；Step 2 用少量真实样本确认来源结构；Step 3 才按样本
选择来源、定向补量、建立统一 Record Set 和 Filesystem Scope。场景研究不是来源白名单。

## 目录结构

```text
env_gen/data_gen/
├── config.py
├── run_pipeline.py
├── analysis/
│   ├── scenario_research.py
│   ├── source_plan.py
│   ├── entity_profiling.py
│   ├── operation_candidates.py
│   ├── composition_estimation.py
│   ├── quality.py
│   ├── validator.py
│   └── checkpoint_schemas/
├── steps/
│   ├── step0_prepare_run.py
│   ├── step1_research_scenario.py
│   ├── step2_explore_sources.py
│   ├── step3_integrate_data.py
│   ├── step4_freeze_environment.py
│   └── step5_validate_and_publish.py
│   ├── exploration/     # Step 2 来源探索命令和 Prompt
│   ├── integration/     # Step 3 集成命令、转换运行器和 Prompt
│   ├── collection/      # 下载/Raw/兼容性支持命令
│   │   ├── commands/
│   │   └── support/
│   └── common/
│       ├── constants.py
│       ├── control_io.py
│       └── workspace_files.py
└── docs/
```

## Step 1 产物

`provenance/scenario_research.json` 包含：

- 可快速识别场景的环境简述和详细说明；
- 稳定业务实体及其关键属性；
- Seed 参考工具和调研补充工具的语义说明；
- 典型任务及其目标、过程和结果；
- 后续数据方向、关键 HTTP(S) 来源和待确认问题。

Step 1 使用轻量语义结构。身份、名称唯一性、参考工具与任务覆盖及来源 URL 格式由程序检查；
最终字段、关系和文件工作区要等真实数据取得后再确定。

## Step 2 命令

Step 2 只做来源探索和代表性取样，不创建最终 Entity 或 SQLite 模型。Agent 通过
`.datagen/explorectl` 写正式数据：

```text
save-source-plan   保存来源入口、精确 URL 和探索状态
download           下载一个已登记精确 URL 的代表性样本
download-batch     并发下载多个已登记精确 URL
assess             重算来源画像和结构盲区
finalize           ready/insufficient_public_data 收口
```

## Step 3 命令

Step 3 读取 Step 2 的实际样本，通过 `.datagen/integratectl` 选择来源、定向深采并物化最终资产：

```text
save-plan          保存 Record Set、Filesystem Scope、关系和需求绑定
download           对选中来源定向补采已登记 URL
build-record-set   运行确定性转换并物化 SQLite 表
build-scope        复制/解包真实文件工作区
assess             重算集成度、关系、路径和多源连接
save-field-review  对照 Raw 保存当前字段分布提示的核实证据
finalize           integrated 收口
```

Step 2 通过 `source_plan.research_refinements` 记录真实样本对 Step 1 假设的确认、修订、淘汰或新增
结论。Step 3 的 integration plan 必须从这些实际样本长出来，而不是把每个来源机械变成一张表。

## 运行态与发布数据

```text
<environment_root>/
├── environment.json
├── state/
│   ├── records.sqlite
│   └── filesystem_scopes/<scope_id>/
└── provenance/
    ├── raw/
    ├── transformations/
    └── *_profile.json
```

采集期间只有 `workspace/raw/`；它在发布时冻结到 `provenance/raw/`，不会暴露给任务。结构化记录
统一进入 `records.sqlite`，需要工具直接处理的文件或项目目录进入命名 Scope。Record 可以用声明了
`filesystem_path` 的顶层字段保存 Scope 相对路径。v2 不再使用 `workspace/entities/`、
`workspace/derived/` 或 `reports/` 作为最终资源类型。

## 质量判断

Step 3 每轮先运行 integration profile，再运行 environment quality profile。缺口可以触发定向补采、
转换修复、Scope 拆分或移除无关资产；补采后必须重新物化并重新画像。Step 4/5 只冻结和复验，不再
负责发现性下载。

integration profile 同时输出有界字段频次、样本和形态，并提示异常偏斜或跨字段值域重叠。这类提示
需要 Agent 回到 Raw 判断，不能由通用 Python 猜测领域语义；存在提示时，必须保存与当前画像哈希
绑定的 `field_review.json` 后才能收口。发现抽取错误时只重建受影响的 Record Set。

`quality_tier=rich` 的硬门槛来自：

- 核心来源收口；
- 场景数据需求具有真实来源、Record Set/Scope 和非空字段证据；
- 必要领域文件实际存在；混合环境中的核心记录与核心文件具有真实路径索引；
- 核心 Record Set 的记录深度和实际填充业务字段达到策略要求；
- 关系闭合且 gap 在允许范围内。

`operation_candidate_diagnostics` 和 `composition_estimate` 只描述数据潜力，不参与 rich 门槛，
也不表示工具已实现、任务已生成或组合链已执行。

## 运行

```bash
PYTHONPATH=. python -m env_gen.data_gen \
  --seed-path seed_gen/data/smithery_140_v1_0824.json \
  --global-id smithery_sidneybissoli_ibge_br_mcp_8 \
  --schema-path schemas/environment.schema.json \
  --output-dir /tmp/ibge-env \
  --overwrite
```

Web Search 默认开启，可用 `--no-enable-web-search` 关闭。网络下载仍由 Step 2 的受控命令执行。

## 验证

```bash
PYTHONPATH=. python -m compileall -q env_gen/data_gen
PYTHONPATH=. pytest -q
```
