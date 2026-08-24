from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import Any

from agent_world_mini.utils.io import extract_json_object
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.schemas.models import ResearchBundle, ToolSpec
from agent_world_mini.env_gen.tool_gen.compiler import EnvironmentCompiler


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "record"


def _plural(value: str) -> str:
    if value.endswith("y") and len(value) > 1 and value[-2] not in "aeiou":
        return value[:-1] + "ies"
    return value if value.endswith("s") else value + "s"


class ToolDesigner:
    """Compile source-grounded environment configuration into public tools.

    The shared runtime implements a small set of safe operations.  What varies
    per environment is the public tool contract: entity type, fields, relation,
    input/output names, and tests are derived from the mined state.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.last_selection_report: dict[str, Any] = {"status": "not_run"}

    def design(self, bundle: ResearchBundle, use_agent_selection: bool = True) -> tuple[list[ToolSpec], str]:
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in bundle.records:
            by_type[record.entity_type].append(record.attributes | {"entity_id": record.entity_id})
        tools: list[ToolSpec] = []
        search_tools: dict[str, ToolSpec] = {}
        # Do not impose a fixed tool count. Every entity type that has a real
        # text key can expose discovery and inspection; relation-only join
        # records remain internal plumbing and are reached through endpoints.
        for entity_type, rows in sorted(by_type.items(), key=lambda item: (-len(item[1]), item[0])):
            if entity_type.endswith("_link"):
                continue
            singular, plural = _slug(entity_type), _plural(_slug(entity_type))
            fields = self._search_fields(rows)
            if not fields:
                continue
            search_name = f"search_{plural}"
            search = ToolSpec(
                name=search_name,
                description=f"Search locally stored {entity_type.replace('_', ' ')} records by their documented text fields.",
                inputs={"query": "string"},
                outputs={f"{singular}_ids": "string[]", f"{plural}": f"{entity_type}[]"},
                reads=[entity_type],
                produces=[f"{singular}_ids", f"{plural}"],
                operation="search",
                entity_type=entity_type,
                search_fields=fields,
                input_sources={"query": "external"},
                test_cases=self._search_tests(rows, fields),
            )
            lookup = ToolSpec(
                name=f"get_{singular}",
                description=f"Retrieve one locally stored {entity_type.replace('_', ' ')} record by an ID returned from a search.",
                inputs={"entity_id": "string"},
                outputs={singular: entity_type},
                reads=[entity_type, f"{singular}_ids"],
                produces=[singular],
                requires_tools=[search_name],
                operation="lookup",
                entity_type=entity_type,
                input_bindings={"entity_id": f"{search_name}.{singular}_ids[0]"},
                input_sources={"entity_id": "internal"},
                test_cases=self._lookup_tests(rows),
            )
            tools.extend([search, lookup])
            search_tools[entity_type] = search
            numeric_field = self._numeric_field(rows)
            if numeric_field:
                rank_name = f"rank_{plural}_by_{_slug(numeric_field)}"
                tools.append(ToolSpec(
                    name=rank_name,
                    description=f"Rank locally stored {entity_type.replace('_', ' ')} records by the documented numeric field {numeric_field}.",
                    inputs={"limit": "integer"},
                    outputs={f"ranked_{plural}": f"{entity_type}[]"},
                    reads=[entity_type],
                    produces=[f"ranked_{plural}"],
                    operation="rank",
                    entity_type=entity_type,
                    sort_field=numeric_field,
                    input_sources={"limit": "external"},
                    test_cases=[{"arguments": {"limit": min(3, len(rows))}, "expect_nonempty": True}],
                ))
                if len(rows) >= 2:
                    tools.append(ToolSpec(
                        name=f"compare_{plural}_by_{_slug(numeric_field)}",
                        description=f"Compare two locally stored {entity_type.replace('_', ' ')} records using the documented numeric field {numeric_field}.",
                        inputs={"left_id": "string", "right_id": "string"},
                        outputs={"winner_id": "string", "difference": "number", "comparison": "object"},
                        reads=[entity_type],
                        produces=["winner_id", "comparison"],
                        operation="compare",
                        entity_type=entity_type,
                        sort_field=numeric_field,
                        input_bindings={
                            "left_id": f"{rank_name}.ranked_{plural}[0].entity_id",
                            "right_id": f"{rank_name}.ranked_{plural}[1].entity_id",
                        },
                        input_sources={"left_id": "internal", "right_id": "internal"},
                        test_cases=[{
                            "arguments": {"left_id": rows[0]["entity_id"], "right_id": rows[1]["entity_id"]},
                            "expect_winner": True,
                        }],
                    ))
            for field in self._categorical_fields(rows):
                values = sorted({str(row[field]) for row in rows if row.get(field) not in (None, "")})
                tools.append(ToolSpec(
                    name=f"list_{plural}_by_{_slug(field)}",
                    description=f"List locally stored {entity_type.replace('_', ' ')} records with a documented {field} value.",
                    inputs={field: "string", "limit": "integer"},
                    outputs={plural: f"{entity_type}[]"},
                    reads=[entity_type],
                    produces=[plural],
                    operation="filter",
                    entity_type=entity_type,
                    relation_field=field,
                    input_sources={field: "external", "limit": "external"},
                    test_cases=[{"arguments": {field: values[0], "limit": min(3, len(rows))}, "expect_nonempty": True}],
                ))
                tools.append(ToolSpec(
                    name=f"count_{plural}_by_{_slug(field)}",
                    description=f"Count locally stored {entity_type.replace('_', ' ')} records grouped by their documented {field} value.",
                    inputs={},
                    outputs={f"{plural}_counts": "object"},
                    reads=[entity_type],
                    produces=[f"{plural}_counts"],
                    operation="group_count",
                    entity_type=entity_type,
                    relation_field=field,
                    test_cases=[{"arguments": {}, "expect_nonempty": True}],
                ))

        tools.extend(self._relation_tools(by_type, search_tools))
        tools.extend(self._bridge_tools(by_type, search_tools))
        tools.extend(EnvironmentCompiler(self.llm).compile_tools(bundle))
        candidates = self._deduplicate(tools)
        if not use_agent_selection:
            self.last_selection_report = {
                "status": "local_validation_pending",
                "candidate_tools": len(candidates),
                "reason": "Luna handoff mode keeps data-derived candidates for local execution validation.",
            }
            return candidates, "data_configuration_compiler"
        if not self.llm.enabled:
            self.last_selection_report = {"status": "not_run_no_llm", "candidate_tools": len(candidates)}
            return candidates, "data_configuration_compiler"
        selected = self._select_by_agent(bundle, candidates)
        return selected, "data_grounded_agent_selection"

    def _select_by_agent(self, bundle: ResearchBundle, candidates: list[ToolSpec]) -> list[ToolSpec]:
        prompt = json.dumps(self.selection_packet(bundle, candidates), ensure_ascii=False)
        result: dict[str, Any] = {}
        for attempt in range(2):
            try:
                result = extract_json_object(self.llm.complete_json(
                    "Choose a coherent connected toolset from the candidate tools. MCP tools are capability clues, not names to copy. keep_tool_names must contain only exact candidate tool names. Prefer real relation traversal when the data has relations. Metadata or documentation lookup alone is not a usable environment.",
                    prompt,
                ))
            except RuntimeError:
                if attempt == 0:
                    print("[tools] model request failed; retrying once", flush=True)
                    continue
                raise
            usable = result.get("usable_environment")
            explicitly_unusable = usable is False or (isinstance(usable, str) and usable.strip().lower() == "false")
            if explicitly_unusable or result.get("keep_tool_names"):
                break
            if attempt == 0:
                print("[tools] model returned an unexplained empty selection; retrying once", flush=True)
        return self.apply_selection_result(bundle, candidates, result)

    @staticmethod
    def selection_packet(bundle: ResearchBundle, candidates: list[ToolSpec]) -> dict[str, Any]:
        return {
            "environment": bundle.theme,
            "mcp_description": bundle.theme_metadata.get("source_description", ""),
            "mcp_tools": [
                {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or "")[:300],
                }
                for tool in bundle.theme_metadata.get("documented_tools", [])
                if isinstance(tool, dict)
            ],
            "data_entities": bundle.state_contract.get("entities", []),
            "data_relations": bundle.state_contract.get("relations", []),
            "local_resources": [
                {key: resource.get(key) for key in ("resource_id", "name", "media_type", "source_url")}
                for resource in bundle.resources
            ],
            "environment_blueprint": bundle.theme_metadata.get("environment_blueprint", {}),
            "candidate_tools": [
                {"name": tool.name, "description": tool.description, "inputs": tool.inputs, "outputs": tool.outputs}
                for tool in candidates
            ],
            "return": {
                "usable_environment": True,
                "keep_tool_names": ["candidate tool name"],
                "capability_support": [{
                    "mcp_tool_name": "exact MCP tool name",
                    "retained_tool_names": ["candidate tool producing the same business result"],
                }],
                "description_updates": {"candidate tool name": "clear business-facing description"},
                "missing_capabilities": ["core capability unsupported by the mined data"],
                "reason": "brief explanation of the resulting capability coverage",
            },
        }

    def apply_selection_result(
        self,
        bundle: ResearchBundle,
        candidates: list[ToolSpec],
        result: dict[str, Any],
    ) -> list[ToolSpec]:
        usable = result.get("usable_environment")
        documented_names = {
            str(tool.get("name"))
            for tool in bundle.theme_metadata.get("documented_tools", [])
            if isinstance(tool, dict) and tool.get("name")
        }
        by_name = {tool.name: tool for tool in candidates}
        requested = [str(name) for name in result.get("keep_tool_names", []) if str(name) in by_name]
        requested_names = set(requested)
        capability_support = []
        for item in result.get("capability_support", []):
            if not isinstance(item, dict):
                continue
            mcp_name = str(item.get("mcp_tool_name") or "")
            retained_names = [
                str(name)
                for name in item.get("retained_tool_names", [])
                if str(name) in requested_names
            ]
            if mcp_name in documented_names and retained_names:
                capability_support.append({"mcp_tool_name": mcp_name, "retained_tool_names": retained_names})
        supported_mcp_tools = sorted({item["mcp_tool_name"] for item in capability_support})
        explicitly_unusable = usable is False or (isinstance(usable, str) and usable.strip().lower() == "false")
        if explicitly_unusable or not requested:
            self.last_selection_report = {
                "status": "unusable_data",
                "candidate_tools": len(candidates),
                "retained_by_agent": 0,
                "supported_mcp_tools": supported_mcp_tools,
                "capability_support": capability_support,
                "missing_capabilities": result.get("missing_capabilities", []),
                "reason": str(result.get("reason") or ""),
                "requested_tool_names": [str(name) for name in result.get("keep_tool_names", [])],
            }
            return []
        supported_candidate_names = {
            name for item in capability_support for name in item["retained_tool_names"]
        }
        if supported_candidate_names:
            connected_entities = {
                entity
                for name in supported_candidate_names
                for entity in (by_name[name].entity_type, by_name[name].related_entity_type)
                if entity
            }
            changed = True
            while changed:
                changed = False
                for tool in candidates:
                    if tool.operation not in {"relation", "relation_rank", "linked_id", "bridge_relation"} or tool.entity_type not in connected_entities:
                        continue
                    if tool.related_entity_type and tool.related_entity_type not in connected_entities:
                        connected_entities.add(tool.related_entity_type)
                        changed = True
            requested = [
                name for name in requested
                if by_name[name].entity_type in connected_entities
                or by_name[name].related_entity_type in connected_entities
                or name in supported_candidate_names
            ]
            requested_names = set(requested)
        keep = set(requested)
        changed = True
        while changed:
            changed = False
            for name in list(keep):
                for required in by_name[name].requires_tools:
                    if required in by_name and required not in keep:
                        keep.add(required)
                        changed = True
        descriptions = result.get("description_updates", {})
        selected = []
        for tool in candidates:
            if tool.name not in keep:
                continue
            if isinstance(descriptions, dict) and descriptions.get(tool.name):
                tool.description = str(descriptions[tool.name]).strip()
            selected.append(tool)
        self.last_selection_report = {
            "status": "selected",
            "candidate_tools": len(candidates),
            "retained_by_agent": len(selected),
            "supported_mcp_tools": supported_mcp_tools,
            "capability_support": capability_support,
            "reason": str(result.get("reason") or ""),
            "retained_tools": [tool.name for tool in selected],
        }
        return selected

    @staticmethod
    def _search_fields(rows: list[dict[str, Any]]) -> list[str]:
        preferred = ("name", "title", "full_name", "modelId", "id", "repo_name", "description", "city", "region", "state", "indicator")
        fields = [field for field in preferred if any(isinstance(row.get(field), str) and row[field] for row in rows)]
        if fields:
            return fields[:3]
        discovered: list[str] = []
        for row in rows:
            for field, value in row.items():
                if field not in {"entity_id", "_id"} and not field.startswith("_") and isinstance(value, str) and value and field not in discovered:
                    discovered.append(field)
        return discovered[:3]

    @staticmethod
    def _numeric_field(rows: list[dict[str, Any]]) -> str | None:
        preferred = ("value", "free_bikes", "empty_slots", "comments", "stargazers_count", "open_issues_count")
        excluded = {"year", "number", "latitude", "longitude", "search_rank"}

        def is_identifier(field: str) -> bool:
            return field == "id" or field.endswith(("_id", "_number", "_code", "_key"))

        values: dict[str, set[int | float]] = defaultdict(set)
        for row in rows:
            for field, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[field].add(value)
        for field in preferred:
            if len(values.get(field, set())) >= 2:
                return field
        candidates = [
            (len(distinct), field)
            for field, distinct in values.items()
            if len(distinct) >= 2 and field not in excluded and not is_identifier(field)
        ]
        return max(candidates)[1] if candidates else None

    @staticmethod
    def _categorical_fields(rows: list[dict[str, Any]]) -> list[str]:
        preferred = ("state", "region", "income_level", "language", "work_type", "type", "country")
        fields: list[str] = []
        for field in preferred:
            values = {str(row[field]) for row in rows if isinstance(row.get(field), (str, bool)) and row.get(field) not in (None, "")}
            if 2 <= len(values) <= 8:
                fields.append(field)
        return fields[:2]

    @staticmethod
    def _search_tests(rows: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
        if not rows or not fields:
            return [{"arguments": {"query": ""}, "expect_nonempty": True}]
        value = next((str(row[field]) for row in rows for field in fields if row.get(field)), "")
        return [
            {"arguments": {"query": value[: max(1, min(12, len(value)))]}, "expect_nonempty": True},
            {"arguments": {"query": "__not_a_real_record__"}, "expect_count": 0},
        ]

    @staticmethod
    def _lookup_tests(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"arguments": {"entity_id": rows[0]["entity_id"]}, "expect_entity_id": rows[0]["entity_id"]}] if rows else []

    def _relation_tools(self, by_type: dict[str, list[dict[str, Any]]], search_tools: dict[str, ToolSpec]) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        entity_ids = {entity_type: {row["entity_id"] for row in rows} for entity_type, rows in by_type.items()}
        for target_type, rows in by_type.items():
            if target_type.endswith("_link"):
                continue
            for field in sorted({field for row in rows for field in row if field.endswith("_id")}):
                values = {str(row[field]) for row in rows if row.get(field) is not None}
                for source_type, ids in entity_ids.items():
                    if source_type == target_type or not values or not values <= ids:
                        continue
                    source_search = search_tools.get(source_type)
                    if not source_search:
                        continue
                    linked_row = next(row for row in rows if row.get(field) is not None)
                    linked_source_id = str(linked_row[field])
                    source_singular, target_plural = _slug(source_type), _plural(_slug(target_type))
                    relation_name = f"list_{target_plural}_for_{source_singular}"
                    tools.append(ToolSpec(
                        name=relation_name,
                        description=f"List locally stored {target_type.replace('_', ' ')} records linked to one {source_type.replace('_', ' ')} record.",
                        inputs={"entity_id": "string", "limit": "integer"},
                        outputs={target_plural: f"{target_type}[]"},
                        reads=[source_type, target_type, f"{source_singular}_ids"],
                        produces=[target_plural],
                        requires_tools=[source_search.name],
                        operation="relation",
                        entity_type=source_type,
                        related_entity_type=target_type,
                        relation_field=field,
                        input_bindings={"entity_id": f"{source_search.name}.{source_singular}_ids[0]"},
                        input_sources={"entity_id": "internal", "limit": "external"},
                        test_cases=[{"arguments": {"entity_id": next(iter(values)), "limit": min(3, len(rows))}, "expect_nonempty": True}],
                    ))
                    # The same verified foreign key also supports the forward
                    # direction: resolve the linked parent id from a child.
                    target_singular, source_singular = _slug(target_type), _slug(source_type)
                    tools.append(ToolSpec(
                        name=f"resolve_{source_singular}_for_{target_singular}",
                        description=f"Resolve the stored {source_type.replace('_', ' ')} identifier linked from one {target_type.replace('_', ' ')} record.",
                        inputs={"entity_id": "string"},
                        outputs={f"{source_singular}_id": "string"},
                        reads=[target_type, source_type],
                        produces=[f"{source_singular}_id"],
                        operation="linked_id",
                        entity_type=target_type,
                        related_entity_type=source_type,
                        relation_field=field,
                        requires_tools=[relation_name],
                        input_bindings={"entity_id": f"{relation_name}.{target_plural}[0].entity_id"},
                        input_sources={"entity_id": "internal"},
                        test_cases=[{"arguments": {"entity_id": linked_row["entity_id"]}, "expect_entity_id": linked_source_id}],
                    ))
                    numeric_field = self._numeric_field(rows)
                    if numeric_field:
                        tools.append(ToolSpec(
                            name=f"rank_{target_plural}_for_{source_singular}_by_{_slug(numeric_field)}",
                            description=f"Rank linked {target_type.replace('_', ' ')} records for one {source_type.replace('_', ' ')} by the documented numeric field {numeric_field}.",
                            inputs={"entity_id": "string", "limit": "integer"},
                            outputs={f"ranked_{target_plural}": f"{target_type}[]"},
                            reads=[source_type, target_type, f"{source_singular}_ids"],
                            produces=[f"ranked_{target_plural}"],
                            requires_tools=[source_search.name],
                            operation="relation_rank",
                            entity_type=source_type,
                            related_entity_type=target_type,
                            relation_field=field,
                            sort_field=numeric_field,
                            input_bindings={"entity_id": f"{source_search.name}.{source_singular}_ids[0]"},
                            input_sources={"entity_id": "internal", "limit": "external"},
                            test_cases=[{"arguments": {"entity_id": next(iter(values)), "limit": min(2, len(rows))}, "expect_nonempty": True}],
                        ))
        return tools

    def _bridge_tools(self, by_type: dict[str, list[dict[str, Any]]], search_tools: dict[str, ToolSpec]) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        entity_ids = {entity_type: {str(row["entity_id"]) for row in rows} for entity_type, rows in by_type.items()}
        for link_type, rows in by_type.items():
            if not link_type.endswith("_link"):
                continue
            bindings: list[tuple[str, str]] = []
            for field in sorted({field for row in rows for field in row if field.endswith("_id")}):
                values = {str(row[field]) for row in rows if row.get(field) is not None}
                target = next((entity_type for entity_type, ids in entity_ids.items() if entity_type != link_type and values and values <= ids), None)
                if target:
                    bindings.append((field, target))
            if len(bindings) != 2 or bindings[0][1] == bindings[1][1]:
                continue
            for (source_field, source_type), (target_field, target_type) in (bindings, bindings[::-1]):
                source_search = search_tools.get(source_type)
                if source_search is None:
                    continue
                source_singular = _slug(source_type)
                target_plural = _plural(_slug(target_type))
                example = next((row for row in rows if row.get(source_field) is not None and row.get(target_field) is not None), None)
                if example is None:
                    continue
                tools.append(ToolSpec(
                    name=f"list_{target_plural}_for_{source_singular}",
                    description=f"List locally stored {target_type.replace('_', ' ')} records linked to one {source_type.replace('_', ' ')} record.",
                    inputs={"entity_id": "string", "limit": "integer"},
                    outputs={target_plural: f"{target_type}[]"},
                    reads=[source_type, link_type, target_type],
                    produces=[target_plural],
                    requires_tools=[source_search.name],
                    operation="bridge_relation",
                    entity_type=source_type,
                    related_entity_type=target_type,
                    link_entity_type=link_type,
                    source_relation_field=source_field,
                    target_relation_field=target_field,
                    input_bindings={"entity_id": f"{source_search.name}.{_slug(source_type)}_ids[0]"},
                    input_sources={"entity_id": "internal", "limit": "external"},
                    test_cases=[{
                        "arguments": {"entity_id": str(example[source_field]), "limit": min(3, len(rows))},
                        "expect_nonempty": True,
                    }],
                ))
        return tools

    @staticmethod
    def _deduplicate(tools: list[ToolSpec]) -> list[ToolSpec]:
        kept: list[ToolSpec] = []
        fingerprints: set[tuple[object, ...]] = set()
        for tool in tools:
            fingerprint = (
                tool.operation, tool.entity_type, tool.related_entity_type, tool.relation_field,
                tool.link_entity_type, tool.source_relation_field, tool.target_relation_field, tool.sort_field,
                tool.backend,
                json.dumps(tool.config, sort_keys=True),
                tool.name if tool.backend == "python" or tool.operation in {"copy_resource", "read_file", "write_file", "delete_file"} else "",
            )
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                kept.append(tool)
        return kept


class ToolValidator:
    """Execute generated test cases and reject invalid configuration tools."""

    def validate(self, tools: list[ToolSpec], runtime: Any) -> tuple[list[ToolSpec], list[dict[str, Any]]]:
        retained: list[ToolSpec] = []
        reports: list[dict[str, Any]] = []
        for tool in tools:
            failures: list[str] = []
            rows = runtime.rows_for(tool.entity_type)
            if tool.operation in {"search", "lookup", "rank", "filter", "group_count", "relation", "relation_rank", "linked_id", "bridge_relation", "compare"} and not rows and not any(
                test.get("setup_calls") for test in tool.test_cases
            ):
                failures.append("target_entity_type_has_no_rows")
            if tool.operation == "search" and not tool.search_fields:
                failures.append("search_has_no_text_fields")
            if tool.operation == "rank" and not tool.sort_field:
                failures.append("rank_has_no_numeric_field")
            if tool.operation in {"filter", "group_count"} and not tool.relation_field:
                failures.append("categorical_operation_has_no_field")
            if tool.operation in {"compare", "relation_rank"} and not tool.sort_field:
                failures.append("numeric_operation_has_no_numeric_field")
            if tool.operation in {"relation", "bridge_relation"} and not tool.related_entity_type:
                failures.append("relation_has_no_target_type")
            if tool.operation == "bridge_relation" and not all((tool.link_entity_type, tool.source_relation_field, tool.target_relation_field)):
                failures.append("bridge_relation_is_incomplete")
            if tool.operation == "linked_id" and not tool.related_entity_type:
                failures.append("linked_id_has_no_target_type")
            for test in tool.test_cases:
                try:
                    runtime.reset()
                    for setup in test.get("setup_calls", []):
                        runtime.call(setup["tool"], setup.get("arguments", {}))
                    result = runtime.call(tool.name, test["arguments"])
                    if test.get("expect_nonempty") and not result:
                        failures.append(f"test_returned_empty:{test['arguments']}")
                    if "expect_count" in test and len(result) != test["expect_count"]:
                        failures.append(f"test_count_mismatch:{test['arguments']}")
                    if "expect_entity_id" in test and result.get("entity_id") != test["expect_entity_id"]:
                        failures.append(f"test_entity_mismatch:{test['arguments']}")
                    if test.get("expect_winner") and not result.get("winner_id"):
                        failures.append(f"test_missing_winner:{test['arguments']}")
                    if test.get("expect_deleted") and not result.get("deleted"):
                        failures.append(f"test_missing_deletion:{test['arguments']}")
                    if test.get("expect_file") and not result.get("path"):
                        failures.append(f"test_missing_file:{test['arguments']}")
                    if test.get("expect_entity_type") and result.get("entity_type") != test["expect_entity_type"]:
                        failures.append(f"test_entity_type_mismatch:{test['arguments']}")
                except Exception as error:
                    failures.append(f"runtime_error:{type(error).__name__}")
            report = {"tool": tool.name, "status": "passed" if not failures else "rejected", "failures": failures, "tests": tool.test_cases}
            reports.append(report)
            if not failures:
                retained.append(tool)
        retained_names = {tool.name for tool in retained}
        retained = [tool for tool in retained if set(tool.requires_tools) <= retained_names]
        return retained, reports
