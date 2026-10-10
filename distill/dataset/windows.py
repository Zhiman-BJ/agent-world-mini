#!/usr/bin/env python3
"""Build complete model-visible training windows from compaction sessions."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "standard_trajectory.schema.json"




def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not an object")
            yield value


def write_line(stream: Any, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_model_request(path: Path) -> dict[str, Any]:
    candidates = [
        row for row in jsonl(path)
        if isinstance(row.get("request"), dict) and isinstance(row.get("request_id"), int)
    ]
    if not candidates:
        raise ValueError(f"no model request in {path}")
    return min(candidates, key=lambda row: row["request_id"])


def source_id(record: dict[str, Any]) -> str:
    trajectory_id = str(record["trajectory_id"])
    return trajectory_id.split("::compaction_", 1)[0]


def assistant_message(step: dict[str, Any]) -> dict[str, Any]:
    calls = step.get("tool_calls", [])
    message: dict[str, Any] = {
        "role": "assistant",
        "content": step.get("assistant_text") or None,
        "reasoning_content": (step.get("reasoning") or {}).get("text") or "",
    }
    if calls:
        message["tool_calls"] = [
            {
                "id": call["call_id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(
                        call.get("arguments"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in calls
        ]
    return message


def step_messages(step: dict[str, Any]) -> list[dict[str, Any]]:
    messages = [assistant_message(step)]
    for call in step.get("tool_calls", []):
        output = call.get("output")
        content = (
            output
            if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        )
        messages.append({
            "role": "tool",
            "tool_call_id": call["call_id"],
            "content": content,
        })
    return messages


def step_annotation(call: dict[str, Any]) -> dict[str, Any]:
    output = call.get("output")
    return {
        "name": call["name"],
        "tool_kind": call.get("tool_kind", "unknown"),
        "is_error": bool(call.get("is_error")),
        "output_encoding": "text" if isinstance(output, str) else "json",
    }


def message_call_ids(messages: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls", []) or []:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                result.add(call["id"])
        if isinstance(message.get("tool_call_id"), str):
            result.add(message["tool_call_id"])
    return result


def request_id_for_step(step: dict[str, Any]) -> int:
    request_ids = [
        exchange.get("request_id")
        for exchange in step.get("model_exchanges", [])
        if isinstance(exchange, dict)
        and exchange.get("kind") == "agent_step"
        and isinstance(exchange.get("request_id"), int)
    ]
    if not request_ids:
        raise ValueError(f"step {step.get('step_id')} has no agent request ID")
    # Retries can leave more than one exchange on a logical step. The final
    # request produced the response represented by this step.
    return max(request_ids)


def validate_conversation(record: dict[str, Any]) -> None:
    tools = {
        item.get("function", {}).get("name")
        for item in record.get("tools", [])
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    issued: dict[str, int] = {}
    resolved: set[str] = set()
    assistant_count = 0
    for index, message in enumerate(record["messages"]):
        role = message.get("role")
        if role == "assistant":
            assistant_count += 1
        for call in message.get("tool_calls", []) or []:
            call_id = call.get("id")
            function = call.get("function", {})
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(call_id, str) or not call_id:
                raise ValueError(f"invalid call ID in {record['trajectory_id']}")
            if call_id in issued:
                raise ValueError(f"duplicate call ID {call_id} in {record['trajectory_id']}")
            if name not in tools:
                raise ValueError(
                    f"undefined tool {name!r} in {record['trajectory_id']}"
                )
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                json.loads(arguments)
            issued[call_id] = index
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in issued:
                raise ValueError(
                    f"tool result without preceding call {call_id!r} in "
                    f"{record['trajectory_id']}"
                )
            if call_id in resolved:
                raise ValueError(
                    f"duplicate tool result {call_id!r} in {record['trajectory_id']}"
                )
            resolved.add(call_id)
    if assistant_count == 0:
        raise ValueError(f"window has no assistant target: {record['trajectory_id']}")
    unresolved = set(issued) - resolved
    if unresolved:
        raise ValueError(
            f"unresolved tool calls in {record['trajectory_id']}: {sorted(unresolved)}"
        )


def make_record(
    base: dict[str, Any],
    trajectory_id: str,
    case: str,
    window_index: int,
    window_kind: str,
    compaction_index: int | None,
    start_request_id: int,
    end_request_id_exclusive: int | None,
    input_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    selected_steps: list[tuple[int, dict[str, Any]]],
    all_annotations: dict[str, dict[str, Any]],
    evidence_ref: str,
) -> dict[str, Any]:
    messages = deepcopy(input_messages)
    for _, step in selected_steps:
        messages.extend(step_messages(step))
    present_ids = message_call_ids(messages)
    annotations = {
        call_id: deepcopy(annotation)
        for call_id, annotation in all_annotations.items()
        if call_id in present_ids
    }
    normalizations = set(base["provenance"].get("normalizations", []))
    normalizations.discard("single_assistant_continuation_target")
    normalizations.update({
        "compaction_windows_split_at_real_model_input_boundaries",
        "all_assistant_turns_retained_within_window",
        "full_pre_compaction_history_not_duplicated_after_compaction",
    })
    if window_kind == "pre_compaction":
        suffix = f"window_{window_index:03d}_pre_compaction"
        normalizations.add("complete_pre_compaction_window")
    else:
        if compaction_index is None:
            raise ValueError("post-compaction window lacks compaction index")
        suffix = (
            f"window_{window_index:03d}_after_compaction_"
            f"{compaction_index:03d}"
        )
        normalizations.add("real_post_compaction_model_input")
        normalizations.add("complete_post_compaction_window")
    record = {
        "schema_version": base["schema_version"],
        "message_format": base["message_format"],
        "trajectory_id": f"{trajectory_id}::{suffix}",
        "model": deepcopy(base["model"]),
        "task": deepcopy(base["task"]),
        "environment": deepcopy(base["environment"]),
        "tools": deepcopy(tools),
        "messages": messages,
        "tool_call_annotations": annotations,
        "evaluation": {
            "outcome": "pass",
            "source": "canonical_verifier",
            "category": "verifier_pass_compaction_window",
        },
        "quality_flags": deepcopy(base.get("quality_flags", [])),
        "provenance": {
            "case": case,
            "evidence_ref": evidence_ref,
            "source_trajectory_sha256": base["provenance"][
                "source_trajectory_sha256"
            ],
            "cleaned_trajectory_sha256": base["provenance"][
                "cleaned_trajectory_sha256"
            ],
            "cleaned_input_sha256": value_sha256({
                "messages": input_messages,
                "tools": tools,
                "window_kind": window_kind,
                "start_request_id": start_request_id,
                "end_request_id_exclusive": end_request_id_exclusive,
            }),
            "raw_model_evidence_available": True,
            "reasoning_preserved": True,
            "normalizations": sorted(normalizations),
        },
    }
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    dataset = args.dataset.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".{output.name}.building-", dir=output.parent))

    from . import training as builder
    validator = Draft202012Validator(load(SCHEMA_PATH))
    counts: Counter[str] = Counter()

    continuation_records = list(
        jsonl(source / "compaction_continuation_trajectories.jsonl")
    )
    bases: dict[str, dict[str, Any]] = {}
    source_cases: dict[str, str] = {}
    for record in continuation_records:
        sid = source_id(record)
        case = str(record.get("provenance", {}).get("case", ""))
        if sid in source_cases and source_cases[sid] != case:
            raise ValueError(f"case mismatch for {sid}")
        source_cases[sid] = case
        bases.setdefault(sid, record)

    contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in jsonl(dataset / "post_compaction_contexts.jsonl"):
        sid = str(row.get("trajectory_id", ""))
        if sid not in bases or not isinstance(row.get("model_input"), dict):
            continue
        request_id = row.get("model_request_record", {}).get("request_id")
        if not isinstance(request_id, int):
            continue
        contexts[sid].append(row)

    expected_context_count = len(continuation_records)
    actual_context_count = sum(len(rows) for rows in contexts.values())
    if actual_context_count != expected_context_count:
        raise ValueError(
            f"expected {expected_context_count} recoverable contexts, found "
            f"{actual_context_count}"
        )

    windows_path = build / "compaction_window_trajectories_strict.jsonl"
    audit_path = build / "compaction_window_audit.jsonl"
    index_path = build / "training_index.jsonl"
    generated: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []

    for sid in sorted(bases):
        base = bases[sid]
        case = source_cases[sid]
        task_dir = dataset / "canonical/verifier_pass" / case
        trajectory_path = task_dir / "trajectory.json"
        request_path = task_dir / "raw/model_requests.jsonl"
        if not trajectory_path.is_file() or not request_path.is_file():
            raise ValueError(f"missing canonical source for {case}")
        trajectory = load(trajectory_path)
        if trajectory.get("trajectory_id") != sid:
            raise ValueError(f"canonical trajectory mismatch for {case}")
        request_record = first_model_request(request_path)
        request = request_record["request"]
        cleaner = builder.Cleaner(trajectory, request, case)
        cleaned = builder.cleaned_trajectory_view(trajectory, cleaner)
        source_steps = trajectory.get("steps", [])
        cleaned_steps = cleaned.get("steps", [])
        if len(source_steps) != len(cleaned_steps):
            raise ValueError(f"step count mismatch for {case}")

        indexed_steps: list[tuple[int, dict[str, Any]]] = []
        all_annotations: dict[str, dict[str, Any]] = {}
        for source_step, cleaned_step in zip(source_steps, cleaned_steps, strict=True):
            request_id = request_id_for_step(source_step)
            indexed_steps.append((request_id, cleaned_step))
            for call in cleaned_step.get("tool_calls", []):
                call_id = str(call["call_id"])
                if call_id in all_annotations:
                    raise ValueError(f"duplicate cleaned call ID {call_id} in {case}")
                all_annotations[call_id] = step_annotation(call)
        if [item[0] for item in indexed_steps] != sorted(item[0] for item in indexed_steps):
            raise ValueError(f"non-monotonic step request IDs for {case}")

        context_rows = sorted(
            contexts[sid],
            key=lambda row: int(row["model_request_record"]["request_id"]),
        )
        start_ids = [
            int(row["model_request_record"]["request_id"])
            for row in context_rows
        ]
        if len(start_ids) != len(set(start_ids)):
            raise ValueError(f"duplicate compaction start request for {case}")
        step_ids = {item[0] for item in indexed_steps}
        missing_targets = sorted(set(start_ids) - step_ids)
        if missing_targets:
            raise ValueError(
                f"post-compaction request has no assistant step in {case}: "
                f"{missing_targets}"
            )

        initial_messages = builder.cleaned_initial_messages(
            request.get("messages", []),
            cleaner,
            str(trajectory["task"]["prompt"]),
            str(trajectory["task"].get("task_text", "")),
        )
        initial_tools = builder.cleaned_tools(request.get("tools", []), cleaner)

        boundaries: list[tuple[str, int | None, int, list[dict[str, Any]], list[dict[str, Any]], str]] = [
            (
                "pre_compaction",
                None,
                min(item[0] for item in indexed_steps),
                initial_messages,
                initial_tools,
                f"{trajectory_path}#before_first_compaction",
            )
        ]
        for row in context_rows:
            normalized = builder.normalized_model_input(row["model_input"], cleaner)
            boundaries.append((
                "post_compaction",
                int(row["compaction_index"]),
                int(row["model_request_record"]["request_id"]),
                deepcopy(normalized.get("messages", [])),
                deepcopy(normalized.get("tools", [])),
                (
                    f"{dataset / 'post_compaction_contexts.jsonl'}#"
                    f"trajectory_id={sid}&compaction_index={row['compaction_index']}"
                ),
            ))

        assigned_step_ids: list[int] = []
        for window_index, boundary in enumerate(boundaries):
            kind, compaction_index, start_id, input_messages, tools, evidence = boundary
            end_id = (
                boundaries[window_index + 1][2]
                if window_index + 1 < len(boundaries)
                else None
            )
            selected = [
                item for item in indexed_steps
                if item[0] >= start_id and (end_id is None or item[0] < end_id)
            ]
            if not selected:
                raise ValueError(
                    f"empty context window {window_index} for {case}: "
                    f"[{start_id}, {end_id})"
                )
            assigned_step_ids.extend(item[0] for item in selected)
            record = make_record(
                base=base,
                trajectory_id=sid,
                case=case,
                window_index=window_index,
                window_kind=kind,
                compaction_index=compaction_index,
                start_request_id=start_id,
                end_request_id_exclusive=end_id,
                input_messages=input_messages,
                tools=tools,
                selected_steps=selected,
                all_annotations=all_annotations,
                evidence_ref=evidence,
            )
            validator.validate(record)
            validate_conversation(record)
            generated.append(record)
            roles = Counter(message.get("role") for message in record["messages"])
            audit = {
                "training_id": record["trajectory_id"],
                "source_trajectory_id": sid,
                "case": case,
                "window_index": window_index,
                "window_kind": kind,
                "compaction_index": compaction_index,
                "start_request_id": start_id,
                "end_request_id_exclusive": end_id,
                "assistant_targets": roles["assistant"],
                "tool_results": roles["tool"],
                "message_count": len(record["messages"]),
                "tool_call_count": sum(
                    len(message.get("tool_calls", []) or [])
                    for message in record["messages"]
                ),
                "ends_with_role": record["messages"][-1].get("role"),
            }
            audits.append(audit)
            counts["compaction_windows"] += 1
            counts[f"{kind}_windows"] += 1
            counts["assistant_targets_in_windows"] += roles["assistant"]
            counts["tool_results_in_windows"] += roles["tool"]
        expected_step_ids = [item[0] for item in indexed_steps]
        if assigned_step_ids != expected_step_ids:
            raise ValueError(
                f"window assignment did not partition steps for {case}: "
                f"expected={expected_step_ids}, assigned={assigned_step_ids}"
            )
        counts["compacted_source_trajectories"] += 1

    with windows_path.open("w", encoding="utf-8") as stream:
        for record in generated:
            write_line(stream, record)
    with audit_path.open("w", encoding="utf-8") as stream:
        for row in audits:
            write_line(stream, row)

    strict_output = build / "training_trajectories_strict.jsonl"
    with (
        (source / "training_trajectories_strict.jsonl").open(
            encoding="utf-8"
        ) as source_stream,
        strict_output.open("w", encoding="utf-8") as output_stream,
        index_path.open("w", encoding="utf-8") as index_stream,
    ):
        for line in source_stream:
            if not line.strip():
                continue
            record = json.loads(line)
            if "::compaction_" in str(record.get("trajectory_id", "")):
                counts["single_target_continuations_replaced"] += 1
                continue
            output_stream.write(line)
            write_line(index_stream, {
                "training_id": record["trajectory_id"],
                "source_trajectory_id": record["trajectory_id"],
                "sample_type": "unchanged_strict_record",
                "case": record.get("provenance", {}).get("case"),
            })
            counts["unchanged_strict_records"] += 1
        for record, audit in zip(generated, audits, strict=True):
            output_stream.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            write_line(index_stream, {
                "training_id": record["trajectory_id"],
                "source_trajectory_id": audit["source_trajectory_id"],
                "sample_type": "compaction_window",
                "case": audit["case"],
                "window_index": audit["window_index"],
                "window_kind": audit["window_kind"],
                "compaction_index": audit["compaction_index"],
            })

    counts["strict_training_samples"] = (
        counts["unchanged_strict_records"] + counts["compaction_windows"]
    )
    if counts["single_target_continuations_replaced"] != 72:
        raise ValueError(
            "expected to replace 72 one-turn continuations, got "
            f"{counts['single_target_continuations_replaced']}"
        )
    if counts["compacted_source_trajectories"] != 41:
        raise ValueError(
            "expected 41 compacted source trajectories, got "
            f"{counts['compacted_source_trajectories']}"
        )
    if counts["post_compaction_windows"] != 72:
        raise ValueError(
            f"expected 72 post-compaction windows, got {counts['post_compaction_windows']}"
        )
    if counts["pre_compaction_windows"] != 41:
        raise ValueError(
            f"expected 41 pre-compaction windows, got {counts['pre_compaction_windows']}"
        )

    manifest = {
        "schema_version": "agent_world.compaction_windowed_training_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_release": str(source),
        "canonical_dataset": str(dataset),
        "training_schema": str(SCHEMA_PATH),
        "context_policy": {
            "split_rule": (
                "one non-overlapping training segment before the first compaction and "
                "one complete segment after each recoverable compaction"
            ),
            "assistant_loss": "all assistant messages in every segment",
            "masked_roles": ["system", "developer", "user", "tool"],
            "post_compaction_input": "exact cleaned model input captured by the harness",
            "segment_end": "immediately before the next real post-compaction model input",
        },
        "counts": dict(counts),
        "files": {
            "strict_training": strict_output.name,
            "compaction_windows": windows_path.name,
            "training_index": index_path.name,
            "compaction_window_audit": audit_path.name,
        },
        "sha256": {
            "strict_training": file_sha256(strict_output),
            "compaction_windows": file_sha256(windows_path),
            "training_index": file_sha256(index_path),
            "compaction_window_audit": file_sha256(audit_path),
        },
    }
    (build / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (build / "README.md").write_text(
        '# 标准训练轨迹\n\n本目录包含经过验证、清洗和标准化的工具调用轨迹。\n数量、来源、质量标记、上下文处理策略及文件校验值见 manifest.json。\n训练消费严格视图；审计文件保留原始证据与源轨迹对应关系。\n',
        encoding="utf-8",
    )

    for path in build.iterdir():
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    os.replace(build, output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
