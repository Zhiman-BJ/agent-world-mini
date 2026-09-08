"""OmniaBench-aligned Program-form task generation pipeline."""

from .run_pipeline import run_selected_steps
from .steps import (
    run_step1,
    run_step2,
    run_step3,
    run_step4,
    run_step5,
    run_step6,
    run_step7,
    run_step8,
    run_step9,
    run_step10,
    run_step11,
    run_step12,
)
from .utils.contracts import ProgramGenerationPolicy, TaskFields
from .utils.environment import CompleteEnvironmentPackage
from .utils.reference_program import ProgramExecutionResult, execute_reference_program
from .utils.tool_runtime import CompleteEnvironmentRuntime

__all__ = [
    "CompleteEnvironmentPackage",
    "CompleteEnvironmentRuntime",
    "ProgramExecutionResult",
    "ProgramGenerationPolicy",
    "TaskFields",
    "execute_reference_program",
    "run_selected_steps",
    *[f"run_step{number}" for number in range(1, 13)],
]
