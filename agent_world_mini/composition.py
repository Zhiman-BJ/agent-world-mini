from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .io_utils import write_json
from .llm import LLMClient
from .models import ResearchBundle, ToolSpec
from .runtime import LocalToolRuntime
from .tasks import TaskSynthesizer


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _entity_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(value["entity_id"])} if value.get("entity_id") is not None else set()
        for child in value.values():
            found.update(_entity_ids(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_entity_ids(child))
        return found
    return set()


def _call_signature(call: dict[str, Any]) -> tuple[str, str]:
    return str(call["tool"]), json.dumps(call.get("arguments", {}), sort_keys=True)


def _merged_calls(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    merged = []
    seen: set[tuple[str, str]] = set()
    for task in (left, right):
        for call in task.get("validation", {}).get("reference_calls", []):
            signature = _call_signature(call)
            if signature in seen:
                continue
            seen.add(signature)
            merged.append({"tool": call["tool"], "arguments": call.get("arguments", {})})
    return merged


def pair_tasks(tasks: list[dict[str, Any]], max_steps: int = 14) -> list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]]:
    candidates = []
    for left_index, left in enumerate(tasks):
        left_ids = _entity_ids(left.get("reference_execution", {}).get("trace", []))
        left_signatures = {_call_signature(call) for call in left.get("validation", {}).get("reference_calls", [])}
        for right in tasks[left_index + 1:]:
            overlap = left_ids & _entity_ids(right.get("reference_execution", {}).get("trace", []))
            if not overlap or left.get("request") == right.get("request"):
                continue
            right_signatures = {_call_signature(call) for call in right.get("validation", {}).get("reference_calls", [])}
            if not left_signatures - right_signatures or not right_signatures - left_signatures:
                continue
            calls = _merged_calls(left, right)
            if len(calls) > max_steps:
                continue
            complement = min(len(left_signatures - right_signatures), len(right_signatures - left_signatures))
            candidates.append((len(overlap), complement, len(calls), str(left["task_id"]), str(right["task_id"]), left, right, calls))
    candidates.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4]))
    used: set[str] = set()
    pairs = []
    for _overlap, _complement, _length, left_id, right_id, left, right, calls in candidates:
        if left_id in used or right_id in used:
            continue
        used.update((left_id, right_id))
        pairs.append((left, right, calls))
    return pairs


def export_packet(environment_dir: Path) -> dict[str, Any]:
    bundle = ResearchBundle.from_dict(_load(environment_dir / "research_bundle.json"))
    tools = [ToolSpec(**item) for item in _load(environment_dir / "tool_specs.json").get("tools", [])]
    tasks = _load(environment_dir / "tasks.json").get("tasks", [])
    runtime = LocalToolRuntime(bundle.records, tools)
    compositions = []
    for index, (left, right, calls) in enumerate(pair_tasks(tasks), start=1):
        execution = runtime.execute(calls)
        compositions.append({
            "composition_id": f"composition_{index:03d}",
            "source_task_ids": [left["task_id"], right["task_id"]],
            "source_requests": [left["request"], right["request"]],
            "executed_trace": execution["trace"],
            "calls": calls,
        })
    packet = {
        "theme": bundle.theme,
        "review_agent": "gpt-5.6-luna",
        "instructions": [
            "Keep a composition only when the two source tasks concern the same real subject and form one useful multi-part request.",
            "Preserve meaningful context, comparison, provenance, and related-entity branches; do not keep mechanical detours.",
            "Use only provided step indices and facts. Do not add or modify tools, arguments, calls, or answers.",
            "Write one concise self-contained request without tool names, APIs, schemas, or internal IDs.",
            "If the two objectives cannot be combined naturally, omit the composition instead of shortening it to one source task.",
            "Answer-slot step indices must cover exactly the kept steps.",
        ],
        "return_format": {
            "reviews": [{
                "composition_id": "composition_001",
                "keep_step_indices": [0, 1],
                "request": "One natural multi-part request",
                "answer_slots": [{"name": "result", "description": "Requested result", "step_indices": [0, 1]}],
                "rubric": ["Uses the observed evidence"],
            }],
        },
        "compositions": compositions,
    }
    write_json(environment_dir / "luna_composition_packet.json", packet)
    return {"source_tasks": len(tasks), "composition_candidates": len(compositions)}


def import_reviews(environment_dir: Path, reviews_path: Path) -> dict[str, Any]:
    bundle = ResearchBundle.from_dict(_load(environment_dir / "research_bundle.json"))
    tools = [ToolSpec(**item) for item in _load(environment_dir / "tool_specs.json").get("tools", [])]
    tasks_payload = _load(environment_dir / "tasks.json")
    packet = _load(environment_dir / "luna_composition_packet.json")
    review_payload = _load(reviews_path)
    by_id = {item["composition_id"]: item for item in packet.get("compositions", [])}
    reviews = {
        str(item.get("composition_id")): item
        for item in review_payload.get("reviews", [])
        if isinstance(item, dict) and item.get("composition_id")
    }
    synthesizer = TaskSynthesizer(LLMClient())
    runtime = LocalToolRuntime(bundle.records, tools)
    existing = list(tasks_payload.get("tasks", []))
    seen_requests = {str(task.get("request")) for task in existing}
    accepted = []
    rejected = 0
    for composition_id, item in by_id.items():
        review = reviews.get(composition_id)
        if review is None:
            continue
        causal_core = [
            {"tool": call["tool"], "arguments": call.get("arguments", {}), "argument_provenance": {}}
            for call in item["calls"]
        ]
        task = synthesizer._task_from_review(tools, runtime, {"causal_core": causal_core}, len(existing) + len(accepted) + 1, review)
        if task is None or task.request in seen_requests:
            rejected += 1
            continue
        seen_requests.add(task.request)
        task.validation["composition_source_task_ids"] = item["source_task_ids"]
        accepted.append(task.to_dict())
    combined = existing + accepted
    for index, task in enumerate(combined, start=1):
        task["task_id"] = f"task_{index:03d}"
    tasks_payload["tasks"] = combined
    tasks_payload["generation_mode"] = "luna_reviewed_graph_walks_with_related_compositions"
    write_json(environment_dir / "tasks.json", tasks_payload)
    report = {
        "composition_candidates": len(by_id),
        "submitted_reviews": len(reviews),
        "accepted_compositions": len(accepted),
        "rejected_reviews": rejected,
        "total_tasks": len(combined),
    }
    write_json(environment_dir / "luna_composition_result.json", report)
    summary_path = environment_dir / "summary.json"
    summary = _load(summary_path)
    summary.update({
        "successful_tasks": len(combined),
        "task_generation_mode": tasks_payload["generation_mode"],
        "average_successful_task_steps": round(
            sum(task["validation"]["chain_steps"] for task in combined) / len(combined), 2
        ) if combined else 0,
        "luna_composition": report,
    })
    write_json(summary_path, summary)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compose related, already verified Luna tasks")
    parser.add_argument("command", choices=("export", "import"))
    parser.add_argument("environment_dir", type=Path)
    parser.add_argument("reviews", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        result = export_packet(args.environment_dir)
    else:
        if args.reviews is None:
            parser.error("reviews path is required for import")
        result = import_reviews(args.environment_dir, args.reviews)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
