from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Record:
    entity_type: str
    entity_id: str
    attributes: dict[str, Any]
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchBundle:
    theme: str
    adapter: str
    retrieved_at: str
    sources: list[dict[str, str]]
    records: list[Record]
    derived_datasets: dict[str, list[dict[str, Any]]]
    theme_metadata: dict[str, Any] = field(default_factory=dict)
    complexification: list[dict[str, Any]] = field(default_factory=list)
    state_contract: dict[str, Any] = field(default_factory=dict)
    overlay_seed: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme,
            "adapter": self.adapter,
            "retrieved_at": self.retrieved_at,
            "sources": self.sources,
            "records": [record.to_dict() for record in self.records],
            "derived_datasets": self.derived_datasets,
            "theme_metadata": self.theme_metadata,
            "complexification": self.complexification,
            "state_contract": self.state_contract,
            "overlay_seed": self.overlay_seed,
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    reads: list[str]
    produces: list[str]
    requires_tools: list[str] = field(default_factory=list)
    mutates_state: bool = False
    operation: str = "search"
    entity_type: str = ""
    search_fields: list[str] = field(default_factory=list)
    sort_field: str | None = None
    related_entity_type: str | None = None
    relation_field: str | None = None
    input_bindings: dict[str, str] = field(default_factory=dict)
    test_cases: list[dict[str, Any]] = field(default_factory=list)
    input_sources: dict[str, str] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolChain:
    tool_names: list[str]
    produced_artifacts: list[str]
    edge_kinds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Task:
    task_id: str
    request: str
    available_tools: list[dict[str, Any]]
    hidden_reference_chain: list[str]
    validation: dict[str, Any]
    reference_execution: dict[str, Any] = field(default_factory=dict)
    initial_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
