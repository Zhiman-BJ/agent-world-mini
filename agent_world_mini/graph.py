from __future__ import annotations

from collections import defaultdict
import json
import random
from .io_utils import extract_json_object
from .llm import LLMClient
from .models import ToolChain, ToolSpec


class ToolGraph:
    """Sparse data-flow graph with topology-aware exploratory sampling."""

    def __init__(self, tools: list[ToolSpec], llm: LLMClient | None = None):
        self.tools = {tool.name: tool for tool in tools}
        self.llm = llm
        self.edges = self._build_edges()
        self.construction_mode = "schema_data_flow"
        self.refinement_error = ""
        if llm is not None and llm.enabled and self.edges:
            self._refine_weak_edges()

    @staticmethod
    def _output_entity(tool: ToolSpec) -> str | None:
        if tool.operation in {"search", "lookup", "rank", "compare", "filter"}:
            return tool.entity_type
        if tool.operation in {"relation", "relation_rank", "linked_id"}:
            return tool.related_entity_type
        return None

    @staticmethod
    def _input_entity(tool: ToolSpec) -> str | None:
        if tool.operation in {"lookup", "compare", "relation", "relation_rank", "linked_id"}:
            return tool.entity_type
        return None

    def _edge_kind(self, source: ToolSpec, target: ToolSpec) -> tuple[str, int, str] | None:
        declared = [binding for binding in target.input_bindings.values() if binding.startswith(f"{source.name}.")]
        if declared:
            return "strong", 3, ", ".join(declared)
        output_entity = self._output_entity(source)
        if output_entity and output_entity == self._input_entity(target):
            return "weak", 2, f"{output_entity} value can flow into the target"
        return None

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

    def _refine_weak_edges(self) -> None:
        strong = [edge for edge in self.edges if edge["kind"] == "strong"]
        weak = [edge for edge in self.edges if edge["kind"] == "weak"]
        try:
            result = extract_json_object(self.llm.complete_json(
                "Refine this tool dependency graph. Keep only useful directed weak links and add missing strong or weak links when one tool's result can support the next. Unrelated tools need no edge.",
                json.dumps({
                    "tools": [
                        {"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs}
                        for tool in self.tools.values()
                    ],
                    "fixed_strong_edges": strong,
                    "candidate_weak_edges": weak,
                    "return": {"edges": [{"from": "tool", "to": "tool", "kind": "weak", "reason": "brief reason"}]},
                }, ensure_ascii=False),
            ))
            refined: list[dict[str, object]] = list(strong)
            seen = {(str(edge["from"]), str(edge["to"])) for edge in refined}
            for edge in result.get("edges", []):
                if not isinstance(edge, dict):
                    continue
                source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
                kind = str(edge.get("kind") or "weak")
                if source not in self.tools or target not in self.tools or source == target or kind not in {"strong", "weak"}:
                    continue
                if (source, target) in seen:
                    continue
                refined.append({
                    "from": source,
                    "to": target,
                    "kind": kind,
                    "weight": 3 if kind == "strong" else 2,
                    "reason": str(edge.get("reason") or "LLM-refined logical dependency"),
                })
                seen.add((source, target))
            self.edges = refined
            self.construction_mode = "schema_plus_llm_refinement"
        except (RuntimeError, ValueError, TypeError, KeyError) as error:
            self.refinement_error = f"{type(error).__name__}: {error}"
            self.construction_mode = "schema_data_flow_refinement_failed"

    def chains(self) -> list[ToolChain]:
        incoming = defaultdict(int)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            if edge["kind"] == "strong":
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

    def walks(self, min_steps: int = 1, max_steps: int = 8, count: int = 32, seed: int = 7) -> list[ToolChain]:
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
                available_entities = {self._output_entity(self.tools[item]) for item in path}
                needs_internal = any(source == "internal" for source in self.tools[name].input_sources.values())
                if needs_internal and self._input_entity(self.tools[name]) not in available_entities:
                    priors = [item for item in incoming_strong[name] if path.count(item) < 2]
                    if priors:
                        prior = rng.choice(sorted(priors))
                        add_with_priors(path, prior, depth + 1)
            if path.count(name) < 2:
                path.append(name)

        for _ in range(count):
            target_steps = rng.randint(min_steps, max_steps)
            path: list[str] = []
            add_with_priors(path, rng.choice(names))
            while len(path) < target_steps:
                current = path[-1]
                choices = [edge for edge in outgoing[current] if path.count(str(edge["to"])) < 2]
                if choices:
                    weighted = [edge for edge in choices for _ in range(int(edge["weight"]))]
                    next_name = str(rng.choice(weighted)["to"])
                else:
                    independent = [name for name in names if path.count(name) < 2]
                    if not independent:
                        break
                    next_name = rng.choice(independent)
                before = len(path)
                add_with_priors(path, next_name)
                if len(path) == before:
                    break
            kinds = [edge_lookup.get((left, right), "independent") for left, right in zip(path, path[1:])]
            walks.append(ToolChain(path, [artifact for name in path for artifact in self.tools[name].produces], kinds))
        return walks

    def to_dict(self, chains: list[ToolChain], walks: list[ToolChain] | None = None) -> dict[str, object]:
        return {
            "construction_mode": self.construction_mode,
            "refinement_error": self.refinement_error,
            "nodes": [tool.to_dict() for tool in self.tools.values()],
            "edges": self.edges,
            "independent_transitions": "implicit fallback during sampling",
            "strict_chains": [chain.to_dict() for chain in chains],
            "raw_weighted_walks": [chain.to_dict() for chain in walks or []],
        }
