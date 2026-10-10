"""Command line entry points for Kimi K3 trajectory distillation."""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import re
import shutil
import threading
from typing import Any

from .export import export_trajectory, validate_trajectory
from .environment_errors import environment_errors as _environment_errors, tool_timeouts
from .relay import _stream_error
from .runner import (
    KIMI_K3_CONTEXT_SIZE,
    KIMI_K3_DEFAULT_EFFORT,
    KIMI_K3_EFFORTS,
    KIMI_DEFAULT_MAX_TOOL_CALLS,
    KIMI_MAX_TOOL_CALLS,
    KIMI_UPSTREAM_MAX_RETRIES,
    PROVIDER_TYPES,
    DistillRunError,
    EnvironmentErrorCircuitBreaker,
    make_server_config,
    run_k3_distillation,
)
from .visualize import render_combined_visualization, render_run_visualizations, render_trajectory_html


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _prompt(arguments: argparse.Namespace) -> str:
    if arguments.prompt is not None:
        return arguments.prompt
    return arguments.prompt_file.expanduser().read_text(encoding="utf-8")


def _common(arguments: argparse.Namespace) -> dict[str, Any]:
    api_key = os.environ.get(arguments.api_key_env)
    if not api_key:
        raise ValueError(f"API key environment variable is empty: {arguments.api_key_env}")
    return {
        "kimi_bin": arguments.kimi_bin,
        "base_url": arguments.base_url,
        "api_key": api_key,
        "max_context_size": arguments.max_context_size,
        "model_id": arguments.model_id,
        "model_alias": arguments.model_alias,
        "provider_type": arguments.provider_type,
        "reasoning_effort": arguments.reasoning_effort,
        "max_upstream_retries": arguments.max_upstream_retries,
        "max_environment_errors": arguments.max_environment_errors_per_task,
    }


def _run_one(arguments: argparse.Namespace) -> None:
    trajectory = run_k3_distillation(
        _prompt(arguments),
        arguments.initial_state,
        arguments.server_config,
        arguments.output,
        task=_read_json(arguments.task_metadata) if arguments.task_metadata else None,
        evaluation=_read_json(arguments.evaluation) if arguments.evaluation else None,
        **_common(arguments),
    )
    print(arguments.output.expanduser().resolve() / "trajectory.json")
    print(f"steps={len(trajectory['steps'])} compactions={len(trajectory['compactions'])}")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "case"


def _distillation_prompt(case: Any) -> str:
    """Submit the task body without its duplicated published workspace brief."""
    task_text = case.task.get("task_text")
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be non-empty")
    prompt = task_text.strip()
    summary = case.environment.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return prompt
    first_paragraph, separator, remainder = prompt.partition("\n\n")
    if separator and first_paragraph == summary.strip() and remainder.strip():
        return remainder.strip()
    return prompt


@lru_cache(maxsize=None)
def _source_run_runtime_backend(source_run: Path) -> str:
    bundle_path = source_run / "intermediate/step_5_bundle.json"
    bundle = _read_json(bundle_path)
    declared = bundle.get("runtime")
    if not isinstance(declared, dict):
        delivery = bundle.get("toolgen_delivery")
        declared = delivery.get("runtime") if isinstance(delivery, dict) else None
    if isinstance(declared, dict):
        backend = declared.get("backend")
        if isinstance(backend, str) and backend:
            return backend
    return "host_python"


def _case_runtime_backend(case: Any) -> str:
    """Return the declared ToolGen runtime without starting or validating it."""
    runtime = case.runtime if isinstance(case.runtime, dict) else {}
    backend = runtime.get("runtime_backend")
    if isinstance(backend, str) and backend:
        return backend
    delivery_runtime = runtime.get("delivery_runtime")
    if isinstance(delivery_runtime, dict):
        backend = delivery_runtime.get("backend")
        if isinstance(backend, str) and backend:
            return backend
    return _source_run_runtime_backend(case.source_run.resolve())


def _is_environment_setup_error(error: Exception) -> bool:
    text = str(error).casefold()
    return any(marker in text for marker in (
        "不符合 schema",
        "invalid tool schema",
        "mcp json schema",
        "任务交付缺少 tools_repaired.json",
        "delivery binding",
        "first model request omitted environment mcp tools",
    ))


def _model_transport_stats(path: Path) -> tuple[int, int, int]:
    requests = retries = 0
    final_error_ids: set[int | str] = set()
    if not path.is_file():
        return requests, 0, retries
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        requests += 1
        status = row.get("status", 200)
        if row.get("origin") != "local_harness" and (
            row.get("error") or isinstance(status, int) and status >= 400
        ):
            final_error_ids.add(row.get("request_id", f"line-{requests}"))
        attempts = row.get("attempts")
        if isinstance(attempts, list):
            retries += len(attempts)
    model_io = path.parent / "model_io"
    if model_io.is_dir():
        for response in model_io.glob("*.response.sse"):
            if _stream_error(response) is None:
                continue
            try:
                request_id: int | str = int(response.name.split(".", 1)[0])
            except ValueError:
                request_id = response.name
            final_error_ids.add(request_id)
    return requests, len(final_error_ids), retries


def _available_memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _io_full_pressure_avg10() -> float | None:
    """Return the ten-second full I/O PSI percentage when the kernel exposes it."""
    try:
        for line in Path("/proc/pressure/io").read_text(encoding="utf-8").splitlines():
            if not line.startswith("full "):
                continue
            values = dict(field.split("=", 1) for field in line.split()[1:])
            return float(values["avg10"])
    except (OSError, ValueError, KeyError):
        return None
    return None


def _is_task_local_upstream_timeout(result: dict[str, Any]) -> bool:
    """Return whether a terminal upstream failure is a per-task TokenHub timeout."""
    stop = result.get("upstream_stop")
    if not isinstance(stop, dict):
        return False
    error = stop.get("stream_error")
    if not isinstance(error, dict):
        return False
    return (
        str(error.get("code") or "") == "504001"
        or (
            error.get("type") == "timeout_error"
            and error.get("source") == "upstream"
        )
    )


class _AdaptiveConcurrencyController:
    """Adjust new dispatch without terminating already-running task workers."""

    def __init__(self, maximum: int, minimum: int, initial: int | None = None):
        if not 1 <= minimum <= maximum:
            raise ValueError("adaptive minimum concurrency must be between 1 and maximum")
        if initial is None:
            initial = maximum
        if not minimum <= initial <= maximum:
            raise ValueError("adaptive initial concurrency must be between minimum and maximum")
        self.maximum = maximum
        self.minimum = minimum
        self.current = initial
        self._recent: deque[dict[str, Any]] = deque(maxlen=20)
        self._stable_since_adjustment = 0
        self.history: list[dict[str, Any]] = []

    def observe(self, result: dict[str, Any]) -> None:
        self._recent.append(result)
        self._stable_since_adjustment += 1
        # A final TokenHub 504 is terminal for that task after its request-level
        # retry budget is exhausted. It is not evidence that local batch
        # concurrency is unhealthy, so do not throttle unrelated tasks for it.
        transport_errors = sum(
            item.get("upstream_final_error_count", 0)
            for item in self._recent
            if not _is_task_local_upstream_timeout(item)
        )
        environment_errors = sum(item.get("environment_error_count", 0) for item in self._recent)
        startup_errors = sum(item.get("mcp_startup_failure_count", 0) for item in self._recent)
        verifier_errors = sum(
            item.get("verification", {}).get("status") == "verification_error"
            for item in self._recent
        )
        cpu_count = os.cpu_count() or 1
        load = os.getloadavg()[0]
        available = _available_memory_bytes()
        io_pressure = _io_full_pressure_avg10()
        reason = None
        io_pressure_reason = False
        if transport_errors >= 3:
            reason = f"{transport_errors} final upstream errors in recent tasks"
        elif startup_errors >= 3:
            reason = f"{startup_errors} MCP startup failures in recent tasks"
        elif environment_errors >= 3:
            reason = f"{environment_errors} resource/environment errors in recent tasks"
        elif verifier_errors >= 3:
            reason = f"{verifier_errors} verifier infrastructure errors in recent tasks"
        elif load >= cpu_count * 0.9:
            reason = f"one-minute load {load:.1f} >= 90% of {cpu_count} CPUs"
        elif available is not None and available < 128 * 1024 ** 3:
            reason = f"available memory {available // (1024 ** 3)} GiB < 128 GiB"
        elif io_pressure is not None and io_pressure >= 25.0:
            reason = f"full I/O pressure avg10 {io_pressure:.1f}% >= 25.0%"
            io_pressure_reason = True
        if reason is not None:
            self._stable_since_adjustment = 0
            if self.current > self.minimum:
                previous = self.current
                self.current = max(
                    self.minimum,
                    self.current - 25 if io_pressure_reason else self.current // 2,
                )
                self._record("decrease", previous, reason)
            return
        stable = transport_errors == startup_errors == environment_errors == verifier_errors == 0
        if (
            stable
            and self.current < self.maximum
            and self._stable_since_adjustment >= 50
            and load < cpu_count * 0.7
            and (available is None or available >= 256 * 1024 ** 3)
            and (io_pressure is None or io_pressure < 10.0)
        ):
            previous = self.current
            recovery_step = min(25, max(5, self.current // 2))
            self.current = min(self.maximum, self.current + recovery_step)
            self._stable_since_adjustment = 0
            self._record("increase", previous, "50 stable completions and healthy resources")

    def _record(self, action: str, previous: int, reason: str) -> None:
        self.history.append({
            "at": datetime.now().astimezone().isoformat(),
            "action": action,
            "from": previous,
            "to": self.current,
            "reason": reason,
        })


def _completed_case_names(paths: list[Path]) -> set[str]:
    names = set()
    for path in paths:
        summary = _read_json(path)
        results = summary.get("results")
        if not isinstance(results, list):
            raise ValueError(f"summary.results must be an array: {path}")
        names.update(
            item["case"] for item in results
            if isinstance(item, dict)
            and item.get("status") == "completed"
            and isinstance(item.get("case"), str)
        )
    return names


def _processed_case_names(paths: list[Path]) -> set[str]:
    names = set()
    for path in paths:
        summary = _read_json(path)
        results = summary.get("results")
        if not isinstance(results, list):
            raise ValueError(f"summary.results must be an array: {path}")
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("case"), str):
                raise ValueError(f"every processed summary result needs a case name: {path}")
            names.add(item["case"])
    return names


def _defer_state_cleanup(result: dict[str, Any], verify_after_each: bool) -> bool:
    return (
        result.get("status") == "completed"
        and verify_after_each
        and result.get("verification", {}).get("status") != "verified"
    )


def _selected_case_names(paths: list[Path]) -> set[str]:
    names = set()
    for path in paths:
        selection = _read_json(path)
        cases = selection.get("cases")
        if not isinstance(cases, list):
            raise ValueError(f"selection.cases must be an array: {path}")
        for item in cases:
            if not isinstance(item, dict) or not isinstance(item.get("case_name"), str):
                raise ValueError(f"every selection case needs a case_name: {path}")
            names.add(item["case_name"])
    return names


def _select_summary_case_names(path: Path) -> list[str]:
    summary = _read_json(path)
    results = summary.get("results")
    if not isinstance(results, list):
        raise ValueError(f"summary.results must be an array: {path}")
    names = [
        item.get("case") for item in results
        if isinstance(item, dict) and isinstance(item.get("case"), str)
    ]
    if len(names) != len(results):
        raise ValueError(f"every summary result must have a case name: {path}")
    if len(set(names)) != len(names):
        raise ValueError(f"summary contains duplicate case names: {path}")
    return names


def _override_case_deliveries(cases: list[Any], delivery_root: Path) -> list[Any]:
    """Bind selected task cases to repaired deliveries without changing task state or text."""
    from harness.delivery import load_delivery

    root = delivery_root.expanduser().resolve()
    # Delivery directory names identify packages, while binding.json may expose
    # the environment/tool identity used by task metadata.  They are not
    # required to be identical (for example, a scenario package can provide a
    # shared stem-analysis environment), so index both forms before loading.
    bindings_by_environment: dict[str, Path] = {}
    for candidate in sorted((root / "environments").glob("*/binding.json")):
        try:
            metadata = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid delivery binding: {candidate}") from exc
        aliases = {candidate.parent.name}
        if isinstance(metadata, dict) and isinstance(metadata.get("environment_id"), str):
            aliases.add(metadata["environment_id"])
        for alias in aliases:
            previous = bindings_by_environment.get(alias)
            if previous is not None and previous != candidate:
                raise ValueError(f"duplicate delivery binding for environment {alias!r}")
            bindings_by_environment[alias] = candidate
    overridden = []
    cache: dict[str, Any] = {}
    for case in cases:
        environment_id = case.task.get("environment_id")
        if not isinstance(environment_id, str) or not environment_id:
            raise ValueError("task is missing environment_id")
        binding = bindings_by_environment.get(environment_id)
        if binding is None:
            expected = root / "environments" / environment_id / "binding.json"
            raise ValueError(f"delivery override is missing binding: {expected}")
        delivery = cache.setdefault(environment_id, load_delivery(binding))
        if delivery.package.environment.get("environment_id") != environment_id:
            raise ValueError(f"delivery override environment mismatch: {binding}")
        environment = dict(delivery.package.environment)
        environment["tools"] = list(delivery.package.tools)
        runtime = {
            "binding_path": str(delivery.binding_path),
            "initial_state": str(delivery.package.package_root / "state"),
        }
        if delivery.software_root is not None and delivery.python_path is not None:
            runtime["software"] = {
                "root": str(delivery.software_root),
                "python": str(delivery.python_path),
            }
        overridden.append(replace(case, environment=environment, runtime=runtime))
    return overridden


def _bounded_results(
    cases: list[Any], run: Any, *, max_workers: int, max_environment_errors: int | None,
    on_progress: Any | None = None, stop_event: Any | None = None,
    concurrency_controller: _AdaptiveConcurrencyController | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Run at most max_workers cases at once and stop dispatching at the error threshold."""
    results: dict[int, dict[str, Any]] = {}
    next_index = 0
    environment_error_count = 0
    stop_reason = None
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {}

        def submit() -> None:
            nonlocal next_index
            while (
                stop_reason is None
                and not (stop_event is not None and stop_event.is_set())
                and next_index < len(cases)
                and len(pending) < (
                    concurrency_controller.current if concurrency_controller else max_workers
                )
            ):
                pending[executor.submit(run, cases[next_index])] = next_index
                next_index += 1

        submit()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            if stop_reason is None and stop_event is not None and stop_event.is_set():
                stop_reason = "environment_error_limit_reached: live circuit breaker tripped"
            for future in sorted(done, key=lambda item: pending[item]):
                index = pending.pop(future)
                result = future.result()
                results[index] = result
                if concurrency_controller is not None:
                    concurrency_controller.observe(result)
                environment_error_count += int(result.get("environment_error_count", 0))
                if (
                    stop_reason is None
                    and max_environment_errors is not None
                    and environment_error_count >= max_environment_errors
                ):
                    stop_reason = (
                        f"environment_error_limit_reached: {environment_error_count} >= "
                        f"{max_environment_errors}"
                    )
            submit()
            if on_progress is not None:
                on_progress(
                    [results[index] for index in sorted(results)],
                    next_index,
                    len(pending),
                    environment_error_count,
                    stop_reason,
                )
    return [results[index] for index in sorted(results)], environment_error_count, stop_reason


def _run_taskgen(arguments: argparse.Namespace) -> None:
    from task_gen.task_eval import load_cases

    input_root = arguments.input_root.expanduser().resolve()
    selected_names = (
        _select_summary_case_names(arguments.select_summary)
        if arguments.select_summary
        else None
    )
    selected_case_names = set(selected_names) if selected_names is not None else None
    cases = load_cases(input_root, selected_case_names=selected_case_names)
    if not cases:
        for child in sorted(path for path in input_root.iterdir() if path.is_dir()):
            cases.extend(load_cases(child, selected_case_names=selected_case_names))
    if arguments.environment_id:
        cases = [case for case in cases if case.task.get("environment_id") == arguments.environment_id]
    if arguments.task_id:
        cases = [case for case in cases if case.task.get("task_id") == arguments.task_id]
    if arguments.select_summary:
        cases_by_name = {
            _safe_name(f"{case.source_run.name}__{case.task['task_id']}"): case
            for case in cases
        }
        missing = [name for name in selected_names if name not in cases_by_name]
        if missing:
            preview = ", ".join(missing[:5])
            suffix = " ..." if len(missing) > 5 else ""
            raise ValueError(f"selection summary cases not found: {preview}{suffix}")
        cases = [cases_by_name[name] for name in selected_names]
    if arguments.delivery_root:
        cases = _override_case_deliveries(cases, arguments.delivery_root)
    if arguments.runtime_backend:
        selected_backends = set(arguments.runtime_backend)
        cases = [case for case in cases if _case_runtime_backend(case) in selected_backends]
    population_size = len(cases)
    if arguments.sample_size is not None:
        if arguments.sample_size > population_size:
            raise ValueError(
                f"sample size {arguments.sample_size} exceeds population {population_size}"
            )
        cases = random.Random(arguments.sample_seed).sample(cases, arguments.sample_size)
    selected_count = len(cases)
    skipped_names = _completed_case_names(arguments.skip_summary)
    skipped_names.update(_processed_case_names(arguments.skip_processed_summary))
    skipped_names.update(_selected_case_names(arguments.skip_selection))
    if skipped_names:
        cases = [
            case for case in cases
            if _safe_name(f"{case.source_run.name}__{case.task['task_id']}") not in skipped_names
        ]
    skipped_completed_count = selected_count - len(cases)
    if arguments.limit is not None:
        cases = cases[:arguments.limit]
    if not cases:
        raise ValueError("no task-generation cases found")
    run_dir = arguments.output_root.expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True)
    verifier_root = run_dir / "verifiers"
    verifier_slots = threading.BoundedSemaphore(arguments.verifier_concurrency)
    concurrency_controller = (
        _AdaptiveConcurrencyController(
            arguments.max_concurrency,
            arguments.min_concurrency,
            arguments.initial_concurrency,
        )
        if arguments.adaptive_concurrency else None
    )
    environment_error_circuit_breaker = (
        EnvironmentErrorCircuitBreaker(arguments.max_environment_errors)
        if arguments.max_environment_errors is not None else None
    )
    (run_dir / "selection.json").write_text(
        json.dumps(
            {
                "input_root": str(input_root),
                "population_size": population_size,
                "sample_size": arguments.sample_size,
                "sample_seed": arguments.sample_seed if arguments.sample_size is not None else None,
                "runtime_backends": arguments.runtime_backend,
                "selected_count": selected_count,
                "cases": [
                    {
                        "case_name": _safe_name(
                            f"{case.source_run.name}__{case.task['task_id']}"
                        ),
                        "source_run": str(case.source_run),
                        "task_id": case.task["task_id"],
                        "environment_id": case.task.get("environment_id"),
                    }
                    for case in cases
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    def run(case) -> dict[str, Any]:
        from .taskgen_verifier import delete_task_state, verify_distilled_case

        case_name = _safe_name(f"{case.source_run.name}__{case.task['task_id']}")
        output = run_dir / case_name
        server_config = make_server_config(
            case.environment,
            max_tool_calls=arguments.max_tool_calls,
            timeout=arguments.tool_timeout,
            memory_limit=arguments.tool_memory_limit,
            write_limit=arguments.tool_write_limit,
            process_limit=arguments.tool_process_limit,
            runtime=case.runtime,
        )
        try:
            trajectory = run_k3_distillation(
                _distillation_prompt(case),
                case.initial_state,
                server_config,
                output,
                task={
                    "task_id": case.task.get("task_id"),
                    "task_text": case.task.get("task_text"),
                    "source_run": case.source_run.name,
                },
                environment_error_circuit_breaker=environment_error_circuit_breaker,
                **_common(arguments),
            )
        except Exception as error:
            environment_setup_error = _is_environment_setup_error(error)
            result = {
                "case": case_name,
                "status": "environment_stopped" if environment_setup_error else "failed",
                "error": f"{type(error).__name__}: {error}",
                "trajectory": str(output / "trajectory.json") if output.exists() else None,
            }
            if environment_setup_error:
                result["environment_setup_error"] = True
        else:
            result = {
                "case": case_name,
                "status": "completed",
                "trajectory": str(output / "trajectory.json"),
                "steps": len(trajectory["steps"]),
                "compactions": len(trajectory["compactions"]),
            }
        environment_errors = _environment_errors(output / "raw/environment_tool_calls.jsonl")
        result["environment_error_count"] = len(environment_errors)
        if environment_errors:
            result["environment_errors"] = environment_errors
        timeouts = tool_timeouts(output / "raw/environment_tool_calls.jsonl")
        result["tool_timeout_count"] = len(timeouts)
        if timeouts:
            result["tool_timeouts"] = timeouts
            if result["status"] == "completed":
                result["status"] = "failed"
                result["error"] = "tool execution timed out; task is not retried"
        requests, final_errors, retries = _model_transport_stats(
            output / "raw/model_responses.jsonl"
        )
        result["model_request_count"] = requests
        result["upstream_final_error_count"] = final_errors
        result["upstream_retry_count"] = retries
        upstream_stop_path = output / "raw/upstream_stop.json"
        result["upstream_stop_count"] = int(upstream_stop_path.is_file())
        if upstream_stop_path.is_file():
            result["upstream_stop"] = _read_json(upstream_stop_path)
        startup_failure_path = output / "raw/mcp_startup_failure.json"
        result["mcp_startup_failure_count"] = int(startup_failure_path.is_file())
        if startup_failure_path.is_file():
            result["status"] = "environment_stopped"
            result["environment_setup_error"] = True
            result["mcp_startup_failure"] = _read_json(startup_failure_path)
        stop_path = output / "raw/environment_stop.json"
        if stop_path.is_file():
            result["status"] = "environment_stopped"
            result["environment_stop"] = _read_json(stop_path)
        if result["status"] == "completed" and arguments.verify_after_each:
            with verifier_slots:
                result["verification"] = verify_distilled_case(
                    case,
                    case_name=case_name,
                    task_dir=output,
                    verifier_root=verifier_root,
                    codex_home=arguments.verifier_codex_home,
                    model=arguments.verifier_model,
                    reasoning_effort=arguments.verifier_reasoning_effort,
                    timeout_seconds=arguments.verifier_timeout,
                    attempts=arguments.verifier_attempts,
                )
        verification_failed_to_run = _defer_state_cleanup(
            result, arguments.verify_after_each
        )
        if arguments.delete_task_state and output.exists() and not verification_failed_to_run:
            try:
                result["deleted_state_paths"] = delete_task_state(output)
            except Exception as error:
                result["state_cleanup_error"] = f"{type(error).__name__}: {error}"
        elif verification_failed_to_run:
            result["state_cleanup_deferred"] = "verification_did_not_produce_a_verdict"
        return result

    def write_summary(
        results: list[dict[str, Any]], started_count: int, in_progress_count: int,
        environment_error_count: int, stop_reason: str | None,
    ) -> dict[str, Any]:
        summary = {
            "run_directory": str(run_dir),
            "selected_count": selected_count,
            "skipped_completed_count": skipped_completed_count,
            "remaining_count": len(cases),
            "started_count": started_count,
            "in_progress_count": in_progress_count,
            "case_count": len(results),
            "completed_count": sum(item["status"] == "completed" for item in results),
            "failed_count": sum(item["status"] == "failed" for item in results),
            "environment_stopped_count": sum(
                item["status"] == "environment_stopped" for item in results
            ),
            "environment_setup_error_count": sum(
                item.get("environment_setup_error") is True for item in results
            ),
            "not_started_count": len(cases) - started_count,
            "environment_error_count": environment_error_count,
            "tool_timeout_count": sum(item.get("tool_timeout_count", 0) for item in results),
            "mcp_startup_failure_count": sum(
                item.get("mcp_startup_failure_count", 0) for item in results
            ),
            "upstream_stop_count": sum(
                item.get("upstream_stop_count", 0) for item in results
            ),
            "verified_count": sum(
                item.get("verification", {}).get("status") == "verified" for item in results
            ),
            "verifier_passed_count": sum(
                item.get("verification", {}).get("outcome") == "pass" for item in results
            ),
            "verifier_failed_count": sum(
                item.get("verification", {}).get("outcome") == "fail" for item in results
            ),
            "verification_error_count": sum(
                item.get("verification", {}).get("status") == "verification_error"
                for item in results
            ),
            "state_cleanup_error_count": sum(
                "state_cleanup_error" in item for item in results
            ),
            "adaptive_concurrency": arguments.adaptive_concurrency,
            "concurrency_target": (
                concurrency_controller.current
                if concurrency_controller is not None else arguments.max_concurrency
            ),
            "minimum_concurrency": arguments.min_concurrency,
            "initial_concurrency": (
                arguments.initial_concurrency
                if arguments.initial_concurrency is not None
                else arguments.max_concurrency
            ),
            "maximum_concurrency": arguments.max_concurrency,
            "runtime_backends": arguments.runtime_backend,
            "concurrency_adjustments": (
                concurrency_controller.history if concurrency_controller is not None else []
            ),
            "max_environment_errors": arguments.max_environment_errors,
            "stopped_early": stop_reason is not None,
            "stop_reason": stop_reason,
            "results": results,
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary

    write_summary([], 0, 0, 0, None)
    results, environment_error_count, stop_reason = _bounded_results(
        cases,
        run,
        max_workers=arguments.max_concurrency,
        max_environment_errors=arguments.max_environment_errors,
        on_progress=write_summary,
        stop_event=(
            environment_error_circuit_breaker.event
            if environment_error_circuit_breaker is not None else None
        ),
        concurrency_controller=concurrency_controller,
    )
    summary = write_summary(
        results,
        len(results),
        0,
        environment_error_count,
        stop_reason,
    )
    print(run_dir)
    if summary["failed_count"] or summary["stopped_early"]:
        raise SystemExit(1)


def _export(arguments: argparse.Namespace) -> None:
    trajectory = export_trajectory(
        arguments.raw,
        arguments.output,
        task=_read_json(arguments.task),
        environment=_read_json(arguments.environment),
        evaluation=_read_json(arguments.evaluation) if arguments.evaluation else None,
        trajectory_id=arguments.trajectory_id,
    )
    validate_trajectory(trajectory)
    print(arguments.output.expanduser().resolve())


def _visualize(arguments: argparse.Namespace) -> None:
    if arguments.run_directory:
        renderer = render_combined_visualization if arguments.combined else render_run_visualizations
        print(renderer(arguments.run_directory, arguments.output, arguments.limit))
        return
    if arguments.combined:
        raise ValueError("--combined requires --run-directory")
    output = arguments.output or arguments.trajectory.with_suffix(".html")
    print(render_trajectory_html(arguments.trajectory, output))


def _add_kimi_options(parser: argparse.ArgumentParser) -> None:
    default_bin = os.environ.get("KIMI_CODE_BIN") or shutil.which("kimi") or "kimi"
    parser.add_argument("--kimi-bin", type=Path, default=Path(default_bin))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KIMI_BASE_URL"),
        help="upstream OpenAI-compatible base URL; defaults to KIMI_BASE_URL",
    )
    parser.add_argument("--api-key-env", default="KIMI_API_KEY")
    parser.add_argument(
        "--max-context-size",
        type=int,
        default=int(os.environ.get("KIMI_MAX_CONTEXT_SIZE", KIMI_K3_CONTEXT_SIZE)),
        help=(
            "K3 context cap used by Kimi CLI; 262144 and 1048576 are supported, "
            "defaulting to the official 1048576-token window"
        ),
    )
    parser.add_argument("--model-id", default="kimi-k3")
    parser.add_argument("--model-alias", default="evaluation")
    parser.add_argument(
        "--provider-type",
        choices=tuple(sorted(PROVIDER_TYPES)),
        default="kimi",
        help="Kimi Code provider used for the upstream model API",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=KIMI_K3_EFFORTS,
        default=os.environ.get("KIMI_REASONING_EFFORT", KIMI_K3_DEFAULT_EFFORT),
        help="K3 reasoning effort; defaults to max for distillation runs",
    )
    parser.add_argument(
        "--max-environment-errors-per-task",
        type=int,
        default=1,
        help="stop only the affected task after this many execution-layer environment errors",
    )
    parser.add_argument(
        "--max-upstream-retries",
        type=int,
        default=int(os.environ.get("KIMI_MAX_UPSTREAM_RETRIES", KIMI_UPSTREAM_MAX_RETRIES)),
        help="retry transient Kimi/MaaS responses per model request; internal MaaS 400 is narrowly classified",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect standard Kimi K3 tool-use trajectories")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one prepared environment task")
    prompt = run.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    run.add_argument("--initial-state", type=Path, required=True)
    run.add_argument("--server-config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--task-metadata", type=Path)
    run.add_argument("--evaluation", type=Path)
    _add_kimi_options(run)
    run.set_defaults(handler=_run_one)

    batch = commands.add_parser("taskgen", help="distill task_gen bundles; failures do not stop later cases")
    batch.add_argument("--input-root", type=Path, required=True)
    batch.add_argument("--output-root", type=Path, default=Path("distill/.runs"))
    batch.add_argument("--environment-id")
    batch.add_argument("--task-id")
    batch.add_argument(
        "--runtime-backend",
        action="append",
        choices=("host_python", "python_profile", "docker"),
        help=(
            "only run cases using this ToolGen runtime backend; may be repeated "
            "to include multiple backends"
        ),
    )
    batch.add_argument(
        "--delivery-root",
        type=Path,
        help=(
            "override selected cases with binding.json files from "
            "<root>/environments/<environment_id>; task text and task initial state are unchanged"
        ),
    )
    batch.add_argument(
        "--select-summary",
        type=Path,
        help="run only cases listed in this summary.json, preserving its order",
    )
    batch.add_argument(
        "--skip-summary",
        type=Path,
        action="append",
        default=[],
        help="skip cases marked completed in a prior summary.json; may be repeated",
    )
    batch.add_argument(
        "--skip-processed-summary",
        type=Path,
        action="append",
        default=[],
        help="skip every case recorded in a prior summary.json; may be repeated",
    )
    batch.add_argument(
        "--skip-selection",
        type=Path,
        action="append",
        default=[],
        help="skip every dispatched case recorded in a prior selection.json; may be repeated",
    )
    batch.add_argument("--limit", type=int)
    batch.add_argument("--sample-size", type=int)
    batch.add_argument("--sample-seed", type=int, default=20261005)
    batch.add_argument("--max-concurrency", type=int, default=1)
    batch.add_argument(
        "--adaptive-concurrency",
        action="store_true",
        help="dynamically reduce or restore new task dispatch based on recent failures and host pressure",
    )
    batch.add_argument(
        "--min-concurrency",
        type=int,
        default=25,
        help="minimum new-task dispatch target when adaptive concurrency is enabled",
    )
    batch.add_argument(
        "--initial-concurrency",
        type=int,
        help=(
            "initial new-task dispatch target when adaptive concurrency is enabled; "
            "defaults to max-concurrency"
        ),
    )
    batch.add_argument(
        "--max-environment-errors",
        type=int,
        default=None,
        help=(
            "optionally stop the whole batch after this many execution-layer tool errors; "
            "disabled by default so one bad environment does not stop unrelated tasks"
        ),
    )
    batch.add_argument(
        "--max-tool-calls",
        type=int,
        default=KIMI_DEFAULT_MAX_TOOL_CALLS,
        help=(
            f"per-task tool-call budget (default: {KIMI_DEFAULT_MAX_TOOL_CALLS}; "
            f"hard limit: {KIMI_MAX_TOOL_CALLS})"
        ),
    )
    batch.add_argument("--tool-timeout", type=int, default=300)
    batch.add_argument("--tool-memory-limit", type=int, default=128 * 1024 * 1024 * 1024)
    batch.add_argument("--tool-write-limit", type=int, default=256 * 1024 * 1024)
    batch.add_argument(
        "--tool-process-limit",
        type=int,
        default=1024,
        help=(
            "legacy isolated-tool process limit; direct task-workspace execution "
            "does not apply UID-wide RLIMIT_NPROC"
        ),
    )
    batch.add_argument(
        "--verify-after-each",
        action="store_true",
        help="run the independent verifier immediately after each completed distillation",
    )
    batch.add_argument(
        "--delete-task-state",
        action="store_true",
        help="delete each model workspace and private state after processing",
    )
    batch.add_argument("--verifier-concurrency", type=int, default=25)
    batch.add_argument("--verifier-codex-home", type=Path, default=Path.home() / ".codex")
    batch.add_argument("--verifier-model", default="gpt-6.1-sol")
    batch.add_argument("--verifier-reasoning-effort", default="high")
    batch.add_argument("--verifier-timeout", type=int, default=1800)
    batch.add_argument("--verifier-attempts", type=int, default=3)
    _add_kimi_options(batch)
    batch.set_defaults(handler=_run_taskgen)

    export = commands.add_parser("export", help="rebuild trajectory.json from retained raw evidence")
    export.add_argument("--raw", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--task", type=Path, required=True)
    export.add_argument("--environment", type=Path, required=True)
    export.add_argument("--evaluation", type=Path)
    export.add_argument("--trajectory-id")
    export.set_defaults(handler=_export)

    visualize = commands.add_parser("visualize", help="generate a self-contained trajectory HTML")
    visualize_input = visualize.add_mutually_exclusive_group(required=True)
    visualize_input.add_argument("--trajectory", type=Path)
    visualize_input.add_argument("--run-directory", type=Path)
    visualize.add_argument("--output", type=Path)
    visualize.add_argument("--limit", type=int)
    visualize.add_argument("--combined", action="store_true")
    visualize.set_defaults(handler=_visualize)
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    for name in (
        "limit", "sample_size", "max_concurrency", "min_concurrency", "max_environment_errors", "max_environment_errors_per_task",
        "max_tool_calls", "tool_timeout", "tool_memory_limit",
        "tool_write_limit", "tool_process_limit", "verifier_concurrency",
        "verifier_timeout", "verifier_attempts",
    ):
        value = getattr(arguments, name, None)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if getattr(arguments, "max_tool_calls", None) is not None and arguments.max_tool_calls > KIMI_MAX_TOOL_CALLS:
        parser.error(f"--max-tool-calls cannot exceed {KIMI_MAX_TOOL_CALLS}")
    if (
        getattr(arguments, "adaptive_concurrency", False)
        and arguments.min_concurrency > arguments.max_concurrency
    ):
        parser.error("--min-concurrency cannot exceed --max-concurrency")
    if getattr(arguments, "adaptive_concurrency", False) and (
        arguments.initial_concurrency is not None
        and not arguments.min_concurrency
        <= arguments.initial_concurrency
        <= arguments.max_concurrency
    ):
        parser.error(
            "--initial-concurrency must be between --min-concurrency and --max-concurrency"
        )
    if getattr(arguments, "max_upstream_retries", 0) < 0:
        parser.error("--max-upstream-retries must be non-negative")
    try:
        if hasattr(arguments, "base_url") and not arguments.base_url:
            raise ValueError("set --base-url or KIMI_BASE_URL")
        if hasattr(arguments, "max_context_size") and not arguments.max_context_size:
            raise ValueError("set --max-context-size or KIMI_MAX_CONTEXT_SIZE to the upstream K3 limit")
        arguments.handler(arguments)
    except (ValueError, DistillRunError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
