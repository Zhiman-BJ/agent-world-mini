# Agent-World Mini

用真实公开数据构建可执行的工具环境，生成可重放的任务，再收集和验证模型的工具调用轨迹。
`Zhiman-BJ/agent-world-mini` 的 `main` 是正式代码来源。批次配置、数据、运行日志和软件镜像按各自运行目录保存。

## 数据流

```text
Seed → DataGen → ToolGen 交付包 → TaskGen → 模型执行与 Verifier → 训练轨迹
                    │              │              │
                    └────────────── Harness ───────┘
```

| 模块 | 输入和产物 | 正式代码 |
| --- | --- | --- |
| Seed | 场景、业务范围和参考能力 | `seed_gen/` |
| DataGen | 真实数据 → 环境声明、记录库、文件区域、来源和校验报告 | `env_gen/data_gen/` |
| ToolGen | 环境 → 工具计划、实现、真实执行验证、软件绑定和交付包 | `env_gen/tool_gen/` |
| Harness | 隔离执行、资源访问、任务状态、MCP 协议 | `harness/`、`utils/record_store.py` |
| Program TaskGen | 交付包 → 场景调研、业务任务、参考程序和重放结果 | `task_gen/program/` |
| Tool-graph TaskGen | 交付包 → 工具关系图、调用链、执行轨迹和任务 | `task_gen/tool_graph/` |
| 评测与蒸馏 | 模型执行轨迹、最终状态、Verifier 判定和原始请求 | `task_gen/task_eval*.py`、`distill/` |
| 训练数据构建 | 已验证轨迹 → 标准 JSONL、工具定义、上下文窗口和审计信息 | `distill/dataset/` |

Program 和 Tool-graph 是两种任务生成策略，各自保留正式入口，使用同一份工具交付与执行契约。
工具包已交付表示包内工具通过验收；计划中的 `rejected/skipped` 能力仍见报告，不计为已实现。

## 安装与运行

需要 Python 3.10+。隔离执行使用 Linux bubblewrap 或交付绑定的 Docker 镜像；生成与评测需各自的模型配置。

```bash
python -m pip install -e .
python -m env_gen.data_gen --help
python -m env_gen.tool_gen --help
python -m task_gen.program --help
python -m task_gen.tool_graph --help
python -m distill --help
```

交付包以 `delivery/environments/<environment_id>/binding.json` 为入口，绑定环境、正式工具、验证回执与软件或镜像。
下游读取 binding，不手工拼接不同批次的工具和软件目录。业务文件参数依照工具 Schema 使用 Scope 相对路径；
MCP 资源发现返回的 `aw://` 引用由资源解析器处理。

Program 的两种调用方式共用三个业务阶段：`python -m task_gen.program` 管理配置、运行状态与接续；
`python -m task_gen.program.run_pipeline` 提供显式阶段选择和已有阶段产物复用。
实际生成及重放逻辑位于 `step_0_environment_load.py`、`step_1_task_research.py`、`step_2_solution_generate.py`。

## 训练数据

轨迹采集和训练数据构建分开执行。标准导出保留真实工具调用及结果，Verifier 的判定用于选择训练样本。
`distill/dataset` 的输入和输出均由命令行明确指定：

```bash
python -m distill.dataset.training --help
python -m distill.dataset.paths --help
python -m distill.dataset.merge --help
python -m distill.dataset.windows --help
python -m distill.dataset.append --help
```

输出保存训练记录及对应审计信息；已有输出目录受保护。详见 [数据构建说明](distill/dataset/README.md)。

## 检查与维护

```bash
python -m pytest -q
```

涉及 Docker 的集成测试可通过 `TOOLGEN_DOCKER_TEST_IMAGE` 指定已有测试镜像。
需要外部场景 CSV 或真实模型凭据的测试，按测试自身的输入要求提供材料；测试跳过与通过分别报告。

模块说明：[DataGen](env_gen/data_gen/README.md)、[ToolGen](env_gen/tool_gen/README.md)、
[Program](task_gen/program/README.md)、[Distill](distill/README.md)、[系统架构](docs/SYSTEM_ARCHITECTURE_ZH.md)。
