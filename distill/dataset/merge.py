#!/usr/bin/env python3
"""Merge ordinary PASS trajectories with real post-compaction continuations."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterator

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "standard_trajectory.schema.json"




def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not an object")
            yield value


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


def dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_line(stream: Any, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


_LEGACY_KIMI_RESULT_PATH = re.compile(
    r"/tmp/agent-world-kimi-home-[^/\s\"']+/sessions/[^/\s\"']+/"
    r"agents/main/tool-results/"
)
_LEGACY_TRUNCATED_MCP_PREFIX = re.compile(r"mcp__agent_world_distill_?-")
_LEGACY_KIMI_HOME = re.compile(r"/tmp/agent-world-kimi-home-[^/\s\"']+")


def normalize_legacy_value(value: Any, case: str, counts: Counter[str]) -> Any:
    if isinstance(value, str):
        original = value
        prefix_count = value.count("mcp__agent_world_distill__")
        if prefix_count:
            value = value.replace("mcp__agent_world_distill__", "")
            counts["legacy_mcp_prefix_replacements"] += prefix_count
        value, n = _LEGACY_TRUNCATED_MCP_PREFIX.subn("", value)
        counts["legacy_truncated_mcp_prefix_replacements"] += n
        wrapper_count = value.count("<mcp-result-extras>") + value.count(
            "</mcp-result-extras>"
        )
        if wrapper_count:
            value = value.replace("<mcp-result-extras>", "")
            value = value.replace("</mcp-result-extras>", "")
            counts["legacy_mcp_wrapper_tags_removed"] += wrapper_count
        structured_count = value.count("structuredContent")
        if structured_count:
            value = value.replace("structuredContent", "result")
            counts["legacy_structured_content_replacements"] += structured_count
        virtual_result_root = f"/tmp/{case}/tool-results/"
        value, n = _LEGACY_KIMI_RESULT_PATH.subn(virtual_result_root, value)
        counts["legacy_random_result_paths_replaced"] += n
        value, n = _LEGACY_KIMI_HOME.subn(f"/tmp/{case}", value)
        counts["legacy_kimi_home_paths_replaced"] += n
        session_count = value.count("/session/agents/main/tool-results/")
        if session_count:
            value = value.replace(
                "/session/agents/main/tool-results/", virtual_result_root
            )
            counts["legacy_generic_result_paths_replaced"] += session_count
        for source in ("~/.kimi/sessions", ".kimi/sessions"):
            occurrence_count = value.count(source)
            if occurrence_count:
                value = value.replace(source, f"/tmp/{case}")
                counts["legacy_kimi_session_paths_replaced"] += occurrence_count
        occurrence_count = value.count("/.kimi/")
        if occurrence_count:
            value = value.replace("/.kimi/", "/session/")
            counts["legacy_kimi_paths_replaced"] += occurrence_count
        if value != original:
            counts["legacy_strings_changed"] += 1
        return value
    if isinstance(value, list):
        return [normalize_legacy_value(item, case, counts) for item in value]
    if isinstance(value, dict):
        return {
            key: normalize_legacy_value(item, case, counts)
            for key, item in value.items()
        }
    return value


def call_ids_in_messages(messages: list[dict[str, Any]]) -> set[str]:
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


def assistant_message(step: dict[str, Any]) -> dict[str, Any]:
    calls = step.get("tool_calls", [])
    result: dict[str, Any] = {
        "role": "assistant",
        "content": step.get("assistant_text") or None,
        "reasoning_content": (step.get("reasoning") or {}).get("text") or "",
    }
    if calls:
        result["tool_calls"] = [
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
    return result


def request_step_index(
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
            if not isinstance(request_id, int):
                continue
            if request_id in result:
                raise ValueError(f"duplicate agent request_id {request_id}")
            result[request_id] = cleaned_step
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    clean = args.clean.expanduser().resolve()
    source = args.source.expanduser().resolve()
    legacy = args.legacy.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    output.mkdir(parents=True)

    from . import training as builder
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    raw_contexts = list(jsonl(source / "post_compaction_contexts.jsonl"))
    compacted_ids = {str(row["trajectory_id"]) for row in raw_contexts}
    valid_raw_contexts = {
        (str(row["trajectory_id"]), int(row["compaction_index"])): row
        for row in raw_contexts
        if isinstance(row.get("model_input"), dict)
    }
    cleaned_contexts = {
        (str(row["trajectory_id"]), int(row["compaction_index"])): row
        for row in jsonl(clean / "compaction_training_contexts.jsonl")
    }
    if valid_raw_contexts.keys() != cleaned_contexts.keys():
        missing_clean = sorted(valid_raw_contexts.keys() - cleaned_contexts.keys())
        missing_raw = sorted(cleaned_contexts.keys() - valid_raw_contexts.keys())
        raise ValueError(
            f"compaction context sets differ; missing_clean={missing_clean}, "
            f"missing_raw={missing_raw}"
        )

    combined_path = output / "training_trajectories.jsonl"
    continuation_path = output / "compaction_continuation_trajectories.jsonl"
    index_path = output / "training_index.jsonl"
    base_records: dict[str, dict[str, Any]] = {}
    training_ids: set[str] = set()
    counts: Counter[str] = Counter()

    fd, temporary_name = tempfile.mkstemp(prefix=".combined.", dir=output)
    try:
        with (
            os.fdopen(fd, "w", encoding="utf-8") as combined_stream,
            index_path.open("w", encoding="utf-8") as index_stream,
        ):
            for record in jsonl(legacy):
                case = str(record.get("provenance", {}).get("case", "legacy-case"))
                record = normalize_legacy_value(record, case, counts)
                normalizations = record.get("provenance", {}).get("normalizations")
                if isinstance(normalizations, list):
                    normalizations.append("legacy_template_references_canonicalized")
                    record["provenance"]["normalizations"] = sorted(
                        set(normalizations)
                    )
                validator.validate(record)
                trajectory_id = str(record["trajectory_id"])
                if trajectory_id in training_ids:
                    raise ValueError(f"duplicate legacy training ID: {trajectory_id}")
                training_ids.add(trajectory_id)
                write_line(combined_stream, record)
                write_line(index_stream, {
                    "training_id": trajectory_id,
                    "source_trajectory_id": trajectory_id,
                    "sample_type": "legacy_pass",
                })
                counts["legacy_pass"] += 1
                if record.get("quality_flags"):
                    counts["legacy_pass_with_quality_flags"] += 1
            for record in jsonl(clean / "training_trajectories.jsonl"):
                trajectory_id = str(record["trajectory_id"])
                if trajectory_id in compacted_ids:
                    base_records[trajectory_id] = record
                    counts["full_compacted_trajectories_excluded"] += 1
                    continue
                validator.validate(record)
                if trajectory_id in training_ids:
                    raise ValueError(f"duplicate cross-batch training ID: {trajectory_id}")
                training_ids.add(trajectory_id)
                write_line(combined_stream, record)
                write_line(index_stream, {
                    "training_id": trajectory_id,
                    "source_trajectory_id": trajectory_id,
                    "sample_type": "ordinary_pass",
                })
                counts["ordinary_pass"] += 1
            combined_stream.flush()
            os.fsync(combined_stream.fileno())
        os.replace(temporary_name, combined_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    if base_records.keys() != compacted_ids:
        missing = sorted(compacted_ids - base_records.keys())
        raise ValueError(f"missing compacted base records: {missing}")

    source_pairs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for source_record, input_record in builder.paired_jsonl(
        source / "trajectories.jsonl", source / "initial_requests.jsonl"
    ):
        trajectory_id = str(source_record["trajectory_id"])
        if trajectory_id in compacted_ids:
            source_pairs[trajectory_id] = (source_record, input_record)
    if source_pairs.keys() != compacted_ids:
        missing = sorted(compacted_ids - source_pairs.keys())
        raise ValueError(f"missing compacted source trajectories: {missing}")

    continuation_records: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for key in sorted(cleaned_contexts):
        trajectory_id, compaction_index = key
        context = cleaned_contexts[key]
        raw_context = valid_raw_contexts[key]
        source_record, input_record = source_pairs[trajectory_id]
        source_trajectory = source_record["trajectory"]
        case = str(source_record["case"])
        cleaner = builder.Cleaner(source_trajectory, input_record["request"], case)
        cleaned_view = builder.cleaned_trajectory_view(source_trajectory, cleaner)
        steps_by_request = request_step_index(
            source_trajectory.get("steps", []), cleaned_view.get("steps", [])
        )
        request_id = raw_context.get("model_request_record", {}).get("request_id")
        if not isinstance(request_id, int) or request_id not in steps_by_request:
            raise ValueError(
                f"no assistant target for {trajectory_id} compaction {compaction_index} "
                f"request_id={request_id}"
            )

        target_step = steps_by_request[request_id]
        target = assistant_message(target_step)
        model_input = deepcopy(context["model_input"])
        messages = deepcopy(model_input.get("messages", []))
        messages.append(target)
        tools = deepcopy(model_input.get("tools", []))
        defined_names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, dict)
        }
        target_names = {
            call.get("function", {}).get("name")
            for call in target.get("tool_calls", [])
            if isinstance(call, dict)
        }
        undefined = sorted(name for name in target_names if name not in defined_names)
        if undefined:
            raise ValueError(
                f"undefined target tools for {trajectory_id}/{compaction_index}: {undefined}"
            )

        base = base_records[trajectory_id]
        present_call_ids = call_ids_in_messages(messages)
        annotations = {
            call_id: deepcopy(annotation)
            for call_id, annotation in base.get("tool_call_annotations", {}).items()
            if call_id in present_call_ids
        }
        for call in target_step.get("tool_calls", []):
            call_id = str(call["call_id"])
            call_output = call.get("output")
            annotations[call_id] = {
                "name": call["name"],
                "tool_kind": call.get("tool_kind", "unknown"),
                "is_error": bool(call.get("is_error")),
                "output_encoding": (
                    "text" if isinstance(call_output, str) else "json"
                ),
            }

        training_id = f"{trajectory_id}::compaction_{compaction_index:03d}"
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
                "category": "verifier_pass_compaction_continuation",
            },
            "quality_flags": deepcopy(base.get("quality_flags", [])),
            "provenance": {
                "case": case,
                "evidence_ref": (
                    f"{os.path.relpath(source, output)}/post_compaction_contexts.jsonl#"
                    f"trajectory_id={trajectory_id}&compaction_index={compaction_index}"
                ),
                "source_trajectory_sha256": base["provenance"][
                    "source_trajectory_sha256"
                ],
                "cleaned_trajectory_sha256": base["provenance"][
                    "cleaned_trajectory_sha256"
                ],
                "cleaned_input_sha256": value_sha256({
                    "messages": model_input.get("messages", []),
                    "tools": tools,
                }),
                "raw_model_evidence_available": True,
                "reasoning_preserved": True,
                "normalizations": sorted(set(
                    base["provenance"].get("normalizations", [])
                    + [
                        "real_post_compaction_model_input",
                        "single_assistant_continuation_target",
                        "full_pre_compaction_history_excluded",
                    ]
                )),
            },
        }
        validator.validate(record)
        if messages[-1].get("role") != "assistant":
            raise ValueError(f"continuation does not end in assistant: {training_id}")
        input_chars = len(json.dumps(
            {"messages": model_input.get("messages", []), "tools": tools},
            ensure_ascii=False,
        ))
        target_chars = len(json.dumps(target, ensure_ascii=False))
        audit_rows.append({
            "training_id": training_id,
            "source_trajectory_id": trajectory_id,
            "case": case,
            "compaction_index": compaction_index,
            "request_id": request_id,
            "input_message_count": len(model_input.get("messages", [])),
            "input_chars_with_tools": input_chars,
            "target_chars": target_chars,
            "target_tool_call_count": len(target.get("tool_calls", [])),
            "context_summary_sha256": value_sha256(context.get("context_summary", "")),
        })
        continuation_records.append(record)
        counts["compaction_continuations"] += 1
        counts["continuation_target_tool_calls"] += len(target.get("tool_calls", []))
        counts["max_continuation_input_chars_with_tools"] = max(
            counts["max_continuation_input_chars_with_tools"], input_chars
        )
        counts["max_continuation_target_chars"] = max(
            counts["max_continuation_target_chars"], target_chars
        )

    with (
        continuation_path.open("w", encoding="utf-8") as continuation_stream,
        combined_path.open("a", encoding="utf-8") as combined_stream,
        index_path.open("a", encoding="utf-8") as index_stream,
    ):
        for record, audit in zip(continuation_records, audit_rows, strict=True):
            if record["trajectory_id"] in training_ids:
                raise ValueError(
                    f"duplicate continuation training ID: {record['trajectory_id']}"
                )
            training_ids.add(record["trajectory_id"])
            write_line(continuation_stream, record)
            write_line(combined_stream, record)
            write_line(index_stream, {
                "training_id": record["trajectory_id"],
                "source_trajectory_id": audit["source_trajectory_id"],
                "sample_type": "compaction_continuation",
                "compaction_index": audit["compaction_index"],
                "request_id": audit["request_id"],
            })

    audit_path = output / "compaction_continuation_audit.jsonl"
    with audit_path.open("w", encoding="utf-8") as stream:
        for row in audit_rows:
            write_line(stream, row)
    shutil.copy2(
        clean / "compaction_audit_exclusions.jsonl",
        output / "compaction_audit_exclusions.jsonl",
    )

    strict_path = output / "training_trajectories_strict.jsonl"
    quality_exclusions_path = output / "known_quality_exclusions.jsonl"
    with (
        combined_path.open(encoding="utf-8") as source_stream,
        strict_path.open("w", encoding="utf-8") as strict_stream,
        quality_exclusions_path.open("w", encoding="utf-8") as exclusion_stream,
    ):
        for line in source_stream:
            if not line.strip():
                continue
            record = json.loads(line)
            flags = record.get("quality_flags", [])
            if flags:
                write_line(exclusion_stream, {
                    "trajectory_id": record["trajectory_id"],
                    "case": record.get("provenance", {}).get("case"),
                    "quality_flags": flags,
                    "reason": "excluded_from_strict_training_view",
                })
                counts["strict_quality_exclusions"] += 1
                continue
            strict_stream.write(line)
            counts["strict_training_samples"] += 1

    counts["training_samples"] = (
        counts["legacy_pass"]
        + counts["ordinary_pass"]
        + counts["compaction_continuations"]
    )
    counts["excluded_unrecoverable_compactions"] = sum(
        1 for _ in jsonl(output / "compaction_audit_exclusions.jsonl")
    )
    if counts["ordinary_pass"] != 1958:
        raise ValueError(f"expected 1958 ordinary PASS records, got {counts['ordinary_pass']}")
    if counts["compaction_continuations"] != 53:
        raise ValueError(
            "expected 53 compaction continuations, got "
            f"{counts['compaction_continuations']}"
        )
    if counts["legacy_pass"] != 413:
        raise ValueError(f"expected 413 legacy PASS records, got {counts['legacy_pass']}")
    if counts["training_samples"] != 2424:
        raise ValueError(f"expected 2424 merged samples, got {counts['training_samples']}")
    if counts["strict_training_samples"] != 2417:
        raise ValueError(
            f"expected 2417 strict samples, got {counts['strict_training_samples']}"
        )

    manifest = {
        "schema_version": "agent_world.merged_pass_training_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legacy_source": str(legacy),
        "source": str(clean),
        "compaction_source": str(source / "post_compaction_contexts.jsonl"),
        "training_schema": str(SCHEMA_PATH),
        "context_policy": {
            "max_context_tokens": 262144,
            "ordinary_pass": "retain cleaned complete trajectory",
            "compacted_pass": (
                "exclude fully expanded trajectory; use the real post-compaction "
                "request and its immediately following assistant response"
            ),
            "duplicate_full_history_with_summary": False,
        },
        "quality_policy": {
            "complete_training_file": "retains every verifier PASS sample",
            "strict_training_file": "excludes records with non-empty quality_flags",
        },
        "counts": dict(counts),
        "files": {
            "training": combined_path.name,
            "strict_training": strict_path.name,
            "compaction_continuations": continuation_path.name,
            "training_index": index_path.name,
            "compaction_audit": audit_path.name,
            "compaction_exclusions": "compaction_audit_exclusions.jsonl",
            "quality_exclusions": quality_exclusions_path.name,
        },
        "sha256": {
            "training": file_sha256(combined_path),
            "strict_training": file_sha256(strict_path),
            "compaction_continuations": file_sha256(continuation_path),
            "training_index": file_sha256(index_path),
        },
    }
    dump(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        '# 标准训练轨迹\n\n本目录包含经过验证、清洗和标准化的工具调用轨迹。\n数量、来源、质量标记、上下文处理策略及文件校验值见 manifest.json。\n训练消费严格视图；审计文件保留原始证据与源轨迹对应关系。\n',
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
