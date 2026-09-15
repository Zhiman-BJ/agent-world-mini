import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from task_gen.task_eval_verifier import (
    prepare_verification_workspace, sandbox_command, validate_verdict,
    run_verification_agent, verify_execution, VerificationError,
)

PUBLIC_TOOL = {"name": "read", "description": "Read value", "inputSchema": {"type": "object"},
               "outputSchema": {"type": "object"}}
ENVIRONMENT = {"tools": [{**PUBLIC_TOOL, "internal": {"code": "def run(arguments, context):\n    return {'success': True}"}}]}


def prepare(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    (initial / "record.json").write_text('{"value": 8}')
    root = prepare_verification_workspace(tmp_path / "audit", task={"task_id": "t1", "task_text": "Return value."},
        environment={}, initial_state=initial, actual_state=initial, calls=[], answer="8", execution={})
    (root / "scratch/investigation.md").write_text("R1: inspected record.json value 8 against answer 8.")
    return root


def verdict():
    return {"task_id": "t1", "requirements": [{"id": "R1", "task_quote": "Return value.",
        "requirement": "Return the recorded value", "analysis": "Record and answer both show 8",
        "evidence": [{"path": "evidence/actual/answer.txt", "locator": "line 1", "finding": "8"}],
        "passed": True}], "non_blocking_issues": [], "summary": "Delivered", "failure_causes": [], "outcome": "pass"}


def test_os_sandbox_protects_evidence_and_hides_host(tmp_path):
    root = prepare(tmp_path)
    auth = tmp_path / "auth"
    auth.mkdir()
    hidden = tmp_path / "host-secret"
    hidden.write_text("not accessible")
    prefix = sandbox_command(root, auth, runtime_paths=[])
    script = f"""from pathlib import Path
assert not Path({str(hidden)!r}).exists()
p = Path('evidence/actual/answer.txt')
assert p.read_text() == '8'
try:
    p.write_text('forged')
except OSError:
    pass
else:
    raise AssertionError('evidence was writable')
Path('scratch/check.txt').write_text('verified')
"""
    result = subprocess.run([*prefix, "/usr/bin/python3", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (root / "scratch/check.txt").read_text() == "verified"
    assert (tmp_path / "initial/record.json").read_text() == '{"value": 8}'


def test_verdict_rejects_reference_errors_and_inconsistent_pass(tmp_path):
    root = prepare(tmp_path)
    assert validate_verdict(verdict(), root)["outcome"] == "pass"
    value = verdict()
    value["requirements"][0]["evidence"][0]["path"] = "../initial/record.json"
    with pytest.raises(ValueError):
        validate_verdict(value, root)
    value = verdict()
    value["requirements"][0]["passed"] = False
    with pytest.raises(ValueError):
        validate_verdict(value, root)
    value = verdict()
    value["requirements"][0]["task_quote"] = "Invented requirement"
    with pytest.raises(ValueError):
        validate_verdict(value, root)
    (root / "scratch/investigation.md").unlink()
    with pytest.raises(ValueError, match="direct investigation"):
        validate_verdict(verdict(), root)


def test_retries_invalid_result_and_preserves_failure(tmp_path):
    root = prepare(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('model="test"')
    with patch("task_gen.task_eval_verifier._VerifierClient.run", side_effect=["{}", json.dumps(verdict())]) as run:
        assert run_verification_agent(root, {"codex_home": str(home)})["outcome"] == "pass"
        assert run.call_count == 2
    history = json.loads((root / "logs/attempts.json").read_text())
    assert history[0]["error"] and history[1]["error"] is None
    with patch("task_gen.task_eval_verifier._VerifierClient.run", return_value="{}"):
        with pytest.raises(VerificationError):
            run_verification_agent(root, {"codex_home": str(home), "attempts": 1})


def test_interrupted_solver_still_supplies_real_trace_to_verifier(tmp_path):
    from task_gen.task_eval import EvalCase, evaluate_case
    initial = tmp_path / "initial"
    initial.mkdir()
    case = EvalCase(tmp_path, {"task_id": "t1", "task_text": "Write result.", "available_tools": [PUBLIC_TOOL]},
                    ENVIRONMENT, initial, None, [])
    def solver(prompt, state, config, trace):
        (state / "partial.txt").write_text("partial work")
        trace.write_text(json.dumps({"tool": "write", "result": {"success": True}}) + "\n")
        raise RuntimeError("context exhausted")
    def verifier(**kwargs):
        assert len(kwargs["calls"]) == 1
        assert "context exhausted" in kwargs["execution"]["error"]
        assert (kwargs["actual_state"] / "partial.txt").read_text() == "partial work"
        assert kwargs["config"] == {}  # Solver model must not select the verifier model.
        return {"outcome": "fail", "requirements": [], "summary": "Incomplete"}
    result = evaluate_case(case, tmp_path / "state", {"model": "qwen"},
                           agent_run_fn=solver, verifier_run_fn=verifier)
    assert result["outcome"] == "fail"
    assert result["tool_calls"] and "context exhausted" in result["agent_error"]


def test_verifier_outage_is_not_a_task_failure(tmp_path):
    from task_gen.task_eval import EvalCase, evaluate_case
    initial = tmp_path / "initial"
    initial.mkdir()
    case = EvalCase(tmp_path, {"task_id": "t1", "task_text": "Return 8.", "available_tools": [PUBLIC_TOOL]},
                    ENVIRONMENT, initial, None, [])
    def unavailable(**kwargs):
        raise VerificationError("API unavailable")
    result = evaluate_case(case, tmp_path / "state", {}, agent_run_fn=lambda *args: "8",
                           verifier_run_fn=unavailable)
    assert result["outcome"] == "verification_error"
    assert result["agent_answer"] == "8"
    with pytest.raises(VerificationError, match="Missing state"):
        verify_execution(run_dir=tmp_path / "missing-evidence", task=case.task,
                         environment=ENVIRONMENT, initial_state=tmp_path / "absent",
                         actual_state=initial, calls=[], answer="8", execution={})
