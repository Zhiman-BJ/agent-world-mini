from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from task_gen.task_eval_mcp import TaskEvalMcpServer


def make_server(tmp_path, payload):
    server = TaskEvalMcpServer({
        "tools": [], "workspace_root": str(tmp_path),
        "trace": str(tmp_path / "calls.jsonl"), "max_tool_calls": 75,
    })
    server.solution_validator = SimpleNamespace(validate=lambda arguments: payload)
    return server


def validate(server):
    return server.handle({
        "method": "tools/call",
        "params": {"name": "validate_solution_calls", "arguments": {"calls": [], "proposal": {}}},
    })


def test_source_errors_are_visible_before_large_replay_in_mcp_preview(tmp_path):
    error = "answer source 1: ValueError: tool sources require an exact earlier visible result"
    payload = {
        "validation_id": "receipt",
        "execution_passed": True,
        "calls": [{"tool": "inspect", "result": {"data": "x" * 500_000}}],
        "state": {"changed": False},
        "source_check_passed": False,
        "source_check_errors": [error] * 59,
    }
    original = deepcopy(payload)
    response = validate(make_server(tmp_path, payload))
    wire = json.dumps(response, ensure_ascii=False, allow_nan=False)
    assert len(wire) > 500_000
    assert '"source_check_passed": false' in wire[:2000]
    assert error in wire[:2000]
    assert wire.index('"source_check_errors"') < wire.index('"calls"')
    assert response["structuredContent"] == original
    assert response["isError"] is False
    assert response["content"] == []
    assert payload == original
    assert list(payload) == list(original)


@pytest.mark.parametrize("execution_passed,source_check_passed", [(False, False), (True, False), (True, True)])
def test_status_fields_precede_replay_without_changing_the_complete_receipt(
    tmp_path, execution_passed, source_check_passed,
):
    payload = {
        "calls": [{"tool": "inspect", "result": {"data": "x" * 500_000}}],
        "state": {"changed": False},
        "backend": "shared_mcp_executor",
        "model_inference": False,
        "validation_id": "receipt",
        "execution_passed": execution_passed,
        "source_check_passed": source_check_passed,
        "source_check_errors": [] if source_check_passed else ["Source does not match."],
        "failed_call": None if execution_passed else 1,
        "call_count": 1,
        "execution_reused": True,
        "semantic_correctness_proved": False,
    }
    response = validate(make_server(tmp_path, payload))
    wire = json.dumps(response, ensure_ascii=False, allow_nan=False)
    for field in ("validation_id", "execution_passed", "source_check_passed", "failed_call", "call_count"):
        assert wire.index(json.dumps(field)) < wire.index('"calls"')
    assert response["structuredContent"] == payload
    assert set(response["structuredContent"]) == set(payload)
    assert response["structuredContent"]["semantic_correctness_proved"] is False


def test_validation_without_source_check_fields_keeps_its_original_contract(tmp_path):
    payload = {"calls": [], "execution_passed": True, "validation_id": "receipt", "call_count": 0}
    response = validate(make_server(tmp_path, payload))
    assert response["structuredContent"] == payload
    assert "source_check_passed" not in response["structuredContent"]
    assert "source_check_errors" not in response["structuredContent"]


def test_validation_exception_still_returns_the_actual_error(tmp_path):
    server = make_server(tmp_path, {})

    def reject(arguments):
        raise ValueError("Complete proposal required")

    server.solution_validator.validate = reject
    response = validate(server)
    assert response["isError"] is True
    assert response["structuredContent"] == {"execution_passed": False, "error": "Complete proposal required"}
