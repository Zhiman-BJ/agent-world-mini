from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any


MODELS = [
    ("base", "Base"),
    ("lora_tool_only", "LoRA tool-only"),
    ("lora_mixed", "LoRA mixed"),
    ("full_sft_mixed", "Full SFT mixed"),
    ("lora_mixed_grpo", "LoRA + GRPO"),
]


def percentage(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return None
    return float(text.removesuffix("%"))


def bfcl_score(root: Path, tag: str) -> float | None:
    scores: list[float] = []
    for run in sorted((root / "bfcl_v4" / tag).glob("run-*")):
        path = run / "score" / "data_overall.csv"
        if not (run / ".complete").exists() or not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle), None)
        score = percentage(row.get("Overall Acc")) if row else None
        if score is not None:
            scores.append(score)
    return mean(scores) if scores else None


def mcpmark_scores(root: Path, tag: str) -> dict[str, float]:
    totals: dict[str, list[int]] = {}
    for path in (root / "mcpmark" / f"agentworld-{tag}").rglob("summary.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        service = report.get("model_config", {}).get("mcp_service")
        total = int(report.get("total_tasks", 0))
        successful = int(report.get("successful_tasks", 0))
        if service and total:
            counts = totals.setdefault(service, [0, 0])
            counts[0] += successful
            counts[1] += total
    scores = {service: successful / total * 100 for service, (successful, total) in totals.items()}
    successful = sum(value[0] for value in totals.values())
    total = sum(value[1] for value in totals.values())
    if total:
        scores["average"] = successful / total * 100
    return scores


def tau2_scores(root: Path, tag: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    all_rewards: list[float] = []
    for path in sorted((root / "tau2" / tag).glob("*/results.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        rewards = [
            float(item["reward_info"]["reward"])
            for item in report.get("simulations", [])
            if item.get("reward_info", {}).get("reward") is not None
        ]
        if rewards:
            scores[path.parent.name] = mean(rewards) * 100
            all_rewards.extend(rewards)
    if all_rewards:
        scores["average"] = mean(all_rewards) * 100
    return scores


def atlas_scores(root: Path, tag: str) -> dict[str, float]:
    path = root / "mcp_atlas" / tag / "scored" / f"coverage_stats_{tag}_combined.json"
    if not path.exists():
        return {}
    values = json.loads(path.read_text(encoding="utf-8")).get("all", {})
    scores: dict[str, float] = {}
    if values.get("pass_rate_0.50") is not None:
        scores["pass"] = float(values["pass_rate_0.50"])
    if values.get("mean_coverage") is not None:
        scores["coverage"] = float(values["mean_coverage"]) * 100
    return scores


def vitabench_scores(root: Path, tag: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    all_rewards: list[float] = []
    for path in sorted((root / "vitabench" / tag).glob("*/results.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        rewards = [
            float(item["reward_info"]["reward"])
            for item in report.get("simulations", [])
            if item.get("reward_info", {}).get("reward") is not None
        ]
        if rewards:
            scores[path.parent.name] = mean(rewards) * 100
            all_rewards.extend(rewards)
    if all_rewards:
        scores["average"] = mean(all_rewards) * 100
    return scores


def display(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize official tool-use benchmarks")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag-prefix", default="")
    args = parser.parse_args()

    columns = [
        "MCP-Mark File",
        "GitHub",
        "Notion",
        "Playwright",
        "Postgres",
        "MCP-Mark Avg",
        "BFCL V4",
        "tau2 Retail",
        "Telecom",
        "Airline",
        "tau2 Avg",
        "MCP-Atlas Pass@0.50",
        "MCP-Atlas Coverage",
        "Vita Delivery",
        "In-store",
        "OTA",
        "Vita Avg",
    ]
    lines = [
        "# Official tool-use benchmark results",
        "",
        "All values are percentages. A dash means that the formal result is not complete yet.",
        "",
        "| Model | " + " | ".join(columns) + " |",
        "| --- | " + " | ".join("---:" for _ in columns) + " |",
    ]
    for tag, label in MODELS:
        result_tag = f"{args.tag_prefix}{tag}"
        mcpmark = mcpmark_scores(args.results, result_tag)
        tau2 = tau2_scores(args.results, result_tag)
        atlas = atlas_scores(args.results, result_tag)
        vita = vitabench_scores(args.results, result_tag)
        values = [
            mcpmark.get("filesystem"),
            mcpmark.get("github"),
            mcpmark.get("notion"),
            mcpmark.get("playwright"),
            mcpmark.get("postgres"),
            mcpmark.get("average"),
            bfcl_score(args.results, result_tag),
            tau2.get("retail"),
            tau2.get("telecom"),
            tau2.get("airline"),
            tau2.get("average"),
            atlas.get("pass"),
            atlas.get("coverage"),
            vita.get("delivery"),
            vita.get("instore"),
            vita.get("ota"),
            vita.get("average"),
        ]
        lines.append(f"| {label} | " + " | ".join(display(value) for value in values) + " |")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
