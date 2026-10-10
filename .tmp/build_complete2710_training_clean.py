#!/usr/bin/env python3
"""Build the final training view for the complete2710 verified PASS batch."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterator

from jsonschema import Draft202012Validator


REPO = Path("/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini")
sys.path.insert(0, str(REPO))

from distill.clean import MCP_PREFIX, tool_name_mapping
from distill.visualize import render_combined_visualization


DEFAULT_SOURCE = Path(
    "/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/"
    "path_cleaned_20261008"
)
DEFAULT_OUTPUT = Path(
    "/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/"
    "training_cleaned_20261008"
)
DELIVERY_ROOTS = (
    Path("/data1/agent_world/toolgen_environments/delivery"),
    Path("/data1/agent_world/toolgen_semiconductor_expanded/delivery"),
)
SCHEMA_PATH = REPO / "distill/standard_trajectory.schema.json"

_MCP_EXTRAS = re.compile(
    r"<mcp-result-extras>\s*(\{.*\})\s*</mcp-result-extras>", re.DOTALL
)
_EXTERNALIZED = re.compile(
    r"\[Per-line truncation occurred; the complete output was saved to a file\.\s*"
    r"output_path:\s*(\S+)\s*"
    r"next_step:\s*(.*?)\]",
    re.DOTALL,
)
_RUNTIME_HOST_PATH = re.compile(
    r"/data1/agent_world/(?:toolgen_environments|toolgen_semiconductor_expanded)/runtime"
)
_DELIVERY_HOST_PATH = re.compile(
    r"/data1/agent_world/(?:toolgen_environments|toolgen_semiconductor_expanded)/delivery"
)
_ELLIPSIS_HOST_PATH = re.compile(r"/data1/(?:agent_world/)?\.\.\.(?=/|\b)")
_REMAINING_DATA1_PATH = re.compile(r"/data1/[A-Za-z0-9_./:@+~-]*")
_MCP_NAMESPACE_FRAGMENT = re.compile(r"mcp__agent_world_distill_+-?")
_MCP_TRUNCATED_NAMESPACE = re.compile(
    "(?:"
    + "|".join(
        re.escape(MCP_PREFIX[:length] + "-")
        for length in range(len(MCP_PREFIX) - 1, len("mcp__a") - 1, -1)
    )
    + ")"
)

AUDIT_STEP_KEYS = {
    "step_id", "turn_id", "started_event_sequence", "completed_event_sequence",
    "model_exchanges", "retries", "finish",
}
AUDIT_CALL_KEYS = {
    "environment_record", "started_event_sequence", "completed_event_sequence",
}


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


def paired_jsonl(left: Path, right: Path) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    left_rows = jsonl(left)
    right_rows = jsonl(right)
    for left_row, right_row in zip(left_rows, right_rows, strict=True):
        if left_row.get("trajectory_id") != right_row.get("trajectory_id"):
            raise ValueError("trajectory and initial-request order differs")
        yield left_row, right_row


def portable_runtime_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _RUNTIME_HOST_PATH.sub("/runtime", value)
        value = _DELIVERY_HOST_PATH.sub("/delivery", value)
        return _REMAINING_DATA1_PATH.sub("/host-path", value)
    if isinstance(value, list):
        return [portable_runtime_value(item) for item in value]
    if isinstance(value, dict):
        return {key: portable_runtime_value(item) for key, item in value.items()}
    return value


class Cleaner:
    def __init__(self, trajectory: dict[str, Any], request: dict[str, Any], case: str):
        self.case = case
        self.workspace = f"/{case}/model-workspace"
        self.mapping = tool_name_mapping(trajectory, request)
        self.stats: Counter[str] = Counter()
        self.output_parse_errors: list[str] = []

    def text(self, value: str) -> str:
        original = value
        for source in sorted(self.mapping, key=len, reverse=True):
            target = self.mapping[source]
            if source != target and source in value:
                count = value.count(source)
                value = value.replace(source, target)
                self.stats["tool_name_replacements"] += count
        if MCP_PREFIX in value:
            count = value.count(MCP_PREFIX)
            value = value.replace(MCP_PREFIX, "")
            self.stats["prefix_fallback_replacements"] += count
        value, count = _MCP_NAMESPACE_FRAGMENT.subn("", value)
        self.stats["truncated_prefix_replacements"] += count
        value, count = _MCP_TRUNCATED_NAMESPACE.subn("", value)
        self.stats["truncated_filename_prefix_replacements"] += count
        value, count = re.subn(r"\bmcp__(?:\*|\.\.\.)", "environment tools", value)
        self.stats["generic_mcp_references_replaced"] += count
        value, count = re.subn(
            r"\bmcp__agent_world_distill(?:__)?\s+tools\b",
            "environment tools",
            value,
        )
        self.stats["generic_mcp_references_replaced"] += count
        value, count = re.subn(
            r"\bmcp__agent_world_distill(?:__)?", "environment_tool", value
        )
        self.stats["generic_mcp_references_replaced"] += count
        if "mcp__" in value:
            count = value.count("mcp__")
            value = value.replace("mcp__", "environment_")
            self.stats["generic_mcp_references_replaced"] += count

        value, count = _RUNTIME_HOST_PATH.subn("/runtime", value)
        self.stats["runtime_path_replacements"] += count
        value, count = _DELIVERY_HOST_PATH.subn("/delivery", value)
        self.stats["delivery_path_replacements"] += count
        value, count = _ELLIPSIS_HOST_PATH.subn(self.workspace, value)
        self.stats["ellipsis_path_replacements"] += count
        value, count = _REMAINING_DATA1_PATH.subn("/host-path", value)
        self.stats["remaining_host_path_replacements"] += count
        if value != original:
            self.stats["strings_changed"] += 1
        return value

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        return value

    def artifact_text(self, value: str) -> str:
        original = value
        value = value.replace("<mcp-result-extras>", "")
        value = value.replace("</mcp-result-extras>", "")
        value = value.replace("structuredContent", "result")
        if value != original:
            self.stats["secondary_wrapper_references_removed"] += 1
        return value

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.artifact_text(value)
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, dict):
            return {
                self.artifact_text(key) if isinstance(key, str) else key: self.sanitize(item)
                for key, item in value.items()
            }
        return value

    def training_value(self, value: Any) -> Any:
        return self.sanitize(self.value(value))

    def training_text(self, value: str) -> str:
        return self.artifact_text(self.text(value))

    def tool_name(self, name: str) -> str:
        return self.mapping.get(name, self.text(name))

    def call_id(self, call_id: str) -> str:
        return self.text(call_id)

    def mcp_output(self, call: dict[str, Any]) -> Any:
        output = self.value(deepcopy(call.get("output")))
        if call.get("tool_kind") != "environment_mcp":
            return self.sanitize(output)
        if not isinstance(output, dict) or not isinstance(output.get("output"), str):
            self.stats["mcp_outputs_already_structured"] += 1
            return output
        text = output["output"].strip()
        external = _EXTERNALIZED.search(text)
        if external is not None:
            preview = text[:external.start()].strip()
            preview = preview.removeprefix("<mcp-result-extras>").strip()
            preview = preview.removesuffix("</mcp-result-extras>").strip()
            result = {
                "externalized": True,
                "output_path": external.group(1),
                "truncation": "per_line",
                "next_step": "Use Read or Grep with output_path to inspect the saved result.",
            }
            if preview:
                result["preview"] = self.artifact_text(preview)
            self.stats["mcp_outputs_externalized"] += 1
            return result

        extras = _MCP_EXTRAS.search(text)
        if extras is not None:
            try:
                payload = json.loads(extras.group(1))
            except json.JSONDecodeError as error:
                self.output_parse_errors.append(str(call.get("call_id")))
                self.stats["mcp_output_parse_errors"] += 1
                return {
                    "text": self.artifact_text(text),
                    "structured_parse_error": str(error),
                }
            if isinstance(payload, dict) and "structuredContent" in payload:
                self.stats["mcp_outputs_unwrapped"] += 1
                return payload["structuredContent"]
            self.stats["mcp_outputs_json_without_structured_content"] += 1
            return payload

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self.stats["mcp_outputs_plain_text"] += 1
            return {"text": self.artifact_text(text)}
        self.stats["mcp_outputs_plain_json"] += 1
        return payload


def cleaned_tools(tools: list[Any], cleaner: Cleaner) -> list[dict[str, Any]]:
    result = cleaner.training_value(deepcopy(tools))
    names = []
    for item in result:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            continue
        names.append(function["name"])
    if len(names) != len(set(names)):
        raise ValueError(f"tool names collide after cleaning for {cleaner.case}")
    if any(MCP_PREFIX in name for name in names):
        raise ValueError(f"prefixed tool definition remains for {cleaner.case}")
    return result


def cleaned_initial_messages(
    messages: list[Any], cleaner: Cleaner, prompt: str, original_task_text: str,
) -> list[dict[str, Any]]:
    result = cleaner.training_value(deepcopy(messages))
    prompt = cleaner.training_text(prompt)
    original_task_text = cleaner.training_text(original_task_text)
    exact_prompt = 0
    replacements = 0
    for message in result:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if content == prompt:
            exact_prompt += 1
        elif content == original_task_text:
            message["content"] = prompt
            exact_prompt += 1
            replacements += 1
    if exact_prompt != 1:
        raise ValueError(
            f"expected one exact training prompt for {cleaner.case}, found {exact_prompt}"
        )
    cleaner.stats["initial_task_message_replacements"] += replacements
    return result


def cleaned_call(call: dict[str, Any], cleaner: Cleaner) -> dict[str, Any]:
    result = {
        key: cleaner.training_value(deepcopy(value))
        for key, value in call.items()
        if key not in AUDIT_CALL_KEYS and key != "output"
    }
    result["name"] = cleaner.tool_name(str(call.get("name", "")))
    result["call_id"] = cleaner.call_id(str(call.get("call_id", "")))
    result["output"] = cleaner.mcp_output(call)
    if MCP_PREFIX in result["name"] or MCP_PREFIX in result["call_id"]:
        raise ValueError(f"prefixed call identity remains in {cleaner.case}")
    return result


def cleaned_trajectory_view(
    trajectory: dict[str, Any], cleaner: Cleaner,
) -> dict[str, Any]:
    prompt = cleaner.training_text(str(trajectory["task"]["prompt"]))
    tool_names = sorted({
        cleaner.tool_name(str(name))
        for name in trajectory.get("environment", {}).get("tool_names", [])
    })
    steps = []
    for source_step in trajectory.get("steps", []):
        step = {
            key: cleaner.training_value(deepcopy(value))
            for key, value in source_step.items()
            if key not in AUDIT_STEP_KEYS and key != "tool_calls"
        }
        reasoning = step.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning.pop("event_sequences", None)
        step["tool_calls"] = [
            cleaned_call(call, cleaner)
            for call in source_step.get("tool_calls", [])
            if isinstance(call, dict)
        ]
        steps.append(step)
    call_ids = [
        call["call_id"] for step in steps for call in step.get("tool_calls", [])
    ]
    if len(call_ids) != len(set(call_ids)):
        raise ValueError(f"call IDs collide after cleaning for {cleaner.case}")
    return {
        "schema_version": trajectory.get("schema_version"),
        "trajectory_id": trajectory["trajectory_id"],
        "model": deepcopy(trajectory.get("model", {})),
        "task": {
            "task_id": trajectory.get("task", {}).get("task_id"),
            "prompt": prompt,
            "task_text": prompt,
        },
        "environment": {
            "environment_id": trajectory.get("environment", {}).get("environment_id"),
            "name": trajectory.get("environment", {}).get("name"),
            "tool_names": tool_names,
        },
        "steps": steps,
        "compactions": [],
        "outcome": {
            "status": trajectory.get("outcome", {}).get("status"),
            "final_answer": cleaner.training_value(
                trajectory.get("outcome", {}).get("final_answer")
            ),
        },
    }


def trajectory_messages(
    initial_messages: list[dict[str, Any]], trajectory: dict[str, Any], cleaner: Cleaner,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    messages = deepcopy(initial_messages)
    annotations: dict[str, dict[str, Any]] = {}
    for step in trajectory["steps"]:
        calls = step.get("tool_calls", [])
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": step.get("assistant_text") or None,
            "reasoning_content": (step.get("reasoning") or {}).get("text") or "",
        }
        if calls:
            assistant["tool_calls"] = [
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
        messages.append(assistant)
        for call in calls:
            output = call.get("output")
            if isinstance(output, str):
                content = output
                encoding = "text"
            else:
                content = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
                encoding = "json"
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


def delivery_index() -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = defaultdict(list)
    for root in DELIVERY_ROOTS:
        for environment_file in sorted(
            (root / "environments").glob("*/environment/environment.json")
        ):
            environment = load(environment_file)
            environment_id = environment.get("environment_id")
            if isinstance(environment_id, str):
                result[environment_id].append(environment_file.parents[1])
    return result


def delivery_for(
    index: dict[str, list[Path]], environment_id: str, case: str,
    expected_tools: set[str],
) -> tuple[Path, Path]:
    matches = []
    for environment_dir in index.get(environment_id, []):
        tool_path = environment_dir / "tools/tools_repaired.json"
        if not tool_path.is_file():
            tool_path = environment_dir / "tools/tools.json"
        if not tool_path.is_file():
            continue
        names = {
            tool.get("name") for tool in load(tool_path).get("tools", [])
            if isinstance(tool, dict)
        }
        if names == expected_tools:
            matches.append((environment_dir, tool_path))
    if len(matches) > 1:
        case_matches = [item for item in matches if item[0].name in case]
        if len(case_matches) == 1:
            matches = case_matches
    if len(matches) != 1:
        raise ValueError(
            f"expected one delivery for {case}/{environment_id}, found {len(matches)}"
        )
    return matches[0]


def environment_metadata(
    environment_dir: Path, tool_path: Path,
) -> dict[str, Any]:
    delivery_root = environment_dir.parents[1]
    binding = environment_dir / "binding.json"
    environment = environment_dir / "environment/environment.json"
    freeze = environment_dir / "environment/provenance/freeze_manifest.json"
    runtime = environment_dir / "runtime/runtime.json"
    missing = [path for path in (binding, environment, freeze, runtime, tool_path) if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete delivery {environment_dir}: {missing}")
    return {
        "delivery_namespace": delivery_root.parent.name,
        "binding_ref": binding.relative_to(delivery_root).as_posix(),
        "binding_sha256": file_sha256(binding),
        "environment_definition_sha256": file_sha256(environment),
        "initial_state_manifest_sha256": file_sha256(freeze),
        "tools_sha256": file_sha256(tool_path),
        "runtime": portable_runtime_value(load(runtime)),
    }


def normalized_model_input(model_input: dict[str, Any], cleaner: Cleaner) -> dict[str, Any]:
    result = cleaner.value(deepcopy(model_input))
    tools = result.get("tools")
    if isinstance(tools, list):
        result["tools"] = cleaned_tools(tools, cleaner)
    for message in result.get("messages", []):
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                if isinstance(call.get("id"), str):
                    call["id"] = cleaner.call_id(call["id"])
                function = call.get("function")
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    function["name"] = cleaner.tool_name(function["name"])
        if isinstance(message.get("tool_call_id"), str):
            message["tool_call_id"] = cleaner.call_id(message["tool_call_id"])
        if (
            message.get("role") == "tool"
            and isinstance(message.get("content"), str)
            and (
                "<mcp-result-extras>" in message["content"]
                or "[Per-line truncation occurred;" in message["content"]
            )
        ):
            fake_call = {
                "tool_kind": "environment_mcp",
                "call_id": message.get("tool_call_id"),
                "output": {"output": message["content"]},
            }
            normalized = cleaner.mcp_output(fake_call)
            message["content"] = (
                normalized if isinstance(normalized, str)
                else json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            )
    return cleaner.sanitize(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    schema = load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    deliveries = delivery_index()
    delivery_cache: dict[tuple[str, str], tuple[Path, Path, dict[str, Any]]] = {}
    cleaners: dict[str, Cleaner] = {}
    sample_cases = {
        row["case"]: row["sample_reason"]
        for row in load(source / "visualization_sample/sample_manifest.json")["samples"]
    }
    sample_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    counts: Counter[str] = Counter()

    training_path = output / "training_trajectories.jsonl"
    audit_path = output / "audit_metadata.jsonl"
    fd, temporary_name = tempfile.mkstemp(prefix=".training.", dir=output)
    try:
        with (
            os.fdopen(fd, "w", encoding="utf-8") as training_stream,
            audit_path.open("w", encoding="utf-8") as audit_stream,
        ):
            for source_record, input_record in paired_jsonl(
                source / "trajectories.jsonl", source / "initial_requests.jsonl"
            ):
                trajectory = source_record["trajectory"]
                request = input_record["request"]
                case = source_record["case"]
                cleaner = Cleaner(trajectory, request, case)
                cleaned_view = cleaned_trajectory_view(trajectory, cleaner)
                prompt = cleaned_view["task"]["prompt"]
                initial_messages = cleaned_initial_messages(
                    request.get("messages", []),
                    cleaner,
                    str(trajectory["task"]["prompt"]),
                    str(trajectory["task"].get("task_text", "")),
                )
                tools = cleaned_tools(request.get("tools", []), cleaner)
                messages, annotations = trajectory_messages(
                    initial_messages, cleaned_view, cleaner
                )
                defined_names = {
                    item.get("function", {}).get("name")
                    for item in tools if isinstance(item, dict)
                }
                undefined = sorted({
                    annotation["name"] for annotation in annotations.values()
                    if annotation["name"] not in defined_names
                })
                quality_flags = [
                    f"historical_undefined_tool_call:{name}" for name in undefined
                ]
                quality_flags.extend(
                    f"mcp_result_parse_error:{call_id}"
                    for call_id in cleaner.output_parse_errors
                )

                environment_id = str(trajectory["environment"]["environment_id"])
                expected_tools = set(trajectory["environment"].get("tool_names", []))
                delivery_key = (environment_id, case.split("__", 1)[0])
                if delivery_key not in delivery_cache:
                    environment_dir, tool_path = delivery_for(
                        deliveries, environment_id, case, expected_tools
                    )
                    delivery_cache[delivery_key] = (
                        environment_dir, tool_path,
                        environment_metadata(environment_dir, tool_path),
                    )
                environment_dir, tool_path, delivery = delivery_cache[delivery_key]
                record = {
                    "schema_version": "agent_world.standard_trajectory.v1",
                    "message_format": "openai.chat_completions.messages.v1+kimi.reasoning_content",
                    "trajectory_id": trajectory["trajectory_id"],
                    "model": deepcopy(trajectory.get("model", {})),
                    "task": {
                        "task_id": trajectory.get("task", {}).get("task_id"),
                        "task_text": prompt,
                    },
                    "environment": {
                        "environment_id": environment_id,
                        **delivery,
                    },
                    "tools": tools,
                    "messages": messages,
                    "tool_call_annotations": annotations,
                    "evaluation": {
                        "outcome": "pass",
                        "source": "canonical_verifier",
                        "category": source_record.get("label", "verifier_pass"),
                    },
                    "quality_flags": sorted(set(quality_flags)),
                    "provenance": {
                        "case": case,
                        "evidence_ref": (
                            f"../path_cleaned_20261008/trajectories.jsonl#"
                            f"trajectory_id={trajectory['trajectory_id']}"
                        ),
                        "source_trajectory_sha256": value_sha256(trajectory),
                        "cleaned_trajectory_sha256": value_sha256(cleaned_view),
                        "cleaned_input_sha256": value_sha256({
                            "messages": initial_messages, "tools": tools,
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
                validator.validate(record)
                training_stream.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                audit_stream.write(json.dumps({
                    "trajectory_id": trajectory["trajectory_id"],
                    "case": case,
                    "audit_trajectory_ref": record["provenance"]["evidence_ref"],
                    "original_task_text": trajectory.get("task", {}).get("task_text"),
                    "training_prompt": prompt,
                    "task_text_changed": (
                        trajectory.get("task", {}).get("task_text") != prompt
                    ),
                    "removed_from_training_messages": [
                        "environment_record", "event_sequences", "step_id",
                        "started_event_sequence", "completed_event_sequence",
                        "model_exchanges", "retries", "finish",
                    ],
                    "delivery_ref": str(environment_dir),
                    "tool_definition_ref": str(tool_path),
                }, ensure_ascii=False, separators=(",", ":")) + "\n")
                cleaners[trajectory["trajectory_id"]] = cleaner
                if case in sample_cases:
                    sample_input = deepcopy(input_record)
                    sample_input["request"] = cleaner.value(deepcopy(request))
                    sample_input["request"]["messages"] = initial_messages
                    sample_input["request"]["tools"] = tools
                    sample_payloads[case] = (cleaned_view, sample_input)

                counts["trajectories"] += 1
                counts["messages"] += len(messages)
                counts["tool_calls"] += len(annotations)
                counts["task_text_changed"] += (
                    trajectory.get("task", {}).get("task_text") != prompt
                )
                counts["flagged_trajectories"] += bool(quality_flags)
                counts.update(cleaner.stats)
                if counts["trajectories"] % 100 == 0:
                    print(f"built {counts['trajectories']} training trajectories", flush=True)
                if args.limit is not None and counts["trajectories"] >= args.limit:
                    break
            training_stream.flush()
            os.fsync(training_stream.fileno())
        os.replace(temporary_name, training_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    compaction_path = output / "compaction_training_contexts.jsonl"
    compaction_exclusions_path = output / "compaction_audit_exclusions.jsonl"
    with (
        compaction_path.open("w", encoding="utf-8") as stream,
        compaction_exclusions_path.open("w", encoding="utf-8") as exclusion_stream,
    ):
        for source_context in jsonl(source / "post_compaction_contexts.jsonl"):
            trajectory_id = source_context.get("trajectory_id")
            cleaner = cleaners.get(trajectory_id)
            if cleaner is None:
                continue
            model_input = source_context.get("model_input")
            if not isinstance(model_input, dict):
                exclusion_stream.write(json.dumps({
                    "trajectory_id": trajectory_id,
                    "case": source_context.get("case"),
                    "compaction_index": source_context.get("compaction_index"),
                    "reason": "model_input_is_not_available",
                    "audit_source": (
                        "../path_cleaned_20261008/post_compaction_contexts.jsonl"
                    ),
                }, ensure_ascii=False, separators=(",", ":")) + "\n")
                counts["compaction_contexts_excluded"] += 1
                continue
            context = {
                "schema_version": "agent_world.compaction_training_context.v1",
                "trajectory_id": trajectory_id,
                "case": source_context.get("case"),
                "compaction_index": source_context.get("compaction_index"),
                "context_summary": cleaner.training_text(
                    str(source_context.get("context_summary", ""))
                ),
                "model_input": normalized_model_input(
                    model_input, cleaner
                ),
            }
            stream.write(json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts["compaction_contexts"] += 1

    visualization = output / "visualization_sample"
    visualization.mkdir()
    results = []
    sample_manifest = []
    for case, reason in sample_cases.items():
        payload = sample_payloads.get(case)
        if payload is None:
            continue
        case_dir = visualization / case
        case_dir.mkdir()
        dump(case_dir / "trajectory.json", payload[0])
        dump(case_dir / "input.json", payload[1])
        results.append({"case": case, "status": "completed"})
        sample_manifest.append({"case": case, "sample_reason": reason})
    dump(visualization / "summary.json", {"results": results})
    dump(visualization / "sample_manifest.json", {"samples": sample_manifest})
    render_combined_visualization(visualization)

    manifest = {
        "schema_version": "agent_world.training_clean_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "audit_source": str(source / "trajectories.jsonl"),
        "training_schema": str(SCHEMA_PATH),
        "counts": dict(counts),
        "files": {
            "training_trajectories": training_path.name,
            "audit_metadata": audit_path.name,
            "compaction_training_contexts": compaction_path.name,
            "compaction_audit_exclusions": compaction_exclusions_path.name,
            "visualization": "visualization_sample/combined.html",
        },
    }
    manifest["training_sha256"] = file_sha256(training_path)
    dump(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        """# 最终训练清洗轨迹

本目录在路径清洗结果之上完成训练格式清洗，源轨迹保持不变。

- `training_trajectories.jsonl`：标准 Chat Completions 训练轨迹。
- `audit_metadata.jsonl`：原始 `task_text`、审计证据和被排除字段说明。
- `compaction_training_contexts.jsonl`：同步清洗后的压缩恢复上下文。
- `compaction_audit_exclusions.jsonl`：因缺少模型输入而未进入训练的压缩记录。
- `visualization_sample/combined.html`：五类典型轨迹的清洗后可视化。

训练轨迹使用 `task.prompt` 作为 `task.task_text`；工具名、调用 ID 和长结果文件引用使用基本工具名；MCP 包装结果已转换成单一结构化结果。`environment_record`、事件序号、重试记录、模型交换索引和请求遥测不进入训练消息。
""",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
