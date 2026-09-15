# ToolGen 下游交付契约 v1.0

## 1. 适用范围

本契约规定 ToolGen 如何把可执行工具、DataGen v2 环境状态和专业软件交给下游。
当前直接消费方是 GitHub `zhiman/main` 中的 `task_gen/program`，对接入口是
`steps/step1_prepare_environment.py`，工具执行位于 `utils/tool_runtime.py`。

一个 `package_id` 代表一套可交付的工具运行环境。下游始终从对应的 binding 加载，
不单独猜测工具、状态或软件目录的组合关系。

## 2. 交付目录

```text
<delivery_root>/
├── bindings/
│   └── <package_id>.json
├── tools/
│   └── <package_id>/
│       ├── tools.json
│       ├── tool_validation.json
│       ├── tool_grounding.json
│       └── action_plan.json
├── environments/
│   └── <package_id>/
│       ├── environment.json
│       ├── validation.json
│       ├── state/
│       │   ├── records.sqlite
│       │   └── filesystem_scopes/<scope_id>/...
│       ├── provenance/
│       ├── tool_runtime.json
│       └── tool_generation/software_environment.json
└── software_profiles/
    └── profiles/<profile_id>/
        ├── profile.json
        ├── requirements.txt
        ├── python/bin/python
        └── node/node_modules/             # 需要 Node 依赖时存在
```

`tools/`、`environments/` 和 `software_profiles/` 分别保存工具、基线状态和依赖。
`bindings/` 是它们之间的稳定对应关系。

## 3. Binding 字段

Binding 必须符合 `schemas/toolgen_delivery_binding.schema.json`。所有路径相对
`delivery_root` 解析。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 本契约版本，当前为 `1.0` |
| `package_id` | 交付单元标识，也是 binding 文件名 |
| `environment_id` | `environment.json` 内的业务环境标识 |
| `environment_path` | DataGen v2 环境包目录 |
| `environment_validation_path` | DataGen 环境验收回执，`valid` 必须为 `true` |
| `tools_path` | 正式工具集 |
| `tool_validation_path` | ToolGen 的逐工具实际执行结果 |
| `software_profile` | 专业依赖 Profile ID；无额外依赖时为 `null` |
| `software_profile_path` | Profile 目录；与 `software_profile` 同时有值或同时为 `null` |

示例：

```json
{
  "schema_version": "1.0",
  "package_id": "pypi_atomate2_5",
  "environment_id": "atomate2_semiconductor_defects",
  "tools_path": "tools/pypi_atomate2_5/tools.json",
  "tool_validation_path": "tools/pypi_atomate2_5/tool_validation.json",
  "environment_path": "environments/pypi_atomate2_5",
  "environment_validation_path": "environments/pypi_atomate2_5/validation.json",
  "software_profile": "py311-c3ac59c31e29",
  "software_profile_path": "software_profiles/profiles/py311-c3ac59c31e29"
}
```

## 4. 工具契约

`tools.json` 的根节点为：

```text
schema_version
environment_id
tools[]
```

每个工具包含：

| 字段 | 消费方式 |
| --- | --- |
| `name` | 工具调用名 |
| `description` | 工具完成的业务动作 |
| `usageConditions` | 操作对象、调用前提和可见副作用 |
| `inputSchema` | `arguments` 的 JSON Schema |
| `outputSchema` | 返回值的 JSON Schema |
| `internal.code` | `run(arguments, context)` 的内部实现 |

任务调研、建图和求解 Agent 可见前五项，其中 `usageConditions` 用于判断工具应在
什么对象和状态下调用。`internal.code` 只交给 Runtime，不进入 Agent 上下文。

调用参数必须通过 `inputSchema`。返回值使用统一包装：

```json
{"success": true, "data": {}}
```

或：

```json
{
  "success": false,
  "error": {
    "code": "business_error_code",
    "path": "arguments.field_name",
    "message": "Readable error message",
    "retryable": false
  }
}
```

`success=false` 表示一次有效的业务失败。`retryable=true` 表示同一参数在临时性故障恢复后可重试；
参数、对象状态或业务规则导致的失败使用 `retryable=false`。

## 5. Runtime 会话

下游以“一条任务一个 Runtime 会话”为状态边界：

1. 根据 binding 读取环境和工具。
2. 将 `environment_path/state/` 复制为该任务的独立状态。
3. 同一任务的全部工具调用在同一会话内按顺序执行。
4. 完成后保存调用轨迹、最终状态和状态变化。
5. 下一条任务从基线重新创建会话。

这样可以在一条长链中保留前序修改，同时让两条任务之间的数据互不影响。

ToolGen 已提供这一执行语义：

```python
from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime

package = ToolPackage.load(environment_root, tools=tool_document["tools"])
with ToolRuntime(package) as runtime:
    before = runtime.snapshot()
    result = runtime.call(tool_name, arguments)
    after = runtime.snapshot()
```

Runtime 会校验输入与输出、执行真实 `internal.code`、限制只读资源变更，并在调用异常或
业务失败产生副作用时恢复该次调用前的状态。

## 6. 专业软件加载

Binding 存在 `software_profile_path` 时，下游使用该 Profile 的 Python 启动工具执行工作进程：

```text
<delivery_root>/<software_profile_path>/python/bin/python
```

Node 模块位于：

```text
<delivery_root>/<software_profile_path>/node/node_modules
```

Profile 是只读依赖运行时，任务状态仍位于 Runtime 创建的独立副本中。
下游的主进程可以使用自身 Python，但实际工具必须在绑定的 Profile 工作进程中执行，
以便 `atomate2`、`pymatgen`、`doped`、`klayout`、`solc` 等依赖可正常导入和调用。

## 7. TaskGen 对接

GitHub `zhiman/main` 中的 `task_gen/program` 是当前下游模块。它的 Step 1 已支持分开的
DataGen 环境包和 ToolGen 工具文件。从 binding 取出两个路径后，执行：

```bash
python -m task_gen.program.run_pipeline \
  --step 1 \
  --environment-package <delivery_root>/<environment_path> \
  --tools-path <delivery_root>/<tools_path> \
  --output-dir <taskgen_output>
```

Step 1 会读取 `environment.json` 和 `validation.json`，复制基线 `state/`，加载
`tools.json`，并生成 `step1_environment.json` 与 `baseline_environment/`。

完整的 TaskGen 对接按以下对应关系实现：

| ToolGen 契约 | TaskGen 消费位置 |
| --- | --- |
| `environment_path` | Step 1 `environment_package` |
| `tools_path` | Step 1 `tools_path` |
| `provenance/scenario_research.json` | Step 2 的现实任务调研输入 |
| 公开工具字段 | Step 2 任务调研、Step 3 Solution 生成、Step 5 独立求解 |
| `internal.code` | Step 3 参考 Solution 执行和 Step 5 求解 MCP |
| `software_profile_path` | Step 3 和 Step 5 的工具执行工作进程 |
| Runtime 状态快照 | Ground Truth、状态 Verifier 和难度评测 |

TaskGen 冻结基线时保留 binding 或等价的软件 Profile 引用。它的公开工具投影包含
`name`、`description`、`usageConditions`、`inputSchema` 和 `outputSchema`。它的工具执行层实现
ToolGen Runtime 语义，并在绑定的 Profile Python 中运行。

完整执行层需要按以下接口对接：

1. 公开工具信息保留 `usageConditions`，供任务建图和工具选择使用。
2. 工具的 `run(arguments, context)` 可以访问 `context.environment`、`context.records`、
   `context.scope_root(scope_id)` 和 `context.software_root`。
3. Step 1 的冻结产物保留 binding 中的 `software_profile_path`；Step 3 和 Step 5
   在该 Profile Python 的工作进程中执行工具。

## 8. 参考加载代码

```python
import json
from pathlib import Path

from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime

delivery_root = Path("/data/agentworld-toolgen-results")
package_id = "pypi_atomate2_5"
binding = json.loads(
    (delivery_root / "bindings" / f"{package_id}.json").read_text(encoding="utf-8")
)
environment_root = delivery_root / binding["environment_path"]
tool_document = json.loads(
    (delivery_root / binding["tools_path"]).read_text(encoding="utf-8")
)

package = ToolPackage.load(environment_root, tools=tool_document["tools"])
with ToolRuntime(package) as runtime:
    result = runtime.call(
        "get_project_dossier",
        {"project_id": "gan_mg_defect"},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

这段代码由 binding 解析路径，因此不依赖某个用户的个人目录。运行它的 Python 由
`software_profile_path` 决定，`PYTHONPATH` 指向下游自己的 AgentWorld 代码仓库。

## 9. 接收条件

下游在开始任务生成前确认：

1. Binding 符合 `toolgen_delivery_binding.schema.json`，其路径均相对交付根目录可读。
2. `environment_validation_path` 的 `valid=true`。
3. `tool_validation_path` 中正式 `tools.json` 对应的工具均为 `passed`。
4. `software_profile_path` 有值时，Profile Python 可启动且锁定依赖可导入。
5. 用新 Runtime 会话执行一个代表工具后，基线环境保持不变。

满足以上条件后，该 `package_id` 可进入任务调研、Solution 执行、Verifier 构建和独立求解。
