"""Step 2: generate, execute, repair, and replay Program solutions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .steps.step3_generate_task_solution import run_step3
from .utils.io import read_records


def generate_solutions(input: dict[str, Any], *, generation_agent: Any = None, review_agent: Any = None) -> dict[str, Any]:
    config = input["config"]
    output = run_step3(
        step1_path=Path(input["step1_path"]),
        step2_path=Path(input["step2_path"]),
        output_dir=Path(input["run_dir"]),
        policy=config.policy,
        generation_agent=generation_agent,
        review_agent=review_agent or generation_agent,
        candidates_path=config.candidates_path,
    )
    return {"tasks": read_records(output.output_path), "step3_path": str(output.output_path)}
