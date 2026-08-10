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
        try:
            runtime.reset()
            final_answer = None
            for _step in range(max_steps):
                try:
                    response = self.llm.complete_json(
                        "Use the tools one step at a time. Return JSON {call:{tool,arguments}} or {final:true,answer:object}. Include every requested answer section and only facts observed from tools.",
                        json.dumps({"task": task["request"], "required_answer_sections": task.get("validation", {}).get("answer_slots", []), "tools": self.tools, "previous_observations": trace}, ensure_ascii=False),
                    )
                    action = extract_json_object(response)
                except (RuntimeError, ValueError) as error:
                    return {
                        "success": False,
                        "infrastructure_error": True,
                        "calls": trace,
                        "judgment": {"passed": False, "reason": "model_request_or_response_error", "error": f"{type(error).__name__}: {error}"},
                    }
                if action.get("final") is True:
                    final_answer = action.get("answer")
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
        trace_text = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        unsupported: dict[str, list[str]] = {}
        absent: dict[str, list[str]] = {}
        for name, value in reference_answer.items():
            if final_answer[name] in (None, "", [], {}):
                absent[name] = ["non-empty answer"]
                continue
            expected_values = FiveRunVerifier._anchors(value)
            answer_text = json.dumps(final_answer[name], ensure_ascii=False, sort_keys=True)
            missing_from_answer = [item for item in expected_values if not FiveRunVerifier._contains(answer_text, item)]
            missing_from_trace = [item for item in expected_values if not FiveRunVerifier._contains(trace_text, item)]
            if missing_from_answer:
                absent[name] = missing_from_answer
            if missing_from_trace:
                unsupported[name] = missing_from_trace
        if absent or unsupported:
            return {"passed": False, "reason": "fact_or_evidence_mismatch", "absent": absent, "unsupported": unsupported}
        return {"passed": True, "reason": "all_answer_sections_have_observed_reference_anchors"}

    @staticmethod
    def _anchors(value: Any) -> list[str]:
        ignored = {"source_url", "entity_type", "color", "profile_url"}
        if isinstance(value, dict):
            if "entity_id" in value or "entity_type" in value:
                for key in ("title", "name", "full_name", "login", "number", "entity_id"):
                    if value.get(key) not in (None, ""):
                        return [str(value[key])]
            anchors = [anchor for key, child in value.items() if key not in ignored for anchor in FiveRunVerifier._anchors(child)]
            return list(dict.fromkeys(anchors))
        if isinstance(value, list):
            anchors = [anchor for child in value for anchor in FiveRunVerifier._anchors(child)]
            return list(dict.fromkeys(anchors))
        if value is None or isinstance(value, bool):
            return []
        return [str(value)]

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
            return [{key: row.get(key) for key in ("entity_id", "entity_type", "name", "source_url")} for row in result[:8]] + [{"truncated": len(result) - 8}]
        return result
