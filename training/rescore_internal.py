from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent_world_mini.task_gen.validation.five_run import FiveRunVerifier
from training.evaluate_internal import load_jsonl, parse_answer


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score saved internal trajectories without rerunning a model")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.input.read_text(encoding="utf-8"))
    rows = load_jsonl(args.data_root / "grpo" / f"agentworld_{report['split']}.jsonl")
    rows_by_id = {
        (row["extra_info"]["environment_id"], row["extra_info"]["task_id"]): row for row in rows
    }

    buckets: dict[str, list[bool]] = defaultdict(list)
    for result in report["results"]:
        row = rows_by_id[(result["environment_id"], result["task_id"])]
        truth = json.loads(row["reward_model"]["ground_truth"])
        task = {"request": truth["request"], "validation": {"answer_slots": truth["answer_slots"]}}
        for attempt in result["attempts"]:
            if "final_text" not in attempt:
                continue
            answer = parse_answer(attempt.get("final_text", ""))
            judgment = FiveRunVerifier._judge(
                task,
                {"reference_answer": truth["reference_answer"]},
                attempt["calls"],
                answer,
            )
            attempt["final_answer"] = answer
            attempt["judgment"] = judgment
            attempt["success"] = judgment["passed"]
        steps = result["chain_steps"]
        bucket = "1-3" if steps <= 3 else "4-6" if steps <= 6 else "7+"
        buckets[bucket].append(bool(result["attempts"][0]["success"]))

    results = report["results"]
    report["pass_at_1"] = round(sum(item["attempts"][0]["success"] for item in results) / len(results), 6)
    report["pass_at_k"] = round(
        sum(any(attempt["success"] for attempt in item["attempts"]) for item in results) / len(results), 6
    )
    report["by_reference_length"] = {
        name: {"tasks": len(values), "pass_at_1": round(sum(values) / len(values), 6)}
        for name, values in sorted(buckets.items())
    }
    report["failure_reasons"] = dict(
        Counter(
            item["attempts"][0]["judgment"].get("reason", "unknown")
            for item in results
            if not item["attempts"][0]["success"]
        )
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    main()
