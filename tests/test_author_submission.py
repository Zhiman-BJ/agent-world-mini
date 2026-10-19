from copy import deepcopy
import json

import pytest

from task_gen.program.author_submission import resolve_submission, submission_stop_check


@pytest.fixture
def receipt():
    proposal = {"task_text": "完整用户委托", "real_need": "真实用途", "reference_answer": "实测答案",
                "solution_calls": [{"tool": "inspect", "arguments": {}, "argument_sources": {},
                                    "purpose": "取得实际依据"}],
                "answer_sources": [{"kind": "call", "call": 1, "pointer": "/data"}],
                "self_check": {"checked": True}, "self_check_notes": ["作者自查不等于独立判定"]}
    return {"request": {"proposal": proposal}, "result": {"validation_id": "success-2",
            "execution_passed": True, "source_check_passed": True}}


def test_receipt_submission_selects_the_explicit_successful_complete_record(receipt):
    older = deepcopy(receipt)
    older["result"]["validation_id"] = "success-1"
    older["request"]["proposal"]["task_text"] = "另一版题目"

    proposal, binding = resolve_submission('{"validation_id":"success-2"}', [older, receipt])

    assert proposal == {**receipt["request"]["proposal"], "validation_id": "success-2"}
    assert binding["kind"] == "validation_receipt"
    assert binding["new_execution"] is False
    assert binding["semantic_correctness_proved"] is False
    proposal["self_check_notes"].append("不会修改原始记录")
    assert len(receipt["request"]["proposal"]["self_check_notes"]) == 1


@pytest.mark.parametrize("mutation", ["absent", "duplicate", "execution_failed", "source_failed", "partial_proposal"])
def test_receipt_submission_never_falls_back_or_bypasses_validation(receipt, mutation):
    records = [receipt]
    if mutation == "absent":
        receipt["result"]["validation_id"] = "another"
    elif mutation == "duplicate":
        records.append(deepcopy(receipt))
    elif mutation == "execution_failed":
        receipt["result"]["execution_passed"] = False
    elif mutation == "source_failed":
        receipt["result"]["source_check_passed"] = False
    else:
        receipt["request"]["proposal"].pop("reference_answer")

    with pytest.raises(ValueError):
        resolve_submission('{"validation_id":"success-2"}', records)


@pytest.mark.parametrize("answer", [
    '{"validation_id":""}', '{"validation_id":null}', '{"validation_id":[]}',
    '{"validation_id":"success-2","task_text":"changed"}',
    '{"tool":"inspect","arguments":{}}',
    'prefix {"validation_id":"success-2"}',
    '{"validation_id":"success-2"} trailing',
    '}, {"tool":"inspect"}',
    '[{"validation_id":"success-2"}]',
])
def test_submission_rejects_fragments_extra_fields_and_implicit_repairs(receipt, answer):
    with pytest.raises(ValueError):
        resolve_submission(answer, [receipt])


@pytest.mark.parametrize("fence", ["", "```json\n", "```\n"])
def test_complete_legacy_submission_is_not_silently_replaced_by_a_receipt(receipt, fence):
    submitted = {**deepcopy(receipt["request"]["proposal"]), "validation_id": "success-2"}
    submitted["self_check_notes"] = ["changed after validation"]
    text = json.dumps(submitted)
    if fence:
        text = fence + text + "\n```"

    proposal, binding = resolve_submission(text, [receipt])

    assert proposal == submitted
    assert proposal != {**receipt["request"]["proposal"], "validation_id": "success-2"}
    assert binding["kind"] == "full_proposal"


def test_stop_check_returns_validation_errors_to_the_same_session(tmp_path, receipt):
    receipt["result"].update(source_check_passed=False,
                             source_check_errors=["call 15: missing public source"])
    (tmp_path / "solution_validations.jsonl").write_text(json.dumps(receipt) + "\n")
    decision = submission_stop_check(tmp_path)
    assert decision["allowed"] is False
    assert "call 15: missing public source" in decision["reason"]
    assert "submit_validated_solution" in decision["reason"]


def test_stop_check_requires_a_real_successful_submitted_validation(tmp_path, receipt):
    (tmp_path / "solution_validations.jsonl").write_text(json.dumps(receipt) + "\n")
    assert submission_stop_check(tmp_path)["allowed"] is False
    (tmp_path / "solution_submissions.jsonl").write_text(
        json.dumps({"accepted": True, "validation_id": "success-2"}) + "\n")
    assert submission_stop_check(tmp_path) == {"allowed": True, "validation_id": "success-2"}
    receipt["result"]["source_check_passed"] = False
    (tmp_path / "solution_validations.jsonl").write_text(json.dumps(receipt) + "\n")
    assert submission_stop_check(tmp_path)["allowed"] is False


def test_stop_hook_exits_two_for_missing_submission(tmp_path):
    import subprocess
    import sys
    from task_gen.program import author_submission

    result = subprocess.run([sys.executable, author_submission.__file__, "--stop-check", str(tmp_path)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "validate_solution_calls" in result.stderr
    assert json.loads((tmp_path / "author_stop_checks.jsonl").read_text())["allowed"] is False
