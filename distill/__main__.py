"""Command line entry points for Kimi K3 trajectory distillation."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from .export import export_trajectory, validate_trajectory
from .environment_errors import environment_errors as _environment_errors, tool_timeouts
from .runner import (
    KIMI_K3_CONTEXT_SIZE,
    KIMI_K3_DEFAULT_EFFORT,
    KIMI_K3_EFFORTS,
    DistillRunError,
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


def _bounded_results(
    cases: list[Any], run: Any, *, max_workers: int, max_environment_errors: int | None,
    on_progress: Any | None = None,
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
            while stop_reason is None and next_index < len(cases) and len(pending) < max_workers:
                pending[executor.submit(run, cases[next_index])] = next_index
                next_index += 1

        submit()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: pending[item]):
                index = pending.pop(future)
                result = future.result()
                results[index] = result
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
    from task_gen.task_eval import _agent_prompt, load_cases

    input_root = arguments.input_root.expanduser().resolve()
    cases = load_cases(input_root)
    if not cases:
        for child in sorted(path for path in input_root.iterdir() if path.is_dir()):
            cases.extend(load_cases(child))
    if arguments.environment_id:
        cases = [case for case in cases if case.task.get("environment_id") == arguments.environment_id]
    if arguments.task_id:
        cases = [case for case in cases if case.task.get("task_id") == arguments.task_id]
    if arguments.select_summary:
        selected_names = _select_summary_case_names(arguments.select_summary)
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
    selected_count = len(cases)
    completed_names = _completed_case_names(arguments.skip_summary)
    if completed_names:
        cases = [
            case for case in cases
            if _safe_name(f"{case.source_run.name}__{case.task['task_id']}") not in completed_names
        ]
    skipped_completed_count = selected_count - len(cases)
    if arguments.limit is not None:
        cases = cases[:arguments.limit]
    if not cases:
        raise ValueError("no task-generation cases found")
    run_dir = arguments.output_root.expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True)

    def run(case) -> dict[str, Any]:
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
                _agent_prompt(case, arguments.max_tool_calls),
                case.initial_state,
                server_config,
                output,
                task={
                    "task_id": case.task.get("task_id"),
                    "task_text": case.task.get("task_text"),
                    "source_run": case.source_run.name,
                },
                **_common(arguments),
            )
        except Exception as error:
            result = {
                "case": case_name,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "trajectory": str(output / "trajectory.json") if output.exists() else None,
            }
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
        stop_path = output / "raw/environment_stop.json"
        if stop_path.is_file():
            result["status"] = "environment_stopped"
            result["environment_stop"] = _read_json(stop_path)
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
            "not_started_count": len(cases) - started_count,
            "environment_error_count": environment_error_count,
            "tool_timeout_count": sum(item.get("tool_timeout_count", 0) for item in results),
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
        help="K3 context size used by Kimi CLI; defaults to the official 1048576-token window",
    )
    parser.add_argument("--model-id", default="kimi-k3")
    parser.add_argument("--model-alias", default="evaluation")
    parser.add_argument(
        "--provider-type",
        choices=("kimi",),
        default="kimi",
        help="official Kimi Chat Completions provider",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=KIMI_K3_EFFORTS,
        default=os.environ.get("KIMI_REASONING_EFFORT", KIMI_K3_DEFAULT_EFFORT),
        help="K3 reasoning effort; defaults to high for distillation runs",
    )
    parser.add_argument(
        "--max-environment-errors-per-task",
        type=int,
        help="stop only the affected task after this many execution-layer environment errors",
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
    batch.add_argument("--limit", type=int)
    batch.add_argument("--max-concurrency", type=int, default=1)
    batch.add_argument(
        "--max-environment-errors",
        type=int,
        help="stop dispatching new cases after this many execution-layer tool errors",
    )
    batch.add_argument("--max-tool-calls", type=int, default=100)
    batch.add_argument("--tool-timeout", type=int, default=300)
    batch.add_argument("--tool-memory-limit", type=int, default=128 * 1024 * 1024 * 1024)
    batch.add_argument("--tool-write-limit", type=int, default=256 * 1024 * 1024)
    batch.add_argument("--tool-process-limit", type=int, default=1024)
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
        "limit", "max_concurrency", "max_environment_errors", "max_environment_errors_per_task",
        "max_tool_calls", "tool_timeout", "tool_memory_limit",
        "tool_write_limit", "tool_process_limit",
    ):
        value = getattr(arguments, name, None)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
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
