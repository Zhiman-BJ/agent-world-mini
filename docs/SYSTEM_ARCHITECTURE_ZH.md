# 代码与数据流架构

## 正式维护边界

正式源码由 `Zhiman-BJ/agent-world-mini/main` 维护。服务器上的工作目录是具体版本的部署；
批次的计划、草稿、数据、修复凭据和日志属于运行产物。新修复先进入正式源码并测试，再按版本部署。

## 阶段与契约

1. `seed_gen/` 提供场景及参考能力。`env_gen/data_gen/` 获取真实数据，构建环境、记录集合和文件区域，冻结来源并校验。
2. `env_gen/tool_gen/compiler.py` 盘点能力、制定计划、生成工具、执行验证与修复；`test_contract.py` 校验和冻结验收用例。
   `software.py`、`docker_runtime.py` 准备执行依赖，`delivery.py` 发布通过工具及依赖绑定。
3. `binding.json` 把 `environment.json`、`tools.json`、校验回执、软件 Profile 或 Docker runtime 组合成正式交付。
   `delivery_contract.py` 是加载契约，Harness 和任务生成调用该契约。
4. Program 和 Tool-graph TaskGen 使用不同策略生成任务。它们共享交付加载、运行状态、工具执行和资源协议。
5. `task_eval*.py`、`distill/runner.py` 运行模型，记录实际工具调用；Verifier 检查任务结果。
   `distill/export.py`、`distill/clean.py` 输出标准轨迹；`distill/dataset/` 选择通过样本并构建训练窗口。

## 执行代码归属

| 能力 | 实现 |
| --- | --- |
| 工具调用、Schema 验证、失败回滚 | `harness/execution.py` |
| bubblewrap、直接受限进程、Docker 执行后端 | `harness/sandbox.py` |
| 任务工作区与私有记录库布局 | `harness/layout.py` |
| Context 与逻辑状态检查 | `harness/state.py`、`harness/state_snapshot.py` |
| Record Set CRUD 与字段验证 | `utils/record_store.py` |
| 资源路径解析与边界 | `harness/resources.py` |
| MCP 工具协议、资源分页与读取 | `harness/mcp_protocol.py`、`harness/mcp_resources.py` |
| Kimi 原生文件访问限制 | `harness/kimi_file_policy.py` |
| ToolGen 交付和生成期会话管理 | `env_gen/tool_gen/runtime.py`、`runtime_launch.py` |

`task_gen/tool_runtime.py` 和 `task_gen/tool_graph/state_runtime.py` 是已有调用端的适配入口，
执行和 Context 实现由 Harness 提供。Program 的软件 Profile 子进程保留专业 Python 环境的执行适配。
软件、状态与业务数据的可读写范围由后端控制，实际工具声明还要通过资源、状态和结果检查。

ToolGen 通用 MCP 同时提供工具与资源方法；任务 MCP 仅提供环境工具，文件读取交给模型的受限原生工具。
任务工作区只包含公开 Scope 文件，记录库保存在私有目录。文件参数采用工作区相对路径（多 Scope 包含挂载目录）
或工作区内绝对路径；容器运行将模型看到的绝对路径映射到实际执行工作区。Program 参考执行则使用完整私有状态副本。

## 容器与专业软件部署

普通主机上的通用工具沙箱使用 bubblewrap 的独立 PID 和文件系统隔离。对于已有独立
PID namespace 的 Docker 容器或非 hostPID Kubernetes Pod，设置
`AGENT_WORLD_DOCKER_RUNTIME=1` 或 `AGENT_WORLD_KUBERNETES_RUNTIME=1`，使通用工具
沙箱复用该容器的只读 procfs，并保持进程视图一致。私有共享内存和临时工作目录用于
科学软件的缓存与 IPC；业务工作区的资源权限、Schema 检查和失败回滚仍由 Harness 执行。

Python profile 优先使用自己的原生库、证书和 PETSc/SLEPc 路径，并过滤继承自其他
Python 版本的扩展库目录。软件包、模型权重与镜像由交付依赖提供，部署时需从实际
执行入口验证。Docker daemon 与 executor 的代码挂载路径不同时，可用
`AGENT_WORLD_DOCKER_PROJECT_ROOT` 指定 daemon 可见的代码目录；两端必须使用相同源码版本。

## 任务生成入口

Program 的 `pipeline.py` 管理配置、运行目录、暂停和接续；`run_pipeline.py` 管理显式阶段选择与阶段复用。
两者调用同一组 `step_0`、`step_1`、`step_2` 实现。Tool-graph 的 `pipeline.py` 管理图构建、采样、执行、
任务反推和验证。二者的产物和配置分别留在各自模块，不互相替代。

## 产物判定

`tools.json` 只包含通过工具。`tool_validation.json` 保存通过、拒绝和跳过的明细；
交付包与能力全实现是两个状态。训练样本还需实际模型轨迹和任务 Verifier 判定，
工具通过验收本身不等于模型成功完成任务。

## 部署与存档

新任务使用固定 Git 提交的正式部署路径。活动批次和数据迁移继续使用其已固定源码与数据，
切换前检查进程、工作区和依赖。历史分支以 Git bundle 留档；历史批次源码随批次快照保存，
运行问题的维护修改回到正式 main，部署记录写明提交和实际测试。
