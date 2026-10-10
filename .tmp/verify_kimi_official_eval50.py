from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from distill.runner import make_server_config
from task_gen.task_eval import load_cases
from task_gen.task_eval_mcp import bind_delivery
from task_gen.task_eval_verifier import verify_execution


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def copy_tree_links(source: Path, target: Path) -> None:
    shutil.copytree(source, target, copy_function=os.link)


def reconstruct_actual_state(case: Any, task_dir: Path, target: Path) -> None:
    copy_tree_links(case.initial_state, target)
    private = task_dir / "private-state"
    path_map = read_json(private / "path-map.json")
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
        copy_tree_links(source, destination)


def verifier_environment(case: Any) -> dict[str, Any]:
    config = bind_delivery(make_server_config(case.environment, runtime=case.runtime))
    environment = dict(case.environment)
    environment["tools"] = config["tools"]
    return environment


def mutation_count(calls: list[dict[str, Any]], environment: dict[str, Any]) -> int:
    tools = {tool["name"]: tool for tool in environment.get("tools", [])}
    return sum(
        1
        for call in calls
        if tools.get(str(call.get("tool")), {}).get("usageConditions", {}).get("sideEffects")
        and call.get("error") is None
        and isinstance(call.get("result"), dict)
        and call["result"].get("success") is True
    )


def verify_one(case: Any, row: dict[str, Any], output_root: Path, timeout: int) -> dict[str, Any]:
    case_name = row["case"]
    result_path = output_root / "results" / f"{case_name}.json"
    trajectory_path = Path(row["trajectory"])
    task_dir = trajectory_path.parent
    trajectory = read_json(trajectory_path)
    calls = [
        json.loads(line)
        for line in (task_dir / "raw/environment_tool_calls.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    environment = verifier_environment(case)
    verifier_dir = output_root / "verifiers" / case_name
    if verifier_dir.exists():
        shutil.rmtree(verifier_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="actual-state-", dir=output_root) as temporary:
            actual_state = Path(temporary) / "state"
            reconstruct_actual_state(case, task_dir, actual_state)
            verdict = verify_execution(
                run_dir=verifier_dir,
                config={
                    "codex_home": str(Path.home() / ".codex"),
                    "model": "gpt-6.1-sol",
                    "reasoning_effort": "high",
                    "timeout_seconds": timeout,
                    "attempts": 3,
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
                    "mutation_count": mutation_count(calls, environment),
                },
                reference_state=case.reference_state,
                reference_calls=case.reference_calls,
                reference_answer=case.task.get("reference", {}).get("answer", ""),
            )
        result = {
            "case": case_name,
            "status": "verified",
            "outcome": verdict["outcome"],
            "summary": verdict["summary"],
            "failure_causes": verdict["failure_causes"],
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
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-concurrency", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--skip-summary", type=Path, action="append", default=[])
    parser.add_argument("--exclude-case", action="append", default=[])
    args = parser.parse_args()

    run_summary = read_json(args.run_dir / "summary.json")
    rows = [row for row in run_summary["results"] if row.get("status") == "completed"]
    skipped = {
        result["case"]
        for summary_path in args.skip_summary
        for result in read_json(summary_path).get("results", [])
        if result.get("status") == "verified"
    }
    excluded = set(args.exclude_case)
    rows = [row for row in rows if row["case"] not in skipped | excluded]
    selected = {row["case"] for row in rows}
    cases = load_cases(args.input_root, selected_case_names=selected)
    cases_by_name = {
        f"{case.source_run.name}__{case.task['task_id']}": case for case in cases
    }
    if set(cases_by_name) != selected:
        missing = sorted(selected - set(cases_by_name))
        raise ValueError(f"selected cases not found: {missing[:5]}")

    args.output_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    write_summary(args.output_root, results, len(rows))
    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        futures = {
            executor.submit(
                verify_one,
                cases_by_name[row["case"]],
                row,
                args.output_root,
                args.timeout,
            ): row["case"]
            for row in rows
        }
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "case": futures[future],
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
