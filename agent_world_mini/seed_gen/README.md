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
- `data/smithery_140_v1_0824.json`：按环境种子 v1 结构转换的 140 条正式产物。
- `data/env示例1_v1.json`：正式结构片段及带字段说明的单环境阅读样例。
- `data/prepared_environments_10.json`：整理过程中的小规模参考样例。
- `data/smithery_servers.json`：保留 Smithery 列表接口原字段的完整快照。
- `data/smithery_servers_report.json`：完整快照的分页、数量和 SHA-256 校验信息。
- `data/theme_sources.json`：内置主题来源。

环境种子格式见 `../schemas/环境种子契约-v1.0.md` 和
`../schemas/env_seeds.schema.json`。注释样例中的中文 key 和省略号不属于正式格式，
批量机器产物以 `data/smithery_140_v1_0824.json` 为准。

## 维护命令

配置仓库根目录 `.env` 中的 `SMITHERY_API_KEY` 后，抓取 Smithery 的
`remote=true` 和 `remote=false` 列表：

```powershell
python -m agent_world_mini.seed_gen.scripts.fetch_smithery_servers
```

脚本使用稳定分页种子，将结果写入 `data/smithery_servers.json` 和
`data/smithery_servers_report.json`。列表记录保留来源字段，不请求每个服务的工具详情。

将 `data/prepared_environments.json` 转换为 v1 环境种子：

```powershell
python -m agent_world_mini.seed_gen.scripts.convert_smithery_140_v1
```

转换结果写入 `data/smithery_140_v1_0824.json`。工具定义保持原样，环境名称使用
`qualifiedName`，来源 URL 为 `https://smithery.ai/servers/{qualifiedName}`，且不保留
环境级 `iconUrl`。
