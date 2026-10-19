"""Model-free protocol, provenance, and trajectory checks for task hardening."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from task_gen.eval_cases import EvalCase, load_cases

from .utils.io import read_json, read_jsonl, write_json


NATIVE_TOOLS = {"Read", "Grep", "Glob"}
DISABLED_TOOLS = {"Write", "Edit", "Bash", "Agent", "AgentSwarm", "WebSearch",
                  "FetchURL", "AskUserQuestion", "select_tools"}
FROZEN_TOOL_CALL_LIMIT = 65
EXTENDED_AUTHOR_TOOL_CALL_LIMIT = 75
EXTENDED_EVALUATION_TOOL_CALL_LIMIT = 85


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_protocol(protocol: dict) -> None:
    expected = {"reasoning_effort": "high", "provider_type": "openai_responses",
                "context_cap": 262144, "max_output_tokens": 131072,
                "read_default_chars": 80000, "read_max_chars": 128000,
                "compaction_trigger_ratio": 0.8,
                "reference_answer_supplied_to_solver": False}
    for field, value in expected.items():
        if type(protocol.get(field)) is not type(value) or protocol[field] != value:
            raise ValueError(f"interactive reference must retain the frozen protocol: {field}")
    role = protocol.get("protocol_role", "frozen")
    allowed_limit = {"author_extended": EXTENDED_AUTHOR_TOOL_CALL_LIMIT,
                     "evaluation_extended": EXTENDED_EVALUATION_TOOL_CALL_LIMIT}.get(
                         role, FROZEN_TOOL_CALL_LIMIT)
    if protocol.get("max_total_tool_calls") != allowed_limit:
        raise ValueError("interactive reference must retain the frozen protocol: max_total_tool_calls")
    if any(not isinstance(protocol.get(field), str) or not protocol[field].strip()
           for field in ("model", "provider_base_url")):
        raise ValueError("interactive reference requires an explicit model and provider URL")


def source_fingerprint(case) -> dict:
    state = []
    for path in sorted(case.initial_state.rglob("*")):
        if path.is_symlink():
            raise ValueError("frozen initial state must not contain symbolic links")
        if path.is_file():
            state.append({"path": path.relative_to(case.initial_state).as_posix(),
                          "sha256": sha256(path)})
    return {"tasks_sha256": sha256(case.source_run / "tasks.json"),
            "tools_sha256": sha256(Path(case.runtime["tools_path"])),
            "binding_sha256": sha256(Path(case.runtime["binding_path"])),
            "bundle_sha256": sha256(case.source_run / "intermediate/step_5_bundle.json"),
            "initial_state_files": state}


def check_upstream_requests(execution: Path, protocol: dict) -> dict:
    requests = read_jsonl(execution / "raw/model_requests.jsonl")
    if not requests:
        raise ValueError("interactive reference has no recorded upstream requests")
    for record in requests:
        request = record["request"]
        relative = Path(record["raw_path"])
        actual = (execution / relative).resolve()
        if relative.is_absolute() or not actual.is_relative_to(execution.resolve()):
            raise ValueError("upstream request evidence points outside this execution")
        if (sha256(actual) != record["sha256"] or read_json(actual) != request
                or request.get("model") != protocol["model"] or request.get("stream") is not True
                or request.get("reasoning", {}).get("effort") != protocol["reasoning_effort"]
                or type(request.get("max_output_tokens")) is not int
                or not 0 < request["max_output_tokens"] <= protocol["max_output_tokens"]):
            raise ValueError("actual upstream request differs from its evidence or model protocol")
    return {"request_count": len(requests),
            "model_requests_sha256": sha256(execution / "raw/model_requests.jsonl")}


def ordered_interactions(trajectory: dict, raw_calls: list[dict], task: dict,
                         protocol: dict, policy: dict) -> list[dict]:
    if any(trajectory["task"].get(field) != value for field, value in public_task(task).items()):
        raise ValueError("interactive trajectory differs from the public task")
    expected_model = {"upstream_id": protocol["model"], "provider": protocol["provider_type"],
                      "reasoning_effort": protocol["reasoning_effort"],
                      "max_context_size": protocol["context_cap"],
                      "max_output_size": protocol["max_output_tokens"]}
    if any(trajectory["model"].get(field) != value for field, value in expected_model.items()):
        raise ValueError("interactive trajectory uses another model protocol")
    enabled = {"mcp__agent_world_distill__*", *NATIVE_TOOLS}
    if (set(policy["tools_enabled"]) != enabled
            or not DISABLED_TOOLS.issubset(policy["tools_disabled"])
            or policy["tool_call_budget"] != {
                "limit": protocol["max_total_tool_calls"],
                "scope": "environment_mcp_plus_native_file_tools",
            }
            or policy.get("result_reader") != "native_kimi_tool_results"
            or policy.get("native_read", {}).get("default_max_chars") != protocol["read_default_chars"]
            or policy.get("native_read", {}).get("max_chars") != protocol["read_max_chars"]
            or policy.get("compaction_trigger_ratio") != protocol["compaction_trigger_ratio"]):
        raise ValueError("interactive trajectory uses another tool, read or budget policy")
    if (trajectory["outcome"].get("status") != "completed"
            or not trajectory["outcome"].get("final_answer", "").strip()):
        raise ValueError("interactive reference did not complete an answer")
    calls = [call for step in trajectory["steps"] for call in step.get("tool_calls", [])]
    if not 1 <= len(calls) <= protocol["max_total_tool_calls"]:
        raise ValueError("interactive reference has no calls or exceeded the shared total budget")
    observed, interactions, identifiers = [], [], set()
    for call in calls:
        identifier = call["call_id"]
        if not identifier or identifier in identifiers:
            raise ValueError("interactive calls must have unique recorded identifiers")
        identifiers.add(identifier)
        start, end = call.get("started_event_sequence"), call.get("completed_event_sequence")
        if type(start) is not int or type(end) is not int or end <= start:
            raise ValueError("interactive call is missing its actual start or completion event")
        if call["tool_kind"] == "native_file_tool":
            if call["name"] not in NATIVE_TOOLS or isinstance(call.get("environment_record"), dict):
                raise ValueError("interactive reference used an unsupported native tool")
        elif call["tool_kind"] == "environment_mcp":
            record = call.get("environment_record")
            if (not isinstance(record, dict)
                    or call["name"] != f"mcp__agent_world_distill__{record.get('tool')}"):
                raise ValueError("interactive domain call is missing its exact raw MCP record")
            observed.append(record)
        elif call["tool_kind"] == "harness_support":
            if (call["name"] not in {"mcp__agent_world_distill__validate_solution_calls",
                                     "mcp__agent_world_distill__submit_validated_solution"}
                    or not policy.get("solution_validation")):
                raise ValueError("interactive reference used an unexpected author support tool")
        else:
            raise ValueError("interactive reference used an unsupported tool kind")
        if "output" not in call:
            raise ValueError("interactive call is missing its model-visible result")
        interactions.append({"call_id": identifier, "name": call["name"],
                             "tool_kind": call["tool_kind"], "arguments": call["arguments"],
                             "visible_output": call["output"], "is_error": call.get("is_error", False),
                             "started_event_sequence": start, "completed_event_sequence": end})
    def signature(record):
        return json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)

    if Counter(map(signature, observed)) != Counter(map(signature, raw_calls)):
        raise ValueError("interactive trajectory does not exactly cover the actual raw MCP calls")
    return sorted(interactions, key=lambda call: call["started_event_sequence"])


def public_task(task: dict) -> dict:
    result = {field: task[field] for field in ("task_id", "task_text")}
    if any(not isinstance(value, str) or not value.strip() for value in result.values()):
        raise ValueError("public task must have a nonempty id and text")
    return result


def select_case(input_root: Path, name: str) -> EvalCase:
    cases = load_cases(input_root, selected_case_names={name})
    if len(cases) != 1:
        raise ValueError(f"expected one existing case: {name}")
    return cases[0]


def _atomic_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    temporary.replace(path)


def evaluation_environment() -> dict[str, str]:
    import importlib.util

    wrapper_path = Path(__file__).resolve().parents[3] / "agent_train/evaluation/run_taskgen_eval.py"
    if not wrapper_path.is_file():
        raise ValueError("evaluation runtime environment wrapper is missing")
    spec = importlib.util.spec_from_file_location("reference_eval_runtime", wrapper_path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    return wrapper.scoped_runtime_environment(os.environ)


def reference_calls(trajectory: dict) -> list[dict]:
    calls = sorted((call for step in trajectory["steps"] for call in step.get("tool_calls", [])),
                   key=lambda call: call["started_event_sequence"])
    result = []
    for call in calls:
        if call["tool_kind"] == "environment_mcp":
            if not isinstance(call.get("environment_record"), dict):
                raise ValueError("interactive reference is missing raw domain evidence")
            result.append(deepcopy(call["environment_record"]))
        elif call["tool_kind"] == "native_file_tool" and call["name"] in {"Read", "Grep", "Glob"}:
            result.append({"tool": call["name"], "arguments": call["arguments"], "result": call["output"],
                           "error": "native_file_tool_error" if call.get("is_error") else None})
        else:
            raise ValueError("unsupported tool in interactive reference")
    return result
