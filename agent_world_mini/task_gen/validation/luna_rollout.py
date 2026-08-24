from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_world_mini.utils.io import write_json
from agent_world_mini.schemas.models import ResearchBundle, ToolSpec
from agent_world_mini.runtime.engine import LocalToolRuntime
from agent_world_mini.task_gen.validation.five_run import FiveRunVerifier


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_environment(environment_dir: Path) -> tuple[ResearchBundle, list[ToolSpec], dict[str, Any]]:
    bundle = ResearchBundle.from_dict(_load_json(environment_dir / "research_bundle.json"))
    tools = [ToolSpec(**item) for item in _load_json(environment_dir / "tool_specs.json").get("tools", [])]
    tasks = _load_json(environment_dir / "tasks.json")
    return bundle, tools, tasks


def _find_task(tasks: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in tasks.get("tasks", []):
        if task.get("task_id") == task_id:
            return task
    raise ValueError(f"Unknown task id: {task_id}")


def _session_path(environment_dir: Path, task_id: str, run_id: int) -> Path:
    return environment_dir / "luna_five_run" / f"{task_id}__run_{run_id}.json"


def list_tasks(environment_dir: Path) -> dict[str, Any]:
    _bundle, _tools, tasks = _load_environment(environment_dir)
    return {
        "tasks": [
            {"task_id": task["task_id"], "request": task["request"]}
            for task in tasks.get("tasks", [])
        ]
    }


def start(environment_dir: Path, task_id: str, run_id: int, max_steps: int = 14) -> dict[str, Any]:
    _bundle, _tools, tasks = _load_environment(environment_dir)
    task = _find_task(tasks, task_id)
    session = {
        "task_id": task_id,
        "run_id": run_id,
        "max_steps": max_steps,
        "request": task["request"],
        "required_answer_sections": [
            {"name": slot["name"], "description": slot.get("description", "")}
            for slot in task.get("validation", {}).get("answer_slots", [])
        ],
        "calls": [],
        "final_answer": None,
        "judgment": None,
    }
    write_json(_session_path(environment_dir, task_id, run_id), session)
    return {
        "task_id": task_id,
        "run_id": run_id,
        "request": task["request"],
        "required_answer_sections": session["required_answer_sections"],
        "tools": task["available_tools"],
        "max_steps": max_steps,
    }


def call(environment_dir: Path, task_id: str, run_id: int, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    bundle, tools, _tasks = _load_environment(environment_dir)
    path = _session_path(environment_dir, task_id, run_id)
    session = _load_json(path)
    if session.get("final_answer") is not None:
        raise ValueError("This rollout is already finished")
    if len(session["calls"]) >= int(session["max_steps"]):
        raise ValueError("This rollout has exhausted its tool-call budget")
    runtime = LocalToolRuntime(bundle, tools)
    for previous in session["calls"]:
        runtime.call(previous["tool"], previous["arguments"])
    result = runtime.call(tool_name, arguments)
    session["calls"].append({"tool": tool_name, "arguments": arguments, "result": result})
    write_json(path, session)
    return {"step": len(session["calls"]), "result": result, "remaining_steps": int(session["max_steps"]) - len(session["calls"])}


def finish(environment_dir: Path, task_id: str, run_id: int, answer: dict[str, Any]) -> dict[str, Any]:
    bundle, tools, tasks = _load_environment(environment_dir)
    task = _find_task(tasks, task_id)
    path = _session_path(environment_dir, task_id, run_id)
    session = _load_json(path)
    judgment = FiveRunVerifier._judge(task, task["reference_execution"], session["calls"], answer)
    outcome = task.get("validation", {}).get("outcome", {})
    if judgment.get("passed") and outcome:
        runtime = LocalToolRuntime(bundle, tools)
        for previous in session["calls"]:
            runtime.call(previous["tool"], previous["arguments"])
        state_judgment = runtime.check_outcome(outcome)
        if not state_judgment["passed"]:
            judgment = {"passed": False, "reason": "state_or_file_outcome_mismatch", **state_judgment}
    session["final_answer"] = answer
    session["judgment"] = judgment
    session["success"] = bool(judgment["passed"])
    write_json(path, session)
    return {"success": session["success"], "judgment": judgment}


def task_status(environment_dir: Path, task_id: str) -> dict[str, Any]:
    runs = []
    for run_id in range(1, 6):
        path = _session_path(environment_dir, task_id, run_id)
        if path.is_file():
            session = _load_json(path)
            runs.append({
                "run_id": run_id,
                "complete": session.get("judgment") is not None,
                "success": bool(session.get("success")),
                "calls": len(session.get("calls", [])),
            })
        else:
            runs.append({"run_id": run_id, "complete": False, "success": False, "calls": 0})
    complete = [item for item in runs if item["complete"]]
    successes = sum(item["success"] for item in complete)
    failures = len(complete) - successes
    decision = "passed" if successes >= 2 else ("rejected" if failures >= 4 or len(complete) == 5 else "continue")
    return {"task_id": task_id, "runs": runs, "successes": successes, "failures": failures, "decision": decision}


def aggregate(environment_dir: Path) -> dict[str, Any]:
    tasks_payload = _load_json(environment_dir / "tasks.json")
    passed = []
    rejected = list(tasks_payload.get("rejected_tasks", []))
    for task in tasks_payload.get("tasks", []):
        task_id = str(task["task_id"])
        sessions = []
        for run_id in range(1, 6):
            path = _session_path(environment_dir, task_id, run_id)
            if not path.is_file():
                break
            session = _load_json(path)
            if session.get("judgment") is None:
                raise ValueError(f"Incomplete Luna rollout: {task_id} run {run_id}")
            sessions.append(session)
            successes = sum(bool(item.get("success")) for item in sessions)
            failures = len(sessions) - successes
            if successes >= 2 or failures >= 4:
                break
        successes = sum(bool(session.get("success")) for session in sessions)
        failures = len(sessions) - successes
        if successes < 2 and failures < 4 and len(sessions) < 5:
            raise ValueError(f"Undecided Luna rollout: {task_id} has only {len(sessions)} complete runs")
        verification = {
            "status": "passed" if successes >= 2 else "rejected",
            "successes": successes,
            "attempted_runs": len(sessions),
            "decided_early": len(sessions) < 5,
            "solver": "gpt-5.6-luna_subagent",
            "runs": sessions,
        }
        task["validation"]["five_run_verification"] = verification
        if successes >= 2:
            passed.append(task)
        else:
            rejected.append(task)

    verified_tasks = [
        task
        for task in passed + rejected
        if task.get("validation", {}).get("five_run_verification")
    ]
    task_reports = [
        {
            "task_id": task["task_id"],
            "status": task["validation"]["five_run_verification"]["status"],
            "successes": task["validation"]["five_run_verification"]["successes"],
        }
        for task in verified_tasks
    ]
    result = {
        "generation_mode": tasks_payload.get("generation_mode", "luna_reviewed_graph_walks"),
        "tasks": passed,
        "rejected_tasks": rejected,
        "inconclusive_tasks": list(tasks_payload.get("inconclusive_tasks", [])),
    }
    write_json(environment_dir / "tasks.json", result)
    report = {
        "solver": "gpt-5.6-luna_subagent",
        "tasks_before": len(task_reports),
        "tasks_passed": len(passed),
        "tasks_rejected": sum(item["status"] == "rejected" for item in task_reports),
        "task_reports": task_reports,
    }
    write_json(environment_dir / "luna_five_run_report.json", report)
    summary_path = environment_dir / "summary.json"
    summary = _load_json(summary_path)
    summary.update({
        "successful_tasks": len(passed),
        "five_run_attempted_tasks": len(task_reports),
        "five_run_rejected_tasks": sum(item["status"] == "rejected" for item in task_reports),
        "five_run_inconclusive_tasks": 0,
        "five_run_statuses": sorted({item["status"] for item in task_reports}),
        "five_run_solver": "gpt-5.6-luna_subagent",
        "average_successful_task_steps": round(
            sum(task["validation"]["chain_steps"] for task in passed) / len(passed), 2
        ) if passed else 0,
    })
    write_json(summary_path, summary)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Local runtime bridge for Luna five-run rollouts")
    parser.add_argument("command", choices=("list", "start", "call", "finish", "status", "aggregate"))
    parser.add_argument("environment_dir", type=Path)
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("run_id", nargs="?", type=int)
    parser.add_argument("--tool")
    parser.add_argument("--json", default="{}", help="JSON object containing call arguments or the final answer")
    parser.add_argument("--max-steps", type=int, default=14)
    args = parser.parse_args()
    if args.command == "list":
        result = list_tasks(args.environment_dir)
    elif args.command == "aggregate":
        result = aggregate(args.environment_dir)
    elif not args.task_id:
        parser.error("task_id is required")
    elif args.command == "status":
        result = task_status(args.environment_dir, args.task_id)
    elif args.run_id is None:
        parser.error("run_id is required")
    elif args.command == "start":
        result = start(args.environment_dir, args.task_id, args.run_id, args.max_steps)
    elif args.command == "call":
        if not args.tool:
            parser.error("--tool is required for call")
        result = call(args.environment_dir, args.task_id, args.run_id, args.tool, json.loads(args.json))
    else:
        result = finish(args.environment_dir, args.task_id, args.run_id, json.loads(args.json))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
