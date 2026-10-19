"""Resolve an author's explicit final submission to its recorded complete proposal."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shlex
import sys


PROPOSAL_FIELDS = frozenset({"task_text", "real_need", "reference_answer", "solution_calls",
                             "answer_sources", "validation_id", "self_check", "self_check_notes"})


def check_proposal_fields(proposal) -> None:
    if not isinstance(proposal, dict):
        raise ValueError("complete proposal must be an object with fields: " + ", ".join(sorted(PROPOSAL_FIELDS)))
    missing = sorted(PROPOSAL_FIELDS - set(proposal))
    unexpected = sorted(set(proposal) - PROPOSAL_FIELDS)
    if missing or unexpected:
        raise ValueError(f"complete proposal fields differ; missing={missing}, unexpected={unexpected}; "
                         "required fields: " + ", ".join(sorted(PROPOSAL_FIELDS)))


def resolve_submission(answer: str, reports: list[dict]) -> tuple[dict, dict]:
    text = answer.strip()
    if text.startswith("```json\n") or text.startswith("```\n"):
        if not text.endswith("```"):
            raise ValueError("author submission has an incomplete JSON fence")
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    submission = json.loads(text)
    if not isinstance(submission, dict):
        raise ValueError("author submission must be a complete JSON object")
    if set(submission) == PROPOSAL_FIELDS:
        return submission, {"kind": "full_proposal", "validation_id": submission["validation_id"]}
    if set(submission) != {"validation_id"}:
        raise ValueError("submit a validation receipt ID or the complete proposal, without extra fields")
    identifier = submission["validation_id"]
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("receipt submission requires a nonempty validation ID")
    matches = [report for report in reports if report.get("result", {}).get("validation_id") == identifier]
    if len(matches) != 1:
        raise ValueError("receipt submission requires exactly one matching validation record")
    record = matches[0]
    if (record["result"].get("execution_passed") is not True
            or record["result"].get("source_check_passed") is not True):
        raise ValueError("receipt submission requires successful execution and complete source checking")
    proposal = record.get("request", {}).get("proposal")
    if (not isinstance(proposal, dict)
            or set(proposal) - {"validation_id"} != PROPOSAL_FIELDS - {"validation_id"}):
        raise ValueError("receipt must contain the complete author-validated proposal")
    proposal = {**deepcopy(proposal), "validation_id": identifier}
    return proposal, {"kind": "validation_receipt", "validation_id": identifier,
                      "proposal_source": "matching_complete_author_validation_request",
                      "new_execution": False, "semantic_correctness_proved": False}


def build_stop_hook_command(raw: Path) -> str:
    return shlex.join([sys.executable, str(Path(__file__).resolve()), "--stop-check", str(raw.resolve())])


def submission_stop_check(raw: Path) -> dict:
    def records(filename):
        path = raw / filename
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []

    try:
        validations = records("solution_validations.jsonl")
        submissions = records("solution_submissions.jsonl")
        if submissions and submissions[-1].get("accepted") is True:
            identifier = submissions[-1]["validation_id"]
            resolve_submission(json.dumps({"validation_id": identifier}), validations)
            return {"allowed": True, "validation_id": identifier}
        latest = validations[-1]["result"] if validations else {}
        errors = latest.get("source_check_errors", [])
        reason = ("作者方案尚未完成提交。请在当前会话调用 validate_solution_calls 校验完整方案，"
                  "修正全部错误后调用 submit_validated_solution；accepted=true 后再返回 validation_id。")
        if latest:
            reason += (f" 最近校验 execution_passed={latest.get('execution_passed')}, "
                       f"source_check_passed={latest.get('source_check_passed')}, "
                       f"failed_call={latest.get('failed_call')}。")
        if errors:
            reason += " 错误：" + "; ".join(str(error) for error in errors)
        return {"allowed": False, "reason": reason}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"allowed": False, "reason": f"作者提交证据无法核对：{error}。请重新完整校验并提交成功 ID。"}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--stop-check":
        raise SystemExit("usage: author_submission.py --stop-check RAW_DIRECTORY")
    raw = Path(sys.argv[2])
    decision = submission_stop_check(raw)
    with (raw / "author_stop_checks.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
    if decision["allowed"]:
        return 0
    print(decision["reason"], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
