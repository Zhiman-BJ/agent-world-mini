# ToolGen

ToolGen 只负责一件事：把 DataGen 已经准备好的数据环境，补成一组真正能执行的工具。

## 它从上游拿什么

一个正常的 DataGen 环境包至少包含：

```text
environment.json              资源、可写范围和业务规则
workspace/                    Agent 和工具实际读取、修改的 JSON/CSV/文本/目录
provenance/research_request.json
provenance/research_report.json
provenance/source_inventory.json
provenance/data_profile.json
provenance/quality_profile.json
provenance/sources.json       上游调研范围、实体关系、字段画像、规模和来源
```

原 MCP 的工具说明来自同一条上游 seed 的 `init_ref_tools`。它是业务能力线索，不是可以直接复制的实现：最终工具仍要以 `workspace/` 中已经存在的真实数据为准。

## 实际流程

1. Python 在环境包中创建 `tool_generation/`，写一份很轻的 `context.json`。里面只有文件路径和大小，不复制整个 workspace。
2. Luna Agent 第一次只做完整能力盘点：查询、关系、状态变化、文件操作，以及原 MCP 能力线索分别能否由当前数据实现，写入 `capability_inventory.json`。
3. Luna Agent 根据盘点写 `action_plan.json`。一个动作可以覆盖几个相近的能力，所以能力数量不等于工具数量；例如排名、比较、聚合可以是一个带参数的分析工具。
4. Python 按 `action_plan` 逐个调用 Agent，每次只生成一个 `drafts/<tool>.json`，完成一个就更新 `progress.json`。已有草稿会直接保留并跳过，因此中断后可以从当前动作继续。
5. 某个动作生成失败时先记录并继续后面的动作；全部动作结束后再回头重试一次。仍失败的动作标为 skipped，不影响其他工具进入验证。
6. Runtime 在临时副本中执行所有已生成草稿的真实调用。只有通过执行验证的工具才会写入 `environment.json.tools[]`；跳过或拒绝的工具会留在 `tool_validation.json` 中说明原因。

例如，工单数据中存在工单、处理人、状态和解决说明，而且工单资源可写，那么可以自然生成“分配工单”和“解决工单”。如果数据中没有删除语义，就不会为了凑齐 CRUD 硬造 `delete_ticket`。

## 运行

直接处理一个 DataGen 包：

```powershell
python -m env_gen.tool_gen <环境包目录>
```

同时使用当前种子中的 MCP 工具说明：

```powershell
python -m env_gen.tool_gen <环境包目录> `
  --seed-path seed_gen/data/smithery_140_v1_0824.json `
  --seed-id <global_id>
```

也可以直接传一个工具线索数组：

```powershell
python -m env_gen.tool_gen <环境包目录> --tool-hints hints.json
```

默认 Agent 是 `gpt-5.6-luna`，使用本机 Codex 登录配置。ToolGen 不联网，因为真实数据已经由 DataGen 放进环境包。

## 中间产物

```text
tool_generation/context.json          Agent 的入口索引
tool_generation/reference_tools.json  上游 MCP 能力线索
tool_generation/capability_inventory.json  当前数据完整支持和不支持哪些能力
tool_generation/action_plan.json      为什么生成这些工具
tool_generation/drafts/*.json         工具代码和执行样例
tool_generation/progress.json         已完成工具和当前工具
tool_generation/tool_validation.json  每个工具的实际执行结果
```

这些文件用于查看 ToolGen 做了什么；下游真正使用的是补全后的 `environment.json` 和原来的 `workspace/`。
