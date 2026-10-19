"""Run, inspect, and verify tool-use evaluations."""

from __future__ import annotations

import argparse
import importlib
import sys


COMMANDS = {
    "run": ("distill.__main__", "taskgen", "Run tasks through the official Kimi Code CLI"),
    "verify": ("eval.verifier", None, "Verify execution evidence supplied as API input JSON"),
    "verify-existing": ("eval.existing", None, "Verify an exported request.json evidence workspace"),
    "report": ("eval.report", None, "Summarize explicit run summaries with a fixed denominator"),
    "visualize": ("distill.__main__", "visualize", "Render retained trajectories as HTML"),
}


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS, help="; ".join(
        f"{name}: {entry[2]}" for name, entry in COMMANDS.items()
    ))
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.parse_args(arguments)
        return
    command = parser.parse_args(arguments[:1]).command
    module_name, forwarded_command, _ = COMMANDS[command]
    forwarded = ([forwarded_command] if forwarded_command else []) + arguments[1:]
    module = importlib.import_module(module_name)
    module.main(forwarded)


if __name__ == "__main__":
    main()
