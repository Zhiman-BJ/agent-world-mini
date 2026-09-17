# ToolGen 上游交付要求

适用于由 binding 连接环境、工具、状态与软件 Profile 的交付包。

## 目录与绑定

- 交付根目录必须包含 `environments/<package>/binding.json` 与 `software_profiles/profiles/<profile>/`。
- binding 中的环境、工具、验收回执、状态和软件路径必须是根目录内的相对路径，且与实际文件一致。
- 消费方应从 binding 解析布局，不应把拆分后的目录重新拼成旧布局。

## Python 与依赖

- `software/profile.json:python_path` 必须指向虚拟环境启动器，例如 `python-3.11/bin/python`；发布时不得对该启动器调用 `resolve()` 后记录基础解释器。
- `pyvenv.cfg` 的 `home`、`executable` 或 `base-executable` 若指向交付内基础运行时，发布器必须改写为最终交付位置并验证目标存在。绝对路径使 Profile 与目的目录绑定；移动交付后必须重新发布或重新准备。
- 若基础运行时不在交付内，必须声明其精确路径、Python 版本、系统包/动态库要求和重建方法。此类目录不能宣称可独立搬迁。
- 每个 Profile 必须安装与自身 Python ABI 兼容的完整依赖。不得用另一 Python 版本的 `site-packages` 兜底。
- 验收必须从声明的启动器以隔离模式实际导入 `jsonschema` 和业务依赖；缺失项应明确报告，不得在原始交付目录中临时安装后掩盖问题。

## 工具、资源与回执

- `tools.json` 中每个正式工具必须在 `tool_validation.json` 中有 `passed` 回执。
- `usageConditions.targetResources` 必须引用已声明且实际存在的 Record Set 或 Filesystem Scope。
- 工具代码、Schema、验证回执、资源文件及软件 requirements 必须随交付保存；候选工具的 rejected/skipped 回执不得被误报为正式工具缺失。

## 初态完整性

- 环境初态必须可复制、不可通过交付内符号链接逃逸，Record Set 数据库应通过 SQLite `PRAGMA quick_check`。
- 发布后应比较源与交付的工具定义、代码和初态内容；准备副本不得与源目录硬链接可变文件。
- `generic_fem_pde` 一类无 Record Set 的环境可以合法地没有 SQLite 数据库，验收应按 binding 声明执行。

## 实际验收

- Schema、文件存在性和 `python --version` 只算预检。每个环境还必须执行至少一个依赖业务库的真实工具调用。
- 有写操作的环境必须在状态副本中验证提交与失败回滚，且证明源初态未变化。
- 交付方应保存结构化调用结果，区分依赖/部署失败、工具实现失败和任务求解失败。
- 跨机器可用性必须在没有生成机目录的主机上另行验证；仅在生成机成功不能证明可搬迁。
