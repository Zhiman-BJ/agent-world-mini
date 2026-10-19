"""Summarize selected execution summaries without hiding missing evaluations."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def outcome_category(result: dict) -> str:
    stop = result.get("tool_budget_stop") or {}
    if stop.get("reason") == "total_tool_call_limit_exceeded":
        return "tool_budget_fail"
    status = result.get("status", "pending")
    if status in {"cancelled", "canceled"}:
        return "execution_failed"
    verification = result.get("verification") or {}
    if verification.get("status") == "verified" and verification.get("outcome") in {"pass", "fail"}:
        return "verifier_" + verification["outcome"]
    if verification.get("status") == "verification_error":
        return "verification_error"
    if status in {"failed", "environment_stopped"}:
        return "execution_failed"
    if status == "completed":
        return "completed_unverified"
    return "pending"


def summarize(paths: list[Path], expected: int, *, interruption_policy: str) -> dict:
    if expected < 1:
        raise ValueError("expected must be positive")
    if interruption_policy not in {"separate", "fail"}:
        raise ValueError("Unknown interruption policy")
    results = {}
    replacements = []
    for path in paths:
        summary = json.loads(path.read_text(encoding="utf-8"))
        rows = summary["results"]
        if not isinstance(rows, list):
            raise ValueError(f"Expected a results list in {path}")
        seen = set()
        for result in rows:
            case = result["case"]
            if not isinstance(case, str) or not case or case in seen:
                raise ValueError(f"Invalid or duplicate case in {path}: {case}")
            seen.add(case)
            if case in results:
                replacements.append({"case": case, "previous": results[case]["summary_source"],
                                     "replacement": str(path)})
            results[case] = {**result, "summary_source": str(path),
                             "report_category": outcome_category(result)}
    if len(results) > expected:
        raise ValueError("Observed more unique cases than --expected; check the fixed selection")
    counts = Counter({category: 0 for category in (
        "verifier_pass", "verifier_fail", "tool_budget_fail", "verification_error",
        "execution_failed", "completed_unverified", "pending",
    )})
    counts.update(row["report_category"] for row in results.values())
    counts["not_started"] = expected - len(results)
    passed = counts["verifier_pass"]
    failed = counts["verifier_fail"] + counts["tool_budget_fail"]
    if interruption_policy == "fail":
        failed += counts["execution_failed"]
    unresolved = expected - passed - failed
    return {
        "expected": expected, "observed": len(results), "counts": dict(counts),
        "model_pass": passed, "model_fail": failed, "unresolved": unresolved,
        "complete": unresolved == 0,
        "fixed_denominator_pass_fraction": passed / expected,
        "decided_pass_rate": passed / (passed + failed) if passed + failed else None,
        "interruption_policy": interruption_policy,
        "summary_sources": [str(path) for path in paths], "replacements": replacements,
        "note": "Later explicit summaries replace earlier cases. Pending, unverified and verifier errors remain unresolved. Execution interruptions count as FAIL only under interruption_policy=fail. No evidence or verdict is modified.",
        "results": list(results.values()),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", type=Path, required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--interruption-policy", choices=("separate", "fail"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = summarize(arguments.summary, arguments.expected,
                           interruption_policy=arguments.interruption_policy)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
