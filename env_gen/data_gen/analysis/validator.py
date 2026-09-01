"""DataGen 中间提交点和最终环境包的确定性校验。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.record_primitives import _choose_primary_id
from env_gen.data_gen.analysis.structured_io import (
    StructuredDataError,
    read_entity_groups,
)
from env_gen.data_gen.config import CollectionPolicy


CHECKPOINT_SCHEMA_DIR = Path(__file__).resolve().parent / "checkpoint_schemas"
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_PROGRAM_SUFFIXES = {
    ".bash", ".c", ".cjs", ".cpp", ".cs", ".fish", ".go", ".h", ".hpp",
    ".ipynb", ".java", ".js", ".jsx", ".lua", ".mjs", ".php", ".pl",
    ".ps1", ".py", ".pyc", ".r", ".rb", ".rs", ".sh", ".sql", ".ts",
    ".tsx", ".zsh",
}


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


class EnvironmentPackageValidator:
    """只相信磁盘文件和选中 Seed，不接受 Agent 自报统计。"""

    def __init__(
        self,
        schema_path: Path,
        *,
        seed: dict[str, Any],
        seed_sha256: str,
        collection_policy: CollectionPolicy | None = None,
        checkpoint_schema_dir: Path | None = None,
    ) -> None:
        self.seed = seed
        self.seed_global_id = str(seed.get("global_id") or "")
        self.seed_sha256 = seed_sha256
        self.collection_policy = collection_policy or CollectionPolicy()
        self.checkpoint_schema_dir = (checkpoint_schema_dir or CHECKPOINT_SCHEMA_DIR).resolve()

        schema_path = schema_path.resolve()
        self.environment_schema = self._read_schema(schema_path)
        self.environment_validator = Draft202012Validator(self.environment_schema)
        self.checkpoint_validator = Draft202012Validator(
            self._read_schema(self.checkpoint_schema_dir / "data_checkpoint.schema.json")
        )
        self.report_validator = Draft202012Validator(
            self._read_schema(self.checkpoint_schema_dir / "research_report.schema.json")
        )

    @staticmethod
    def _read_schema(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Schema 根节点必须是对象：{path}")
        Draft202012Validator.check_schema(payload)
        return payload

    def validate_data_checkpoint(
        self,
        package_root: Path,
        *,
        checkpoint_path: Path | None = None,
    ) -> ValidationReport:
        report = ValidationReport()
        package_root = package_root.resolve()
        self._validate_package_layout(package_root, report)
        checkpoint_path = checkpoint_path or package_root / "provenance/data_checkpoint.json"
        checkpoint = self._load_json(checkpoint_path, report, "$.data_checkpoint")
        if not isinstance(checkpoint, dict):
            return report
        self._validate_schema(
            self.checkpoint_validator,
            checkpoint,
            report,
            "$.data_checkpoint",
            "data_checkpoint_schema",
        )
        self._validate_seed_reference(checkpoint, report, "$.data_checkpoint")

        workspace = package_root / "workspace"
        listed: set[str] = set()
        counts = {"raw_files": 0, "entity_files": 0, "derived_files": 0}
        byte_counts = {"raw_files": 0, "derived_files": 0}
        for bucket in counts:
            expected_directory = {
                "raw_files": "raw",
                "entity_files": "entities",
                "derived_files": "derived",
            }[bucket]
            values = checkpoint.get(bucket, [])
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                pointer = f"$.data_checkpoint.{bucket}[{index}]"
                path = self._workspace_path(workspace, value)
                if not isinstance(value, str) or not value.startswith(f"{expected_directory}/"):
                    self._error(report, "checkpoint_wrong_directory", pointer, f"文件必须位于 {expected_directory}/")
                    continue
                if value in listed:
                    self._error(report, "duplicate_checkpoint_file", pointer, f"重复文件：{value}")
                listed.add(value)
                if path is None or not path.is_file():
                    self._error(report, "checkpoint_missing_file", pointer, f"文件不存在或路径越界：{value}")
                    continue
                counts[bucket] += 1
                if bucket in byte_counts:
                    byte_counts[bucket] += path.stat().st_size
                if bucket != "raw_files" and path.suffix.lower() in _PROGRAM_SUFFIXES:
                    self._error(report, "program_file_in_business_data", pointer, f"业务数据桶不能包含程序：{value}")
                if bucket == "entity_files":
                    self._validate_canonical_entity_file(path, report, pointer)

        actual = self._business_files(workspace)
        for value in sorted(actual - listed):
            self._error(report, "unlisted_checkpoint_file", "$.data_checkpoint", f"业务文件未登记：{value}")
        for value in sorted(listed - actual):
            self._error(report, "listed_file_not_in_workspace", "$.data_checkpoint", f"登记文件不在 workspace：{value}")

        policy = self.collection_policy
        if counts["raw_files"] > policy.max_raw_files:
            self._error(report, "raw_file_budget_exceeded", "$.data_checkpoint.raw_files", f"raw 文件数 {counts['raw_files']} > {policy.max_raw_files}")
        if byte_counts["raw_files"] > policy.max_raw_bytes:
            self._error(report, "raw_byte_budget_exceeded", "$.workspace.raw", f"raw 大小 {byte_counts['raw_files']} > {policy.max_raw_bytes}")
        if byte_counts["derived_files"] > policy.max_derived_bytes:
            self._error(report, "derived_byte_budget_exceeded", "$.workspace.derived", f"derived 大小 {byte_counts['derived_files']} > {policy.max_derived_bytes}")
        workspace_bytes = sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file()) if workspace.is_dir() else 0
        if workspace_bytes > policy.max_workspace_bytes:
            self._error(report, "workspace_budget_exceeded", "$.workspace", f"workspace 大小 {workspace_bytes} > {policy.max_workspace_bytes}")

        self._validate_checkpoint_source_map(checkpoint, report)
        if checkpoint.get("status") == "insufficient_public_data":
            self._error(
                report,
                "insufficient_public_data",
                "$.data_checkpoint.status",
                str(checkpoint.get("summary") or "核心公开数据不足"),
            )
        elif checkpoint.get("status") == "ready" and not counts["raw_files"]:
            self._error(report, "checkpoint_without_raw", "$.data_checkpoint.raw_files", "ready 至少需要一个真实 raw 文件")
        report.statistics.update({**counts, "workspace_bytes": workspace_bytes})
        return report

    def validate(self, package_root: Path) -> ValidationReport:
        package_root = package_root.resolve()
        report = self.validate_data_checkpoint(package_root)
        checkpoint = self._load_json(package_root / "provenance/data_checkpoint.json", report, "$.data_checkpoint")
        environment = self._load_json(package_root / "environment.json", report, "$.environment")
        source_plan = self._load_json(
            package_root / "provenance/source_plan.json",
            report,
            "$.source_plan",
        )
        if not isinstance(environment, dict) or not isinstance(checkpoint, dict):
            return report
        self._validate_schema(
            self.environment_validator,
            environment,
            report,
            "$.environment",
            "environment_schema",
        )

        workspace = package_root / "workspace"
        resources = self._resource_map(environment.get("resources"), report)
        resource_files: dict[str, list[Path]] = {}
        owners: dict[str, list[str]] = {}
        entity_inventory: dict[str, dict[str, Any]] = {}
        for resource_id, resource in resources.items():
            files = self._files_for_resource(workspace, resource, report, resource_id)
            resource_files[resource_id] = files
            for path in files:
                relative = path.relative_to(workspace).as_posix()
                if relative.startswith(("raw/", "entities/", "derived/")):
                    owners.setdefault(relative, []).append(resource_id)
            self._validate_resource_contract(resource_id, resource, resources, files, report)
            if resource.get("data_type") == "entity":
                self._load_declared_entities(resource_id, resource, files, entity_inventory, report)

        self._validate_entity_file_references(
            workspace,
            resources,
            owners,
            entity_inventory,
            report,
        )

        expected = self._business_files(workspace)
        for relative in sorted(expected):
            matched = owners.get(relative, [])
            if not matched:
                self._error(report, "unowned_business_file", "$.environment.resources", f"业务文件没有资源声明：{relative}")
            elif len(matched) > 1:
                self._error(report, "multiply_owned_business_file", "$.environment.resources", f"业务文件被多个资源覆盖：{relative} -> {matched}")
        self._validate_resource_graph(resources, report)

        sources = self._validate_sources(
            package_root / "provenance/sources.json",
            workspace,
            resources,
            resource_files,
            checkpoint,
            report,
        )
        research = self._validate_research_report(
            package_root / "provenance/research_report.json",
            resources,
            entity_inventory,
            report,
            expected_representation_mode=(
                str(source_plan.get("data_mode"))
                if isinstance(source_plan, dict)
                and isinstance(source_plan.get("data_mode"), str)
                else None
            ),
        )
        report.statistics.update(
            {
                "resources": len(resources),
                "entity_types": len(entity_inventory),
                "entity_records": {
                    name: len(entry["records"])
                    for name, entry in sorted(entity_inventory.items())
                },
                "sources": len(sources),
                "reference_tools": len(self.seed.get("init_ref_tools", [])),
                "research_status": research.get("status") if isinstance(research, dict) else None,
            }
        )
        return report

    def finalize_provenance(self, package_root: Path) -> None:
        path = package_root / "provenance/sources.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        workspace = (package_root / "workspace").resolve()
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                continue
            for item in source.get("files", []):
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    continue
                target = self._workspace_path(workspace, item["path"])
                if target is not None and target.is_file():
                    item["sha256"] = self._sha256(target)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _validate_seed_reference(self, payload: dict[str, Any], report: ValidationReport, pointer: str) -> None:
        if payload.get("seed_global_id") != self.seed_global_id:
            self._error(report, "seed_global_id_mismatch", f"{pointer}.seed_global_id", "文件没有引用本次 Seed global_id")
        if payload.get("seed_sha256") != self.seed_sha256:
            self._error(report, "seed_sha256_mismatch", f"{pointer}.seed_sha256", "文件没有引用本次选中 Seed 的 SHA-256")

    def _validate_checkpoint_source_map(self, checkpoint: dict[str, Any], report: ValidationReport) -> None:
        raw_files = {value for value in checkpoint.get("raw_files", []) if isinstance(value, str)}
        source_urls = {value for value in checkpoint.get("source_urls", []) if isinstance(value, str)}
        mapping = checkpoint.get("source_file_map", [])
        mapped: set[str] = set()
        if checkpoint.get("status") == "ready" and not isinstance(mapping, list):
            self._error(report, "missing_source_file_map", "$.data_checkpoint.source_file_map", "ready 必须提供逐文件来源映射")
            return
        for index, item in enumerate(mapping if isinstance(mapping, list) else []):
            pointer = f"$.data_checkpoint.source_file_map[{index}]"
            if not isinstance(item, dict):
                continue
            if item.get("url") not in source_urls:
                self._error(report, "source_map_unknown_url", f"{pointer}.url", "映射 URL 未在 source_urls 中登记")
            for value in item.get("file_paths", []):
                if value not in raw_files:
                    self._error(report, "source_map_unknown_file", f"{pointer}.file_paths", f"映射了未知 raw 文件：{value}")
                if value in mapped:
                    self._error(report, "raw_file_multiple_sources", f"{pointer}.file_paths", f"raw 文件被重复映射：{value}")
                mapped.add(value)
        if checkpoint.get("status") == "ready" and mapped != raw_files:
            self._error(report, "source_map_incomplete", "$.data_checkpoint.source_file_map", f"来源映射缺少文件：{sorted(raw_files - mapped)}")

    def _validate_canonical_entity_file(self, path: Path, report: ValidationReport, pointer: str) -> None:
        try:
            payload = read_entity_groups(path)
        except StructuredDataError as error:
            self._error(report, "invalid_entity_file", pointer, str(error))
            return
        if not payload:
            self._error(report, "invalid_entity_root", pointer, "entity 文件至少需要一个实体类型")
            return
        for entity_type, records in payload.items():
            entity_pointer = f"{pointer}.{entity_type}"
            if not isinstance(entity_type, str) or not _ID_PATTERN.fullmatch(entity_type):
                self._error(report, "invalid_entity_type", entity_pointer, "实体类型必须使用小写 snake_case")
            if not isinstance(records, list) or not records:
                self._error(report, "empty_entity_records", entity_pointer, "实体值必须是非空记录数组")
                continue
            expected_fields: set[str] | None = None
            field_types: dict[str, type] = {}
            primary: str | None = None
            ids: set[str] = set()
            for index, record in enumerate(records):
                record_pointer = f"{entity_pointer}[{index}]"
                if not isinstance(record, dict) or not record:
                    self._error(report, "invalid_entity_record", record_pointer, "实体记录必须是非空对象")
                    continue
                fields = set(record)
                invalid_fields = sorted(
                    name for name in fields if not _ID_PATTERN.fullmatch(str(name))
                )
                if invalid_fields:
                    self._error(
                        report,
                        "invalid_entity_field",
                        record_pointer,
                        f"字段必须使用小写 snake_case：{invalid_fields}",
                    )
                expected_fields = fields if expected_fields is None else expected_fields
                if fields != expected_fields:
                    self._error(report, "inconsistent_entity_fields", record_pointer, f"同类记录字段必须一致；应为 {sorted(expected_fields)}")
                for name, value in record.items():
                    if value is None or not isinstance(value, (str, bool, int, float)):
                        self._error(report, "non_scalar_entity_value", f"{record_pointer}.{name}", "entity 字段必须是非空 JSON 标量")
                        continue
                    if isinstance(value, float) and not math.isfinite(value):
                        self._error(report, "non_finite_entity_number", f"{record_pointer}.{name}", "entity 数值必须是有限值")
                        continue
                    value_type = bool if isinstance(value, bool) else type(value)
                    previous = field_types.setdefault(name, value_type)
                    if previous is not value_type and not ({previous, value_type} <= {int, float}):
                        self._error(report, "inconsistent_entity_type", f"{record_pointer}.{name}", "同一字段的数据类型不一致")
                if primary is None:
                    primary = _choose_primary_id(entity_type, fields)
                if primary and primary in record:
                    key = json.dumps(record[primary], ensure_ascii=False, sort_keys=True)
                    if key in ids:
                        self._error(report, "duplicate_entity_id", f"{record_pointer}.{primary}", f"实体主键重复：{record[primary]}")
                    ids.add(key)
            if primary is None and not entity_type.endswith("_link"):
                id_fields = [name for name in (expected_fields or set()) if name.endswith("_id") or name == "id"]
                if len(id_fields) < 2:
                    self._error(report, "missing_entity_primary_key", entity_pointer, "普通实体必须有稳定主键；关系桥必须至少有两个外键")

    def _resource_map(self, values: Any, report: ValidationReport) -> dict[str, dict[str, Any]]:
        if not isinstance(values, list):
            self._error(report, "invalid_resources", "$.environment.resources", "resources 必须是数组")
            return {}
        result: dict[str, dict[str, Any]] = {}
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            resource_id = value.get("resource_id")
            if not isinstance(resource_id, str):
                continue
            if resource_id in result:
                self._error(report, "duplicate_resource_id", f"$.environment.resources[{index}]", f"resource_id 重复：{resource_id}")
            result[resource_id] = value
        return result

    def _files_for_resource(
        self,
        workspace: Path,
        resource: dict[str, Any],
        report: ValidationReport,
        resource_id: str,
    ) -> list[Path]:
        value = resource.get("path")
        if not isinstance(value, str):
            return []
        pointer = f"$.environment.resources.{resource_id}.path"
        if resource.get("storage_type") == "file_collection":
            pure = PurePosixPath(value)
            fixed: list[str] = []
            for part in pure.parts:
                if any(marker in part for marker in "*?["):
                    break
                fixed.append(part)
            if not fixed or self._workspace_path(workspace, "/".join(fixed)) is None:
                self._error(report, "unsafe_resource_glob", pointer, f"资源 glob 路径越界：{value}")
                return []
            return sorted(path for path in workspace.glob(value) if path.is_file())
        target = self._workspace_path(workspace, value)
        if target is None:
            self._error(report, "unsafe_resource_path", pointer, f"资源路径越界：{value}")
            return []
        if resource.get("storage_type") == "directory":
            if not target.is_dir():
                self._error(report, "missing_resource_directory", pointer, f"目录不存在：{value}")
                return []
            return sorted(path for path in target.rglob("*") if path.is_file())
        if not target.is_file():
            self._error(report, "missing_resource_file", pointer, f"文件不存在：{value}")
            return []
        return [target]

    def _validate_resource_contract(
        self,
        resource_id: str,
        resource: dict[str, Any],
        resources: dict[str, dict[str, Any]],
        files: list[Path],
        report: ValidationReport,
    ) -> None:
        pointer = f"$.environment.resources.{resource_id}"
        data_type = resource.get("data_type")
        sources = resource.get("source_resources")
        if data_type == "raw":
            if sources is not None or resource.get("entity_schema") is not None:
                self._error(report, "raw_has_derived_fields", pointer, "raw 不能声明 source_resources 或 entity_schema")
        elif data_type == "entity":
            if not isinstance(sources, list) or not sources:
                self._error(report, "entity_without_sources", f"{pointer}.source_resources", "entity 必须引用来源资源")
            if not isinstance(resource.get("entity_schema"), dict) or not resource["entity_schema"]:
                self._error(report, "entity_without_schema", f"{pointer}.entity_schema", "entity 必须声明实体 Schema")
        elif data_type == "derived":
            if not isinstance(sources, list) or not sources:
                self._error(report, "derived_without_sources", f"{pointer}.source_resources", "derived 必须引用来源资源")
            if resource.get("entity_schema") is not None:
                self._error(report, "derived_has_entity_schema", pointer, "derived 不能声明 entity_schema")
        elif data_type == "output":
            if resource.get("storage_type") != "directory" or resource.get("writable") is not True:
                self._error(report, "invalid_output_resource", pointer, "output 必须是 writable directory")
        for source_id in sources if isinstance(sources, list) else []:
            if source_id not in resources:
                self._error(report, "unknown_source_resource", f"{pointer}.source_resources", f"引用未知资源：{source_id}")
        if data_type in {"raw", "entity", "derived"} and not files:
            self._error(report, "empty_business_resource", pointer, "业务资源必须覆盖至少一个文件")

    def _load_declared_entities(
        self,
        resource_id: str,
        resource: dict[str, Any],
        files: list[Path],
        inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        schema = resource.get("entity_schema")
        if not isinstance(schema, dict):
            return
        for path in files:
            format_name = resource.get("format")
            entity_name = (
                next(iter(schema))
                if format_name in {"jsonl", "csv", "parquet"} and len(schema) == 1
                else None
            )
            try:
                payload = read_entity_groups(
                    path,
                    entity_name=entity_name,
                    declared_format=(format_name if isinstance(format_name, str) else None),
                )
            except StructuredDataError as error:
                self._error(
                    report,
                    "invalid_declared_entity_file",
                    f"$.workspace.{path.name}",
                    str(error),
                )
                continue
            if set(payload) != set(schema):
                self._error(report, "entity_type_mismatch", f"$.environment.resources.{resource_id}.entity_schema", f"声明实体与文件根 key 不一致：声明 {sorted(schema)}，文件 {sorted(payload)}")
            for entity_type, definition in schema.items():
                records = payload.get(entity_type, [])
                if not isinstance(records, list):
                    continue
                fields = self._declared_fields(definition)
                for index, record in enumerate(records):
                    if not isinstance(record, dict):
                        continue
                    missing = sorted(set(fields) - set(record))
                    extra = sorted(set(record) - set(fields))
                    if missing or extra:
                        self._error(report, "entity_schema_field_mismatch", f"$.workspace.{path.name}.{entity_type}[{index}]", f"缺少 {missing}，多出 {extra}")
                    for name, expected in fields.items():
                        if name in record and not self._matches_type(record[name], expected):
                            self._error(report, "entity_schema_type_mismatch", f"$.workspace.{path.name}.{entity_type}[{index}].{name}", f"应为 {expected}")
                entry = inventory.setdefault(entity_type, {"records": [], "fields": set(), "resource_ids": set()})
                entry["records"].extend(item for item in records if isinstance(item, dict))
                entry["fields"].update(fields)
                entry["resource_ids"].add(resource_id)

    def _validate_resource_graph(self, resources: dict[str, dict[str, Any]], report: ValidationReport) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(resource_id: str) -> None:
            if resource_id in visiting:
                self._error(report, "resource_lineage_cycle", "$.environment.resources", f"资源血缘存在环：{resource_id}")
                return
            if resource_id in visited:
                return
            visiting.add(resource_id)
            for source_id in resources[resource_id].get("source_resources", []):
                if source_id in resources:
                    visit(source_id)
            visiting.remove(resource_id)
            visited.add(resource_id)

        for resource_id in resources:
            visit(resource_id)

    def _validate_entity_file_references(
        self,
        workspace: Path,
        resources: dict[str, dict[str, Any]],
        owners: dict[str, list[str]],
        entity_inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """验证 Entity 的 *_file_path 指向真实且有血缘的文件资源。"""

        for entity_type, entry in entity_inventory.items():
            file_fields = {
                field
                for field in entry.get("fields", set())
                if field == "file_path" or str(field).endswith("_file_path")
            }
            if not file_fields:
                continue
            entity_resource_ids = {
                value
                for value in entry.get("resource_ids", set())
                if value in resources
            }
            declared_sources = {
                source_id
                for resource_id in entity_resource_ids
                for source_id in resources[resource_id].get("source_resources", [])
                if isinstance(source_id, str)
            }
            for index, record in enumerate(entry.get("records", [])):
                for field in file_fields:
                    if field not in record:
                        continue
                    value = record[field]
                    target = self._workspace_path(workspace, value)
                    pointer = f"$.entities.{entity_type}[{index}].{field}"
                    if target is None:
                        self._error(
                            report,
                            "unsafe_entity_file_reference",
                            pointer,
                            f"文件引用必须是 workspace 下的安全相对路径：{value}",
                        )
                        continue
                    relative = target.relative_to(workspace).as_posix()
                    file_owners = owners.get(relative, [])
                    if not target.is_file() or not file_owners:
                        self._error(
                            report,
                            "missing_entity_file_reference",
                            pointer,
                            f"文件索引引用的资源不存在或未声明：{relative}",
                        )
                        continue
                    valid_owners = {
                        resource_id
                        for resource_id in file_owners
                        if resources.get(resource_id, {}).get("data_type")
                        in {"raw", "derived"}
                    }
                    if not valid_owners:
                        self._error(
                            report,
                            "invalid_entity_file_resource",
                            pointer,
                            f"file_path 只能指向 raw/derived 文件资源：{relative}",
                        )
                    elif declared_sources.isdisjoint(valid_owners):
                        self._error(
                            report,
                            "entity_file_source_missing",
                            pointer,
                            (
                                f"实体资源的 source_resources 没有引用文件 {relative} "
                                f"所属资源：{sorted(valid_owners)}"
                            ),
                        )

    def _validate_sources(
        self,
        path: Path,
        workspace: Path,
        resources: dict[str, dict[str, Any]],
        resource_files: dict[str, list[Path]],
        checkpoint: dict[str, Any],
        report: ValidationReport,
    ) -> list[dict[str, Any]]:
        payload = self._load_json(path, report, "$.provenance")
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "sources"}:
            self._error(report, "invalid_sources_root", "$.provenance", "sources.json 顶层只能有 schema_version 和 sources")
            return []
        values = payload.get("sources")
        if not isinstance(values, list):
            self._error(report, "invalid_sources", "$.provenance.sources", "sources 必须是数组")
            return []
        raw_resources = {key for key, item in resources.items() if item.get("data_type") == "raw"}
        expected_files = {
            path.relative_to(workspace).as_posix()
            for resource_id in raw_resources
            for path in resource_files.get(resource_id, [])
        }
        checkpoint_urls = {value for value in checkpoint.get("source_urls", []) if isinstance(value, str)}
        covered_resources: set[str] = set()
        covered_files: set[str] = set()
        ids: set[str] = set()
        for index, source in enumerate(values):
            pointer = f"$.provenance.sources[{index}]"
            if not isinstance(source, dict):
                self._error(report, "invalid_source", pointer, "来源必须是对象")
                continue
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not _ID_PATTERN.fullmatch(source_id) or source_id in ids:
                self._error(report, "invalid_source_id", f"{pointer}.source_id", "source_id 必须是唯一 snake_case")
            else:
                ids.add(source_id)
            url = source.get("url")
            parsed = urlparse(str(url or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or url not in checkpoint_urls:
                self._error(report, "unverified_source_url", f"{pointer}.url", "URL 必须是 checkpoint 中实际访问过的 HTTP/HTTPS 来源")
            self._validate_timestamp(source.get("retrieved_at"), f"{pointer}.retrieved_at", report)
            resource_ids = source.get("resource_ids", [])
            allowed_paths: set[Path] = set()
            for resource_id in resource_ids if isinstance(resource_ids, list) else []:
                if resource_id not in raw_resources:
                    self._error(report, "source_non_raw_resource", f"{pointer}.resource_ids", f"来源只能关联 raw：{resource_id}")
                else:
                    covered_resources.add(resource_id)
                    allowed_paths.update(path.resolve() for path in resource_files.get(resource_id, []))
            for item in source.get("files", []) if isinstance(source.get("files"), list) else []:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    continue
                target = self._workspace_path(workspace, item["path"])
                if target is None or not target.is_file() or target.resolve() not in allowed_paths:
                    self._error(report, "source_file_mismatch", f"{pointer}.files", f"来源文件不存在或不属于声明资源：{item['path']}")
                    continue
                relative = target.relative_to(workspace).as_posix()
                if relative in covered_files:
                    self._error(report, "raw_file_multiple_provenance", f"{pointer}.files", f"raw 文件重复溯源：{relative}")
                covered_files.add(relative)
                digest = item.get("sha256")
                if digest is not None and digest != self._sha256(target):
                    self._error(report, "source_sha256_mismatch", f"{pointer}.files", f"哈希不一致：{relative}")
        for value in sorted(raw_resources - covered_resources):
            self._error(report, "untraced_raw_resource", "$.provenance.sources", f"raw 资源没有来源：{value}")
        for value in sorted(expected_files - covered_files):
            self._error(report, "untraced_raw_file", "$.provenance.sources", f"raw 文件没有来源：{value}")
        return [item for item in values if isinstance(item, dict)]

    def _validate_research_report(
        self,
        path: Path,
        resources: dict[str, dict[str, Any]],
        entities: dict[str, dict[str, Any]],
        report: ValidationReport,
        *,
        expected_representation_mode: str | None = None,
    ) -> dict[str, Any] | None:
        payload = self._load_json(path, report, "$.research_report")
        if not isinstance(payload, dict):
            return None
        self._validate_schema(self.report_validator, payload, report, "$.research_report", "research_report_schema")
        self._validate_seed_reference(payload, report, "$.research_report")
        if (
            expected_representation_mode is not None
            and payload.get("representation_mode") != expected_representation_mode
        ):
            self._error(
                report,
                "representation_mode_mismatch",
                "$.research_report.representation_mode",
                (
                    "research report 必须沿用 source plan 的数据形态判断："
                    f"{expected_representation_mode}"
                ),
            )

        expected_tools = {
            str(item.get("name"))
            for item in self.seed.get("init_ref_tools", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        covered: set[str] = set()
        coverage_ids: set[str] = set()
        for index, item in enumerate(payload.get("coverage", [])):
            if not isinstance(item, dict):
                continue
            pointer = f"$.research_report.coverage[{index}]"
            coverage_id = item.get("coverage_id")
            if coverage_id in coverage_ids:
                self._error(report, "duplicate_coverage_id", f"{pointer}.coverage_id", f"coverage_id 重复：{coverage_id}")
            if isinstance(coverage_id, str):
                coverage_ids.add(coverage_id)
            names = item.get("reference_tools", [])
            for name in names if isinstance(names, list) else []:
                if name not in expected_tools:
                    self._error(report, "unknown_reference_tool", f"{pointer}.reference_tools", f"Seed 中不存在参考工具：{name}")
                if name in covered:
                    self._error(report, "reference_tool_multiple_coverage", f"{pointer}.reference_tools", f"参考工具重复覆盖：{name}")
                covered.add(name)
            status = item.get("status")
            evidence = item.get("evidence", [])
            if status in {"supported", "partial"} and not evidence:
                self._error(report, "supported_without_evidence", f"{pointer}.evidence", "supported/partial 必须提供真实实体证据")
            self._validate_evidence(evidence, resources, entities, report, f"{pointer}.evidence")
        if covered != expected_tools:
            self._error(report, "reference_tool_coverage_mismatch", "$.research_report.coverage", f"参考工具覆盖不完整；缺少 {sorted(expected_tools - covered)}")

        self._validate_evidence(
            [evidence for item in payload.get("extensions", []) if isinstance(item, dict) for evidence in item.get("evidence", [])],
            resources,
            entities,
            report,
            "$.research_report.extensions",
        )
        self._validate_report_relations(payload.get("relations"), entities, report)
        return payload

    def _validate_evidence(
        self,
        values: Any,
        resources: dict[str, dict[str, Any]],
        entities: dict[str, dict[str, Any]],
        report: ValidationReport,
        pointer: str,
    ) -> None:
        if not isinstance(values, list):
            return
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            item_pointer = f"{pointer}[{index}]"
            resource_id = item.get("resource_id")
            if resource_id not in resources:
                self._error(report, "unknown_evidence_resource", f"{item_pointer}.resource_id", f"未知资源：{resource_id}")
            entity_types = item.get("entity_types", [])
            for entity_type in entity_types if isinstance(entity_types, list) else []:
                if entity_type not in entities:
                    self._error(report, "unknown_evidence_entity", f"{item_pointer}.entity_types", f"未知实体：{entity_type}")
                elif resource_id not in entities[entity_type]["resource_ids"]:
                    self._error(report, "evidence_resource_mismatch", item_pointer, f"实体 {entity_type} 不属于资源 {resource_id}")
            for field_ref in item.get("field_refs", []):
                if not isinstance(field_ref, str) or "." not in field_ref:
                    continue
                entity_type, field_name = field_ref.split(".", 1)
                if entity_type not in entities or field_name not in entities[entity_type]["fields"]:
                    self._error(report, "unknown_evidence_field", f"{item_pointer}.field_refs", f"未知字段：{field_ref}")

    def _validate_report_relations(self, values: Any, entities: dict[str, dict[str, Any]], report: ValidationReport) -> None:
        if not isinstance(values, list):
            return
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            pointer = f"$.research_report.relations[{index}]"
            source = entities.get(str(item.get("from_entity")))
            target = entities.get(str(item.get("to_entity")))
            source_field = item.get("field")
            target_field = item.get("target_field")
            if source is None or target is None:
                self._error(report, "unknown_relation_entity", pointer, "关系引用了未知实体")
                continue
            target_values = {self._value_key(record.get(target_field)) for record in target["records"] if record.get(target_field) is not None}
            source_values = [self._value_key(record.get(source_field)) for record in source["records"] if record.get(source_field) is not None]
            if not source_values or len(target_values) != len([record for record in target["records"] if record.get(target_field) is not None]):
                self._error(report, "relation_target_not_unique", pointer, "关系目标字段必须存在且唯一")
            missing = [value for value in source_values if value not in target_values]
            if missing:
                self._error(report, "relation_not_closed", pointer, f"关系存在 {len(missing)} 个缺失目标")

    def _validate_package_layout(self, package_root: Path, report: ValidationReport) -> None:
        allowed_files = {"environment.json", "validation.json", "validation_errors.json"}
        allowed_dirs = {".datagen", "provenance", "workspace"}
        if not package_root.is_dir():
            self._error(report, "missing_package", "$", f"环境目录不存在：{package_root}")
            return
        for path in package_root.iterdir():
            if path.is_file() and path.name not in allowed_files:
                self._error(report, "unexpected_package_file", "$", f"环境根目录包含旁路文件：{path.name}")
            elif path.is_dir() and path.name not in allowed_dirs:
                self._error(report, "unexpected_package_directory", "$", f"环境根目录包含旁路目录：{path.name}")

    @staticmethod
    def _business_files(workspace: Path) -> set[str]:
        result: set[str] = set()
        for bucket in ("raw", "entities", "derived"):
            root = workspace / bucket
            if root.is_dir():
                result.update(path.relative_to(workspace).as_posix() for path in root.rglob("*") if path.is_file())
        return result

    @staticmethod
    def _workspace_path(workspace: Path, value: Any) -> Path | None:
        if not isinstance(value, str):
            return None
        pure = PurePosixPath(value)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            return None
        target = workspace.joinpath(*pure.parts).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            return None
        return target

    @staticmethod
    def _declared_fields(definition: Any) -> dict[str, str]:
        if not isinstance(definition, dict):
            return {}
        fields = definition.get("fields")
        if not isinstance(fields, dict):
            return {}
        return {
            name: str(spec.get("type"))
            for name, spec in fields.items()
            if isinstance(name, str) and isinstance(spec, dict) and isinstance(spec.get("type"), str)
        }

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        return False

    @staticmethod
    def _validate_timestamp(value: Any, pointer: str, report: ValidationReport) -> None:
        if not isinstance(value, str):
            EnvironmentPackageValidator._error(report, "invalid_timestamp", pointer, "时间必须是带时区 ISO 8601 字符串")
            return
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            EnvironmentPackageValidator._error(report, "invalid_timestamp", pointer, "时间不是合法 ISO 8601")
            return
        if parsed.tzinfo is None:
            EnvironmentPackageValidator._error(report, "timezone_required", pointer, "时间必须包含时区")

    @staticmethod
    def _validate_schema(
        validator: Draft202012Validator,
        payload: object,
        report: ValidationReport,
        pointer: str,
        code: str,
    ) -> None:
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path)):
            child = pointer
            for part in error.absolute_path:
                child += f"[{part}]" if isinstance(part, int) else f".{part}"
            EnvironmentPackageValidator._error(report, code, child, error.message)

    @staticmethod
    def _load_json(path: Path, report: ValidationReport, pointer: str) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            EnvironmentPackageValidator._error(report, "missing_json_file", pointer, f"缺少文件：{path}")
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            EnvironmentPackageValidator._error(report, "invalid_json_file", pointer, f"JSON 无法读取：{path}: {error}")
        return None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _value_key(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _error(report: ValidationReport, code: str, path: str, message: str) -> None:
        report.errors.append(ValidationIssue(code, path, message))
