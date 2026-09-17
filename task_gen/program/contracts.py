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


@dataclass
class Config:
    environment_package: Path | None = None
    output_root: Path = PROJECT_ROOT / "runs/program"
    model: str = "gpt-5.6-sol"
    policy: ProgramGenerationPolicy = field(default_factory=ProgramGenerationPolicy)
    tools_path: Path | None = None
    delivery_root: Path | None = None
    binding_path: Path | None = None
    package_id: str | None = None
    scenario_research_path: Path | None = None
    research_fixture_path: Path | None = None
    candidates_path: Path | None = None
    agent_timeout_seconds: int = 1800


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
    step0_path: Path
    step1_path: Path
    tasks: list[dict[str, Any]]


class StageOutput(TypedDict, total=False):
    environment: dict[str, Any]
    step0_path: str
    task_research: dict[str, Any]
    step1_path: str
    tasks: list[dict[str, Any]]
    generated_tasks: list[dict[str, Any]]
    step2_path: str
    external_bundle_path: str
    rejected: list[dict[str, Any]]
