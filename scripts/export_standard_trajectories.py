#!/usr/bin/env python3
"""Export cleaned Agent World trajectories as one Chat Completions JSONL file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_VERSION = "agent_world.standard_trajectory.v1"
MESSAGE_FORMAT = "openai.chat_completions.messages.v1+kimi.reasoning_content"
_HOST_DELIVERY_PATH = re.compile(r"/data1/(?:[^/\s\"'`]+/)*delivery/")
_HOST_RUNTIME_PATH = re.compile(r"/data1/(?:[^/\s\"'`]+/)*runtime/")
_ELLIPSIS_WORKSPACE_PATH = re.compile(r"/data1/\.\.\./(?:model-)?workspace")
_REMAINING_HOST_PATH = re.compile(r"/data1/[^\s\"'`]*")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_text(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value, "text"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")), "json"


def portable_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _ELLIPSIS_WORKSPACE_PATH.sub("/workspace", value)
        value = _HOST_DELIVERY_PATH.sub("/delivery/", value)
        value = _HOST_RUNTIME_PATH.sub("/runtime/", value)
        return _REMAINING_HOST_PATH.sub("/host-path", value)
    if isinstance(value, list):
        return [portable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: portable_value(item) for key, item in value.items()}
    return value


def delivery_metadata(repaired_tools: Path, tools_sha256: str) -> dict[str, Any]:
    environment_dir = repaired_tools.parents[1]
    delivery_root = environment_dir.parents[1]
    binding = environment_dir / "binding.json"
    environment_definition = environment_dir / "environment/environment.json"
    freeze_manifest = environment_dir / "environment/provenance/freeze_manifest.json"
    runtime_path = environment_dir / "runtime/runtime.json"
    required = [binding, environment_definition, freeze_manifest, runtime_path]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"delivery metadata is incomplete: {missing}")
    runtime = portable_value(load(runtime_path))
    if runtime.get("backend") == "docker":
        for key in ("software_root", "python_command"):
            value = runtime.get(key)
            if isinstance(value, str) and value.startswith("/host-path"):
                runtime.pop(key)
    return {
        "delivery_namespace": delivery_root.parent.name,
        "binding_ref": binding.relative_to(delivery_root).as_posix(),
        "binding_sha256": sha256(binding),
        "environment_definition_sha256": sha256(environment_definition),
        "initial_state_manifest_sha256": sha256(freeze_manifest),
        "tools_sha256": tools_sha256,
        "runtime": runtime,
    }


def trajectory_messages(
    initial_messages: list[dict[str, Any]], trajectory: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    messages = portable_value(
        json.loads(json.dumps(initial_messages, ensure_ascii=False))
    )
    annotations: dict[str, dict[str, Any]] = {}
    for step in trajectory["steps"]:
        calls = step.get("tool_calls", [])
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": portable_value(step.get("assistant_text")) or None,
            "reasoning_content": portable_value(
                (step.get("reasoning") or {}).get("text") or ""
            ),
        }
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": call["call_id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(
                            portable_value(call.get("arguments")),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
                for call in calls
            ]
        messages.append(assistant)
        for call in calls:
            content, encoding = json_text(portable_value(call.get("output")))
            messages.append({
                "role": "tool",
                "tool_call_id": call["call_id"],
                "content": content,
            })
            annotations[call["call_id"]] = {
                "name": call["name"],
                "tool_kind": call.get("tool_kind", "unknown"),
                "is_error": bool(call.get("is_error")),
                "output_encoding": encoding,
            }
    return messages, annotations


def build_record(
    root: Path,
    cleaned_record: dict[str, Any],
    accepted_record: dict[str, Any],
) -> dict[str, Any]:
    trajectory_path = Path(cleaned_record["trajectory"])
    input_path = Path(cleaned_record["input"])
    trajectory = load(trajectory_path)
    model_input = load(input_path)
    request = model_input["request"]
    task_text = trajectory["task"]["task_text"]
    if not trajectory["steps"] or (
        trajectory["steps"][-1].get("assistant_text")
        != trajectory["outcome"].get("final_answer")
    ):
        raise ValueError(f"{cleaned_record['case']} final answer is not the last assistant message")
    task_occurrences = sum(
        message.get("role") == "user" and message.get("content") == task_text
        for message in request["messages"]
    )
    if task_occurrences != 1:
        raise ValueError(
            f"{cleaned_record['case']} has {task_occurrences} exact task messages"
        )

    messages, annotations = trajectory_messages(request["messages"], trajectory)
    tool_names = {
        tool.get("function", {}).get("name")
        for tool in request.get("tools", [])
        if isinstance(tool, dict)
    }
    undefined = sorted({
        annotation["name"]
        for annotation in annotations.values()
        if annotation["name"] not in tool_names
    })
    quality_flags = [
        f"historical_undefined_tool_call:{name}" for name in undefined
    ]

    repaired_tools = Path(cleaned_record["repaired_tools"])
    environment = {
        "environment_id": trajectory["environment"]["environment_id"],
        **delivery_metadata(repaired_tools, cleaned_record["repaired_tools_sha256"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "message_format": MESSAGE_FORMAT,
        "trajectory_id": trajectory["trajectory_id"],
        "model": trajectory["model"],
        "task": {
            "task_id": trajectory["task"].get("task_id"),
            "task_text": task_text,
        },
        "environment": environment,
        "tools": request.get("tools", []),
        "messages": messages,
        "tool_call_annotations": annotations,
        "evaluation": {
            "outcome": "pass",
            "source": accepted_record["verification"],
            "category": accepted_record["category"],
        },
        "quality_flags": quality_flags,
        "provenance": {
            "case": cleaned_record["case"],
            "evidence_ref": (
                f"trajectories/{cleaned_record['case']}"
            ),
            "source_trajectory_sha256": cleaned_record["source_sha256"],
            "cleaned_trajectory_sha256": sha256(trajectory_path),
            "cleaned_input_sha256": sha256(input_path),
            "raw_model_evidence_available": bool(cleaned_record["raw_evidence"]),
            "reasoning_preserved": True,
            "normalizations": [
                "user_prompt_reduced_to_original_task_text",
                "mcp_tool_names_canonicalized",
                "tool_definitions_repaired",
                "mcp_result_wrappers_removed",
                "runtime_paths_portabilized",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaned-root", type=Path, required=True)
    parser.add_argument("--accepted-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "distill/standard_trajectory.schema.json",
    )
    args = parser.parse_args()
    root = args.cleaned_root.expanduser().resolve()
    cleaned_manifest = load(root / "manifest.json")
    accepted_manifest = load(args.accepted_manifest.expanduser().resolve())
    accepted_by_case = {row["case"]: row for row in accepted_manifest["accepted"]}
    records = cleaned_manifest["records"]
    if len(records) != accepted_manifest["counts"]["accepted_total"]:
        raise SystemExit("cleaned and accepted trajectory counts differ")
    if {row["case"] for row in records} != set(accepted_by_case):
        raise SystemExit("cleaned and accepted case sets differ")

    validator = Draft202012Validator(load(args.schema.expanduser().resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{args.output.name}.", dir=args.output.parent
    )
    counts = {
        "trajectories": 0,
        "messages": 0,
        "tool_calls": 0,
        "reasoning_characters": 0,
        "flagged_trajectories": 0,
    }
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for cleaned_record in sorted(records, key=lambda row: row["case"]):
                record = build_record(
                    root,
                    cleaned_record,
                    accepted_by_case[cleaned_record["case"]],
                )
                validator.validate(record)
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
                counts["trajectories"] += 1
                counts["messages"] += len(record["messages"])
                counts["tool_calls"] += len(record["tool_call_annotations"])
                counts["reasoning_characters"] += sum(
                    len(message.get("reasoning_content", ""))
                    for message in record["messages"]
                )
                counts["flagged_trajectories"] += bool(record["quality_flags"])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, args.output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    counts["bytes"] = args.output.stat().st_size
    counts["sha256"] = sha256(args.output)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
