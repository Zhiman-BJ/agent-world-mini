from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agent_world_mini.runtime import LocalToolRuntime


def anchors(value: Any, field: str = "") -> list[str]:
    ignored = {"entity_id", "source_url", "entity_type", "color", "profile_url", "query"}
    if isinstance(value, dict):
        values: list[str] = []
        for key, child in value.items():
            if key in ignored or key.endswith("_id") or key in {"rxcui", "pubchem_cid", "cas_registry_number"}:
                continue
            values.extend(anchors(child, key))
        return list(dict.fromkeys(values))
    if isinstance(value, list):
        return list(dict.fromkeys(item for child in value for item in anchors(child, field)))
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def relevant_anchors(values: list[str], request: str) -> list[str]:
    request_tokens = {part for part in re.findall(r"[a-z0-9]+", request.lower()) if len(part) >= 4}
    textual, numeric = [], []
    for value in values:
        normalized = " ".join(re.findall(r"[a-z0-9]+", value.lower()))
        if not normalized:
            continue
        try:
            float(value)
            is_numeric = True
        except (TypeError, ValueError):
            is_numeric = False
        if is_numeric:
            numeric.append(value)
        elif set(normalized.split()) & request_tokens:
            textual.append(value)
    return list(dict.fromkeys(textual + numeric if textual else values))


def entity_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        values = [str(value["entity_id"])] if value.get("entity_id") not in (None, "") else []
        for child in value.values():
            values.extend(entity_ids(child))
        return list(dict.fromkeys(values))
    if isinstance(value, list):
        return list(dict.fromkeys(item for child in value for item in entity_ids(child)))
    return []


def entity_records(value: Any) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        entity_id = item.get("entity_id")
        if entity_id not in (None, ""):
            records[str(entity_id)] = item
            return
        for child in item.values():
            visit(child)

    visit(value)
    return list(records.values())


def contains(text: str, value: str) -> bool:
    normalize = lambda item: " ".join(re.findall(r"[a-z0-9]+", item.lower()))
    needle = normalize(value)
    if needle and needle in normalize(text):
        return True
    date = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if date:
        months = (
            "january february march april may june july august september october november december"
        ).split()
        month = int(date.group(2))
        if 1 <= month <= 12 and normalize(f"{months[month - 1]} {date.group(1)}") in normalize(text):
            return True
    return "#" in value and normalize(value.rsplit("#", 1)[-1]) in normalize(text)


def parse_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def answer_coverage(answer_text: str, reference: Any, request: str, compact: bool) -> float:
    if compact:
        records = entity_records(reference)
        if records:
            scores = []
            for record in records:
                values = anchors(record)
                if values:
                    required = min(2, len(values))
                    matches = sum(contains(answer_text, value) for value in values)
                    scores.append(min(1.0, matches / required))
                else:
                    scores.append(float(contains(answer_text, str(record["entity_id"]))))
            return sum(scores) / len(scores)
        values = anchors(reference)
    else:
        values = relevant_anchors(anchors(reference), request)
    if not values:
        return float(bool(answer_text))
    required = max(1, min(3, (len(values) + 1) // 2))
    return min(1.0, sum(contains(answer_text, value) for value in values) / required)


def score_one(solution: str, ground_truth: str | dict[str, Any]) -> dict[str, float]:
    expected = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
    tool_responses = re.findall(r"<tool_response>\s*(.*?)\s*</tool_response>", solution, flags=re.DOTALL)
    observed = "\n".join(tool_responses)
    final_text = solution.rsplit("</tool_response>", 1)[-1] if tool_responses else solution
    final_text = re.sub(r"<think>.*?</think>", "", final_text, flags=re.DOTALL).strip()
    final_answer = parse_json_object(final_text)
    reference_answer = expected.get("reference_answer", {})
    expected_answer = expected.get("expected_answer") or reference_answer
    if not reference_answer:
        return {"score": 0.0, "slot_coverage": 0.0, "grounding": 0.0, "duplicate_penalty": 0.0}

    completed = 0.0
    grounded = 0.0
    for name, reference in expected_answer.items():
        evidence = reference_answer.get(name, reference)
        expected_entities = entity_ids(evidence)
        if final_answer is not None and name in final_answer:
            answer_text = json.dumps(final_answer[name], ensure_ascii=False, sort_keys=True)
        elif "expected_answer" not in expected and len(reference_answer) == 1:
            answer_text = final_text
        else:
            answer_text = ""
        completed += answer_coverage(
            answer_text,
            reference,
            str(expected.get("request", "")),
            compact="expected_answer" in expected,
        )
        if expected_entities:
            grounded += sum(entity_id.casefold() in observed.casefold() for entity_id in expected_entities) / len(
                expected_entities
            )
        else:
            evidence_values = anchors(evidence)
            required = max(1, min(3, (len(evidence_values) + 1) // 2)) if evidence_values else 0
            grounded += (
                min(1.0, sum(contains(observed, value) for value in evidence_values) / required)
                if required
                else 1.0
            )

    calls = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", solution, flags=re.DOTALL)
    normalized_calls = [" ".join(call.split()) for call in calls]
    duplicates = len(normalized_calls) - len(set(normalized_calls))
    duplicate_penalty = min(0.1, duplicates * 0.02)
    slot_coverage = completed / len(expected_answer)
    grounding = grounded / len(expected_answer)
    score = max(0.0, slot_coverage * grounding - duplicate_penalty)
    return {
        "score": score,
        "slot_coverage": slot_coverage,
        "grounding": grounding,
        "duplicate_penalty": duplicate_penalty,
    }


def replay_outcome(
    solution: str,
    expected: dict[str, Any],
    extra_info: dict[str, Any] | None,
) -> float | None:
    outcome = expected.get("outcome")
    if not outcome:
        return None
    info = extra_info or {}
    tools_kwargs = info.get("tools_kwargs", {})
    if not isinstance(tools_kwargs, dict) or not tools_kwargs:
        return 0.0
    first = next(iter(tools_kwargs.values()), {})
    relative = first.get("create_kwargs", {}).get("environment_file") if isinstance(first, dict) else None
    data_root = info.get("data_root") or os.environ.get("AGENTWORLD_DATA_ROOT")
    if not relative or not data_root:
        return 0.0
    try:
        payload = json.loads((Path(str(data_root)) / str(relative)).read_text(encoding="utf-8"))
        runtime = LocalToolRuntime.from_dict(payload)
        calls = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", solution, flags=re.DOTALL)
        for text in calls:
            action = parse_json_object(text)
            if not isinstance(action, dict):
                return 0.0
            function = action.get("function") if isinstance(action.get("function"), dict) else action
            name = function.get("name") or function.get("tool")
            arguments = function.get("arguments", function.get("parameters", {}))
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            config = tools_kwargs.get(name, {})
            original = config.get("create_kwargs", {}).get("original_name") if isinstance(config, dict) else None
            if not original or not isinstance(arguments, dict):
                return 0.0
            runtime.call(str(original), arguments)
        return float(runtime.check_outcome(outcome)["passed"])
    except Exception:
        return 0.0


def compute_score(
    data_source: str | None = None,
    solution_str: str | None = None,
    ground_truth: str | dict[str, Any] | None = None,
    extra_info: dict[str, Any] | None = None,
    data_sources: list[str] | None = None,
    solution_strs: list[str] | None = None,
    ground_truths: list[str | dict[str, Any]] | None = None,
    extra_infos: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, float] | list[dict[str, float]]:
    """Score one current veRL rollout, while retaining old batch-runner compatibility."""
    if solution_str is not None and ground_truth is not None:
        expected = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
        scored = score_one(solution_str, expected)
        outcome_score = replay_outcome(solution_str, expected, extra_info)
        if outcome_score is not None:
            scored["outcome"] = outcome_score
            scored["score"] *= outcome_score
        return scored
    if solution_strs is None or ground_truths is None:
        raise ValueError("Missing rollout solution or ground truth")
    infos = extra_infos or [{} for _ in solution_strs]
    results = []
    for solution, truth, info in zip(solution_strs, ground_truths, infos, strict=True):
        expected = json.loads(truth) if isinstance(truth, str) else truth
        scored = score_one(solution, expected)
        outcome_score = replay_outcome(solution, expected, info)
        if outcome_score is not None:
            scored["outcome"] = outcome_score
            scored["score"] *= outcome_score
        results.append(scored)
    return results
