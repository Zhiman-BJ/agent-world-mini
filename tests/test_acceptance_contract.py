from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from task_gen.acceptance_contract import PROFILE, strict_policy, text_sha256, validate_acceptance_contract
from task_gen.task_eval_verifier import prepare_verification_workspace, run_verification_agent, validate_verdict
from task_gen.program.utils.io import write_json


@pytest.fixture
def fixed_checklist(tmp_path):
    task = {"task_id": "task", "task_text": "Deliver value A. Also deliver value B."}
    middle = task["task_text"].index("Also")
    requirements = []
    for index, (start, end) in enumerate(((0, middle), (middle, len(task["task_text"]))), 1):
        quote = task["task_text"][start:end]
        requirements.append({"id": f"R{index}", "task_quote": quote, "requirement": quote.strip(),
                             "source_start": start, "source_end": end})
    contract = {"schema_version": 1, "task_id": task["task_id"],
                "task_text_sha256": text_sha256(task["task_text"]), "requirements": requirements}
    initial = tmp_path / "initial"
    initial.mkdir()
    return task, contract, initial


def prepare(tmp_path, task, contract, initial):
    root = prepare_verification_workspace(
        tmp_path / "verification", task=task, environment={}, initial_state=initial,
        actual_state=initial, calls=[], answer="A=17", execution={},
        verification_profile=PROFILE, acceptance_contract=contract)
    (root / "scratch/investigation.md").write_text("R1 A is delivered. R2 B is missing.")
    return root


def verdict(contract):
    return {"task_id": contract["task_id"], "requirements": [{
        "id": row["id"], "task_quote": row["task_quote"], "requirement": row["requirement"],
        "analysis": "Evidence checked", "passed": True,
        "evidence": [{"path": "evidence/actual/answer.txt", "locator": "line 1", "finding": "A=17"}],
    } for row in contract["requirements"]], "non_blocking_issues": [], "summary": "Checked",
        "failure_causes": [], "outcome": "pass"}


def test_contract_binds_exact_task_and_source_coverage(fixed_checklist):
    task, contract, _ = fixed_checklist
    assert validate_acceptance_contract(contract, task) == contract
    assert strict_policy(PROFILE, contract, task)["orchestration_only"] is True
    with pytest.raises(ValueError, match="exact task text"):
        validate_acceptance_contract(contract, {**task, "task_text": task["task_text"] + " Extra requirement."})
    missing = {**contract, "requirements": contract["requirements"][:1]}
    with pytest.raises(ValueError, match="uncovered"):
        validate_acceptance_contract(missing, task)


@pytest.mark.parametrize("field,value", [("source_start", True), ("source_end", 999), ("task_quote", "Invented"), ("id", "R8")])
def test_contract_rejects_unbound_or_unstable_rows(fixed_checklist, field, value):
    task, contract, _ = fixed_checklist
    changed = deepcopy(contract)
    changed["requirements"][0][field] = value
    with pytest.raises(ValueError):
        validate_acceptance_contract(changed, task)


def test_host_rejects_old_a_only_false_pass(tmp_path, fixed_checklist):
    task, contract, initial = fixed_checklist
    root = prepare(tmp_path, task, contract, initial)
    incomplete = verdict(contract)
    incomplete["requirements"] = incomplete["requirements"][:1]
    with pytest.raises(ValueError, match="every frozen requirement"):
        validate_verdict(incomplete, root)
    complete = verdict(contract)
    complete["requirements"][1]["passed"] = False
    complete.update(outcome="fail", failure_causes=["B was not delivered"])
    assert validate_verdict(complete, root)["outcome"] == "fail"


def test_verifier_cannot_weaken_frozen_requirement(tmp_path, fixed_checklist):
    task, contract, initial = fixed_checklist
    root = prepare(tmp_path, task, contract, initial)
    weaker = verdict(contract)
    weaker["requirements"][1]["requirement"] = "B is optional"
    with pytest.raises(ValueError, match="changed frozen requirement R2"):
        validate_verdict(weaker, root)


def test_contract_evidence_digest_cannot_be_changed(tmp_path, fixed_checklist):
    task, contract, initial = fixed_checklist
    root = prepare(tmp_path, task, contract, initial)
    changed = deepcopy(contract)
    changed["requirements"][1]["requirement"] = "B is optional"
    write_json(root / "evidence/acceptance_contract.json", changed)
    with pytest.raises(ValueError, match="digest changed"):
        validate_verdict(verdict(changed), root)


def test_strict_agent_uses_same_policy_and_records_prompt(tmp_path, fixed_checklist):
    task, contract, initial = fixed_checklist
    root = prepare(tmp_path, task, contract, initial)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('model="test"')
    with patch("task_gen.task_eval_verifier._VerifierClient.run", return_value=json.dumps(verdict(contract))) as agent:
        assert run_verification_agent(root, {"codex_home": str(home)})["outcome"] == "pass"
    prompt = agent.call_args.args[0]
    assert "已冻结的验收清单" in prompt
    assert "本轮新增计算权限约束" in prompt
    assert "提交前格式自检" in prompt
    saved = json.loads((root / "logs/verification_policy.json").read_text())
    assert saved["verification_profile"] == PROFILE
    assert saved["effective_prompt_sha256"] == text_sha256(prompt)


def test_strict_policy_rejects_relaxed_flags(tmp_path, fixed_checklist):
    task, contract, initial = fixed_checklist
    root = prepare(tmp_path, task, contract, initial)
    with pytest.raises(ValueError, match="requires orchestration_only"):
        run_verification_agent(root, {"orchestration_only": False})


def test_strict_configuration_cannot_reuse_a_legacy_workspace(tmp_path, fixed_checklist):
    task, _, initial = fixed_checklist
    root = prepare_verification_workspace(tmp_path / "legacy", task=task, environment={}, initial_state=initial,
                                          actual_state=initial, calls=[], answer="A=17", execution={})
    with pytest.raises(ValueError, match="prepared verification profile"):
        run_verification_agent(root, {"verification_profile": PROFILE})


def test_contract_requires_explicit_profile(fixed_checklist):
    task, contract, _ = fixed_checklist
    with pytest.raises(ValueError, match="explicit verification profile"):
        strict_policy(None, contract, task)
