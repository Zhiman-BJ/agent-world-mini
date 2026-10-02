from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import json
from pathlib import Path
import shutil
import sys
import threading
import traceback
from typing import Any


REPO = Path("/home/sunshuo/AgenticDataGeneration/agent-world-mini")
SOURCE_ROOT = Path(
    "/data1/home/tianfang/agent-world-mini-zhiman/runs/"
    "semiconductor_160_20260919_step3_514331a"
)
sys.path.insert(0, str(REPO))

from task_gen.task_eval_verifier import verify_execution


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def source_runs() -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for tasks_path in SOURCE_ROOT.glob("*/tasks.json"):
        environment_dir = tasks_path.parent
        indexed[environment_dir.name] = environment_dir
        run_path = environment_dir / "run.json"
        if run_path.is_file():
            try:
                run_dir = read_json(run_path).get("run_dir")
            except Exception:
                run_dir = None
            if isinstance(run_dir, str) and run_dir:
                indexed[Path(run_dir).name] = environment_dir
    return indexed


_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=16)
def _load_inputs(run_dir: Path) -> dict[str, Any]:
    tasks = {item["task_id"]: item for item in read_json(run_dir / "tasks.json")}
    bundle = read_json(run_dir / "intermediate/step_5_bundle.json")
    executions = {
        item["task_id"]: item.get("execution", {})
        for item in bundle.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("task_id"), str)
    }
    return {
        "tasks": tasks,
        "environment": bundle["environment"],
        "executions": executions,
    }


def load_inputs(run_dir: Path) -> dict[str, Any]:
    with _LOAD_LOCK:
        return _load_inputs(run_dir)


def resolve_run_dir(trajectory: dict[str, Any], runs: dict[str, Path]) -> Path:
    source_run = trajectory["task"]["source_run"]
    if source_run in runs:
        return runs[source_run]
    environment_id = trajectory["environment"]["environment_id"]
    run_dir = SOURCE_ROOT / environment_id
    if not (run_dir / "tasks.json").is_file():
        raise FileNotFoundError(f"source task bundle unavailable: {source_run}")
    return run_dir


def mutation_count(calls: list[dict[str, Any]], environment: dict[str, Any]) -> int:
    tools = {tool["name"]: tool for tool in environment.get("tools", [])}
    total = 0
    for call in calls:
        tool = tools.get(str(call.get("tool")))
        result = call.get("result")
        if (
            tool
            and tool.get("usageConditions", {}).get("sideEffects")
            and call.get("error") is None
            and isinstance(result, dict)
            and result.get("success") is True
        ):
            total += 1
    return total


def verify_one(
    row: dict[str, Any], runs: dict[str, Path], output_root: Path, timeout: int
) -> dict[str, Any]:
    case_name = row["case"]
    result_path = output_root / "results" / f"{case_name}.json"
    if result_path.is_file():
        existing = read_json(result_path)
        if existing.get("status") == "verified":
            return existing
    trajectory_path = Path(row["trajectory"])
    task_dir = trajectory_path.parent
    trajectory = read_json(trajectory_path)
    run_dir = resolve_run_dir(trajectory, runs)
    inputs = load_inputs(run_dir)
    task_id = trajectory["task"]["task_id"]
    task = inputs["tasks"][task_id]
    expected_text = trajectory["task"].get("task_text")
    if expected_text is not None and task.get("task_text") != expected_text:
        raise ValueError(f"task text mismatch: {case_name}")
    initial_state = (run_dir / task["initial_state"]).resolve()
    reference_relative = task.get("reference", {}).get("final_state")
    reference_state = (
        (run_dir / reference_relative).resolve()
        if isinstance(reference_relative, str) and reference_relative
        else None
    )
    execution = inputs["executions"].get(task_id, {})
    reference_calls = execution.get("tool_calls")
    if not isinstance(reference_calls, list):
        reference_calls = task.get("reference", {}).get("tool_calls", [])
    actual_state = task_dir / "execution-state"
    if not actual_state.is_dir():
        raise FileNotFoundError(f"actual execution-state missing: {actual_state}")
    raw_calls = task_dir / "raw/environment_tool_calls.jsonl"
    calls = [
        json.loads(line)
        for line in raw_calls.read_text(encoding="utf-8").splitlines()
        if line
    ]
    verifier_dir = output_root / "verifiers" / case_name
    if verifier_dir.exists():
        shutil.rmtree(verifier_dir)
    try:
        verdict = verify_execution(
            run_dir=verifier_dir,
            config={
                "codex_home": str(Path.home() / ".codex"),
                "model": None,
                "reasoning_effort": "high",
                "timeout_seconds": timeout,
                "attempts": 3,
            },
            task=task,
            environment=trajectory["environment"],
            initial_state=initial_state,
            actual_state=actual_state,
            calls=calls,
            answer=trajectory["outcome"]["final_answer"],
            execution={
                "error": None,
                "attempts": 1,
                "reconstructed_workspace": False,
                "call_source": "raw",
                "mutation_count": mutation_count(calls, trajectory["environment"]),
            },
            reference_state=reference_state,
            reference_calls=reference_calls,
            reference_answer=task.get("reference", {}).get("answer", ""),
        )
        result = {
            "case": case_name,
            "status": "verified",
            "outcome": verdict["outcome"],
            "summary": verdict["summary"],
            "verdict": str(verifier_dir / "verdict.json"),
        }
    except Exception as error:
        result = {
            "case": case_name,
            "status": "verification_error",
            "outcome": None,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        }
    evidence = verifier_dir / "evidence"
    if evidence.is_dir():
        shutil.rmtree(evidence)
    write_json(result_path, result)
    return result


def write_summary(output_root: Path, results: list[dict[str, Any]], total: int) -> None:
    counts: dict[str, int] = {}
    for result in results:
        key = result.get("outcome") or result["status"]
        counts[key] = counts.get(key, 0) + 1
    write_json(
        output_root / "summary.json",
        {
            "total": total,
            "processed": len(results),
            "pending": total - len(results),
            "counts": counts,
            "results": sorted(results, key=lambda item: item["case"]),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    rows_by_case: dict[str, dict[str, Any]] = {}
    for run_dir in args.run_dir:
        summary = read_json(run_dir / "summary.json")
        for row in summary["results"]:
            if row.get("status") == "completed":
                rows_by_case[row["case"]] = row
    rows = list(rows_by_case.values())
    args.output_root.mkdir(parents=True, exist_ok=True)
    runs = source_runs()
    results: list[dict[str, Any]] = []
    write_summary(args.output_root, results, len(rows))
    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        futures = {
            executor.submit(verify_one, row, runs, args.output_root, args.timeout): row["case"]
            for row in rows
        }
        for future in as_completed(futures):
            case_name = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "case": case_name,
                    "status": "setup_error",
                    "outcome": None,
                    "error": f"{type(error).__name__}: {error}",
                    "traceback": traceback.format_exc(),
                }
            results.append(result)
            write_summary(args.output_root, results, len(rows))
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
