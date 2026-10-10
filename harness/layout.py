"""Per-task directory layout for model-visible files and private record state."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class TaskRunLayout:
    output: Path
    model_workspace: Path
    private_state: Path
    record_database: Path
    path_map: Path
    scope_mounts: dict[str, str]
    raw: Path
    trajectory: Path


def _safe_name(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return normalized[:120] or fallback


def create_task_run_layout(
    initial_state: Path,
    output_dir: Path,
    *,
    environment: dict[str, Any],
    workspace_name: str | None = None,
) -> TaskRunLayout:
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

    if not isinstance(environment, dict):
        raise TypeError("environment must be an object")

    output_dir.mkdir(parents=True)
    name = _safe_name(
        workspace_name or str(environment.get("environment_id") or "task"),
        "task",
    )
    model_workspace = output_dir / name
    model_workspace.mkdir()
    private_state = output_dir / "private-state"
    private_state.mkdir()
    record_database = private_state / "records.sqlite"
    source_database = initial_state / "records.sqlite"
    if source_database.is_file():
        shutil.copy2(source_database, record_database)

    scopes = [
        str(item["scope_id"])
        for item in environment.get("filesystem_scopes", [])
    ]
    if len(scopes) != len(set(scopes)):
        raise ValueError("environment contains duplicate filesystem scope ids")
    scope_mounts = {
        scope_id: "." if len(scopes) == 1 else _safe_name(scope_id, f"files_{index + 1}")
        for index, scope_id in enumerate(scopes)
    }
    if len(set(scope_mounts.values())) != len(scope_mounts):
        raise ValueError("filesystem scope names collide after path normalization")
    for scope_id, mount in scope_mounts.items():
        source = initial_state / "filesystem_scopes" / scope_id
        if not source.is_dir():
            raise ValueError(f"initial state is missing filesystem scope: {scope_id}")
        destination = model_workspace if mount == "." else model_workspace / mount
        if mount == ".":
            for child in source.iterdir():
                target = destination / child.name
                if child.is_dir():
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
        else:
            shutil.copytree(source, destination)
    path_map = private_state / "path-map.json"
    path_map.write_text(
        json.dumps(
            {
                "workspace_root": str(model_workspace),
                "record_database": str(record_database),
                "scope_mounts": scope_mounts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    raw = output_dir / "raw"
    raw.mkdir()
    return TaskRunLayout(
        output=output_dir,
        model_workspace=model_workspace,
        private_state=private_state,
        record_database=record_database,
        path_map=path_map,
        scope_mounts=scope_mounts,
        raw=raw,
        trajectory=output_dir / "trajectory.json",
    )


__all__ = ["TaskRunLayout", "create_task_run_layout"]
