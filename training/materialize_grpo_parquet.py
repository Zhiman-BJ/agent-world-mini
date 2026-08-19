from __future__ import annotations

import argparse
import json
from pathlib import Path

from datasets import Dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert GRPO JSONL files to veRL parquet files")
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_root.resolve()
    for split in ("train", "dev", "test"):
        source = root / "grpo" / f"agentworld_{split}.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        Dataset.from_list(rows).to_parquet(root / "grpo" / f"agentworld_{split}.parquet")
        print(f"{split}: {len(rows)} rows")


if __name__ == "__main__":
    main()
