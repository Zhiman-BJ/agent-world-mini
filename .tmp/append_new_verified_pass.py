#!/usr/bin/env python3
"""Clean newly verified PASS trajectories and append them to a consolidated release."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator

from jsonschema import Draft202012Validator


REPO = Path("/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini")
ROOT = Path("/data1/agent_world/aw-data/kimi_k3_complete2710_20261006")
DEFAULT_BASE = ROOT / "training_merged_pass_20261008"
DEFAULT_OUTPUT = ROOT / "training_merged_pass_20261008_plus_incremental"
DEFAULT_SCAN_ROOTS = (
    ROOT / "new570_50x",
    ROOT / "incremental_autofollow_40x",
)
SCHEMA_PATH = REPO / "distill/standard_trajectory.schema.json"
PATH_CLEANER_PATH = REPO / ".tmp/clean_complete2710_paths.py"
TRAINING_BUILDER_PATH = REPO / ".tmp/build_complete2710_training_clean.py"

FORBIDDEN_MODEL_TEXT = (
    "mcp__agent_world_distill__",
    "<mcp-result-extras>",
    "</mcp-result-extras>",
    "structuredContent",
    "/data1/",
    "/tmp/agent-world-kimi-home-",
    "/.kimi/",
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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


def copy_and_append(base: Path, delta: Path, destination: Path) -> None:
    with destination.open("wb") as output:
        with base.open("rb") as source:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        if delta.is_file():
            with delta.open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def accumulate_increment(base: Path, delta: Path, destination: Path) -> None:
    if base.is_file():
        copy_and_append(base, delta, destination)
    else:
        os.replace(delta, destination)


def model_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from model_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from model_strings(item)


def validate_model_surface(record: dict[str, Any]) -> None:
    surface = {
        "task": record.get("task"),
        "tools": record.get("tools"),
        "messages": record.get("messages"),
    }
    residues: dict[str, int] = {}
    for text in model_strings(surface):
        for marker in FORBIDDEN_MODEL_TEXT:
            count = text.count(marker)
            if count:
                residues[marker] = residues.get(marker, 0) + count
    if residues:
        raise ValueError(
            f"model-visible cleaning residues in {record['trajectory_id']}: {residues}"
        )

    defined = {
        item.get("function", {}).get("name")
        for item in record.get("tools", [])
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    issued: dict[str, tuple[str, int]] = {}
    resolved: set[str] = set()
    messages = record.get("messages", [])
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls", []) or []:
            call_id = call.get("id")
            function = call.get("function", {})
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(call_id, str) or not call_id:
                raise ValueError(f"invalid tool call ID in {record['trajectory_id']}")
            if call_id in issued:
                raise ValueError(f"duplicate tool call ID {call_id}")
            if name not in defined:
                raise ValueError(f"undefined tool {name!r} in {record['trajectory_id']}")
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                json.loads(arguments)
            issued[call_id] = (str(name), message_index)
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in issued:
                raise ValueError(
                    f"tool result precedes or lacks call {call_id!r} in "
                    f"{record['trajectory_id']}"
                )
            if call_id in resolved:
                raise ValueError(f"duplicate tool result for {call_id}")
            resolved.add(call_id)
    unresolved = set(issued) - resolved
    if unresolved:
        last_index = len(messages) - 1
        invalid = sorted(
            call_id for call_id in unresolved if issued[call_id][1] != last_index
        )
        if invalid:
            raise ValueError(
                f"unresolved non-target calls in {record['trajectory_id']}: {invalid}"
            )


def existing_cases(training_path: Path) -> set[str]:
    cases: set[str] = set()
    for record in jsonl(training_path):
        case = record.get("provenance", {}).get("case")
        if isinstance(case, str):
            cases.add(case)
    return cases


def task_directory_for_verdict(verdict_path: Path) -> tuple[str, Path] | None:
    if verdict_path.parent.parent.name != "verifiers":
        return None
    case = verdict_path.parent.name
    run_directory = verdict_path.parent.parent.parent
    return case, run_directory / case


def scan_passes(scan_roots: list[Path], excluded_cases: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for root in scan_roots:
        if not root.is_dir():
            continue
        for verdict_path in root.rglob("verdict.json"):
            location = task_directory_for_verdict(verdict_path)
            if location is None:
                continue
            case, task_directory = location
            if case in excluded_cases:
                continue
            try:
                verdict = load(verdict_path)
            except (OSError, json.JSONDecodeError):
                continue
            if verdict.get("outcome") != "pass":
                continue
            trajectory_path = task_directory / "trajectory.json"
            requests_path = task_directory / "raw/model_requests.jsonl"
            if not trajectory_path.is_file() or not requests_path.is_file():
                continue
            candidate = {
                "case": case,
                "task_directory": task_directory,
                "trajectory_path": trajectory_path,
                "requests_path": requests_path,
                "verdict_path": verdict_path,
                "verdict": verdict,
                "verdict_mtime_ns": verdict_path.stat().st_mtime_ns,
            }
            current = selected.get(case)
            if current is None or candidate["verdict_mtime_ns"] > current["verdict_mtime_ns"]:
                selected[case] = candidate
    return selected


def indexed_passes(index_path: Path, excluded_cases: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in jsonl(index_path):
        if row.get("canonical") is not True or row.get("label") != "verifier_pass":
            continue
        case = row.get("case")
        trajectory_value = row.get("trajectory_path")
        verdict_value = row.get("verification", {}).get("verdict")
        if (
            not isinstance(case, str)
            or case in excluded_cases
            or not isinstance(trajectory_value, str)
            or not isinstance(verdict_value, str)
        ):
            continue
        trajectory_path = Path(trajectory_value)
        verdict_path = Path(verdict_value)
        task_directory = trajectory_path.parent
        requests_path = task_directory / "raw/model_requests.jsonl"
        if (
            not trajectory_path.is_file()
            or not verdict_path.is_file()
            or not requests_path.is_file()
        ):
            continue
        verdict = load(verdict_path)
        if verdict.get("outcome") != "pass":
            raise ValueError(f"canonical PASS index points to non-PASS verdict: {verdict_path}")
        if case in selected:
            raise ValueError(f"duplicate canonical PASS case in {index_path}: {case}")
        selected[case] = {
            "case": case,
            "task_directory": task_directory,
            "trajectory_path": trajectory_path,
            "requests_path": requests_path,
            "verdict_path": verdict_path,
            "verdict": verdict,
            "verdict_mtime_ns": verdict_path.stat().st_mtime_ns,
        }
    return selected


def first_request_record(records: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    for record in records:
        if isinstance(record.get("request"), dict):
            return record
    raise ValueError(f"no model request in {path}")


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
                        call.get("arguments"), ensure_ascii=False, separators=(",", ":")
                    ),
                },
            }
            for call in calls
        ]
    return message


def step_by_request_id(
    source_steps: list[dict[str, Any]], cleaned_steps: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if len(source_steps) != len(cleaned_steps):
        raise ValueError("source and cleaned step counts differ")
    result: dict[int, dict[str, Any]] = {}
    for source_step, cleaned_step in zip(source_steps, cleaned_steps, strict=True):
        for exchange in source_step.get("model_exchanges", []):
            if not isinstance(exchange, dict) or exchange.get("kind") != "agent_step":
                continue
            request_id = exchange.get("request_id")
            if isinstance(request_id, int):
                if request_id in result:
                    raise ValueError(f"duplicate agent request_id {request_id}")
                result[request_id] = cleaned_step
    return result


def call_ids(messages: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls", []) or []:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                result.add(call["id"])
        if isinstance(message.get("tool_call_id"), str):
            result.add(message["tool_call_id"])
    return result


def build_base_record(
    candidate: dict[str, Any], path_module: Any, builder: Any,
    deliveries: dict[str, list[Path]], delivery_cache: dict[tuple[str, str], Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = candidate["case"]
    task_directory = candidate["task_directory"]
    raw_trajectory = load(candidate["trajectory_path"])
    request_records = list(jsonl(candidate["requests_path"]))
    first_record = first_request_record(request_records, candidate["requests_path"])
    cwd = path_module.request_cwd(first_record)
    path_cleaner = path_module.PathCleaner(case, task_directory, cwd)
    trajectory = path_cleaner.value(deepcopy(raw_trajectory))
    request = path_cleaner.value(deepcopy(first_record["request"]))
    cleaner = builder.Cleaner(trajectory, request, case)
    cleaned_view = builder.cleaned_trajectory_view(trajectory, cleaner)
    prompt = cleaned_view["task"]["prompt"]
    initial_messages = builder.cleaned_initial_messages(
        request.get("messages", []),
        cleaner,
        str(trajectory["task"]["prompt"]),
        str(trajectory["task"].get("task_text", "")),
    )
    tools = builder.cleaned_tools(request.get("tools", []), cleaner)
    messages, annotations = builder.trajectory_messages(
        initial_messages, cleaned_view, cleaner
    )
    defined_names = {
        item.get("function", {}).get("name")
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    undefined = sorted({
        annotation["name"]
        for annotation in annotations.values()
        if annotation["name"] not in defined_names
    })
    quality_flags = [f"historical_undefined_tool_call:{name}" for name in undefined]
    quality_flags.extend(
        f"mcp_result_parse_error:{item}" for item in cleaner.output_parse_errors
    )

    environment_id = str(trajectory["environment"]["environment_id"])
    expected_tools = set(trajectory["environment"].get("tool_names", []))
    delivery_key = (environment_id, case.split("__", 1)[0])
    if delivery_key not in delivery_cache:
        environment_dir, tool_path = builder.delivery_for(
            deliveries, environment_id, case, expected_tools
        )
        delivery_cache[delivery_key] = (
            environment_dir,
            tool_path,
            builder.environment_metadata(environment_dir, tool_path),
        )
    _, _, delivery = delivery_cache[delivery_key]

    record = {
        "schema_version": "agent_world.standard_trajectory.v1",
        "message_format": "openai.chat_completions.messages.v1+kimi.reasoning_content",
        "trajectory_id": trajectory["trajectory_id"],
        "model": deepcopy(trajectory.get("model", {})),
        "task": {
            "task_id": trajectory.get("task", {}).get("task_id"),
            "task_text": prompt,
        },
        "environment": {"environment_id": environment_id, **delivery},
        "tools": tools,
        "messages": messages,
        "tool_call_annotations": annotations,
        "evaluation": {
            "outcome": "pass",
            "source": "canonical_verifier",
            "category": "incremental_verifier_pass",
        },
        "quality_flags": sorted(set(quality_flags)),
        "provenance": {
            "case": case,
            "evidence_ref": (
                "incremental_source_snapshot.jsonl#"
                f"trajectory_id={trajectory['trajectory_id']}"
            ),
            "source_trajectory_sha256": value_sha256(raw_trajectory),
            "cleaned_trajectory_sha256": value_sha256(cleaned_view),
            "cleaned_input_sha256": value_sha256({
                "messages": initial_messages,
                "tools": tools,
            }),
            "raw_model_evidence_available": True,
            "reasoning_preserved": True,
            "normalizations": [
                "training_task_uses_prompt",
                "mcp_tool_names_canonicalized",
                "mcp_call_ids_canonicalized",
                "long_result_references_canonicalized",
                "mcp_result_wrappers_removed",
                "runtime_paths_virtualized",
                "audit_fields_excluded_from_training_messages",
            ],
        },
    }
    context = {
        "case": case,
        "raw_trajectory": raw_trajectory,
        "trajectory": trajectory,
        "cleaned_view": cleaned_view,
        "request_records": request_records,
        "path_cleaner": path_cleaner,
        "cleaner": cleaner,
        "base_record": record,
    }
    return record, context


def continuation_records(
    context: dict[str, Any], builder: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    trajectory = context["trajectory"]
    cleaned_view = context["cleaned_view"]
    requests = context["request_records"]
    path_cleaner = context["path_cleaner"]
    cleaner = context["cleaner"]
    base = context["base_record"]
    case = context["case"]
    steps = step_by_request_id(trajectory.get("steps", []), cleaned_view.get("steps", []))
    records: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for position, compaction in enumerate(trajectory.get("compactions", [])):
        compaction_index = int(compaction.get("index", position))
        after_context = compaction.get("after_context")
        line = after_context.get("line") if isinstance(after_context, dict) else None
        if not isinstance(line, int) or line < 1 or line > len(requests):
            exclusions.append({
                "trajectory_id": trajectory["trajectory_id"],
                "case": case,
                "compaction_index": compaction_index,
                "reason": "post_compaction_model_input_is_not_available",
            })
            continue
        request_record = requests[line - 1]
        request_id = request_record.get("request_id")
        model_input = request_record.get("request")
        if (
            request_record.get("kind") != "agent_step"
            or not isinstance(request_id, int)
            or not isinstance(model_input, dict)
            or request_id not in steps
        ):
            exclusions.append({
                "trajectory_id": trajectory["trajectory_id"],
                "case": case,
                "compaction_index": compaction_index,
                "request_id": request_id,
                "reason": "post_compaction_target_is_not_recoverable",
            })
            continue

        path_cleaned_input = path_cleaner.value(deepcopy(model_input))
        normalized_input = builder.normalized_model_input(path_cleaned_input, cleaner)
        messages = deepcopy(normalized_input.get("messages", []))
        tools = deepcopy(normalized_input.get("tools", []))
        target_step = steps[request_id]
        target = assistant_message(target_step)
        messages.append(target)
        present_call_ids = call_ids(messages)
        annotations = {
            call_id: deepcopy(annotation)
            for call_id, annotation in base.get("tool_call_annotations", {}).items()
            if call_id in present_call_ids
        }
        for call in target_step.get("tool_calls", []):
            output = call.get("output")
            annotations[call["call_id"]] = {
                "name": call["name"],
                "tool_kind": call.get("tool_kind", "unknown"),
                "is_error": bool(call.get("is_error")),
                "output_encoding": "text" if isinstance(output, str) else "json",
            }
        training_id = (
            f"{trajectory['trajectory_id']}::compaction_{compaction_index:03d}"
        )
        record = {
            "schema_version": base["schema_version"],
            "message_format": base["message_format"],
            "trajectory_id": training_id,
            "model": deepcopy(base["model"]),
            "task": deepcopy(base["task"]),
            "environment": deepcopy(base["environment"]),
            "tools": tools,
            "messages": messages,
            "tool_call_annotations": annotations,
            "evaluation": {
                "outcome": "pass",
                "source": "canonical_verifier",
                "category": "incremental_verifier_pass_compaction_continuation",
            },
            "quality_flags": deepcopy(base.get("quality_flags", [])),
            "provenance": {
                **deepcopy(base["provenance"]),
                "evidence_ref": (
                    "incremental_source_snapshot.jsonl#"
                    f"trajectory_id={trajectory['trajectory_id']}"
                    f"&compaction_index={compaction_index}"
                ),
                "cleaned_input_sha256": value_sha256({
                    "messages": normalized_input.get("messages", []),
                    "tools": tools,
                }),
                "normalizations": sorted(set(
                    base["provenance"]["normalizations"]
                    + [
                        "real_post_compaction_model_input",
                        "single_assistant_continuation_target",
                        "full_pre_compaction_history_excluded",
                    ]
                )),
            },
        }
        records.append(record)
        context_summary = cleaner.training_text(
            str(compaction.get("context_summary") or compaction.get("summary") or "")
        )
        audits.append({
            "training_id": training_id,
            "source_trajectory_id": trajectory["trajectory_id"],
            "case": case,
            "compaction_index": compaction_index,
            "request_id": request_id,
            "input_message_count": len(normalized_input.get("messages", [])),
            "input_chars_with_tools": len(json.dumps(
                {
                    "messages": normalized_input.get("messages", []),
                    "tools": tools,
                },
                ensure_ascii=False,
            )),
            "target_chars": len(json.dumps(target, ensure_ascii=False)),
            "target_tool_call_count": len(target.get("tool_calls", [])),
            "context_summary_sha256": value_sha256(context_summary),
        })
    return records, audits, exclusions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scan-root", type=Path, action="append", dest="scan_roots")
    parser.add_argument(
        "--attempts-index",
        type=Path,
        help="Use canonical verifier PASS selections from an attempts.jsonl index.",
    )
    args = parser.parse_args()

    base = args.base.expanduser().resolve()
    output = args.output.expanduser().resolve()
    scan_roots = [path.expanduser().resolve() for path in (args.scan_roots or DEFAULT_SCAN_ROOTS)]
    attempts_index = args.attempts_index.expanduser().resolve() if args.attempts_index else None
    if attempts_index is not None and not attempts_index.is_file():
        raise SystemExit(f"missing attempts index: {attempts_index}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    for required in (
        "training_trajectories.jsonl",
        "training_trajectories_strict.jsonl",
        "training_index.jsonl",
        "compaction_continuation_trajectories.jsonl",
        "compaction_continuation_audit.jsonl",
        "compaction_audit_exclusions.jsonl",
        "known_quality_exclusions.jsonl",
        "manifest.json",
    ):
        if not (base / required).is_file():
            raise SystemExit(f"missing base file: {base / required}")

    path_module = load_module("incremental_path_cleaner", PATH_CLEANER_PATH)
    builder = load_module("incremental_training_builder", TRAINING_BUILDER_PATH)
    validator = Draft202012Validator(load(SCHEMA_PATH))
    parent_manifest = load(base / "manifest.json")
    seen_cases = existing_cases(base / "training_trajectories.jsonl")
    seen_training_ids = {
        str(record["training_id"])
        for record in jsonl(base / "training_index.jsonl")
    }
    deliveries = builder.delivery_index()
    delivery_cache: dict[tuple[str, str], Any] = {}
    counts: Counter[str] = Counter()

    build_parent = output.parent
    build_parent.mkdir(parents=True, exist_ok=True)
    build_directory = Path(tempfile.mkdtemp(prefix=f".{output.name}.building-", dir=build_parent))
    delta_training = build_directory / ".delta_training.jsonl"
    delta_strict = build_directory / ".delta_strict.jsonl"
    delta_index = build_directory / ".delta_index.jsonl"
    delta_continuations = build_directory / ".delta_continuations.jsonl"
    delta_audit = build_directory / ".delta_compaction_audit.jsonl"
    delta_exclusions = build_directory / ".delta_compaction_exclusions.jsonl"
    delta_quality = build_directory / ".delta_quality_exclusions.jsonl"
    snapshot_path = build_directory / ".delta_source_snapshot.jsonl"

    try:
        with (
            delta_training.open("w", encoding="utf-8") as training_stream,
            delta_strict.open("w", encoding="utf-8") as strict_stream,
            delta_index.open("w", encoding="utf-8") as index_stream,
            delta_continuations.open("w", encoding="utf-8") as continuation_stream,
            delta_audit.open("w", encoding="utf-8") as audit_stream,
            delta_exclusions.open("w", encoding="utf-8") as exclusion_stream,
            delta_quality.open("w", encoding="utf-8") as quality_stream,
            snapshot_path.open("w", encoding="utf-8") as snapshot_stream,
        ):
            while True:
                candidates = (
                    indexed_passes(attempts_index, seen_cases)
                    if attempts_index is not None
                    else scan_passes(scan_roots, seen_cases)
                )
                if not candidates:
                    break
                for case in sorted(candidates):
                    candidate = candidates[case]
                    base_record, context = build_base_record(
                        candidate, path_module, builder, deliveries, delivery_cache
                    )
                    source_trajectory_id = str(base_record["trajectory_id"])
                    compactions = context["trajectory"].get("compactions", [])
                    if compactions:
                        records, audits, exclusions = continuation_records(context, builder)
                        counts["incremental_compacted_pass_cases"] += 1
                    else:
                        records, audits, exclusions = [base_record], [], []
                        counts["incremental_ordinary_pass"] += 1

                    for record in records:
                        training_id = str(record["trajectory_id"])
                        if training_id in seen_training_ids:
                            raise ValueError(f"duplicate training ID: {training_id}")
                        validator.validate(record)
                        validate_model_surface(record)
                        write_line(training_stream, record)
                        if not record.get("quality_flags"):
                            write_line(strict_stream, record)
                            counts["incremental_strict_samples"] += 1
                        else:
                            write_line(quality_stream, {
                                "trajectory_id": training_id,
                                "case": case,
                                "quality_flags": record["quality_flags"],
                                "reason": "excluded_from_strict_training_view",
                            })
                            counts["incremental_quality_exclusions"] += 1
                        sample_type = (
                            "incremental_compaction_continuation"
                            if "::compaction_" in training_id
                            else "incremental_ordinary_pass"
                        )
                        index_row = {
                            "training_id": training_id,
                            "source_trajectory_id": source_trajectory_id,
                            "sample_type": sample_type,
                            "case": case,
                        }
                        if audits:
                            matching = next(
                                row for row in audits if row["training_id"] == training_id
                            )
                            index_row["compaction_index"] = matching["compaction_index"]
                            index_row["request_id"] = matching["request_id"]
                        write_line(index_stream, index_row)
                        if sample_type == "incremental_compaction_continuation":
                            write_line(continuation_stream, record)
                            counts["incremental_compaction_continuations"] += 1
                        seen_training_ids.add(training_id)
                        counts["incremental_training_samples"] += 1
                    for row in audits:
                        write_line(audit_stream, row)
                    for row in exclusions:
                        write_line(exclusion_stream, row)
                        counts["incremental_unrecoverable_compactions"] += 1
                    write_line(snapshot_stream, {
                        "case": case,
                        "source_trajectory_id": source_trajectory_id,
                        "trajectory_path": str(candidate["trajectory_path"]),
                        "trajectory_sha256": file_sha256(candidate["trajectory_path"]),
                        "verdict_path": str(candidate["verdict_path"]),
                        "verdict_sha256": file_sha256(candidate["verdict_path"]),
                        "verdict_mtime_ns": candidate["verdict_mtime_ns"],
                        "compaction_count": len(compactions),
                        "emitted_training_samples": len(records),
                        "quality_flags": base_record.get("quality_flags", []),
                    })
                    seen_cases.add(case)
                    counts["incremental_pass_cases"] += 1
                    if counts["incremental_pass_cases"] % 25 == 0:
                        print(
                            f"cleaned {counts['incremental_pass_cases']} incremental PASS cases",
                            flush=True,
                        )

            for stream in (
                training_stream, strict_stream, index_stream, continuation_stream,
                audit_stream, exclusion_stream, quality_stream, snapshot_stream,
            ):
                stream.flush()
                os.fsync(stream.fileno())

        merged_files = {
            "training_trajectories.jsonl": delta_training,
            "training_trajectories_strict.jsonl": delta_strict,
            "training_index.jsonl": delta_index,
            "compaction_continuation_trajectories.jsonl": delta_continuations,
            "compaction_continuation_audit.jsonl": delta_audit,
            "compaction_audit_exclusions.jsonl": delta_exclusions,
            "known_quality_exclusions.jsonl": delta_quality,
        }
        for filename, delta in merged_files.items():
            merged = build_directory / f".{filename}.merged"
            copy_and_append(base / filename, delta, merged)
            os.replace(merged, build_directory / filename)

        incremental_files = {
            "incremental_verified_pass_trajectories.jsonl": delta_training,
            "incremental_verified_pass_strict.jsonl": delta_strict,
            "incremental_training_index.jsonl": delta_index,
            "incremental_compaction_continuations.jsonl": delta_continuations,
            "incremental_compaction_audit.jsonl": delta_audit,
            "incremental_compaction_exclusions.jsonl": delta_exclusions,
            "incremental_quality_exclusions.jsonl": delta_quality,
            "incremental_source_snapshot.jsonl": snapshot_path,
        }
        for filename, delta in incremental_files.items():
            accumulate_increment(base / filename, delta, build_directory / filename)

        base_counts = parent_manifest.get("counts", {})
        total_training = int(base_counts.get("training_samples", 0)) + counts[
            "incremental_training_samples"
        ]
        total_strict = int(base_counts.get("strict_training_samples", 0)) + counts[
            "incremental_strict_samples"
        ]
        counts["training_samples"] = total_training
        counts["strict_training_samples"] = total_strict
        counts["ordinary_pass"] = int(base_counts.get("ordinary_pass", 0)) + counts[
            "incremental_ordinary_pass"
        ]
        counts["compaction_continuations"] = int(
            base_counts.get("compaction_continuations", 0)
        ) + counts["incremental_compaction_continuations"]
        counts["full_compacted_trajectories_excluded"] = int(
            base_counts.get("full_compacted_trajectories_excluded", 0)
        ) + counts["incremental_compacted_pass_cases"]
        counts["legacy_pass"] = int(base_counts.get("legacy_pass", 0))
        counts["strict_quality_exclusions"] = int(
            base_counts.get("strict_quality_exclusions", 0)
        ) + counts["incremental_quality_exclusions"]
        counts["excluded_unrecoverable_compactions"] = int(
            base_counts.get("excluded_unrecoverable_compactions", 0)
        ) + counts["incremental_unrecoverable_compactions"]

        cumulative_incremental_training = (
            build_directory / "incremental_verified_pass_trajectories.jsonl"
        )
        cumulative_incremental_strict = (
            build_directory / "incremental_verified_pass_strict.jsonl"
        )
        cumulative_incremental_snapshot = (
            build_directory / "incremental_source_snapshot.jsonl"
        )
        cumulative_incremental_continuations = (
            build_directory / "incremental_compaction_continuations.jsonl"
        )
        cumulative_incremental_exclusions = (
            build_directory / "incremental_compaction_exclusions.jsonl"
        )
        cumulative_incremental_quality = (
            build_directory / "incremental_quality_exclusions.jsonl"
        )
        snapshot_rows = list(jsonl(cumulative_incremental_snapshot))
        counts["cumulative_incremental_pass_cases"] = len(snapshot_rows)
        counts["cumulative_incremental_ordinary_pass"] = sum(
            int(row.get("compaction_count", 0)) == 0 for row in snapshot_rows
        )
        counts["cumulative_incremental_compacted_pass_cases"] = sum(
            int(row.get("compaction_count", 0)) > 0 for row in snapshot_rows
        )
        counts["cumulative_incremental_training_samples"] = sum(
            1 for _ in jsonl(cumulative_incremental_training)
        )
        counts["cumulative_incremental_strict_samples"] = sum(
            1 for _ in jsonl(cumulative_incremental_strict)
        )
        counts["cumulative_incremental_compaction_continuations"] = sum(
            1 for _ in jsonl(cumulative_incremental_continuations)
        )
        counts["cumulative_incremental_unrecoverable_compactions"] = sum(
            1 for _ in jsonl(cumulative_incremental_exclusions)
        )
        counts["cumulative_incremental_quality_exclusions"] = sum(
            1 for _ in jsonl(cumulative_incremental_quality)
        )

        training_path = build_directory / "training_trajectories.jsonl"
        strict_path = build_directory / "training_trajectories_strict.jsonl"
        index_path = build_directory / "training_index.jsonl"
        continuation_path = build_directory / "compaction_continuation_trajectories.jsonl"
        if sum(1 for _ in jsonl(training_path)) != total_training:
            raise ValueError("merged training line count does not match manifest")
        if sum(1 for _ in jsonl(strict_path)) != total_strict:
            raise ValueError("merged strict line count does not match manifest")
        if sum(1 for _ in jsonl(index_path)) != total_training:
            raise ValueError("merged index line count does not match training count")

        manifest = {
            "schema_version": "agent_world.merged_pass_training_manifest.v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parent_release": str(base),
            "parent_manifest_sha256": file_sha256(base / "manifest.json"),
            "scan_roots": [str(path) for path in scan_roots],
            "canonical_attempts_index": (
                str(attempts_index) if attempts_index is not None else None
            ),
            "training_schema": str(SCHEMA_PATH),
            "context_policy": deepcopy(parent_manifest.get("context_policy", {})),
            "quality_policy": deepcopy(parent_manifest.get("quality_policy", {})),
            "base_counts": base_counts,
            "counts": dict(counts),
            "files": {
                "training": training_path.name,
                "strict_training": strict_path.name,
                "compaction_continuations": continuation_path.name,
                "training_index": index_path.name,
                "compaction_audit": "compaction_continuation_audit.jsonl",
                "compaction_exclusions": "compaction_audit_exclusions.jsonl",
                "quality_exclusions": "known_quality_exclusions.jsonl",
                "incremental_training": cumulative_incremental_training.name,
                "incremental_strict_training": cumulative_incremental_strict.name,
                "incremental_index": "incremental_training_index.jsonl",
                "incremental_compaction_continuations": cumulative_incremental_continuations.name,
                "incremental_compaction_audit": "incremental_compaction_audit.jsonl",
                "incremental_compaction_exclusions": cumulative_incremental_exclusions.name,
                "incremental_quality_exclusions": cumulative_incremental_quality.name,
                "incremental_source_snapshot": cumulative_incremental_snapshot.name,
            },
            "sha256": {
                "training": file_sha256(training_path),
                "strict_training": file_sha256(strict_path),
                "compaction_continuations": file_sha256(continuation_path),
                "training_index": file_sha256(index_path),
                "incremental_training": file_sha256(cumulative_incremental_training),
                "incremental_source_snapshot": file_sha256(
                    cumulative_incremental_snapshot
                ),
            },
        }
        dump(build_directory / "manifest.json", manifest)
        (build_directory / "README.md").write_text(
            f"""# Kimi K3 PASS 训练集（含最新增量）

本目录在父版本 `{base}` 的基础上，继续加入已完整落盘且 verifier 判定为 PASS 的新轨迹。

## 推荐文件

- `training_trajectories_strict.jsonl`：推荐训练视图，共 {total_strict:,} 个样本。
- `training_trajectories.jsonl`：完整 PASS 视图，共 {total_training:,} 个样本。
- `training_index.jsonl`：训练样本与源轨迹的映射。
- `incremental_verified_pass_trajectories.jsonl`：相对最初 2,424 条父版本累计新增的 {counts['cumulative_incremental_training_samples']:,} 个样本。
- `incremental_source_snapshot.jsonl`：本轮源轨迹与 verifier 证据清单，仅供审计。

## 本轮增量

- 累计新增 verifier PASS case：{counts['cumulative_incremental_pass_cases']:,}
- 累计新增未压缩完整轨迹：{counts['cumulative_incremental_ordinary_pass']:,}
- 累计新增发生压缩的 PASS 源轨迹：{counts['cumulative_incremental_compacted_pass_cases']:,}
- 累计新增的压缩 continuation：{counts['cumulative_incremental_compaction_continuations']:,}
- 累计无法恢复的压缩点：{counts['cumulative_incremental_unrecoverable_compactions']:,}
- 累计因质量标记未进入 strict 的新增样本：{counts['cumulative_incremental_quality_exclusions']:,}

清洗规则与父版本一致：训练任务使用原始 `task.prompt`；保留 Kimi Code 原始 system prompt，仅虚拟化运行路径；清除 MCP 命名空间前缀；解包重复的 MCP 结果包装；审计事件、重试遥测和 `environment_record` 不进入训练消息。压缩轨迹不使用完整展开历史，只保留真实压缩后上下文对应的 continuation 样本。
""",
            encoding="utf-8",
        )
        os.replace(build_directory, output)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except BaseException:
        shutil.rmtree(build_directory, ignore_errors=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
