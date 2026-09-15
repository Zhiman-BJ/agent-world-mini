"""Step 1: research real-world task archetypes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .steps.step2_research_real_world_tasks import run_step2
from .utils.io import read_json


def research_tasks(input: dict[str, Any], *, agent: Any = None) -> dict[str, Any]:
    config = input["config"]
    step1_path = Path(input["step1_path"])
    output = run_step2(
        step1_path=step1_path,
        output_dir=Path(input["run_dir"]),
        agent=agent,
        research_fixture_path=config.research_fixture_path,
    )
    return {"task_research": read_json(output.output_path), "step2_path": str(output.output_path)}
