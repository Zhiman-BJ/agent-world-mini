"""Minimal command surface for direct Step 3 integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .direct_commands import assess_environment, build_environment, finalize_environment


def _main() -> None:
    parser = argparse.ArgumentParser(description="DataGen Step 3 直接集成控制器")
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="用统一 build.py 生成全部 state")
    build.add_argument("--timeout-seconds", type=int, default=900)
    commands.add_parser("assess", help="校验最终环境并独立重放 build.py")
    commands.add_parser("finalize", help="验收通过后收口 Step 3")
    arguments = parser.parse_args()
    if arguments.command == "build":
        result = build_environment(
            arguments.run_dir, timeout_seconds=arguments.timeout_seconds,
        )
    elif arguments.command == "assess":
        result = assess_environment(arguments.run_dir)
    else:
        result = finalize_environment(arguments.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


main = _main

__all__ = ["_main", "main"]
