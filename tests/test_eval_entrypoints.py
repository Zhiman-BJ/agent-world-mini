import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.__main__ import main
from eval.existing import evidence_arguments
from eval.report import summarize
from eval.verifier import prepare_verification_workspace


def test_old_verifier_import_is_the_same_module():
    current = importlib.import_module("eval.verifier")
    legacy = importlib.import_module("task_gen.task_eval_verifier")
    assert legacy is current
    with patch("task_gen.task_eval_verifier._VerifierClient") as client:
        assert current._VerifierClient is client


@pytest.mark.parametrize("command,forwarded", [("run", "taskgen"), ("visualize", "visualize")])
def test_eval_reuses_distill_cli_validation(command, forwarded):
    with patch("distill.__main__.main") as run:
        main([command, "--help"])
    run.assert_called_once_with([forwarded, "--help"])


def prepared_evidence(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    (initial / "record.json").write_text('{"value": 8}')
    return prepare_verification_workspace(
        tmp_path / "evidence-package", task={"task_id": "t1", "task_text": "Return value."},
        environment={"environment_id": "e1", "tools": []},
        initial_state=initial, actual_state=initial, calls=[], answer="8", execution={},
    )


def test_existing_evidence_adapter_and_prepare_only(tmp_path):
    root = prepared_evidence(tmp_path)
    evidence = evidence_arguments(root / "request.json")
    assert evidence["answer"] == "8"
    assert evidence["environment"]["tools"] == []
    assert evidence["actual_state"] == root / "evidence/actual/final"
    output = tmp_path / "new-verification"
    with patch("eval.existing.verify_execution") as verify:
        main(["verify-existing", "--request", str(root / "request.json"),
              "--output", str(output), "--prepare-only"])
    verify.assert_not_called()
    assert (output / "evidence/state_runtime.py").is_file()
    assert (output / "evidence/actual/answer.txt").read_text() == "8"


@pytest.mark.parametrize("bad_path", ["../initial", "/tmp"])
def test_existing_evidence_rejects_external_paths(tmp_path, bad_path):
    root = prepared_evidence(tmp_path)
    request_path = root / "request.json"
    request = json.loads(request_path.read_text())
    request["initial"] = bad_path
    request_path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="Invalid evidence location"):
        evidence_arguments(request_path)


def test_existing_evidence_does_not_write_into_source(tmp_path):
    root = prepared_evidence(tmp_path)
    with pytest.raises(SystemExit) as result:
        main(["verify-existing", "--request", str(root / "request.json"),
              "--output", str(root / "new"), "--prepare-only"])
    assert result.value.code == 2
    assert not (root / "new").exists()


def write_summary(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"results": rows}))
    return path


def test_report_fixed_denominator_and_interruption_policy(tmp_path):
    source = write_summary(tmp_path / "summary.json", [
        {"case": "pass", "status": "completed", "verification": {"status": "verified", "outcome": "pass"}},
        {"case": "fail", "verification": {"status": "verified", "outcome": "fail"}},
        {"case": "budget", "status": "failed", "tool_budget_stop": {"reason": "total_tool_call_limit_exceeded"}},
        {"case": "cancelled", "status": "cancelled",
         "verification": {"status": "verified", "outcome": "pass"}},
        {"case": "unverified", "status": "completed"},
        {"case": "verifier-error", "verification": {"status": "verification_error"}},
    ])
    report = summarize([source], 7, interruption_policy="separate")
    assert report["model_pass"] == 1
    assert report["model_fail"] == 2
    assert report["unresolved"] == 4
    assert report["counts"]["not_started"] == 1
    assert report["fixed_denominator_pass_fraction"] == 1 / 7
    assert report["decided_pass_rate"] == 1 / 3
    strict = summarize([source], 7, interruption_policy="fail")
    assert strict["model_fail"] == 3
    assert strict["unresolved"] == 3
    assert not strict["complete"]


def test_report_retries_are_explicit_and_duplicate_cases_are_rejected(tmp_path):
    first = write_summary(tmp_path / "first.json", [{"case": "t1", "status": "failed"}])
    second = write_summary(tmp_path / "second.json", [{"case": "t1", "status": "completed",
                          "verification": {"status": "verified", "outcome": "pass"}}])
    report = summarize([first, second], 1, interruption_policy="fail")
    assert report["model_pass"] == 1 and report["complete"]
    assert report["replacements"][0]["previous"] == str(first)
    invalid = write_summary(tmp_path / "duplicate.json", [{"case": "t1"}, {"case": "t1"}])
    with pytest.raises(ValueError, match="duplicate"):
        summarize([invalid], 1, interruption_policy="fail")


def test_report_refuses_to_overwrite_source(tmp_path):
    source = write_summary(tmp_path / "summary.json", [])
    original = source.read_bytes()
    with pytest.raises(SystemExit) as result:
        main(["report", "--summary", str(source), "--expected", "1",
              "--interruption-policy", "fail", "--output", str(source)])
    assert result.value.code == 1
    assert source.read_bytes() == original
