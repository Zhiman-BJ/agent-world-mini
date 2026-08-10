from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .io_utils import extract_json_object
from .llm import LLMClient
from .models import Record, Task, ToolChain, ToolSpec
from .runtime import LocalToolRuntime


class TaskSynthesizer:
    """Agent-World-style graph-walk task synthesis.

    A raw weighted walk is only a proposal. It becomes a task only after its
    parameters are instantiated from prior observations, its causal core runs,
    and an LLM writes a non-leaking user objective for that executed evidence.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def synthesize(
        self,
        theme: str,
        tools: list[ToolSpec],
        walks: list[ToolChain],
        records: list[Record],
        max_semantic_reviews: int | None = None,
        max_tasks: int | None = None,
        semantic_workers: int = 4,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        runtime = LocalToolRuntime(records, tools)
        by_name = {tool.name: tool for tool in tools}
        executed: list[dict[str, Any]] = []
        seen_executions: set[str] = set()
        rejected: Counter[str] = Counter()
        for walk_index, walk in enumerate(walks):
            try:
                candidate = self._instantiate_walk([by_name[name] for name in walk.tool_names], runtime, walk_index)
            except (KeyError, TypeError, ValueError, StopIteration):
                rejected["cannot_bind_or_execute"] += 1
                continue
            execution = runtime.execute([{"tool": step["tool"], "arguments": step["arguments"]} for step in candidate])
            quality_failure = self._basic_quality_failure(candidate)
            if quality_failure:
                rejected[quality_failure] += 1
                continue
            signature = json.dumps(
                [{"tool": call["tool"], "arguments": call["arguments"]} for call in candidate],
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature in seen_executions:
                rejected["duplicate_candidate_execution"] += 1
                continue
            seen_executions.add(signature)
            causal_core = self._causal_core(candidate)
            executed.append({
                "raw_walk": walk.to_dict(), "calls": candidate, "causal_core": causal_core,
                "execution": execution,
            })

        report: dict[str, Any] = {
            "raw_walks": len(walks),
            "executed_walks": len(executed),
            "rejected_walks": dict(rejected),
            "executed_step_distribution": dict(Counter(len(item["calls"]) for item in executed)),
            "causal_core_step_distribution": dict(Counter(len(item["causal_core"]) for item in executed)),
            "candidates": executed[:24],
        }
        if not self.llm.enabled:
            return [], "awaiting_api_semantic_review", report

        tasks: list[Task] = []
        seen_requests: set[str] = set()
        review_candidates = sorted(executed, key=lambda item: len(item["calls"]), reverse=True)
        if max_semantic_reviews is not None:
            review_candidates = review_candidates[:max_semantic_reviews]
        reviewed: list[Task] = []
        with ThreadPoolExecutor(max_workers=max(1, semantic_workers)) as pool:
            futures = [pool.submit(self._review_and_describe, theme, tools, runtime, item, index + 1) for index, item in enumerate(review_candidates)]
            for future in as_completed(futures):
                task = future.result()
                if task is not None:
                    reviewed.append(task)
        for task in sorted(reviewed, key=lambda candidate: candidate.task_id):
            if task.request in seen_requests:
                continue
            seen_requests.add(task.request)
            tasks.append(task)
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        report["semantic_tasks"] = len(tasks)
        return tasks, "api_reviewed_graph_walks", report

    def synthesize_adaptive(
        self,
        theme: str,
        tools: list[ToolSpec],
        records: list[Record],
        sample_walks: Any,
        initial_candidates: int = 32,
        batch_candidates: int = 16,
        max_candidates: int = 128,
        max_semantic_reviews: int | None = None,
        max_tasks: int | None = None,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        tasks: list[Task] = []
        seen_requests: set[str] = set()
        batch_reports: list[dict[str, Any]] = []
        attempted = 0
        low_yield_batches = 0
        mode = "awaiting_api_semantic_review"

        while attempted < max_candidates:
            batch_size = initial_candidates if attempted == 0 else batch_candidates
            batch_size = min(batch_size, max_candidates - attempted)
            remaining_reviews = None
            if max_semantic_reviews is not None:
                remaining_reviews = max(0, max_semantic_reviews - sum(report.get("semantic_reviews", 0) for report in batch_reports))
                if remaining_reviews == 0:
                    break
            walks = sample_walks(count=batch_size, seed=7 + attempted)
            batch_tasks, mode, report = self.synthesize(
                theme,
                tools,
                walks,
                records,
                max_semantic_reviews=remaining_reviews,
                max_tasks=None,
                semantic_workers=2,
            )
            attempted += batch_size
            new_tasks = 0
            for task in batch_tasks:
                if task.request in seen_requests:
                    continue
                seen_requests.add(task.request)
                tasks.append(task)
                new_tasks += 1
                if max_tasks is not None and len(tasks) >= max_tasks:
                    break
            report["candidate_offset"] = attempted - batch_size
            report["new_unique_tasks"] = new_tasks
            report["semantic_reviews"] = min(report.get("executed_walks", 0), remaining_reviews) if remaining_reviews is not None else report.get("executed_walks", 0)
            batch_reports.append(report)

            if not self.llm.enabled or (max_tasks is not None and len(tasks) >= max_tasks):
                break
            low_yield_batches = low_yield_batches + 1 if new_tasks / max(1, batch_size) < 0.1 else 0
            if low_yield_batches >= 2:
                break

        for index, task in enumerate(tasks, start=1):
            task.task_id = f"task_{index:03d}"
        combined = {
            "raw_walks": sum(report.get("raw_walks", 0) for report in batch_reports),
            "executed_walks": sum(report.get("executed_walks", 0) for report in batch_reports),
            "semantic_tasks": len(tasks),
            "candidate_budget": max_candidates,
            "candidate_attempts": attempted,
            "stopped_after_low_yield_batches": low_yield_batches >= 2,
            "batches": batch_reports,
        }
        return tasks, mode, combined

    def _instantiate_walk(self, chain_tools: list[ToolSpec], runtime: LocalToolRuntime, walk_index: int) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for step_index, tool in enumerate(chain_tools):
            arguments, provenance = self._arguments_for(tool, runtime, observations, walk_index + step_index)
            result = runtime.call(tool.name, arguments)
            if result in (None, [], {}):
                raise ValueError("empty observation")
            calls.append({"tool": tool.name, "arguments": arguments, "argument_provenance": provenance})
            observations.append({"tool": tool.name, "result": result})
        return calls

    def _arguments_for(self, tool: ToolSpec, runtime: LocalToolRuntime, observations: list[dict[str, Any]], cursor: int) -> tuple[dict[str, Any], dict[str, str]]:
        if tool.operation == "search":
            rows = runtime.rows_for(tool.entity_type)
            row = rows[cursor % len(rows)]
            field = next((field for field in tool.search_fields if row.get(field)), None)
            if field is None:
                raise ValueError("search field has no value")
            value = str(row[field])
            return {"query": value[: max(1, min(18, len(value)))]}, {"query": "user_seed"}
        if tool.operation == "rank":
            return {"limit": min(3, len(runtime.rows_for(tool.entity_type)))}, {"limit": "task_constraint"}
        if tool.operation == "filter":
            field = tool.relation_field or ""
            values = sorted({str(row[field]) for row in runtime.rows_for(tool.entity_type) if row.get(field) not in (None, "")})
            if not values:
                raise ValueError("filter has no usable values")
            return {field: values[cursor % len(values)], "limit": 3}, {field: "user_seed", "limit": "task_constraint"}
        if tool.operation == "group_count":
            return {}, {}
        if tool.operation in {"lookup", "relation", "relation_rank", "linked_id"}:
            entity_type = tool.entity_type
            try:
                entity_id, origin = self._observed_id(entity_type, observations)
            except ValueError:
                rows = runtime.rows_for(entity_type)
                if not rows:
                    raise
                entity_id, origin = str(rows[cursor % len(rows)]["entity_id"]), "database_seed"
            arguments = {"entity_id": entity_id}
            provenance = {"entity_id": origin}
            if tool.operation in {"relation", "relation_rank"}:
                arguments["limit"] = 2
                provenance["limit"] = "task_constraint"
            return arguments, provenance
        if tool.operation == "compare":
            ids = self._observed_ids(tool.entity_type, observations)
            if len(ids) < 2:
                ids.extend((str(row["entity_id"]), "database_seed") for row in runtime.rows_for(tool.entity_type) if (str(row["entity_id"]), "database_seed") not in ids)
            if len(ids) < 2:
                raise ValueError("comparison needs two records")
            if ids[0][0] == ids[1][0]:
                raise ValueError("comparison needs distinct records")
            return {"left_id": ids[0][0], "right_id": ids[1][0]}, {"left_id": ids[0][1], "right_id": ids[1][1]}
        raise ValueError(f"unsupported operation: {tool.operation}")

    @staticmethod
    def _rows_in(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [row for item in value for row in TaskSynthesizer._rows_in(item)]
        if isinstance(value, dict):
            rows = [value] if "entity_id" in value else []
            for child in value.values():
                rows.extend(TaskSynthesizer._rows_in(child))
            return rows
        return []

    def _observed_ids(self, entity_type: str, observations: list[dict[str, Any]]) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for index in range(len(observations) - 1, -1, -1):
            for row in self._rows_in(observations[index]["result"]):
                if row.get("entity_type") == entity_type and row.get("entity_id") is not None:
                    candidate = (str(row["entity_id"]), f"observation:{index}")
                    if candidate not in found:
                        found.append(candidate)
        return found

    def _observed_id(self, entity_type: str, observations: list[dict[str, Any]]) -> tuple[str, str]:
        ids = self._observed_ids(entity_type, observations)
        if not ids:
            raise ValueError(f"required {entity_type} id was not observed")
        return ids[0]

    @staticmethod
    def _causal_core(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the last action and all calls that supply an observed argument."""
        if not calls:
            return []
        required = {len(calls) - 1}
        changed = True
        while changed:
            changed = False
            for index in sorted(required, reverse=True):
                for origin in calls[index]["argument_provenance"].values():
                    if not origin.startswith("observation:"):
                        continue
                    source_index = int(origin.split(":", 1)[1])
                    if source_index not in required:
                        required.add(source_index)
                        changed = True
        kept = [call for index, call in enumerate(calls) if index in required]
        signatures = {(call["tool"], json.dumps(call["arguments"], sort_keys=True)) for call in kept}
        if len(signatures) != len(kept):
            return []
        return kept

    @staticmethod
    def _basic_quality_failure(calls: list[dict[str, Any]]) -> str | None:
        signatures = {(call["tool"], json.dumps(call["arguments"], sort_keys=True)) for call in calls}
        if len(signatures) != len(calls):
            return "duplicate_tool_call"
        for call in calls:
            arguments = call["arguments"]
            if "left_id" in arguments and arguments.get("left_id") == arguments.get("right_id"):
                return "self_comparison"
        return None

    def _review_and_describe(self, theme: str, tools: list[ToolSpec], runtime: LocalToolRuntime, item: dict[str, Any], task_index: int) -> Task | None:
        all_calls = item["calls"]
        prompt = {
            "theme": theme,
            "tool_contracts": [{"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs} for tool in tools],
            "executed_trace": item["execution"]["trace"],
            "constraints": [
                "Select only steps that form one realistic objective. Keep separate branches only when the final request compares, combines, or acts on their results; unrelated facts do not become coherent merely by putting them in one report.",
                "A selected step whose arguments came from an earlier observation must retain that earlier step too. Do not select two calls with exactly the same function and arguments.",
                "Write one concise, natural multi-part user request that covers every selected observation.",
                "Do not mention tool names, APIs, schemas, database tables, ids, reference traces, or implementation details.",
                "Do not invent a business action or fact absent from the trace.",
                "Return JSON {keep_step_indices:integer[],request:string,answer_slots:[{name:string,description:string,step_indices:integer[]}], rubric:[string]}. The union of step_indices must cover exactly the selected steps. Each slot names a distinct answer section and may cover multiple steps.",
            ],
        }
        try:
            result = extract_json_object(self.llm.complete_json(
                "You turn executed, source-grounded tool evidence into a verifiable user task. Reject artificial chains rather than writing a task for them.",
                json.dumps(prompt, ensure_ascii=False),
            ))
            request = str(result["request"]).strip()
            answer_slots = result["answer_slots"]
            kept_indexes = sorted({int(index) for index in result["keep_step_indices"]})
        except (RuntimeError, KeyError, TypeError, ValueError):
            return None
        if not isinstance(answer_slots, list) or not answer_slots or not kept_indexes or not set(kept_indexes) <= set(range(len(all_calls))):
            return None
        for index in kept_indexes:
            for origin in all_calls[index]["argument_provenance"].values():
                if origin.startswith("observation:") and int(origin.split(":", 1)[1]) not in kept_indexes:
                    return None
        covered: set[int] = set()
        normalized_slots: list[dict[str, Any]] = []
        for slot in answer_slots:
            indexes = {int(index) for index in slot.get("step_indices", [])}
            if not slot.get("name") or not indexes <= set(kept_indexes):
                return None
            covered.update(indexes)
            normalized_slots.append({"name": str(slot["name"]), "description": str(slot.get("description", "")), "step_indices": sorted(indexes)})
        if covered != set(kept_indexes):
            return None
        calls = [call for index, call in enumerate(all_calls) if index in kept_indexes]
        tool_names = [call["tool"] for call in calls]
        if not request or any(name in request for name in tool_names):
            return None
        execution = runtime.execute([{"tool": call["tool"], "arguments": call["arguments"]} for call in calls])
        old_to_new = {old: new for new, old in enumerate(kept_indexes)}
        normalized_slots = [{**slot, "step_indices": [old_to_new[index] for index in slot["step_indices"]]} for slot in normalized_slots]
        reference_answer = {
            slot["name"]: [execution["trace"][index]["result"] for index in slot["step_indices"]]
            for slot in normalized_slots
        }
        return Task(
            task_id=f"task_{task_index:03d}",
            request=request,
            available_tools=self._public_tools(tools),
            hidden_reference_chain=tool_names,
            validation={
                "reference_plan_executed": True,
                "no_tool_name_leak": True,
                "chain_steps": len(calls),
                "no_duplicate_call_arguments": len({(call["tool"], json.dumps(call["arguments"], sort_keys=True)) for call in calls}) == len(calls),
                "semantic_reviewed": True,
                "reference_calls": [{"tool": call["tool"], "arguments": call["arguments"]} for call in calls],
                "rubric": result.get("rubric", []),
                "answer_slots": normalized_slots,
            },
            reference_execution=execution | {"reference_answer": reference_answer},
            initial_state=runtime.snapshot(),
        )

    @staticmethod
    def _public_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [{"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs, "mutates_state": tool.mutates_state} for tool in tools]
