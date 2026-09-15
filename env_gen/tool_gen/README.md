# ToolGen

ToolGen 只负责一件事：把 DataGen 已经准备好的数据环境，补成一组真正能执行的工具。

## 它从上游拿什么

一个正常的 DataGen 环境包至少包含：

```text
environment.json                    业务场景、Record Set、关系和访问边界
environment.md                      便于阅读的环境说明（若上游提供）
state/records.sqlite                所有结构化 Record Set
state/filesystem_scopes/<scope_id>/ 工具可直接处理的文件或项目目录
provenance/scenario_research.json   场景研究与参考工具
provenance/integration_plan.json    数据如何集成为最终资产
provenance/*_profile.json           字段、关系、质量和来源画像
```

原 MCP 的工具说明优先从 DataGen 的 `scenario_research.json` 读取，也可由 Seed 的
`init_ref_tools` 补充。它们提供现实业务能力线索；最终实现仍以 `environment.json` 和 `state/`
中已经发布的数据为准。

## 实际流程

1. Python 在环境包中创建 `tool_generation/`，写一份轻量 `context.json`，列出 DataGen v2
   的声明、状态和 provenance 入口。
2. Sol Agent 先盘点环境支持的完整工作面：按键读取、查询筛选、统计分析、实体关系、业务状态
   变化、文件处理和跨资产工作流。每项正式能力同时记录现实操作来源和当前环境的执行后端，
   写入 `capability_inventory.json`。
3. 专业环境的软件计划会解析为共享软件 Profile。Python 版本与依赖规格相同的环境复用同一
   Profile；依赖组合不同的环境保持隔离，同时共享 uv 和 npm 下载缓存。
4. Sol Agent 再把能力整理成 `action_plan.json`。一个工具对应一个完整用户目的；相同目的的
   筛选或排序做成参数，目的或副作用不同的动作分别保留。每个动作同时说明操作对象、调用前
   状态和成功后的状态变化。
5. Python 每次把 5 个相关动作交给 Agent 编写，分别保存到 `drafts/<tool>.json`。这样一个正常
   环境即使规划出几十个工具，也只需少量 Agent 会话，并且可以从已有草稿继续。
6. 某一批缺少草稿时记录进度并继续，最后集中重试一次。
7. Runtime 在隔离副本中执行草稿里的真实调用，核对返回值、实际状态变化和工具声明的目标
   资源。通过验证的工具写入独立的 `tools.json`，现实来源索引写入
   `tool_generation/tool_grounding.json`。
8. 正式交付将工具、环境状态和软件 Profile 分别发布，并用绑定文件记录三者的对应关系。

例如，工单数据中存在工单、处理人、状态和解决说明，参考系统也支持分配和解决工单，而且
当前 Record Set 可写，ToolGen 才会生成对应工具。对于 CAD 等专业软件，建模动作需要对应
官方 API、CLI 或真实项目文件；改变一份自建状态 JSON 不等价于执行专业软件操作。

## 真实性准入

`decision=implement` 的能力必须同时具备：

- `reality_evidence`：参考工具、官方文档或公认的标准数据操作所支持的现实动作；
- `execution_backends`：当前环境中实际承载调用的 Record Set 或 Filesystem Scope；
- 本地字段、关系或文件依据，说明现实动作怎样适配到当前环境；
- Runtime 可执行样例，验证返回结果与真实状态变化。

专业状态变化需要参考工具或官方文档依据。参考接口与本地字段不同时，可按相同业务语义进行
`direct`、`adapted`、`narrowed` 或 `composed` 适配。

## 运行

直接处理一个 DataGen 包：

```bash
python3 -m env_gen.tool_gen <环境包目录>
```

同时使用当前种子中的 MCP 工具说明：

```bash
python3 -m env_gen.tool_gen <环境包目录> \
  --seed-path seed_gen/data/smithery_140_v1_0824.json \
  --seed-id <global_id>
```

也可以直接传一个工具线索数组：

```bash
python3 -m env_gen.tool_gen <环境包目录> --tool-hints hints.json
```

默认 Agent 是 `gpt-5.6-sol`，使用本机 Codex 登录配置。能力盘点和规划允许联网核对现实业务
操作；草稿阶段只使用本地 DataGen 数据和 Runtime API。

服务器默认将正式结果发布到：

```text
/data/agentworld-toolgen-results/
├── tools/<package_id>/
├── environments/<package_id>/
├── software_profiles/profiles/<profile_id>/
└── bindings/<package_id>.json
```

`bindings/<package_id>.json` 保存工具路径、环境路径和软件 Profile。下游以绑定文件为入口，
可以独立读取工具描述，也可以在执行时组合对应环境状态和专业软件。
下游加载、任务级状态隔离和工具调用方式见
[`ToolGen 下游交付契约 v1.0`](../../schemas/ToolGen下游交付契约-v1.0.md)。

## 中间产物

```text
tool_generation/context.json              Agent 的入口索引
tool_generation/reference_tools.json      上游 MCP 和场景研究中的能力线索
tool_generation/capability_inventory.json  当前数据完整支持和不支持哪些能力
tool_generation/action_plan.json          为什么生成这些工具
tool_generation/drafts/*.json             工具代码和执行样例
tool_generation/progress.json             已完成工具和当前工具
tool_generation/tool_validation.json      每个工具的实际执行结果
tool_generation/tool_grounding.json       每个正式工具的现实来源和执行后端
tools.json                                下游使用的正式工具集合
```

这些中间文件用于查看 ToolGen 做了什么；下游使用原始 `environment.json`、`state/` 和新生成的
`tools.json`。

完整的上下游数据流和逐阶段说明见
[`docs/SYSTEM_ARCHITECTURE_ZH.md`](../../docs/SYSTEM_ARCHITECTURE_ZH.md)。真实 World Bank 运行的
中间产物示例见
[`docs/examples/toolgen-world-bank/`](../../docs/examples/toolgen-world-bank/README.md)。
