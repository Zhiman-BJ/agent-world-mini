# 校验 Schema

`schemas/` 外层的四个 `.schema.json` 文件是契约结构示例，直接展示 Agent 需要生成的
JSON 形状；本目录保存真正用于机器校验的 JSON Schema Draft 2020-12 文件。

| 结构示例 | 机器校验文件 | 校验对象 |
| --- | --- | --- |
| `../env_seeds.schema.json` | `env_seeds.schema.json` | 环境种子数组 |
| `../environment.schema.json` | `environment.schema.json` | 不带工具的环境对象 |
| `../tool.schema.json` | `tool.schema.json` | 单个工具对象 |
| `../complete_environment.schema.json` | `complete_environment.schema.json` | 带工具的完整环境对象 |

跨资源引用、工作区文件存在性、工具代码编译和回滚行为不能只由 JSON Schema 表达，仍由
对应的 Python Validator 检查。
