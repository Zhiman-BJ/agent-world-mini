"""Convert official Kimi CLI evidence into the stable trajectory contract."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
import uuid


SCHEMA_VERSION = "agent_world.kimi_trajectory.v1"


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} must contain a JSON object")
        value["_line"] = number
        records.append(value)
    return records


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _record_ref(path: Path, record: dict[str, Any], root: Path) -> dict[str, Any]:
    clean = {key: value for key, value in record.items() if key != "_line"}
    return {
        "path": path.relative_to(root).as_posix(),
        "line": record["_line"],
        "sha256": hashlib.sha256(_canonical(clean)).hexdigest(),
    }


def _artifact_manifest(raw_dir: Path, root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(candidate for candidate in raw_dir.rglob("*") if candidate.is_file()):
        data = path.read_bytes()
        artifacts.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
    return artifacts


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _new_step(index: int, event: dict[str, Any], wire_line: int) -> dict[str, Any]:
    return {
        "index": index,
        "turn_id": _integer(event.get("turnId")),
        "step": _integer(event.get("step"), index + 1),
        "step_id": event.get("uuid"),
        "started_event_sequence": wire_line,
        "reasoning": {"text": "", "event_sequences": []},
        "assistant_text": "",
        "tool_calls": [],
        "model_exchanges": [],
        "retries": [],
        "finish": None,
    }


def _tool_kind(name: str) -> str:
    return "harness_support" if name.endswith("__read_tool_result") else "environment_mcp"


def _short_tool_name(name: str) -> str:
    marker = "mcp__agent_world_distill__"
    return name[len(marker):] if name.startswith(marker) else name


def _attach_tool_records(
    steps: Iterable[dict[str, Any]],
    environment_records: list[dict[str, Any]],
    support_records: list[dict[str, Any]],
) -> None:
    unused_environment = list(environment_records)
    unused_support = list(support_records)
    for step in steps:
        for call in step["tool_calls"]:
            short_name = _short_tool_name(call["name"])
            records = unused_support if call["tool_kind"] == "harness_support" else unused_environment
            match = next((
                index for index, record in enumerate(records)
                if record.get("tool") == short_name and record.get("arguments") == call.get("arguments")
            ), None)
            record = records.pop(match) if match is not None else None
            clean = {key: value for key, value in record.items() if key != "_line"} if record else None
            call["support_record" if call["tool_kind"] == "harness_support" else "environment_record"] = clean
            call.setdefault("environment_record", None)
            call.setdefault("support_record", None)


def _parse_sse(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", ""
    reasoning, text = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            if isinstance(event.get("delta"), str):
                reasoning.append(event["delta"])
        elif event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            text.append(event["delta"])
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            for key in ("reasoning_content", "reasoning"):
                if isinstance(delta.get(key), str):
                    reasoning.append(delta[key])
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
    return "".join(reasoning), "".join(text)


def _exchange(
    request: dict[str, Any],
    responses: dict[int, dict[str, Any]],
    raw_dir: Path,
    root: Path,
) -> dict[str, Any]:
    response = responses.get(request["request_id"])
    return {
        "request_id": request["request_id"],
        "kind": request.get("kind", "other"),
        "request": _record_ref(raw_dir / "model_requests.jsonl", request, root),
        "response": _record_ref(raw_dir / "model_responses.jsonl", response, root) if response else None,
    }


def export_trajectory(
    raw_dir: Path,
    output_path: Path,
    *,
    task: dict[str, Any],
    environment: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    trajectory_id: str | None = None,
) -> dict[str, Any]:
    """Build one training record while retaining every raw artifact by reference."""
    raw_dir = raw_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    root = output_path.parent
    if not raw_dir.is_dir() or raw_dir.parent != root:
        raise ValueError("raw_dir must be the raw/ directory beside trajectory.json")

    wire_records = _load_jsonl(raw_dir / "wire.jsonl")
    request_records = _load_jsonl(raw_dir / "model_requests.jsonl")
    response_records = _load_jsonl(raw_dir / "model_responses.jsonl")
    environment_records = _load_jsonl(raw_dir / "environment_tool_calls.jsonl")
    support_records = _load_jsonl(raw_dir / "result_reads.jsonl")
    run_result = _load_json(raw_dir / "run_result.json", {})

    steps: list[dict[str, Any]] = []
    steps_by_uuid: dict[str, dict[str, Any]] = {}
    current_by_turn: dict[int, dict[str, Any]] = {}
    calls: dict[str, dict[str, Any]] = {}
    compactions: list[dict[str, Any]] = []
    active_compaction: dict[str, Any] | None = None

    for record in wire_records:
        record_type = record.get("type")
        wire_line = record["_line"]
        event = record.get("event") if record_type == "context.append_loop_event" else None
        if isinstance(event, dict):
            event_type = event.get("type")
            turn_id = _integer(event.get("turnId"))
            if event_type == "step.begin":
                step = _new_step(len(steps), event, wire_line)
                steps.append(step)
                current_by_turn[turn_id] = step
                if isinstance(event.get("uuid"), str):
                    steps_by_uuid[event["uuid"]] = step
            elif event_type == "content.part":
                step = steps_by_uuid.get(event.get("stepUuid")) or current_by_turn.get(turn_id)
                part = event.get("part", {})
                if step is not None and part.get("type") == "text" and isinstance(part.get("text"), str):
                    step["assistant_text"] += part["text"]
            elif event_type == "tool.call":
                step = steps_by_uuid.get(event.get("stepUuid")) or current_by_turn.get(turn_id)
                if step is not None:
                    name = event.get("name", "")
                    call = {
                        "call_id": event.get("toolCallId", ""),
                        "name": name,
                        "tool_kind": _tool_kind(name),
                        "arguments": event.get("args"),
                        "output": None,
                        "is_error": False,
                        "started_event_sequence": wire_line,
                    }
                    step["tool_calls"].append(call)
                    calls[call["call_id"]] = call
            elif event_type == "tool.result":
                call = calls.get(event.get("toolCallId"))
                if call is not None:
                    result = event.get("result")
                    call["output"] = result
                    call["is_error"] = bool(event.get("isError") or (
                        isinstance(result, dict) and result.get("isError") is True
                    ))
                    call["completed_event_sequence"] = wire_line
            elif event_type == "step.end":
                step = steps_by_uuid.get(event.get("uuid")) or current_by_turn.get(turn_id)
                if step is not None:
                    step["finish"] = {
                        key: value for key, value in event.items()
                        if key not in {"type", "uuid", "turnId", "step", "stepUuid"}
                    }
                    step["completed_event_sequence"] = wire_line
        elif record_type == "compaction.started":
            active_compaction = {
                "index": len(compactions),
                "trigger": record.get("trigger", "auto"),
                "started_event_sequence": wire_line,
                "completed_event_sequence": None,
                "status": "started",
                "summary": None,
                "context_summary": None,
                "tokens_before": None,
                "tokens_after": None,
                "compacted_count": None,
                "before_context": None,
                "after_context": None,
                "model_exchanges": [],
            }
            compactions.append(active_compaction)
        elif record_type in {"compaction.completed", "compaction.cancelled"} and active_compaction is not None:
            result = record.get("result", {})
            active_compaction.update({
                "completed_event_sequence": wire_line,
                "status": "completed" if record_type.endswith("completed") else "cancelled",
                "summary": result.get("summary"),
                "context_summary": result.get("contextSummary"),
                "tokens_before": result.get("tokensBefore"),
                "tokens_after": result.get("tokensAfter"),
                "compacted_count": result.get("compactedCount"),
            })
            active_compaction = None
        elif record_type == "context.apply_compaction" and compactions:
            compaction = compactions[-1]
            compaction["summary"] = record.get("summary", compaction.get("summary"))
            compaction["compacted_count"] = record.get("compactedCount", compaction.get("compacted_count"))

    response_by_id = {
        record["request_id"]: record for record in response_records
        if isinstance(record.get("request_id"), int)
    }
    step_lookup = {(step["turn_id"], step["step"]): step for step in steps}
    last_compaction: dict[str, Any] | None = None
    for request in request_records:
        exchange = _exchange(request, response_by_id, raw_dir, root)
        response = response_by_id.get(request["request_id"])
        reasoning, text = "", ""
        if response and isinstance(response.get("body_path"), str):
            reasoning, text = _parse_sse(root / response["body_path"])
        if request.get("kind") == "compaction":
            if not compactions or all(c["model_exchanges"] for c in compactions):
                compactions.append({
                    "index": len(compactions), "trigger": "auto",
                    "started_event_sequence": request.get("wire_line") or request["request_id"],
                    "completed_event_sequence": None, "status": "completed",
                    "summary": text or None, "context_summary": None,
                    "tokens_before": None, "tokens_after": None, "compacted_count": None,
                    "before_context": None, "after_context": None, "model_exchanges": [],
                })
            last_compaction = next((c for c in compactions if not c["model_exchanges"]), compactions[-1])
            last_compaction["model_exchanges"].append(exchange)
            last_compaction["before_context"] = exchange["request"]
            if text and not last_compaction.get("summary"):
                last_compaction["summary"] = text
            continue
        step = step_lookup.get((request.get("turn_id"), request.get("step")))
        if step is None:
            step = next((candidate for candidate in steps if not candidate["model_exchanges"]), None)
        if step is not None:
            step["model_exchanges"].append(exchange)
            if reasoning:
                step["reasoning"]["text"] += reasoning
                step["reasoning"]["event_sequences"].append(
                    request.get("wire_line") or request["request_id"]
                )
            if text and not step["assistant_text"]:
                step["assistant_text"] = text
            if last_compaction is not None and last_compaction.get("after_context") is None:
                last_compaction["after_context"] = exchange["request"]
                last_compaction = None

    _attach_tool_records(steps, environment_records, support_records)
    model = run_result.get("model", {})
    tool_names = environment.get("tool_names")
    if not isinstance(tool_names, list):
        tool_names = sorted({
            record.get("tool") for record in environment_records if isinstance(record.get("tool"), str)
        })
    session_export = raw_dir / "kimi_session.zip"
    trajectory = {
        "schema_version": SCHEMA_VERSION,
        "trajectory_id": trajectory_id or str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "alias": model.get("alias", "unknown"),
            "upstream_id": model.get("upstream_id", "unknown"),
            "display_name": model.get("display_name"),
            "provider": model.get("provider"),
            "max_context_size": model.get("max_context_size"),
            "max_output_size": model.get("max_output_size"),
            "reasoning_effort": model.get("reasoning_effort"),
        },
        "task": task,
        "environment": {**environment, "tool_names": sorted(tool_names)},
        "steps": steps,
        "compactions": compactions,
        "outcome": {
            "status": run_result.get("reason", "unknown"),
            "final_answer": run_result.get("answer", ""),
            "usage": run_result.get("usage"),
            "evaluation": evaluation,
            **({"error": run_result["error"]} if run_result.get("error") else {}),
        },
        "reasoning_preservation": {
            "thinking_deltas": any(step["reasoning"]["event_sequences"] for step in steps),
            "raw_model_responses": bool(response_records),
            "model_input_contexts": bool(request_records),
            "context_snapshots": any(
                compaction.get("before_context") or compaction.get("after_context")
                for compaction in compactions
            ),
            "session_export": session_export.is_file(),
        },
        "artifacts": _artifact_manifest(raw_dir, root),
    }
    output_path.write_text(json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return trajectory


def validate_trajectory(trajectory: dict[str, Any]) -> None:
    """Validate one exported trajectory against the versioned JSON Schema."""
    from jsonschema import Draft202012Validator, FormatChecker

    schema_path = Path(__file__).with_name("trajectory.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(trajectory)
