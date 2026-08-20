from __future__ import annotations

from collections import defaultdict
import random
from typing import Any
from .llm import LLMClient
from .models import ToolChain, ToolSpec


class ToolGraph:
    """Sparse data-flow graph with topology-aware exploratory sampling."""

    def __init__(self, tools: list[ToolSpec], llm: LLMClient | None = None, runtime: Any | None = None):
        self.tools = {tool.name: tool for tool in tools}
        self.llm = llm
        self.runtime = runtime
        self.edges = self._build_edges()
        self.construction_mode = "value_checked_schema_data_flow" if runtime is not None else "schema_data_flow"
        self.sampling_mode = "topology_expansion_without_target_length"
        self.refinement_error = ""

    @staticmethod
    def _output_entity(tool: ToolSpec) -> str | None:
        if tool.operation in {"search", "lookup", "rank", "compare", "filter", "create", "update"}:
            return tool.entity_type
        if tool.operation in {"relation", "relation_rank", "linked_id", "bridge_relation"}:
            return tool.related_entity_type
        return None

    @staticmethod
    def _input_entity(tool: ToolSpec) -> str | None:
        if tool.operation in {"lookup", "compare", "relation", "relation_rank", "linked_id", "bridge_relation", "update", "delete"}:
            return tool.entity_type
        return None

    def _edge_kind(self, source: ToolSpec, target: ToolSpec) -> tuple[str, int, str] | None:
        if source.name in target.requires_tools:
            return "strong", 3, "declared prerequisite"
        declared = [binding for binding in target.input_bindings.values() if binding.startswith(f"{source.name}.")]
        if declared and self._values_can_flow(source, target):
            return "strong", 3, ", ".join(declared)
        changed_state = [] if source.operation in {"delete", "delete_file"} else sorted(set(source.writes) & set(target.reads))
        if changed_state and not self._same_state_object(source, target):
            changed_state = []
        if changed_state:
            return "state", 3, ", ".join(changed_state)
        output_entity = self._output_entity(source)
        if output_entity and output_entity == self._input_entity(target) and self._values_can_flow(source, target):
            return "weak", 2, f"{output_entity} value can flow into the target"
        return None

    @staticmethod
    def _same_state_object(source: ToolSpec, target: ToolSpec) -> bool:
        target_selectors = target.selector_inputs()
        if not target_selectors:
            return True
        source_bindings = {
            source.input_bindings[name]
            for name in source.selector_inputs()
            if source.input_bindings.get(name)
        }
        target_bindings = {
            target.input_bindings[name]
            for name in target_selectors
            if target.input_bindings.get(name)
        }
        return bool(source_bindings & target_bindings)

    def _values_can_flow(self, source: ToolSpec, target: ToolSpec) -> bool:
        if self.runtime is None:
            return True
        if source.mutates_state or target.mutates_state:
            return True
        possible = self._possible_output_ids(source)
        accepted = self._feasible_input_ids(target)
        if possible is None or accepted is None:
            return True
        required = 2 if target.operation == "compare" else 1
        return len(possible & accepted) >= required

    def _possible_output_ids(self, tool: ToolSpec) -> set[str] | None:
        if self.runtime is None:
            return None
        if tool.operation in {"search", "lookup", "rank", "compare", "filter"}:
            return {str(row["entity_id"]) for row in self.runtime.rows_for(tool.entity_type)}
        if tool.operation in {"relation", "relation_rank"}:
            source_ids = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.entity_type)}
            return {
                str(row["entity_id"])
                for row in self.runtime.rows_for(tool.related_entity_type or "")
                if str(row.get(tool.relation_field or "")) in source_ids
            }
        if tool.operation == "bridge_relation":
            targets = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.related_entity_type or "")}
            return {
                str(row[tool.target_relation_field or ""])
                for row in self.runtime.rows_for(tool.link_entity_type or "")
                if str(row.get(tool.target_relation_field or "")) in targets
            }
        if tool.operation == "linked_id":
            targets = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.related_entity_type or "")}
            return {
                str(row[tool.relation_field or ""])
                for row in self.runtime.rows_for(tool.entity_type)
                if str(row.get(tool.relation_field or "")) in targets
            }
        return None

    def _feasible_input_ids(self, tool: ToolSpec) -> set[str] | None:
        if self.runtime is None or self._input_entity(tool) is None:
            return None
        source_ids = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.entity_type)}
        if tool.operation in {"lookup", "compare"}:
            return source_ids
        if tool.operation in {"relation", "relation_rank"}:
            return source_ids & {
                str(row[tool.relation_field or ""])
                for row in self.runtime.rows_for(tool.related_entity_type or "")
                if row.get(tool.relation_field or "") is not None
            }
        if tool.operation == "bridge_relation":
            target_ids = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.related_entity_type or "")}
            return source_ids & {
                str(row[tool.source_relation_field or ""])
                for row in self.runtime.rows_for(tool.link_entity_type or "")
                if str(row.get(tool.target_relation_field or "")) in target_ids
            }
        if tool.operation == "linked_id":
            target_ids = {str(row["entity_id"]) for row in self.runtime.rows_for(tool.related_entity_type or "")}
            return {
                str(row["entity_id"])
                for row in self.runtime.rows_for(tool.entity_type)
                if str(row.get(tool.relation_field or "")) in target_ids
            }
        return source_ids

    def _build_edges(self) -> list[dict[str, object]]:
        edges: list[dict[str, object]] = []
        for source in sorted(self.tools.values(), key=lambda tool: tool.name):
            for target in sorted(self.tools.values(), key=lambda tool: tool.name):
                if source.name == target.name:
                    continue
                relation = self._edge_kind(source, target)
                if relation is None:
                    continue
                kind, weight, reason = relation
                edges.append({"from": source.name, "to": target.name, "kind": kind, "weight": weight, "reason": reason})
        return edges

    def chains(self) -> list[ToolChain]:
        incoming = defaultdict(int)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            if edge["kind"] in {"strong", "state"}:
                incoming[str(edge["to"])] += 1
                outgoing[str(edge["from"])].append(str(edge["to"]))
        roots = sorted(name for name in self.tools if incoming[name] == 0)
        paths: set[tuple[str, ...]] = set()

        def visit(path: list[str]) -> None:
            next_nodes = sorted(set(outgoing[path[-1]]))
            if not next_nodes:
                paths.add(tuple(path))
                return
            for next_node in next_nodes:
                if next_node not in path:
                    visit([*path, next_node])

        for root in roots:
            visit([root])
        return [
            ToolChain(list(path), [artifact for name in path for artifact in self.tools[name].produces], ["strong"] * max(0, len(path) - 1))
            for path in sorted(paths, key=lambda item: (len(item), item))
        ]

    def walks(self, max_steps: int = 14, count: int = 32, seed: int = 7) -> list[ToolChain]:
        """Sample useful connected topology without assigning a target length."""
        if not self.tools:
            return []
        outgoing: dict[str, list[dict[str, object]]] = defaultdict(list)
        incoming_strong: dict[str, list[str]] = defaultdict(list)
        edge_lookup: dict[tuple[str, str], str] = {}
        for edge in self.edges:
            source, target = str(edge["from"]), str(edge["to"])
            outgoing[source].append(edge)
            edge_lookup[(source, target)] = str(edge["kind"])
            if edge["kind"] == "strong":
                incoming_strong[target].append(source)

        rng = random.Random(seed)
        names = sorted(self.tools)
        walks: list[ToolChain] = []

        def add_with_priors(path: list[str], name: str, depth: int = 0) -> None:
            if depth < 3:
                tool = self.tools[name]
                declared_priors = list(dict.fromkeys([
                    *tool.requires_tools,
                    *(binding.partition(".")[0] for binding in tool.input_bindings.values() if "." in binding),
                ]))
                for prior in declared_priors:
                    if prior in self.tools and prior not in path:
                        add_with_priors(path, prior, depth + 1)
                available_entities = {self._output_entity(self.tools[item]) for item in path}
                needs_internal = any(source == "internal" for source in tool.input_sources.values())
                if not declared_priors and needs_internal and self._input_entity(tool) not in available_entities:
                    priors = [item for item in incoming_strong[name] if item not in path]
                    if priors:
                        prior = rng.choice(sorted(priors, key=lambda item: (self.tools[item].operation != "search", item)))
                        add_with_priors(path, prior, depth + 1)
            repeatable = self.tools[name].operation in {"lookup", "relation", "relation_rank", "bridge_relation"}
            if path.count(name) < (2 if repeatable else 1):
                path.append(name)

        roots = [
            name for name in names
            if self.tools[name].produces
            and not any(source == "internal" for source in self.tools[name].input_sources.values())
        ] or names
        root_weights = {
            "search": 4, "rank": 3, "filter": 2, "group_count": 1,
        }

        for walk_index in range(count):
            path: list[str] = []
            transitions: set[tuple[str, str]] = set()
            weighted_roots = [name for name in roots for _ in range(root_weights.get(self.tools[name].operation, 1))]
            root = rng.choice(weighted_roots)
            add_with_priors(path, root)
            frontier: list[tuple[str, int]] = [(root, 0)]
            while frontier and len(path) < max_steps:
                current, depth = frontier.pop(0)
                choices = []
                for edge in outgoing[current]:
                    target = str(edge["to"])
                    repeat_limit = 2 if self.tools[target].operation in {"lookup", "relation", "relation_rank", "bridge_relation"} else 1
                    if path.count(target) >= repeat_limit:
                        continue
                    input_entity = self._input_entity(self.tools[target])
                    output_entity = self._output_entity(self.tools[target])
                    if input_entity and output_entity and input_entity != output_entity and (output_entity, input_entity) in transitions:
                        continue
                    choices.append(edge)
                if not choices:
                    continue
                operation_priority = {
                    "relation_rank": 6, "bridge_relation": 5, "relation": 5,
                    "compare": 4, "lookup": 3, "linked_id": 2,
                    "create": 5, "update": 6, "delete": 4,
                    "copy_resource": 5, "read_file": 6, "python": 6, "write_file": 5,
                }
                weighted_choices = [
                    edge
                    for edge in choices
                    for _ in range(int(edge["weight"]) + operation_priority.get(self.tools[str(edge["to"])].operation, 1))
                ]
                fanout = min(len(choices), 2 if depth < 2 else 1, max_steps - len(path))
                selected: list[dict[str, object]] = []
                while weighted_choices and len(selected) < fanout:
                    edge = rng.choice(weighted_choices)
                    if edge not in selected:
                        selected.append(edge)
                    weighted_choices = [item for item in weighted_choices if item is not edge]
                for edge in selected:
                    target = str(edge["to"])
                    before = len(path)
                    add_with_priors(path, target)
                    if len(path) > before:
                        input_entity = self._input_entity(self.tools[target])
                        output_entity = self._output_entity(self.tools[target])
                        if input_entity and output_entity and input_entity != output_entity:
                            transitions.add((input_entity, output_entity))
                        frontier.append((target, depth + 1))
                    feasible = self._feasible_input_ids(self.tools[target])
                    if (
                        len(path) < max_steps
                        and self.tools[target].operation in {"relation", "relation_rank", "bridge_relation"}
                        and feasible is not None
                        and len(feasible) >= 2
                        and path.count(target) == 1
                    ):
                        path.append(target)
            kinds = [
                "branch" if left == right else edge_lookup.get((left, right), "dependency")
                for left, right in zip(path, path[1:])
            ]
            walks.append(ToolChain(path, [artifact for name in path for artifact in self.tools[name].produces], kinds))
        return walks

    def to_dict(self, chains: list[ToolChain], walks: list[ToolChain] | None = None) -> dict[str, object]:
        return {
            "construction_mode": self.construction_mode,
            "sampling_mode": self.sampling_mode,
            "refinement_error": self.refinement_error,
            "nodes": [tool.to_dict() for tool in self.tools.values()],
            "edges": self.edges,
            "independent_transitions": "disabled; sampled subgraphs stay on executable data-flow edges",
            "strict_chains": [chain.to_dict() for chain in chains],
            "raw_weighted_walks": [chain.to_dict() for chain in walks or []],
        }
