"""Retry terminal environments that produced no accepted Program-form tasks.

The recovery run is intentionally separate from the primary supervisor. It
reuses valid Step 1 research, carries forward actionable checker feedback, and
never touches environments that are still running or already have a task.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_FAILURE_MARKERS = (
    "tool_unavailable",
    "tool_timed_out",
    "当前不可用",
    "执行超时",
    "timeoutexpired",
    "cannot open shared object file",
    "no module named",
    "工具沙箱异常退出",
    "threadpoolbuilderror",
    "pthread_create",
)


class _ConcurrencyGate:
    """Limit active recovery runs while allowing the limit to grow in place."""

    def __init__(self, limit: int) -> None:
        self._condition = threading.Condition()
        self._limit = limit
        self._active = 0

    def acquire(self) -> None:
        with self._condition:
            while self._active >= self._limit:
                self._condition.wait()
            self._active += 1

    def release(self) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def set_limit(self, limit: int) -> None:
        with self._condition:
            if limit < self._limit:
                raise ValueError("恢复并发只能在运行期间增加")
            self._limit = limit
            self._condition.notify_all()

    def snapshot(self) -> tuple[int, int]:
        with self._condition:
            return self._limit, self._active


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _task_count(environment_root: Path) -> int:
    return sum(
        1
        for path in environment_root.glob("attempt_*/tasks/*_program_*")
        if path.is_dir()
    )


def _run_task_count(run_dir: Path) -> int:
    return sum(
        1
        for path in run_dir.glob("tasks/*_program_*")
        if path.is_dir()
    )


def _validation_passed(run_dir: Path, task_count: int) -> bool:
    path = run_dir / "step2_validation.json"
    if not path.is_file():
        return False
    try:
        value = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return value.get("status") == "passed" and int(value.get("accepted", 0)) >= task_count


def _environment_completed(environment_root: Path, task_count: int) -> bool:
    return any(
        _validation_passed(run_dir, task_count)
        for run_dir in environment_root.glob("attempt_*")
        if run_dir.is_dir()
    )


def _prior_feedback(environment_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    previous_rejections: list[dict[str, Any]] = []
    avoid_tools: set[str] = set()
    validations = sorted(environment_root.glob("attempt_*/step2_validation.json"))
    if validations:
        try:
            value = _read_json(validations[-1])
            previous_rejections = [
                item
                for item in value.get("rejections", [])
                if isinstance(item, dict)
            ][-12:]
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    for rejection in previous_rejections:
        text = json.dumps(rejection.get("reasons", []), ensure_ascii=False)
        folded = text.casefold()
        if not any(marker in folded for marker in TOOL_FAILURE_MARKERS):
            continue
        avoid_tools.update(
            match.group(1)
            for match in re.finditer(
                r"工具\s+([A-Za-z][A-Za-z0-9_.-]*)\s+"
                r"(?:执行失败|执行超时|当前不可用)",
                text,
            )
        )
    return previous_rejections, sorted(avoid_tools)


def _bindings(delivery_roots: list[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for root in delivery_roots:
        if not root.is_dir():
            continue
        for environment in sorted(root.iterdir()):
            binding = environment / "binding.json"
            if environment.is_dir() and binding.is_file():
                result.setdefault(environment.name, binding)
    return result


def _active_pipeline_process_count() -> int:
    """Count live Program pipeline processes without relying on shell tools."""
    marker = b"task_gen.program.run_pipeline"
    count = 0
    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if marker in command_path.read_bytes():
                count += 1
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return count


def _active_environment_ids(batch_root: Path) -> set[str]:
    """Return environment IDs currently handled by Program pipeline processes."""
    active: set[str] = set()
    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = [
                item.decode("utf-8", errors="replace")
                for item in command_path.read_bytes().split(b"\0")
                if item
            ]
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if "task_gen.program.run_pipeline" not in arguments:
            continue
        try:
            output_dir = Path(
                arguments[arguments.index("--output-dir") + 1]
            ).resolve()
            relative = output_dir.relative_to(batch_root)
        except (ValueError, IndexError):
            continue
        if len(relative.parts) >= 2:
            active.add(relative.parts[0])
    return active


def _release_baseline(environment_root: Path) -> None:
    for baseline in environment_root.glob("attempt_*/baseline_environment"):
        try:
            if baseline.is_symlink():
                baseline.unlink()
            elif baseline.is_dir():
                shutil.rmtree(baseline)
        except FileNotFoundError:
            pass


def _copy_research(environment_root: Path, run_dir: Path) -> bool:
    attempts = sorted(
        (path for path in environment_root.glob("attempt_*") if path != run_dir),
        reverse=True,
    )
    for attempt in attempts:
        source = attempt / "step1_task_research.json"
        if source.is_file():
            shutil.copy2(source, run_dir / source.name)
            return True
    return False


def _attempt_number(environment_root: Path) -> int:
    numbers = []
    for path in environment_root.glob("attempt_*"):
        match = re.fullmatch(r"attempt_(\d+)", path.name)
        if match and path.is_dir():
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _run_one(
    environment_id: str,
    binding: Path,
    *,
    batch_root: Path,
    project_root: Path,
    arguments: argparse.Namespace,
) -> dict[str, Any]:
    environment_root = batch_root / environment_id
    attempt = _attempt_number(environment_root)
    run_dir = environment_root / f"attempt_{attempt:02d}"
    run_dir.mkdir(parents=True, exist_ok=False)
    reused_research = _copy_research(environment_root, run_dir)
    previous_rejections, avoid_tools = _prior_feedback(environment_root)
    _write_json(run_dir / "retry_context.json", {
        "schema_version": "1.0",
        "source": "zero_task_recovery",
        "previous_rejections": previous_rejections,
        "avoid_tools": avoid_tools,
    })
    steps = "0,2" if reused_research else "all"
    command = [
        sys.executable,
        "-m",
        "task_gen.program.run_pipeline",
        "--step",
        steps,
        "--binding",
        str(binding),
        "--output-dir",
        str(run_dir),
        "--model",
        arguments.model,
        "--task-count",
        str(arguments.task_count),
        "--task-generation-attempts",
        str(arguments.generation_attempts),
        "--max-repair-rounds",
        str(arguments.repair_rounds),
        "--minimum-effective-tool-calls",
        str(arguments.minimum_effective_tool_calls),
        "--execution-timeout-seconds",
        str(arguments.execution_timeout_seconds),
        "--quality-timeout-seconds",
        str(arguments.quality_timeout_seconds),
        "--agent-timeout-seconds",
        str(arguments.agent_timeout_seconds),
        "--review-timeout-seconds",
        str(arguments.review_timeout_seconds),
        "--clean-replays",
        "1",
        "--research-attempts",
        str(arguments.research_attempts),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PYTHONUNBUFFERED"] = "1"
    environment["AGENT_WORLD_DOCKER_COMPAT_PATCH"] = "1"
    environment["OMP_NUM_THREADS"] = "1"
    environment["OPENBLAS_NUM_THREADS"] = "1"
    environment["MKL_NUM_THREADS"] = "1"
    environment["NUMEXPR_NUM_THREADS"] = "1"
    environment["TF_NUM_INTRAOP_THREADS"] = "1"
    environment["TF_NUM_INTEROP_THREADS"] = "1"
    environment["XLA_FLAGS"] = (
        "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1"
    )
    started = time.time()
    log_path = run_dir / "pipeline.log"
    with log_path.open("ab", buffering=0) as stream:
        stream.write((
            f"### ZERO TASK RETRY {datetime.now(timezone.utc).isoformat()}\n"
            + " ".join(command)
            + "\n"
        ).encode())
        try:
            completed = subprocess.run(
                command,
                cwd=project_root.parent,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
            returncode = completed.returncode
        except Exception as error:
            stream.write(f"RUNNER_ERROR {type(error).__name__}: {error}\n".encode())
            returncode = 99
    accepted = _run_task_count(run_dir)
    passed = _validation_passed(run_dir, arguments.task_count)
    _release_baseline(environment_root)
    return {
        "environment_id": environment_id,
        "attempt": attempt,
        "run_dir": str(run_dir),
        "returncode": returncode,
        "accepted": accepted,
        "passed": passed,
        "reused_research": reused_research,
        "avoid_tools": avoid_tools,
        "elapsed_seconds": round(time.time() - started, 1),
        "status": "passed" if passed else "failed_generation",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, action="append", required=True)
    parser.add_argument("--workers", type=int, default=30)
    parser.add_argument(
        "--max-workers",
        type=int,
        help="逐级扩容的并发上限；省略时保持固定并发",
    )
    parser.add_argument(
        "--ramp-step",
        type=int,
        default=20,
        help="每次增加的恢复并发数",
    )
    parser.add_argument(
        "--ramp-interval-seconds",
        type=float,
        default=900.0,
        help="两次并发扩容之间的观察时间",
    )
    parser.add_argument(
        "--global-pipeline-limit",
        type=int,
        help="扩容时全机 task_gen.program.run_pipeline 进程上限",
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
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--environment-id",
        action="append",
        dest="environment_ids",
        help=(
            "只恢复指定环境；可重复传入。省略时仍从主批次状态中选择"
            "所有零任务失败环境"
        ),
    )
    parser.add_argument(
        "--environment-manifest",
        type=Path,
        help="包含 environment_ids 数组的冻结环境清单",
    )
    parser.add_argument(
        "--exclude-environment-id",
        action="append",
        dest="exclude_environment_ids",
        help="不在本队列启动的环境；可重复传入",
    )
    parser.add_argument(
        "--exclude-environment-manifest",
        type=Path,
        action="append",
        help="排除清单，每个文件需包含 environment_ids 数组",
    )
    parser.add_argument(
        "--exclude-active-environments",
        action="store_true",
        help="启动时排除已有 run_pipeline 正在处理的环境",
    )
    parser.add_argument(
        "--status-file-name",
        default="retry_zero_status.json",
        help="批次目录内的状态文件名，便于运行互不重叠的补充恢复队列",
    )
    parser.add_argument(
        "--lock-file-name",
        default="retry_zero_supervisor.lock",
        help="批次目录内的锁文件名，便于运行互不重叠的补充恢复队列",
    )
    return parser


def _batch_file(batch_root: Path, name: str, *, label: str) -> Path:
    """Resolve one supervisor metadata file without allowing path traversal."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{label} 必须是批次目录内的普通文件名")
    return batch_root / name


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.workers < 1:
        raise ValueError("--workers 必须至少为 1")
    max_workers = arguments.max_workers or arguments.workers
    if max_workers < arguments.workers:
        raise ValueError("--max-workers 不能小于 --workers")
    if arguments.ramp_step < 1:
        raise ValueError("--ramp-step 必须至少为 1")
    if arguments.ramp_interval_seconds <= 0:
        raise ValueError("--ramp-interval-seconds 必须大于 0")
    if arguments.global_pipeline_limit is not None and arguments.global_pipeline_limit < 1:
        raise ValueError("--global-pipeline-limit 必须至少为 1")
    if arguments.quality_timeout_seconds <= arguments.execution_timeout_seconds:
        raise ValueError("质检超时必须大于 Solution 执行超时")
    batch_root = arguments.batch_root.resolve()
    project_root = Path(__file__).resolve().parents[2]
    status_path = _batch_file(
        batch_root, arguments.status_file_name, label="--status-file-name",
    )
    lock_path = _batch_file(
        batch_root, arguments.lock_file_name, label="--lock-file-name",
    )
    lock_stream = lock_path.open("a+")
    try:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("已有零任务恢复监督器正在运行") from error

    bindings = _bindings([
        path.resolve()
        for path in (arguments.delivery_root)
    ])
    targets = []
    explicit_ids = set(arguments.environment_ids or [])
    excluded_ids = set(arguments.exclude_environment_ids or [])
    for excluded_manifest_path in arguments.exclude_environment_manifest or []:
        excluded_manifest = _read_json(excluded_manifest_path.resolve())
        excluded_manifest_ids = (
            excluded_manifest.get("environment_ids")
            if isinstance(excluded_manifest, dict)
            else None
        )
        if not isinstance(excluded_manifest_ids, list) or not all(
            isinstance(item, str) and item for item in excluded_manifest_ids
        ):
            raise ValueError(
                "--exclude-environment-manifest 必须包含非空字符串组成的 "
                "environment_ids 数组"
            )
        excluded_ids.update(excluded_manifest_ids)
    if arguments.exclude_active_environments:
        excluded_ids.update(_active_environment_ids(batch_root))
    if arguments.environment_manifest is not None:
        manifest = _read_json(arguments.environment_manifest.resolve())
        manifest_ids = manifest.get("environment_ids") if isinstance(manifest, dict) else None
        if not isinstance(manifest_ids, list) or not all(
            isinstance(item, str) and item for item in manifest_ids
        ):
            raise ValueError("--environment-manifest 必须包含非空字符串组成的 environment_ids 数组")
        explicit_ids.update(manifest_ids)
    if explicit_ids:
        unknown = sorted(explicit_ids - bindings.keys())
        if unknown:
            raise ValueError("指定环境没有可用 binding：" + ", ".join(unknown))
        for environment_id in sorted(explicit_ids - excluded_ids):
            # An explicit supplemental queue is assembled from a point-in-time
            # status snapshot. Recheck the durable output before starting so a
            # task accepted in the meantime is never regenerated concurrently.
            if not _environment_completed(
                batch_root / environment_id,
                arguments.task_count,
            ):
                targets.append((environment_id, bindings[environment_id]))
    else:
        primary_status = _read_json(batch_root / "batch_status_adaptive180.json")
        for environment_id, record in primary_status.get("records", {}).items():
            environment_root = batch_root / environment_id
            if record.get("status") != "failed_generation":
                continue
            if _task_count(environment_root) != 0:
                continue
            if environment_id not in bindings:
                continue
            # Do not repeat a completed recovery, but allow a new attempt when a
            # supervisor was interrupted before it could write Step 2 validation.
            recovery_validations = [
                path
                for path in environment_root.glob("attempt_*/step2_validation.json")
                if path.parent.name != "attempt_01"
            ]
            if recovery_validations:
                continue
            if environment_id not in excluded_ids:
                targets.append((environment_id, bindings[environment_id]))
    targets.sort()
    if arguments.limit is not None:
        targets = targets[: arguments.limit]

    records: dict[str, dict[str, Any]] = {}
    status_lock = threading.RLock()
    started_at = datetime.now(timezone.utc).isoformat()
    gate = _ConcurrencyGate(arguments.workers)
    stage_history = [{
        "workers": arguments.workers,
        "started_at": started_at,
    }]

    def publish() -> None:
        active_limit, active_runs = gate.snapshot()
        with status_lock:
            _write_json(status_path, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "started_at": started_at,
                "target_count": len(targets),
                "workers": active_limit,
                "initial_workers": arguments.workers,
                "max_workers": max_workers,
                "active_runs": active_runs,
                "completed": len(records),
                "passed": sum(item.get("passed") is True for item in records.values()),
                "accepted": sum(int(item.get("accepted", 0)) for item in records.values()),
                "stage_history": stage_history,
                "records": records,
            })

    def run_with_gate(environment_id: str, binding: Path) -> dict[str, Any]:
        gate.acquire()
        publish()
        try:
            return _run_one(
                environment_id,
                binding,
                batch_root=batch_root,
                project_root=project_root,
                arguments=arguments,
            )
        finally:
            gate.release()
            publish()

    ramp_stopped = threading.Event()

    def ramp_workers() -> None:
        while not ramp_stopped.wait(arguments.ramp_interval_seconds):
            current_limit, _ = gate.snapshot()
            if current_limit >= max_workers:
                return
            increment = arguments.ramp_step
            if arguments.global_pipeline_limit is not None:
                global_active = _active_pipeline_process_count()
                increment = min(
                    increment,
                    max(0, arguments.global_pipeline_limit - global_active),
                )
                if increment == 0:
                    print(
                        "zero-task recovery ramp held: "
                        f"global_active={global_active} "
                        f"global_limit={arguments.global_pipeline_limit}",
                        flush=True,
                    )
                    continue
            next_limit = min(current_limit + increment, max_workers)
            gate.set_limit(next_limit)
            with status_lock:
                stage_history.append({
                    "workers": next_limit,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "global_pipeline_processes": _active_pipeline_process_count(),
                })
            publish()
            print(f"zero-task recovery ramped to workers={next_limit}", flush=True)

    publish()
    print(
        f"zero-task recovery targets={len(targets)} workers={arguments.workers} "
        f"max_workers={max_workers}",
        flush=True,
    )
    ramp_thread = threading.Thread(
        target=ramp_workers,
        name="zero-retry-ramp",
        daemon=True,
    )
    ramp_thread.start()
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zero-retry") as pool:
        futures = {
            pool.submit(
                run_with_gate,
                environment_id,
                binding,
            ): environment_id
            for environment_id, binding in targets
        }
        for future in as_completed(futures):
            environment_id = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "environment_id": environment_id,
                    "status": "runner_error",
                    "passed": False,
                    "accepted": 0,
                    "error": f"{type(error).__name__}: {error}",
                }
            with status_lock:
                records[environment_id] = result
            publish()
            print(
                f"[{len(records)}/{len(targets)}] {environment_id}: "
                f"{result.get('status')} accepted={result.get('accepted', 0)}",
                flush=True,
            )
    ramp_stopped.set()
    ramp_thread.join(timeout=1)


if __name__ == "__main__":
    main()
