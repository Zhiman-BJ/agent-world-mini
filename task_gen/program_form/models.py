from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProgramGenerationPolicy:
    task_count: int = 2
    candidate_multiplier: int = 2
    min_tool_calls: int = 4
    min_distinct_tools: int = 2
    clean_replays: int = 2
    require_state_change: bool = False
    max_repair_rounds: int = 2
    execution_timeout_seconds: float = 15.0

    def validate(self) -> None:
        if self.task_count < 1:
            raise ValueError("task_count 必须至少为 1")
        if self.candidate_multiplier < 1:
            raise ValueError("candidate_multiplier 必须至少为 1")
        if self.min_tool_calls < 1:
            raise ValueError("min_tool_calls 必须至少为 1")
        if self.min_distinct_tools < 1:
            raise ValueError("min_distinct_tools 必须至少为 1")
        if self.clean_replays < 2:
            raise ValueError("clean_replays 必须至少为 2")
        if self.max_repair_rounds < 0:
            raise ValueError("max_repair_rounds 不能小于 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "candidate_count": self.task_count * self.candidate_multiplier,
            "min_tool_calls": self.min_tool_calls,
            "min_distinct_tools": self.min_distinct_tools,
            "clean_replays": self.clean_replays,
            "require_state_change": self.require_state_change,
            "max_repair_rounds": self.max_repair_rounds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
        }


@dataclass
class ProgramTaskCandidate:
    task: str
    output_schema: dict[str, Any]
    solution_code: str
    design: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramTaskCandidate":
        return cls(
            task=str(value["task"]).strip(),
            output_schema=deepcopy(value["output_schema"]),
            solution_code=str(value["solution_code"]).strip(),
            design=deepcopy(value["design"]),
        )


@dataclass(frozen=True)
class ProgramGenerationResult:
    output_dir: Path
    tasks_path: Path
    validation_path: Path
    candidates_path: Path
    task_count: int
    repair_rounds: int
