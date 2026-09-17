"""Fault-tolerant batch runner for independent DataGen Seeds."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Callable
from uuid import uuid4

from env_gen.data_gen.analysis.seed import load_selected_seed
from env_gen.data_gen.config import (
    DEFAULT_ENVIRONMENT_SCHEMA,
    DEFAULT_OSS_OUTPUT_ROOT,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
    DEFAULT_SEED_VALIDATION_SCHEMA,
    DataGenConfig,
)
from env_gen.data_gen.run_pipeline import DataGenerationResult, run_pipeline
from env_gen.data_gen.steps.step1_research_scenario import ScenarioResearchError
from env_gen.data_gen.steps.step2_collect_data import DataCollectionError
from env_gen.data_gen.steps.step3_integrate_data import IntegrationError
from env_gen.data_gen.steps.step4_freeze_environment import EnvironmentFreezeError
from utils.search_agent.codex import is_retryable_error


PipelineRunner = Callable[[DataGenConfig], DataGenerationResult]


@dataclass(frozen=True)
class BatchJob:
    index: int
    global_id: str
    seed_path: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return normalized or "seed"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _error_detail(error: BaseException) -> str:
    details = [f"{type(error).__name__}: {error}"]
    details.extend(str(note) for note in getattr(error, "__notes__", ()))
    return "\n".join(details)[-6000:]


def _failed_stage(error: BaseException) -> str:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ScenarioResearchError):
            return "step1_research"
        if isinstance(current, DataCollectionError):
            return "step2_collection"
        if isinstance(current, IntegrationError):
            return "step3_integration"
        if isinstance(current, EnvironmentFreezeError):
            return "step4_freeze"
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    message = str(error).lower()
    for marker, stage in (
        ("step 1", "step1_research"),
        ("step 2", "step2_collection"),
        ("step 3", "step3_integration"),
        ("step 4", "step4_freeze"),
    ):
        if marker in message:
            return stage
    return "step0_prepare"


def _existing_output(output_root: Path, global_id: str) -> Path | None:
    safe_id = _safe_name(global_id)
    for tier in ("rich", "partial", "not_rich"):
        candidate = output_root / tier / safe_id
        if candidate.exists():
            return candidate
    return None


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(item["status"] for item in results)
    return {
        "total": len(results),
        "pending": counts["pending"],
        "completed": counts["completed"],
        "skipped_existing": counts["skipped_existing"],
        "invalid": counts["invalid"],
        "failed": counts["failed"],
    }


def _prepare_jobs(
    *,
    seed_path: Path,
    selected_ids: set[str] | None,
    validation_schema_path: Path,
    input_dir: Path,
) -> tuple[list[BatchJob], list[dict[str, Any]]]:
    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Seed 文件无法读取：{seed_path}: {error}") from error
    if not isinstance(payload, list):
        raise ValueError("Seed 文件根节点必须是数组")
    if not payload:
        raise ValueError("Seed 文件至少需要包含一条 Seed")

    raw_ids = [
        item.get("global_id") if isinstance(item, dict) else None
        for item in payload
    ]
    counts = Counter(value for value in raw_ids if isinstance(value, str))
    available = set(counts)
    jobs: list[BatchJob] = []
    results: list[dict[str, Any]] = []
    input_dir.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(payload):
        global_id = raw_ids[index]
        if selected_ids is not None and global_id not in selected_ids:
            continue
        label = global_id if isinstance(global_id, str) and global_id else f"index_{index}"
        if not isinstance(global_id, str) or not global_id:
            results.append({
                "index": index,
                "global_id": label,
                "status": "invalid",
                "stage": "preflight",
                "attempts": [],
                "error": "Seed 缺少非空 global_id",
            })
            continue
        if counts[global_id] > 1:
            results.append({
                "index": index,
                "global_id": global_id,
                "status": "invalid",
                "stage": "preflight",
                "attempts": [],
                "error": "Seed 文件中存在重复 global_id",
            })
            continue
        isolated_path = input_dir / f"{index:04d}_{_safe_name(global_id)}.json"
        _write_json(isolated_path, [item])
        try:
            load_selected_seed(isolated_path, global_id, validation_schema_path)
        except Exception as error:
            results.append({
                "index": index,
                "global_id": global_id,
                "status": "invalid",
                "stage": "preflight",
                "attempts": [],
                "error": _error_detail(error),
            })
            continue
        jobs.append(BatchJob(index=index, global_id=global_id, seed_path=isolated_path))

    if selected_ids is not None:
        for global_id in sorted(selected_ids - available):
            results.append({
                "index": len(payload),
                "global_id": global_id,
                "status": "invalid",
                "stage": "preflight",
                "attempts": [],
                "error": "指定的 global_id 不在 Seed 文件中",
            })
    return jobs, results


def _run_job(
    job: BatchJob,
    *,
    output_root: Path,
    schema_path: Path,
    validation_schema_path: Path,
    contract_path: Path | None,
    model: str | None,
    reasoning_effort: str,
    timeout_seconds: int,
    max_repair_rounds: int,
    enable_web_search: bool,
    allow_partial_integration: bool,
    overwrite: bool,
    max_attempts: int,
    retry_delay_seconds: float,
    pipeline_runner: PipelineRunner,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    started_at = _now()
    existing = _existing_output(output_root, job.global_id)
    if existing is not None and not overwrite:
        return {
            "index": job.index,
            "global_id": job.global_id,
            "status": "skipped_existing",
            "stage": "complete",
            "attempts": [],
            "output_dir": str(existing),
            "started_at": started_at,
            "finished_at": _now(),
        }

    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        attempt_started = _now()
        try:
            config = DataGenConfig(
                seed_path=job.seed_path,
                global_id=job.global_id,
                schema_path=schema_path,
                seed_validation_schema_path=validation_schema_path,
                contract_path=contract_path,
                output_root=output_root,
                overwrite=overwrite,
                model=model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=timeout_seconds,
                max_repair_rounds=max_repair_rounds,
                enable_web_search=enable_web_search,
                allow_partial_integration=allow_partial_integration,
            )
            generated = pipeline_runner(config)
        except Exception as error:
            retryable = is_retryable_error(error)
            attempts.append({
                "attempt": attempt,
                "started_at": attempt_started,
                "finished_at": _now(),
                "status": "failed",
                "stage": _failed_stage(error),
                "retryable": retryable,
                "error": _error_detail(error),
            })
            if retryable and attempt < max_attempts:
                if retry_delay_seconds:
                    sleeper(retry_delay_seconds)
                continue
            return {
                "index": job.index,
                "global_id": job.global_id,
                "status": "failed",
                "stage": attempts[-1]["stage"],
                "attempts": attempts,
                "retry_exhausted": retryable and attempt >= max_attempts,
                "error": attempts[-1]["error"],
                "started_at": started_at,
                "finished_at": _now(),
            }
        attempts.append({
            "attempt": attempt,
            "started_at": attempt_started,
            "finished_at": _now(),
            "status": "completed",
        })
        return {
            "index": job.index,
            "global_id": job.global_id,
            "status": "completed",
            "stage": "complete",
            "attempts": attempts,
            "output_dir": str(generated.output_dir),
            "quality_tier": generated.quality_tier,
            "integration_tier": generated.integration_tier,
            "elapsed_seconds": round(generated.elapsed_seconds, 3),
            "started_at": started_at,
            "finished_at": _now(),
        }
    raise AssertionError("max_attempts validation failed")


def run_batch(
    *,
    seed_path: Path,
    output_root: Path,
    global_ids: list[str] | None = None,
    schema_path: Path = DEFAULT_ENVIRONMENT_SCHEMA,
    validation_schema_path: Path = DEFAULT_SEED_VALIDATION_SCHEMA,
    contract_path: Path | None = None,
    model: str | None = DEFAULT_RESEARCH_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout_seconds: int = 4200,
    max_repair_rounds: int = 2,
    enable_web_search: bool = True,
    allow_partial_integration: bool = False,
    overwrite: bool = False,
    concurrency: int = 1,
    max_attempts: int = 2,
    retry_delay_seconds: float = 10.0,
    pipeline_runner: PipelineRunner = run_pipeline,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run independent Seeds without letting one failure terminate the batch."""

    if concurrency < 1:
        raise ValueError("concurrency 必须至少为 1")
    if max_attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds 不能小于 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if max_repair_rounds < 0:
        raise ValueError("max_repair_rounds 不能小于 0")

    seed_path = seed_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    schema_path = schema_path.expanduser().resolve()
    validation_schema_path = validation_schema_path.expanduser().resolve()
    contract_path = contract_path.expanduser().resolve() if contract_path else None
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    batch_dir = output_root / "batch_runs" / batch_id
    report_path = batch_dir / "report.json"
    batch_dir.mkdir(parents=True, exist_ok=False)

    jobs, results = _prepare_jobs(
        seed_path=seed_path,
        selected_ids=set(global_ids) if global_ids else None,
        validation_schema_path=validation_schema_path,
        input_dir=batch_dir / "inputs",
    )
    pending = [{
        "index": job.index,
        "global_id": job.global_id,
        "status": "pending",
        "stage": "pending",
        "attempts": [],
    } for job in jobs]
    initial_results = sorted(
        [*results, *pending],
        key=lambda item: (item["index"], item["global_id"]),
    )
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "seed_path": str(seed_path),
        "output_root": str(output_root),
        "started_at": _now(),
        "finished_at": None,
        "settings": {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "timeout_seconds": timeout_seconds,
            "concurrency": concurrency,
            "max_attempts": max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
            "overwrite": overwrite,
            "allow_partial_integration": allow_partial_integration,
        },
        "summary": _summary(initial_results),
        "results": initial_results,
    }
    _write_json(report_path, report)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_jobs = {
            executor.submit(
                _run_job,
                job,
                output_root=output_root,
                schema_path=schema_path,
                validation_schema_path=validation_schema_path,
                contract_path=contract_path,
                model=model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=timeout_seconds,
                max_repair_rounds=max_repair_rounds,
                enable_web_search=enable_web_search,
                allow_partial_integration=allow_partial_integration,
                overwrite=overwrite,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
                pipeline_runner=pipeline_runner,
                sleeper=sleeper,
            ): job
            for job in jobs
        }
        for future in as_completed(future_jobs):
            job = future_jobs[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "index": job.index,
                    "global_id": job.global_id,
                    "status": "failed",
                    "stage": _failed_stage(error),
                    "attempts": [],
                    "retry_exhausted": False,
                    "error": _error_detail(error),
                    "started_at": report["started_at"],
                    "finished_at": _now(),
                }
            report["results"] = [
                item
                for item in report["results"]
                if not (
                    item["index"] == job.index
                    and item["global_id"] == job.global_id
                )
            ]
            report["results"].append(result)
            report["results"].sort(key=lambda item: (item["index"], item["global_id"]))
            report["summary"] = _summary(report["results"])
            _write_json(report_path, report)
            print(
                f"[{result['status']}] {result['global_id']}"
                + (f" -> {result['output_dir']}" if result.get("output_dir") else ""),
                flush=True,
            )

    report["summary"] = _summary(report["results"])
    report["finished_at"] = _now()
    report["report_path"] = str(report_path)
    _write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐条校验并容错执行一个 Seed 数组文件中的 DataGen 流水线"
    )
    parser.add_argument("--seed-path", type=Path, required=True)
    parser.add_argument("--global-id", action="append", dest="global_ids")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OSS_OUTPUT_ROOT)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_ENVIRONMENT_SCHEMA)
    parser.add_argument(
        "--seed-validation-schema-path",
        type=Path,
        default=DEFAULT_SEED_VALIDATION_SCHEMA,
    )
    parser.add_argument("--contract-path", type=Path)
    parser.add_argument("--model", default=DEFAULT_RESEARCH_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default=DEFAULT_REASONING_EFFORT,
    )
    parser.add_argument("--timeout-seconds", type=int, default=4200)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-delay-seconds", type=float, default=10.0)
    parser.add_argument(
        "--enable-web-search",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--allow-partial-integration", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    report = run_batch(
        seed_path=arguments.seed_path,
        output_root=arguments.output_root,
        global_ids=arguments.global_ids,
        schema_path=arguments.schema_path,
        validation_schema_path=arguments.seed_validation_schema_path,
        contract_path=arguments.contract_path,
        model=arguments.model,
        reasoning_effort=arguments.reasoning_effort,
        timeout_seconds=arguments.timeout_seconds,
        max_repair_rounds=arguments.max_repair_rounds,
        enable_web_search=arguments.enable_web_search,
        allow_partial_integration=arguments.allow_partial_integration,
        overwrite=arguments.overwrite,
        concurrency=arguments.concurrency,
        max_attempts=arguments.max_attempts,
        retry_delay_seconds=arguments.retry_delay_seconds,
    )
    print(json.dumps({
        "report_path": report["report_path"],
        "summary": report["summary"],
    }, ensure_ascii=False, indent=2))
    if report["summary"]["invalid"] or report["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
