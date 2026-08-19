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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchBundle":
        records = [
            Record(
                entity_type=str(item["entity_type"]),
                entity_id=str(item["entity_id"]),
                attributes=dict(item["attributes"]),
                source_url=str(item["source_url"]),
            )
            for item in value.get("records", [])
        ]
        if not value.get("theme") or not records:
            raise ValueError("A research bundle requires a theme and at least one record")
        entity_ids: dict[str, set[str]] = {}
        entity_fields: dict[str, set[str]] = {}
        for record in records:
            entity_ids.setdefault(record.entity_type, set()).add(record.entity_id)
            entity_fields.setdefault(record.entity_type, set()).update(record.attributes)
        relations: set[tuple[str, str]] = set()
        for record in records:
            for field, field_value in record.attributes.items():
                if not field.endswith("_id") or field_value is None:
                    continue
                for target_type, ids in entity_ids.items():
                    if str(field_value) in ids:
                        relations.add((f"{record.entity_type}.{field}", f"{target_type}.entity_id"))
        state_contract = dict(value.get("state_contract", {})) or {
            "state_classes": {"source_records": "immutable_source", "overlay_records": "local_overlay"},
            "entities": [
                {
                    "entity_type": entity_type,
                    "fields": sorted(entity_fields[entity_type]),
                    "record_count": len(ids),
                }
                for entity_type, ids in sorted(entity_ids.items())
            ],
            "relations": sorted(relations),
            "invariants": [
                "entity ids are unique within an entity type",
                "source records are immutable during a rollout",
                "local overlay is reset before each rollout",
            ],
        }
        derived_datasets = dict(value.get("derived_datasets", {})) or {
            "operational_entities": [
                record.attributes | {"entity_id": record.entity_id, "entity_type": record.entity_type}
                for record in records
            ]
        }
        complexification_value = value.get("complexification", [])
        if isinstance(complexification_value, dict):
            complexification_value = [complexification_value]
        elif isinstance(complexification_value, str):
            complexification_value = [{"note": complexification_value}]
        return cls(
            theme=str(value["theme"]),
            adapter=str(value.get("adapter") or "codex_research_agent"),
            retrieved_at=str(value.get("retrieved_at") or ""),
            sources=[dict(item) for item in value.get("sources", [])],
            records=records,
            derived_datasets=derived_datasets,
            theme_metadata=dict(value.get("theme_metadata", {})),
            complexification=[dict(item) for item in complexification_value if isinstance(item, dict)],
            state_contract=state_contract,
            overlay_seed=[dict(item) for item in value.get("overlay_seed", [])],
        )


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
    link_entity_type: str | None = None
    source_relation_field: str | None = None
    target_relation_field: str | None = None
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
