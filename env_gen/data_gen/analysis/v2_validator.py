"""对 v2 environment.json 与实际 state 进行独立、确定性校验。"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .filesystem_scopes import structure_definition_issues, validate_scope_tree
from .integration_materialization import environment_from_plan, validate_records
from .integration_plan import field_definition_issues
from .validator import ValidationIssue, ValidationReport


_SQL_TYPES = {
    "string": "TEXT",
    "integer": "INTEGER",
    "number": "REAL",
    "boolean": "INTEGER",
    "object": "TEXT",
    "array": "TEXT",
}
_RESERVED_IDS = {"raw", "derived", "output", "temp", "misc"}


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


class V2EnvironmentPackageValidator:
    """只依据环境契约、SQLite、Scope 文件树和集成计划判定。"""

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
        *,
        integration_plan: dict[str, Any] | None = None,
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
        if integration_plan is not None:
            expected = environment_from_plan(integration_plan)
            if environment != expected:
                self._error(
                    report,
                    "environment_not_derived_from_plan",
                    "$.environment",
                    "environment.json 与当前 integration_plan 的确定性导出结果不同",
                )
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
                for issue in field_definition_issues(
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
        for message in validate_records(records, record_set):
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
