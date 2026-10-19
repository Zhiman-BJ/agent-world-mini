from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from distill.taskgen_verifier import verification_calls


def test_verifier_state_copy_handles_cross_filesystem(tmp_path, monkeypatch):
    import errno
    import os
    from distill.taskgen_verifier import _copy_tree_links

    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.txt").write_text("immutable evidence")

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", cross_device)
    destination = tmp_path / "destination"
    _copy_tree_links(source, destination)
    assert (destination / "evidence.txt").read_text() == "immutable evidence"
    (destination / "evidence.txt").write_text("different copy")
    assert (source / "evidence.txt").read_text() == "immutable evidence"


@pytest.fixture
def evidence():
    business = {
        "tool": "export_structure_formats", "arguments": {"checkpoint": "s3"},
        "result": {"success": True, "path": "structure.vasp"}, "error": None,
    }
    native = {
        "tool_kind": "native_file_tool", "name": "Read",
        "arguments": {"path": "structure.vasp"},
        "output": {"output": "1\tSi6", "note": "End of file reached."},
        "call_id": "read-1", "completed_event_sequence": 10, "is_error": False,
    }
    trajectory = {"steps": [{"tool_calls": [
        {
            "tool_kind": "environment_mcp", "environment_record": business,
            "name": "mcp__workspace__export_structure_formats",
            "arguments": business["arguments"], "call_id": "export-1",
            "completed_event_sequence": 8,
        }, native,
    ]}]}
    return trajectory, [business]


def test_verification_includes_original_mcp_and_complete_native_evidence(evidence):
    trajectory, business = evidence
    original = deepcopy(trajectory)
    calls = verification_calls(trajectory, business)

    assert calls[0] == business[0]
    assert calls[1] == {
        "tool": "Read", "arguments": {"path": "structure.vasp"},
        "result": {"output": "1\tSi6", "note": "End of file reached."},
        "error": None, "call_id": "read-1",
    }
    assert trajectory == original


@pytest.mark.parametrize("name", ["Read", "Grep", "Glob"])
def test_verification_preserves_each_native_tool_and_errors(evidence, name):
    trajectory, business = evidence
    native = trajectory["steps"][0]["tool_calls"][1]
    native.update(name=name, is_error=True, output={"output": "Access denied"})
    calls = verification_calls(trajectory, business)

    assert calls[1]["tool"] == name
    assert calls[1]["result"] == {"output": "Access denied"}
    assert calls[1]["error"] == "native_file_tool_error"


def test_verification_preserves_raw_completion_order_without_guessing_a_joint_timeline(evidence):
    trajectory, business = evidence
    second = {**business[0], "arguments": {"checkpoint": "s5"}}
    trajectory["steps"][0]["tool_calls"].append({
        "tool_kind": "environment_mcp", "environment_record": second,
    })
    calls = verification_calls(trajectory, [second, business[0]])

    assert [call["tool"] for call in calls] == ["export_structure_formats", "export_structure_formats", "Read"]
    assert calls[:2] == [second, business[0]]


@pytest.mark.parametrize("change", ["missing", "changed_result", "duplicate_attachment"])
def test_verification_rejects_conflicting_or_duplicated_exported_mcp_evidence(evidence, change):
    trajectory, business = evidence
    raw = deepcopy(business)
    if change == "missing":
        raw.clear()
    elif change == "duplicate_attachment":
        trajectory["steps"][0]["tool_calls"].append(
            deepcopy(trajectory["steps"][0]["tool_calls"][0]),
        )
    else:
        raw[0]["result"]["success"] = False
    with pytest.raises(ValueError, match="differ from the original raw trace"):
        verification_calls(trajectory, raw)


def test_verification_retains_raw_mcp_with_normalized_paths_missing_from_export(evidence):
    trajectory, business = evidence
    exported = trajectory["steps"][0]["tool_calls"][0]
    exported.update(environment_record=None, arguments={"path": "scope/structure.vasp"})
    business[0]["arguments"] = {"path": "structure.vasp"}
    calls = verification_calls(trajectory, business)

    assert calls[0] == business[0]
    assert calls[0]["arguments"] == {"path": "structure.vasp"}
    assert calls[1]["tool"] == "Read"


def test_verification_retains_every_raw_record_even_if_unattached_or_repeated(evidence):
    trajectory, business = evidence
    extra = {**business[0], "arguments": {"checkpoint": "s5"}}
    raw = [business[0], extra, deepcopy(business[0])]
    calls = verification_calls(trajectory, raw)

    assert calls[:3] == raw
    assert len(calls) == 4


def test_unfinished_unmatched_cli_attempt_is_not_invented_as_an_executed_call():
    attempt = {"tool_kind": "environment_mcp", "name": "Bash", "is_error": False}

    assert verification_calls({"steps": [{"tool_calls": [attempt]}]}, []) == []


def test_verification_retains_cli_mcp_rejections_without_inventing_raw_records():
    rejected = {
        "tool_kind": "environment_mcp", "name": "mcp__workspace__missing",
        "arguments": {}, "output": {"output": "Unknown tool"}, "is_error": True,
        "call_id": "rejected-1", "completed_event_sequence": 12, "environment_record": None,
    }
    calls = verification_calls({"steps": [{"tool_calls": [rejected]}]}, [])

    assert calls[0]["tool"] == rejected["name"]
    assert calls[0]["result"] == rejected["output"]
    assert calls[0]["error"] == "environment_mcp_error"


@pytest.mark.parametrize("change", ["unfinished", "unknown_tool", "unknown_kind"])
def test_verification_rejects_incomplete_or_unsupported_native_evidence(evidence, change):
    trajectory, business = evidence
    native = trajectory["steps"][0]["tool_calls"][1]
    if change == "unfinished":
        native.pop("completed_event_sequence")
    elif change == "unknown_tool":
        native["name"] = "Bash"
    else:
        native["tool_kind"] = "unknown"
    with pytest.raises(ValueError):
        verification_calls(trajectory, business)


def test_mcp_only_trajectory_retains_original_raw_records(evidence):
    trajectory, business = evidence
    trajectory["steps"][0]["tool_calls"].pop()

    assert verification_calls(trajectory, business) == business
    assert verification_calls({"steps": []}, []) == []


def test_immediate_verifier_receives_native_evidence_and_raw_mutation_count(tmp_path, monkeypatch, evidence):
    from distill import taskgen_verifier as verifier

    trajectory, business = evidence
    trajectory["outcome"] = {"final_answer": "RELEASE"}
    native = trajectory["steps"][0]["tool_calls"][1]
    native["name"] = "Read"
    task_dir = tmp_path / "task"
    (task_dir / "raw").mkdir(parents=True)
    (task_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    (task_dir / "raw/environment_tool_calls.jsonl").write_text(
        json.dumps(business[0]) + "\n", encoding="utf-8",
    )
    environment = {"tools": [
        {"name": "export_structure_formats", "usageConditions": {"sideEffects": True}},
        {"name": "Read", "usageConditions": {"sideEffects": True}},
    ]}
    captured = {}

    def inspect_execution(**kwargs):
        captured.update(kwargs)
        return {"outcome": "pass", "summary": "Complete evidence", "failure_causes": []}

    monkeypatch.setattr(verifier, "_verifier_environment", lambda case: environment)
    monkeypatch.setattr(verifier, "reconstruct_actual_state", lambda *args: None)
    monkeypatch.setattr(verifier, "verify_execution", inspect_execution)
    case = SimpleNamespace(
        task={"task_id": "example"}, initial_state=tmp_path / "initial",
        reference_state=None, reference_calls=[],
    )
    result = verifier.verify_distilled_case(
        case, case_name="example", task_dir=task_dir, verifier_root=tmp_path / "verifiers",
        codex_home=tmp_path / "codex", model="test", reasoning_effort="high",
        timeout_seconds=10, attempts=1,
    )

    assert result["status"] == "verified"
    assert [call["tool"] for call in captured["calls"]] == ["export_structure_formats", "Read"]
    assert captured["answer"] == "RELEASE"
    assert captured["execution"]["mutation_count"] == 1
    assert captured["execution"]["call_source"] == "raw_mcp_and_exported_native_cli_records"
    assert captured["config"]["submission_preflight"] is True
    manifest = captured["execution"]["cli_tool_call_manifest"]
    assert manifest[0]["call_id"] == "export-1"
    assert manifest[0]["raw_record_attached"] is True
    assert manifest[1]["name"] == "Read"
    assert manifest[1]["completed_event_sequence"] == 10
    assert "not a joint timeline" in captured["execution"]["call_order"]


def test_incomplete_evidence_is_a_verification_error_not_a_task_failure(tmp_path, monkeypatch, evidence):
    from distill import taskgen_verifier as verifier

    trajectory, business = evidence
    trajectory["steps"][0]["tool_calls"][1].pop("completed_event_sequence")
    task_dir = tmp_path / "task"
    (task_dir / "raw").mkdir(parents=True)
    (task_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    (task_dir / "raw/environment_tool_calls.jsonl").write_text(
        json.dumps(business[0]) + "\n", encoding="utf-8",
    )

    def reject_grading(**kwargs):
        pytest.fail("Incomplete evidence must not reach the model grader")

    monkeypatch.setattr(verifier, "verify_execution", reject_grading)
    result = verifier.verify_distilled_case(
        SimpleNamespace(), case_name="example", task_dir=task_dir,
        verifier_root=tmp_path / "verifiers", codex_home=tmp_path / "codex",
        model="test", reasoning_effort="high", timeout_seconds=10, attempts=1,
    )

    assert result["status"] == "verification_error"
    assert result["outcome"] is None
    assert "Native call has no completed CLI result" in result["error"]
