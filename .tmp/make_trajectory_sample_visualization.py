#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

REPO = Path("/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini")
BATCH = Path("/data1/agent_world/trajectory/semiconductor_kimi_k3_20260930")
OUTPUT = BATCH / "visualizations/random_sample_20261002"
SEED = 20261002
PER_CATEGORY = 3

sys.path.insert(0, str(REPO))
from distill.visualize import render_combined_visualization


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


manifest = read_json(BATCH / "manifest.json")
groups = {
    "untouched_old_pass": [
        row for row in manifest["accepted"] if row["category"] == "untouched_old_pass"
    ],
    "mismatch_replacement": [
        row for row in manifest["accepted"] if row["category"] == "mismatch_replacement"
    ],
}
recovery_pass = {row["case"] for row in manifest["recovery_verifier"]["pass"]}
groups["recovered_verified_pass"] = [
    row for row in manifest["pending_verifier"] if row["case"] in recovery_pass
]

rng = random.Random(SEED)
selected = []
for category, rows in groups.items():
    chosen = rng.sample(sorted(rows, key=lambda row: row["case"]), PER_CATEGORY)
    for row in chosen:
        selected.append({**row, "sample_category": category})
rng.shuffle(selected)

if OUTPUT.exists():
    shutil.rmtree(OUTPUT)
OUTPUT.mkdir(parents=True)

results = []
sample_rows = []
for index, row in enumerate(selected, 1):
    case = row["case"]
    source = Path(row["source"])
    trajectory = read_json(source / "trajectory.json")
    case_dir = OUTPUT / case
    case_dir.mkdir()
    (case_dir / "trajectory.json").symlink_to(source / "trajectory.json")
    (case_dir / "raw").symlink_to(source / "raw", target_is_directory=True)
    task_text = trajectory.get("task", {}).get("task_text", "")
    steps = trajectory.get("steps", [])
    tool_calls = sum(len(step.get("tool_calls", [])) for step in steps)
    first_request = source / "raw/model_requests.jsonl"
    first_request_bytes = first_request.stat().st_size if first_request.is_file() else 0
    results.append({
        "case": case,
        "status": "completed",
        "sample_category": row["sample_category"],
    })
    sample_rows.append({
        "index": index,
        "case": case,
        "category": row["sample_category"],
        "source": str(source),
        "task_prompt": task_text,
        "task_prompt_chars": len(task_text),
        "steps": len(steps),
        "tool_calls": tool_calls,
        "first_request_file_bytes": first_request_bytes,
    })

(OUTPUT / "summary.json").write_text(
    json.dumps({
        "run_id": OUTPUT.name,
        "selection": {
            "seed": SEED,
            "per_category": PER_CATEGORY,
            "categories": list(groups),
        },
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
(OUTPUT / "sample_manifest.json").write_text(
    json.dumps({
        "seed": SEED,
        "sample_count": len(sample_rows),
        "samples": sample_rows,
    }, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

render_combined_visualization(OUTPUT)
print(OUTPUT)
print(json.dumps(sample_rows, ensure_ascii=False, indent=2))
