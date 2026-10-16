"""Follow a recovery supervisor and retry newly completed zero-task runs once."""

from __future__ import annotations

import argparse
import fcntl
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .retry_zero_task_environments import (
    _bindings,
    _read_json,
    _run_one,
    _task_count,
    _write_json,
)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _active_environment_ids(batch_root: Path) -> set[str]:
    """Read real Pipeline argv values without matching the monitor itself."""
    active: set[str] = set()
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        arguments = [
            value.decode("utf-8", errors="replace")
            for value in raw.split(b"\0")
            if value
        ]
        if "task_gen.program.run_pipeline" not in arguments:
            continue
        try:
            output = Path(arguments[arguments.index("--output-dir") + 1]).resolve()
            relative = output.relative_to(batch_root)
        except (ValueError, IndexError):
            continue
        if len(relative.parts) >= 2:
            active.add(relative.parts[0])
    return active


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-root", type=Path, action="append", required=True)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--source-status-name", default="retry_zero_status.json")
    parser.add_argument("--source-pid-file-name", default="retry_zero_supervisor.pid")
    parser.add_argument("--status-file-name", default="retry_zero_follower_status.json")
    parser.add_argument("--lock-file-name", default="retry_zero_follower.lock")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument(
        "--minimum-uncovered-polls",
        type=int,
        default=1,
        help=(
            "环境连续多少次轮询都已结束、仍为零任务且没有其他 Pipeline "
            "处理时才启动；大于 1 可让多个跟随监督器安全分层补位"
        ),
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--task-count", type=int, default=10)
    parser.add_argument("--generation-attempts", type=int, default=8)
    parser.add_argument("--repair-rounds", type=int, default=6)
    parser.add_argument("--minimum-effective-tool-calls", type=int, default=15)
    parser.add_argument("--execution-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--quality-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--agent-timeout-seconds", type=int, default=3600)
    parser.add_argument("--review-timeout-seconds", type=int, default=600)
    parser.add_argument("--research-attempts", type=int, default=5)
    return parser


def _local_file(batch_root: Path, name: str, label: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{label} 必须是批次目录内的普通文件名")
    return batch_root / name


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.workers < 1:
        raise ValueError("--workers 必须至少为 1")
    if arguments.poll_seconds <= 0:
        raise ValueError("--poll-seconds 必须大于 0")
    if arguments.minimum_uncovered_polls < 1:
        raise ValueError("--minimum-uncovered-polls 必须至少为 1")

    batch_root = arguments.batch_root.resolve()
    project_root = Path(__file__).resolve().parents[2]
    source_status_path = _local_file(
        batch_root, arguments.source_status_name, "--source-status-name",
    )
    source_pid_path = _local_file(
        batch_root, arguments.source_pid_file_name, "--source-pid-file-name",
    )
    status_path = _local_file(
        batch_root, arguments.status_file_name, "--status-file-name",
    )
    lock_path = _local_file(
        batch_root, arguments.lock_file_name, "--lock-file-name",
    )
    lock_stream = lock_path.open("a+")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("已有零任务跟随监督器正在运行") from error

    source_pid = int(source_pid_path.read_text(encoding="utf-8").strip())
    bindings = _bindings([path.resolve() for path in arguments.delivery_root])
    run_arguments = SimpleNamespace(
        model=arguments.model,
        task_count=arguments.task_count,
        generation_attempts=arguments.generation_attempts,
        repair_rounds=arguments.repair_rounds,
        minimum_effective_tool_calls=arguments.minimum_effective_tool_calls,
        execution_timeout_seconds=arguments.execution_timeout_seconds,
        quality_timeout_seconds=arguments.quality_timeout_seconds,
        agent_timeout_seconds=arguments.agent_timeout_seconds,
        review_timeout_seconds=arguments.review_timeout_seconds,
        research_attempts=arguments.research_attempts,
    )
    records: dict[str, dict[str, Any]] = {}
    if status_path.is_file():
        previous = _read_json(status_path)
        records.update(previous.get("records", {}))
    attempted = set(records)
    running: dict[Future[dict[str, Any]], str] = {}
    uncovered_observations: dict[str, int] = {}
    started_at = datetime.now(timezone.utc).isoformat()

    def publish(source: dict[str, Any], waiting: list[str]) -> None:
        _write_json(status_path, {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at,
            "source_status": str(source_status_path),
            "source_alive": _process_alive(source_pid),
            "source_completed": int(source.get("completed", 0)),
            "source_target_count": int(source.get("target_count", 0)),
            "workers": arguments.workers,
            "running": sorted(running.values()),
            "waiting_on_other_runs": waiting,
            "observing_uncovered": dict(sorted(uncovered_observations.items())),
            "minimum_uncovered_polls": arguments.minimum_uncovered_polls,
            "attempted": len(attempted),
            "accepted": sum(int(item.get("accepted", 0)) for item in records.values()),
            "records": records,
        })

    with ThreadPoolExecutor(
        max_workers=arguments.workers, thread_name_prefix="zero-follow",
    ) as pool:
        while True:
            for future, environment_id in list(running.items()):
                if not future.done():
                    continue
                try:
                    records[environment_id] = future.result()
                except Exception as error:
                    records[environment_id] = {
                        "environment_id": environment_id,
                        "status": "runner_error",
                        "accepted": 0,
                        "passed": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                del running[future]

            source = _read_json(source_status_path)
            active = _active_environment_ids(batch_root)
            waiting: list[str] = []
            eligible: list[str] = []
            for environment_id, record in source.get("records", {}).items():
                if int(record.get("accepted", 0)) != 0:
                    uncovered_observations.pop(environment_id, None)
                    continue
                if environment_id in attempted or _task_count(batch_root / environment_id):
                    uncovered_observations.pop(environment_id, None)
                    continue
                if environment_id in active:
                    uncovered_observations.pop(environment_id, None)
                    waiting.append(environment_id)
                elif environment_id in bindings:
                    observations = uncovered_observations.get(environment_id, 0) + 1
                    uncovered_observations[environment_id] = observations
                    if observations >= arguments.minimum_uncovered_polls:
                        eligible.append(environment_id)
                else:
                    uncovered_observations.pop(environment_id, None)
                    attempted.add(environment_id)
                    records[environment_id] = {
                        "environment_id": environment_id,
                        "status": "missing_binding",
                        "accepted": 0,
                        "passed": False,
                    }

            capacity = arguments.workers - len(running)
            for environment_id in sorted(eligible)[:capacity]:
                # Recheck after discovery so a task published by another run in
                # this polling interval wins and no duplicate attempt starts.
                if _task_count(batch_root / environment_id):
                    uncovered_observations.pop(environment_id, None)
                    continue
                active = _active_environment_ids(batch_root)
                if environment_id in active:
                    uncovered_observations.pop(environment_id, None)
                    waiting.append(environment_id)
                    continue
                attempted.add(environment_id)
                uncovered_observations.pop(environment_id, None)
                future = pool.submit(
                    _run_one,
                    environment_id,
                    bindings[environment_id],
                    batch_root=batch_root,
                    project_root=project_root,
                    arguments=run_arguments,
                )
                running[future] = environment_id

            publish(source, sorted(set(waiting)))
            source_done = (
                not _process_alive(source_pid)
                or int(source.get("completed", 0)) >= int(source.get("target_count", 0))
            )
            if source_done and not running and not waiting and not eligible:
                break
            time.sleep(arguments.poll_seconds)


if __name__ == "__main__":
    main()
