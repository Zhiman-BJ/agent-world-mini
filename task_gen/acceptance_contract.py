"""Bind a fixed acceptance checklist to its unchanged public task text."""

from __future__ import annotations

import hashlib
import json


PROFILE = "frozen_requirements_v1"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def contract_sha256(contract: dict) -> str:
    return text_sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True, allow_nan=False))


def validate_acceptance_contract(contract: dict, task: dict) -> dict:
    if not isinstance(contract, dict) or set(contract) != {
        "schema_version", "task_id", "task_text_sha256", "requirements",
    }:
        raise ValueError("acceptance contract fields do not match the output contract")
    text = task["task_text"]
    if (type(contract["schema_version"]) is not int or contract["schema_version"] != 1
            or contract["task_id"] != task["task_id"]
            or contract["task_text_sha256"] != text_sha256(text)):
        raise ValueError("acceptance contract is not bound to this exact task text")
    requirements = contract["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("acceptance contract must contain requirements")
    covered = set()
    for index, requirement in enumerate(requirements, 1):
        if not isinstance(requirement, dict) or set(requirement) != {
            "id", "task_quote", "requirement", "source_start", "source_end",
        }:
            raise ValueError("invalid acceptance requirement fields")
        if requirement["id"] != f"R{index}":
            raise ValueError("acceptance requirement ids must be consecutive and stable")
        start, end = requirement["source_start"], requirement["source_end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or requirement["task_quote"] != text[start:end]):
            raise ValueError(f"acceptance requirement {requirement['id']} has an invalid source span")
        if (not isinstance(requirement["requirement"], str) or not requirement["requirement"].strip()
                or not text[start:end].strip()):
            raise ValueError("acceptance requirement and source quote must be nonempty")
        covered.update(range(start, end))
    missing = [index for index, character in enumerate(text) if not character.isspace() and index not in covered]
    if missing:
        raise ValueError(f"acceptance contract leaves public task text uncovered at character {missing[0]}")
    return contract


def strict_policy(profile: str | None, contract: dict | None, task: dict) -> dict:
    if profile is None:
        if contract is not None:
            raise ValueError("acceptance contracts require an explicit verification profile")
        return {}
    if profile != PROFILE:
        raise ValueError(f"unknown verification profile: {profile}")
    validate_acceptance_contract(contract, task)
    return {"verification_profile": PROFILE, "acceptance_contract_sha256": contract_sha256(contract),
            "orchestration_only": True, "submission_preflight": True}
