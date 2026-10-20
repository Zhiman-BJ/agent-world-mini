"""Verify retained evidence without running the solver or modifying its files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .verifier import (
    prepare_verification_workspace,
    serialized_state_preparation,
    verify_execution,
)


def evidence_arguments(request_path: Path) -> dict:
    root = request_path.resolve().parent
    request = json.loads(request_path.read_text(encoding="utf-8"))

    def location(key: str) -> Path:
        value = request[key]
        if not isinstance(value, str):
            raise ValueError(f"Evidence location must be a relative path: {key}")
        relative = Path(value)
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or not path.exists():
            raise ValueError(f"Invalid evidence location: {key}")
        return path

    environment = json.loads(location("environment").read_text(encoding="utf-8"))
    environment["tools"] = json.loads(location("tools").read_text(encoding="utf-8"))
    actual = location("actual")
    for name in ("final", "answer.txt", "tool_calls.jsonl", "execution.json"):
        path = (actual / name).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError(f"Missing or invalid actual evidence: {name}")
    evidence = {
        "task": {"task_id": request["task_id"], "task_text": request["task_text"]},
        "environment": environment,
        "initial_state": location("initial"),
        "actual_state": actual / "final",
        "answer": (actual / "answer.txt").read_text(encoding="utf-8"),
        "calls": [json.loads(line) for line in
                  (actual / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()],
        "execution": json.loads((actual / "execution.json").read_text(encoding="utf-8")),
    }
    if request.get("verification_profile"):
        evidence.update(
            verification_profile=request["verification_profile"],
            acceptance_contract=json.loads(location("acceptance_contract").read_text(encoding="utf-8")),
        )
    return evidence


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path,
                        help="Prepared request.json containing task_text and relative evidence paths")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="high")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--orchestration-only", action="store_true",
                        help="Apply the restricted computation policy to verifier investigation")
    parser.add_argument("--submission-preflight", action="store_true")
    parser.add_argument("--method", choices=("direct_codex_rubric", "codex"), default=None)
    parser.add_argument("--verifier-dir", type=Path, help="Reuse a frozen Direct verifier directory")
    arguments = parser.parse_args(argv)
    if arguments.timeout < 1 or arguments.attempts < 1:
        parser.error("timeout and attempts must be positive")
    source = arguments.request.resolve().parent
    target = arguments.output.resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        parser.error("output must be outside, and not an ancestor of, the evidence directory")
    try:
        evidence = evidence_arguments(arguments.request)
        if arguments.prepare_only:
            with serialized_state_preparation():
                prepare_verification_workspace(target, **evidence)
            result = {"status": "prepared", "request": str(target / "request.json")}
        else:
            result = verify_execution(run_dir=target, config={
                **({"method": arguments.method} if arguments.method else {}),
                **({"verifier_dir": str(arguments.verifier_dir)} if arguments.verifier_dir else {}),
                "codex_home": str(arguments.codex_home.expanduser().resolve()),
                "model": arguments.model, "reasoning_effort": arguments.reasoning_effort,
                "timeout_seconds": arguments.timeout, "attempts": arguments.attempts,
                **({"orchestration_only": True} if arguments.orchestration_only else {}),
                **({"submission_preflight": True} if arguments.submission_preflight else {}),
            }, **evidence)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
