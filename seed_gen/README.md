# Seed generation

该目录集中管理环境种子的运行时代码、Smithery 数据、转换脚本和契约文档。

```text
seed_gen/
├── catalog.py, themes.py     # 目录读取、主题投影与选择
├── data/                     # 运行时数据、原始快照、样例和转换产物
└── scripts/                  # 可独立执行的数据抓取与转换脚本
```

## 数据

- `data/prepared_environments.json`：批量环境生成默认读取的 140 条 Smithery 环境目录。
- `data/smithery_1000_v1_0902.json`：按环境种子 v1.1 结构整理的 Smithery 批量产物。
- `data/smithery_140_v1_0824.json`：历史 v1.0 格式的 140 条产物。
- `data/env示例1_v1.json`：正式结构片段及带字段说明的单环境阅读样例。
- `data/prepared_environments_10.json`：整理过程中的小规模参考样例。
- `data/smithery_servers.json`：保留 Smithery 列表接口原字段的完整快照。
- `data/smithery_servers_report.json`：完整快照的分页、数量和 SHA-256 校验信息。
- `data/theme_sources.json`：内置主题来源。

### 从列表快照续爬环境种子

`catalog.py` 不再调用 LLM 整理目录项。它读取按 `useCount` 降序保存的
`data/smithery_servers.json`，默认取前 1000 条并仅请求每个服务的详情，直接
写成环境种子 v1.1 契约数组：

```powershell
python -m seed_gen.catalog `
  --source seed_gen/data/smithery_servers.json `
  --output seed_gen/data/smithery_1000_v1_0902.json `
  --limit 1000
```

详情工具统一写为 `name/type/module/description/input/output`，MCP 工具固定使用
`type=function`、`module=null`；`iconUrl` 不写入种子核心数据，其余详情元数据置于
`others.source_metadata`。工具分类计数写入 `environment.nums`；Smithery 的 `class` 和
`class_func` 均为 0，`function` 和 `all_func` 均为参考工具数量。产物不再写入
`others.tool_count/data_directions/organization_status`。该过程不调用 LLM，也不需要任何模型配置；
Smithery API 的鉴权仍按接口要求使用 `SMITHERY_API_KEY`。

后续种子导出默认每个环境最多保留 100 个有效参考工具，可用 `--max-tools 1..100`
进一步限制。超限时仅在所有工具都有合法的工具级 `useCount`（或 `use_count`）时按其降序选择；
否则保留来源顺序前 100 个（或指定限额）。服务级 `use_count` 不参与工具内部排序。
选择记录写入 `others.tool_selection`，`environment.nums` 统计保留后的工具数量。
该策略不修改既有快照，也不调用 LLM。

### 采集后的适用性筛选

维度、分数锚点及准入建议见 [MCP种子筛选与评分-v1.md](MCP种子筛选与评分-v1.md)。
全量材料审计与 8 个有证据的静态评分样例可离线复现：

```powershell
python -m seed_gen.scripts.analyze_mcp_seeds --reviews seed_gen/data/mcp_seed_review_examples_v1.json
```

报告默认输出到 `../reports/smithery_seed_screening_20260911/analysis.md` 和 `analysis.json`。
未审核的种子总分为 `null`；无工具记录归入待补采，不直接判为不适用。静态评分不代表运行验证通过。

### 仅补爬空工具种子

详情请求默认最多尝试 5 次，HTTP 成功但没有有效工具也会重试；间隔按 1、2、4、8 秒退避。
HTTP 401/403/404/410 不盲目重复请求，429 等临时错误可以重试；服务指定的长冷却时间会记录后留待下次。
所有工作线程共享请求节流，默认请求间隔至少 1 秒；遇到 429 共享冷却时间，无 Retry-After 时至少等待 30 秒。

```powershell
python -m seed_gen.catalog --retry-empty --output seed_gen/data/smithery_1000_v1_0902.json --workers 2 --attempts 5 --retry-delay 2 --request-interval 1
```

该模式读取 `--output` 中的现有数组，只请求 `init_ref_tools=[]` 的环境，不重新获取列表，
也不使用 `--limit` 截断补爬目标。成功后更新工具、`environment.nums`、`others.tool_selection`
和 `others.tools_refresh`；已有非空工具的记录与补爬失败的记录保持不变。
名字、ID、序号、环境描述及原快照版本保留；新工具采集时间单独记入 `tools_refresh.fetched_at`。
单环境工具仍最多 100 个。每完成 10 条原子保存进度，重复运行只请求尚为空的记录。

每次运行在 `reports/smithery_tools_retry_20260911/<UTC时间>/` 下保存 `before.json` 原文件备份、
`report.json` 尝试结果以及 `details/` 中成功获取的详情（包含来源 Schema）。可用 `--report-dir` 改目录。
报告区分空响应、HTTP 状态和请求异常；耗尽重试仍为空表示未解决，不能据此声称网站无工具。
补爬修改了源快照后，旧筛选分析的 SHA-256 和统计仅适用于补爬前版本，应另存新分析结果。

2026-09-11 本轮从 550 条空记录中补回 535 条，新增 6,766 个工具，剩余 15 条：
12 条空响应、2 条 404、1 条返回的 7 个工具全部缺描述。MoodTrip 补回 12 个工具。
完整结果见 `../reports/smithery_tools_retry_20260911/summary.md`；补爬后材料统计见
`../reports/smithery_seed_screening_after_retry_20260911/analysis.md`。
可运行 `python -m seed_gen.scripts.validate_smithery_tools_retry` 对照备份验证修改范围和 Schema。

### Agent 自动评分与筛选

使用本机 `CodexAgentClient` 对有工具的环境逐条进行语义审核。Agent 只能读取提示词中提供的种子摘要，
不能联网或修改工作区；程序校验六项分数、真实工具引用、任务链和准入阈值。无工具记录由确定性规则直接标记
`needs_tool_evidence`。结果不会覆盖种子 JSON，而是写入独立报告，适合先小批量试跑：

```powershell
# 先试评前 10 条；默认单并发，避免一次启动大量 Agent
python -m seed_gen.scripts.agent_score_mcp_seeds --limit 10 --workers 1 `
  --output-dir reports/smithery_agent_scoring_20260911

# 继续未完成项；失败项也会重试，已完成项不会重复调用
python -m seed_gen.scripts.agent_score_mcp_seeds --workers 1 --resume `
  --output-dir reports/smithery_agent_scoring_20260911
```

全量运行将审核 975 条记录，调用成本和耗时取决于本机 Codex 配额；建议先检查 10 条输出。
Agent 服务返回 429/503 或断开时，增加 `--retries`，保持 `--workers 1` 或小并发。
`agent_scores.progress.json` 支持断点，`agent_scores.json` 是最终结构化结果，
`agent_logs/` 保存每次会话日志；运行元数据记录源文件 SHA-256、模型和失败项。
评分字段中的 `runtime_verified` 永远为 `false`，静态 Agent 评分不能替代真实环境任务执行验收。

运行中也会导出 `screening_summary.md` 和 `screened/` 分类种子（每 10 条刷新，结束时再次刷新）。
分类种子保留源记录完整内容，未评分项单列在 `screened/pending_ids.json`，不视为淘汰。
断点使用原子替换；`--resume` 会核对源文件 SHA-256，源数据变更后应使用新目录。

若进程中断但 `agent_logs/` 中已有完整响应，可先离线恢复，不产生模型调用：

```powershell
python -m seed_gen.scripts.agent_score_mcp_seeds --resume --recover-logs --recover-only `
  --output-dir reports/smithery_agent_scoring_20260912_full
```

恢复逐条核对日志中的审核材料和当前提示词，校验分数与工具引用，保留已有评分。
详情见 `recovery_report.json`；无效响应留待正常续跑重试。
恢复前必须停止同目录的评分进程，避免同时写断点。
长批次应通过 `Start-Process -WindowStyle Hidden` 启动并用 `-RedirectStandardOutput` /
`-RedirectStandardError` 指定不同日志文件，避免无人读取的终端管道阻塞进度写入。
保持一个评分进程，使用 `--workers 2 --checkpoint-every 1 --resume` 续跑。

批次退出后执行最终验收，检查所有源记录均已评分、分类门槛、工具引用、源 Schema 和分类导出：

```powershell
python -m seed_gen.scripts.finalize_agent_seed_scores --output-dir reports/smithery_agent_scoring_20260912_full
```

若评分与分类不一致，先确认没有同目录评分进程，再添加 `--prepare-repair`：它先保存
`pre_repair_*.json`，仅从断点中移出需复核条目；原始会话日志和种子不变。
随后用评分器的 `--strict-routing --resume` 复核，最后再次验收。
最终产物为 `final_summary.md`、`final_audit.json` 和 `screened/*.json`。

Windows 上可以用 `monitor_agent_seed_scores` 接管已有批次的后续处理：

```powershell
python -u -m seed_gen.scripts.monitor_agent_seed_scores --workers 2 --max-rounds 3 `
  --output-dir reports/smithery_agent_scoring_20260912_full
```

监控器每 30 秒检查当前批次，不会另启重复评分；进程退出后自动验收，定向复核最多 3 轮。
发现源数据变化、重复评分进程或复核仍失败时记为 `needs_attention`，不会误报完成。
`monitor_status.json` 是监控状态，`monitor_logs/` 保留复核批次日志。启动它时同样应将 stdout/stderr 重定向到文件。

### 历史产物迁移

已有 v1.0 Smithery 产物可原地迁移为 v1.1：

```powershell
python -m seed_gen.migrate_smithery_v11
```

环境种子格式见 `../schemas/环境种子契约-v1.1.md`；
`../schemas/env_seeds.schema.json` 是种子结构示例（真实场景种子产物）；机器校验使用
`../schemas/validation/env_seeds.schema.json`。注释样例中的中文 key 和省略号不属于正式格式，
批量机器产物以 `data/smithery_1000_v1_0902.json` 为准。

## 维护命令

配置仓库根目录 `.env` 中的 `SMITHERY_API_KEY` 后，抓取 Smithery 的
`remote=true` 和 `remote=false` 列表：

```powershell
python -m seed_gen.scripts.fetch_smithery_servers
```

脚本使用稳定分页种子，将结果写入 `data/smithery_servers.json` 和
`data/smithery_servers_report.json`。列表记录保留来源字段，不请求每个服务的工具详情。

历史脚本可将 `data/prepared_environments.json` 转换为 v1.0 环境种子：

```powershell
python -m seed_gen.scripts.convert_smithery_140_v1
```

转换结果写入 `data/smithery_140_v1_0824.json`。工具定义保持原样，环境名称使用
`qualifiedName`，来源 URL 为 `https://smithery.ai/servers/{qualifiedName}`，且不保留
环境级 `iconUrl`。
