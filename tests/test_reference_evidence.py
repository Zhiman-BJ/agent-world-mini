from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from task_gen.program import reference_evidence as reference
from task_gen.program.utils.io import read_json, write_json


@pytest.fixture
def protocol():
    return {"model": "test-model", "provider_type": "openai_responses",
            "provider_base_url": "http://localhost:8000/v1", "reasoning_effort": "high",
            "context_cap": 262144, "max_output_tokens": 131072,
            "read_default_chars": 80000, "read_max_chars": 128000,
            "max_total_tool_calls": 65, "compaction_trigger_ratio": 0.8,
            "reference_answer_supplied_to_solver": False}


@pytest.fixture
def evidence(protocol):
    task = {"task_id": "task", "task_text": "Deliver the supported public result."}
    raw = {"tool": "query", "arguments": {}, "result": {"success": True, "data": {"value": 42}},
           "error": None}
    domain = {"call_id": "domain", "name": "mcp__agent_world_distill__query",
              "tool_kind": "environment_mcp", "arguments": {}, "output": {"output": "visible result"},
              "environment_record": raw, "started_event_sequence": 10, "completed_event_sequence": 15}
    native = {"call_id": "read", "name": "Read", "tool_kind": "native_file_tool",
              "arguments": {"path": "public-result.txt"}, "output": {"output": "public excerpt"},
              "started_event_sequence": 20, "completed_event_sequence": 25}
    trajectory = {"task": task, "model": {"upstream_id": protocol["model"],
        "provider": protocol["provider_type"], "reasoning_effort": "high",
        "max_context_size": 262144, "max_output_size": 131072},
        "outcome": {"status": "completed", "final_answer": "The public value is 42."},
        "steps": [{"tool_calls": [native, domain]}]}
    policy = {"tools_enabled": ["mcp__agent_world_distill__*", "Read", "Grep", "Glob"],
              "tools_disabled": sorted(reference.DISABLED_TOOLS),
              "tool_call_budget": {"limit": 65, "scope": "environment_mcp_plus_native_file_tools"},
              "result_reader": "native_kimi_tool_results",
              "native_read": {"default_max_chars": 80000, "max_chars": 128000},
              "compaction_trigger_ratio": 0.8}
    return trajectory, [raw], task, protocol, policy


def test_export_retains_actual_visible_outputs_and_parallel_event_order(evidence):
    interactions = reference.ordered_interactions(*evidence)
    assert [call["call_id"] for call in interactions] == ["domain", "read"]
    assert interactions[0]["visible_output"] == {"output": "visible result"}
    assert "environment_record" not in interactions[0]
    assert interactions[1]["visible_output"] == {"output": "public excerpt"}


def test_author_support_calls_keep_observation_indices_but_are_not_domain_evidence(evidence):
    trajectory, raw, task, protocol, policy = evidence
    policy["solution_validation"] = {"max_attempts": 3}
    trajectory["steps"][0]["tool_calls"].append({
        "call_id": "validate", "name": "mcp__agent_world_distill__validate_solution_calls",
        "tool_kind": "harness_support", "arguments": {"calls": []},
        "output": {"execution_passed": True}, "started_event_sequence": 30, "completed_event_sequence": 35})
    interactions = reference.ordered_interactions(*evidence)
    assert [call["call_id"] for call in interactions] == ["domain", "read", "validate"]
    policy.pop("solution_validation")
    with pytest.raises(ValueError, match="unexpected author support"):
        reference.ordered_interactions(*evidence)


@pytest.mark.parametrize("mutation", ["task", "model", "shell", "read", "scope", "empty_answer",
                                    "missing_raw", "extra_raw", "duplicate_id", "incomplete",
                                    "unknown_tool", "missing_output", "renamed_mcp", "budget"])
def test_incomplete_or_different_protocol_cannot_become_an_interactive_reference(evidence, mutation):
    trajectory, raw, task, protocol, policy = evidence
    call = trajectory["steps"][0]["tool_calls"][0]
    if mutation == "task":
        trajectory["task"] = {**task, "task_text": "Another task"}
    elif mutation == "model":
        trajectory["model"]["reasoning_effort"] = "xhigh"
    elif mutation == "shell":
        policy["tools_enabled"].append("Bash")
    elif mutation == "read":
        policy["native_read"]["max_chars"] = 500000
    elif mutation == "scope":
        policy["tool_call_budget"]["scope"] = "MCP_only"
    elif mutation == "empty_answer":
        trajectory["outcome"]["final_answer"] = " "
    elif mutation == "missing_raw":
        raw.clear()
    elif mutation == "extra_raw":
        raw.append(deepcopy(raw[0]))
    elif mutation == "duplicate_id":
        call["call_id"] = "domain"
    elif mutation == "incomplete":
        call.pop("completed_event_sequence")
    elif mutation == "unknown_tool":
        call["name"] = "Bash"
    elif mutation == "missing_output":
        call.pop("output")
    elif mutation == "renamed_mcp":
        trajectory["steps"][0]["tool_calls"][1]["name"] = "query"
    else:
        trajectory["steps"][0]["tool_calls"] *= 34
    with pytest.raises(ValueError):
        reference.ordered_interactions(trajectory, raw, task, protocol, policy)


def test_frozen_protocol_is_checked(protocol):
    reference.check_protocol(protocol)
    protocol["max_total_tool_calls"] = True
    with pytest.raises(ValueError, match="frozen protocol"):
        reference.check_protocol(protocol)


@pytest.fixture
def request_evidence(tmp_path, protocol):
    execution = tmp_path / "execution"
    request = {"model": protocol["model"], "stream": True, "reasoning": {"effort": "high"},
               "max_output_tokens": protocol["max_output_tokens"]}
    write_json(execution / "raw/request.json", request)
    record = {"request": request, "raw_path": "raw/request.json",
              "sha256": reference.sha256(execution / "raw/request.json")}
    (execution / "raw/model_requests.jsonl").write_text(json.dumps(record) + "\n")
    return execution, protocol


def test_upstream_requests_are_bound_to_actual_payloads(request_evidence):
    execution, protocol = request_evidence
    result = reference.check_upstream_requests(execution, protocol)
    assert result["request_count"] == 1
    assert result["model_requests_sha256"] == reference.sha256(execution / "raw/model_requests.jsonl")


@pytest.mark.parametrize("mutation", ["model", "effort", "hash", "body", "path", "output_cap", "empty"])
def test_actual_request_payload_is_checked_not_only_self_reported_model(request_evidence, mutation):
    execution, protocol = request_evidence
    path = execution / "raw/model_requests.jsonl"
    record = json.loads(path.read_text())
    if mutation == "model":
        record["request"]["model"] = "another-model"
    elif mutation == "effort":
        record["request"]["reasoning"]["effort"] = "xhigh"
    elif mutation == "hash":
        record["sha256"] = "wrong"
    elif mutation == "body":
        record["request"]["extra"] = "not in actual payload"
    elif mutation == "path":
        record["raw_path"] = "../outside.json"
    elif mutation == "output_cap":
        record["request"]["max_output_tokens"] = 200000
    path.write_text("" if mutation == "empty" else json.dumps(record) + "\n")
    with pytest.raises(ValueError):
        reference.check_upstream_requests(execution, protocol)


def test_frozen_source_hashes_cover_state_and_reject_symlinks(tmp_path):
    source = tmp_path / "source"
    initial = source / "initial"
    initial.mkdir(parents=True)
    (initial / "data.txt").write_text("original")
    for name in ("tasks.json", "tools.json", "binding.json", "intermediate/step_5_bundle.json"):
        write_json(source / name, {})
    case = SimpleNamespace(source_run=source, initial_state=initial,
        runtime={"tools_path": str(source / "tools.json"), "binding_path": str(source / "binding.json")})
    original = reference.source_fingerprint(case)
    (initial / "data.txt").write_text("changed")
    assert reference.source_fingerprint(case) != original
    (initial / "link.txt").symlink_to(initial / "data.txt")
    with pytest.raises(ValueError, match="symbolic links"):
        reference.source_fingerprint(case)


def test_reference_calls_preserve_actual_order_and_copy_raw_evidence(evidence):
    trajectory, raw, *_ = evidence
    calls = reference.reference_calls(trajectory)
    assert [call["tool"] for call in calls] == ["query", "Read"]
    assert calls[0] == raw[0]
    assert calls[1]["result"] == {"output": "public excerpt"}
    calls[0]["result"]["data"]["value"] = 999
    assert raw[0]["result"]["data"]["value"] == 42
