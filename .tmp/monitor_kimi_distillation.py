from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
from typing import Any


_STREAM_ERROR_CACHE: dict[Path, tuple[int, int, bool]] = {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def response_stream_has_error(path: Path) -> bool:
    try:
        status = path.stat()
    except OSError:
        return False
    cached = _STREAM_ERROR_CACHE.get(path)
    signature = (status.st_size, status.st_mtime_ns)
    if cached is not None and cached[:2] == signature:
        return cached[2]
    event_name: str | None = None
    try:
        stream = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return False
    with stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                event_name = None
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            payload = line[5:].strip() if line.startswith("data:") else line
            if not payload.startswith("{"):
                continue
            try:
                value = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("error"), dict):
                _STREAM_ERROR_CACHE[path] = (*signature, True)
                return True
            if event_name == "error":
                _STREAM_ERROR_CACHE[path] = (*signature, True)
                return True
    _STREAM_ERROR_CACHE[path] = (*signature, False)
    return False


def jsonl_stats(run: Path) -> tuple[int, int, int, int, int, dict[str, int]]:
    requests = 0
    final_error_ids: set[tuple[str, int | str]] = set()
    stream_error_ids: set[tuple[str, int | str]] = set()
    retries = 0
    mcp_startup_failures = 0
    for task in run.glob("*__*"):
        if (task / "raw/mcp_startup_failure.json").is_file():
            mcp_startup_failures += 1
        model_io = task / "raw/model_io"
        if model_io.is_dir():
            requests += sum(1 for _ in model_io.glob("*.request.json"))
            for response in model_io.glob("*.response.sse"):
                if response_stream_has_error(response):
                    try:
                        request_id: int | str = int(response.name.split(".", 1)[0])
                    except ValueError:
                        request_id = response.name
                    stream_error_ids.add((task.name, request_id))
        responses = task / "raw/model_responses.jsonl"
        if not responses.is_file():
            continue
        for line in responses.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            status = row.get("status", 200)
            if row.get("origin") != "local_harness" and (
                row.get("error") or isinstance(status, int) and status >= 400
            ):
                final_error_ids.add((task.name, row.get("request_id", f"line-{len(final_error_ids)}")))
            attempts = row.get("attempts")
            if isinstance(attempts, list):
                retries += len(attempts)
    model_error_ids = final_error_ids | stream_error_ids
    stream_errors_by_task: dict[str, int] = {}
    for task_name, _request_id in stream_error_ids:
        stream_errors_by_task[task_name] = stream_errors_by_task.get(task_name, 0) + 1
    return (
        requests,
        len(model_error_ids),
        retries,
        mcp_startup_failures,
        len(stream_error_ids),
        stream_errors_by_task,
    )


def live_stream_error_counts(run: Path) -> dict[str, int]:
    """Count failed model streams only for tasks with a live Kimi process."""
    run = run.resolve()
    task_dirs: set[Path] = set()
    for process in Path("/proc").glob("[0-9]*"):
        try:
            if (process / "comm").read_text(encoding="utf-8").strip() != "kimi-code":
                continue
            cwd = (process / "cwd").resolve()
            relative = cwd.relative_to(run)
        except (OSError, ValueError):
            continue
        if relative.parts:
            task_dirs.add(run / relative.parts[0])
    counts: dict[str, int] = {}
    for task_dir in task_dirs:
        count = sum(
            response_stream_has_error(response)
            for response in (task_dir / "raw/model_io").glob("*.response.sse")
        )
        if count:
            counts[task_dir.name] = count
    return counts


def stop_repeated_stream_error_tasks(
    run: Path, counts: dict[str, int], limit: int
) -> list[dict[str, Any]]:
    stopped: list[dict[str, Any]] = []
    candidates = {
        (run / task_name).resolve(): count
        for task_name, count in counts.items()
        if count >= limit and not (run / task_name / "trajectory.json").is_file()
    }
    if not candidates:
        return stopped
    for process in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(process.name)
            if (process / "comm").read_text(encoding="utf-8").strip() != "kimi-code":
                continue
            cwd = (process / "cwd").resolve()
            fields = (process / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            process_group = int(fields[2])
        except (OSError, ValueError, IndexError):
            continue
        for task_dir, count in candidates.items():
            if not cwd.is_relative_to(task_dir) or process_group != pid:
                continue
            marker = task_dir / "raw/upstream_stream_stop.json"
            marker.write_text(json.dumps({
                "reason": "repeated_upstream_stream_errors",
                "stream_error_count": count,
                "limit": limit,
                "stopped_at": datetime.now().astimezone().isoformat(),
                "pid": pid,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                continue
            stopped.append({"task": task_dir.name, "pid": pid, "stream_errors": count})
    return stopped


def _jsonl_record_count(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            return sum(bool(line.strip()) for line in stream)
    except OSError:
        return 0


def _latest_distillation_run(root: Path) -> Path | None:
    summaries = list(root.glob("resumed*/*/summary.json"))
    summaries.extend(root.glob("20*/summary.json"))
    summaries.sort(key=lambda path: path.stat().st_mtime_ns)
    return summaries[-1].parent if summaries else None


def stop_exceeded_tool_budget_tasks(
    run: Path, limit: int
) -> list[dict[str, Any]]:
    """Stop live tasks whose native plus MCP tool calls exceed the total budget."""
    stopped: list[dict[str, Any]] = []
    run = run.resolve()
    for process in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(process.name)
            if (process / "comm").read_text(encoding="utf-8").strip() != "kimi-code":
                continue
            cwd = (process / "cwd").resolve()
            relative = cwd.relative_to(run)
            if not relative.parts:
                continue
            task_dir = run / relative.parts[0]
            fields = (process / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            process_group = int(fields[2])
        except (OSError, ValueError, IndexError):
            continue
        raw = task_dir / "raw"
        native_calls = _jsonl_record_count(raw / "native_file_access.jsonl")
        mcp_calls = _jsonl_record_count(raw / "environment_tool_calls.jsonl")
        observed = native_calls + mcp_calls
        if observed <= limit:
            continue
        marker = raw / "tool_budget_stop.json"
        if not marker.is_file():
            marker.write_text(json.dumps({
                "reason": "total_tool_call_limit_exceeded",
                "limit": limit,
                "observed_count": observed,
                "native_file_tool_calls": native_calls,
                "environment_mcp_tool_calls": mcp_calls,
                "stopped_at": datetime.now().astimezone().isoformat(),
                "pid": pid,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            continue
        stopped.append({
            "task": task_dir.name,
            "pid": pid,
            "observed_count": observed,
            "native_file_tool_calls": native_calls,
            "environment_mcp_tool_calls": mcp_calls,
        })
    return stopped


def write_line(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def service_state(unit: str) -> str:
    states: list[str] = []
    for command in (
        ["systemctl", "--user", "is-active", unit],
        ["systemctl", "is-active", unit],
    ):
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        state = result.stdout.strip()
        if state in {"active", "activating", "reloading", "deactivating"}:
            return state
        if state:
            states.append(state)
    return states[-1] if states else "unknown"


def stop_service(unit: str) -> None:
    subprocess.run(["systemctl", "--user", "stop", unit], check=False)
    if service_state(unit) in {"active", "activating", "reloading"}:
        subprocess.run(["sudo", "-n", "systemctl", "stop", unit], check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--minimum-free-gib", type=int, default=150)
    parser.add_argument("--error-window-minimum", type=int, default=10)
    parser.add_argument(
        "--minimum-concurrency-error-window-minimum",
        type=int,
        default=2,
        help="stop after this many new model errors while adaptive concurrency is at its floor",
    )
    parser.add_argument("--verification-error-window-minimum", type=int, default=5)
    parser.add_argument("--mcp-startup-failure-window-minimum", type=int, default=3)
    parser.add_argument("--per-task-stream-error-limit", type=int, default=2)
    parser.add_argument("--total-tool-call-limit", type=int, default=65)
    parser.add_argument("--tool-budget-poll-seconds", type=int, default=5)
    parser.add_argument(
        "--externally-enforce-tool-budget",
        action="store_true",
        help=(
            "terminate Kimi process groups when the observed tool count exceeds "
            "the limit; normally leave disabled and use the runner's native budget"
        ),
    )
    args = parser.parse_args()
    if args.total_tool_call_limit < 1:
        parser.error("--total-tool-call-limit must be positive")
    if args.tool_budget_poll_seconds < 1:
        parser.error("--tool-budget-poll-seconds must be positive")

    root = args.root.resolve()
    log = root / "monitor.jsonl"
    previous: dict[str, int] | None = None
    while True:
        state = service_state(args.unit)
        recovery_summaries = sorted(
            root.glob("recovery*/summary.json"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        recovery_path = recovery_summaries[-1] if recovery_summaries else None
        recovery = read_json(recovery_path) if recovery_path is not None else {}
        recovery_running = (
            recovery.get("status") == "running"
            or int(recovery.get("pending_verification") or 0) > 0
        )
        run = _latest_distillation_run(root)
        summary = read_json(run / "summary.json") if run else {}
        requests, final_errors, retries, mcp_startup_failures, stream_errors, stream_errors_by_task = (
            jsonl_stats(run) if run else (0, 0, 0, 0, 0, {})
        )
        abandoned_tasks = (
            stop_repeated_stream_error_tasks(
                run, stream_errors_by_task, args.per_task_stream_error_limit
            )
            if run and state == "active"
            else []
        )
        abandoned_tool_budget_tasks = (
            stop_exceeded_tool_budget_tasks(run, args.total_tool_call_limit)
            if args.externally_enforce_tool_budget and run and state == "active"
            else []
        )
        verification_errors = int((
            recovery.get("counts", {}).get("verification_error")
            if recovery_running
            else summary.get("verification_error_count")
        ) or 0)
        disk = shutil.disk_usage(root)
        free_gib = disk.free // (1024 ** 3)
        row = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "service": state,
            "phase": "recovery" if recovery_running else "distill",
            "recovery_summary": str(recovery_path) if recovery_path else None,
            "recovery": {
                key: recovery.get(key)
                for key in ("status", "processed", "pending_verification", "counts")
            },
            "run": str(run) if run else None,
            "summary": {
                key: summary.get(key)
                for key in (
                    "case_count", "completed_count", "failed_count",
                    "environment_stopped_count", "verified_count",
                    "verifier_passed_count", "verifier_failed_count",
                    "verification_error_count", "environment_error_count",
                    "tool_timeout_count", "started_count", "in_progress_count",
                    "not_started_count",
                )
            },
            "model_requests": requests,
            "final_model_errors": final_errors,
            "stream_model_errors": stream_errors,
            "stream_errors_by_task": stream_errors_by_task,
            "abandoned_stream_error_tasks": abandoned_tasks,
            "abandoned_tool_budget_tasks": abandoned_tool_budget_tasks,
            "upstream_retries": retries,
            "mcp_startup_failures": mcp_startup_failures,
            "free_disk_gib": free_gib,
        }
        critical = None
        if free_gib < args.minimum_free_gib:
            critical = f"free disk {free_gib} GiB is below {args.minimum_free_gib} GiB"
        run_key = (
            f"recovery:{recovery_path}"
            if recovery_running
            else str(run) if run is not None else None
        )
        same_run = previous is not None and previous.get("run") == run_key
        if same_run:
            verification_delta = verification_errors - previous["verification_errors"]
            if verification_delta >= args.verification_error_window_minimum:
                critical = f"{verification_delta} new verifier infrastructure errors"
            # Recovery inspects a stopped run, so its model/MCP counters are
            # historical. Only compare those counters while actively distilling
            # the same run in two consecutive samples.
            if not recovery_running:
                request_delta = requests - previous["requests"]
                error_delta = final_errors - previous["final_errors"]
                if request_delta >= 20 and error_delta >= args.error_window_minimum:
                    critical = f"{error_delta} final model errors in {request_delta} new requests"
                concurrency_target = summary.get("concurrency_target")
                minimum_concurrency = summary.get("minimum_concurrency")
                at_minimum_concurrency = (
                    isinstance(concurrency_target, int)
                    and isinstance(minimum_concurrency, int)
                    and concurrency_target <= minimum_concurrency
                )
                if (
                    request_delta > 0
                    and at_minimum_concurrency
                    and error_delta >= args.minimum_concurrency_error_window_minimum
                ):
                    critical = (
                        f"{error_delta} final model errors in {request_delta} new requests "
                        f"while at minimum concurrency {minimum_concurrency}"
                    )
                startup_failure_delta = (
                    mcp_startup_failures - previous["mcp_startup_failures"]
                )
                if startup_failure_delta >= args.mcp_startup_failure_window_minimum:
                    row["warning"] = (
                        f"{startup_failure_delta} new MCP startup failures; "
                        "individual tasks were abandoned and adaptive concurrency handles backoff"
                    )
        if critical and state == "active":
            row["action"] = "stop"
            row["reason"] = critical
            write_line(log, row)
            stop_service(args.unit)
            return
        write_line(log, row)
        previous = {
            "run": run_key,
            "requests": requests,
            "final_errors": final_errors,
            "verification_errors": verification_errors,
            "mcp_startup_failures": mcp_startup_failures,
        }
        if state not in {"active", "activating"}:
            return
        deadline = time.monotonic() + args.interval
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(args.tool_budget_poll_seconds, remaining))
            if service_state(args.unit) not in {"active", "activating"}:
                break
            budget_run = _latest_distillation_run(root)
            stopped_for_stream_errors: list[dict[str, Any]] = []
            if budget_run is not None:
                stopped_for_stream_errors = stop_repeated_stream_error_tasks(
                    budget_run,
                    live_stream_error_counts(budget_run),
                    args.per_task_stream_error_limit,
                )
            if stopped_for_stream_errors:
                write_line(log, {
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "service": "active",
                    "phase": "stream_error_enforcement",
                    "run": str(budget_run),
                    "per_task_stream_error_limit": args.per_task_stream_error_limit,
                    "abandoned_stream_error_tasks": stopped_for_stream_errors,
                })
            stopped_for_budget = (
                stop_exceeded_tool_budget_tasks(
                    budget_run, args.total_tool_call_limit
                )
                if args.externally_enforce_tool_budget and budget_run is not None
                else []
            )
            if stopped_for_budget:
                write_line(log, {
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "service": "active",
                    "phase": "tool_budget_enforcement",
                    "run": str(budget_run),
                    "total_tool_call_limit": args.total_tool_call_limit,
                    "abandoned_tool_budget_tasks": stopped_for_budget,
                })


if __name__ == "__main__":
    main()
