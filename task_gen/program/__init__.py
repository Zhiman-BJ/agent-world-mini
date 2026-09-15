"""Tool Graph-shaped five-stage Program task generation pipeline."""

from .utils.contracts import ProgramGenerationPolicy, TaskFields


def __getattr__(name):
    if name == "run":
        from .pipeline import run
        return run
    if name == "run_selected_steps":
        from .run_pipeline import run_selected_steps
        return run_selected_steps
    if name in {"ProgramExecutionResult", "execute_solution_code", "validate_solution_code"}:
        from .steps.step3_generate_task_solution import ProgramExecutionResult, execute_solution_code, validate_solution_code
        return locals()[name]
    if name == "CompleteEnvironmentPackage":
        from .utils.environment import CompleteEnvironmentPackage
        return CompleteEnvironmentPackage
    if name == "CompleteEnvironmentRuntime":
        from .utils.tool_runtime import CompleteEnvironmentRuntime
        return CompleteEnvironmentRuntime
    if name.startswith("run_step"):
        from . import steps
        return getattr(steps, name)
    raise AttributeError(name)

__all__ = [
    "CompleteEnvironmentPackage",
    "CompleteEnvironmentRuntime",
    "ProgramExecutionResult",
    "ProgramGenerationPolicy",
    "TaskFields",
    "execute_solution_code",
    "run_selected_steps",
    "run",
    "run_step1",
    "run_step2",
    "run_step3",
    "run_step4",
    "run_step5",
    "validate_solution_code",
]
