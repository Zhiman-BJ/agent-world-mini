from __future__ import annotations

import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from itertools import combinations
from typing import Any

from agent_world_mini.utils.io import extract_json_object
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.schemas.models import Record, ResearchBundle, Task, ToolChain, ToolSpec
from agent_world_mini.runtime.engine import LocalToolRuntime


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
        candidate_only: bool = False,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        runtime = LocalToolRuntime(records, tools)
        by_name = {tool.name: tool for tool in tools}
        executed: list[dict[str, Any]] = []
        seen_executions = seen_execution_signatures if seen_execution_signatures is not None else set()
        rejected: Counter[str] = Counter()
        for walk_index, walk in enumerate(walks):
            try:
                runtime.reset()
                candidate = self._instantiate_walk([by_name[name] for name in walk.tool_names], runtime, walk_index)
            except (KeyError, RuntimeError, TypeError, ValueError, StopIteration):
                rejected["cannot_bind_or_execute"] += 1
                continue
            quality_failure = self._basic_quality_failure(candidate)
            if quality_failure:
                rejected[quality_failure] += 1
                continue
            causal_core = self._causal_core(candidate, by_name)
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
            runtime.reset()
            initial_state = runtime.snapshot()
            execution = runtime.execute([{"tool": step["tool"], "arguments": step["arguments"]} for step in causal_core])
            executed.append({
                "raw_walk": walk.to_dict(), "calls": candidate, "causal_core": causal_core,
                "execution": execution | {"initial_state": initial_state, "final_state": runtime.snapshot()},
            })

        report: dict[str, Any] = {
            "raw_walks": len(walks),
            "executed_walks": len(executed),
            "rejected_walks": dict(rejected),
            "executed_step_distribution": dict(Counter(len(item["calls"]) for item in executed)),
            "causal_core_step_distribution": dict(Counter(len(item["causal_core"]) for item in executed)),
            "candidates": executed,
        }
        if candidate_only:
            return [], "awaiting_luna_semantic_review", report
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
        candidate_only: bool = False,
    ) -> tuple[list[Task], str, dict[str, Any]]:
        tasks: list[Task] = []
        seen_requests: set[str] = set()
        seen_execution_signatures: set[str] = set()
        batch_reports: list[dict[str, Any]] = []
        attempted = 0
        low_yield_batches = 0
        mode = "awaiting_api_semantic_review"
        semantic_workers = max(1, int(os.environ.get("SEMANTIC_REVIEW_WORKERS", "1")))

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
                semantic_workers=semantic_workers,
                seen_execution_signatures=seen_execution_signatures,
                candidate_only=candidate_only,
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

            if (not candidate_only and not self.llm.enabled) or (max_tasks is not None and len(tasks) >= max_tasks):
                break
            if not candidate_only:
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

    @staticmethod
    def luna_review_packet(bundle: ResearchBundle, tools: list[ToolSpec], report: dict[str, Any]) -> dict[str, Any]:
        candidates = []
        candidate_index = 0
        for batch in report.get("batches", []):
            for item in batch.get("candidates", []):
                candidate_index += 1
                candidates.append({
                    "candidate_id": f"candidate_{candidate_index:03d}",
                    "causal_core": item["causal_core"],
                    "executed_trace": item["execution"]["trace"],
                })
        return {
            "theme": bundle.theme,
            "review_agent": "gpt-5.6-luna",
            "tool_selection": {
                "environment": bundle.theme,
                "mcp_description": bundle.theme_metadata.get("source_description", ""),
                "mcp_tools": bundle.theme_metadata.get("documented_tools", []),
                "data_entities": bundle.state_contract.get("entities", []),
                "data_relations": bundle.state_contract.get("relations", []),
                "candidate_tools": TaskSynthesizer._public_tools(tools),
            },
            "tool_contracts": TaskSynthesizer._public_tools(tools),
            "instructions": [
                "Choose a coherent useful subset from candidate_tools and return its exact names in keep_tool_names.",
                "Review the whole packet and keep a representative for every distinct useful objective; do not cap the number of reviews.",
                "Omit artificial or incoherent candidates.",
                "Keep only candidates whose first call can be derived from the request without a hidden internal ID; search queries must be natural text.",
                "Keep only steps needed for one realistic objective, including every observation dependency.",
                "Preserve coherent context, comparison, provenance, or related-entity branches when they form useful parts of the same objective; remove only detours.",
                "Write a concise self-contained request without tool names, schemas, APIs, internal IDs, vague prior context, or invented facts.",
                "Answer-slot step indices must cover exactly the kept steps.",
            ],
            "return_format": {
                "usable_environment": True,
                "keep_tool_names": ["exact candidate tool name"],
                "reason": "brief tool selection reason",
                "reviews": [{
                    "candidate_id": "candidate_001",
                    "keep_step_indices": [0],
                    "request": "Natural user request",
                    "answer_slots": [{"name": "result", "description": "Requested result", "step_indices": [0]}],
                    "rubric": ["Uses the observed result"],
                }],
            },
            "candidates": candidates,
        }

    def tasks_from_luna_reviews(
        self,
        tools: list[ToolSpec],
        records: list[Record],
        packet: dict[str, Any],
        review_payload: dict[str, Any],
        max_tasks: int | None = None,
    ) -> tuple[list[Task], dict[str, Any]]:
        runtime = LocalToolRuntime(records, tools)
        candidates = {
            str(item.get("candidate_id")): item
            for item in packet.get("candidates", [])
            if isinstance(item, dict) and item.get("candidate_id")
        }
        reviews = {
            str(item.get("candidate_id")): item
            for item in review_payload.get("reviews", [])
            if isinstance(item, dict) and item.get("candidate_id")
        }
        tasks: list[Task] = []
        rejected = 0
        seen_requests: set[str] = set()
        for candidate_number, (candidate_id, item) in enumerate(candidates.items(), start=1):
            review = reviews.get(candidate_id)
            if review is None:
                continue
            candidate = {"causal_core": item.get("causal_core", [])}
            task = self._task_from_review(tools, runtime, candidate, candidate_number, review)
            if task is None or task.request in seen_requests:
                rejected += 1
                continue
            seen_requests.add(task.request)
            tasks.append(task)
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        for index, task in enumerate(tasks, start=1):
            task.task_id = f"task_{index:03d}"
        return tasks, {
            "packet_candidates": len(candidates),
            "submitted_reviews": len(reviews),
            "accepted_tasks": len(tasks),
            "rejected_reviews": rejected,
            "unknown_candidate_reviews": sorted(set(reviews) - set(candidates)),
        }

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
                    runtime.reset()
                    for previous in calls:
                        runtime.call(previous["tool"], previous["arguments"])
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
        if tool.operation in {"create", "update", "delete", "copy_resource", "read_file", "write_file", "delete_file", "python"}:
            return self._configured_argument_options(tool, observations)
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

    def _configured_argument_options(
        self,
        tool: ToolSpec,
        observations: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, str]]]:
        examples = [
            dict(test.get("arguments", {}))
            for test in tool.test_cases
            if isinstance(test, dict) and isinstance(test.get("arguments", {}), dict)
        ] or [{}]
        options: list[tuple[dict[str, Any], dict[str, str]]] = []
        for example in examples[:6]:
            arguments: dict[str, Any] = {}
            provenance: dict[str, str] = {}
            valid = True
            for name, kind in tool.inputs.items():
                source = tool.input_sources.get(name, "external")
                if source == "internal":
                    found = self._bound_observation_values(tool.input_bindings.get(name, ""), name, observations)
                    if not found:
                        valid = False
                        break
                    value, index = found[0]
                    arguments[name] = value
                    provenance[name] = f"observation:{index}"
                else:
                    arguments[name] = deepcopy(example[name]) if name in example else self._example_argument(kind, name)
                    provenance[name] = "task_constraint"
            if valid:
                option = (arguments, provenance)
                if option not in options:
                    options.append(option)
        return options

    @staticmethod
    def _example_argument(kind: str, name: str) -> Any:
        lowered = str(kind).lower()
        if lowered in {"integer", "int"}:
            return 1
        if lowered in {"number", "float"}:
            return 1.0
        if lowered in {"boolean", "bool"}:
            return True
        if lowered in {"object", "dict"}:
            return {}
        if lowered.endswith("[]") or lowered in {"array", "list"}:
            return []
        return f"example_{name}"

    @staticmethod
    def _bound_observation_values(
        binding: str,
        input_name: str,
        observations: list[dict[str, Any]],
    ) -> list[tuple[Any, int]]:
        source_tool, separator, path = binding.partition(".")
        found: list[tuple[Any, int]] = []
        for index in range(len(observations) - 1, -1, -1):
            observation = observations[index]
            if source_tool and separator and observation["tool"] != source_tool:
                continue
            values = TaskSynthesizer._values_at_path(observation["result"], path or input_name)
            found.extend((value, index) for value in values if value not in (None, ""))
        return found

    @staticmethod
    def _values_at_path(value: Any, path: str) -> list[Any]:
        current = [value]
        for name, index_text in re.findall(r"([a-zA-Z0-9_]+)(?:\[([0-9]+)\])?", path):
            next_values = []
            for item in current:
                if isinstance(item, dict) and name in item:
                    child = item[name]
                    if index_text:
                        if isinstance(child, list) and int(index_text) < len(child):
                            next_values.append(child[int(index_text)])
                    else:
                        next_values.append(child)
                elif isinstance(item, list):
                    next_values.extend(child[name] for child in item if isinstance(child, dict) and name in child)
            current = next_values
            if not current:
                break
        return current

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
    def _causal_core(
        calls: list[dict[str, Any]],
        tools: dict[str, ToolSpec] | None = None,
    ) -> list[dict[str, Any]]:
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
            if tools is not None:
                target = tools[call["tool"]]
                for source_index in range(index):
                    source = tools[calls[source_index]["tool"]]
                    shares_state = set(source.writes) & set(target.reads) and TaskSynthesizer._same_state_arguments(
                        source,
                        target,
                        calls[source_index]["arguments"],
                        call["arguments"],
                    )
                    if source.name in target.requires_tools or shares_state:
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
    def _same_state_arguments(
        source: ToolSpec,
        target: ToolSpec,
        source_arguments: dict[str, Any],
        target_arguments: dict[str, Any],
    ) -> bool:
        target_names = target.selector_inputs()
        if not target_names:
            return True
        source_values = {
            str(source_arguments[name])
            for name in source.selector_inputs()
            if source_arguments.get(name) not in (None, "")
        }
        target_values = {
            str(target_arguments[name])
            for name in target_names
            if target_arguments.get(name) not in (None, "")
        }
        return bool(source_values & target_values)

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
        response = ""
        try:
            response = self.llm.complete_json(
                "Turn executed, source-grounded tool evidence into verifiable user tasks. Skip artificial chains.",
                json.dumps(prompt, ensure_ascii=False),
            )
            result = extract_json_object(response)
            raw_reviews = result["reviews"]
        except (RuntimeError, KeyError, TypeError, ValueError) as error:
            preview = response.strip().replace("\n", " ")[:300]
            print(f"[tasks] review request failed: {type(error).__name__}: {error}; response={preview!r}", flush=True)
            return []
        if not isinstance(raw_reviews, list):
            print(f"[tasks] review response has non-list reviews: {type(raw_reviews).__name__}", flush=True)
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
        if raw_reviews and not tasks:
            print(f"[tasks] review returned {len(raw_reviews)} item(s), but none passed task construction", flush=True)
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
        available_tool_names = {tool.name for tool in tools}
        if not request or any(name in request for name in tool_names) or not set(tool_names) <= available_tool_names:
            return None
        task_runtime = runtime.fork()
        initial_state = task_runtime.snapshot()
        execution = task_runtime.execute([{"tool": call["tool"], "arguments": call["arguments"]} for call in calls])
        final_state = task_runtime.snapshot()
        outcome = LocalToolRuntime.outcome(initial_state, final_state)
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
                "outcome": outcome,
            },
            reference_execution=execution | {"reference_answer": reference_answer, "final_state": final_state},
            initial_state=initial_state,
        )

    @staticmethod
    def _public_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [{"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs, "mutates_state": tool.mutates_state} for tool in tools]
