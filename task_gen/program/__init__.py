"""Three-step Program-form task generation pipeline."""

from .utils.contracts import ProgramGenerationPolicy, TaskFields


def __getattr__(name):
    if name == "run":
        from .pipeline import run
        return run
    if name == "run_selected_steps":
        from .run_pipeline import run_selected_steps
        return run_selected_steps
    if name in {"run_step0", "ToolGenDelivery"}:
        from .step_0_environment_load import ToolGenDelivery, run_step0
        return locals()[name]
    if name in {"run_step1", "Step1Result", "validate_task_research"}:
        from .step_1_task_research import Step1Result, run_step1, validate_task_research
        return locals()[name]
    if name in {
        "run_step2",
        "Step2Result",
        "ProgramExecutionResult",
        "execute_solution_code",
        "validate_solution_code",
    }:
        from .step_2_solution_generate import (
            ProgramExecutionResult,
            Step2Result,
            execute_solution_code,
            run_step2,
            validate_solution_code,
        )
        return locals()[name]
    if name == "CompleteEnvironmentPackage":
        from .utils.environment import CompleteEnvironmentPackage
        return CompleteEnvironmentPackage
    if name == "CompleteEnvironmentRuntime":
        from .utils.tool_runtime import CompleteEnvironmentRuntime
        return CompleteEnvironmentRuntime
    raise AttributeError(name)


__all__ = [
    "CompleteEnvironmentPackage",
    "CompleteEnvironmentRuntime",
    "ProgramExecutionResult",
    "ProgramGenerationPolicy",
    "Step1Result",
    "Step2Result",
    "TaskFields",
    "ToolGenDelivery",
    "execute_solution_code",
    "run",
    "run_selected_steps",
    "run_step0",
    "run_step1",
    "run_step2",
    "validate_solution_code",
    "validate_task_research",
]
