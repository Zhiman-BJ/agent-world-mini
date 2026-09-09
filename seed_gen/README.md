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
- `data/theme_sources.json`：内置主题来源。

已有 v1.0 Smithery 产物可原地迁移为 v1.1：

```powershell
python -m seed_gen.migrate_smithery_v11
```

环境种子格式见 `../schemas/环境种子契约-v1.0.md` 和
`../schemas/env_seeds.schema.json` 是种子结构示例；机器校验使用
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
