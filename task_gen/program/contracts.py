"""Shared contracts for the Tool Graph-shaped Program pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict

from .utils.contracts import ProgramGenerationPolicy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AppendOnlyBundle = dict[str, Any]


class ProgramPipelineStep(str, Enum):
    ENVIRONMENT_LOAD = "step_0_environment_load"
    TASK_RESEARCH = "step_1_task_research"
    SOLUTION_GENERATE = "step_2_solution_generate"
    SCORING_GENERATE = "step_3_scoring_generate"
    DIFFICULTY_PUBLISH = "step_4_difficulty_publish"


@dataclass
class Config:
    environment_package: Path = PROJECT_ROOT / "artifacts/toolgen-reality-openalex-run"
    output_root: Path = PROJECT_ROOT / "runs/program"
    model: str = "gpt-5.6-terra"
    policy: ProgramGenerationPolicy = field(default_factory=ProgramGenerationPolicy)
    tools_path: Path | None = None
    scenario_research_path: Path | None = None
    research_fixture_path: Path | None = None
    candidates_path: Path | None = None
    scoring_fixture_path: Path | None = None
    agent_timeout_seconds: int = 1800
    max_solver_tool_calls: int = 100


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    task_count: int
    rejected_count: int
    cost_report: dict[str, Any] = field(default_factory=dict)


class StageInput(TypedDict, total=False):
    config: Config
    run_dir: Path
    environment: dict[str, Any]
    step1_path: Path
    step2_path: Path
    step3_path: Path
    step4_path: Path
    tasks: list[dict[str, Any]]


class StageOutput(TypedDict, total=False):
    environment: dict[str, Any]
    step1_path: str
    task_research: dict[str, Any]
    step2_path: str
    tasks: list[dict[str, Any]]
    step3_path: str
    scoring: list[dict[str, Any]]
    step4_path: str
    final_tasks: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
