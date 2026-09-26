from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TaskFields:
    """Program-form 生成阶段共享的内部任务字段。"""

    ENV_ID = "env_id"
    ENV_CLASS_NAME = "env_class_name"
    ENVIRONMENT_PACKAGE = "environment_package"
    TASK_ID = "task_id"
    ARCHETYPE_ID = "archetype_id"
    TASK_INTERNAL = "task_internal"
    WORKSPACE_BRIEF = "workspace_brief"
    TASK_SUMMARY = "task_summary"
    TASK_PUBLIC = "task_public"
    OUTPUT_SCHEMA = "output_schema"
    SOLUTION_CODE = "solution_code"
    SOLUTION_CODE_ORIGINAL = "solution_code_original"
    SOLUTION_CODE_FIXED = "solution_code_fixed"
    SOLUTION_TRACE = "solution_trace"
    GROUND_TRUTH = "ground_truth"


@dataclass(frozen=True)
class ProgramGenerationPolicy:
    task_count: int = 1
    # 保留该字段只是为了兼容旧命令行；Step 2 现在每轮固定生成一条任务。
    candidate_multiplier: int = 1
    task_generation_attempts: int = 3
    clean_replays: int = 2
    require_state_change: bool = False
    max_repair_rounds: int = 10
    execution_timeout_seconds: float = 15.0

    def validate(self) -> None:
        if self.task_count < 1:
            raise ValueError("task_count 必须至少为 1")
        if self.candidate_multiplier < 1:
            raise ValueError("candidate_multiplier 必须至少为 1")
        if self.task_generation_attempts < 1:
            raise ValueError("task_generation_attempts 必须至少为 1")
        if self.clean_replays < 2:
            raise ValueError("clean_replays 必须至少为 2")
        if self.max_repair_rounds < 0:
            raise ValueError("max_repair_rounds 不能小于 0")
        if self.execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds 必须大于 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "candidate_count": 1,
            "single_task_generation": True,
            "task_generation_attempts": self.task_generation_attempts,
            "clean_replays": self.clean_replays,
            "require_state_change": self.require_state_change,
            "max_repair_rounds": self.max_repair_rounds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
        }
