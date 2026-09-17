"""Resumable batch runner for independent DataGen environment packages."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .software import software_download_environment


class BatchAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchConfig:
    source_root: Path
    workspace_root: Path
    output_root: Path
    workers: int = 4
    model: str = "gpt-5.6-sol"
    timeout_seconds: int = 1800
    draft_batch_size: int = 5
    max_repairs: int = 1
    software_repair_attempts: int = 3
    max_attempts: int = 2
    retry_delay_seconds: int = 30
    heartbeat_seconds: int = 10
    environments: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class ToolGenBatchRunner:
    def __init__(
        self,
        config: BatchConfig,
        *,
        command_builder: Callable[[Path], list[str]] | None = None,
    ) -> None:
        if config.workers < 1:
            raise ValueError("workers 必须至少为 1")
        if config.max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self.config = config
        self.workspace_root = config.workspace_root.resolve()
        self.environment_root = self.workspace_root / "environments"
        self.log_root = self.workspace_root / "logs"
        self.runtime_root = self.workspace_root / "runtime"
        self.status_path = self.log_root / "status.json"
        self.lock_path = self.workspace_root / ".toolgen-batch.lock"
        self.command_builder = command_builder or self._toolgen_command
        self.stop_event = threading.Event()
        self.status_lock = threading.RLock()
        self.process_lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.status: dict = {}
        self._lock_stream = None

    def run(self) -> int:
        sources = self._sources()
        self._prepare_directories()
        self._acquire_lock()
        previous_handlers = self._install_signal_handlers()
        try:
            self._prepare_status(sources)
            self._copy_sources(sources)
            pending = []
            for source in sources:
                if self._delivery_complete(source.name):
                    self._update_environment(
                        source.name, state="succeeded", resumed=True, finished_at=_now()
                    )
                else:
                    pending.append(source.name)

            with ThreadPoolExecutor(max_workers=self.config.workers) as executor:
                futures = {
                    executor.submit(self._safe_run_environment, name): name
                    for name in pending
                }
                for future in as_completed(futures):
                    future.result()

            with self.status_lock:
                states = {
                    item.get("state")
                    for item in self.status["environments"].values()
                }
                if self.stop_event.is_set():
                    self.status["state"] = "interrupted"
                elif "failed" in states:
                    self.status["state"] = "completed_with_failures"
                else:
                    self.status["state"] = "completed"
                self.status["finished_at"] = _now()
                self._write_status_locked()
            if self.stop_event.is_set():
                return 130
            return 1 if self.status["state"] == "completed_with_failures" else 0
        except Exception as error:
            if self.status:
                with self.status_lock:
                    self.status["state"] = "failed"
                    self.status["batch_error"] = f"{type(error).__name__}: {error}"
                    self.status["finished_at"] = _now()
                    self._write_status_locked()
            raise
        finally:
            self._terminate_all_processes()
            self._restore_signal_handlers(previous_handlers)
            self._release_lock()

    def _sources(self) -> list[Path]:
        source_root = self.config.source_root.resolve()
        if not source_root.is_dir():
            raise FileNotFoundError(f"找不到环境目录：{source_root}")
        available = {
            path.name: path
            for path in source_root.iterdir()
            if path.is_dir() and (path / "environment.json").is_file()
        }
        if self.config.environments:
            missing = sorted(set(self.config.environments) - set(available))
            if missing:
                raise ValueError("找不到指定环境：" + ", ".join(missing))
            names = self.config.environments
        else:
            names = tuple(sorted(available))
        if not names:
            raise ValueError(f"{source_root} 中没有 DataGen 环境包")
        return [available[name] for name in names]

    def _prepare_directories(self) -> None:
        for path in (
            self.workspace_root,
            self.environment_root,
            self.log_root,
            self.runtime_root,
            self.config.output_root.resolve(),
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _acquire_lock(self) -> None:
        self._lock_stream = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock_stream.close()
            self._lock_stream = None
            raise BatchAlreadyRunning(
                f"这个工作区已有 ToolGen 批次在运行：{self.workspace_root}"
            ) from error
        self._lock_stream.seek(0)
        self._lock_stream.truncate()
        self._lock_stream.write(str(os.getpid()) + "\n")
        self._lock_stream.flush()

    def _release_lock(self) -> None:
        if self._lock_stream is None:
            return
        fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
        self._lock_stream.close()
        self._lock_stream = None

    def _prepare_status(self, sources: list[Path]) -> None:
        previous = _read_json(self.status_path)
        previous_environments = previous.get("environments", {})
        if not isinstance(previous_environments, dict):
            previous_environments = {}
        entries = {}
        for source in sources:
            old = previous_environments.get(source.name, {})
            entry = dict(old) if isinstance(old, dict) else {}
            if entry.get("state") in {"running", "retrying", "stopping"}:
                entry.update(
                    state="pending",
                    resumed_after_interruption=True,
                    previous_run_stopped_at=_now(),
                )
            entry.update(
                source=str(source.resolve()),
                workspace=str((self.environment_root / source.name).resolve()),
            )
            entry.setdefault("state", "pending")
            entries[source.name] = entry
        self.status = {
            "state": "preparing",
            "model": self.config.model,
            "workers": self.config.workers,
            "source_root": str(self.config.source_root.resolve()),
            "workspace_root": str(self.workspace_root),
            "output_root": str(self.config.output_root.resolve()),
            "started_at": _now(),
            "updated_at": _now(),
            "environment_count": len(sources),
            "environments": entries,
        }
        with self.status_lock:
            self._write_status_locked()
            self.status["state"] = "running"
            self._write_status_locked()

    def _copy_sources(self, sources: list[Path]) -> None:
        for source in sources:
            target = self.environment_root / source.name
            if not target.exists():
                shutil.copytree(source, target)
            self._update_environment(source.name, copied=True)

    def _toolgen_command(self, package: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "env_gen.tool_gen",
            str(package),
            "--model",
            self.config.model,
            "--output-root",
            str(self.config.output_root.resolve()),
            "--timeout-seconds",
            str(self.config.timeout_seconds),
            "--draft-batch-size",
            str(self.config.draft_batch_size),
            "--max-repairs",
            str(self.config.max_repairs),
            "--software-repair-attempts",
            str(self.config.software_repair_attempts),
        ]

    def _subprocess_environment(self) -> dict[str, str]:
        environment = software_download_environment(shared_root=self.runtime_root)
        project_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, [project_root, environment.get("PYTHONPATH")])
        )
        environment["TOOLGEN_SHARED_SOFTWARE_ROOT"] = str(self.runtime_root)
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def _safe_run_environment(self, name: str) -> None:
        try:
            self._run_environment(name)
        except Exception as error:
            self._update_environment(
                name,
                state="interrupted" if self.stop_event.is_set() else "failed",
                finished_at=_now(),
                error=f"{type(error).__name__}: {error}",
            )

    def _run_environment(self, name: str) -> None:
        package = self.environment_root / name
        log_path = self.log_root / f"{name}.log"
        starting_attempt = int(
            self.status["environments"].get(name, {}).get("attempt", 0)
        )
        for run_attempt in range(1, self.config.max_attempts + 1):
            if self.stop_event.is_set():
                self._update_environment(name, state="interrupted", finished_at=_now())
                return
            attempt = starting_attempt + run_attempt
            command = self.command_builder(package)
            self._update_environment(
                name,
                state="running",
                attempt=attempt,
                started_at=_now(),
                log=str(log_path),
                command=command,
                error=None,
            )
            with log_path.open("a", encoding="utf-8") as output:
                output.write(
                    f"\n[{_now()}] attempt={attempt} "
                    + json.dumps(command, ensure_ascii=False)
                    + "\n"
                )
                output.flush()
                process = subprocess.Popen(
                    command,
                    cwd=Path(__file__).resolve().parents[2],
                    env=self._subprocess_environment(),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                with self.process_lock:
                    self.processes[name] = process
                try:
                    while process.poll() is None:
                        if self.stop_event.wait(self.config.heartbeat_seconds):
                            self._terminate_process(process)
                            break
                        self._update_environment(
                            name,
                            heartbeat_at=_now(),
                            progress=self._progress(package),
                        )
                    returncode = process.wait()
                finally:
                    with self.process_lock:
                        self.processes.pop(name, None)

            if self.stop_event.is_set():
                self._update_environment(
                    name, state="interrupted", returncode=returncode, finished_at=_now()
                )
                return
            if returncode == 0 and self._delivery_complete(name):
                self._update_environment(
                    name,
                    state="succeeded",
                    returncode=0,
                    finished_at=_now(),
                    progress=self._progress(package),
                )
                return
            error = (
                f"ToolGen exit={returncode}"
                if returncode
                else "ToolGen 正常退出，但没有形成完整交付"
            )
            log_excerpt = self._log_excerpt(log_path)
            if run_attempt < self.config.max_attempts:
                self._update_environment(
                    name,
                    state="retrying",
                    returncode=returncode,
                    error=error,
                    log_excerpt=log_excerpt,
                    retry_at=_now(),
                )
                if self.stop_event.wait(self.config.retry_delay_seconds):
                    self._update_environment(
                        name, state="interrupted", finished_at=_now()
                    )
                    return
                continue
            self._update_environment(
                name,
                state="failed",
                returncode=returncode,
                error=error,
                log_excerpt=log_excerpt,
                finished_at=_now(),
                progress=self._progress(package),
            )

    def _progress(self, package: Path) -> dict | None:
        value = _read_json(package / "tool_generation/progress.json")
        return value or None

    @staticmethod
    def _log_excerpt(path: Path, limit: int = 4000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def _delivery_complete(self, name: str) -> bool:
        root = self.config.output_root.resolve() / "environments" / name
        return all(
            path.is_file()
            for path in (
                root / "binding.json",
                root / "tools/tools.json",
                root / "environment/environment.json",
            )
        )

    def _update_environment(self, name: str, **values: object) -> None:
        with self.status_lock:
            self.status["environments"].setdefault(name, {}).update(values)
            self._write_status_locked()

    def _write_status_locked(self) -> None:
        counts: dict[str, int] = {}
        for entry in self.status.get("environments", {}).values():
            state = str(entry.get("state", "pending"))
            counts[state] = counts.get(state, 0) + 1
        self.status["summary"] = counts
        self.status["updated_at"] = _now()
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.status_path)

    def _install_signal_handlers(self) -> dict[int, object]:
        previous = {}
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.getsignal(number)
            signal.signal(number, self._handle_signal)
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: dict[int, object]) -> None:
        for number, handler in previous.items():
            signal.signal(number, handler)

    def _handle_signal(self, _number: int, _frame: object) -> None:
        self.stop_event.set()
        with self.status_lock:
            self.status["state"] = "stopping"
            self._write_status_locked()
        self._terminate_all_processes()

    def _terminate_all_processes(self) -> None:
        with self.process_lock:
            processes = list(self.processes.values())
        for process in processes:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量运行 ToolGen；支持并发、断点续跑和独立失败记录"
    )
    parser.add_argument("source_root", type=Path, help="包含多个 DataGen 环境包的目录")
    parser.add_argument("workspace_root", type=Path, help="本批次的工作目录")
    parser.add_argument("--output-root", type=Path, help="正式交付目录，默认是工作目录/delivery")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--draft-batch-size", type=int, default=5)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--software-repair-attempts", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-delay-seconds", type=int, default=30)
    parser.add_argument("--heartbeat-seconds", type=int, default=10)
    parser.add_argument(
        "--environment",
        action="append",
        default=[],
        help="只运行指定环境，可重复传入",
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    workspace = arguments.workspace_root.resolve()
    config = BatchConfig(
        source_root=arguments.source_root,
        workspace_root=workspace,
        output_root=(arguments.output_root or workspace / "delivery"),
        workers=arguments.workers,
        model=arguments.model,
        timeout_seconds=arguments.timeout_seconds,
        draft_batch_size=arguments.draft_batch_size,
        max_repairs=arguments.max_repairs,
        software_repair_attempts=arguments.software_repair_attempts,
        max_attempts=arguments.max_attempts,
        retry_delay_seconds=arguments.retry_delay_seconds,
        heartbeat_seconds=arguments.heartbeat_seconds,
        environments=tuple(arguments.environment),
    )
    try:
        returncode = ToolGenBatchRunner(config).run()
    except BatchAlreadyRunning as error:
        raise SystemExit(str(error)) from error
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
