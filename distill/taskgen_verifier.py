"""Immediate verification and state cleanup for distilled task_gen cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import traceback
from typing import Any

from task_gen.task_eval_mcp import bind_delivery
from task_gen.task_eval_mcp import TaskEvalMcpServer
from task_gen.task_eval_verifier import serialized_state_preparation, verify_execution
from harness.layout import create_task_run_layout

from .runner import make_server_config


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_tree_links(source: Path, target: Path) -> None:
    shutil.copytree(source, target, copy_function=os.link)


def reconstruct_actual_state(case: Any, task_dir: Path, target: Path) -> None:
    """Rebuild the verifier's state view from the model workspace and private DB."""
    _copy_tree_links(case.initial_state, target)
    private = task_dir / "private-state"
    path_map = _read_json(private / "path-map.json")
    workspace = Path(path_map["workspace_root"]).resolve()
    if not workspace.is_relative_to(task_dir.resolve()):
        raise ValueError(f"workspace outside task directory: {workspace}")

    database = target / "records.sqlite"
    private_database = private / "records.sqlite"
    if private_database.is_file():
        if database.exists():
            database.unlink()
        shutil.copy2(private_database, database)
    elif database.exists():
        raise FileNotFoundError(f"private database is missing: {private_database}")

    scope_root = target / "filesystem_scopes"
    scope_root.mkdir(exist_ok=True)
    mounts = path_map["scope_mounts"]
    for item in case.environment.get("filesystem_scopes", []):
        scope_id = str(item["scope_id"])
        destination = scope_root / scope_id
        if destination.exists():
            shutil.rmtree(destination)
        mount = mounts[scope_id]
        source = workspace if mount == "." else workspace / mount
        _copy_tree_links(source, destination)


def _verifier_environment(case: Any) -> dict[str, Any]:
    config = bind_delivery(make_server_config(case.environment, runtime=case.runtime))
    environment = dict(case.environment)
    environment["tools"] = config["tools"]
    return environment


def _mutation_count(calls: list[dict[str, Any]], environment: dict[str, Any]) -> int:
    tools = {tool["name"]: tool for tool in environment.get("tools", [])}
    return sum(
        1
        for call in calls
        if tools.get(str(call.get("tool")), {}).get("usageConditions", {}).get("sideEffects")
        and call.get("error") is None
        and isinstance(call.get("result"), dict)
        and call["result"].get("success") is True
    )


def verify_distilled_case(
    case: Any,
    *,
    case_name: str,
    task_dir: Path,
    verifier_root: Path,
    codex_home: Path,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int,
    attempts: int,
) -> dict[str, Any]:
    """Verify one completed trajectory and retain the compact verdict/logs only."""
    verifier_dir = verifier_root / case_name
    if verifier_dir.exists():
        shutil.rmtree(verifier_dir)
    try:
        trajectory = _read_json(task_dir / "trajectory.json")
        trace_path = task_dir / "raw/environment_tool_calls.jsonl"
        calls = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        environment = _verifier_environment(case)
        verifier_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="actual-state-", dir=verifier_root) as temporary:
            actual_state = Path(temporary) / "state"
            with serialized_state_preparation():
                reconstruct_actual_state(case, task_dir, actual_state)
            verdict = verify_execution(
                run_dir=verifier_dir,
                config={
                    "codex_home": str(codex_home.expanduser().resolve()),
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "timeout_seconds": timeout_seconds,
                    "attempts": attempts,
                },
                task=case.task,
                environment=environment,
                initial_state=case.initial_state,
                actual_state=actual_state,
                calls=calls,
                answer=trajectory["outcome"]["final_answer"],
                execution={
                    "error": None,
                    "attempts": 1,
                    "harness": "official_kimi_code_cli",
                    "call_source": "raw",
                    "mutation_count": _mutation_count(calls, environment),
                },
                reference_state=case.reference_state,
                reference_calls=case.reference_calls,
                reference_answer=case.task.get("reference", {}).get("answer", ""),
            )
        return {
            "status": "verified",
            "outcome": verdict["outcome"],
            "summary": verdict["summary"],
            "failure_causes": verdict["failure_causes"],
            "verdict": str(verifier_dir / "verdict.json"),
        }
    except Exception as error:
        return {
            "status": "verification_error",
            "outcome": None,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        }
    finally:
        evidence = verifier_dir / "evidence"
        if evidence.is_dir():
            shutil.rmtree(evidence)


def replay_distilled_case_state(case: Any, source_task_dir: Path, target_task_dir: Path) -> Path:
    """Rebuild a deleted final state by replaying its recorded environment calls."""
    source_task_dir = source_task_dir.resolve()
    target_task_dir = target_task_dir.resolve()
    calls = [
        json.loads(line)
        for line in (source_task_dir / "raw/environment_tool_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    layout = create_task_run_layout(
        case.initial_state,
        target_task_dir,
        environment=case.environment,
        workspace_name=f"{case.task['environment_id']}_{case.task['task_id']}",
    )
    shutil.copy2(source_task_dir / "trajectory.json", layout.trajectory)
    config = make_server_config(
        case.environment,
        max_tool_calls=max(1, len(calls)),
        runtime=case.runtime,
    )
    config.update({
        "workspace_root": str(layout.model_workspace),
        "record_database": str(layout.record_database),
        "scope_mounts": layout.scope_mounts,
        "trace": str(layout.raw / "environment_tool_calls.jsonl"),
        "budget_stop_marker": str(layout.raw / "tool_budget_stop.json"),
        "tool_budget_counter": str(layout.raw / "tool_budget_counter.json"),
    })
    server = TaskEvalMcpServer(config)
    for index, call in enumerate(calls, start=1):
        server.handle({
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {
                "name": call["tool"],
                "arguments": call.get("arguments", {}),
            },
        })
    replayed = [
        line for line in (layout.raw / "environment_tool_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    if len(replayed) != len(calls):
        raise RuntimeError(f"replayed {len(replayed)} of {len(calls)} environment calls")
    return target_task_dir


def delete_task_state(task_dir: Path) -> list[str]:
    """Delete mutable execution state without touching trajectory or raw evidence."""
    task_dir = task_dir.resolve()
    private = task_dir / "private-state"
    candidates: list[Path] = []
    path_map = private / "path-map.json"
    if path_map.is_file():
        workspace_value = _read_json(path_map).get("workspace_root")
        if isinstance(workspace_value, str):
            workspace = Path(workspace_value).resolve()
            if not workspace.is_relative_to(task_dir):
                raise ValueError(f"refusing to delete workspace outside task directory: {workspace}")
            candidates.append(workspace)
    candidates.append(private)

    removed = []
    for path in dict.fromkeys(candidates):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
        elif path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


__all__ = [
    "delete_task_state",
    "reconstruct_actual_state",
    "replay_distilled_case_state",
    "verify_distilled_case",
]
