from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import sys
import threading
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from distill.taskgen_verifier import delete_task_state, verify_distilled_case
from task_gen.task_eval import load_cases


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verdict_result(case_name: str, verdict_path: Path) -> dict[str, Any]:
    verdict = read_json(verdict_path)
    return {
        "case": case_name,
        "status": "completed",
        "verification": {
            "status": "verified",
            "outcome": verdict["outcome"],
            "summary": verdict["summary"],
            "failure_causes": verdict["failure_causes"],
            "verdict": str(verdict_path),
        },
        "recovered": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--max-concurrency", type=int, default=25)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="delete interrupted cases absent from the saved summary so a resumed run retries them",
    )
    parser.add_argument(
        "--retry-tool-alias-startup-failures",
        action="store_true",
        help="retry cases falsely rejected when Kimi shortened multiple long MCP tool names",
    )
    args = parser.parse_args()

    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    completed_dirs: dict[str, tuple[Path, Path]] = {}
    partial_dirs: list[Path] = []

    for run in (path.resolve() for path in args.run):
        summary_path = run / "summary.json"
        if summary_path.is_file():
            for result in read_json(summary_path).get("results", []):
                if isinstance(result, dict) and isinstance(result.get("case"), str):
                    startup_failure = run / result["case"] / "raw/mcp_startup_failure.json"
                    if args.retry_tool_alias_startup_failures and startup_failure.is_file():
                        failure = read_json(startup_failure)
                        if failure.get("reason") == "mcp_tools_missing_from_first_model_request":
                            continue
                    results[result["case"]] = result
        for task_dir in sorted(
            path for path in run.iterdir()
            if path.is_dir() and path.name != "verifiers" and "__" in path.name
        ):
            recorded = task_dir.name in results
            run_result_path = task_dir / "raw/run_result.json"
            if not run_result_path.is_file():
                if not recorded:
                    partial_dirs.append(task_dir)
                continue
            run_result = read_json(run_result_path)
            if run_result.get("reason") != "completed" or not (task_dir / "trajectory.json").is_file():
                if args.retry_incomplete and not recorded:
                    partial_dirs.append(task_dir)
                else:
                    results.setdefault(task_dir.name, {
                        "case": task_dir.name,
                        "status": "failed",
                        "error": (
                            run_result.get("error")
                            or run_result.get("reason")
                            or "Kimi did not complete"
                        ),
                        "recovered": True,
                    })
                continue
            completed_dirs[task_dir.name] = (task_dir, run / "verifiers")

    # A verdict may have landed immediately before the parent batch was killed.
    for case_name, (task_dir, verifier_root) in completed_dirs.items():
        if case_name in results:
            continue
        verdict_path = verifier_root / case_name / "verdict.json"
        if verdict_path.is_file():
            results[case_name] = verdict_result(case_name, verdict_path)
            results[case_name]["deleted_state_paths"] = delete_task_state(task_dir)
            evidence = verdict_path.parent / "evidence"
            if evidence.is_dir():
                shutil.rmtree(evidence)

    pending = {
        case_name: value
        for case_name, value in completed_dirs.items()
        if case_name not in results
    }
    cases = load_cases(args.input_root, selected_case_names=set(pending)) if pending else []
    cases_by_name = {
        f"{case.source_run.name}__{case.task['task_id']}": case for case in cases
    }
    missing = sorted(set(pending) - set(cases_by_name))
    if missing:
        raise ValueError(f"completed cases not found in input collection: {missing[:5]}")

    lock = threading.Lock()

    def save() -> None:
        rows = sorted(results.values(), key=lambda item: item["case"])
        write_json(output / "summary.json", {
            "status": "running" if pending else "completed",
            "processed": len(rows),
            "pending_verification": len(pending),
            "partial_deleted": len(partial_dirs),
            "counts": {
                "completed": sum(item.get("status") == "completed" for item in rows),
                "failed": sum(item.get("status") != "completed" for item in rows),
                "pass": sum(item.get("verification", {}).get("outcome") == "pass" for item in rows),
                "fail": sum(item.get("verification", {}).get("outcome") == "fail" for item in rows),
                "verification_error": sum(
                    item.get("verification", {}).get("status") == "verification_error"
                    for item in rows
                ),
            },
            "results": rows,
        })

    save()

    def verify(case_name: str) -> dict[str, Any]:
        task_dir, verifier_root = pending[case_name]
        verification = verify_distilled_case(
            cases_by_name[case_name],
            case_name=case_name,
            task_dir=task_dir,
            verifier_root=verifier_root,
            codex_home=args.codex_home,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout_seconds=args.timeout,
            attempts=args.attempts,
        )
        result = {
            "case": case_name,
            "status": "completed",
            "trajectory": str(task_dir / "trajectory.json"),
            "verification": verification,
            "recovered": True,
        }
        result["deleted_state_paths"] = delete_task_state(task_dir)
        return result

    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        futures = {executor.submit(verify, name): name for name in pending}
        for future in as_completed(futures):
            case_name = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "case": case_name,
                    "status": "completed",
                    "verification": {
                        "status": "verification_error",
                        "outcome": None,
                        "error": f"{type(error).__name__}: {error}",
                    },
                    "recovered": True,
                }
            with lock:
                results[case_name] = result
                pending.pop(case_name)
                save()

    # Interrupted executions are not trajectories. Remove their copied state
    # and raw fragments so the resumed batch can recreate them from source.
    for task_dir in partial_dirs:
        verifier = task_dir.parent / "verifiers" / task_dir.name
        if verifier.is_dir():
            shutil.rmtree(verifier)
        shutil.rmtree(task_dir)

    pending.clear()
    save()


if __name__ == "__main__":
    main()
