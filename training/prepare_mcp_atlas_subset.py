from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable


ENVFACTORY_EXCLUDED_SERVERS = {
    "brave-search",
    "google-workspace",
    "mongodb",
    "oxylabs",
    "slack",
    "wikipedia",
}


def used_tools(row: dict[str, Any]) -> list[str]:
    trajectory = row.get("TRAJECTORY", "[]")
    messages = json.loads(trajectory) if isinstance(trajectory, str) else trajectory
    return [
        call["function"]["name"]
        for message in messages
        for call in message.get("tool_calls", [])
    ]


def tool_server(name: str) -> str:
    for server in ENVFACTORY_EXCLUDED_SERVERS:
        if name.startswith(f"{server}_") or name.startswith(f"{server}-"):
            return server
    return ""


def envfactory_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not any(tool_server(name) for name in used_tools(row))]


def write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["TASK", "PROMPT", "ENABLED_TOOLS", "GTFA_CLAIMS", "TRAJECTORY"]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare the 291-task MCP-Atlas subset used by EnvFactory."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-output", type=Path)
    args = parser.parse_args()

    from datasets import load_dataset

    rows = envfactory_rows(load_dataset("ScaleAI/MCP-Atlas", split="train"))
    if len(rows) != 291:
        raise RuntimeError(f"expected EnvFactory's 291 tasks, found {len(rows)}")
    write_csv(rows, args.output)

    if args.smoke_output:
        smoke_id = "689e0b1d9c8e2ac413c1f23b"
        smoke = [row for row in rows if str(row.get("TASK")) == smoke_id]
        if len(smoke) != 1:
            raise RuntimeError(f"smoke task {smoke_id} is missing")
        write_csv(smoke, args.smoke_output)

    print(f"wrote {len(rows)} EnvFactory MCP-Atlas tasks to {args.output}")


if __name__ == "__main__":
    main()
