from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from agent_world_mini.utils.io import extract_json_object
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.schemas.models import ResearchBundle, ToolSpec


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "item"


def _field_specs(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    fields: dict[str, dict[str, Any]] = {}
    for name, spec in value.items():
        if isinstance(spec, str):
            fields[str(name)] = {"type": spec, "example": _example_for_type(spec, str(name))}
        elif isinstance(spec, dict):
            kind = str(spec.get("type") or "string")
            fields[str(name)] = {
                "type": kind,
                "example": deepcopy(spec.get("example", _example_for_type(kind, str(name)))),
                "update_example": deepcopy(spec.get("update_example", spec.get("example", _example_for_type(kind, str(name))))),
            }
    return fields


def _example_for_type(kind: str, name: str) -> Any:
    lowered = kind.lower()
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
    return f"example_{_slug(name)}"


class EnvironmentCompiler:
    """Turn one researched bundle into executable, environment-specific tools.

    The compiler itself is theme-agnostic. Research supplies real resources and
    a small capability blueprint; this class turns that blueprint into the same
    runtime primitives for every environment.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def prepare(self, bundle: ResearchBundle, use_agent: bool = True) -> ResearchBundle:
        metadata = bundle.theme_metadata
        existing = metadata.get("environment_blueprint")
        if isinstance(existing, dict):
            metadata["environment_blueprint"] = self._normalize(existing)
            return bundle

        inferred = self._inferred_blueprint(bundle)
        should_plan = bool(bundle.resources or bundle.overlay_seed or self._has_action_capabilities(bundle))
        if use_agent and self.llm.enabled and should_plan:
            try:
                result = extract_json_object(self.llm.complete_json(
                    "Design a small executable environment plan from the supplied real data and MCP capability clues. "
                    "Mutable rows are local rollout state, not new real-world facts. Use CRUD for ordinary state and "
                    "a Python tool only for a useful operation that CRUD or file reading cannot express. Never invent "
                    "scientific, commercial, or operational facts. Return only the requested JSON.",
                    json.dumps(self._planning_packet(bundle), ensure_ascii=False),
                ))
                inferred = self._normalize(result)
            except (RuntimeError, ValueError, TypeError):
                pass
        metadata["environment_blueprint"] = inferred
        return bundle

    @staticmethod
    def _has_action_capabilities(bundle: ResearchBundle) -> bool:
        action_words = re.compile(r"\b(create|add|update|edit|delete|remove|write|upload|download|export|import|publish|cancel|approve)\b", re.I)
        return any(
            action_words.search(f"{tool.get('name', '')} {tool.get('description', '')}")
            for tool in bundle.theme_metadata.get("documented_tools", [])
            if isinstance(tool, dict)
        )

    @staticmethod
    def _inferred_blueprint(bundle: ResearchBundle) -> dict[str, Any]:
        mutable: dict[str, dict[str, Any]] = {}
        for row in bundle.overlay_seed:
            entity_type = str(row.get("entity_type") or "")
            if not entity_type:
                continue
            attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else row
            fields = {
                str(name): {"type": EnvironmentCompiler._value_type(value), "example": deepcopy(value)}
                for name, value in attributes.items()
                if name not in {"entity_id", "entity_type", "source_url"}
            }
            current = mutable.setdefault(entity_type, {
                "entity_type": entity_type,
                "fields": {},
                "operations": ["create", "read", "update", "delete"],
            })
            current["fields"].update(fields)
        return {"mutable_entities": list(mutable.values()), "python_tools": []}

    @staticmethod
    def _value_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        return "string"

    @staticmethod
    def _normalize(value: dict[str, Any]) -> dict[str, Any]:
        mutable_entities = []
        for item in value.get("mutable_entities", []):
            if not isinstance(item, dict) or not item.get("entity_type"):
                continue
            fields = _field_specs(item.get("fields"))
            if not fields:
                continue
            operations = [
                str(operation).lower()
                for operation in item.get("operations", ["create", "read", "update", "delete"])
                if str(operation).lower() in {"create", "read", "update", "delete"}
            ]
            mutable_entities.append({
                "entity_type": _slug(str(item["entity_type"])),
                "description": str(item.get("description") or "").strip(),
                "fields": fields,
                "operations": list(dict.fromkeys(operations)),
                "update_fields": [str(name) for name in item.get("update_fields", []) if str(name) in fields],
            })

        python_tools = []
        for item in value.get("python_tools", []):
            if not isinstance(item, dict) or not item.get("name") or not item.get("implementation"):
                continue
            python_tools.append({
                "name": _slug(str(item["name"])),
                "description": str(item.get("description") or "Execute an environment operation."),
                "inputs": dict(item.get("inputs", {})),
                "outputs": dict(item.get("outputs", {})),
                "reads": [str(name) for name in item.get("reads", [])],
                "produces": [str(name) for name in item.get("produces", [])],
                "writes": [str(name) for name in item.get("writes", [])],
                "requires_tools": [str(name) for name in item.get("requires_tools", [])],
                "input_bindings": dict(item.get("input_bindings", {})),
                "input_sources": dict(item.get("input_sources", {})),
                "implementation": str(item["implementation"]),
                "test_cases": [dict(test) for test in item.get("test_cases", []) if isinstance(test, dict)],
            })
        return {"mutable_entities": mutable_entities, "python_tools": python_tools}

    @staticmethod
    def _planning_packet(bundle: ResearchBundle) -> dict[str, Any]:
        entities: dict[str, set[str]] = {}
        for record in bundle.records:
            entities.setdefault(record.entity_type, set()).update(record.attributes)
        return {
            "environment": bundle.theme,
            "real_entities": [
                {"entity_type": name, "fields": sorted(fields)} for name, fields in sorted(entities.items())
            ],
            "resources": [
                {
                    "resource_id": resource.get("resource_id") or resource.get("id"),
                    "name": resource.get("name"),
                    "media_type": resource.get("media_type"),
                    "source_url": resource.get("source_url"),
                }
                for resource in bundle.resources
            ],
            "documented_capabilities": [
                {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or "")[:300],
                    "input_schema": tool.get("inputSchema") or tool.get("input_schema") or {},
                }
                for tool in bundle.theme_metadata.get("documented_tools", [])
                if isinstance(tool, dict)
            ],
            "return": {
                "mutable_entities": [{
                    "entity_type": "local domain entity",
                    "description": "what the user manages",
                    "fields": {"field": {"type": "string", "example": "initial task-local value", "update_example": "updated task-local value"}},
                    "operations": ["create", "read", "update", "delete"],
                    "update_fields": ["field"],
                }],
                "python_tools": [{
                    "name": "domain_operation",
                    "description": "clear operation",
                    "inputs": {"path": "string"},
                    "outputs": {"result": "object"},
                    "reads": ["file_path"],
                    "produces": ["result"],
                    "writes": [],
                    "requires_tools": ["download_resource_name"],
                    "input_bindings": {"path": "download_resource_name.path"},
                    "input_sources": {"path": "internal"},
                    "implementation": "def run(context, arguments):\n    data = context.read_file(arguments['path'])\n    return data\n",
                    "test_cases": [{"setup_calls": [{"tool": "download_resource_name", "arguments": {}}], "arguments": {"path": "files/name.json"}, "expect_nonempty": True}],
                }],
            },
        }

    def compile_tools(self, bundle: ResearchBundle) -> list[ToolSpec]:
        tools = self._resource_tools(bundle)
        blueprint = bundle.theme_metadata.get("environment_blueprint", {})
        for entity in blueprint.get("mutable_entities", []) if isinstance(blueprint, dict) else []:
            tools.extend(self._mutable_entity_tools(entity))
        for item in blueprint.get("python_tools", []) if isinstance(blueprint, dict) else []:
            tools.append(ToolSpec(
                name=str(item["name"]),
                description=str(item["description"]),
                inputs=dict(item["inputs"]),
                outputs=dict(item["outputs"]),
                reads=list(item["reads"]),
                produces=list(item["produces"]),
                requires_tools=list(item["requires_tools"]),
                mutates_state=bool(item["writes"]),
                operation="python",
                input_bindings=dict(item["input_bindings"]),
                input_sources=dict(item["input_sources"]),
                test_cases=list(item["test_cases"]),
                writes=list(item["writes"]),
                backend="python",
                implementation=str(item["implementation"]),
            ))
        return tools

    @staticmethod
    def _resource_tools(bundle: ResearchBundle) -> list[ToolSpec]:
        tools = []
        for resource in bundle.resources:
            resource_id = str(resource.get("resource_id") or resource.get("id") or "")
            if not resource_id:
                continue
            stem = _slug(str(resource.get("name") or resource_id))
            download_name = f"download_{stem}"
            path = f"files/{resource.get('name') or resource_id + '.json'}".replace("\\", "/")
            tools.append(ToolSpec(
                name=download_name,
                description=f"Copy the researched {resource.get('name') or resource_id} resource into this task's workspace.",
                inputs={},
                outputs={"path": "string", "resource": "object"},
                reads=[f"resource:{resource_id}"],
                produces=["file_path", f"file:{stem}"],
                mutates_state=True,
                operation="copy_resource",
                config={"resource_id": resource_id},
                test_cases=[{"arguments": {}, "expect_file": True}],
                writes=[f"file:{stem}"],
                effects=["writes:workspace_file"],
            ))
            tools.append(ToolSpec(
                name=f"read_{stem}",
                description=f"Read the local {resource.get('name') or resource_id} workspace file.",
                inputs={"path": "string"},
                outputs={"data": "object"},
                reads=["file_path", f"file:{stem}"],
                produces=[f"{stem}_data"],
                requires_tools=[download_name],
                operation="read_file",
                input_bindings={"path": f"{download_name}.path"},
                input_sources={"path": "internal"},
                test_cases=[{
                    "setup_calls": [{"tool": download_name, "arguments": {}}],
                    "arguments": {"path": path},
                    "expect_nonempty": True,
                }],
            ))
        return tools

    @staticmethod
    def _mutable_entity_tools(item: dict[str, Any]) -> list[ToolSpec]:
        entity_type = str(item["entity_type"])
        singular = _slug(entity_type)
        fields = _field_specs(item.get("fields"))
        examples = {name: deepcopy(spec["example"]) for name, spec in fields.items()}
        input_types = {name: str(spec["type"]) for name, spec in fields.items()}
        operations = set(item.get("operations", []))
        update_names = list(item.get("update_fields") or fields)[:3]
        update_inputs = {name: input_types[name] for name in update_names}
        description = str(item.get("description") or entity_type.replace("_", " "))
        create_name = f"create_{singular}"
        get_name = f"get_{singular}"
        tools: list[ToolSpec] = []
        if "create" in operations:
            tools.append(ToolSpec(
                name=create_name,
                description=f"Create one {description} in the local task state.",
                inputs=input_types,
                outputs={singular: entity_type, f"{singular}_id": "string"},
                reads=[],
                produces=[entity_type, f"{singular}_id"],
                mutates_state=True,
                operation="create",
                entity_type=entity_type,
                input_sources={name: "external" for name in input_types},
                test_cases=[{"arguments": examples, "expect_entity_type": entity_type}],
                writes=[entity_type],
                effects=[f"creates:{entity_type}"],
            ))
        if "read" in operations:
            setup = [{"tool": create_name, "arguments": examples}] if "create" in operations else []
            tools.append(ToolSpec(
                name=get_name,
                description=f"Retrieve one {description} from the local task state.",
                inputs={"entity_id": "string"},
                outputs={singular: entity_type},
                reads=[entity_type, f"{singular}_id"],
                produces=[entity_type, f"{singular}_id"],
                requires_tools=[create_name] if setup else [],
                operation="lookup",
                entity_type=entity_type,
                input_bindings={"entity_id": f"{create_name}.entity_id"} if setup else {},
                input_sources={"entity_id": "internal" if setup else "external"},
                test_cases=[{"setup_calls": setup, "arguments": {"entity_id": f"{singular}-1"}, "expect_entity_id": f"{singular}-1"}],
            ))
        if "update" in operations and update_inputs:
            setup = [{"tool": create_name, "arguments": examples}] if "create" in operations else []
            arguments = {"entity_id": f"{singular}-1"} | {
                name: deepcopy(fields[name].get("update_example", fields[name]["example"])) for name in update_names
            }
            tools.append(ToolSpec(
                name=f"update_{singular}",
                description=f"Update one {description} in the local task state.",
                inputs={"entity_id": "string"} | update_inputs,
                outputs={singular: entity_type},
                reads=[entity_type, f"{singular}_id"],
                produces=[entity_type, f"{singular}_id"],
                requires_tools=[create_name] if setup else [],
                mutates_state=True,
                operation="update",
                entity_type=entity_type,
                input_bindings={"entity_id": f"{create_name}.entity_id"} if setup else {},
                input_sources={"entity_id": "internal" if setup else "external"} | {name: "external" for name in update_inputs},
                test_cases=[{"setup_calls": setup, "arguments": arguments, "expect_entity_id": f"{singular}-1"}],
                writes=[entity_type],
                effects=[f"updates:{entity_type}"],
            ))
        if "delete" in operations:
            dependency = f"update_{singular}" if "update" in operations and update_inputs else create_name
            setup = [{"tool": create_name, "arguments": examples}] if "create" in operations else []
            tools.append(ToolSpec(
                name=f"delete_{singular}",
                description=f"Delete one {description} from the local task state.",
                inputs={"entity_id": "string"},
                outputs={"deleted": "boolean", f"{singular}_id": "string"},
                reads=[entity_type, f"{singular}_id"],
                produces=["deletion", f"{singular}_id"],
                requires_tools=[create_name] if setup else [],
                mutates_state=True,
                operation="delete",
                entity_type=entity_type,
                input_bindings={"entity_id": f"{dependency}.entity_id"} if setup else {},
                input_sources={"entity_id": "internal" if setup else "external"},
                test_cases=[{"setup_calls": setup, "arguments": {"entity_id": f"{singular}-1"}, "expect_deleted": True}],
                writes=[entity_type],
                effects=[f"deletes:{entity_type}"],
            ))
        return tools
