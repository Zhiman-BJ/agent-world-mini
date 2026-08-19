from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def learned_tokens(row: dict[str, Any], tokenizer: Any) -> int:
    learned_roles = {"gpt", "function_call"}
    return sum(
        len(tokenizer.encode(turn["value"], add_special_tokens=False))
        for turn in row["conversations"]
        if turn["from"] in learned_roles
    )


def build(data_root: Path, tokenizer_path: str, replay_fraction: float, seed: int) -> dict[str, Any]:
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    tool_rows = load_jsonl(data_root / "sft" / "agentworld_train.jsonl")
    replay_rows = load_jsonl(data_root / "sft" / "general_replay_pool.jsonl")
    tool_tokens = sum(learned_tokens(row, tokenizer) for row in tool_rows)
    replay_target = round(tool_tokens * replay_fraction / (1 - replay_fraction))
    rng = random.Random(seed)
    rng.shuffle(replay_rows)
    selected: list[dict[str, Any]] = []
    replay_tokens = 0
    for row in replay_rows:
        selected.append(row)
        replay_tokens += learned_tokens(row, tokenizer)
        if replay_tokens >= replay_target:
            break
    if replay_tokens < replay_target:
        raise RuntimeError(f"Replay pool has {replay_tokens} tokens; {replay_target} required")

    mixed = tool_rows + selected
    rng.shuffle(mixed)
    output = data_root / "sft" / "agentworld_train_mixed.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in mixed), encoding="utf-8")
    actual_fraction = replay_tokens / (tool_tokens + replay_tokens)
    report = {
        "tokenizer": tokenizer_path,
        "agentworld_rows": len(tool_rows),
        "agentworld_learned_tokens": tool_tokens,
        "replay_rows": len(selected),
        "replay_learned_tokens": replay_tokens,
        "target_replay_fraction": replay_fraction,
        "actual_replay_fraction": round(actual_fraction, 6),
    }
    (data_root / "sft_mix_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    info_path = data_root / "dataset_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["agentworld_train_mixed"] = {
        **info["agentworld_train"],
        "file_name": "sft/agentworld_train_mixed.jsonl",
    }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mix replay by learned-token budget")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--tokenizer", default="Qwen/Qwen3-8B")
    parser.add_argument("--replay-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()
    print(json.dumps(build(args.data_root.resolve(), args.tokenizer, args.replay_fraction, args.seed), indent=2))


if __name__ == "__main__":
    main()
