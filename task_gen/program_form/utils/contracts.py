from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class TaskFields:
    """与 OmniaBench Program-form 管线对齐的任务记录字段。"""

    ENV_ID = "env_id"
    ENV_CLASS_NAME = "env_class_name"
    ENVIRONMENT_PACKAGE = "environment_package"
    INITIAL_STATE = "initial_state"
    TASK_ID = "task_id"
    TASK_INTERNAL = "task_internal"
    TASK_INTERNAL_FINETUNED = "task_internal_finetuned"
    TASK_PUBLIC = "task_public"
    OUTPUT_SCHEMA = "output_schema"
    SOLUTION_CODE = "solution_code"
    SOLUTION_CODE_ORIGINAL = "solution_code_original"
    SOLUTION_CODE_FIXED = "solution_code_fixed"
    SOLUTION_DEBUG_SUCCESS = "solution_debug_success"
    SOLUTION_DEBUG_HISTORY = "solution_debug_history"
    SOLUTION_EXECUTION_TRAJECTORY = "solution_execution_trajectory"
    SOLUTION_TRACE = "solution_trace"
    TASK_FINETUNE_SUCCESS = "task_finetune_success"
    TASK_FINETUNE_MODIFICATION = "task_finetune_modification"
    TASK_FINETUNE_NEEDED = "task_finetune_needed"
    GROUND_TRUTH = "ground_truth"
    POST_SOLUTION_STATE_SNAPSHOT = "post_solution_state_snapshot"
    VERIFIER_CODE = "verifier_code"
    VERIFIER_DEBUG_SUCCESS = "verifier_debug_success"
    VERIFIER_DEBUG_HISTORY = "verifier_debug_history"
    MULTI_EXEC_RESULTS = "multi_exec_results"
    CONSISTENCY_PASS_COUNT = "consistency_pass_count"
    CONSISTENCY_PASS_RATE = "consistency_pass_rate"
    CONSISTENCY_THRESHOLD = "consistency_threshold"
    CONSISTENCY_TOTAL_RUNS = "total_runs"
    CONSISTENCY_KEEP = "consistency_keep"
    FILTER_STATUS = "filter_status"
    FILTER_REASON = "filter_reason"
    REWRITE_SUCCESS = "rewrite_success"
    STEP9_CONSISTENT = "step9_consistent"
    STEP9_FILTER_REASON = "step9_filter_reason"
    RUBRIC_COUNT = "rubric_count"
    RUBRIC_TOTAL_SCORE = "rubric_total_score"
    GENERAL_RUBRIC_SCORE = "general_rubric_score"
    TASK_SPECIFIC_RUBRIC_SCORE = "task_specific_rubric_score"
    RUBRICS_TEXT = "rubrics_text"
    RUBRIC_EXPLANATION = "rubric_explanation"
    RUBRIC_ITEMS = "rubric_items"
    RUBRIC_GENERATION_META = "rubric_generation_meta"
    RUBRIC_SCORE_AUTOCORRECTED = "rubric_score_autocorrected"
    RUBRIC_ACTUAL_RUBRIC_COUNT = "rubric_actual_rubric_count"
    RUBRIC_ACTUAL_TOTAL_SCORE = "rubric_actual_total_score"
    RUBRIC_ACTUAL_GENERAL_SCORE = "rubric_actual_general_score"
    RUBRIC_ACTUAL_TASK_SPECIFIC_SCORE = "rubric_actual_task_specific_score"
    DIFFICULTY_EVAL_RESULTS_3 = "difficulty_eval_results_3"
    DIFFICULTY_PASS_COUNT_3 = "difficulty_pass_count_3"
    DIFFICULTY_EVAL_N = "difficulty_eval_n"
    EMPIRICAL_PASS_RATE_3 = "empirical_pass_rate_3"
    PASS_AT_K_ESTIMATE = "pass_at_k_estimate"
    PASS_AT_K = "pass_at_k"
    DIFFICULTY_BUCKET = "difficulty_bucket"
    DIFFICULTY_SUCCESS_MASK = "difficulty_success_mask"


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
    consistency_runs: int = 5
    consistency_threshold: int = 2
    difficulty_eval_runs: int = 5
    trace_state_judge_runs: int = 3
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
        if self.consistency_runs < 1:
            raise ValueError("consistency_runs 必须至少为 1")
        if not 1 <= self.consistency_threshold <= self.consistency_runs:
            raise ValueError("consistency_threshold 必须位于 1..consistency_runs")
        if self.difficulty_eval_runs < 1:
            raise ValueError("difficulty_eval_runs 必须至少为 1")
        if self.trace_state_judge_runs < 1:
            raise ValueError("trace_state_judge_runs 必须至少为 1")
        if self.trace_state_judge_runs % 2 == 0:
            raise ValueError("trace_state_judge_runs 必须是奇数")
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
            "consistency_runs": self.consistency_runs,
            "consistency_threshold": self.consistency_threshold,
            "difficulty_eval_runs": self.difficulty_eval_runs,
            "trace_state_judge_runs": self.trace_state_judge_runs,
            "total_rubric_score": self.total_rubric_score,
            "general_rubric_score": self.general_rubric_score,
        }


@dataclass
class ProgramTaskCandidate:
    task_internal: str
    output_schema: dict[str, Any]
    solution_code: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            task_internal=str(value["task_internal"]).strip(),
            output_schema=deepcopy(value["output_schema"]),
            solution_code=str(value["solution_code"]).strip(),
        )
