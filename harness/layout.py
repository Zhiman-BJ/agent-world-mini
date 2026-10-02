"""Per-task directory layout with separate model and execution state roots."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskRunLayout:
    output: Path
    execution_state: Path
    model_workspace: Path
    raw: Path
    trajectory: Path


def create_task_run_layout(initial_state: Path, output_dir: Path) -> TaskRunLayout:
    initial_state = initial_state.expanduser()
    if initial_state.is_symlink():
        raise ValueError("initial_state must not contain symbolic links")
    initial_state = initial_state.resolve()
    output_dir = output_dir.expanduser().resolve()
    if not initial_state.is_dir():
        raise ValueError(f"initial_state is not a directory: {initial_state}")
    if any(path.is_symlink() for path in [initial_state, *initial_state.rglob("*")]):
        raise ValueError("initial_state must not contain symbolic links")
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")

    output_dir.mkdir(parents=True)
    execution_state = output_dir / "execution-state"
    shutil.copytree(initial_state, execution_state)
    model_workspace = output_dir / "model-workspace"
    model_workspace.mkdir()
    raw = output_dir / "raw"
    raw.mkdir()
    return TaskRunLayout(
        output=output_dir,
        execution_state=execution_state,
        model_workspace=model_workspace,
        raw=raw,
        trajectory=output_dir / "trajectory.json",
    )


__all__ = ["TaskRunLayout", "create_task_run_layout"]
