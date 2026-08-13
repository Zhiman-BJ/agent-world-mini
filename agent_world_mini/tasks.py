from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
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
        seen_execution_signatures: set[str] | None = None,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        runtime = LocalToolRuntime(records, tools)
        by_name = {tool.name: tool for tool in tools}
        executed: list[dict[str, Any]] = []
        seen_executions = seen_execution_signatures if seen_execution_signatures is not None else set()
        rejected: Counter[str] = Counter()
        for walk_index, walk in enumerate(walks):
            try:
                candidate = self._instantiate_walk([by_name[name] for name in walk.tool_names], runtime, walk_index)
            except (KeyError, RuntimeError, TypeError, ValueError, StopIteration):
                rejected["cannot_bind_or_execute"] += 1
                continue
            quality_failure = self._basic_quality_failure(candidate)
            if quality_failure:
                rejected[quality_failure] += 1
                continue
            causal_core = self._causal_core(candidate)
            if not causal_core:
                rejected["empty_causal_core"] += 1
                continue
            signature = json.dumps(
                [{"tool": call["tool"], "arguments": call["arguments"]} for call in causal_core],
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature in seen_executions:
                rejected["duplicate_candidate_execution"] += 1
                continue
            seen_executions.add(signature)
            executed.append({
                "raw_walk": walk.to_dict(), "calls": candidate, "causal_core": causal_core,
                "execution": runtime.execute([{"tool": step["tool"], "arguments": step["arguments"]} for step in causal_core]),
            })

        report: dict[str, Any] = {
            "raw_walks": len(walks),
            "executed_walks": len(executed),
            "rejected_walks": dict(rejected),
            "executed_step_distribution": dict(Counter(len(item["calls"]) for item in executed)),
            "causal_core_step_distribution": dict(Counter(len(item["causal_core"]) for item in executed)),
            "candidates": executed,
        }
        if not self.llm.enabled:
            return [], "awaiting_api_semantic_review", report

        tasks: list[Task] = []
        seen_requests: set[str] = set()
        review_candidates = sorted(executed, key=lambda item: len(item["causal_core"]), reverse=True)
        if max_semantic_reviews is not None:
            review_candidates = review_candidates[:max_semantic_reviews]
        reviewed: list[Task] = []
        review_batches = [
            list(enumerate(review_candidates[start:start + 4], start=start + 1))
            for start in range(0, len(review_candidates), 4)
        ]
        with ThreadPoolExecutor(max_workers=max(1, semantic_workers)) as pool:
            futures = [pool.submit(self._review_batch, theme, tools, runtime, batch) for batch in review_batches]
            for future in as_completed(futures):
                reviewed.extend(future.result())
        for task in sorted(reviewed, key=lambda candidate: candidate.task_id):
            if task.request in seen_requests:
                continue
            seen_requests.add(task.request)
            tasks.append(task)
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        report["semantic_tasks"] = len(tasks)
        report["semantic_reviews"] = len(review_candidates)
        report["semantic_review_requests"] = len(review_batches)
        return tasks, "api_reviewed_graph_walks", report

    def synthesize_adaptive(
        self,
        theme: str,
        tools: list[ToolSpec],
        records: list[Record],
        sample_walks: Any,
        initial_candidates: int = 64,
        batch_candidates: int = 32,
        max_candidates: int = 128,
        max_semantic_reviews: int | None = None,
        max_tasks: int | None = None,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        tasks: list[Task] = []
        seen_requests: set[str] = set()
        seen_execution_signatures: set[str] = set()
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
                semantic_workers=4,
                seen_execution_signatures=seen_execution_signatures,
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
            batch_reports.append(report)
            print(
                f"[tasks] candidates {attempted - batch_size + 1}-{attempted}: "
                f"{report.get('executed_walks', 0)} executed chains, {new_tasks} new tasks, "
                f"{report.get('semantic_review_requests', 0)} review requests",
                flush=True,
            )

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
            "global_unique_executions": len(seen_execution_signatures),
            "stopped_after_low_yield_batches": low_yield_batches >= 2,
            "batches": batch_reports,
        }
        return tasks, mode, combined

    def _instantiate_walk(self, chain_tools: list[ToolSpec], runtime: LocalToolRuntime, walk_index: int) -> list[dict[str, Any]]:
        attempts = 0

        def bind(step_index: int, observations: list[dict[str, Any]], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal attempts
            if step_index == len(chain_tools):
                return calls
            tool = chain_tools[step_index]
            existing = {(call["tool"], json.dumps(call["arguments"], sort_keys=True)) for call in calls}
            for arguments, provenance in self._argument_options(tool, runtime, observations, walk_index + step_index):
                attempts += 1
                if attempts > 160:
                    raise RuntimeError("argument backtracking budget exhausted")
                signature = (tool.name, json.dumps(arguments, sort_keys=True))
                if signature in existing:
                    continue
                try:
                    result = runtime.call(tool.name, arguments)
                except (KeyError, TypeError, ValueError, StopIteration):
                    continue
                if result in (None, [], {}):
                    continue
                call = {"tool": tool.name, "arguments": arguments, "argument_provenance": provenance}
                try:
                    return bind(
                        step_index + 1,
                        [*observations, {"tool": tool.name, "result": result}],
                        [*calls, call],
                    )
                except ValueError:
                    continue
            raise ValueError(f"no executable arguments for {tool.name}")

        return bind(0, [], [])

    def _argument_options(
        self,
        tool: ToolSpec,
        runtime: LocalToolRuntime,
        observations: list[dict[str, Any]],
        cursor: int,
    ) -> list[tuple[dict[str, Any], dict[str, str]]]:
        if tool.operation == "search":
            options: list[tuple[dict[str, Any], dict[str, str]]] = []
            rows = runtime.rows_for(tool.entity_type)
            for offset in range(min(len(rows), 6)):
                row = rows[(cursor + offset) % len(rows)]
                field = next((field for field in tool.search_fields if row.get(field)), None)
                if field is None:
                    continue
                value = str(row[field])
                option = ({"query": value[: max(1, min(18, len(value)))]}, {"query": "user_seed"})
                if option not in options:
                    options.append(option)
            return options
        if tool.operation == "rank":
            return [({"limit": min(4, len(runtime.rows_for(tool.entity_type)))}, {"limit": "task_constraint"})]
        if tool.operation == "filter":
            field = tool.relation_field or ""
            values = sorted({str(row[field]) for row in runtime.rows_for(tool.entity_type) if row.get(field) not in (None, "")})
            return [({field: value, "limit": 3}, {field: "user_seed", "limit": "task_constraint"}) for value in values[:6]]
        if tool.operation == "group_count":
            return [({}, {})]
        if tool.operation in {"lookup", "relation", "relation_rank", "linked_id", "bridge_relation"}:
            ids = self._observed_ids(tool.entity_type, observations)
            ids.extend(
                (str(row["entity_id"]), "database_seed")
                for row in runtime.rows_for(tool.entity_type)
                if str(row["entity_id"]) not in {entity_id for entity_id, _origin in ids}
            )
            options = []
            for entity_id, origin in ids[:8]:
                arguments = {"entity_id": entity_id}
                provenance = {"entity_id": origin}
                if tool.operation in {"relation", "relation_rank", "bridge_relation"}:
                    arguments["limit"] = 3
                    provenance["limit"] = "task_constraint"
                options.append((arguments, provenance))
            return options
        if tool.operation == "compare":
            ids = self._observed_ids(tool.entity_type, observations)
            ids.extend(
                (str(row["entity_id"]), "database_seed")
                for row in runtime.rows_for(tool.entity_type)
                if str(row["entity_id"]) not in {entity_id for entity_id, _origin in ids}
            )
            return [
                (
                    {"left_id": left[0], "right_id": right[0]},
                    {"left_id": left[1], "right_id": right[1]},
                )
                for left, right in list(combinations(ids, 2))[:12]
                if left[0] != right[0]
            ]
        return []

    def _arguments_for(self, tool: ToolSpec, runtime: LocalToolRuntime, observations: list[dict[str, Any]], cursor: int) -> tuple[dict[str, Any], dict[str, str]]:
        options = self._argument_options(tool, runtime, observations, cursor)
        if options:
            return options[0]
        raise ValueError(f"no arguments for {tool.name}")

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
        seen_ids: set[str] = set()
        for index in range(len(observations) - 1, -1, -1):
            for row in self._rows_in(observations[index]["result"]):
                if row.get("entity_type") == entity_type and row.get("entity_id") is not None:
                    entity_id = str(row["entity_id"])
                    if entity_id not in seen_ids:
                        seen_ids.add(entity_id)
                        found.append((entity_id, f"observation:{index}"))
        return found

    def _observed_id(self, entity_type: str, observations: list[dict[str, Any]]) -> tuple[str, str]:
        ids = self._observed_ids(entity_type, observations)
        if not ids:
            raise ValueError(f"required {entity_type} id was not observed")
        return ids[0]

    @staticmethod
    def _causal_core(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the largest connected dependency component, including branches."""
        if not calls:
            return []
        adjacency = {index: set() for index in range(len(calls))}
        for index, call in enumerate(calls):
            for origin in call["argument_provenance"].values():
                if not origin.startswith("observation:"):
                    continue
                source_index = int(origin.split(":", 1)[1])
                adjacency[index].add(source_index)
                adjacency[source_index].add(index)
        components: list[set[int]] = []
        unseen = set(range(len(calls)))
        while unseen:
            root = unseen.pop()
            component = {root}
            frontier = [root]
            while frontier:
                current = frontier.pop()
                for neighbor in adjacency[current]:
                    if neighbor not in component:
                        component.add(neighbor)
                        unseen.discard(neighbor)
                        frontier.append(neighbor)
            components.append(component)
        required = max(components, key=lambda component: (len(component), len(calls) - 1 in component))
        old_to_new = {old: new for new, old in enumerate(sorted(required))}
        kept = []
        for index, call in enumerate(calls):
            if index not in required:
                continue
            provenance = {}
            for name, origin in call["argument_provenance"].items():
                if origin.startswith("observation:"):
                    source_index = int(origin.split(":", 1)[1])
                    provenance[name] = f"observation:{old_to_new[source_index]}"
                else:
                    provenance[name] = origin
            kept.append(call | {"argument_provenance": provenance})
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

    def _review_batch(
        self,
        theme: str,
        tools: list[ToolSpec],
        runtime: LocalToolRuntime,
        candidates: list[tuple[int, dict[str, Any]]],
    ) -> list[Task]:
        prompt = {
            "theme": theme,
            "tool_contracts": [{"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs} for tool in tools],
            "candidates": [
                {"candidate_index": task_index, "executed_trace": item["execution"]["trace"]}
                for task_index, item in candidates
            ],
            "constraints": [
                "Review each candidate independently. Omit artificial or incoherent candidates from reviews.",
                "Select only steps that form one realistic objective. Reject a chain that follows a relation and then reverses it only to confirm an entity already known.",
                "A selected step whose arguments came from an earlier observation must retain that earlier step. Do not select duplicate calls.",
                "Write one concise, natural request covering every selected observation, without tool names, APIs, schemas, database tables, internal IDs, or implementation details.",
                "Do not invent an action or fact absent from the trace.",
                "Return JSON {reviews:[{candidate_index:integer,keep_step_indices:integer[],request:string,answer_slots:[{name:string,description:string,step_indices:integer[]}],rubric:[string]}]}. Within each review, answer-slot step indices must cover exactly the selected steps.",
            ],
        }
        try:
            result = extract_json_object(self.llm.complete_json(
                "Turn executed, source-grounded tool evidence into verifiable user tasks. Skip artificial chains.",
                json.dumps(prompt, ensure_ascii=False),
            ))
            raw_reviews = result["reviews"]
        except (RuntimeError, KeyError, TypeError, ValueError):
            return []
        if not isinstance(raw_reviews, list):
            return []
        reviews = {
            int(review["candidate_index"]): review
            for review in raw_reviews
            if isinstance(review, dict) and str(review.get("candidate_index", "")).isdigit()
        }
        tasks: list[Task] = []
        for task_index, item in candidates:
            review = reviews.get(task_index)
            if review is None:
                continue
            task = self._task_from_review(tools, runtime, item, task_index, review)
            if task is not None:
                tasks.append(task)
        return tasks

    def _task_from_review(
        self,
        tools: list[ToolSpec],
        runtime: LocalToolRuntime,
        item: dict[str, Any],
        task_index: int,
        result: dict[str, Any],
    ) -> Task | None:
        all_calls = item["causal_core"]
        try:
            request = str(result["request"]).strip()
            answer_slots = result["answer_slots"]
            kept_indexes = sorted({int(index) for index in result["keep_step_indices"]})
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(answer_slots, list) or not answer_slots or not kept_indexes or not set(kept_indexes) <= set(range(len(all_calls))):
            return None
        if len(all_calls) - 1 not in kept_indexes:
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
