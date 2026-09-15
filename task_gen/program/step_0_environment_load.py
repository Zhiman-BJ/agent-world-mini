"""Step 0: freeze the input environment package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .steps.step1_prepare_environment import run_step1
from .utils.io import read_json


def load_environment(input: dict[str, Any]) -> dict[str, Any]:
    config = input["config"]
    run_dir = Path(input["run_dir"])
    receipt = run_step1(
        environment_package=config.environment_package,
        tools_path=config.tools_path,
        scenario_research_path=config.scenario_research_path,
        output_dir=run_dir,
    )
    payload = read_json(receipt)
    return {"environment": payload["public_environment"], "step1_path": str(receipt)}
