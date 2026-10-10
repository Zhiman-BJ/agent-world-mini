from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
import shutil

from distill.visualize import (
    _combined_template,
    _embedded_json,
    render_trajectory_html,
)


VERIFY = Path("/data1/agent_world/aw-data/k3_mismatch_replacements_verifier_20261003")
OUT = VERIFY / "native_tools_sample_visualization"
CASES = [
    # PASS: substantial use of Read/Grep.
    "semiconductor_scenario_07_03_01__task2",
    "semiconductor_scenario_07_04_03__task10",
    # PASS: examples where the restricted native tool surface is narrower.
    "semiconductor_scenario_04_01_03__task12",  # Glob
    "semiconductor_scenario_03_01_02__task18",  # Read
    "semiconductor_scenario_04_01_01__task24",  # Grep
    # FAIL: the only replacement trajectory using all three native tools.
    "semiconductor_scenario_07_05_03__task3",
]


def load_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for outcome in ("pass", "fail"):
        for line in (VERIFY / "classified" / f"{outcome}.jsonl").read_text(
            encoding="utf-8"
        ).splitlines():
            row = json.loads(line)
            index[row["case"]] = row
    return index


def reset_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name in {"selection.json", "summary.json", "index.html", "combined.html"}:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> None:
    index = load_index()
    rows = []
    reset_dir(OUT)
    for case in CASES:
        row = index[case]
        source_dir = Path(row["trajectory"]).parent
        target_dir = OUT / case
        target_dir.mkdir(parents=True, exist_ok=True)
        trajectory_link = target_dir / "trajectory.json"
        if trajectory_link.exists() or trajectory_link.is_symlink():
            trajectory_link.unlink()
        trajectory_link.symlink_to(source_dir / "trajectory.json")
        raw_link = target_dir / "raw"
        if raw_link.exists() or raw_link.is_symlink():
            if raw_link.is_dir() and not raw_link.is_symlink():
                shutil.rmtree(raw_link)
            else:
                raw_link.unlink()
        raw_link.symlink_to(source_dir / "raw", target_is_directory=True)
        trajectory = json.loads((source_dir / "trajectory.json").read_text(encoding="utf-8"))
        native = [
            call
            for step in trajectory.get("steps", [])
            for call in step.get("tool_calls", [])
            if call.get("tool_kind") == "native_file_tool"
        ]
        native_names = sorted({call.get("name") for call in native})
        rows.append(
            {
                "case": case,
                "status": row["outcome"],
                "verifier_summary": row["verifier_summary"],
                "trajectory": str(target_dir / "trajectory.json"),
                "source_trajectory": row["trajectory"],
                "native_tool_calls": len(native),
                "native_tool_names": native_names,
                "steps": len(trajectory.get("steps", [])),
                "environment_id": row["environment_id"],
            }
        )
    summary = {
        "total": len(rows),
        "completed": len(rows),
        "failed": 0,
        "results": [
            {
                "case": row["case"],
                "status": "completed",
                "trajectory": row["trajectory"],
                "steps": row["steps"],
                "compactions": 0,
                "environment_error_count": 0,
                "tool_timeout_count": 0,
            }
            for row in rows
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "selection.json").write_text(
        json.dumps(
            {
                "purpose": "Inspect native restricted-tool usage in the new verifier batch.",
                "note": "The PASS subset has no trajectory using all three Read/Grep/Glob tools. The final sample is the only all-three case and is explicitly verifier FAIL.",
                "samples": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    documents = []
    for index_number, row in enumerate(rows, 1):
        case_dir = OUT / row["case"]
        html_path = render_trajectory_html(
            case_dir / "trajectory.json", case_dir / "trajectory.html"
        )
        html = html_path.read_bytes()
        tool_label = "+".join(row["native_tool_names"])
        documents.append(
            {
                "index": index_number,
                "case": row["case"],
                "environment_id": row["environment_id"],
                "task_id": f"{row['status'].upper()} | {tool_label} | {row['case']}",
                "task_text": row["verifier_summary"],
                "status": f"verifier_{row['status']}",
                "gzip_base64": base64.b64encode(
                    gzip.compress(html, compresslevel=9, mtime=0)
                ).decode("ascii"),
            }
        )
    combined = _combined_template(
        _embedded_json({"run_id": "new-verifier native Read/Grep/Glob samples", "documents": documents})
    ).replace("Kimi K3 最终轨迹集", "Kimi K3 原生文件工具样本")
    (OUT / "combined.html").write_text(combined, encoding="utf-8")
    print(json.dumps({"output": str(OUT), "samples": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
