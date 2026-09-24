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

批量处理多个环境时使用：

```bash
agent-world-tool-gen-batch <上游环境目录> <本批次工作目录> --workers 8
```

批量入口会保存每个环境的日志和心跳状态，隔离单环境失败，并在再次启动时跳过已完成交付、继续未完成环境。服务器部署、查看进度、停止续跑和镜像源配置见
[`ToolGen 批量运行说明`](BATCH_RUN_ZH.md)。

默认 Agent 是 `gpt-5.6-sol`，使用运行服务器的 Codex 登录配置。盘点、规划、编写和修复
均允许通过联网命令查阅官方资料、下载软件并探测真实接口；工具操作使用 DataGen 的真实数据。

专业环境的软件计划会解析为共享软件 Profile。Python 版本与依赖规格相同的环境复用同一
Profile；依赖组合不同的环境保持隔离，同时共享 uv 和 npm 下载缓存。Agent 会把软件计划写入
`tool_generation/software_plan.json`，程序在对应 Profile 中安装依赖。安装失败时，错误日志和
研究资料会返回给同一个 Agent，由它修正包名、版本或 Python 要求后继续安装；默认最多修复三次，
可通过 `--software-repair-attempts` 调整。每次进度和最终原因写入 `software_status.json`。

更换 Python 版本时使用对应版本的独立 Profile。工具验证启动绑定 Profile 中的 Python，业务状态
仍在任务副本内操作；专业算法必须调用真实接口或处理真实项目文件。软件安装成功只说明运行条件
满足，工具仍需通过实际调用验证。

依赖系统程序或共享库的环境可以在 `software_plan.json` 中增加 `container`：明确基础镜像、
可安装的 apt 包名和必要环境变量。镜像按完整软件计划计算稳定名称，相同计划只构建一次：

```bash
python3 -m env_gen.tool_gen.docker_runtime <环境包目录>
```

构建完成后会写入 `tool_generation/container_runtime.json`。正式交付把运行方式保存到
`runtime/runtime.json`：普通环境继续使用宿主机 Python 或共享 Python Profile，选择 Docker 的
环境由 MCP 启动层执行对应镜像。环境状态和工具定义仍是独立交付内容，不会被放进镜像。

软件环境也可以单独准备或恢复：

```bash
python3 -m env_gen.tool_gen.software prepare <环境包>
python3 -m env_gen.tool_gen.software exec <环境包> -- <下游脚本.py> <参数>
```

服务器默认将正式结果发布到：

```text
/data/agentworld-toolgen-results/
├── environments/<package_id>/
│   ├── binding.json
│   ├── environment/
│   ├── tools/
│   ├── software/profile.json
│   └── runtime/runtime.json
├── software_profiles/profiles/<profile_id>/
└── contracts/
```

每个 `environments/<package_id>/` 都是一个独立交付单元。`binding.json` 保存这个环境内部的
工具、环境状态和软件映射；共享软件本体仍放在 `software_profiles/profiles/`，多个环境可以复用。
下游从环境目录里的 `binding.json` 开始加载，不需要自己拼接几个顶层目录。
下游加载、任务级状态隔离和工具调用方式见
[`ToolGen 下游交付契约 v1.0`](../../schemas/ToolGen下游交付契约-v1.0.md)。

## 环境资源与运行边界

`workspace` 是 Runtime 内部保存任务状态副本的物理目录，本地运行、沙箱和容器中的位置可以
不同，不进入公开工具参数。`Filesystem Scope` 是 `environment.json` 声明的逻辑资源分区，
说明一组文件的用途和读写权限。Agent 通过 `aw://<scope_id>/<relative_path>` 引用具体资源，
不需要知道服务器目录或容器挂载点。

MCP 执行器统一提供三个资源工具：

- `get_environment_overview`：查看 Record Set、Filesystem Scope 和文件数量；
- `list_environment_resources`：按 Scope 和名称查找真实资源；
- `inspect_environment_resource`：查看资源属性和文本预览。

例如 `aw://design_files/main.kicad_sch` 在本地和容器中可以对应不同的物理目录。Runtime 会在
业务工具执行前核对 Scope 和资源类型，再把该引用转换成 `main.kicad_sch`；工具代码始终通过
`context.scope_root("design_files")` 找到当前任务副本。新生成工具的文件输入和输出字段都使用
`x-resource-scope` 标明它属于哪个 Scope，使用 `x-resource-kind` 标明它应当是文件还是目录。
工具代码内部仍接收、返回 Scope 内相对路径，Runtime 对外统一转换成 `aw://` 引用，因此一个
工具返回的文件可以直接作为下一个工具的参数。已有工具仍可继续接收原来的相对路径。

Kimi Code 的工作区只决定它自己的文件工具从哪里读取相对路径；MCP 配置中的 `cwd` 只决定
MCP 子进程从哪里启动。ToolGen 在生成 Kimi 配置时会明确设置该 `cwd`，但环境资源始终由
`binding.json` 和 Filesystem Scope 解析，不把 Kimi 工作区当作环境资源根目录。

代码职责保持独立：`resources.py` 负责资源目录与引用解析，`runtime.py` 负责工具执行，
`mcp_server.py` 提供与客户端无关的 MCP 能力，`kimi_mcp.py` 只负责 Kimi Code 的启动适配，
软件 Profile 与 Docker 的进程环境由 `runtime_launch.py` 组织，镜像配方和构建由
`docker_runtime.py` 负责；这些运行后端不进入 ToolGen 的能力盘点、工具规划和工具代码生成逻辑。

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
