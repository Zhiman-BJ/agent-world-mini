from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_world_mini.utils.io import write_json


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _average(values: list[int]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def _step_distribution(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(task["validation"]["chain_steps"]) for task in tasks).items()))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def export_batch(batch_dir: Path) -> dict[str, Any]:
    manifest = _load(batch_dir / "production_manifest.json")
    manifest_environments = {
        item["slug"]: item
        for item in manifest.get("environments", manifest.get("candidate_pool", []))
    }
    completed_environments = []
    final_tasks = []
    all_five_run_tasks = []
    successful_trajectories = []
    preferred_demonstrations = []
    incomplete = []
    failure_reasons: Counter[str] = Counter()
    rollout_attempts = 0
    rollout_successes = 0
    rollout_tool_calls = 0
    raw_walks = 0
    executed_walks = 0
    reviewed_natural_tasks = 0
    accepted_compositions = 0
    semantically_rejected_tasks = 0

    for environment_id in sorted(manifest_environments):
        environment_dir = batch_dir / environment_id
        if not environment_dir.is_dir():
            incomplete.append(environment_id)
            continue
        report_path = environment_dir / "luna_five_run_report.json"
        if not report_path.is_file():
            incomplete.append(environment_dir.name)
            continue

        report = _load(report_path)
        tasks_payload = _load(environment_dir / "tasks.json")
        summary = _load(environment_dir / "summary.json")
        raw_walks += int(summary.get("raw_weighted_walks", 0))
        executed_walks += int(summary.get("executed_walks", 0))
        reviewed_natural_tasks += int(summary.get("luna_review", {}).get("accepted_tasks", 0))
        accepted_compositions += int(summary.get("luna_composition", {}).get("accepted_compositions", 0))
        passed = list(tasks_payload.get("tasks", []))
        reviewed = []
        for task in passed + list(tasks_payload.get("rejected_tasks", [])):
            if task.get("validation", {}).get("semantic_rejection_reason"):
                semantically_rejected_tasks += 1
                continue
            verification = task.get("validation", {}).get("five_run_verification")
            if not verification:
                continue
            reviewed.append(task)
            for run in verification.get("runs", []):
                rollout_attempts += 1
                rollout_successes += int(bool(run.get("success")))
                rollout_tool_calls += len(run.get("calls", []))
                if not run.get("success"):
                    reason = run.get("judgment", {}).get("reason", "unknown")
                    failure_reasons[str(reason)] += 1

            successful_runs = [run for run in verification.get("runs", []) if run.get("success")]
            trajectory_rows = []
            for run_index, run in enumerate(successful_runs, start=1):
                trajectory = {
                    "trajectory_id": f"{environment_dir.name}/{task['task_id']}/success_{run_index}",
                    "environment_id": environment_dir.name,
                    "task_id": task["task_id"],
                    "task_five_run_status": verification.get("status"),
                    "request": task["request"],
                    "available_tools": task.get("available_tools", []),
                    "required_answer_sections": task.get("validation", {}).get("answer_slots", []),
                    "reference_calls": task.get("validation", {}).get("reference_calls", []),
                    "reference_answer": task.get("reference_execution", {}).get("reference_answer", {}),
                    "chain_steps": task.get("validation", {}).get("chain_steps", 0),
                    "calls": run.get("calls", []),
                    "final_answer": run.get("final_answer"),
                    "judgment": run.get("judgment", {}),
                }
                successful_trajectories.append(trajectory)
                trajectory_rows.append(trajectory)
            if verification.get("status") == "passed" and trajectory_rows:
                preferred = min(trajectory_rows, key=lambda item: len(item["calls"]))
                preferred_demonstrations.append({**preferred, "preferred_for_sft": True})

        environment_meta = manifest_environments.get(environment_dir.name, {})
        environment_id = environment_dir.name
        for task in passed:
            final_tasks.append({"environment_id": environment_id, **task})
        all_five_run_tasks.extend(reviewed)

        environment_before_steps = [int(task["validation"]["chain_steps"]) for task in reviewed]
        environment_final_steps = [int(task["validation"]["chain_steps"]) for task in passed]

        completed_environments.append({
            "environment_id": environment_id,
            "theme": environment_meta.get("theme"),
            "source_name": environment_meta.get("qualified_name"),
            "research_bundle": _load(environment_dir / "research_bundle.json"),
            "tool_specs": _load(environment_dir / "tool_specs.json"),
            "tasks_before_five_run": len(reviewed),
            "tasks_passed": len(passed),
            "tasks_rejected": len(reviewed) - len(passed),
            "task_pass_rate": round(len(passed) / len(reviewed), 4) if reviewed else 0.0,
            "average_steps_before_five_run": _average(environment_before_steps),
            "average_steps_final": _average(environment_final_steps),
            "tasks_at_least_7_steps": sum(step >= 7 for step in environment_final_steps),
        })

    final_steps = [int(task["validation"]["chain_steps"]) for task in final_tasks]
    before_steps = [int(task["validation"]["chain_steps"]) for task in all_five_run_tasks]
    composed_final = [
        task for task in final_tasks
        if task.get("validation", {}).get("composition_source_task_ids")
    ]
    composed_before = [
        task for task in all_five_run_tasks
        if task.get("validation", {}).get("composition_source_task_ids")
    ]
    natural_final = [
        task for task in final_tasks
        if not task.get("validation", {}).get("composition_source_task_ids")
    ]
    natural_before = [
        task for task in all_five_run_tasks
        if not task.get("validation", {}).get("composition_source_task_ids")
    ]
    tasks_before = len(all_five_run_tasks)
    tasks_passed = len(final_tasks)
    stats = {
        "environments_planned": len(manifest_environments),
        "environments_completed": len(completed_environments),
        "environments_incomplete": incomplete,
        "real_records": manifest.get("research_audit", {}).get("records", 0),
        "sampled_walks": raw_walks,
        "executable_unique_candidate_chains": executed_walks,
        "luna_reviewed_natural_tasks": reviewed_natural_tasks,
        "accepted_related_compositions": accepted_compositions,
        "semantically_rejected_tasks": semantically_rejected_tasks,
        "tasks_before_five_run": tasks_before,
        "tasks_passed": tasks_passed,
        "tasks_rejected": tasks_before - tasks_passed,
        "task_pass_rate": round(tasks_passed / tasks_before, 4) if tasks_before else 0.0,
        "average_steps_before_five_run": _average(before_steps),
        "average_steps_final": _average(final_steps),
        "median_steps_final": _median(final_steps),
        "tasks_at_least_7_steps": sum(step >= 7 for step in final_steps),
        "tasks_at_least_7_steps_rate": round(sum(step >= 7 for step in final_steps) / len(final_steps), 4) if final_steps else 0.0,
        "longest_task_steps": max(final_steps, default=0),
        "before_five_run_step_distribution": _step_distribution(all_five_run_tasks),
        "final_step_distribution": _step_distribution(final_tasks),
        "natural_tasks_before_five_run": len(natural_before),
        "natural_tasks_passed": len(natural_final),
        "natural_task_pass_rate": round(len(natural_final) / len(natural_before), 4) if natural_before else 0.0,
        "natural_task_average_steps_final": _average([int(task["validation"]["chain_steps"]) for task in natural_final]),
        "composed_tasks_before_five_run": len(composed_before),
        "composed_tasks_passed": len(composed_final),
        "composed_task_pass_rate": round(len(composed_final) / len(composed_before), 4) if composed_before else 0.0,
        "composed_task_average_steps_final": _average([int(task["validation"]["chain_steps"]) for task in composed_final]),
        "rollout_attempts": rollout_attempts,
        "rollout_successes": rollout_successes,
        "rollout_success_rate": round(rollout_successes / rollout_attempts, 4) if rollout_attempts else 0.0,
        "rollout_tool_calls": rollout_tool_calls,
        "average_tool_calls_per_rollout": round(rollout_tool_calls / rollout_attempts, 2) if rollout_attempts else 0.0,
        "successful_trajectories": len(successful_trajectories),
        "preferred_sft_demonstrations": len(preferred_demonstrations),
        "failed_rollout_reasons": dict(failure_reasons.most_common()),
    }
    dataset = {
        "dataset_name": manifest.get("batch_id", batch_dir.name),
        "research_agent": manifest.get("research_agent", "gpt-5.6-luna"),
        "five_run_solver": "gpt-5.6-luna_subagent",
        "stats": stats,
        "environments": completed_environments,
        "tasks": final_tasks,
    }
    write_json(batch_dir / "luna_final_dataset.json", dataset)
    _write_jsonl(batch_dir / "luna_successful_trajectories.jsonl", successful_trajectories)
    _write_jsonl(batch_dir / "luna_sft_demonstrations.jsonl", preferred_demonstrations)
    report = {
        "batch_id": dataset["dataset_name"],
        "stats": stats,
        "environments": [
            {key: value for key, value in environment.items() if key not in {"research_bundle", "tool_specs"}}
            for environment in completed_environments
        ],
    }
    write_json(batch_dir / "luna_batch_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine completed Luna five-run environments")
    parser.add_argument("batch_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_batch(args.batch_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
