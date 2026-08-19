from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def enabled_tools(row: dict[str, str]) -> list[str]:
    value = row.get("ENABLED_TOOLS", "[]")
    parsed = json.loads(value)
    return [str(item) for item in parsed]


def belongs_to(tool: str, server: str) -> bool:
    return tool == server or tool.startswith(f"{server}_") or tool.startswith(f"{server}-")


def main() -> None:
    csv.field_size_limit(sys.maxsize)
    parser = argparse.ArgumentParser(
        description="Keep MCP-Atlas tasks whose tools all use keyless MCP servers."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--servers", nargs="+", required=True)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = list(reader)

    kept = [
        row
        for row in rows
        if enabled_tools(row)
        and all(
            any(belongs_to(tool, server) for server in args.servers)
            for tool in enabled_tools(row)
        )
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(kept)

    print(f"kept {len(kept)} of {len(rows)} MCP-Atlas tasks")


if __name__ == "__main__":
    main()
