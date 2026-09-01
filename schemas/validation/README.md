# 校验 Schema

当前环境声明的唯一正式机器 Schema 是 `schemas/environment.schema.json`。新 DataGen、Validator
和发布流程统一读取该文件，不再使用带版本号的环境 Schema 文件名。

本目录继续保存环境种子、工具和任务的发布校验 Schema。这里的
`environment.schema.json` 属于旧版 `resources/rules` 环境协议，只供尚未迁移的旧调用方使用；
新环境不得读取它。

| 契约入口 | 机器校验文件 | 校验对象 |
| --- | --- | --- |
| `../env_seeds.schema.json` | `env_seeds.schema.json` | 环境种子数组 |
| `../environment.schema.json` | `../environment.schema.json` | 当前 Record Set / Filesystem Scope 环境对象 |
| `../tool.schema.json` | `tool.schema.json` | 单个工具对象 |
| `../complete_environment.schema.json` | `complete_environment.schema.json` | 带工具的完整环境对象 |

跨资源引用、工作区文件存在性、工具代码编译和回滚行为不能只由 JSON Schema 表达，仍由
对应的 Python Validator 检查。
