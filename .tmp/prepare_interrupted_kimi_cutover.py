#!/usr/bin/env python3
"""Snapshot terminal and verifier-pending cases, and remove interrupted state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    run = arguments.run.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    summary = load_json(run / "summary.json")
    results = {
        item["case"]: item
        for item in summary.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("case"), str)
    }
    pending: list[str] = []
    deleted: list[str] = []

    for task_dir in sorted(
        path for path in run.iterdir()
        if path.is_dir() and path.name != "verifiers" and "__" in path.name
    ):
        if task_dir.name in results:
            continue
        run_result_path = task_dir / "raw/run_result.json"
        completed = False
        if run_result_path.is_file() and (task_dir / "trajectory.json").is_file():
            try:
                completed = load_json(run_result_path).get("reason") == "completed"
            except (OSError, json.JSONDecodeError):
                completed = False
        if completed:
            pending.append(task_dir.name)
            results[task_dir.name] = {
                "case": task_dir.name,
                "status": "completed",
                "trajectory": str(task_dir / "trajectory.json"),
                "verification": {"status": "pending_recovery", "outcome": None},
                "cutover_snapshot": True,
            }
            continue

        verifier_dir = run / "verifiers" / task_dir.name
        if verifier_dir.is_dir():
            shutil.rmtree(verifier_dir)
        shutil.rmtree(task_dir)
        deleted.append(task_dir.name)

    rows = sorted(results.values(), key=lambda item: item["case"])
    write_json(output / "summary.json", {
        "status": "prepared",
        "processed": len(rows),
        "pending_verification": len(pending),
        "partial_deleted": len(deleted),
        "pending_cases": pending,
        "partial_deleted_cases": deleted,
        "results": rows,
    })
    print(json.dumps({
        "processed": len(rows),
        "pending_verification": len(pending),
        "partial_deleted": len(deleted),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
