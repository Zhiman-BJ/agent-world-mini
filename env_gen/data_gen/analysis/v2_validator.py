"""对 v2 environment.json 与实际 state 进行独立、确定性校验。"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from .filesystem_scopes import structure_definition_issues, validate_scope_tree


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class ValidationReport:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "statistics": self.statistics,
        }


_SQL_TYPES = {
    "string": "TEXT",
    "integer": "INTEGER",
    "number": "REAL",
    "boolean": "INTEGER",
    "object": "TEXT",
    "array": "TEXT",
}
_RESERVED_IDS = {"raw", "derived", "output", "temp", "misc"}
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


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _pointer(error: Any) -> str:
    value = "$.environment"
    for part in error.absolute_path:
        value += f"[{part}]" if isinstance(part, int) else f".{part}"
    return value


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _field_definition_issues(
    definition: dict[str, Any],
    *,
    path: str,
    container_depth: int | None = None,
    top_level: bool = True,
) -> list[ValidationIssue]:
    field_type = str(definition.get("type") or "")
    if container_depth is None:
        container_depth = 1 if field_type in {"object", "array"} else 0
    allowed = _COMMON_FIELD_KEYS | _TYPE_KEYS.get(field_type, set())
    if top_level and (
        field_type == "string"
        or (
            field_type == "array"
            and isinstance(definition.get("items"), dict)
            and definition["items"].get("type") == "string"
        )
    ):
        allowed.add("reference")
    issues: list[ValidationIssue] = []
    unknown = sorted(set(definition) - allowed)
    if unknown:
        issues.append(ValidationIssue(
            "field_keyword_not_allowed", path,
            f"{field_type} 字段包含不允许的参数：{unknown}",
        ))
    if "enum" in definition and "const" in definition:
        issues.append(ValidationIssue(
            "field_enum_const_conflict", path, "enum 和 const 不能同时出现",
        ))
    if field_type == "string":
        format_name = definition.get("format")
        if isinstance(format_name, str) and format_name not in _STRING_FORMATS:
            issues.append(ValidationIssue(
                "unsupported_string_format", f"{path}.format",
                f"不支持的 string format：{format_name}",
            ))
        pattern = definition.get("pattern")
        if isinstance(pattern, str):
            try:
                re.compile(pattern)
            except re.error as error:
                issues.append(ValidationIssue(
                    "invalid_field_pattern", f"{path}.pattern", str(error),
                ))
        if (
            isinstance(definition.get("minLength"), int)
            and isinstance(definition.get("maxLength"), int)
            and definition["minLength"] > definition["maxLength"]
        ):
            issues.append(ValidationIssue(
                "invalid_string_length_bounds", path,
                "minLength 不能大于 maxLength",
            ))
    if field_type in {"integer", "number"}:
        lower = definition.get("exclusiveMinimum", definition.get("minimum"))
        upper = definition.get("exclusiveMaximum", definition.get("maximum"))
        if (
            isinstance(lower, (int, float))
            and isinstance(upper, (int, float))
            and lower > upper
        ):
            issues.append(ValidationIssue(
                "invalid_numeric_bounds", path, "数值下界不能大于上界",
            ))
    expected_python = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }.get(field_type)
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
            issues.append(ValidationIssue(
                "field_literal_type_mismatch", f"{path}.{keyword}",
                f"{keyword} 成员必须符合 type={field_type}",
            ))
    if field_type == "object":
        if definition.get("additionalProperties") is not False:
            issues.append(ValidationIssue(
                "object_must_be_closed", path,
                "Object 必须声明 additionalProperties=false",
            ))
        properties = definition.get("properties")
        required = definition.get("required")
        if not isinstance(properties, dict) or not properties:
            issues.append(ValidationIssue(
                "object_without_properties", path, "Object 必须声明非空 properties",
            ))
        if not isinstance(required, list):
            issues.append(ValidationIssue(
                "object_without_required", path, "Object 必须声明 required 数组",
            ))
        if isinstance(properties, dict) and isinstance(required, list):
            unknown_required = sorted(set(required) - set(properties))
            if unknown_required:
                issues.append(ValidationIssue(
                    "object_unknown_required", f"{path}.required",
                    f"required 引用了未知属性：{unknown_required}",
                ))
            for name, child in properties.items():
                if not isinstance(child, dict):
                    continue
                child_type = str(child.get("type") or "")
                next_depth = container_depth + (
                    1 if child_type in {"object", "array"} else 0
                )
                if next_depth > 2:
                    issues.append(ValidationIssue(
                        "container_nesting_too_deep", f"{path}.properties.{name}",
                        "Object/Array 容器嵌套最多两层",
                    ))
                issues.extend(_field_definition_issues(
                    child,
                    path=f"{path}.properties.{name}",
                    container_depth=next_depth,
                    top_level=False,
                ))
    elif field_type == "array":
        items = definition.get("items")
        if not isinstance(items, dict):
            issues.append(ValidationIssue(
                "array_without_items", path, "Array 必须声明 items",
            ))
        if (
            isinstance(definition.get("minItems"), int)
            and isinstance(definition.get("maxItems"), int)
            and definition["minItems"] > definition["maxItems"]
        ):
            issues.append(ValidationIssue(
                "invalid_array_length_bounds", path,
                "minItems 不能大于 maxItems",
            ))
        if isinstance(items, dict):
            child_type = str(items.get("type") or "")
            next_depth = container_depth + (
                1 if child_type in {"object", "array"} else 0
            )
            if next_depth > 2:
                issues.append(ValidationIssue(
                    "container_nesting_too_deep", f"{path}.items",
                    "Object/Array 容器嵌套最多两层",
                ))
            issues.extend(_field_definition_issues(
                items,
                path=f"{path}.items",
                container_depth=next_depth,
                top_level=False,
            ))
    if not top_level and "reference" in definition:
        issues.append(ValidationIssue(
            "nested_file_reference", path, "嵌套字段不能声明文件 reference",
        ))
    return issues


def _matches_string_format(value: str, format_name: str) -> bool:
    try:
        if format_name == "date":
            date.fromisoformat(value)
        elif format_name == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif format_name == "time":
            time.fromisoformat(value.replace("Z", "+00:00"))
        elif format_name == "uuid":
            UUID(value)
        elif format_name == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme and (parsed.netloc or parsed.path))
        elif format_name == "email":
            return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))
        elif format_name == "hostname":
            return bool(re.fullmatch(
                r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                value,
            ))
        elif format_name in {"ipv4", "ipv6"}:
            import ipaddress
            address = ipaddress.ip_address(value)
            return (
                (format_name == "ipv4" and address.version == 4)
                or (format_name == "ipv6" and address.version == 6)
            )
        elif format_name == "duration":
            return bool(re.fullmatch(
                r"P(?=\d|T\d)(?:\d+Y)?(?:\d+M)?(?:\d+D)?"
                r"(?:T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?",
                value,
            ))
    except (ValueError, TypeError):
        return False
    return True


def _value_issues(
    value: Any, definition: dict[str, Any], *, path: str,
) -> list[str]:
    if value is None:
        return [] if definition.get("nullable") is True else [f"{path} 不允许 null"]
    field_type = definition.get("type")
    valid = (
        (field_type == "string" and isinstance(value, str))
        or (
            field_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (
            field_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        or (field_type == "boolean" and isinstance(value, bool))
        or (field_type == "object" and isinstance(value, dict))
        or (field_type == "array" and isinstance(value, list))
    )
    if not valid:
        return [f"{path} 应为 {field_type}，实际是 {type(value).__name__}"]
    issues: list[str] = []
    if field_type == "string":
        if (
            isinstance(definition.get("minLength"), int)
            and len(value) < definition["minLength"]
        ):
            issues.append(f"{path} 长度小于 minLength")
        if (
            isinstance(definition.get("maxLength"), int)
            and len(value) > definition["maxLength"]
        ):
            issues.append(f"{path} 长度大于 maxLength")
        pattern = definition.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value) is not None
            except re.error as error:
                issues.append(f"{path} 的 pattern 无效：{error}")
            else:
                if not matches:
                    issues.append(f"{path} 不匹配 pattern")
        format_name = definition.get("format")
        if (
            isinstance(format_name, str)
            and not _matches_string_format(value, format_name)
        ):
            issues.append(f"{path} 不符合 format={format_name}")
    if field_type in {"integer", "number"}:
        if "minimum" in definition and value < definition["minimum"]:
            issues.append(f"{path} 小于 minimum")
        if "maximum" in definition and value > definition["maximum"]:
            issues.append(f"{path} 大于 maximum")
        if "exclusiveMinimum" in definition and value <= definition["exclusiveMinimum"]:
            issues.append(f"{path} 不大于 exclusiveMinimum")
        if "exclusiveMaximum" in definition and value >= definition["exclusiveMaximum"]:
            issues.append(f"{path} 不小于 exclusiveMaximum")
        multiple = definition.get("multipleOf")
        if isinstance(multiple, (int, float)) and multiple > 0:
            quotient = value / multiple
            if not math.isclose(
                quotient, round(quotient), rel_tol=1e-9, abs_tol=1e-9,
            ):
                issues.append(f"{path} 不满足 multipleOf")
    if "enum" in definition and value not in definition["enum"]:
        issues.append(f"{path} 不在 enum 中")
    if "const" in definition and value != definition["const"]:
        issues.append(f"{path} 不等于 const")
    if field_type == "object":
        properties = definition.get("properties", {})
        required = set(definition.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            issues.append(f"{path} 缺少属性 {missing}")
        unknown = sorted(set(value) - set(properties))
        if unknown:
            issues.append(f"{path} 包含未知属性 {unknown}")
        for name, child in properties.items():
            if name in value and isinstance(child, dict):
                issues.extend(_value_issues(
                    value[name], child, path=f"{path}.{name}",
                ))
    if field_type == "array":
        if (
            isinstance(definition.get("minItems"), int)
            and len(value) < definition["minItems"]
        ):
            issues.append(f"{path} 元素数小于 minItems")
        if (
            isinstance(definition.get("maxItems"), int)
            and len(value) > definition["maxItems"]
        ):
            issues.append(f"{path} 元素数大于 maxItems")
        items = definition.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                issues.extend(_value_issues(item, items, path=f"{path}[{index}]"))
        if definition.get("uniqueItems") is True:
            serialized = [
                json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value
            ]
            if len(serialized) != len(set(serialized)):
                issues.append(f"{path} 不满足 uniqueItems")
    return issues


def _record_value_issues(
    records: list[dict[str, Any]], record_set: dict[str, Any],
) -> list[str]:
    fields = record_set.get("fields", {})
    issues: list[str] = []
    for index, record in enumerate(records):
        unknown = sorted(set(record) - set(fields))
        if unknown:
            issues.append(f"record[{index}] 包含未声明字段 {unknown}")
        for name, definition in fields.items():
            if isinstance(definition, dict):
                issues.extend(_value_issues(
                    record.get(name), definition, path=f"record[{index}].{name}",
                ))
        if len(issues) >= 30:
            break
    key_fields = record_set.get("key_fields", [])
    if key_fields:
        seen: set[tuple[str, ...]] = set()
        for index, record in enumerate(records):
            key = tuple(
                json.dumps(record.get(name), ensure_ascii=False, sort_keys=True)
                for name in key_fields
            )
            if key in seen:
                issues.append(f"record[{index}] 的 key_fields 重复")
                break
            seen.add(key)
    return issues


class V2EnvironmentPackageValidator:
    """只依据环境契约、SQLite 和 Scope 文件树判定。"""

    def __init__(self, schema_path: Path) -> None:
        self.schema_path = schema_path.resolve()
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(
            self.schema, format_checker=FormatChecker(),
        )

    @staticmethod
    def _error(report: ValidationReport, code: str, path: str, message: str) -> None:
        report.errors.append(ValidationIssue(code, path, message))

    @staticmethod
    def _load_object(path: Path, report: ValidationReport, pointer: str) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            report.errors.append(ValidationIssue("invalid_json", pointer, str(error)))
            return None
        if not isinstance(value, dict):
            report.errors.append(ValidationIssue("invalid_json_root", pointer, "根节点必须是对象"))
            return None
        return value

    def validate(
        self,
        package_root: Path,
    ) -> ValidationReport:
        package_root = package_root.resolve()
        report = ValidationReport()
        environment = self._load_object(
            package_root / "environment.json", report, "$.environment",
        )
        if environment is None:
            return report
        for error in sorted(
            self.validator.iter_errors(environment),
            key=lambda item: tuple(str(value) for value in item.absolute_path),
        ):
            self._error(report, "environment_v2_schema", _pointer(error), error.message)
        self._validate_semantics(environment, report)
        self._validate_state(package_root, environment, report)
        report.statistics.update({
            "record_sets": len(environment.get("record_sets", [])),
            "relationships": len(environment.get("relationships", [])),
            "filesystem_scopes": len(environment.get("filesystem_scopes", [])),
        })
        return report

    def _validate_semantics(
        self, environment: dict[str, Any], report: ValidationReport,
    ) -> None:
        record_sets = [item for item in environment.get("record_sets", []) if isinstance(item, dict)]
        scopes = [item for item in environment.get("filesystem_scopes", []) if isinstance(item, dict)]
        relationships = [item for item in environment.get("relationships", []) if isinstance(item, dict)]
        record_ids = [str(item.get("record_set_id")) for item in record_sets]
        scope_ids = [str(item.get("scope_id")) for item in scopes]
        relationship_ids = [str(item.get("relationship_id")) for item in relationships]
        for label, values, pointer in (
            ("Record Set", record_ids, "$.environment.record_sets"),
            ("Filesystem Scope", scope_ids, "$.environment.filesystem_scopes"),
            ("Relationship", relationship_ids, "$.environment.relationships"),
        ):
            if len(values) != len(set(values)):
                self._error(report, "duplicate_environment_id", pointer, f"{label} ID 重复")
        for kind, value in [("record_set_id", item) for item in record_ids] + [("scope_id", item) for item in scope_ids]:
            if value.startswith("sqlite_") or value in _RESERVED_IDS:
                self._error(
                    report, "reserved_environment_id", f"$.environment.{kind}",
                    f"{kind} 使用了保留名称：{value}",
                )
        scope_id_set = set(scope_ids)
        record_map = {str(item.get("record_set_id")): item for item in record_sets}
        for record_index, record_set in enumerate(record_sets):
            fields = record_set.get("fields", {})
            if not isinstance(fields, dict):
                continue
            for field_name, definition in fields.items():
                if not isinstance(definition, dict):
                    continue
                for issue in _field_definition_issues(
                    definition,
                    path=f"$.environment.record_sets[{record_index}].fields.{field_name}",
                ):
                    self._error(report, issue.code, issue.path, issue.message)
                reference = definition.get("reference")
                if isinstance(reference, dict) and reference.get("scope_id") not in scope_id_set:
                    self._error(
                        report, "unknown_file_scope",
                        f"$.environment.record_sets[{record_index}].fields.{field_name}.reference",
                        "字段引用了不存在的 Filesystem Scope",
                    )
            for key in record_set.get("key_fields", []):
                definition = fields.get(key)
                if not isinstance(definition, dict):
                    self._error(report, "unknown_key_field", f"$.environment.record_sets[{record_index}].key_fields", f"键字段不存在：{key}")
                elif definition.get("type") not in {"string", "integer", "number", "boolean"} or definition.get("nullable") is not False:
                    self._error(report, "invalid_key_field", f"$.environment.record_sets[{record_index}].key_fields", f"键字段必须是非空顶层标量：{key}")
        for scope_index, scope in enumerate(scopes):
            structure = scope.get("structure")
            if isinstance(structure, dict):
                for issue in structure_definition_issues(
                    structure,
                    path=f"$.environment.filesystem_scopes[{scope_index}].structure",
                ):
                    self._error(report, issue.code, issue.path, issue.message)
        for index, relationship in enumerate(relationships):
            endpoints: list[tuple[str, dict[str, Any]]] = []
            for endpoint_name in ("from", "to"):
                endpoint = relationship.get(endpoint_name)
                if not isinstance(endpoint, dict):
                    continue
                endpoints.append((endpoint_name, endpoint))
                record_set = record_map.get(str(endpoint.get("record_set_id")))
                if record_set is None:
                    self._error(report, "unknown_relationship_record_set", f"$.environment.relationships[{index}].{endpoint_name}", "关系端点 Record Set 不存在")
                    continue
                field_names = endpoint.get("fields", [])
                if not field_names:
                    self._error(report, "empty_relationship_endpoint", f"$.environment.relationships[{index}].{endpoint_name}.fields", "关系端点至少需要一个字段")
                fields = record_set.get("fields", {})
                for field_name in field_names:
                    definition = fields.get(field_name) if isinstance(fields, dict) else None
                    if not isinstance(definition, dict) or definition.get("type") not in {"string", "integer", "number", "boolean"}:
                        self._error(report, "invalid_relationship_field", f"$.environment.relationships[{index}].{endpoint_name}.fields", f"关系字段必须是顶层标量：{field_name}")
            if len(endpoints) == 2:
                left = endpoints[0][1].get("fields", [])
                right = endpoints[1][1].get("fields", [])
                if len(left) != len(right):
                    self._error(report, "relationship_arity_mismatch", f"$.environment.relationships[{index}]", "关系两端字段数量必须相同")

    def _validate_state(
        self,
        package_root: Path,
        environment: dict[str, Any],
        report: ValidationReport,
    ) -> None:
        state = package_root / "state"
        if not state.is_dir():
            self._error(report, "missing_state_directory", "$.state", "缺少 state 目录")
            return
        for item in state.rglob("*"):
            if item.is_symlink():
                self._error(report, "state_symlink_not_allowed", "$.state", f"state 不允许符号链接：{item.relative_to(state)}")
        record_sets = [item for item in environment.get("record_sets", []) if isinstance(item, dict)]
        scopes = [item for item in environment.get("filesystem_scopes", []) if isinstance(item, dict)]
        database = state / "records.sqlite"
        if record_sets:
            self._validate_database(database, record_sets, environment.get("relationships", []), state / "filesystem_scopes", report)
        elif database.exists():
            self._error(report, "unexpected_records_database", "$.state.records.sqlite", "没有 Record Set 时不应存在 records.sqlite")
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(database) + suffix).exists():
                self._error(report, "sqlite_sidecar_present", "$.state.records.sqlite", f"发布包不能包含 SQLite sidecar：{database.name + suffix}")
        scopes_root = state / "filesystem_scopes"
        declared_scope_ids = {str(item.get("scope_id")) for item in scopes}
        actual_scope_ids = {
            item.name for item in scopes_root.iterdir() if item.is_dir()
        } if scopes_root.is_dir() else set()
        for missing in sorted(declared_scope_ids - actual_scope_ids):
            self._error(report, "missing_scope_directory", f"$.state.filesystem_scopes.{missing}", "声明的 Scope 目录不存在")
        for extra in sorted(actual_scope_ids - declared_scope_ids):
            self._error(report, "undeclared_scope_directory", f"$.state.filesystem_scopes.{extra}", "存在未声明的 Scope 目录")
        for scope in scopes:
            scope_id = str(scope.get("scope_id"))
            structure = scope.get("structure")
            if isinstance(structure, dict):
                for issue in validate_scope_tree(
                    scopes_root / scope_id,
                    structure,
                    pointer=f"$.state.filesystem_scopes.{scope_id}",
                ):
                    self._error(report, issue.code, issue.path, issue.message)

    def _validate_database(
        self,
        database: Path,
        record_sets: list[dict[str, Any]],
        relationships_value: Any,
        scopes_root: Path,
        report: ValidationReport,
    ) -> None:
        if not database.is_file():
            self._error(report, "missing_records_database", "$.state.records.sqlite", "Record Set 非空但 records.sqlite 不存在")
            return
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        except sqlite3.Error as error:
            self._error(report, "unreadable_records_database", "$.state.records.sqlite", str(error))
            return
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                self._error(report, "sqlite_integrity_failure", "$.state.records.sqlite", str(integrity))
            declared = {str(item.get("record_set_id")): item for item in record_sets}
            actual = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            for missing in sorted(set(declared) - actual):
                self._error(report, "missing_record_table", f"$.state.records.sqlite.{missing}", "声明的 Record Set 表不存在")
            for extra in sorted(actual - set(declared)):
                self._error(report, "undeclared_record_table", f"$.state.records.sqlite.{extra}", "存在未声明的业务表")
            row_counts: dict[str, int] = {}
            for record_set_id, record_set in declared.items():
                if record_set_id not in actual:
                    continue
                row_counts[record_set_id] = self._validate_table(
                    connection, record_set_id, record_set, scopes_root, report,
                )
            self._validate_relationships(
                connection,
                [item for item in relationships_value if isinstance(item, dict)] if isinstance(relationships_value, list) else [],
                report,
            )
            report.statistics["record_counts"] = row_counts
            report.statistics["record_count"] = sum(row_counts.values())
        except sqlite3.Error as error:
            self._error(report, "sqlite_validation_error", "$.state.records.sqlite", str(error))
        finally:
            connection.close()

    def _validate_table(
        self,
        connection: sqlite3.Connection,
        record_set_id: str,
        record_set: dict[str, Any],
        scopes_root: Path,
        report: ValidationReport,
    ) -> int:
        fields = record_set.get("fields", {})
        info = list(connection.execute(f"PRAGMA table_info({_quote(record_set_id)})"))
        actual_names = [str(row[1]) for row in info]
        expected_names = list(fields) if isinstance(fields, dict) else []
        if actual_names != expected_names:
            self._error(report, "record_columns_mismatch", f"$.state.records.sqlite.{record_set_id}", f"列应为 {expected_names}，实际为 {actual_names}")
        for row in info:
            name = str(row[1])
            definition = fields.get(name) if isinstance(fields, dict) else None
            if not isinstance(definition, dict):
                continue
            expected_type = _SQL_TYPES.get(str(definition.get("type")))
            if str(row[2]).upper() != expected_type:
                self._error(report, "record_column_type_mismatch", f"$.state.records.sqlite.{record_set_id}.{name}", f"SQLite 类型应为 {expected_type}，实际为 {row[2]}")
            expected_not_null = definition.get("nullable") is False
            if bool(row[3]) != expected_not_null:
                self._error(report, "record_column_nullability_mismatch", f"$.state.records.sqlite.{record_set_id}.{name}", "SQLite NOT NULL 与 nullable 声明不一致")
        records: list[dict[str, Any]] = []
        cursor = connection.execute(f"SELECT * FROM {_quote(record_set_id)}")
        for row_index, row in enumerate(cursor):
            record: dict[str, Any] = {}
            for name, stored in zip(actual_names, row):
                definition = fields.get(name, {}) if isinstance(fields, dict) else {}
                field_type = definition.get("type")
                value = stored
                if stored is not None and field_type == "boolean":
                    if type(stored) is not int or stored not in {0, 1}:
                        self._error(report, "invalid_sqlite_boolean", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", "Boolean 必须存为 INTEGER 0/1")
                    value = bool(stored)
                elif stored is not None and field_type in {"object", "array"}:
                    try:
                        value = json.loads(stored)
                    except (TypeError, json.JSONDecodeError) as error:
                        self._error(report, "invalid_sqlite_json", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", str(error))
                        value = None
                    else:
                        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        if stored != canonical:
                            self._error(report, "noncanonical_sqlite_json", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", "Object/Array 列必须使用规范 JSON 文本")
                elif isinstance(stored, float) and not math.isfinite(stored):
                    self._error(report, "nonfinite_sqlite_number", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", "数值必须有限")
                record[name] = value
                reference = definition.get("reference") if isinstance(definition, dict) else None
                if isinstance(reference, dict) and value is not None:
                    values = value if isinstance(value, list) else [value]
                    for relative in values:
                        if not isinstance(relative, str) or not _safe_relative(relative):
                            self._error(report, "unsafe_file_reference", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", f"不安全 Scope 相对路径：{relative}")
                            continue
                        target = scopes_root / str(reference.get("scope_id")) / relative
                        expected = target.is_file() if reference.get("target") == "file" else target.is_dir()
                        if not expected:
                            self._error(report, "missing_file_reference", f"$.state.records.sqlite.{record_set_id}[{row_index}].{name}", f"Scope 路径不存在或类型错误：{relative}")
            records.append(record)
        for message in _record_value_issues(records, record_set):
            self._error(report, "invalid_record_value", f"$.state.records.sqlite.{record_set_id}", message)
        if not records:
            self._error(report, "empty_record_table", f"$.state.records.sqlite.{record_set_id}", "Record Set 不能为空")
        return len(records)

    def _validate_relationships(
        self,
        connection: sqlite3.Connection,
        relationships: list[dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        for relationship in relationships:
            relationship_id = str(relationship.get("relationship_id"))
            source = relationship.get("from", {})
            target = relationship.get("to", {})
            source_table = str(source.get("record_set_id"))
            target_table = str(target.get("record_set_id"))
            source_fields = list(source.get("fields", []))
            target_fields = list(target.get("fields", []))
            if not source_fields or len(source_fields) != len(target_fields):
                continue
            target_rows = list(connection.execute(
                f"SELECT {', '.join(_quote(name) for name in target_fields)} FROM {_quote(target_table)}"
            ))
            target_keys = {tuple(row) for row in target_rows}
            if any(any(value is None for value in row) for row in target_rows):
                self._error(report, "null_relationship_target", f"$.environment.relationships.{relationship_id}", "目标关系键不能包含 NULL")
            if len(target_rows) != len(target_keys):
                self._error(report, "nonunique_relationship_target", f"$.environment.relationships.{relationship_id}", "目标关系键组合不唯一")
            source_rows = list(connection.execute(
                f"SELECT {', '.join(_quote(name) for name in source_fields)} FROM {_quote(source_table)}"
            ))
            non_null_source: list[tuple[Any, ...]] = []
            for row in source_rows:
                null_count = sum(value is None for value in row)
                if null_count not in {0, len(row)}:
                    self._error(report, "partial_null_relationship_source", f"$.environment.relationships.{relationship_id}", "复合来源键必须整组为空或整组非空")
                elif null_count == 0:
                    non_null_source.append(tuple(row))
            missing = {row for row in non_null_source if row not in target_keys}
            if missing:
                self._error(report, "unclosed_relationship", f"$.environment.relationships.{relationship_id}", f"有 {len(missing)} 个来源键找不到目标")
            if relationship.get("cardinality") == "one_to_one" and len(non_null_source) != len(set(non_null_source)):
                self._error(report, "one_to_one_source_not_unique", f"$.environment.relationships.{relationship_id}", "one_to_one 来源键不唯一")


__all__ = ["V2EnvironmentPackageValidator"]
