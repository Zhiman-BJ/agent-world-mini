from copy import deepcopy
import json
from pathlib import Path

import pytest

from task_gen.program import solution_validation as validation
from task_gen.program.author_submission import resolve_submission
from task_gen.program.proposal_sources import SELF_CHECKS
from task_gen.task_eval_mcp import TaskEvalMcpServer
from tests.test_solution_validation import config as config


@pytest.fixture
def proposal(config):
    config["solution_validation"]["source_check"] = {
        "protocol": {"max_total_tool_calls": 65}, "original_task_text": "Original", "original_call_count": 1}
    return {
        "task_text": "Inspect and update W01's public counter.", "real_need": "Deliver the updated counter.",
        "reference_answer": "The counter is 1.", "solution_calls": [
            {"tool": "inspect", "arguments": {}, "argument_sources": {}, "purpose": "Inspect current state."},
            {"tool": "increment", "arguments": {"target": "W01"},
             "argument_sources": {"/target": {"kind": "task", "quote": "W01"}}, "purpose": "Update target."}],
        "answer_sources": [{"kind": "call", "call": 2, "pointer": "/counter"}],
        "self_check": dict.fromkeys(SELF_CHECKS, True), "self_check_notes": ["Actual results checked."]}


def execution_calls(proposal):
    return [{"tool": call["tool"], "arguments": call["arguments"]} for call in proposal["solution_calls"]]


def records(config):
    path = Path(config["trace"]).with_name("solution_validations.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_single_complete_proposal_executes_and_preserves_auditable_receipt(config, proposal):
    arguments = {"proposal": proposal}
    original = deepcopy(arguments)
    result = validation.SolutionValidator(config).validate(arguments)

    assert result["execution_passed"] is result["source_check_passed"] is True
    assert [call["tool"] for call in result["calls"]] == ["inspect", "increment"]
    assert result["calls"][-1]["result"]["counter"] == 1
    assert result["semantic_correctness_proved"] is False
    assert arguments == original
    receipt = records(config)[0]
    assert receipt["request"] == {"proposal": proposal, "calls": execution_calls(proposal)}
    final, binding = resolve_submission(json.dumps({"validation_id": result["validation_id"]}), [receipt])
    assert final == {**proposal, "validation_id": result["validation_id"]}
    assert binding["new_execution"] is False
    assert Path(config["workspace_root"], "counter.txt").read_text() == "0"
    assert Path(config["record_database"]).read_bytes() == b"original records"


def test_source_repairs_and_legacy_requests_share_execution_but_recheck_sources(config, proposal):
    validator = validation.SolutionValidator(config)
    proposal["answer_sources"][0]["pointer"] = "/missing"
    first = validator.validate({"proposal": proposal})
    assert first["execution_passed"] is True
    assert first["source_check_passed"] is False

    proposal["answer_sources"][0]["pointer"] = "/counter"
    second = validator.validate({"proposal": proposal})
    assert second["source_check_passed"] is True
    assert second["execution_reused"] is True
    assert second["execution_validation_id"] == first["validation_id"]
    legacy = validator.validate({"proposal": proposal, "calls": execution_calls(proposal)})
    assert legacy["source_check_passed"] is True
    assert legacy["execution_reused"] is True
    assert legacy["execution_validation_id"] == first["validation_id"]
    assert len({first["validation_id"], second["validation_id"], legacy["validation_id"]}) == 3


def test_legacy_conflicting_lists_are_not_silently_reconciled(config, proposal):
    calls = execution_calls(proposal)
    calls[1] = {"tool": "inspect", "arguments": {}}
    result = validation.SolutionValidator(config).validate({"proposal": proposal, "calls": calls})

    assert result["source_check_passed"] is False
    assert "mismatched 1-based call indices=[2]" in result["source_check_errors"][0]
    assert records(config)[0]["request"]["calls"] == calls


@pytest.mark.parametrize("mutation", ["wrong_value", "future_source", "false_self_check", "extra_call_field"])
def test_single_proposal_keeps_parameter_source_and_submission_checks(config, proposal, mutation):
    if mutation == "wrong_value":
        proposal["solution_calls"][1]["arguments"]["target"] = "W02"
    elif mutation == "future_source":
        proposal["solution_calls"][1]["argument_sources"]["/target"] = {
            "kind": "call", "call": 2, "pointer": "/target"}
    elif mutation == "false_self_check":
        proposal["self_check"]["answer_supported"] = False
    else:
        proposal["solution_calls"][0]["observed_call"] = 1
    validator = validation.SolutionValidator(config)
    result = validator.validate({"proposal": proposal})

    assert result["execution_passed"] is True
    assert result["source_check_passed"] is False
    assert result["source_check_errors"]
    with pytest.raises(ValueError, match="source_check_passed=true"):
        validator.submit({"validation_id": result["validation_id"]})


@pytest.mark.parametrize("mutation", [
    "missing_field", "unexpected_field", "not_list", "call_not_object", "missing_tool", "empty_tool",
    "invalid_arguments", "empty_calls", "too_many", "file_tool", "budget",
])
def test_malformed_single_proposals_do_not_snapshot_or_execute(config, proposal, monkeypatch, mutation):
    if mutation == "missing_field":
        proposal.pop("reference_answer")
    elif mutation == "unexpected_field":
        proposal["observed_calls"] = []
    elif mutation == "not_list":
        proposal["solution_calls"] = None
    elif mutation == "call_not_object":
        proposal["solution_calls"][0] = "inspect"
    elif mutation == "missing_tool":
        proposal["solution_calls"][0].pop("tool")
    elif mutation == "empty_tool":
        proposal["solution_calls"][0]["tool"] = ""
    elif mutation == "invalid_arguments":
        proposal["solution_calls"][0]["arguments"] = None
    elif mutation == "empty_calls":
        proposal["solution_calls"] = []
    elif mutation == "too_many":
        proposal["solution_calls"] *= 33
    elif mutation == "file_tool":
        proposal["solution_calls"][0]["tool"] = "Read"
    else:
        config["max_tool_calls"] = 1
    validator = validation.SolutionValidator(config, defer_baseline=True)
    monkeypatch.setattr(validator, "ensure_baseline", lambda: pytest.fail("invalid proposal must not snapshot"))
    monkeypatch.setattr(validator, "execute", lambda *arguments: pytest.fail("invalid proposal must not execute"))

    with pytest.raises(ValueError):
        validator.validate({"proposal": proposal})
    assert validator.attempts == 0
    assert not Path(config["trace"]).with_name("solution_validations.jsonl").exists()


def test_mcp_accepts_single_proposal_and_still_requires_successful_submission(config, proposal):
    server = TaskEvalMcpServer(config)
    result = server.handle({"method": "tools/call", "params": {
        "name": "validate_solution_calls", "arguments": {"proposal": proposal}}})

    assert result["isError"] is False
    receipt = result["structuredContent"]
    assert receipt["execution_passed"] is receipt["source_check_passed"] is True
    submitted = server.handle({"method": "tools/call", "params": {
        "name": "submit_validated_solution", "arguments": {"validation_id": receipt["validation_id"]}}})
    assert submitted["structuredContent"] == {"accepted": True, "validation_id": receipt["validation_id"]}
