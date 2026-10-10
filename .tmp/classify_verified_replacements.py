from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any


VERIFY_ROOT = Path(
    "/data1/agent_world/aw-data/k3_mismatch_replacements_verifier_20261003"
)
SOURCE_SUMMARIES = [
    Path(
        "/data1/agent_world/aw-data/"
        "k3_rerun_pass_mismatch_256k_max_50x_20260929_v2/"
        "runs/20260929_222151_669841/summary.json"
    ),
    Path(
        "/data1/agent_world/aw-data/"
        "k3_rerun_pass_mismatch_256k_max_50x_20260929_v2/"
        "resume_20260930/runs/20260930_005159_889162/summary.json"
    ),
]
CLASSIFIED_ROOT = VERIFY_ROOT / "classified"
SAMPLE_ROOT = VERIFY_ROOT / "pass_sample_10_visualization"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def source_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for summary_path in SOURCE_SUMMARIES:
        for row in read_json(summary_path)["results"]:
            if row.get("status") == "completed":
                rows[row["case"]] = row
    return rows


def tool_call_count(trajectory: dict[str, Any]) -> int:
    return sum(
        len(step.get("tool_calls", []))
        for step in trajectory.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("tool_calls", []), list)
    )


def classify() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = source_rows()
    rows: list[dict[str, Any]] = []
    for result_path in sorted((VERIFY_ROOT / "results").glob("*.json")):
        result = read_json(result_path)
        case = result["case"]
        source = sources.get(case)
        if source is None:
            raise ValueError(f"source trajectory missing for {case}")
        trajectory_path = Path(source["trajectory"])
        trajectory = read_json(trajectory_path)
        rows.append(
            {
                "case": case,
                "outcome": result["outcome"],
                "verifier_summary": result["summary"],
                "trajectory": str(trajectory_path),
                "verdict": result["verdict"],
                "environment_id": trajectory.get("environment", {}).get(
                    "environment_id"
                ),
                "task_id": trajectory.get("task", {}).get("task_id"),
                "task_text": trajectory.get("task", {}).get("task_text", ""),
                "steps": len(trajectory.get("steps", [])),
                "tool_calls": tool_call_count(trajectory),
                "compactions": len(trajectory.get("compactions", [])),
                "environment_error_count": source.get("environment_error_count", 0),
                "tool_timeout_count": source.get("tool_timeout_count", 0),
            }
        )

    if len(rows) != 280:
        raise ValueError(f"expected 280 verifier results, found {len(rows)}")
    passed = [row for row in rows if row["outcome"] == "pass"]
    failed = [row for row in rows if row["outcome"] == "fail"]
    if len(passed) != 212 or len(failed) != 68:
        raise ValueError(
            f"unexpected result counts: pass={len(passed)}, fail={len(failed)}"
        )
    return passed, failed


def replace_link(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target)


def write_classification(
    passed: list[dict[str, Any]], failed: list[dict[str, Any]]
) -> None:
    CLASSIFIED_ROOT.mkdir(parents=True, exist_ok=True)
    for outcome, rows in (("pass", passed), ("fail", failed)):
        link_root = CLASSIFIED_ROOT / outcome
        link_root.mkdir(parents=True, exist_ok=True)
        expected = {row["case"] for row in rows}
        for old in link_root.iterdir():
            if old.name not in expected:
                if old.is_dir() and not old.is_symlink():
                    shutil.rmtree(old)
                else:
                    old.unlink()
        for row in rows:
            replace_link(link_root / row["case"], Path(row["trajectory"]).parent)
        write_jsonl(CLASSIFIED_ROOT / f"{outcome}.jsonl", rows)
        write_json(
            CLASSIFIED_ROOT / f"{outcome}_summary.json",
            {"outcome": outcome, "count": len(rows), "results": rows},
        )
    write_json(
        CLASSIFIED_ROOT / "summary.json",
        {
            "total": len(passed) + len(failed),
            "counts": {"pass": len(passed), "fail": len(failed)},
            "source_verifier_summary": str(VERIFY_ROOT / "summary.json"),
            "pass_manifest": str(CLASSIFIED_ROOT / "pass_summary.json"),
            "fail_manifest": str(CLASSIFIED_ROOT / "fail_summary.json"),
        },
    )


def sample_passes(passed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Cover distinct workflow families first, then favor substantive trajectories.
    prefixes = [
        "semiconductor_parameter_sweep_monte_carlo_1",
        "semiconductor_scenario_01_",
        "semiconductor_scenario_03_",
        "semiconductor_scenario_04_",
        "semiconductor_scenario_05_",
        "semiconductor_scenario_06_",
        "semiconductor_scenario_07_01_",
        "semiconductor_scenario_07_03_",
        "semiconductor_scenario_07_07_",
        "semiconductor_scenario_08_",
    ]
    selected: list[dict[str, Any]] = []
    used_environments: set[str] = set()
    for prefix in prefixes:
        candidates = [
            row
            for row in passed
            if row["case"].startswith(prefix)
            and row["environment_id"] not in used_environments
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda row: (row["tool_calls"], row["steps"], row["case"]),
            reverse=True,
        )
        choice = candidates[0]
        selected.append(choice)
        used_environments.add(choice["environment_id"])
    if len(selected) != 10:
        raise ValueError(f"expected 10 stratified samples, selected {len(selected)}")
    return selected


def prepare_sample_run(samples: list[dict[str, Any]]) -> None:
    SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
    expected = {row["case"] for row in samples}
    for child in SAMPLE_ROOT.iterdir():
        if child.name in {"summary.json", "selection.json", "index.html", "combined.html"}:
            continue
        if child.name not in expected:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    results = []
    for row in samples:
        case_dir = SAMPLE_ROOT / row["case"]
        case_dir.mkdir(parents=True, exist_ok=True)
        replace_link(case_dir / "trajectory.json", Path(row["trajectory"]))
        results.append(
            {
                "case": row["case"],
                "status": "completed",
                "trajectory": str(case_dir / "trajectory.json"),
                "steps": row["steps"],
                "compactions": row["compactions"],
                "environment_error_count": row["environment_error_count"],
                "tool_timeout_count": row["tool_timeout_count"],
            }
        )
    write_json(
        SAMPLE_ROOT / "summary.json",
        {
            "total": len(results),
            "completed": len(results),
            "failed": 0,
            "results": results,
        },
    )
    write_json(
        SAMPLE_ROOT / "selection.json",
        {
            "selection": "Ten verifier-PASS trajectories stratified across workflow families; the largest tool-call trajectory in each selected family was chosen.",
            "source_pass_count": len(passed),
            "samples": samples,
        },
    )


if __name__ == "__main__":
    passed, failed = classify()
    write_classification(passed, failed)
    prepare_sample_run(sample_passes(passed))
    print(
        json.dumps(
            {
                "classified_root": str(CLASSIFIED_ROOT),
                "sample_root": str(SAMPLE_ROOT),
                "pass": len(passed),
                "fail": len(failed),
            },
            ensure_ascii=False,
        )
    )
