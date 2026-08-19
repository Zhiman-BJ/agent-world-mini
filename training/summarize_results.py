from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect internal and lm-eval results into one Markdown table")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.results.resolve()
    models: dict[str, dict[str, Any]] = {}
    for path in root.glob("*_internal_pass1.json"):
        tag = path.name.removesuffix("_internal_pass1.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        models.setdefault(tag, {})["internal_pass1"] = report["pass_at_1"]
    for path in root.glob("*_internal_pass5.json"):
        tag = path.name.removesuffix("_internal_pass5.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        models.setdefault(tag, {})["internal_pass5"] = report["pass_at_k"]
    for directory in root.glob("*_general"):
        tag = directory.name.removesuffix("_general")
        candidates = sorted(directory.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for path in candidates:
            value = json.loads(path.read_text(encoding="utf-8"))
            if "results" not in value:
                continue
            for task, metrics in value["results"].items():
                primary = next((score for key, score in metrics.items() if ",stderr" not in key and isinstance(score, (int, float))), None)
                if primary is not None:
                    models.setdefault(tag, {}).setdefault(task, primary)

    for path in root.glob("bfcl/*/score/data_overall.csv"):
        tag = path.parents[1].name
        with path.open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle), None)
        if row and row.get("Overall Acc"):
            models.setdefault(tag, {})["bfcl_overall"] = row["Overall Acc"]

    columns = ["internal_pass1", "internal_pass5", "ifeval", "mmlu_pro"]
    lines = ["# Agent-World training results", "", "| Model | " + " | ".join(columns) + " |", "| --- | " + " | ".join("---:" for _ in columns) + " |"]
    for tag, values in sorted(models.items()):
        lines.append("| " + tag + " | " + " | ".join(scalar(values.get(column, "-")) for column in columns) + " |")
    lines.extend(
        [
            "",
            "The internal columns use 15 entirely unseen environments. IFEval and MMLU-Pro are retention checks; official tool-use results are summarized separately under `official/summary.md`.",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
