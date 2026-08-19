from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from datasets import load_dataset


SOURCE_ALLOWLIST = (
    "oasst1",
    "flan_v2",
    "aya",
    "wildchat",
    "sciriff",
    "tablegpt",
    "evol_codealpaca",
)
LEAKAGE_MARKERS = ("mmlu", "gsm8k", "math_500", "humaneval", "mbpp", "ifeval", "bfcl")


def assistant_characters(row: dict[str, Any]) -> int:
    return sum(len(turn["value"]) for turn in row["conversations"] if turn["from"] == "gpt")


def convert(example: dict[str, Any]) -> dict[str, Any] | None:
    source = str(example.get("source", ""))
    identifier = str(example.get("id", ""))
    lowered = f"{source} {identifier}".casefold()
    if not any(name in source.casefold() for name in SOURCE_ALLOWLIST):
        return None
    if any(marker in lowered for marker in LEAKAGE_MARKERS):
        return None
    system = ""
    conversations: list[dict[str, str]] = []
    expected = "user"
    for message in example.get("messages", []):
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        if role == "system" and not conversations:
            system = content
            continue
        if role != expected:
            return None
        conversations.append({"from": "human" if role == "user" else "gpt", "value": content})
        expected = "assistant" if role == "user" else "user"
    if not conversations or conversations[-1]["from"] != "gpt":
        return None
    row = {
        "conversations": conversations,
        "system": system,
        "tools": "",
        "metadata": {"source": source, "source_id": identifier, "replay_kind": "general"},
    }
    chars = assistant_characters(row)
    return row if 20 <= chars <= 12000 else None


def category(source: str) -> str:
    lowered = source.casefold()
    if "code" in lowered or "python" in lowered:
        return "code"
    if "sciriff" in lowered or "tablegpt" in lowered:
        return "science"
    return "general"


def fetch(data_root: Path, seed: int, pool_ratio: float, no_tool_fraction: float) -> dict[str, Any]:
    stats = json.loads((data_root / "stats.json").read_text(encoding="utf-8"))
    target = int(stats["assistant_characters"]["train"] * pool_ratio)
    targets = {"general": int(target * 0.6), "code": int(target * 0.25), "science": int(target * 0.15)}
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    dataset = load_dataset("allenai/tulu-3-sft-mixture", split="train", streaming=True)
    dataset = dataset.shuffle(seed=seed, buffer_size=10_000)
    for example in dataset:
        row = convert(example)
        if row is None:
            continue
        bucket = category(row["metadata"]["source"])
        if counts[bucket] >= targets[bucket]:
            continue
        rows.append(row)
        counts[bucket] += assistant_characters(row)
        if all(counts[name] >= amount for name, amount in targets.items()):
            break

    if not all(counts[name] >= amount for name, amount in targets.items()):
        raise RuntimeError(f"Replay stream ended before quotas were filled: {dict(counts)}")

    tool_rows = [
        json.loads(line)
        for line in (data_root / "sft" / "agentworld_train.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rng = random.Random(seed)
    rng.shuffle(rows)
    no_tool_count = round(len(rows) * no_tool_fraction)
    for index, row in enumerate(rows[:no_tool_count]):
        distractor = rng.choice(tool_rows)
        tools = json.loads(distractor["tools"])
        rng.shuffle(tools)
        row["tools"] = json.dumps(tools[:4], ensure_ascii=False)
        row["metadata"]["replay_kind"] = "no_tool_with_distractors"

    output = data_root / "sft" / "general_replay_pool.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    report = {
        "dataset": "allenai/tulu-3-sft-mixture",
        "dataset_license": "ODC-BY-1.0; subset terms also apply",
        "allowed_source_patterns": SOURCE_ALLOWLIST,
        "rows": len(rows),
        "assistant_characters": sum(assistant_characters(row) for row in rows),
        "category_characters": dict(counts),
        "no_tool_rows": no_tool_count,
    }
    (data_root / "general_replay_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a license-traceable general replay pool")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--pool-ratio", type=float, default=0.75)
    parser.add_argument("--no-tool-fraction", type=float, default=0.2)
    args = parser.parse_args()
    print(json.dumps(fetch(args.data_root.resolve(), args.seed, args.pool_ratio, args.no_tool_fraction), indent=2))


if __name__ == "__main__":
    main()
