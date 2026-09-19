#!/usr/bin/env python3
"""Load one ToolGen delivery binding and execute one tool in an isolated session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from env_gen.tool_gen.runtime import ToolPackage, ToolRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", default="{}", help="JSON object")
    arguments = parser.parse_args()

    root = arguments.delivery_root.resolve()
    binding_path = root / "environments" / arguments.package_id / "binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    tool_document = json.loads(
        (root / binding["tools_path"]).read_text(encoding="utf-8")
    )
    tool_arguments = json.loads(arguments.arguments)
    if not isinstance(tool_arguments, dict):
        raise ValueError("--arguments must be a JSON object")

    package = ToolPackage.load(
        root / binding["environment_path"],
        tools=tool_document["tools"],
    )
    with ToolRuntime(package) as runtime:
        result = runtime.call(arguments.tool, tool_arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
