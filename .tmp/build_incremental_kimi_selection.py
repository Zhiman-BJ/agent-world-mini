#!/usr/bin/env python3
"""Build a selection containing cases not yet dispatched by the Kimi runs."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from distill.__main__ import _case_runtime_backend
from task_gen.task_eval import load_cases


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def result_cases(path: Path) -> set[str]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return set()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return set()
    return {
        item["case"]
        for item in results
        if isinstance(item, dict) and isinstance(item.get("case"), str)
    }


def known_cases(history_root: Path) -> set[str]:
    known: set[str] = set()
    # Run summaries live at shallow, bounded depths. Avoid descending through
    # every retained raw trajectory and verifier evidence directory.
    for depth in range(1, 6):
        pattern = "/".join(["*"] * depth + ["summary.json"])
        for path in history_root.glob(pattern):
            known.update(result_cases(path))
    control = history_root / "control"
    for pattern in ("*.selection-summary.json", "*.dispatched-summary.json"):
        for path in control.glob(pattern):
            known.update(result_cases(path))
    return known


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-selection",
        type=Path,
        action="append",
        default=[],
        help="limit candidates to the ordered union of these selection summaries",
    )
    parser.add_argument(
        "--processed-summary",
        type=Path,
        action="append",
        default=[],
        help="exclude every case recorded in these summaries",
    )
    parser.add_argument(
        "--runtime-backend", action="append", default=["python_profile"]
    )
    arguments = parser.parse_args()

    input_root = arguments.input_root.expanduser().resolve()
    history_root = arguments.history_root.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    backends = set(arguments.runtime_backend)

    cases = load_cases(input_root)
    cases_by_name = {
        f"{case.source_run.name}__{case.task['task_id']}": case for case in cases
    }
    if arguments.include_selection:
        candidate_names: list[str] = []
        seen: set[str] = set()
        for path in arguments.include_selection:
            payload = load_json(path.expanduser().resolve())
            for item in payload.get("results", []):
                name = item.get("case") if isinstance(item, dict) else None
                if isinstance(name, str) and name not in seen:
                    candidate_names.append(name)
                    seen.add(name)
        known: set[str] = set()
        for path in arguments.processed_summary:
            known.update(result_cases(path.expanduser().resolve()))
    else:
        candidate_names = list(cases_by_name)
        known = known_cases(history_root)

    selected: list[dict[str, str]] = []
    selected_environments: set[str] = set()
    excluded_by_backend: dict[str, int] = {}
    missing_candidates: list[str] = []
    for case_name in candidate_names:
        if case_name in known:
            continue
        case = cases_by_name.get(case_name)
        if case is None:
            missing_candidates.append(case_name)
            continue
        backend = _case_runtime_backend(case)
        if backend not in backends:
            excluded_by_backend[backend] = excluded_by_backend.get(backend, 0) + 1
            continue
        selected.append({"case": case_name})
        selected_environments.add(case.source_run.name)

    manifest_path = input_root / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_manifest_generated_at": manifest.get("generated_at"),
        "source_environment_count": manifest.get("environment_count"),
        "source_task_count": len(cases),
        "known_case_count": len(known),
        "new_environment_count": len(selected_environments),
        "new_task_count": len(selected),
        "runtime_backends": sorted(backends),
        "excluded_by_backend": excluded_by_backend,
        "missing_candidate_count": len(missing_candidates),
        "missing_candidate_sample": missing_candidates[:20],
        "results": selected,
    }
    atomic_write_json(output, payload)
    print(json.dumps({key: payload[key] for key in (
        "source_environment_count",
        "source_task_count",
        "known_case_count",
        "new_environment_count",
        "new_task_count",
        "runtime_backends",
        "excluded_by_backend",
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
