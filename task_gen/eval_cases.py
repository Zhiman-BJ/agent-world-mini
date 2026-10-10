"""Load generated task cases for the official CLI distillation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    source_run: Path
    task: dict[str, Any]
    environment: dict[str, Any]
    initial_state: Path
    reference_state: Path | None
    reference_calls: list[dict[str, Any]]
    runtime: dict[str, Any] = field(default_factory=dict)


def load_cases(
    input_root: Path,
    *,
    selected_case_names: set[str] | None = None,
) -> list[EvalCase]:
    """Read tasks from the latest complete run for each environment."""
    input_root = input_root.expanduser().resolve()
    collection_root = input_root
    preserve_collection_entries = (
        (collection_root / "manifest.json").is_file()
        and (collection_root / "environments").is_dir()
    )
    if not any(input_root.glob("*/tasks.json")) and (input_root / "environments").is_dir():
        input_root = input_root / "environments"
    selected_by_run: dict[str, set[str]] | None = None
    if selected_case_names is not None:
        selected_by_run = {}
        for name in selected_case_names:
            source_name, separator, task_id = name.rpartition("__")
            if not separator or not source_name or not task_id:
                raise ValueError(f"invalid selected case name: {name}")
            selected_by_run.setdefault(source_name, set()).add(task_id)
    latest: dict[str, tuple[Path, Path, dict[str, Any], dict[str, Any]]] = {}
    for tasks_path in sorted(input_root.glob("*/tasks.json")):
        source_run = tasks_path.parent
        if selected_by_run is not None and source_run.name not in selected_by_run:
            continue
        bundle_path = source_run / "intermediate/step_5_bundle.json"
        if not bundle_path.is_file():
            continue
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        environment = bundle.get("environment")
        if not isinstance(environment, dict):
            raise ValueError(f"无效的正式任务 run：{source_run}")
        environment_id = environment.get("environment_id")
        if not isinstance(environment_id, str) or not environment_id:
            raise ValueError(f"环境缺少 environment_id：{source_run}")
        selection_key = source_run.name if preserve_collection_entries else environment_id
        previous = latest.get(selection_key)
        if previous is None or source_run.name > previous[0].name:
            latest[selection_key] = (source_run, tasks_path, environment, bundle)

    cases: list[EvalCase] = []
    for _, (source_run, tasks_path, environment, bundle) in sorted(latest.items()):
        environment_id = environment["environment_id"]
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            raise ValueError(f"无效的正式任务 run：{source_run}")
        bundle_tasks = {
            candidate.get("task_id"): candidate
            for candidate in bundle.get("tasks", [])
            if isinstance(candidate, dict) and isinstance(candidate.get("task_id"), str)
        }
        for task in tasks:
            if (
                selected_by_run is not None
                and task.get("task_id") not in selected_by_run[source_run.name]
            ):
                continue
            if not isinstance(task, dict) or not isinstance(task.get("initial_state"), str):
                raise ValueError(f"无效任务：{source_run}")
            task_id = task.get("task_id")
            if (
                not isinstance(task_id, str)
                or not task_id
                or "/" in task_id
                or task_id in {".", ".."}
            ):
                raise ValueError(f"无效 task_id：{task_id}")
            relative = Path(task["initial_state"])
            initial_state = (source_run / relative).resolve()
            if relative.is_absolute() or not initial_state.is_relative_to(source_run.resolve()):
                raise ValueError(f"初态路径越界：{relative}")
            if not initial_state.is_dir():
                raise ValueError(f"任务初态不存在：{initial_state}")
            if any(path.is_symlink() for path in [initial_state, *initial_state.rglob("*")]):
                raise ValueError(f"初态路径不得包含符号链接：{initial_state}")
            if task.get("environment_id") != environment_id:
                raise ValueError(f"任务 environment_id 与环境不一致：{source_run}")
            reference_state = None
            reference_relative = task.get("reference", {}).get("final_state")
            if isinstance(reference_relative, str) and reference_relative:
                reference_path = Path(reference_relative)
                reference_state = (source_run / reference_path).resolve()
                if (
                    reference_path.is_absolute()
                    or not reference_state.is_relative_to(source_run.resolve())
                ):
                    raise ValueError(f"参考终态路径越界：{reference_relative}")
                if not reference_state.is_dir():
                    raise ValueError(f"参考终态不存在：{reference_state}")
                if any(
                    path.is_symlink()
                    for path in [reference_state, *reference_state.rglob("*")]
                ):
                    raise ValueError(f"参考终态路径不得包含符号链接：{reference_state}")
            execution = bundle_tasks.get(task_id, {}).get("execution", {})
            bundle_calls = execution.get("tool_calls") if isinstance(execution, dict) else None
            reference_calls = (
                bundle_calls
                if isinstance(bundle_calls, list)
                else task.get("reference", {}).get("tool_calls", [])
            )
            runtime = bundle.get("runtime")
            runtime = dict(runtime) if isinstance(runtime, dict) else {}
            delivery = bundle.get("toolgen_delivery")
            if not runtime.get("binding_path") and isinstance(delivery, dict):
                binding_path = delivery.get("binding_path")
                if isinstance(binding_path, str) and binding_path:
                    runtime["binding_path"] = binding_path
            if runtime.get("binding_path") and not runtime.get("tools_path"):
                repaired_tools = (
                    Path(str(runtime["binding_path"])).expanduser().resolve().parent
                    / "tools/tools_repaired.json"
                )
                if not repaired_tools.is_file():
                    raise ValueError(f"任务交付缺少 tools_repaired.json：{repaired_tools}")
                runtime["tools_path"] = str(repaired_tools)
            cases.append(EvalCase(
                source_run,
                task,
                environment,
                initial_state,
                reference_state,
                reference_calls,
                runtime,
            ))
    return cases


__all__ = ["EvalCase", "load_cases"]
