# ToolGen 下游使用说明

共享交付根目录为：

```text
/data/agentworld-toolgen-results
```

`environments/<package_id>/binding.json` 是一个环境的加载入口。它把工具、DataGen v2 环境基线和
专业软件 Profile 绑定在一起。完整字段和 Runtime 语义见
`contracts/ToolGen下游交付契约-v1.0.md`。

## 可用环境

```bash
find /data/agentworld-toolgen-results/environments -mindepth 2 -maxdepth 2 -name binding.json -print
```

## TaskGen Step 1

以 `pypi_atomate2_5` 为例：

```bash
DELIVERY_ROOT=/data/agentworld-toolgen-results
PACKAGE_ID=pypi_atomate2_5
BINDING="$DELIVERY_ROOT/environments/$PACKAGE_ID/binding.json"
ENVIRONMENT_PATH=$(jq -r .environment_path "$BINDING")
TOOLS_PATH=$(jq -r .tools_path "$BINDING")

python -m task_gen.program.run_pipeline \
  --step 1 \
  --environment-package "$DELIVERY_ROOT/$ENVIRONMENT_PATH" \
  --tools-path "$DELIVERY_ROOT/$TOOLS_PATH" \
  --output-dir "/data/taskgen-runs/$PACKAGE_ID"
```

Step 1 会生成 `step1_environment.json` 和 `baseline_environment/`。每条任务的工具执行会话都应从
`baseline_environment/state/` 创建独立副本，同一条任务内持续使用该副本。

## 执行一个工具

专业工具使用 binding 指定的 Profile Python：

```bash
DELIVERY_ROOT=/data/agentworld-toolgen-results
PACKAGE_ID=pypi_atomate2_5
BINDING="$DELIVERY_ROOT/environments/$PACKAGE_ID/binding.json"
PROFILE_PATH=$(jq -r .software_profile_path "$BINDING")
SOFTWARE_MAPPING=$(jq -r .software_mapping_path "$BINDING")
PYTHON_PATH=$(jq -r .python_path "$DELIVERY_ROOT/$SOFTWARE_MAPPING")

PYTHONPATH=/path/to/agent-world-mini \
TOOLGEN_SOFTWARE_ROOT="$DELIVERY_ROOT/$PROFILE_PATH" \
  "$DELIVERY_ROOT/$PYTHON_PATH" \
  "$DELIVERY_ROOT/contracts/call_toolgen_delivery.py" \
  --delivery-root "$DELIVERY_ROOT" \
  --package-id "$PACKAGE_ID" \
  --tool get_project_dossier \
  --arguments '{"project_id":"gan_mg_defect"}'
```

执行层只把 `name`、`description`、`usageConditions`、`inputSchema` 和 `outputSchema`
交给 Agent；`internal.code` 由 Runtime 执行。Runtime 为工具提供环境声明、Record Set 访问、
Filesystem Scope 访问和专业软件根目录。

## 软件与环境发布

软件 Profile 保存安装好的运行环境和所需系统依赖。下载缓存、失败安装目录和解包的系统文档
保留在构建区；如果运行文件链接到缓存里的内容，交付时会带上该内容。软件目录内部的链接
使用相对路径，已有共享解释器等外部依赖继续使用本服务器上的共享路径。跨服务器迁移时，
需要同时提供这些共享依赖。

交付先准备软件 Profile，再将工具、环境数据、软件映射及 `binding.json` 作为一个环境包发布。
更新失败时保留上一份交付。下游按 `binding.json` 查找环境，并使用软件映射中的 `python_path`
选择解释器；该路径随软件环境而定。
