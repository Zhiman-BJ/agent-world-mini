import json
import os
from pathlib import Path

import pytest

from distill.tests.test_cli_runner import streaming_model as streaming_model


@pytest.mark.parametrize("proposal_only", [False, True])
def test_official_cli_sees_source_error_before_paging_large_validation(tmp_path, streaming_model, proposal_only):
    from distill.runner import run_k3_distillation
    from task_gen.program.author_submission import resolve_submission

    kimi_bin = os.environ.get("KIMI_CODE_BIN")
    if not kimi_bin:
        pytest.skip("Set KIMI_CODE_BIN to the official Kimi CLI")
    base_url, requests = streaming_model
    initial = tmp_path / "initial"
    initial.mkdir()
    tool = {
        "name": "inspect", "description": "Inspect a large deterministic public value.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "success": {"const": True}, "value": {"type": "integer"}, "data": {"type": "string"}},
            "required": ["success", "value", "data"], "additionalProperties": False},
        "internal": {"code": (
            "def run(arguments, context):\n"
            " return {'success': True, 'value': 17, 'data': 'x' * 500000}"
        )},
    }
    output = tmp_path / "run"
    trajectory = run_k3_distillation(
        "AUTHOR_SUBMISSION_STOP_PROBE" + (" PROPOSAL_ONLY" if proposal_only else ""), initial,
        {"workspace_root": ".", "trace": "calls.jsonl", "max_tool_calls": 10, "timeout": 30,
         "memory_limit": 2 * 1024 * 1024 * 1024, "write_limit": 256 * 1024 * 1024,
         "tools": [tool], "environment": {"environment_id": "large-feedback-fixture", "schema_version": "1.0"},
         "solution_validation": {"submission_gate": True, "source_check": {
             "protocol": {"read_default_chars": 80000, "max_total_tool_calls": 75},
             "original_task_text": "Original", "original_call_count": 18}}},
        output, kimi_bin=Path(kimi_bin), base_url=base_url, api_key="test-key-never-written",
        max_context_size=262144,
    )

    assert len(requests) == 5
    validation_arguments = [
        json.loads(call["function"]["arguments"])
        for message in requests[-1]["messages"] for call in message.get("tool_calls", [])
        if call["function"]["name"].endswith("__validate_solution_calls")
    ]
    assert len(validation_arguments) == 2
    assert all(("calls" not in arguments) == proposal_only for arguments in validation_arguments)
    first_feedback = "\n".join(str(message.get("content", ""))
                               for message in requests[1]["messages"] if message.get("role") == "tool")
    assert "source_check_passed" in first_feedback
    assert "answer source 1" in first_feedback
    assert "output_path:" in first_feedback
    assert "x" * 10_000 not in first_feedback
    records = [json.loads(line) for line in (output / "raw/solution_validations.jsonl").read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["result"]["source_check_passed"] is False
    assert len(records[0]["result"]["calls"][0]["result"]["data"]) == 500_000
    assert records[1]["result"]["source_check_passed"] is True
    assert records[1]["result"]["execution_reused"] is True
    assert len(records[1]["result"]["calls"][0]["result"]["data"]) == 500_000
    proposal, binding = resolve_submission(trajectory["outcome"]["final_answer"], records)
    assert proposal["answer_sources"][0]["pointer"] == "/value"
    assert json.loads((output / "raw/solution_submissions.jsonl").read_text()) == {
        "accepted": True, "validation_id": binding["validation_id"]}
    assert all(call["tool_kind"] == "harness_support"
               for step in trajectory["steps"] for call in step["tool_calls"])
