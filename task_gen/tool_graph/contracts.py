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
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        包含直接边和目标级历史前置约束：
        {
            "edges": [{
                "from_tool": str,
                "to_tool": str,
                "weight": 1 | 2 | 3,
                "reason": str,
            }],
            "prerequisites": [{
                "to_tool": str,
                "any_of": [{
                    "all_of": list[str],
                    "reason": str,
                }],
            }],
        }

        from_tool → to_tool 仅表示调用 to_tool 前应调用 from_tool。
        只保存直接关系，不保存传递关系；工具节点由 environment.tools 得到，不重复保存。
        weight 只表示直接下一跳的关系等级：3=强直接关系，2=明确工作流转移，
        1=有具体依据的弱关系；它不是硬前置、采样概率或 LLM 置信度。
        reason 是判定该边的必填依据。

        prerequisites 独立表示执行目标工具前必须已经满足的工具历史。any_of 中任意
        一个方案满足即可；一个方案的 all_of 中所有工具都必须已经执行。没有硬前置的
        目标不出现在 prerequisites 中。不能从 weight=3 推导 prerequisite。

        LLM 必须对每个候选都明确表态：有依赖给 1/2/3，无依赖给 weight=0。
        weight=0 是有效输出但不成为边（图只保存真实存在的边），它的作用是
        审查完整性门禁。每个候选都必须附非空 reason；Step 1 要求每个目标覆盖
        全部候选，漏审或空理由即报错。
    """

    tool_graph: dict[str, Any]


class SampleChainsInput(TypedDict):
    """Step 2 uses config, public tool contracts and graph edges. An isolated probe
    may execute tools on temporary initial-workspace copies. Codex review reads
    its own initial-state copy, but does not receive internal tool code or run
    business writes. Sampling probabilities are independent of edge weights."""

    config: Config
    environment: dict[str, Any]
    tool_graph: dict[str, Any]


class SampleChainsOutput(TypedDict):
    """Step 2 returns candidates, sampling_report and one initial_state_report.

    Objectives prioritize task quality, using original chains as inspiration and
    the initial report as a reference, without chain acceptance decisions.
    An objective may combine independent subtasks. Review adapts the chain to the
    frozen objective and may exceed sampling length/visit caps; it retains the
    planning length floor. Each task contains task_id, chain,
    objective, score, llm_review, logic_score and logic_reason. score sums known
    edges in the reviewed chain; graph-external adjacencies contribute zero.
    Codex review supplies logic_score (0-5) for the final plan's value and expected
    objective completion; its reason supplies logic_reason and task-state-chain
    guidance to execution, composition, reflection, answer and validation.
    There is no separate scoring inference. Invalid scores are review errors.
    sampling_report records objective generation, review decisions, failures and coverage.
    initial_state_report contains summary, observations and errors. It is limited
    evidence, not a complete state snapshot or proof of absence."""

    tasks: list[dict[str, Any]]
    sampling_report: dict[str, Any]
    initial_state_report: dict[str, Any]


class ExecuteChainsInput(TypedDict):
    """Step 3 receives frozen objectives, chains and optional initial observations.

    config.environment_dir/workspace is the source of isolated initial/final
    copies under run_dir/tasks/<task_id>. Parameter generation sees public
    contracts, bounded initial evidence, review guidance, completed calls and previous failures.
    Review guidance is a planning reference, not a new objective or proven facts;
    legacy candidates without it remain executable.
    Only the sandbox executor consumes internal.code."""

    config: Config
    run_dir: Path
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]
    initial_state_report: dict[str, Any] | None


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
        Step 3 扩充后的任务候选，包含 objective、chain、execution 及任务级 workspace 路径；
        Step 4 不把 workspace、文件内容或状态差异放入 LLM 上下文，但不禁止本地代码
        访问任务级 workspace。
    """

    config: Config
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]


class ComposeTasksOutput(TypedDict):
    """Step 4 adds task_text, reference_answer, compose_error.

    Successful execution is required. Reflection failure preserves the draft;
    runtime/format failures leave remaining fields empty and explain compose_error.
    All rounds receive llm_review.reason as review_guidance. Draft/answer do not
    reject semantically; incomplete evidence is reported for final validation.
    Step 5 maps reference_answer to task.reference.answer."""

    tasks: list[dict[str, Any]]


class ValidateTasksInput(TypedDict):
    """Step 5 receives all candidates, the public environment, optional initial
    observations, config and run_dir. It reviews execution and reference_answer
    against final task_text, using review guidance and all public tools."""

    config: Config
    run_dir: Path
    environment: dict[str, Any]
    tasks: list[dict[str, Any]]
    initial_state_report: dict[str, Any] | None


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
                "execution_matches_task": bool,
                "answer_matches_task": bool,
                "task_is_usable": bool,
                "errors": list[str],
            },
        }

        每个候选都保留同样的 task 键形状和 validation。Step 5 只做字段整理、前序
        状态检查、Schema 检查，以及一次独立 LLM 语义审查：执行是否满足任务适用要求、
        答案是否准确完整、任务是否自然且包含必要业务信息。失败项保留候选，
        不猜测、不修补、不重放工具链、不比较 workspace 字节、不去重。

        最终导出由 run_io.finish_run 机械完成：通过项只导出内部 task 字典，
        失败项导出完整候选记录；两边都保持本列表中的相对顺序。
    """

    tasks: list[dict[str, Any]]
