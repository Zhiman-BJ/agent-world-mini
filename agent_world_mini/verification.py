from __future__ import annotations

import json
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .io_utils import extract_json_object
from .llm import LLMClient
from .runtime import LocalToolRuntime


class FiveRunVerifier:
    """Small-scale version of Agent-World's five independent solver rollouts."""

    def __init__(self, llm: LLMClient, runtime: LocalToolRuntime, tools: list[dict[str, Any]]):
        self.llm, self.runtime, self.tools = llm, runtime, tools

    def verify(self, task: dict[str, Any], expected: Any, runs: int = 5, max_steps: int = 12, max_infrastructure_retries: int = 3) -> dict[str, Any]:
        if not self.llm.enabled:
            return {"status": "not_run_no_llm", "runs": []}
        step_budget = max(max_steps, len(task.get("hidden_reference_chain", [])) + 3)
        outcomes: list[dict[str, Any]] = []
        infrastructure_errors: list[dict[str, Any]] = []
        while len(outcomes) < runs and len(infrastructure_errors) <= max_infrastructure_retries:
            batch_size = min(2, runs - len(outcomes))
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                batch = list(pool.map(lambda _: self._run_once(task, expected, step_budget), range(batch_size)))
            for outcome in batch:
                if outcome.get("infrastructure_error"):
                    infrastructure_errors.append(outcome)
                else:
                    outcomes.append(outcome)
            successes = sum(outcome["success"] for outcome in outcomes)
            remaining = runs - len(outcomes)
            if successes >= 2 or successes + remaining < 2:
                break
        successes = sum(outcome["success"] for outcome in outcomes)
        if successes >= 2:
            status = "passed"
        elif len(outcomes) < runs and len(infrastructure_errors) > max_infrastructure_retries:
            status = "inconclusive_infrastructure"
        else:
            status = "rejected"
        return {
            "status": status,
            "successes": successes,
            "attempted_runs": len(outcomes),
            "infrastructure_retries": len(infrastructure_errors),
            "infrastructure_errors": infrastructure_errors,
            "decided_early": len(outcomes) < runs,
            "runs": outcomes,
        }

    def _run_once(self, task: dict[str, Any], expected: Any, max_steps: int) -> dict[str, Any]:
        runtime = deepcopy(self.runtime)
        trace: list[dict[str, Any]] = []
        tool_names = {str(tool.get("name")) for tool in self.tools if tool.get("name")}
        answer_names = {
            str(slot.get("name"))
            for slot in task.get("validation", {}).get("answer_slots", [])
            if isinstance(slot, dict) and slot.get("name")
        }
        try:
            runtime.reset()
            final_answer = None
            for _step in range(max_steps):
                try:
                    response = self.llm.complete_json(
                        "Use the tools one step at a time. Return JSON {call:{tool,arguments}} or {final:true,answer:object}. Include every requested answer section and only facts observed from tools.",
                        json.dumps({"task": task["request"], "required_answer_sections": task.get("validation", {}).get("answer_slots", []), "tools": self.tools, "previous_observations": trace}, ensure_ascii=False),
                    )
                    raw_action = extract_json_object(response)
                except (RuntimeError, ValueError) as error:
                    return {
                        "success": False,
                        "infrastructure_error": True,
                        "calls": trace,
                        "judgment": {"passed": False, "reason": "model_request_or_response_error", "error": f"{type(error).__name__}: {error}"},
                    }
                action = self._normalize_action(raw_action, tool_names, answer_names, bool(trace))
                if action is None:
                    return {
                        "success": False,
                        "calls": trace,
                        "judgment": {
                            "passed": False,
                            "reason": "invalid_action",
                            "error": "expected a tool call or final answer",
                            "raw_action": raw_action,
                        },
                    }
                if action["kind"] == "final":
                    final_answer = action["answer"]
                    break
                call = action["call"]
                result = runtime.call(call["tool"], call.get("arguments", {}))
                trace.append({"tool": call["tool"], "arguments": call.get("arguments", {}), "result": self._preview(result)})
            judgment = self._judge(task, expected, trace, final_answer) if final_answer is not None else {"passed": False, "reason": "no_final_answer"}
            return {"success": judgment["passed"], "calls": trace, "final_answer": final_answer, "judgment": judgment}
        except (ValueError, KeyError, StopIteration, TypeError, AttributeError) as error:
            return {
                "success": False,
                "calls": trace,
                "judgment": {"passed": False, "reason": "rollout_error", "error": f"{type(error).__name__}: {error}"},
            }

    @staticmethod
    def _normalize_action(
        action: Any,
        tool_names: set[str],
        answer_names: set[str],
        has_observations: bool,
    ) -> dict[str, Any] | None:
        """Normalize common equivalent action shapes emitted by JSON-mode models."""
        if not isinstance(action, dict):
            return None

        call = action.get("call")
        if not isinstance(call, dict) and action.get("tool"):
            call = action
        if not isinstance(call, dict) and isinstance(action.get("action"), dict):
            call = action["action"]
        if isinstance(call, dict):
            tool = call.get("tool") or call.get("name")
            arguments = call.get("arguments", call.get("parameters", call.get("input", {})))
            if tool in tool_names and isinstance(arguments, dict):
                return {"kind": "call", "call": {"tool": tool, "arguments": arguments}}

        if action.get("final") is True and isinstance(action.get("answer"), dict):
            return {"kind": "final", "answer": action["answer"]}
        if isinstance(action.get("final"), dict):
            return {"kind": "final", "answer": action["final"]}
        for key in ("final_answer", "answer"):
            if isinstance(action.get(key), dict):
                return {"kind": "final", "answer": action[key]}
        if has_observations and answer_names and answer_names.issubset(action):
            return {"kind": "final", "answer": action}
        return None

    @staticmethod
    def _judge(task: dict[str, Any], expected: Any, trace: list[dict[str, Any]], final_answer: Any) -> dict[str, Any]:
        """Check answer facts and their observed support, not JSON formatting.

        The reference trace is an internal oracle, whereas an agent may answer
        with a concise scalar, a record object, or a short list. Requiring the
        internal nested JSON shape would reject correct user-facing answers.
        """
        reference_answer = expected.get("reference_answer", {}) if isinstance(expected, dict) else {}
        if not isinstance(final_answer, dict):
            return {"passed": False, "reason": "final_answer_is_not_an_object"}
        missing = [name for name in reference_answer if name not in final_answer]
        if missing:
            return {"passed": False, "reason": "missing_answer_slots", "missing": missing}
        request = str(task.get("request", ""))
        trace_text = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        unsupported: dict[str, list[str]] = {}
        absent: dict[str, list[str]] = {}
        for name, value in reference_answer.items():
            if final_answer[name] in (None, "", [], {}):
                absent[name] = ["non-empty answer"]
                continue
            expected_values = FiveRunVerifier._anchors(value)
            relevant_values = FiveRunVerifier._relevant_anchors(expected_values, request)
            # A slot can contain a large discovery result. Require the answer
            # to cover its main facts, without making it reproduce every row.
            required_count = max(1, min(3, (len(relevant_values) + 1) // 2)) if relevant_values else 0
            answer_text = json.dumps(final_answer[name], ensure_ascii=False, sort_keys=True)
            missing_from_answer = [item for item in relevant_values if not FiveRunVerifier._contains(answer_text, item)]
            missing_from_trace = [item for item in relevant_values if not FiveRunVerifier._trace_contains(trace, trace_text, item)]
            if required_count:
                answer_matches = len(relevant_values) - len(missing_from_answer)
                trace_matches = len(relevant_values) - len(missing_from_trace)
                if answer_matches < required_count:
                    missing_from_answer = [f"{required_count} grounded facts (matched {answer_matches})"]
                else:
                    missing_from_answer = []
                if trace_matches < required_count:
                    missing_from_trace = [f"{required_count} observed facts (matched {trace_matches})"]
                else:
                    missing_from_trace = []
            if missing_from_answer:
                absent[name] = missing_from_answer
            if missing_from_trace:
                unsupported[name] = missing_from_trace
        if absent or unsupported:
            return {"passed": False, "reason": "fact_or_evidence_mismatch", "absent": absent, "unsupported": unsupported}
        return {"passed": True, "reason": "all_answer_sections_have_observed_reference_anchors"}

    @staticmethod
    def _anchors(value: Any, field: str = "") -> list[str]:
        ignored = {"source_url", "entity_type", "color", "profile_url", "query"}
        if isinstance(value, dict):
            anchors = []
            entity_id = value.get("entity_id")
            if entity_id not in (None, ""):
                # The human part of an internal id is useful for grounding,
                # but the internal prefix itself is not an answer requirement.
                anchors.append(str(entity_id).rsplit(":", 1)[-1].replace("_", " "))
            for key, child in value.items():
                if key in ignored or key.endswith("_id") or key in {"rxcui", "pubchem_cid", "cas_registry_number"}:
                    continue
                anchors.extend(FiveRunVerifier._anchors(child, key))
            return list(dict.fromkeys(anchors))
        if isinstance(value, list):
            anchors = [anchor for child in value for anchor in FiveRunVerifier._anchors(child, field)]
            return list(dict.fromkeys(anchors))
        if value is None or isinstance(value, bool):
            return []
        return [str(value)]

    @staticmethod
    def _relevant_anchors(anchors: list[str], request: str) -> list[str]:
        request_tokens = {part for part in re.findall(r"[a-z0-9]+", request.lower()) if len(part) >= 4}
        textual_matches = []
        numeric = []
        for anchor in anchors:
            normalized = " ".join(re.findall(r"[a-z0-9]+", anchor.lower()))
            if not normalized:
                continue
            anchor_tokens = set(re.findall(r"[a-z0-9]+", normalized))
            if normalized.isdigit():
                numeric.append(anchor)
            elif anchor_tokens & request_tokens:
                textual_matches.append(anchor)
        # Once the request identifies the relevant entity/topic, numeric facts
        # from that result become relevant too. With no textual match (as in a
        # generic unit-test request), retain the first-hand evidence instead of
        # silently reducing it to an arbitrary number.
        relevant = textual_matches + numeric if textual_matches else anchors
        return list(dict.fromkeys(relevant))

    @staticmethod
    def _trace_contains(trace: list[dict[str, Any]], trace_text: str, value: str) -> bool:
        if FiveRunVerifier._contains(trace_text, value):
            return True
        try:
            target = float(value)
        except (TypeError, ValueError):
            return False
        numbers = []
        for item in trace:
            numbers.extend(float(found) for found in re.findall(r"(?<![a-zA-Z])[0-9]+(?:\.[0-9]+)?", json.dumps(item, ensure_ascii=False)))
        return any(abs(left - right) == target for index, left in enumerate(numbers) for right in numbers[index + 1:])

    @staticmethod
    def _contains(text: str, value: str) -> bool:
        normalize = lambda item: " ".join(re.findall(r"[a-z0-9]+", item.lower()))
        haystack = normalize(text)
        needle = normalize(value)
        if needle and needle in haystack:
            return True
        if "#" in value:
            return normalize(value.rsplit("#", 1)[-1]) in haystack
        return False

    @staticmethod
    def _preview(result: Any) -> Any:
        if isinstance(result, list) and len(result) > 8:
            compact = []
            for row in result[:8]:
                if isinstance(row, dict):
                    compact.append({key: row.get(key) for key in ("entity_id", "entity_type", "name", "source_url")})
                else:
                    compact.append(row)
            return compact + [{"truncated": len(result) - 8}]
        return result
