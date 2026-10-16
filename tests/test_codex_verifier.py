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


def test_verifier_evidence_uses_same_volume_hard_links(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    source = initial / "large-result.bin"
    source.write_bytes(b"result")

    root = prepare_verification_workspace(
        tmp_path / "audit-links",
        task={"task_id": "t1", "task_text": "Return value."},
        environment={},
        initial_state=initial,
        actual_state=initial,
        calls=[],
        answer="result",
        execution={},
    )

    assert (root / "evidence/initial/large-result.bin").stat().st_ino == source.stat().st_ino
    assert (root / "evidence/actual/final/large-result.bin").stat().st_ino == source.stat().st_ino


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
    script = f"""import os
from pathlib import Path
assert not Path({str(hidden)!r}).exists()
assert not os.path.exists('/proc/1/root' + {str(hidden)!r})
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


def test_verdict_quote_error_identifies_requirement_without_changing_acceptance(tmp_path):
    root = prepare(tmp_path)
    value = verdict()
    value["requirements"][0]["task_quote"] = "Return the value."
    with pytest.raises(ValueError, match="requirement 'R1': task_quote is not an exact task excerpt") as failure:
        validate_verdict(value, root)
    assert "Return the value." in str(failure.value)
    assert "contiguous substring" in str(failure.value)
    assert validate_verdict(verdict(), root)["outcome"] == "pass"
    value = verdict()
    value["requirements"].append(dict(value["requirements"][0]))
    with pytest.raises(ValueError, match="duplicate requirement id 'R1'"):
        validate_verdict(value, root)


def test_retry_receives_specific_invalid_quote_instead_of_generic_schema_message(tmp_path):
    root = prepare(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('model="test"')
    invalid = verdict()
    invalid["requirements"][0]["task_quote"] = "Return the value."
    with patch("task_gen.task_eval_verifier._VerifierClient.run", side_effect=[json.dumps(invalid), json.dumps(verdict())]) as run:
        result = run_verification_agent(root, {"codex_home": str(home)})
    assert result["outcome"] == "pass"
    assert "requirement 'R1'" in run.call_args_list[1].args[0]
    assert "Return the value." in run.call_args_list[1].args[0]
    assert "contiguous substring" in run.call_args_list[1].args[0]


@pytest.mark.parametrize("enabled", [None, False, True])
def test_submission_preflight_is_explicit_and_does_not_change_default_prompt(tmp_path, enabled):
    root = prepare(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('model="test"')
    config = {"codex_home": str(home)}
    if enabled is not None:
        config["submission_preflight"] = enabled
    with patch("task_gen.task_eval_verifier._VerifierClient.run", return_value=json.dumps(verdict())) as run:
        assert run_verification_agent(root, config)["outcome"] == "pass"
    prompt = run.call_args.args[0]
    assert ("提交前格式自检" in prompt) == (enabled is True)
    assert prompt.startswith((root / "prompt.txt").read_text())


def test_submission_preflight_does_not_accept_an_invalid_quote(tmp_path):
    root = prepare(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('model="test"')
    invalid = verdict()
    invalid["requirements"][0]["task_quote"] = "Return the value."
    with patch("task_gen.task_eval_verifier._VerifierClient.run", return_value=json.dumps(invalid)):
        with pytest.raises(VerificationError):
            run_verification_agent(root, {
                "codex_home": str(home), "submission_preflight": True, "attempts": 1,
            })
    assert not (root / "verdict.json").exists()


def test_verifier_rejects_missing_initial_state(tmp_path):
    initial = tmp_path / "initial"
    initial.mkdir()
    with pytest.raises(VerificationError, match="Missing state"):
        verify_execution(run_dir=tmp_path / "missing-evidence",
                         task={"task_id": "t1", "task_text": "Return 8."},
                         environment=ENVIRONMENT, initial_state=tmp_path / "absent",
                         actual_state=initial, calls=[], answer="8", execution={})
