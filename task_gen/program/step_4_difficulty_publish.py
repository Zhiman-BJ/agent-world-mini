"""Step 4: independently evaluate difficulty and publish accepted tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .steps.step5_evaluate_difficulty import run_step5
from .utils.io import read_json, read_records


def evaluate_and_publish(input: dict[str, Any], *, rubric_agent: Any = None, solve_fn: Any = None) -> dict[str, Any]:
    config = input["config"]
    kwargs = {
        "step1_path": Path(input["step1_path"]),
        "step4_path": Path(input["step4_path"]),
        "output_dir": Path(input["run_dir"]),
        "policy": config.policy,
        "model": config.model,
        "rubric_agent": rubric_agent,
        "solver_timeout_seconds": config.agent_timeout_seconds,
        "max_solver_tool_calls": config.max_solver_tool_calls,
    }
    if solve_fn is not None:
        kwargs["solve_fn"] = solve_fn
    output = run_step5(**kwargs)
    return {
        "final_tasks": read_json(output.final_path),
        "rejected": read_records(output.rework_path),
        "difficulty": read_records(output.difficulty_path),
    }
