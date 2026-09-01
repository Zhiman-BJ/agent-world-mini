"""Integration Plan 的 Schema 和跨产物语义校验。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from .filesystem_scopes import structure_definition_issues


@dataclass(frozen=True)
class IntegrationPlanIssue:
    code: str
    path: str
    message: str


_SCALAR_TYPES = {"string", "integer", "number", "boolean"}
_COMMON_FIELD_KEYS = {"type", "description", "nullable"}
_TYPE_KEYS = {
    "string": {"format", "pattern", "minLength", "maxLength", "enum", "const"},
    "integer": {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "enum", "const",
    },
    "number": {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "enum", "const",
    },
    "boolean": {"const"},
    "object": {"properties", "required", "additionalProperties"},
    "array": {"items", "minItems", "maxItems", "uniqueItems"},
}
_STRING_FORMATS = {
    "date", "date-time", "time", "duration", "uri", "uuid", "email",
    "hostname", "ipv4", "ipv6",
}
_CARDINALITY_DESCRIPTION = re.compile(
    r"(?:\b(?:two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|\d+)\s+(?:[\w-]+\s+){0,4}(?:files?|records?|rows?|fixtures?|"
    r"samples?|documents?|members?|rules?|inputs?)\b|"
    r"[零二三四五六七八九十百千万两\d]+\s*(?:个|条|份|组|张)"
    r"(?:[^，。；,.]{0,16})?(?:文件|记录|数据|样本|夹具|文档|成员|规则|输入))",
    re.IGNORECASE,
)


def _pointer(error: Any) -> str:
    value = "$"
    for part in error.absolute_path:
        value += f"[{part}]" if isinstance(part, int) else f".{part}"
    return value


def _schema_issues(payload: dict[str, Any], schema: dict[str, Any]) -> list[IntegrationPlanIssue]:
    validator = Draft202012Validator(schema)
    return [
        IntegrationPlanIssue("integration_plan_schema", _pointer(error), error.message)
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    ]


def _safe_workspace_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _field_issues(
    definition: dict[str, Any],
    *,
    path: str,
    container_depth: int,
    top_level: bool,
) -> list[IntegrationPlanIssue]:
    issues: list[IntegrationPlanIssue] = []
    field_type = definition.get("type")
    allowed = _COMMON_FIELD_KEYS | _TYPE_KEYS.get(str(field_type), set())
    if top_level and (
        field_type == "string"
        or (
            field_type == "array"
            and isinstance(definition.get("items"), dict)
            and definition["items"].get("type") == "string"
        )
    ):
        allowed.add("reference")
    unknown = sorted(set(definition) - allowed)
    if unknown:
        issues.append(IntegrationPlanIssue(
            "field_keyword_not_allowed", path,
            f"{field_type} 字段包含不允许的参数：{unknown}",
        ))
    if "enum" in definition and "const" in definition:
        issues.append(IntegrationPlanIssue(
            "field_enum_const_conflict", path, "enum 和 const 不能同时出现",
        ))
    if field_type == "string":
        format_name = definition.get("format")
        if isinstance(format_name, str) and format_name not in _STRING_FORMATS:
            issues.append(IntegrationPlanIssue(
                "unsupported_string_format", f"{path}.format",
                f"不支持的 string format：{format_name}",
            ))
        pattern = definition.get("pattern")
        if isinstance(pattern, str):
            try:
                re.compile(pattern)
            except re.error as error:
                issues.append(IntegrationPlanIssue(
                    "invalid_field_pattern", f"{path}.pattern", str(error),
                ))
        if (
            isinstance(definition.get("minLength"), int)
            and isinstance(definition.get("maxLength"), int)
            and definition["minLength"] > definition["maxLength"]
        ):
            issues.append(IntegrationPlanIssue(
                "invalid_string_length_bounds", path, "minLength 不能大于 maxLength",
            ))
    if field_type in {"integer", "number"}:
        lower = definition.get("exclusiveMinimum", definition.get("minimum"))
        upper = definition.get("exclusiveMaximum", definition.get("maximum"))
        if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) and lower > upper:
            issues.append(IntegrationPlanIssue(
                "invalid_numeric_bounds", path, "数值下界不能大于上界",
            ))
    expected_python = {
        "string": str, "integer": int, "number": (int, float), "boolean": bool,
        "object": dict, "array": list,
    }.get(str(field_type))
    for keyword in ("enum", "const"):
        values = definition.get(keyword)
        candidates = values if keyword == "enum" and isinstance(values, list) else [values]
        if keyword not in definition or expected_python is None:
            continue
        if any(
            not isinstance(value, expected_python)
            or (field_type in {"integer", "number"} and isinstance(value, bool))
            for value in candidates
        ):
            issues.append(IntegrationPlanIssue(
                "field_literal_type_mismatch", f"{path}.{keyword}",
                f"{keyword} 成员必须符合 type={field_type}",
            ))
    if field_type == "object":
        if definition.get("additionalProperties") is not False:
            issues.append(IntegrationPlanIssue(
                "object_must_be_closed", path, "Object 必须声明 additionalProperties=false",
            ))
        properties = definition.get("properties")
        required = definition.get("required")
        if not isinstance(properties, dict) or not properties:
            issues.append(IntegrationPlanIssue(
                "object_without_properties", path, "Object 必须声明非空 properties",
            ))
        if not isinstance(required, list):
            issues.append(IntegrationPlanIssue(
                "object_without_required", path, "Object 必须声明 required 数组",
            ))
        if isinstance(properties, dict) and isinstance(required, list):
            unknown_required = sorted(set(required) - set(properties))
            if unknown_required:
                issues.append(IntegrationPlanIssue(
                    "object_unknown_required", f"{path}.required",
                    f"required 引用了未知属性：{unknown_required}",
                ))
            for name, child in properties.items():
                if not isinstance(child, dict):
                    continue
                child_type = str(child.get("type") or "")
                next_depth = container_depth + (1 if child_type in {"object", "array"} else 0)
                if next_depth > 2:
                    issues.append(IntegrationPlanIssue(
                        "container_nesting_too_deep", f"{path}.properties.{name}",
                        "Object/Array 容器嵌套最多两层",
                    ))
                issues.extend(_field_issues(
                    child,
                    path=f"{path}.properties.{name}",
                    container_depth=next_depth,
                    top_level=False,
                ))
    elif field_type == "array":
        items = definition.get("items")
        if not isinstance(items, dict):
            issues.append(IntegrationPlanIssue(
                "array_without_items", path, "Array 必须声明 items",
            ))
        if (
            isinstance(definition.get("minItems"), int)
            and isinstance(definition.get("maxItems"), int)
            and definition["minItems"] > definition["maxItems"]
        ):
            issues.append(IntegrationPlanIssue(
                "invalid_array_length_bounds", path, "minItems 不能大于 maxItems",
            ))
        if isinstance(items, dict):
            child_type = str(items.get("type") or "")
            next_depth = container_depth + (1 if child_type in {"object", "array"} else 0)
            if next_depth > 2:
                issues.append(IntegrationPlanIssue(
                    "container_nesting_too_deep", f"{path}.items",
                    "Object/Array 容器嵌套最多两层",
                ))
            issues.extend(_field_issues(
                items,
                path=f"{path}.items",
                container_depth=next_depth,
                top_level=False,
            ))
    if not top_level and "reference" in definition:
        issues.append(IntegrationPlanIssue(
            "nested_file_reference", path, "嵌套字段不能声明文件 reference",
        ))
    return issues


def field_definition_issues(
    definition: dict[str, Any], *, path: str, top_level: bool = True,
) -> list[IntegrationPlanIssue]:
    """供 Integration Plan 与最终 v2 Validator 共用字段语义规则。"""

    field_type = str(definition.get("type") or "")
    return _field_issues(
        definition,
        path=path,
        container_depth=1 if field_type in {"object", "array"} else 0,
        top_level=top_level,
    )


def _description_cardinality_issue(
    value: Any, *, path: str,
) -> IntegrationPlanIssue | None:
    if isinstance(value, str) and _CARDINALITY_DESCRIPTION.search(value):
        return IntegrationPlanIssue(
            "volatile_cardinality_description",
            path,
            "description 不能写当前文件或记录数量；数量由画像从实际状态计算，"
            "这里只说明语义、用途和边界",
        )
    return None


def validate_integration_plan(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    seed_global_id: str,
    seed_sha256: str,
    scenario_research: dict[str, Any],
    source_plan: dict[str, Any],
    source_inventory: dict[str, Any],
) -> list[IntegrationPlanIssue]:
    issues = _schema_issues(payload, schema)
    if payload.get("seed_global_id") != seed_global_id:
        issues.append(IntegrationPlanIssue(
            "integration_seed_id_mismatch", "$.seed_global_id", "与当前 Seed 不一致",
        ))
    if payload.get("seed_sha256") != seed_sha256:
        issues.append(IntegrationPlanIssue(
            "integration_seed_hash_mismatch", "$.seed_sha256", "与当前 Seed 哈希不一致",
        ))

    plan_sources = {
        str(item.get("source_id"))
        for item in source_plan.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    source_by_id = {
        str(item.get("source_id")): item
        for item in source_plan.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    inventory_source_by_id = {
        str(item.get("source_id")): item
        for item in source_inventory.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    inventoried_paths = {
        str(item.get("path"))
        for item in source_inventory.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    path_source = {
        str(item.get("path")): str(item.get("source_id"))
        for item in source_inventory.get("files", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("source_id"), str)
    }
    source_decisions = [
        item for item in payload.get("source_decisions", []) if isinstance(item, dict)
    ]
    source_decision_by_id = {
        str(item.get("source_id")): str(item.get("decision"))
        for item in source_decisions
    }
    decision_ids = [str(item.get("source_id")) for item in source_decisions]
    if len(decision_ids) != len(set(decision_ids)):
        issues.append(IntegrationPlanIssue(
            "duplicate_source_decision", "$.source_decisions", "同一来源只能有一个集成决策",
        ))
    missing_decisions = sorted(plan_sources - set(decision_ids))
    unknown_decisions = sorted(set(decision_ids) - plan_sources)
    if missing_decisions:
        issues.append(IntegrationPlanIssue(
            "missing_source_decision", "$.source_decisions",
            f"缺少来源决策：{missing_decisions}",
        ))
    if unknown_decisions:
        issues.append(IntegrationPlanIssue(
            "unknown_source_decision", "$.source_decisions",
            f"引用了未知来源：{unknown_decisions}",
        ))
    for index, decision in enumerate(source_decisions):
        if decision.get("decision") not in {"core", "supporting"}:
            continue
        source_id = str(decision.get("source_id"))
        source = source_by_id.get(source_id, {})
        inventory_source = inventory_source_by_id.get(source_id, {})
        if source.get("status") != "complete":
            issues.append(IntegrationPlanIssue(
                "selected_source_not_complete", f"$.source_decisions[{index}]",
                f"只有 status=complete 的来源可以进入最终资产：{source_id}",
            ))
        if inventory_source.get("profile_status") not in {"usable", "partial"}:
            issues.append(IntegrationPlanIssue(
                "selected_source_not_usable", f"$.source_decisions[{index}]",
                f"来源没有可用真实内容画像：{source_id}",
            ))

    record_sets = [item for item in payload.get("record_sets", []) if isinstance(item, dict)]
    scopes = [item for item in payload.get("filesystem_scopes", []) if isinstance(item, dict)]
    relationships = [item for item in payload.get("relationships", []) if isinstance(item, dict)]
    record_ids = [str(item.get("record_set_id")) for item in record_sets]
    scope_ids = [str(item.get("scope_id")) for item in scopes]
    relation_ids = [str(item.get("relationship_id")) for item in relationships]
    for label, values, path in (
        ("Record Set", record_ids, "$.record_sets"),
        ("Filesystem Scope", scope_ids, "$.filesystem_scopes"),
        ("Relationship", relation_ids, "$.relationships"),
    ):
        if len(values) != len(set(values)):
            issues.append(IntegrationPlanIssue(
                "duplicate_integration_id", path, f"{label} ID 不能重复",
            ))
    record_map = {str(item.get("record_set_id")): item for item in record_sets}
    scope_map = {str(item.get("scope_id")): item for item in scopes}

    for collection_name, assets in (("record_sets", record_sets), ("filesystem_scopes", scopes)):
        for index, asset in enumerate(assets):
            issue = _description_cardinality_issue(
                asset.get("description"), path=f"$.{collection_name}[{index}].description",
            )
            if issue is not None:
                issues.append(issue)

    for index, scope in enumerate(scopes):
        structure = scope.get("structure")
        if isinstance(structure, dict):
            issues.extend(
                IntegrationPlanIssue(item.code, f"$.filesystem_scopes[{index}].{item.path}", item.message)
                for item in structure_definition_issues(structure)
            )

    for index, asset in enumerate([*record_sets, *scopes]):
        kind = "record_sets" if "record_set_id" in asset else "filesystem_scopes"
        pointer = f"$.{kind}[{index if kind == 'record_sets' else index - len(record_sets)}]"
        unknown_sources = sorted(set(asset.get("source_ids", [])) - plan_sources)
        unselected_sources = sorted(
            source_id for source_id in set(asset.get("source_ids", []))
            if source_decision_by_id.get(str(source_id)) not in {"core", "supporting"}
        )
        unknown_paths = sorted(set(asset.get("source_paths", [])) - inventoried_paths)
        if unknown_sources:
            issues.append(IntegrationPlanIssue(
                "asset_unknown_source", f"{pointer}.source_ids",
                f"引用了未知来源：{unknown_sources}",
            ))
        if unselected_sources:
            issues.append(IntegrationPlanIssue(
                "asset_uses_unselected_source", f"{pointer}.source_ids",
                "最终资产只能使用 decision=core/supporting 的来源；"
                f"evidence_only/rejected 只保留溯源证据：{unselected_sources}",
            ))
        if unknown_paths:
            issues.append(IntegrationPlanIssue(
                "asset_unknown_source_path", f"{pointer}.source_paths",
                f"引用了未画像 Raw：{unknown_paths}",
            ))
        for source_path in asset.get("source_paths", []):
            if isinstance(source_path, str) and not _safe_workspace_path(source_path):
                issues.append(IntegrationPlanIssue(
                    "unsafe_source_path", f"{pointer}.source_paths", f"不安全路径：{source_path}",
                ))
            if (
                isinstance(source_path, str)
                and source_path in path_source
                and path_source[source_path] not in set(asset.get("source_ids", []))
            ):
                issues.append(IntegrationPlanIssue(
                    "asset_source_path_mismatch", f"{pointer}.source_paths",
                    f"Raw {source_path} 属于 {path_source[source_path]}，"
                    "但资产 source_ids 没有声明该来源",
                ))
        if asset.get("importance") == "core" and not asset.get("source_paths"):
            issues.append(IntegrationPlanIssue(
                "core_asset_without_source", pointer, "核心资产必须引用至少一个已画像 Raw 文件",
            ))
        if asset.get("standalone_reason") is None and kind == "filesystem_scopes" and not record_sets:
            # 纯文件环境本身不需要额外解释为什么独立。
            pass

    for index, record_set in enumerate(record_sets):
        fields = record_set.get("fields", {})
        if not isinstance(fields, dict):
            continue
        for field_name, definition in fields.items():
            if not isinstance(definition, dict):
                continue
            issues.extend(field_definition_issues(
                definition, path=f"$.record_sets[{index}].fields.{field_name}",
            ))
            reference = definition.get("reference")
            if isinstance(reference, dict) and reference.get("scope_id") not in scope_map:
                issues.append(IntegrationPlanIssue(
                    "unknown_file_scope",
                    f"$.record_sets[{index}].fields.{field_name}.reference.scope_id",
                    "文件引用的 Scope 不存在",
                ))
        for key_field in record_set.get("key_fields", []):
            definition = fields.get(key_field)
            if not isinstance(definition, dict):
                issues.append(IntegrationPlanIssue(
                    "unknown_key_field", f"$.record_sets[{index}].key_fields",
                    f"键字段不存在：{key_field}",
                ))
            elif definition.get("type") not in _SCALAR_TYPES or definition.get("nullable") is not False:
                issues.append(IntegrationPlanIssue(
                    "invalid_key_field", f"$.record_sets[{index}].key_fields",
                    f"键字段必须是 nullable=false 的标量：{key_field}",
                ))

    for index, relationship in enumerate(relationships):
        for endpoint_name in ("from", "to"):
            endpoint = relationship.get(endpoint_name, {})
            record_id = endpoint.get("record_set_id") if isinstance(endpoint, dict) else None
            record = record_map.get(str(record_id))
            if record is None:
                issues.append(IntegrationPlanIssue(
                    "unknown_relationship_record_set",
                    f"$.relationships[{index}].{endpoint_name}.record_set_id",
                    f"关系端点不存在：{record_id}",
                ))
                continue
            fields = record.get("fields", {})
            for field_name in endpoint.get("fields", []):
                definition = fields.get(field_name) if isinstance(fields, dict) else None
                if not isinstance(definition, dict) or definition.get("type") not in _SCALAR_TYPES:
                    issues.append(IntegrationPlanIssue(
                        "invalid_relationship_field",
                        f"$.relationships[{index}].{endpoint_name}.fields",
                        f"关系端点必须引用顶层标量字段：{record_id}.{field_name}",
                    ))

    research_needs = {
        str(item.get("need_id"))
        for item in scenario_research.get("data_needs", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    bindings = [item for item in payload.get("need_bindings", []) if isinstance(item, dict)]
    source_need_statuses = {
        str(item.get("need_id")): str(item.get("status"))
        for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    source_need_sources = {
        str(item.get("need_id")): {
            str(value) for value in item.get("source_ids", [])
            if isinstance(value, str)
        }
        for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict) and isinstance(item.get("need_id"), str)
    }
    asset_sources = {
        str(item.get("record_set_id") or item.get("scope_id")): {
            str(value) for value in item.get("source_ids", [])
            if isinstance(value, str)
        }
        for item in [*record_sets, *scopes]
    }
    expected_binding_status = {
        "supported": "realized",
        "partial": "partial",
        "blocked": "unavailable",
        "unavailable": "unavailable",
        "not_applicable": "not_applicable",
    }
    binding_ids = [str(item.get("need_id")) for item in bindings]
    if len(binding_ids) != len(set(binding_ids)):
        issues.append(IntegrationPlanIssue(
            "duplicate_need_binding", "$.need_bindings", "每个数据需求只能绑定一次",
        ))
    if set(binding_ids) != research_needs:
        issues.append(IntegrationPlanIssue(
            "incomplete_need_bindings", "$.need_bindings",
            f"需求绑定必须与 Step 1 一致；缺少 {sorted(research_needs - set(binding_ids))}，"
            f"多出 {sorted(set(binding_ids) - research_needs)}",
        ))
    for index, binding in enumerate(bindings):
        description_issue = _description_cardinality_issue(
            binding.get("description"), path=f"$.need_bindings[{index}].description",
        )
        if description_issue is not None:
            issues.append(description_issue)
        unknown_records = sorted(set(binding.get("record_set_ids", [])) - set(record_ids))
        unknown_scopes = sorted(set(binding.get("scope_ids", [])) - set(scope_ids))
        if unknown_records or unknown_scopes:
            issues.append(IntegrationPlanIssue(
                "unknown_need_asset", f"$.need_bindings[{index}]",
                f"需求绑定引用了未知资产：record_sets={unknown_records}, scopes={unknown_scopes}",
            ))
        has_assets = bool(binding.get("record_set_ids") or binding.get("scope_ids"))
        status = binding.get("status")
        need_id = str(binding.get("need_id"))
        source_status = source_need_statuses.get(need_id)
        expected_status = expected_binding_status.get(str(source_status))
        if expected_status is None:
            issues.append(IntegrationPlanIssue(
                "unresolved_source_need", f"$.need_bindings[{index}]",
                f"需求在 source_plan 中尚未收口：{source_status}",
            ))
        elif status != expected_status:
            issues.append(IntegrationPlanIssue(
                "need_status_mismatch", f"$.need_bindings[{index}].status",
                f"需求状态必须依据 source_plan 映射为 {expected_status}，"
                f"实际为 {status}",
            ))
        if status in {"realized", "partial"} and not has_assets:
            issues.append(IntegrationPlanIssue(
                "unbound_data_need", f"$.need_bindings[{index}]",
                f"{status} 需求必须绑定至少一个最终资产",
            ))
        if status in {"realized", "partial"} and has_assets:
            bound_ids = {
                *(str(value) for value in binding.get("record_set_ids", [])),
                *(str(value) for value in binding.get("scope_ids", [])),
            }
            bound_sources = set().union(
                *(asset_sources.get(asset_id, set()) for asset_id in bound_ids)
            ) if bound_ids else set()
            evidence_sources = source_need_sources.get(need_id, set())
            if not evidence_sources or not bound_sources.intersection(evidence_sources):
                issues.append(IntegrationPlanIssue(
                    "need_binding_without_source_lineage",
                    f"$.need_bindings[{index}]",
                    "需求绑定的资产没有使用 Step 2 为该需求登记的证据来源；"
                    f"need_sources={sorted(evidence_sources)}, "
                    f"asset_sources={sorted(bound_sources)}",
                ))
        if status in {"unavailable", "not_applicable"} and has_assets:
            issues.append(IntegrationPlanIssue(
                "closed_need_with_assets", f"$.need_bindings[{index}]",
                f"{status} 需求不能绑定最终资产；有部分真实支持时应使用 partial",
            ))
    return issues


def load_and_validate_integration_plan(
    path: Path,
    *,
    schema_path: Path,
    seed_global_id: str,
    seed_sha256: str,
    scenario_research: dict[str, Any],
    source_plan: dict[str, Any],
    source_inventory: dict[str, Any],
) -> tuple[dict[str, Any], list[IntegrationPlanIssue]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, [IntegrationPlanIssue("invalid_integration_plan_json", "$", str(error))]
    if not isinstance(payload, dict):
        return {}, [IntegrationPlanIssue("invalid_integration_plan", "$", "根节点必须是对象")]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return payload, validate_integration_plan(
        payload,
        schema=schema,
        seed_global_id=seed_global_id,
        seed_sha256=seed_sha256,
        scenario_research=scenario_research,
        source_plan=source_plan,
        source_inventory=source_inventory,
    )


__all__ = [
    "IntegrationPlanIssue",
    "field_definition_issues",
    "load_and_validate_integration_plan",
    "validate_integration_plan",
]
