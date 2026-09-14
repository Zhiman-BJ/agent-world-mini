"""Minimal command surface for direct Step 3 integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .direct_commands import assess_environment


def _main() -> None:
    parser = argparse.ArgumentParser(description="DataGen Step 3 直接集成控制器")
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("assess", help="校验 Agent 直接生成的最终环境")
    arguments = parser.parse_args()
    result = assess_environment(arguments.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


main = _main

__all__ = ["_main", "main"]
