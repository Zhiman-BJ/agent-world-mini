"""基于实际 SQLite 和 Filesystem Scope 计算环境集成度。"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path
import re
from typing import Any

from .filesystem_scopes import permitted_invalid_files, validate_scope_tree


_DISPLAY_VALUE_LIMIT = 160
_FIELD_SAMPLE_LIMIT = 5
_TOP_VALUE_LIMIT = 8
_CATEGORICAL_FIELD_NAME = re.compile(
    r"(?:^|_)(?:category|class|delivery|format|kind|method|mode|role|severity|state|status|type|visibility)$"
)


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _gap(code: str, message: str, action: str, asset_ids: list[str]) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "action": action,
        "asset_ids": sorted(set(asset_ids)),
    }


def _table_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(f"SELECT * FROM {_quote(table)}")]


def _key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tuple_key(record: dict[str, Any], fields: list[str]) -> tuple[str, ...]:
    return tuple(_key(record.get(field)) for field in fields)


def _relationship_profile(
    connection: sqlite3.Connection,
    relationships: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    profiles: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []
    for relationship in relationships:
        relation_id = str(relationship["relationship_id"])
        source_id = str(relationship["from"]["record_set_id"])
        target_id = str(relationship["to"]["record_set_id"])
        source_fields = list(relationship["from"]["fields"])
        target_fields = list(relationship["to"]["fields"])
        source_rows = _table_rows(connection, source_id)
        target_rows = _table_rows(connection, target_id)
        target_keys = [_tuple_key(row, target_fields) for row in target_rows]
        target_non_null = [key for key in target_keys if all(value != "null" for value in key)]
        target_unique = len(target_non_null) == len(set(target_non_null)) == len(target_rows)
        target_set = set(target_non_null)
        source_keys = [_tuple_key(row, source_fields) for row in source_rows]
        partially_null = sum(
            any(value == "null" for value in key) and not all(value == "null" for value in key)
            for key in source_keys
        )
        non_null_source = [key for key in source_keys if all(value != "null" for value in key)]
        missing = [key for key in non_null_source if key not in target_set]
        source_unique = len(non_null_source) == len(set(non_null_source))
        structurally_valid = (
            target_unique
            and not partially_null
            and not missing
            and (
                relationship.get("cardinality") != "one_to_one"
                or source_unique
            )
        )
        matched = len(non_null_source) - len(missing)
        realized = structurally_valid and matched > 0
        valid = structurally_valid and realized
        profiles.append({
            "relationship_id": relation_id,
            "from_record_set_id": source_id,
            "to_record_set_id": target_id,
            "source_non_null_count": len(non_null_source),
            "target_record_count": len(target_rows),
            "missing_reference_count": len(missing),
            "matched_reference_count": matched,
            "partially_null_reference_count": partially_null,
            "target_unique": target_unique,
            "source_unique": source_unique,
            "structurally_valid": structurally_valid,
            "realized": realized,
            "valid": valid,
        })
        if not structurally_valid:
            gaps.append(_gap(
                "invalid_relationship",
                f"关系 {relation_id} 未闭合或不满足声明基数。",
                "修正字段映射、补齐目标记录或删除没有真实依据的关系。",
                [source_id, target_id],
            ))
        elif not realized:
            gaps.append(_gap(
                "empty_relationship",
                f"关系 {relation_id} 没有任何非空且命中目标的实际引用。",
                "补采真实关联记录，或删除只存在于计划中的装饰性关系。",
                [source_id, target_id],
            ))
        else:
            edges.append((f"record:{source_id}", f"record:{target_id}"))
    return profiles, gaps, edges


def _safe_scope_path(root: Path, value: str) -> Path | None:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts or "." in relative.parts:
        return None
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _decode_value(value: Any, definition: dict[str, Any]) -> Any:
    if value is None:
        return None
    field_type = definition.get("type")
    if field_type in {"array", "object"} and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if field_type == "boolean" and value in {0, 1}:
        return bool(value)
    return value


def _display_value(value: Any) -> str:
    """Return a bounded, deterministic representation suitable for profile review."""

    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if len(rendered) <= _DISPLAY_VALUE_LIMIT:
        return rendered
    return rendered[: _DISPLAY_VALUE_LIMIT - 3] + "..."


def _sample_values(values: list[Any]) -> list[str]:
    """Sample across source order instead of showing only the first few records."""

    if not values:
        return []
    last = len(values) - 1
    positions = [0, last // 4, last // 2, (last * 3) // 4, last]
    samples: list[str] = []
    seen: set[str] = set()
    for position in positions:
        rendered = _display_value(values[position])
        if rendered in seen:
            continue
        seen.add(rendered)
        samples.append(rendered)
        if len(samples) == _FIELD_SAMPLE_LIMIT:
            break
    return samples


def _value_shape(values: list[Any], field_type: str) -> dict[str, Any]:
    if not values:
        return {}
    if field_type == "string":
        lengths = [len(value) for value in values if isinstance(value, str)]
        return {
            "min_length": min(lengths),
            "max_length": max(lengths),
            "average_length": round(sum(lengths) / len(lengths), 2),
        } if lengths else {}
    if field_type in {"integer", "number"}:
        numbers = [
            value for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        return {"minimum": min(numbers), "maximum": max(numbers)} if numbers else {}
    if field_type == "array":
        lengths = [len(value) for value in values if isinstance(value, list)]
        return {
            "min_items": min(lengths),
            "max_items": max(lengths),
            "average_items": round(sum(lengths) / len(lengths), 2),
        } if lengths else {}
    if field_type == "object":
        key_counts = Counter(
            str(key)
            for value in values if isinstance(value, dict)
            for key in value
        )
        return {
            "observed_keys": [
                {"key": key, "count": count}
                for key, count in sorted(
                    key_counts.items(), key=lambda item: (-item[1], item[0])
                )[:_TOP_VALUE_LIMIT]
            ]
        }
    return {}


def _field_review_findings(
    record_set_id: str,
    fields: dict[str, dict[str, Any]],
    domains: dict[str, set[str]],
    key_fields: set[str],
) -> list[dict[str, Any]]:
    """Produce high-signal review prompts without claiming semantic invalidity."""

    findings: list[dict[str, Any]] = []
    categorical: list[str] = []
    for field_name, profile in fields.items():
        populated = int(profile["populated_count"])
        distinct = int(profile["distinct_count"])
        if (
            field_name not in key_fields
            and profile.get("type") == "string"
            and populated >= 30
            and distinct == 1
            and _CATEGORICAL_FIELD_NAME.search(field_name)
        ):
            top_values = profile.get("top_values", [])
            value = str(top_values[0].get("value")) if top_values else ""
            findings.append({
                "finding_id": f"{record_set_id}:constant_value:{field_name}",
                "code": "constant_categorical_value",
                "record_set_id": record_set_id,
                "fields": [field_name],
                "message": (
                    f"{record_set_id}.{field_name} 的 {populated} 个非空值全部为 {value!r}。"
                    "这可能是目标范围内的真实常量，也可能是解析器默认分支覆盖了整列。"
                ),
                "action": "对照不同位置和不同结构分支的 Raw，确认该列确实只有一个业务类别。",
            })
        if (
            field_name in key_fields
            or profile.get("type") != "string"
            or populated < 30
            or distinct < 2
            or distinct > 20
        ):
            continue
        categorical.append(field_name)
        top_values = profile.get("top_values", [])
        dominant = int(top_values[0]["count"]) if top_values else 0
        if dominant / populated >= 0.9:
            findings.append({
                "finding_id": f"{record_set_id}:dominant_value:{field_name}",
                "code": "dominant_categorical_value",
                "record_set_id": record_set_id,
                "fields": [field_name],
                "message": (
                    f"{record_set_id}.{field_name} 的首位值占 "
                    f"{round(dominant * 100 / populated, 2)}%，可能是真实偏斜，也可能是抽取分支覆盖错误。"
                ),
                "action": "对照不同位置的 Raw 样本；若来源定义了闭合值域，在字段定义中声明 enum。",
            })

    for index, left in enumerate(sorted(categorical)):
        for right in sorted(categorical)[index + 1:]:
            intersection = domains[left].intersection(domains[right])
            smaller = min(len(domains[left]), len(domains[right]))
            if len(intersection) < 2 or len(intersection) / smaller < 0.4:
                continue
            findings.append({
                "finding_id": f"{record_set_id}:overlapping_domains:{left}:{right}",
                "code": "overlapping_categorical_domains",
                "record_set_id": record_set_id,
                "fields": [left, right],
                "message": (
                    f"{record_set_id}.{left} 与 {right} 共享多个高频类别值："
                    + ", ".join(sorted(_display_value(value) for value in intersection)[:5])
                    + "。这可能合理，也可能表示解析结果写入了错误列。"
                ),
                "action": "抽查共享值对应的完整记录和 Raw，确认两个字段的业务边界没有串列。",
            })
    return findings


def _file_reference_profile(
    connection: sqlite3.Connection,
    record_sets: list[dict[str, Any]],
    scopes_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    profiles: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []
    for record_set in record_sets:
        record_set_id = str(record_set["record_set_id"])
        rows = _table_rows(connection, record_set_id)
        for field_name, definition in record_set.get("fields", {}).items():
            if not isinstance(definition, dict) or not isinstance(definition.get("reference"), dict):
                continue
            reference = definition["reference"]
            scope_id = str(reference["scope_id"])
            target_kind = str(reference["target"])
            scope_root = scopes_root / scope_id
            checked = 0
            missing = 0
            invalid = 0
            for row in rows:
                value = _decode_value(row.get(field_name), definition)
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if item is None:
                        continue
                    checked += 1
                    target = _safe_scope_path(scope_root, str(item))
                    if target is None:
                        invalid += 1
                    elif not target.exists():
                        missing += 1
                    elif target_kind == "file" and not target.is_file():
                        invalid += 1
                    elif target_kind == "directory" and not target.is_dir():
                        invalid += 1
            structurally_valid = missing == 0 and invalid == 0
            resolved = checked - missing - invalid
            realized = structurally_valid and resolved > 0
            valid = structurally_valid and realized
            profiles.append({
                "record_set_id": record_set_id,
                "field": field_name,
                "scope_id": scope_id,
                "checked_path_count": checked,
                "resolved_path_count": resolved,
                "missing_path_count": missing,
                "invalid_path_count": invalid,
                "structurally_valid": structurally_valid,
                "realized": realized,
                "valid": valid,
            })
            if not structurally_valid:
                gaps.append(_gap(
                    "invalid_file_reference",
                    f"{record_set_id}.{field_name} 有 {missing} 个缺失路径和 {invalid} 个非法路径。",
                    "重新物化 Scope 或修正 Record 中的 Scope 相对路径。",
                    [record_set_id, scope_id],
                ))
            elif not realized:
                gaps.append(_gap(
                    "empty_file_reference",
                    f"{record_set_id}.{field_name} 没有任何非空且可解析的实际路径。",
                    "补充真实 Scope 相对路径，或删除没有实际用途的文件引用声明。",
                    [record_set_id, scope_id],
                ))
            else:
                edges.append((f"record:{record_set_id}", f"scope:{scope_id}"))
    return profiles, gaps, edges


def _record_field_profile(
    connection: sqlite3.Connection,
    record_set: dict[str, Any],
) -> dict[str, Any]:
    """Compute bounded field facts for both policy checks and semantic review."""

    record_set_id = str(record_set["record_set_id"])
    rows = _table_rows(connection, record_set_id)
    fields: dict[str, dict[str, Any]] = {}
    domains: dict[str, set[str]] = {}
    for field_name, definition in record_set.get("fields", {}).items():
        definition = definition if isinstance(definition, dict) else {}
        values = [_decode_value(row.get(field_name), definition) for row in rows]
        populated = [value for value in values if value not in (None, "", [], {})]
        counts = Counter(_key(value) for value in populated)
        values_by_key = {_key(value): value for value in populated}
        ordered_counts = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        domains[str(field_name)] = set(counts)
        fields[str(field_name)] = {
            "type": str(definition.get("type") or "unknown"),
            "populated_count": len(populated),
            "null_count": sum(value is None for value in values),
            "empty_value_count": sum(value in ("", [], {}) for value in values),
            "populated_percent": round(len(populated) * 100 / len(rows), 2) if rows else 0.0,
            "distinct_count": len(counts),
            "top_values": [
                {
                    "value": _display_value(values_by_key[key]),
                    "count": count,
                    "percent_of_populated": round(count * 100 / len(populated), 2),
                }
                for key, count in ordered_counts[:_TOP_VALUE_LIMIT]
            ],
            "sample_values": _sample_values(populated),
            "value_shape": _value_shape(
                populated, str(definition.get("type") or "unknown")
            ),
        }
    review_findings = _field_review_findings(
        record_set_id,
        fields,
        domains,
        {str(value) for value in record_set.get("key_fields", [])},
    )
    return {
        "record_set_id": record_set_id,
        "record_count": len(rows),
        "fields": fields,
        "review_findings": review_findings,
    }


def _components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for left, right in edges:
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)
    unseen = set(nodes)
    components: list[list[str]] = []
    while unseen:
        start = min(unseen)
        queue: deque[str] = deque([start])
        unseen.remove(start)
        current: list[str] = []
        while queue:
            node = queue.popleft()
            current.append(node)
            for neighbor in sorted(adjacency[node]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(current))
    return sorted(components, key=lambda item: (-len(item), item))


def _broad_flat_scope(scope: dict[str, Any]) -> bool:
    """Return whether a multi-source directory was flattened into a catch-all workspace."""

    source_ids = {str(value) for value in scope.get("source_ids", [])}
    source_paths = [str(value) for value in scope.get("source_paths", [])]
    structure = scope.get("structure", {})
    if (
        len(source_ids) < 3
        or len(source_paths) < 8
        or not isinstance(structure, dict)
        or structure.get("kind") != "directory"
    ):
        return False
    layout = [item for item in structure.get("layout", []) if isinstance(item, dict)]
    if len(layout) < 3:
        return False
    return not any(
        item.get("kind") in {"directory", "directory_collection"}
        for item in layout
    )


def build_integration_profile(
    run_dir: Path,
    *,
    plan: dict[str, Any],
    seed_global_id: str,
    seed_sha256: str,
) -> dict[str, Any]:
    """检查实际状态是否形成可解释、闭合且有来源依据的环境。"""

    run_dir = run_dir.resolve()
    database_path = run_dir / "state/records.sqlite"
    scopes_root = run_dir / "state/filesystem_scopes"
    record_sets = [item for item in plan.get("record_sets", []) if isinstance(item, dict)]
    scopes = [item for item in plan.get("filesystem_scopes", []) if isinstance(item, dict)]
    relationships = [item for item in plan.get("relationships", []) if isinstance(item, dict)]
    gaps: list[dict[str, Any]] = []
    record_counts: dict[str, int] = {}
    record_set_profiles: list[dict[str, Any]] = []
    connection: sqlite3.Connection | None = None
    try:
        tables: set[str] = set()
        if database_path.is_file():
            connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        declared_record_sets = {
            str(item["record_set_id"]) for item in record_sets
        }
        undeclared_record_tables = sorted(tables - declared_record_sets)
        if undeclared_record_tables:
            gaps.append(_gap(
                "undeclared_record_tables",
                "records.sqlite 中存在当前 integration plan 未声明的遗留业务表："
                + ", ".join(undeclared_record_tables),
                "删除旧候选状态或重新建立干净的 records.sqlite，再物化当前计划。",
                undeclared_record_tables,
            ))
        if record_sets:
            if connection is None:
                gaps.append(_gap(
                    "missing_records_database", "计划包含 Record Set，但 records.sqlite 不存在。",
                    "通过受控命令物化所有 Record Set。",
                    [str(item["record_set_id"]) for item in record_sets],
                ))
            else:
                for item in record_sets:
                    record_set_id = str(item["record_set_id"])
                    if record_set_id not in tables:
                        record_counts[record_set_id] = 0
                        gaps.append(_gap(
                            "missing_record_set", f"缺少 Record Set 表 {record_set_id}。",
                            "按 integration_plan 物化该 Record Set。", [record_set_id],
                        ))
                    else:
                        record_counts[record_set_id] = int(connection.execute(
                            f"SELECT COUNT(*) FROM {_quote(record_set_id)}"
                        ).fetchone()[0])
                        if record_counts[record_set_id] == 0:
                            gaps.append(_gap(
                                "empty_record_set", f"Record Set {record_set_id} 没有记录。",
                                "补采真实数据或从计划中移除装饰性空集合。", [record_set_id],
                            ))
                        record_set_profiles.append(_record_field_profile(connection, item))
        scope_file_counts: dict[str, int] = {}
        scope_profiles: list[dict[str, Any]] = []
        declared_scope_ids = {str(item["scope_id"]) for item in scopes}
        actual_scope_ids = {
            path.name for path in scopes_root.iterdir() if path.is_dir()
        } if scopes_root.is_dir() else set()
        undeclared_scope_directories = sorted(actual_scope_ids - declared_scope_ids)
        if undeclared_scope_directories:
            gaps.append(_gap(
                "undeclared_scope_directories",
                "state/filesystem_scopes 中存在当前 integration plan 未声明的遗留目录："
                + ", ".join(undeclared_scope_directories),
                "删除旧候选 Scope，或在有真实用途时把它重新纳入当前计划并物化。",
                undeclared_scope_directories,
            ))
        for item in scopes:
            scope_id = str(item["scope_id"])
            root = scopes_root / scope_id
            count = sum(path.is_file() for path in root.rglob("*")) if root.is_dir() else 0
            scope_file_counts[scope_id] = count
            if count == 0:
                gaps.append(_gap(
                    "empty_or_missing_scope", f"Filesystem Scope {scope_id} 不存在或没有文件。",
                    "从已画像 Raw 复制、解包或转换实际文件。", [scope_id],
                ))
            structure = item.get("structure")
            scope_issues = (
                validate_scope_tree(
                    root, structure,
                    pointer=f"filesystem_scopes.{scope_id}.structure",
                )
                if isinstance(structure, dict) else []
            )
            scope_profiles.append({
                "scope_id": scope_id,
                "file_count": count,
                "valid": not scope_issues,
                "permitted_invalid_files": permitted_invalid_files(root, structure)
                if isinstance(structure, dict) else [],
                "issues": [
                    {"code": issue.code, "path": issue.path, "message": issue.message}
                    for issue in scope_issues
                ],
            })
            if scope_issues:
                gaps.append(_gap(
                    "invalid_scope_structure",
                    f"Filesystem Scope {scope_id} 不满足声明层级："
                    + "；".join(issue.message for issue in scope_issues[:4]),
                    "修正 Scope 物化或 structure 模板，再重建该 Scope。",
                    [scope_id],
                ))
            if _broad_flat_scope(item):
                gaps.append(_gap(
                    "broad_flat_filesystem_scope",
                    f"Filesystem Scope {scope_id} 把多个来源和多类文件平铺为一个混合工作区。",
                    "按实际操作上下文拆分 Scope，或保留来源自带的目录层级；不要建立 all-files 汇总目录。",
                    [scope_id],
                ))

        relation_profiles: list[dict[str, Any]] = []
        relation_gaps: list[dict[str, Any]] = []
        relation_edges: list[tuple[str, str]] = []
        file_profiles: list[dict[str, Any]] = []
        file_gaps: list[dict[str, Any]] = []
        file_edges: list[tuple[str, str]] = []
        if connection is not None and not any(item["code"] == "missing_record_set" for item in gaps):
            relation_profiles, relation_gaps, relation_edges = _relationship_profile(
                connection, relationships,
            )
            file_profiles, file_gaps, file_edges = _file_reference_profile(
                connection, record_sets, scopes_root,
            )
            gaps.extend(relation_gaps)
            gaps.extend(file_gaps)

        nodes = [f"record:{item['record_set_id']}" for item in record_sets] + [
            f"scope:{item['scope_id']}" for item in scopes
        ]
        edges = list(dict.fromkeys([*relation_edges, *file_edges]))
        components = _components(nodes, edges)
        asset_by_node = {
            **{f"record:{item['record_set_id']}": item for item in record_sets},
            **{f"scope:{item['scope_id']}": item for item in scopes},
        }
        isolated = [component[0] for component in components if len(component) == 1 and len(nodes) > 1]
        unjustified = [
            node for node in isolated
            if not str(asset_by_node[node].get("standalone_reason") or "").strip()
        ]
        if unjustified:
            gaps.append(_gap(
                "unjustified_isolated_assets",
                "存在没有关系、文件引用或独立业务理由的数据孤岛。",
                "建立真实关系/文件引用，合并重复集合，或给确实独立的资产写明 standalone_reason。",
                [node.split(":", 1)[1] for node in unjustified],
            ))

        bindings = [item for item in plan.get("need_bindings", []) if isinstance(item, dict)]
        active_bindings = [
            item for item in bindings if item.get("status") in {"realized", "partial"}
        ]
        bound_assets = {
            *(
                str(value)
                for item in active_bindings
                for value in item.get("record_set_ids", [])
            ),
            *(
                str(value)
                for item in active_bindings
                for value in item.get("scope_ids", [])
            ),
        }
        all_assets = [
            str(item.get("record_set_id") or item.get("scope_id"))
            for item in [*record_sets, *scopes]
        ]
        core_assets = [
            str(item.get("record_set_id") or item.get("scope_id"))
            for item in [*record_sets, *scopes]
            if item.get("importance") == "core"
        ]
        unbound_core = sorted(set(core_assets) - bound_assets)
        if unbound_core:
            gaps.append(_gap(
                "core_assets_without_need",
                "核心资产没有绑定任何 Step 1 数据需求。",
                "补充 need_bindings 或移除与场景无关的核心资产。", unbound_core,
            ))
        unbound_assets = sorted(set(all_assets) - bound_assets)
        if unbound_assets:
            gaps.append(_gap(
                "assets_without_data_need",
                "最终资产没有服务任何已实现或部分实现的数据需求。",
                "将真实资产绑定到需求，或从最终环境移除装饰性数据。", unbound_assets,
            ))

        decisions = {
            str(item.get("source_id")): str(item.get("decision"))
            for item in plan.get("source_decisions", [])
            if isinstance(item, dict)
        }
        asset_sources = {
            str(item.get("record_set_id") or item.get("scope_id")): {
                str(value) for value in item.get("source_ids", [])
            }
            for item in [*record_sets, *scopes]
        }
        used_sources = set().union(*asset_sources.values()) if asset_sources else set()
        expected_sources = {
            source_id for source_id, decision in decisions.items()
            if decision in {"core", "supporting"}
        }
        unused_sources = sorted(expected_sources - used_sources)
        if unused_sources:
            gaps.append(_gap(
                "selected_sources_not_integrated",
                "被选为 core/supporting 的来源没有进入任何最终资产。",
                "将来源接入资产，或把决策改为 evidence_only/rejected。", unused_sources,
            ))
        cross_source_edges = 0
        for left, right in edges:
            left_sources = asset_sources.get(left.split(":", 1)[1], set())
            right_sources = asset_sources.get(right.split(":", 1)[1], set())
            if left_sources and right_sources and left_sources != right_sources:
                cross_source_edges += 1
        multi_source_assets = sorted(
            asset_id for asset_id, sources in asset_sources.items() if len(sources) > 1
        )
        if len(used_sources) > 1 and not multi_source_assets and cross_source_edges == 0:
            gaps.append(_gap(
                "multiple_sources_without_integration",
                "环境使用了多个来源，但没有多源合并资产或跨来源连接。",
                "合并同一业务概念，建立真实跨源关系/文件引用，或移除无关来源。",
                sorted(used_sources),
            ))

        scopes_by_source_path: dict[str, list[str]] = defaultdict(list)
        for scope in scopes:
            scope_id = str(scope["scope_id"])
            for source_path in scope.get("source_paths", []):
                scopes_by_source_path[str(source_path)].append(scope_id)
        duplicate_scope_sources = {
            source_path: sorted(set(scope_ids))
            for source_path, scope_ids in scopes_by_source_path.items()
            if len(set(scope_ids)) > 1
        }
        if duplicate_scope_sources:
            affected = sorted({
                scope_id
                for scope_ids in duplicate_scope_sources.values()
                for scope_id in scope_ids
            })
            examples = "; ".join(
                f"{path} -> {','.join(scope_ids)}"
                for path, scope_ids in sorted(duplicate_scope_sources.items())[:4]
            )
            gaps.append(_gap(
                "duplicate_scope_source_paths",
                "同一 Raw 被重复物化到多个最终 Filesystem Scope：" + examples,
                "让原始文件只进入一个职责清晰的 Scope，并让相关 Record 统一引用该 Scope。",
                affected,
            ))

        return {
            "schema_version": "1.0",
            "seed_global_id": seed_global_id,
            "seed_sha256": seed_sha256,
            "integration_tier": "integrated" if not gaps else "fragmented",
            "summary": (
                "所有最终资产均已物化，关系和路径闭合，多源数据形成可解释连接。"
                if not gaps else
                "环境候选仍存在未物化、未闭合或多源割裂问题。"
            ),
            "asset_profile": {
                "record_set_count": len(record_sets),
                "record_count": sum(record_counts.values()),
                "record_counts": record_counts,
                "record_sets": record_set_profiles,
                "field_review": {
                    "status": "attention" if any(
                        item.get("review_findings") for item in record_set_profiles
                    ) else "clear",
                    "finding_count": sum(
                        len(item.get("review_findings", []))
                        for item in record_set_profiles
                    ),
                    "findings": [
                        finding
                        for item in record_set_profiles
                        for finding in item.get("review_findings", [])
                    ],
                },
                "filesystem_scope_count": len(scopes),
                "scope_file_count": sum(scope_file_counts.values()),
                "scope_file_counts": scope_file_counts,
                "filesystem_scopes": scope_profiles,
                "undeclared_record_tables": undeclared_record_tables,
                "undeclared_scope_directories": undeclared_scope_directories,
                "core_asset_count": len(core_assets),
            },
            "connectivity_profile": {
                "component_count": len(components),
                "components": components,
                "edge_count": len(edges),
                "isolated_assets": [node.split(":", 1)[1] for node in isolated],
                "unjustified_isolated_assets": [node.split(":", 1)[1] for node in unjustified],
            },
            "relationship_profile": {
                "declared_count": len(relationships),
                "valid_count": sum(bool(item["valid"]) for item in relation_profiles),
                "relationships": relation_profiles,
            },
            "file_reference_profile": {
                "reference_field_count": len(file_profiles),
                "valid_reference_field_count": sum(bool(item["valid"]) for item in file_profiles),
                "references": file_profiles,
            },
            "source_integration_profile": {
                "selected_source_count": len(expected_sources),
                "used_source_count": len(used_sources),
                "unused_selected_sources": unused_sources,
                "multi_source_assets": multi_source_assets,
                "cross_source_edge_count": cross_source_edges,
                "duplicate_scope_source_paths": duplicate_scope_sources,
            },
            "need_binding_profile": {
                "need_count": len(bindings),
                "realized_need_count": sum(
                    item.get("status") == "realized" for item in bindings
                ),
                "partial_need_count": sum(
                    item.get("status") == "partial" for item in bindings
                ),
                "closed_need_count": sum(
                    item.get("status") in {"unavailable", "not_applicable"}
                    for item in bindings
                ),
                "bound_asset_count": len(bound_assets),
                "unbound_core_assets": unbound_core,
                "unbound_assets": unbound_assets,
                "bindings": bindings,
            },
            "integration_gaps": gaps,
        }
    finally:
        if connection is not None:
            connection.close()


__all__ = ["build_integration_profile"]
