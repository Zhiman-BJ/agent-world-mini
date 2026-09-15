"""Step 3: generate executable rubric and verifier code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .steps.step4_generate_scoring_criteria import run_step4
from .utils.io import read_records


def generate_scoring(input: dict[str, Any], *, agent: Any = None) -> dict[str, Any]:
    config = input["config"]
    output = run_step4(
        step3_path=Path(input["step3_path"]),
        output_dir=Path(input["run_dir"]),
        policy=config.policy,
        agent=agent,
        scoring_fixture_path=config.scoring_fixture_path,
    )
    return {"tasks": read_records(output.output_path), "scoring": read_records(output.output_path), "step4_path": str(output.output_path)}
