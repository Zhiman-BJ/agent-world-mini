from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TaskFields:
    """五步 Program-form 管线跨阶段共享的任务字段。"""

    ENV_ID = "env_id"
    ENV_CLASS_NAME = "env_class_name"
    ENVIRONMENT_PACKAGE = "environment_package"
    TASK_ID = "task_id"
    ARCHETYPE_ID = "archetype_id"
    TASK_INTERNAL = "task_internal"
    TASK_PUBLIC = "task_public"
    OUTPUT_SCHEMA = "output_schema"
    SOLUTION_CODE = "solution_code"
    SOLUTION_CODE_ORIGINAL = "solution_code_original"
    SOLUTION_CODE_FIXED = "solution_code_fixed"
    SOLUTION_TRACE = "solution_trace"
    GROUND_TRUTH = "ground_truth"
    VERIFIER_CODE = "verifier_code"
    STATE_VERIFIER_CODE = "state_verifier_code"
    SCORING_READY = "scoring_ready"
    SCORING_VALIDATION = "scoring_validation"
    RUBRIC_COUNT = "rubric_count"
    RUBRIC_TOTAL_SCORE = "rubric_total_score"
    GENERAL_RUBRIC_SCORE = "general_rubric_score"
    TASK_SPECIFIC_RUBRIC_SCORE = "task_specific_rubric_score"
    RUBRIC_EXPLANATION = "rubric_explanation"
    RUBRIC_ITEMS = "rubric_items"
    DIFFICULTY_EVALUATIONS = "difficulty_evaluations"
    INFRASTRUCTURE_FAILURES = "infrastructure_failures"
    VALID_ROLLOUTS = "valid_rollouts"
    PASS_COUNT = "pass_count"
    EMPIRICAL_PASS_RATE = "empirical_pass_rate"
    PASS_AT_K = "pass_at_k"
    DIFFICULTY_BUCKET = "difficulty_bucket"
    PUBLISH_STATUS = "publish_status"
    PUBLISH_REASON = "publish_reason"


@dataclass(frozen=True)
class ProgramGenerationPolicy:
    task_count: int = 1
    candidate_multiplier: int = 2
    task_generation_attempts: int = 3
    min_tool_calls: int = 4
    min_distinct_tools: int = 2
    clean_replays: int = 2
    require_state_change: bool = False
    max_repair_rounds: int = 10
    execution_timeout_seconds: float = 15.0
    difficulty_eval_runs: int = 5
    minimum_passing_runs: int = 1
    infrastructure_retries: int = 2
    total_rubric_score: int = 14
    general_rubric_score: int = 6

    def validate(self) -> None:
        if self.task_count < 1:
            raise ValueError("task_count 必须至少为 1")
        if self.candidate_multiplier < 1:
            raise ValueError("candidate_multiplier 必须至少为 1")
        if self.task_generation_attempts < 1:
            raise ValueError("task_generation_attempts 必须至少为 1")
        if self.min_tool_calls < 1:
            raise ValueError("min_tool_calls 必须至少为 1")
        if self.min_distinct_tools < 1:
            raise ValueError("min_distinct_tools 必须至少为 1")
        if self.clean_replays < 2:
            raise ValueError("clean_replays 必须至少为 2")
        if self.max_repair_rounds < 0:
            raise ValueError("max_repair_rounds 不能小于 0")
        if self.difficulty_eval_runs < 1:
            raise ValueError("difficulty_eval_runs 必须至少为 1")
        if not 1 <= self.minimum_passing_runs <= self.difficulty_eval_runs:
            raise ValueError("minimum_passing_runs 必须位于 1..difficulty_eval_runs")
        if self.infrastructure_retries < 0:
            raise ValueError("infrastructure_retries 不能小于 0")
        if self.total_rubric_score < 1:
            raise ValueError("total_rubric_score 必须大于 0")
        if not 1 <= self.general_rubric_score < self.total_rubric_score:
            raise ValueError("general_rubric_score 必须位于 1..total_rubric_score-1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "candidate_count": self.task_count * self.candidate_multiplier,
            "task_generation_attempts": self.task_generation_attempts,
            "min_tool_calls": self.min_tool_calls,
            "min_distinct_tools": self.min_distinct_tools,
            "clean_replays": self.clean_replays,
            "require_state_change": self.require_state_change,
            "max_repair_rounds": self.max_repair_rounds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "difficulty_eval_runs": self.difficulty_eval_runs,
            "minimum_passing_runs": self.minimum_passing_runs,
            "infrastructure_retries": self.infrastructure_retries,
            "total_rubric_score": self.total_rubric_score,
            "general_rubric_score": self.general_rubric_score,
        }
