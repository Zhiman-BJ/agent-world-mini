"""Tool Graph 流水线的统一数据契约。

AppendOnlyBundle 是一次流水线运行中持续扩充的信息与产物集合，只由 run_io 读取和扩充；
阶段函数只接收本文件定义的 Input，并只返回对应 Output。
除 `_step` 和逐阶段增加内容的 `tasks` 外，Output 字段不得覆盖已有字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict

AppendOnlyBundle = dict[str, Any]
ToolCall = dict[str, Any]  # {"tool": str, "arguments": dict, "observation": dict}
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PipelineStep(str, Enum):
    """固定流水线步骤；值同时用于 Bundle 元数据和检查点文件名。"""

    ENVIRONMENT_LOAD = "step_0_environment_load"
    GRAPH_BUILD = "step_1_graph_build"
    CHAIN_SAMPLE = "step_2_chain_sample"
    CHAIN_EXECUTE = "step_3_chain_execute"
    TASK_COMPOSE = "step_4_task_compose"
    TASK_VALIDATE = "step_5_task_validate"


@dataclass
class Config:
    """一次运行的完整配置（从 taskgen.yaml 加载后的内存形态）。

    属性：
        environment_dir: 环境包目录（只读）
        schema_dir: JSON Schema 目录
        output_root: 多次运行的父目录，如 runs/taskgen/
        llm: LLM 参数（后端、模型、超时等）
        graph: 建图参数
        planning: 规划参数（链长度约束等）
        execution: 执行参数（候选上限等）
        cost: 成本预算参数
    """

    environment_dir: Path = PROJECT_ROOT / "artifacts/mcp_test3/bugagent"
    schema_dir: Path = PROJECT_ROOT / "schemas"
    output_root: Path = PROJECT_ROOT / "runs/taskgen"
    llm: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)
    planning: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """一次运行的汇总结果，由 pipeline 返回。

    属性：
        run_dir: 本次运行目录
        task_count: 产出的任务数量
        rejected_count: 被校验拒绝的数量
        cost_report: 成本汇总
    """

    run_dir: Path
    task_count: int
    rejected_count: int
    cost_report: dict[str, Any]


class EnvironmentLoadInput(TypedDict):
    """Step 0 输入：完整运行配置。"""

    config: Config


class EnvironmentLoadOutput(TypedDict):
    """Step 0 新增完整环境；不复制或输出 workspace 状态。"""

    environment: dict[str, Any]


class BuildGraphInput(TypedDict):
    """Step 1 输入。

    config:
        完整运行配置。
    environment:
        Step 0 读取的完整 environment.json。建图参考环境描述、resources、
        rules 和工具的公开名称、描述及输入输出 Schema，不依赖 tools[].internal。
    """

    config: Config
    environment: dict[str, Any]


class BuildGraphOutput(TypedDict):
    """Step 1 新增工具直接依赖图。

    tool_graph:
        直接前置边列表。每条边包含：
        {
            "from_tool": str,
            "to_tool": str,
            "weight": 1 | 2 | 3,
            "reason": str,
        }

        from_tool → to_tool 仅表示调用 to_tool 前应调用 from_tool。
        只保存直接关系，不保存传递关系；工具节点由 environment.tools 得到，不重复保存。
        weight 表示依赖强度：3=强依赖，2=有条件的弱依赖，1=辅助性依赖；
        它不是 LLM 置信度。
        reason 是判定该边的必填依据。

        LLM 必须对每个候选都明确表态：有依赖给 1/2/3，无依赖给 weight=0。
        weight=0 是有效输出但不成为边（图只保存真实存在的边），它的作用是
        审查完整性门禁 —— Step 1 要求每个目标覆盖全部候选，漏审即报错。
    """

    tool_graph: list[dict[str, Any]]


class SampleChainsInput(TypedDict):
    """Step 2 输入。

    config:
        完整运行配置。planning 保存 sample_count、review_count、keep_top_count、
        链长、单工具访问次数、随机种子、weight 对应的 edge_sampling_probabilities
        和 diversity_lambda；llm 保存两轮 LLM 处理参数。
    environment:
        Step 0 读取的完整 environment.json。随机游走使用工具名；review 和逻辑性评分
        参考环境描述、resources、rules 和工具公开定义，不依赖 tools[].internal。
    tool_graph:
        Step 1 产生的直接前置边列表，结构见 BuildGraphOutput.tool_graph。Step 2 暂不
        消费 prerequisites；weight=3 的入边只用于确定起点，采样概率来自 config。
    """

    config: Config
    environment: dict[str, Any]
    tool_graph: list[dict[str, Any]]


class SampleChainsOutput(TypedDict):
    """Step 2 创建经过采样、多样性筛选、LLM review 和逻辑性评分的任务候选。

    tasks:
        最终去重后的任务候选列表，默认先处理最多 20 条，再选出最多 10 条交给 Step 3。
        每项包含：
        {
            "task_id": str,
            "chain": list[str],
            "score": int,
            "llm_review": {
                "original_chain": list[str],
                "reason": str,
                "error": str | None,
            },
            "logic_score": int,
            "logic_reason": str,
        }
        task_id 按最终顺序使用 task1、task2……；chain 是 LLM review 后的完整链，
        工具名必须存在于 environment.tools。review 可以在有充分公开契约依据时加入
        graph 中没有的相邻边，因此本阶段不以 graph 边存在性作为 review 结果的硬校验。
        score 是 review 前原始链的边权总和；logic_score 是 0–5 的任务适配性评分。

    sampling_report:
        记录尝试次数、唯一原始链数、观测最长链长度、短链回退、review 数量、review
        修改/失败数量、逻辑评分分布和最终数量，用于评估采样与筛选配置。
    """

    tasks: list[dict[str, Any]]
    sampling_report: dict[str, Any]


class ExecuteChainsInput(TypedDict):
    """Step 3 输入。

    config:
        完整运行配置。execution 使用 max_concurrency（默认 4）、
        retry_count（默认 3）、tool_timeout_seconds（默认 300）、
        tool_result_max_bytes（默认 65536）、tool_max_write_bytes 和
        tool_max_memory_bytes。
    run_dir:
        本次运行的独立目录。所有任务 workspace 都创建在其 ``tasks/`` 子目录中；
        state 字段保存相对该目录的路径。
    environment:
        完整 environment.json。LLM 只可看到环境元数据、resources、rules
        和工具公开字段；只有隔离执行器可读取 tools[].internal.code。
    tasks:
        Step 2 创建的任务候选，每项至少包含 task_id 和完整 chain。

    Step 3 从 ``config.environment_dir / "workspace"`` 读取源 workspace，
    不接收 Step 0 产出的全局 initial_state。LLM 先根据公开环境、全部公开工具定义
    和完整 chain 形成一次内部任务意图；逐工具填参时接收该意图、完整 chain、
    当前工具 inputSchema、本次已完成调用结果和上次失败原因。内部任务意图不写入
    Bundle；真实结果优先于意图。公开资源和真实 result 可以作为事实，未观察到的
    既有状态不得编造；标题、说明、评论、署名等任务创作值可以合理生成。
    LLM 不接收 internal.code 或整个 workspace 内容。
    """

    config: Config
    run_dir: Path
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]


class ExecuteChainsOutput(TypedDict):
    """Step 3 扩充任务候选的执行信息。

    tasks:
        保留每项已有内容并新增：
        {
            "execution": {
                "success": bool,
                "tool_calls": [
                    {
                        "tool": str,
                        "arguments": dict,
                        "result": dict,
                    }
                ],
                "initial_state": str | None,
                "final_state": str | None,
                "error": str | None,
                "attempts": [
                    {
                        "attempt": int,
                        "success": bool,
                        "tool_calls": list[dict],
                        "failed_tool": str | None,
                        "failed_arguments": dict | None,
                        "failure_kind": str | None,
                        "failed_result": dict | None,
                        "error": str | None,
                    }
                ],
            }
        }
        成功与失败的执行记录都保留。参数生成错误只在当前工具位置重试；工具执行失败
        后最多整链重试 3 次，每次从 initial 重新复制 final。候选可并发，但输出顺序不变。
        每个工具在独立子进程中执行并受 300 秒默认硬超时保护。
        initial_state 和 final_state 都是相对本次 run_dir 的 workspace
        目录路径；两个 workspace 与环境源 workspace 目录结构同构，内容分别
        表示执行前和执行后状态。它们不是内联资源数组或文件内容快照。
        失败项删除任务目录，两个字段为 None，只保留执行记录。
        failure_kind 取 llm、input_schema、timeout、exception、business 或
        output_schema；failed_result 保存已返回但判定失败的工具结果。
        启动并发前必须统一拒绝重复 task_id 和已存在的任务目录；工具超时时必须
        终止其整个进程组，不能遗留工具创建的子孙进程。
    """

    tasks: list[dict[str, Any]]


class ComposeTasksInput(TypedDict):
    """Step 4 输入。

    config:
        完整运行配置，包含任务转写使用的 LLM 参数。
    environment:
        完整环境，用于理解环境说明、资源、规则和工具含义。
    tasks:
        Step 3 扩充后的任务候选，包含 chain、execution 及任务级 workspace 路径；
        Step 4 不把 workspace、文件内容或状态差异放入 LLM 上下文，但不禁止本地代码
        访问任务级 workspace。
    """

    config: Config
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]


class ComposeTasksOutput(TypedDict):
    """Step 4 直接扩充流水线候选。

    tasks:
        保留每项已有内容，并为每项新增固定字段：
        {
            "task_text": str | None,
            "reference_answer": str | None,
            "resource_constraints": {
                "should_modify": list[str],
                "can_modify": list[str],
                "must_not_modify": list[str],
            } | None,
            "compose_error": str | None,
        }
        task_text 只描述自然、明确、以业务结果为中心的目标，不把参考链拆成操作步骤，
        不泄漏答案、执行结果、内部 ID 或指定工具调用顺序；允许多个相关交付要求，
        但无法形成自然目标时应记录 compose_error。

        成功转写时前三项有值且 compose_error=None。Step 4 在生成 task_text 后还有一次
        不落盘的反思调用；执行失败或转写失败时前三项为
        None，compose_error 保存原因。三个列表只使用 environment.resources 中的
        resource_id；模型可漏列，漏列项不补全，由使用方按“未列出即禁止修改”的
        默认规则处理。列表不得交叉，不得引用未知 resource_id，writable=false 的
        资源不得进入 should_modify 或 can_modify。约束只到 resource 粒度。

        reference_answer 和 compose_error 是流水线候选的中间字段；Step 5 组装正式
        task 时把 reference_answer 转为 reference.answer，compose_error 只用于验证，
        两者都不作为独立字段进入正式任务。resource_constraints 不同：它会原样写入
        正式 task，因为下游评分器需要据此判断资源改动边界，而这一信息无法从
        task_text 的自然语言中可靠还原。
    """

    tasks: list[dict[str, Any]]


class ValidateTasksInput(TypedDict):
    """Step 5 输入。

    config:
        完整运行配置，包含 Schema 路径和验证参数。
    run_dir:
        本次运行的独立目录，用于安全解析任务中的相对 workspace 路径。
    environment:
        完整环境，用于填写并核对 environment_id、resources 和公开工具定义。
    tasks:
        Step 4 扩充后的全部流水线候选，直接包含 task_text、reference_answer、
        resource_constraints 和 compose_error；任务级 initial/final workspace 路径
        也随候选保存，不需要额外中间数组或按 task_id 关联。
    """

    config: Config
    run_dir: Path
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]


class ValidateTasksOutput(TypedDict):
    """Step 5 组装并用 LLM 做语义验收。

    tasks:
        保留每个候选已有的 chain、execution 和可用的 task_text，并新增：
        {
            "task": {
                "schema_version": str,
                "task_id": str,
                "environment_id": str,
                "task_text": str,
                "difficulty": {"tool_calls": int},
                "initial_state": str | None,
                "available_tools": list[dict],
                "resource_constraints": {
                    "should_modify": list[str],
                    "can_modify": list[str],
                    "must_not_modify": list[str],
                } | None,
                "reference": {
                    "tool_calls": [
                        {"tool": str, "arguments": dict}
                    ],
                    "answer": str | None,
                    "final_state": str | None,
                },
            },
            "validation": {
                "passed": bool,
                "chain_matches_task": bool,
                "task_has_required_information": bool,
                "errors": list[str],
            },
        }

        每个候选都保留同样的 task 键形状和 validation。Step 5 只做字段整理、前序
        状态检查、Schema 检查，以及一次独立 LLM 语义审查：任务文本是否与真实成功
        调用链匹配、是否包含完成任务所需的不可自行发现信息。失败项保留候选，
        不猜测、不修补、不重放工具链、不比较 workspace 字节、不去重。

        最终导出由 run_io.finish_run 机械完成：通过项只导出内部 task 字典，
        失败项导出完整候选记录；两边都保持本列表中的相对顺序。
    """

    tasks: list[dict[str, Any]]
