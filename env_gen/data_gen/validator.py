from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from env_gen.data_gen.policy import (
    ResearchPolicy,
    compile_research_request,
    semantic_match_score,
    semantic_tokens,
    request_sha256,
)
from env_gen.data_gen.analysis.record_extraction import (
    _choose_primary_id as _metadata_choose_primary_id,
    _is_bridge_entity as _metadata_is_bridge_entity,
    _is_identifier_field as _metadata_is_identifier_field,
    _is_relation_key_field,
    _unique_key_fields as _metadata_unique_key_fields,
)


INTERNAL_SCHEMA_DIR = Path(__file__).resolve().parent / "internal_schemas"


@dataclass(frozen=True)
class ValidationIssue:
    """一条可以直接反馈给研究 Agent 的确定性错误。"""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class ValidationReport:
    """环境数据包校验结果。"""

    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "statistics": self.statistics,
        }


class EnvironmentPackageValidator:
    """校验 DataGen 产出的 environment.json、workspace 和来源记录。"""

    _SOURCE_TYPES = {
        "official_api",
        "official_repository",
        "official_dataset",
        "secondary_source",
    }
    _GLOB_MARKERS = {"*", "?", "["}

    def __init__(
        self,
        schema_path: Path,
        *,
        seed: dict[str, Any] | None = None,
        research_policy: ResearchPolicy | None = None,
        research_report_schema_path: Path | None = None,
        internal_schema_dir: Path | None = None,
    ) -> None:
        self.schema_path = schema_path.resolve()
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        if not isinstance(self.schema, dict) or not self.schema.get("$schema"):
            # 外层文件是给 Agent 阅读的契约结构示例；实际 Draft Schema
            # 位于同目录的 validation/，这里兼容直接传入外层路径的调用者。
            candidate = self.schema_path.parent / "validation" / self.schema_path.name
            if not candidate.is_file():
                raise ValueError(
                    f"{self.schema_path} 不是 JSON Schema，且找不到对应校验文件：{candidate}"
                )
            self.schema_path = candidate.resolve()
            self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.schema_validator = Draft202012Validator(self.schema)
        self.research_policy = research_policy or ResearchPolicy()
        self.research_request = (
            compile_research_request(seed, self.research_policy) if seed is not None else None
        )
        self.internal_schema_dir = (internal_schema_dir or INTERNAL_SCHEMA_DIR).resolve()
        report_schema_path = research_report_schema_path or (
            self.internal_schema_dir / "research_report.schema.json"
        )
        checkpoint_schema_path = self.internal_schema_dir / "data_checkpoint.schema.json"
        checkpoint_schema = json.loads(checkpoint_schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(checkpoint_schema)
        self.data_checkpoint_validator = Draft202012Validator(checkpoint_schema)
        request_schema_path = self.internal_schema_dir / "research_request.schema.json"
        self.research_request_validator: Draft202012Validator | None = None
        if request_schema_path.is_file():
            request_schema = json.loads(request_schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(request_schema)
            self.research_request_validator = Draft202012Validator(request_schema)
        self.research_report_validator: Draft202012Validator | None = None
        if self.research_request is not None:
            report_schema = json.loads(report_schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(report_schema)
            self.research_report_validator = Draft202012Validator(report_schema)

    def validate_data_checkpoint(self, package_root: Path) -> ValidationReport:
        """校验采集阶段提交点，确保第二阶段只处理真实已落盘文件。"""

        report = ValidationReport()
        if self.research_request is None:
            return report
        package_root = package_root.resolve()
        checkpoint_path = package_root / "provenance" / "data_checkpoint.json"
        checkpoint = self._load_json(checkpoint_path, report, "$.data_checkpoint")
        if not isinstance(checkpoint, dict):
            return report
        for error in sorted(
            self.data_checkpoint_validator.iter_errors(checkpoint),
            key=lambda item: list(item.path),
        ):
            pointer = "$.data_checkpoint"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            self._error(report, "data_checkpoint_schema", pointer, error.message)
        if checkpoint.get("request_sha256") != request_sha256(self.research_request):
            self._error(
                report,
                "data_checkpoint_request_mismatch",
                "$.data_checkpoint.request_sha256",
                "data_checkpoint 没有引用本次 research_request",
            )
        workspace_root = package_root / "workspace"
        listed: set[str] = set()
        checkpoint_file_counts = {"raw_files": 0, "entity_files": 0, "derived_files": 0}
        checkpoint_raw_bytes = 0
        checkpoint_derived_bytes = 0
        for field_name in ("raw_files", "entity_files", "derived_files"):
            values = checkpoint.get(field_name, [])
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                if not isinstance(value, str):
                    continue
                target = self._resolve_workspace_path(workspace_root, value)
                if target is None or not target.is_file():
                    self._error(
                        report,
                        "checkpoint_missing_file",
                        f"$.data_checkpoint.{field_name}[{index}]",
                        f"提交点列出的文件不存在或越过 workspace：{value}",
                    )
                else:
                    checkpoint_file_counts[field_name] += 1
                    if field_name == "raw_files":
                        checkpoint_raw_bytes += target.stat().st_size
                    elif field_name == "derived_files":
                        checkpoint_derived_bytes += target.stat().st_size
                    self._validate_checkpoint_file(
                        target,
                        field_name,
                        f"$.data_checkpoint.{field_name}[{index}]",
                        report,
                    )
                normalized = PurePosixPath(value).as_posix()
                if normalized != value:
                    self._error(
                        report,
                        "non_canonical_checkpoint_path",
                        f"$.data_checkpoint.{field_name}[{index}]",
                        f"提交点路径必须是规范 POSIX 相对路径：{value}",
                    )
                parts = PurePosixPath(normalized).parts
                expected_root = {
                    "raw_files": "raw",
                    "entity_files": "entities",
                    "derived_files": "derived",
                }[field_name]
                if not parts or parts[0] != expected_root:
                    self._error(
                        report,
                        "checkpoint_wrong_directory",
                        f"$.data_checkpoint.{field_name}[{index}]",
                        f"{field_name} 必须位于 workspace/{expected_root}/ 下，实际为：{value}",
                    )
                if normalized in listed:
                    self._error(
                        report,
                        "duplicate_checkpoint_file",
                        f"$.data_checkpoint.{field_name}[{index}]",
                        f"提交点重复列出文件：{value}",
                    )
                listed.add(normalized)
                if self._is_protocol_metadata_path(normalized):
                    self._error(
                        report,
                        "metadata_file_in_business_bucket",
                        f"$.data_checkpoint.{field_name}[{index}]",
                        f"协议/来源元数据不能登记为业务文件：{value}；请放在 provenance/ 下",
                    )

        # checkpoint 是数据阶段的封闭交付清单。若 Agent 在 raw/entities/derived
        # 下留下未提交文件，后续 metadata 阶段就可能把它们悄悄纳入环境；这会
        # 破坏来源、覆盖和可复现性，因此必须在进入下一阶段前拒绝。
        for relative in self._business_workspace_files(workspace_root):
            if relative not in listed:
                self._error(
                    report,
                    "unlisted_checkpoint_file",
                    "$.data_checkpoint",
                    f"workspace 业务文件未在 checkpoint 登记：{relative}",
                )
        if checkpoint_file_counts["raw_files"] > self.research_policy.max_raw_files:
            self._error(
                report,
                "checkpoint_raw_file_budget_exceeded",
                "$.data_checkpoint.raw_files",
                f"采集阶段原始文件数超过预算：{checkpoint_file_counts['raw_files']} > {self.research_policy.max_raw_files}",
            )
        if checkpoint_raw_bytes > self.research_policy.max_download_bytes:
            self._error(
                report,
                "checkpoint_download_budget_exceeded",
                "$.data_checkpoint.raw_files",
                f"采集阶段原始数据超过预算：{checkpoint_raw_bytes} > {self.research_policy.max_download_bytes} bytes",
            )
        if checkpoint_derived_bytes > self.research_policy.max_derived_bytes:
            self._error(
                report,
                "checkpoint_derived_budget_exceeded",
                "$.data_checkpoint.derived_files",
                f"采集阶段派生文件超过预算：{checkpoint_derived_bytes} > {self.research_policy.max_derived_bytes} bytes",
            )
        checkpoint_workspace_bytes = sum(
            path.stat().st_size
            for path in workspace_root.rglob("*")
            if path.is_file()
        ) if workspace_root.is_dir() else 0
        if checkpoint_workspace_bytes > self.research_policy.max_workspace_bytes:
            self._error(
                report,
                "checkpoint_workspace_budget_exceeded",
                "$.workspace",
                f"采集阶段 workspace 超过预算：{checkpoint_workspace_bytes} > {self.research_policy.max_workspace_bytes} bytes",
            )
        self._validate_source_file_map(
            checkpoint,
            listed_raw_files={
                value
                for value in checkpoint.get("raw_files", [])
                if isinstance(value, str)
            },
            workspace_root=workspace_root,
            report=report,
        )
        if checkpoint.get("status") == "insufficient_public_data":
            self._error(
                report,
                "insufficient_public_data",
                "$.data_checkpoint.status",
                str(checkpoint.get("summary") or "核心公开数据不足，停止生成"),
            )
        elif checkpoint.get("status") == "ready" and not checkpoint.get("raw_files"):
            self._error(
                report,
                "checkpoint_without_raw_data",
                "$.data_checkpoint.raw_files",
                "ready 提交点至少需要一个真实 raw 文件",
            )
        elif checkpoint.get("status") == "ready" and not checkpoint.get("source_urls"):
            self._error(
                report,
                "checkpoint_without_sources",
                "$.data_checkpoint.source_urls",
                "ready 提交点至少需要一个实际访问的来源 URL",
            )
        return report

    def _validate_source_file_map(
        self,
        checkpoint: dict[str, Any],
        *,
        listed_raw_files: set[str],
        workspace_root: Path,
        report: ValidationReport,
    ) -> None:
        """校验可选的来源到文件映射，防止 provenance 过度泛化。"""

        mapping = checkpoint.get("source_file_map")
        if mapping is None:
            if checkpoint.get("status") == "ready" and listed_raw_files:
                self._error(
                    report,
                    "missing_source_file_map",
                    "$.data_checkpoint.source_file_map",
                    "ready 提交点必须把每个 raw 文件映射到一个实际访问的来源 URL",
                )
            return
        if not isinstance(mapping, list):
            return
        source_urls = {
            value for value in checkpoint.get("source_urls", []) if isinstance(value, str)
        }
        mapped_files: set[str] = set()
        mapped_urls: set[str] = set()
        for index, item in enumerate(mapping):
            pointer = f"$.data_checkpoint.source_file_map[{index}]"
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str):
                if url not in source_urls:
                    self._error(
                        report,
                        "source_map_url_not_listed",
                        f"{pointer}.url",
                        f"source_file_map URL 未出现在 source_urls：{url}",
                    )
                if url in mapped_urls:
                    self._error(
                        report,
                        "duplicate_source_map_url",
                        f"{pointer}.url",
                        f"source_file_map 不应重复声明同一 URL：{url}",
                    )
                mapped_urls.add(url)
            paths = item.get("file_paths")
            if not isinstance(paths, list):
                continue
            for path_index, value in enumerate(paths):
                path_pointer = f"{pointer}.file_paths[{path_index}]"
                if not isinstance(value, str):
                    continue
                normalized = PurePosixPath(value).as_posix()
                if normalized != value or not normalized.startswith("raw/"):
                    self._error(
                        report,
                        "invalid_source_map_path",
                        path_pointer,
                        f"来源映射路径必须是 workspace 下的 raw/ 相对路径：{value}",
                    )
                    continue
                if value not in listed_raw_files:
                    self._error(
                        report,
                        "source_map_file_not_listed",
                        path_pointer,
                        f"来源映射文件未登记在 raw_files：{value}",
                    )
                target = self._resolve_workspace_path(workspace_root, value)
                if target is None or not target.is_file():
                    self._error(
                        report,
                        "source_map_missing_file",
                        path_pointer,
                        f"来源映射文件不存在：{value}",
                    )
                if value in mapped_files:
                    self._error(
                        report,
                        "duplicate_source_map_file",
                        path_pointer,
                        f"一个 raw 文件只能映射到一个来源 URL：{value}",
                    )
                mapped_files.add(value)
        missing = sorted(listed_raw_files - mapped_files)
        if missing:
            self._error(
                report,
                "incomplete_source_file_map",
                "$.data_checkpoint.source_file_map",
                f"source_file_map 未覆盖所有 raw 文件：{missing[:8]}",
            )

    def _validate_checkpoint_file(
        self,
        path: Path,
        field_name: str,
        pointer: str,
        report: ValidationReport,
    ) -> None:
        """在元数据阶段前检查提交点文件不是空壳，并解析常见结构化格式。"""

        try:
            if path.stat().st_size == 0:
                self._error(report, "empty_checkpoint_file", pointer, f"提交点文件为空：{path.name}")
                return
            suffix = path.suffix.lower()
            if suffix in {".json", ".sarif"}:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if field_name == "raw_files" and self._is_error_payload(payload):
                    self._error(
                        report,
                        "raw_error_payload",
                        pointer,
                        f"raw 文件是来源错误响应，不是可用业务数据：{path.name}",
                    )
                if field_name == "entity_files" and not self._entity_json_shape_valid(payload):
                    self._error(
                        report,
                        "invalid_checkpoint_entity_file",
                        pointer,
                        f"实体提交文件必须是纯实体记录数组，且每类记录有稳定 ID：{path.name}",
                    )
                elif payload in ({}, [], None, ""):
                    self._error(report, "empty_checkpoint_payload", pointer, f"结构化文件没有内容：{path.name}")
            elif suffix == ".jsonl":
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not lines:
                    self._error(report, "empty_checkpoint_payload", pointer, f"JSONL 文件没有记录：{path.name}")
                for line in lines:
                    json.loads(line)
            elif suffix == ".csv":
                with path.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.reader(stream)
                    header = next(reader, None)
                    rows = next(reader, None)
                if not header or any(not name.strip() for name in header):
                    self._error(report, "invalid_checkpoint_csv", pointer, f"CSV 缺少有效表头：{path.name}")
                elif rows is None:
                    self._error(report, "empty_checkpoint_payload", pointer, f"CSV 没有数据行：{path.name}")
            elif suffix in {".xml", ".atom"}:
                root = ET.fromstring(path.read_text(encoding="utf-8"))
                if not list(root):
                    self._error(report, "empty_checkpoint_payload", pointer, f"XML 文件没有元素记录：{path.name}")
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, ET.ParseError) as error:
            self._error(report, "invalid_checkpoint_content", pointer, f"提交点文件无法按内容读取：{path.name}: {error}")

    @staticmethod
    def _contains_entity_records(payload: Any) -> bool:
        """识别常见实体 JSON 布局中的至少一条对象记录。"""

        if isinstance(payload, list):
            return any(isinstance(item, dict) for item in payload)
        if isinstance(payload, dict):
            return any(
                isinstance(value, list) and any(isinstance(item, dict) for item in value)
                for value in payload.values()
            )
        return False

    @classmethod
    def _is_error_payload(cls, payload: Any) -> bool:
        """识别常见 API 错误响应，避免非空错误 JSON 伪装成数据。"""

        if isinstance(payload, dict):
            has_error = any(
                key in payload and payload[key] not in (None, "", [], {})
                for key in ("error", "errors", "exception", "traceback")
            )
            return has_error and not cls._contains_entity_records(payload)
        if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
            error_keys = {"message", "error", "errors", "exception"}
            return (
                any(error_keys.intersection(item) for item in payload)
                and not any(
                    any(
                        field == "id"
                        or field == "entity_id"
                        or field.endswith("_id")
                        for field in item
                    )
                    for item in payload
                )
            )
        return False

    @staticmethod
    def _entity_json_shape_valid(payload: Any) -> bool:
        """检查 entity JSON 没有混入 collection/metadata 等非记录对象。"""

        if isinstance(payload, list):
            groups = {"_": payload}
        elif isinstance(payload, dict):
            if not payload or any(not isinstance(value, list) for value in payload.values()):
                return False
            groups = payload
        else:
            return False
        for records in groups.values():
            if not records or any(not isinstance(record, dict) for record in records):
                return False
            if not any(
                field == "id" or field == "entity_id" or field.endswith("_id")
                for record in records
                for field in record
            ):
                return False
        return True

    def validate(self, package_root: Path) -> ValidationReport:
        package_root = package_root.resolve()
        report = ValidationReport()
        environment_path = package_root / "environment.json"
        workspace_root = package_root / "workspace"
        provenance_path = package_root / "provenance" / "sources.json"

        research_result = self._validate_research_files(package_root, report)
        checkpoint_report = self.validate_data_checkpoint(package_root)
        report.errors.extend(checkpoint_report.errors)
        # 请求哈希或报告 Schema 错误不能遮蔽其它可确定性检查。旧产物经常
        # 只因为协议升级而 hash 不一致，但其中的实体、关系和覆盖仍然可以
        # 审计；只有确实读取不到报告/环境时才跳过相应检查。
        if isinstance(research_result, dict) and research_result.get("status") == "insufficient_public_data":
            gaps = research_result.get("gaps", [])
            gap_summary = "; ".join(
                f"{item.get('requirement_id', 'unknown')}: {item.get('reason', '')}"
                for item in gaps
                if isinstance(item, dict)
            )
            self._error(
                report,
                "insufficient_public_data",
                "$.research_report.status",
                str(
                    research_result.get("summary")
                    or "公开真实数据不足，环境未生成"
                ) + (f"；{gap_summary}" if gap_summary else ""),
            )
            report.statistics = {"research_status": "insufficient_public_data"}
            return report

        environment = self._load_json(environment_path, report, "$.environment")
        if not isinstance(environment, dict):
            if environment is not None:
                self._error(report, "invalid_environment", "$.environment", "environment.json 根节点必须是对象")
            return report

        for error in sorted(self.schema_validator.iter_errors(environment), key=lambda item: list(item.path)):
            pointer = "$.environment"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            self._error(report, "schema_violation", pointer, error.message)

        if not workspace_root.is_dir():
            self._error(report, "missing_workspace", "$.workspace", "缺少 workspace 目录")

        resources = environment.get("resources")
        if not isinstance(resources, list):
            resources = []
        resource_map = self._resource_map(resources, report)
        self._validate_resource_references(environment, resource_map, report)

        entity_counts: dict[str, int] = {}
        resource_files: dict[str, list[Path]] = {}
        if workspace_root.is_dir():
            for index, resource in enumerate(resources):
                if not isinstance(resource, dict):
                    continue
                files = self._validate_resource(
                    workspace_root,
                    resource,
                    f"$.environment.resources[{index}]",
                    report,
                    entity_counts,
                )
                resource_id = resource.get("resource_id")
                if isinstance(resource_id, str):
                    resource_files[resource_id] = files

        self._validate_workspace_inventory(workspace_root, resource_map, resource_files, report)

        sources = self._validate_provenance(
            provenance_path,
            workspace_root,
            resource_map,
            resource_files,
            report,
        )
        entity_inventory = self._load_entity_inventory(resources, resource_files, report)
        if isinstance(research_result, dict) and research_result.get("status") == "ready":
            self._validate_research_coverage(
                research_result,
                workspace_root,
                resource_map,
                resource_files,
                entity_counts,
                entity_inventory,
                sources,
                report,
            )
        data_type_counts: dict[str, int] = {}
        for resource in resources:
            if isinstance(resource, dict) and isinstance(resource.get("data_type"), str):
                data_type = resource["data_type"]
                data_type_counts[data_type] = data_type_counts.get(data_type, 0) + 1

        research_statistics = dict(report.statistics)
        report.statistics = {
            "environment_id": environment.get("environment_id", ""),
            "resources": len(resource_map),
            "resources_by_data_type": dict(sorted(data_type_counts.items())),
            "workspace_files": sum(1 for item in workspace_root.rglob("*") if item.is_file())
            if workspace_root.is_dir()
            else 0,
            "entity_types": len(entity_counts),
            "entity_records": dict(sorted(entity_counts.items())),
            "sources": len(sources),
            "research_status": research_result.get("status")
            if isinstance(research_result, dict)
            else "not_required",
            **research_statistics,
        }
        return report

    def finalize_provenance(self, package_root: Path) -> None:
        """由程序为 provenance 中列出的真实文件计算 SHA-256。"""

        package_root = package_root.resolve()
        workspace_root = (package_root / "workspace").resolve()
        provenance_path = package_root / "provenance" / "sources.json"
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                continue
            for file_item in source.get("files", []):
                if not isinstance(file_item, dict) or not isinstance(file_item.get("path"), str):
                    continue
                file_path = self._resolve_workspace_path(workspace_root, file_item["path"])
                if file_path is None or not file_path.is_file():
                    continue
                digest = hashlib.sha256()
                with file_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                file_item["sha256"] = digest.hexdigest()
        provenance_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _validate_research_files(
        self,
        package_root: Path,
        report: ValidationReport,
    ) -> dict[str, Any] | None:
        """校验旁路调研请求和报告；环境协议本身不混入生成元数据。"""

        if self.research_request is None:
            return None
        request_path = package_root / "provenance" / "research_request.json"
        observed_request = self._load_json(request_path, report, "$.research_request")
        if isinstance(observed_request, dict) and self.research_request_validator is not None:
            for error in sorted(
                self.research_request_validator.iter_errors(observed_request),
                key=lambda item: list(item.path),
            ):
                pointer = "$.research_request"
                for part in error.absolute_path:
                    pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
                self._error(report, "research_request_schema", pointer, error.message)
        if observed_request != self.research_request:
            self._error(
                report,
                "research_request_mismatch",
                "$.research_request",
                "research_request.json 必须与生成器编译的请求完全一致",
            )

        result_path = package_root / "provenance" / "research_report.json"
        result = self._load_json(result_path, report, "$.research_report")
        if not isinstance(result, dict):
            return None
        assert self.research_report_validator is not None
        for error in sorted(
            self.research_report_validator.iter_errors(result),
            key=lambda item: list(item.path),
        ):
            pointer = "$.research_report"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            self._error(report, "research_report_schema", pointer, error.message)
        if result.get("request_sha256") != request_sha256(self.research_request):
            self._error(
                report,
                "research_report_request_mismatch",
                "$.research_report.request_sha256",
                "research_report 没有引用本次 research_request",
            )
        return result

    def _load_entity_inventory(
        self,
        resources: list[Any],
        resource_files: dict[str, list[Path]],
        report: ValidationReport,
    ) -> dict[str, dict[str, Any]]:
        """读取已通过格式检查的实体，供覆盖证据和外键关系做机械校验。"""

        inventory: dict[str, dict[str, Any]] = {}
        for resource in resources:
            if not isinstance(resource, dict) or resource.get("data_type") != "entity":
                continue
            resource_id = resource.get("resource_id")
            schema = resource.get("entity_schema")
            if not isinstance(resource_id, str) or not isinstance(schema, dict):
                continue
            for entity_type, definition in schema.items():
                fields = self._declared_field_types(definition)
                entry = inventory.setdefault(
                    entity_type,
                    {
                        "resource_ids": set(),
                        "fields": set(fields),
                        "field_types": dict(fields),
                        "records": [],
                    },
                )
                if entry["fields"] != set(fields):
                    self._error(
                        report,
                        "conflicting_entity_schema",
                        f"$.environment.resources.{resource_id}.entity_schema.{entity_type}",
                        f"实体 {entity_type} 在多个资源中的字段定义不一致",
                    )
                elif entry["field_types"] != dict(fields):
                    self._error(
                        report,
                        "conflicting_entity_field_types",
                        f"$.environment.resources.{resource_id}.entity_schema.{entity_type}",
                        f"实体 {entity_type} 在多个资源中的字段类型定义不一致",
                    )
                entry["resource_ids"].add(resource_id)
            for path in resource_files.get(resource_id, []):
                try:
                    records_by_type = self._read_entity_records(
                        path,
                        str(resource.get("format") or ""),
                        set(schema),
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, sqlite3.Error, ValueError) as error:
                    self._error(
                        report,
                        "invalid_entity_inventory",
                        f"$.environment.resources.{resource_id}.path",
                        f"无法读取实体库存 {path.name}：{error}",
                    )
                    continue
                for entity_type, records in records_by_type.items():
                    if entity_type in inventory:
                        inventory[entity_type]["records"].extend(records)
        self._validate_global_entity_ids(inventory, report)
        return inventory

    @staticmethod
    def _read_entity_records(
        path: Path,
        format_name: str,
        entity_types: set[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if format_name == "json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return {
                entity_type: [item for item in payload.get(entity_type, []) if isinstance(item, dict)]
                for entity_type in entity_types
            }
        if format_name == "jsonl" and len(entity_types) == 1:
            entity_type = next(iter(entity_types))
            return {
                entity_type: [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            }
        if format_name == "csv" and len(entity_types) == 1:
            entity_type = next(iter(entity_types))
            with path.open("r", encoding="utf-8", newline="") as stream:
                return {entity_type: list(csv.DictReader(stream))}
        if format_name == "sqlite":
            result: dict[str, list[dict[str, Any]]] = {}
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                for entity_type in entity_types:
                    quoted = entity_type.replace('"', '""')
                    result[entity_type] = [
                        dict(row) for row in connection.execute(f'SELECT * FROM "{quoted}"')
                    ]
            finally:
                connection.close()
            return result
        if format_name == "parquet" and len(entity_types) == 1:
            try:
                import pyarrow.parquet as parquet  # type: ignore[import-not-found]
            except ImportError:
                return {}
            return {next(iter(entity_types)): parquet.read_table(path).to_pylist()}
        return {}

    def _validate_global_entity_ids(
        self,
        inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """跨文件检查同一实体类型的稳定主键，防止 file_collection 重复记录。"""

        for entity_type, entry in inventory.items():
            fields = entry.get("fields", set())
            primary_id = self._primary_id_field(entity_type, fields)
            if primary_id is None:
                continue
            seen: set[str] = set()
            for index, record in enumerate(entry.get("records", [])):
                if not isinstance(record, dict) or primary_id not in record:
                    continue
                key = self._value_key(record[primary_id])
                if key in seen:
                    self._error(
                        report,
                        "duplicate_entity_id_across_files",
                        f"$.environment.entity_schema.{entity_type}",
                        f"实体 {entity_type} 的 {primary_id} 在多个文件中重复（记录索引 {index}）",
                    )
                seen.add(key)

    def _validate_research_coverage(
        self,
        result: dict[str, Any],
        workspace_root: Path,
        resources: dict[str, dict[str, Any]],
        resource_files: dict[str, list[Path]],
        entity_counts: dict[str, int],
        inventory: dict[str, dict[str, Any]],
        sources: list[dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """验证种子覆盖、超额丰富度和真实数据策略，而不是相信自然语言总结。"""

        if result.get("status") != "ready" or self.research_request is None:
            return
        policy = self.research_request["quality_policy"]
        requirements = {
            item["requirement_id"]: item for item in self.research_request["requirements"]
        }
        representation = result.get("representation_mode")
        coverage_items = result.get("coverage", [])
        coverage: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(coverage_items if isinstance(coverage_items, list) else []):
            if not isinstance(item, dict):
                continue
            requirement_id = item.get("requirement_id")
            if requirement_id in coverage:
                self._error(
                    report,
                    "duplicate_coverage_requirement",
                    f"$.research_report.coverage[{index}].requirement_id",
                    f"覆盖项重复：{requirement_id}",
                )
            elif isinstance(requirement_id, str):
                coverage[requirement_id] = item
        if set(coverage) != set(requirements):
            self._error(
                report,
                "coverage_requirement_mismatch",
                "$.research_report.coverage",
                f"覆盖项必须与请求一致；缺少 {sorted(set(requirements) - set(coverage))}，多出 {sorted(set(coverage) - set(requirements))}",
            )
        if result.get("status") == "ready":
            unavailable = [
                requirement_id
                for requirement_id, item in coverage.items()
                if isinstance(item, dict) and item.get("status") != "covered"
            ]
            if unavailable:
                self._error(
                    report,
                    "ready_with_unavailable_requirements",
                    "$.research_report.coverage",
                    f"ready 报告不能包含未覆盖的核心 requirement：{unavailable}",
                )

        seed_entity_types: set[str] = set()
        for requirement_id, requirement in requirements.items():
            item = coverage.get(requirement_id)
            if not item:
                continue
            if item.get("status") != "covered":
                self._error(
                    report,
                    "uncovered_seed_requirement",
                    f"$.research_report.coverage.{requirement_id}",
                    f"核心种子要求未被真实数据覆盖：{requirement['name']}",
                )
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                self._error(
                    report,
                    "missing_coverage_evidence",
                    f"$.research_report.coverage.{requirement_id}.evidence",
                    "covered 状态必须提供至少一条可机械核验的证据",
                )
                continue
            self._validate_evidence_list(evidence, resources, inventory, report, requirement_id)
            if requirement["kind"] == "seed_entity":
                observed_entity_types = {
                    entity_type
                    for evidence_item in evidence
                    if isinstance(evidence_item, dict)
                    for entity_type in evidence_item.get("entity_types", [])
                    if isinstance(entity_type, str)
                }
                if representation in {"structured_records", "hybrid"} and not observed_entity_types:
                    self._error(
                        report,
                        "missing_seed_entity_type",
                        f"$.research_report.coverage.{requirement_id}.evidence",
                        "结构化环境的种子实体覆盖必须引用至少一个真实 entity type",
                    )
                aligned_entity_types = self._validate_requirement_entity_alignment(
                    requirement,
                    observed_entity_types,
                    inventory,
                    f"$.research_report.coverage.{requirement_id}.evidence",
                    report,
                )
                seed_entity_types.update(aligned_entity_types)
                minimum_records = int(requirement.get("minimum_records", 1) or 0)
                if aligned_entity_types:
                    observed_count = max(
                        len(inventory.get(entity_type, {}).get("records", []))
                        for entity_type in aligned_entity_types
                    )
                    if observed_count < minimum_records:
                        self._error(
                            report,
                            "core_entity_records_below_minimum",
                            f"$.research_report.coverage.{requirement_id}.evidence",
                            f"核心实体 {requirement['name']} 至少需要 {minimum_records} 条真实记录，实际最多 {observed_count} 条",
                        )
            if requirement["kind"] == "seed_operation":
                observed_entity_types = {
                    entity_type
                    for evidence_item in evidence
                    if isinstance(evidence_item, dict)
                    for entity_type in evidence_item.get("entity_types", [])
                    if isinstance(entity_type, str)
                }
                self._validate_requirement_entity_alignment(
                    requirement,
                    observed_entity_types,
                    inventory,
                    f"$.research_report.coverage.{requirement_id}.evidence",
                    report,
                )
                target_entity = item.get("target_entity")
                if representation in {"structured_records", "hybrid"}:
                    if not isinstance(target_entity, str) or not target_entity:
                        self._error(
                            report,
                            "missing_operation_target_entity",
                            f"$.research_report.coverage.{requirement_id}.target_entity",
                            "结构化种子操作必须声明实际绑定的目标实体",
                        )
                    elif target_entity not in observed_entity_types:
                        self._error(
                            report,
                            "operation_target_not_in_evidence",
                            f"$.research_report.coverage.{requirement_id}.target_entity",
                            f"目标实体 {target_entity} 没有出现在该操作的证据中",
                        )
                    minimum_records = int(requirement.get("minimum_records", 1) or 0)
                    if isinstance(target_entity, str) and target_entity in inventory:
                        observed_count = len(inventory[target_entity].get("records", []))
                        if observed_count < minimum_records:
                            self._error(
                                report,
                                "operation_records_below_minimum",
                                f"$.research_report.coverage.{requirement_id}.target_entity",
                                f"核心操作 {requirement['name']} 的目标实体至少需要 {minimum_records} 条真实记录，实际 {observed_count} 条",
                            )
                    target_candidates = requirement.get("target_entity_candidates")
                    if isinstance(target_candidates, list) and target_candidates and isinstance(target_entity, str):
                        candidate_scores = {
                            entity_type: max(
                                (
                                    semantic_match_score(candidate, entity_type)
                                    for candidate in target_candidates
                                    if isinstance(candidate, str)
                                ),
                                default=0,
                            )
                            for entity_type in inventory
                        }
                        best_score = max(candidate_scores.values(), default=0)
                        if best_score > 0 and candidate_scores.get(target_entity, 0) != best_score:
                            self._error(
                                report,
                                "operation_target_not_best_match",
                                f"$.research_report.coverage.{requirement_id}.target_entity",
                                f"目标实体 {target_entity} 不是种子声明候选中的最精确匹配",
                            )
                operation_family = item.get("operation_family")
                if not operation_family:
                    self._error(
                        report,
                        "missing_operation_family",
                        f"$.research_report.coverage.{requirement_id}.operation_family",
                        "种子操作覆盖必须声明 operation_family",
                    )
                else:
                    expected_family = self._infer_operation_family(requirement["name"])
                    if expected_family and operation_family != expected_family:
                        self._error(
                            report,
                            "operation_family_mismatch",
                            f"$.research_report.coverage.{requirement_id}.operation_family",
                            f"操作名称 {requirement['name']!r} 应归入 {expected_family}，实际为 {operation_family}",
                        )
                if operation_family == "mutate":
                    target_entity = item.get("target_entity")
                    writable_targets = [
                        resource_id
                        for resource_id, resource in resources.items()
                        if resource.get("data_type") == "entity"
                        and resource.get("writable") is True
                        and (
                            not isinstance(target_entity, str)
                            or target_entity in (resource.get("entity_schema") or {})
                        )
                    ]
                    if not writable_targets:
                        self._error(
                            report,
                            "mutate_requires_writable_entity",
                            f"$.research_report.coverage.{requirement_id}",
                            "mutate 操作必须绑定可写 entity 资源；只读公开快照不能提供该能力",
                        )
                elif operation_family == "export":
                    if not any(
                        resource.get("data_type") == "output"
                        and resource.get("writable") is True
                        for resource in resources.values()
                    ):
                        self._error(
                            report,
                            "export_requires_writable_output",
                            f"$.research_report.coverage.{requirement_id}",
                            "export 操作必须存在可写 output 资源",
                        )
                elif operation_family == "audit":
                    if not any(resource.get("data_type") == "derived" for resource in resources.values()) and not result.get("relations"):
                        self._error(
                            report,
                            "audit_requires_integrity_evidence",
                            f"$.research_report.coverage.{requirement_id}",
                            "audit 操作必须有派生校验结果或闭合关系作为完整性证据",
                        )
                elif operation_family == "traverse":
                    relation_refs = {
                        (
                            relation.get("from_entity"),
                            relation.get("field"),
                            relation.get("to_entity"),
                            relation.get("target_field"),
                        )
                        for relation in result.get("relations", [])
                        if isinstance(relation, dict)
                    }
                    evidence_refs = {
                        ref
                        for evidence_item in evidence
                        if isinstance(evidence_item, dict)
                        for ref in evidence_item.get("field_refs", [])
                        if isinstance(ref, str) and "." in ref
                    }
                    if not any(
                        f"{source}.{field}" in evidence_refs
                        and f"{target}.{target_field}" in evidence_refs
                        for source, field, target, target_field in relation_refs
                    ):
                        self._error(
                            report,
                            "traverse_missing_closed_relation",
                            f"$.research_report.coverage.{requirement_id}",
                            "traverse 操作必须引用一条已闭合关系的两端字段",
                        )
                if representation in {"structured_records", "hybrid"}:
                    field_ref_count = sum(
                        len(evidence_item.get("field_refs", []))
                        for evidence_item in evidence
                        if isinstance(evidence_item, dict)
                        and isinstance(evidence_item.get("field_refs"), list)
                    )
                    if field_ref_count < policy["min_operation_evidence_fields"]:
                        self._error(
                            report,
                            "missing_operation_field_evidence",
                            f"$.research_report.coverage.{requirement_id}.evidence",
                            "结构化环境的种子操作必须至少引用一个真实字段，不能只引用 raw 文件",
                        )
                    self._validate_operation_evidence(
                        operation_family,
                        evidence,
                        inventory,
                        f"$.research_report.coverage.{requirement_id}.evidence",
                        report,
                    )
                    if isinstance(target_entity, str) and requirement.get("target_resolution") == "direct_entity":
                        wrong_target_refs = [
                            ref
                            for evidence_item in evidence
                            if isinstance(evidence_item, dict)
                            for ref in evidence_item.get("field_refs", [])
                            if isinstance(ref, str)
                            and "." in ref
                            and ref.split(".", 1)[0] != target_entity
                        ]
                        if wrong_target_refs:
                            self._error(
                                report,
                                "operation_evidence_wrong_target",
                                f"$.research_report.coverage.{requirement_id}.evidence",
                                f"direct_entity 操作的字段必须来自目标实体 {target_entity}，实际包含：{wrong_target_refs[:3]}",
                            )
                    elif (
                        isinstance(target_entity, str)
                        and requirement.get("target_resolution") == "direct_or_related_fact"
                    ):
                        # 跨事实实体取数不是自由拼接：只允许 evidence 中的
                        # 额外实体通过 research_report.relations 与目标实体
                        # 直接相连。后续 _validate_relations 还会检查关系的
                        # 唯一键、语义和全量值闭合性。
                        observed_entities = {
                            entity_type
                            for evidence_item in evidence
                            if isinstance(evidence_item, dict)
                            for entity_type in evidence_item.get("entity_types", [])
                            if isinstance(entity_type, str)
                        }
                        extra_entities = observed_entities - {target_entity}
                        relation_refs = {
                            (
                                relation.get("from_entity"),
                                relation.get("field"),
                                relation.get("to_entity"),
                                relation.get("target_field"),
                            )
                            for relation in result.get("relations", [])
                            if isinstance(relation, dict)
                        }
                        evidence_refs = {
                            ref
                            for evidence_item in evidence
                            if isinstance(evidence_item, dict)
                            for ref in evidence_item.get("field_refs", [])
                            if isinstance(ref, str) and "." in ref
                        }
                        for extra_entity in sorted(extra_entities):
                            if not self._is_fact_entity(extra_entity):
                                self._error(
                                    report,
                                    "operation_related_entity_not_fact",
                                    f"$.research_report.coverage.{requirement_id}.evidence",
                                    f"direct_or_related_fact 的额外实体 {extra_entity} 必须明确具有 observation、measurement、event、transaction 或 reading 事实语义",
                                )
                            joined = any(
                                (
                                    source == extra_entity
                                    and target == target_entity
                                    and f"{source}.{field}" in evidence_refs
                                    and f"{target}.{target_field}" in evidence_refs
                                )
                                or (
                                    source == target_entity
                                    and target == extra_entity
                                    and f"{source}.{field}" in evidence_refs
                                    and f"{target}.{target_field}" in evidence_refs
                                )
                                for source, field, target, target_field in relation_refs
                            )
                            if not joined:
                                self._error(
                                    report,
                                    "operation_related_fact_missing_relation",
                                    f"$.research_report.coverage.{requirement_id}.evidence",
                                    f"事实实体 {extra_entity} 必须通过闭合关系连接目标实体 {target_entity}",
                                )
                            else:
                                if len(inventory.get(extra_entity, {}).get("records", [])) < int(
                                    requirement.get("minimum_records", 1) or 0
                                ):
                                    self._error(
                                        report,
                                        "related_fact_records_below_minimum",
                                        f"$.research_report.coverage.{requirement_id}.evidence",
                                        f"事实实体 {extra_entity} 的真实记录不足，至少需要 {requirement.get('minimum_records', 1)} 条",
                                    )
                                fact_numeric_refs = [
                                    ref
                                    for ref in evidence_refs
                                    if ref.split(".", 1)[0] == extra_entity
                                    and self._field_type(ref, inventory) in {"integer", "number"}
                                    and not self._is_technical_field(ref.split(".", 1)[1])
                                ]
                                if not fact_numeric_refs:
                                    self._error(
                                        report,
                                        "related_fact_missing_numeric_field",
                                        f"$.research_report.coverage.{requirement_id}.evidence",
                                        f"事实实体 {extra_entity} 必须提供业务数值字段",
                                    )
                                elif not any(
                                    self._field_has_variation(
                                        ref,
                                        inventory,
                                        minimum=int(
                                            policy.get("min_operation_distinct_values", 2)
                                        ),
                                    )
                                    for ref in fact_numeric_refs
                                ):
                                    self._error(
                                        report,
                                        "related_fact_numeric_field_has_no_variation",
                                        f"$.research_report.coverage.{requirement_id}.evidence",
                                        f"事实实体 {extra_entity} 的业务数值字段没有足够的真实取值变化",
                                    )

        extensions = result.get("extensions", [])
        extension_target = int(
            policy.get("extension_capability_target", policy["min_extension_capabilities"])
        )
        if len(extensions) < extension_target:
            self._error(
                report,
                "insufficient_extension_capabilities",
                "$.research_report.extensions",
                f"扩展能力至少需要 {extension_target} 项，实际 {len(extensions)} 项",
            )
        extension_families: set[str] = set()
        extension_ids: set[str] = set()
        for index, item in enumerate(extensions):
            if not isinstance(item, dict):
                continue
            capability_id = item.get("capability_id")
            if capability_id in extension_ids:
                self._error(report, "duplicate_extension", f"$.research_report.extensions[{index}]", f"扩展能力重复：{capability_id}")
            elif isinstance(capability_id, str):
                extension_ids.add(capability_id)
            if isinstance(item.get("operation_family"), str):
                extension_families.add(item["operation_family"])
            self._validate_evidence_list(
                item.get("evidence", []), resources, inventory, report, f"extension:{capability_id}"
            )
            self._validate_operation_evidence(
                item.get("operation_family"),
                item.get("evidence", []),
                inventory,
                f"$.research_report.extensions[{index}].evidence",
                report,
            )
            if item.get("operation_family") == "traverse":
                self._validate_closed_traverse_evidence(
                    item.get("evidence", []),
                    result.get("relations", []),
                    f"$.research_report.extensions[{index}].evidence",
                    report,
                )
        extension_family_target = int(
            policy.get(
                "extension_operation_family_target",
                policy["min_extension_operation_families"],
            )
        )
        if len(extension_families) < extension_family_target:
            self._error(
                report,
                "insufficient_extension_diversity",
                "$.research_report.extensions",
                f"扩展能力至少覆盖 {extension_family_target} 类操作，实际 {len(extension_families)} 类",
            )

        if representation in {"structured_records", "hybrid"}:
            if len(entity_counts) < policy["min_entity_types"]:
                self._error(
                    report,
                    "insufficient_entity_types",
                    "$.environment.resources",
                    f"实体类型至少需要 {policy['min_entity_types']} 种，实际 {len(entity_counts)} 种",
                )
            total_records = sum(entity_counts.values())
            if total_records < policy["min_total_entity_records"]:
                self._error(
                    report,
                    "insufficient_entity_records",
                    "$.environment.resources",
                    f"实体记录至少需要 {policy['min_total_entity_records']} 条，实际 {total_records} 条",
                )
            extension_types = set(inventory) - seed_entity_types
            # 关系桥是连接业务实体的重要结构，但不能单独充当“扩展实体”
            # 数量；否则下载大量 link 行也能绕过丰富度门槛。
            non_bridge_extension_types = {
                entity_type
                for entity_type in extension_types
                if not self._is_bridge_entity(
                    entity_type,
                    inventory[entity_type].get("fields", set()),
                )
            }
            if len(non_bridge_extension_types) < policy["min_extension_entity_types"]:
                self._error(
                    report,
                    "insufficient_extension_entities",
                    "$.research_report.coverage",
                    f"除种子实体映射外至少需要 {policy['min_extension_entity_types']} 种非关系桥扩展实体，实际 {len(non_bridge_extension_types)} 种",
                )
            closed_relations = result.get("relations", [])
            self._validate_relations(closed_relations, inventory, policy, report)
            self._validate_relation_gaps(
                result.get("relation_gaps", []),
                inventory,
                closed_relations,
                report,
            )

        dimension_kinds: set[str] = set()
        for index, dimension in enumerate(result.get("dimensions", [])):
            if not isinstance(dimension, dict):
                continue
            dimension_kinds.add(str(dimension.get("kind") or ""))
            for resource_id in dimension.get("resource_ids", []):
                if resource_id not in resources:
                    self._error(report, "unknown_dimension_resource", f"$.research_report.dimensions[{index}]", f"维度引用未知资源：{resource_id}")
            self._validate_field_refs(
                dimension.get("field_refs", []), inventory, report, f"$.research_report.dimensions[{index}].field_refs"
            )
            self._validate_dimension_variation(dimension, inventory, report, index)
        if len(dimension_kinds - {""}) < policy["min_dimension_kinds"]:
            self._error(
                report,
                "insufficient_data_dimensions",
                "$.research_report.dimensions",
                f"数据至少需要 {policy['min_dimension_kinds']} 种可用于任务变化的维度，实际 {len(dimension_kinds - {''})} 种",
            )

        # 同一文件可能被多个资源引用；预算按唯一 raw 文件重新计算。
        raw_files = {
            path.resolve()
            for resource_id, resource in resources.items()
            if resource.get("data_type") == "raw"
            for path in resource_files.get(resource_id, [])
        }
        source_bytes = sum(path.stat().st_size for path in raw_files)
        if len(sources) > policy["max_sources"]:
            self._error(report, "source_budget_exceeded", "$.provenance.sources", f"来源数超过预算：{len(sources)} > {policy['max_sources']}")
        if len(raw_files) > policy["max_raw_files"]:
            self._error(report, "raw_file_budget_exceeded", "$.workspace", f"原始文件数超过预算：{len(raw_files)} > {policy['max_raw_files']}")
        if source_bytes > policy["max_download_bytes"]:
            self._error(report, "download_budget_exceeded", "$.workspace", f"原始数据超过预算：{source_bytes} > {policy['max_download_bytes']} bytes")
        if policy["primary_sources_required"] and not any(
            source.get("source_type") in {"official_api", "official_repository", "official_dataset"}
            for source in sources
        ):
            self._error(report, "missing_primary_source", "$.provenance.sources", "至少需要一个官方或一手来源")
        if result.get("data_policy", {}).get("synthetic_business_record_count") != 0:
            self._error(report, "synthetic_business_records", "$.research_report.data_policy", "业务记录禁止模型合成")
        report.statistics.update(
            {
                "requirements_total": len(requirements),
                "requirements_covered": sum(
                    1
                    for item in coverage.values()
                    if isinstance(item, dict) and item.get("status") == "covered"
                ),
                "extension_capabilities": len(extensions)
                if isinstance(extensions, list)
                else 0,
                "extension_operation_families": sorted(extension_families),
                "relations": len(result.get("relations", []))
                if isinstance(result.get("relations"), list)
                else 0,
                "relation_gaps": len(result.get("relation_gaps", []))
                if isinstance(result.get("relation_gaps"), list)
                else 0,
                "dimension_kinds": sorted(dimension_kinds - {""}),
                "raw_bytes": source_bytes,
                "derived_bytes": sum(
                    path.stat().st_size
                    for resource_id, resource in resources.items()
                    if resource.get("data_type") == "derived"
                    for path in resource_files.get(resource_id, [])
                ),
                "workspace_bytes": sum(
                    path.stat().st_size
                    for path in workspace_root.rglob("*")
                    if path.is_file()
                ) if workspace_root.is_dir() else 0,
            }
        )
        derived_bytes = report.statistics["derived_bytes"]
        workspace_bytes = report.statistics["workspace_bytes"]
        if derived_bytes > policy["max_derived_bytes"]:
            self._error(
                report,
                "derived_file_budget_exceeded",
                "$.workspace",
                f"派生文件超过预算：{derived_bytes} > {policy['max_derived_bytes']} bytes",
            )
        if workspace_bytes > policy["max_workspace_bytes"]:
            self._error(
                report,
                "workspace_budget_exceeded",
                "$.workspace",
                f"workspace 超过预算：{workspace_bytes} > {policy['max_workspace_bytes']} bytes",
            )

    def _validate_requirement_entity_alignment(
        self,
        requirement: dict[str, Any],
        observed_entity_types: set[str],
        inventory: dict[str, dict[str, Any]],
        pointer: str,
        report: ValidationReport,
    ) -> set[str]:
        """确保覆盖证据引用的是种子要求对应的实体，而不是任意实体。

        仅检查 entity type 的词面和结构，不猜测业务数据。完全匹配优先
        于复合名称包含匹配；如果库存中存在更精确的候选，报告必须引用
        它，避免模型用关系表或同前缀实体冒充种子实体。
        """

        if not observed_entity_types:
            return set()
        kind = requirement.get("kind")
        if kind == "seed_operation":
            target_tokens = requirement.get("target_tokens")
            if not isinstance(target_tokens, list) or not target_tokens:
                return set(observed_entity_types)
            target_name = " ".join(str(token) for token in target_tokens)
        else:
            target_name = str(requirement.get("name") or "")
        available_scores = {
            entity_type: (
                0
                if (
                    kind == "seed_operation"
                    and requirement.get("operation_family") not in {"traverse", "other"}
                    and self._is_bridge_entity(entity_type, inventory[entity_type].get("fields", set()))
                )
                else semantic_match_score(target_name, entity_type)
            )
            for entity_type in inventory
        }
        best_available = max(available_scores.values(), default=0)
        observed_scores = {
            entity_type: available_scores.get(entity_type, 0)
            for entity_type in observed_entity_types
        }
        best_observed = max(observed_scores.values(), default=0)
        aligned = {
            entity_type
            for entity_type, score in observed_scores.items()
            if score > 0
        }
        if best_observed == 0:
            self._error(
                report,
                "coverage_entity_semantic_mismatch",
                pointer,
                f"覆盖证据引用的实体与要求 {target_name!r} 没有可解释的名称交集：{sorted(observed_entity_types)}",
            )
        elif best_available > best_observed:
            preferred = sorted(
                entity_type
                for entity_type, score in available_scores.items()
                if score == best_available
            )
            self._error(
                report,
                "coverage_entity_not_best_match",
                pointer,
                f"覆盖证据没有引用与要求 {target_name!r} 最匹配的实体；优先使用 {preferred}，实际 {sorted(observed_entity_types)}",
            )
        return aligned

    @staticmethod
    def _infer_operation_family(name: str) -> str | None:
        """从种子操作的首个动词推断通用操作族，避免报告任意改名。"""

        first = str(name).strip().lower().replace("-", " ").split(" ", 1)[0]
        families = {
            "search": "search",
            "find": "search",
            "query": "search",
            "inspect": "inspect",
            "get": "inspect",
            "show": "inspect",
            "view": "inspect",
            "list": "list",
            "rank": "rank",
            "sort": "rank",
            "compare": "compare",
            "count": "aggregate",
            "aggregate": "aggregate",
            "filter": "filter",
            "traverse": "traverse",
            "audit": "audit",
            "export": "export",
            "update": "mutate",
            "create": "mutate",
            "delete": "mutate",
        }
        return families.get(first)

    def _validate_dimension_variation(
        self,
        dimension: dict[str, Any],
        inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
        index: int,
    ) -> None:
        """要求报告声明的变化维度在真实记录中确实有可观察差异。"""

        kind = dimension.get("kind")
        if kind == "file_format":
            return
        refs = dimension.get("field_refs", [])
        if not isinstance(refs, list) or not refs:
            self._error(
                report,
                "dimension_without_fields",
                f"$.research_report.dimensions[{index}].field_refs",
                "非 file_format 维度必须引用至少一个实体字段",
            )
            return
        min_values = self.research_request["quality_policy"]["min_dimension_distinct_values"] if self.research_request else 2
        observed = False
        for ref in refs:
            if not isinstance(ref, str) or "." not in ref:
                continue
            entity_type, field = ref.split(".", 1)
            entry = inventory.get(entity_type)
            if not entry:
                continue
            values = {
                self._value_key(record.get(field))
                for record in entry.get("records", [])
                if isinstance(record, dict) and record.get(field) not in (None, "")
            }
            if len(values) >= min_values:
                observed = True
                break
        if not observed:
            self._error(
                report,
                "dimension_has_no_variation",
                f"$.research_report.dimensions[{index}]",
                f"维度 {kind} 没有在真实记录中观察到至少 {min_values} 个不同值",
            )
        # 维度类型必须与字段的可观察形态一致；否则模型可以把同一个
        # 唯一名称字段同时声称为 category 和 text 来凑数量。
        for ref in refs:
            if not isinstance(ref, str) or "." not in ref:
                continue
            entity_type, field = ref.split(".", 1)
            records = inventory.get(entity_type, {}).get("records", [])
            values = [
                str(record.get(field)).strip()
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get(field), str)
                and record.get(field) not in (None, "")
            ]
            distinct = len(set(values))
            average_length = sum(map(len, values)) / len(values) if values else 0.0
            temporal_name = any(
                token in field.lower()
                for token in ("year", "date", "time", "timestamp", "created", "updated", "published")
            )
            if kind == "temporal" and not temporal_name:
                self._error(
                    report,
                    "dimension_not_temporal",
                    f"$.research_report.dimensions[{index}]",
                    f"temporal 维度字段名没有时间语义：{ref}",
                )
            elif kind in {"category", "text"} and temporal_name:
                self._error(
                    report,
                    "dimension_not_business_text",
                    f"$.research_report.dimensions[{index}]",
                    f"{kind} 维度不能把时间字段当作业务文本：{ref}",
                )
            elif kind == "category" and values:
                max_categories = max(8, min(50, len(records) // 2))
                if distinct > max_categories or average_length >= 80:
                    self._error(
                        report,
                        "dimension_not_category",
                        f"$.research_report.dimensions[{index}]",
                        f"category 维度不是有限类别字段：{ref}",
                    )
            elif kind == "text" and values and average_length < 20 and not any(
                token in field.lower()
                for token in ("title", "description", "summary", "body", "abstract", "text", "content")
            ):
                self._error(
                    report,
                    "dimension_not_text",
                    f"$.research_report.dimensions[{index}]",
                    f"text 维度缺少可全文检索的文本字段：{ref}",
                )
        # 技术标识、编码和 URL 不是可供任务改变或比较的业务维度。之前只
        # 在“全部引用都是技术字段”时拒绝，模型可以借一个合法字段夹带
        # ID 来规避门槛；现在所有非 file_format 维度都统一禁止技术字段。
        technical_refs = [
            ref for ref in refs
            if isinstance(ref, str) and "." in ref
            and self._is_technical_field(ref.split(".", 1)[1])
        ]
        if technical_refs:
            self._error(
                report,
                "dimension_uses_technical_field",
                f"$.research_report.dimensions[{index}]",
                f"{kind} 维度不能引用 ID、编码或 URL 字段：{technical_refs}",
            )

    def _validate_operation_evidence(
        self,
        operation_family: Any,
        evidence: Any,
        inventory: dict[str, dict[str, Any]],
        pointer: str,
        report: ValidationReport,
    ) -> None:
        """按操作族检查字段真的能支撑排序、比较、聚合等行为。"""

        if not isinstance(operation_family, str):
            return
        refs = [
            ref
            for item in evidence if isinstance(item, dict)
            for ref in item.get("field_refs", [])
            if isinstance(ref, str) and "." in ref
        ] if isinstance(evidence, list) else []
        if not refs:
            return

        def field_type(ref: str) -> str | None:
            entity_type, field = ref.split(".", 1)
            entry = inventory.get(entity_type)
            if not entry:
                return None
            return entry.get("field_types", {}).get(field)

        numeric = {"integer", "number"}
        if operation_family in {"rank", "compare", "aggregate"}:
            minimum_distinct = int(
                self.research_request["quality_policy"].get(
                    "min_operation_distinct_values",
                    self.research_request["quality_policy"].get(
                        "min_dimension_distinct_values", 2
                    ),
                )
            ) if self.research_request else 2
            numeric_refs = [ref for ref in refs if field_type(ref) in numeric]
            if not numeric_refs:
                self._error(
                    report,
                    "operation_missing_numeric_field",
                    pointer,
                    f"{operation_family} 操作至少需要一个 integer 或 number 字段证据",
                )
            elif not any(
                self._field_has_variation(ref, inventory, minimum=minimum_distinct)
                for ref in numeric_refs
            ):
                self._error(
                    report,
                    "operation_numeric_field_has_no_variation",
                    pointer,
                    f"{operation_family} 操作的数值字段没有至少 {minimum_distinct} 个不同的真实值",
                )
            useful_refs = [
                ref for ref in numeric_refs
                if not self._is_technical_field(ref.split(".", 1)[1])
            ]
            if not useful_refs:
                # 不能因为目标实体恰好有一个递增 ID 就声称可以排序、
                # 比较或聚合。核心操作必须由同一目标实体的业务数值字段
                # 支撑；若确实存在业务指标，应由报告直接引用它，而不是
                # 让校验器替模型猜另一个字段。
                self._error(
                    report,
                    "operation_uses_technical_field",
                    pointer,
                    f"{operation_family} 操作不能只使用 ID/编码等技术数值字段",
                )
        elif operation_family == "timeline":
            minimum_distinct = int(
                self.research_request["quality_policy"].get(
                    "min_operation_distinct_values",
                    self.research_request["quality_policy"].get(
                        "min_dimension_distinct_values", 2
                    ),
                )
            ) if self.research_request else 2
            temporal_refs = [
                ref
                for ref in refs
                if any(
                    token in ref.split(".", 1)[1].lower()
                    for token in ("year", "date", "time", "timestamp", "created", "updated", "published")
                )
            ]
            if not temporal_refs:
                self._error(
                    report,
                    "operation_missing_temporal_field",
                    pointer,
                    "timeline 操作至少需要一个名称明确表示时间的字段证据",
                )
            elif not any(
                self._field_has_variation(ref, inventory, minimum=minimum_distinct)
                for ref in temporal_refs
            ):
                self._error(
                    report,
                    "operation_temporal_field_has_no_variation",
                    pointer,
                    f"timeline 操作的时间字段没有至少 {minimum_distinct} 个不同的真实值",
                )
        elif operation_family in {"filter", "search", "list"}:
            minimum_distinct = int(
                self.research_request["quality_policy"].get(
                    "min_operation_distinct_values",
                    self.research_request["quality_policy"].get(
                        "min_dimension_distinct_values", 2
                    ),
                )
            ) if self.research_request else 2
            if not any(
                self._field_has_variation(ref, inventory, minimum=minimum_distinct)
                for ref in refs
            ):
                self._error(
                    report,
                    "operation_field_has_no_variation",
                    pointer,
                    f"{operation_family} 操作的字段没有可筛选或区分的真实值",
                )
            if operation_family in {"filter", "search"}:
                useful_refs = [
                    ref
                    for ref in refs
                    if self._field_type(ref, inventory) == "string"
                    and not self._is_technical_field(ref.split(".", 1)[1])
                ]
                if not useful_refs:
                    self._error(
                        report,
                        "operation_uses_technical_field",
                        pointer,
                        f"{operation_family} 操作不能只使用 ID、编码或 URL 等技术字段",
                    )
            if operation_family == "list" and not any(
                not self._is_technical_field(ref.split(".", 1)[1])
                for ref in refs
            ):
                self._error(
                    report,
                    "list_requires_business_field",
                    pointer,
                    "list 操作不能只引用技术标识字段",
                )
        elif operation_family == "inspect":
            if not any(
                not self._is_technical_field(ref.split(".", 1)[1])
                for ref in refs
            ):
                self._error(
                    report,
                    "inspect_requires_business_field",
                    pointer,
                    "inspect 操作至少需要一个可读业务字段",
                )
        elif operation_family == "traverse":
            if len(set(refs)) < 2:
                self._error(
                    report,
                    "traverse_requires_relation_fields",
                    pointer,
                    "traverse 操作必须同时提供关系源字段和目标主键字段",
                )
        elif operation_family == "audit":
            if not any(
                not self._is_technical_field(ref.split(".", 1)[1])
                for ref in refs
            ):
                self._error(
                    report,
                    "audit_requires_business_evidence",
                    pointer,
                    "audit 操作至少需要一个业务字段或可解释的完整性证据",
                )

    def _validate_closed_traverse_evidence(
        self,
        evidence: Any,
        relations: Any,
        pointer: str,
        report: ValidationReport,
    ) -> None:
        """要求 traverse 扩展明确对应一条已闭合关系的两端字段。"""

        refs = {
            ref
            for item in evidence if isinstance(item, dict)
            for ref in item.get("field_refs", [])
            if isinstance(ref, str) and "." in ref
        } if isinstance(evidence, list) else set()
        if not isinstance(relations, list):
            relations = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            source = relation.get("from_entity")
            source_field = relation.get("field")
            target = relation.get("to_entity")
            target_field = relation.get("target_field")
            if (
                isinstance(source, str)
                and isinstance(source_field, str)
                and isinstance(target, str)
                and isinstance(target_field, str)
                and f"{source}.{source_field}" in refs
                and f"{target}.{target_field}" in refs
            ):
                return
        self._error(
            report,
            "extension_traverse_missing_closed_relation",
            pointer,
            "traverse 扩展必须引用一条已闭合关系的源字段和目标唯一键，不能只凭两个外键字段声明能力",
        )

    def _field_has_variation(
        self,
        ref: str,
        inventory: dict[str, dict[str, Any]],
        *,
        minimum: int | None = None,
    ) -> bool:
        entity_type, field = ref.split(".", 1)
        entry = inventory.get(entity_type)
        if not entry:
            return False
        values = {
            self._value_key(record.get(field))
            for record in entry.get("records", [])
            if isinstance(record, dict) and record.get(field) not in (None, "")
        }
        if minimum is None:
            minimum = (
                self.research_request["quality_policy"].get(
                    "min_dimension_distinct_values", 2
                )
                if self.research_request
                else 2
            )
        return len(values) >= minimum

    @staticmethod
    def _field_type(
        ref: str,
        inventory: dict[str, dict[str, Any]],
    ) -> str | None:
        entity_type, field = ref.split(".", 1)
        return inventory.get(entity_type, {}).get("field_types", {}).get(field)

    @staticmethod
    def _is_technical_field(field: str) -> bool:
        """识别不应优先充当业务排序/分类维度的技术字段。"""

        lowered = str(field).lower()
        return (
            lowered in {"id", "entity_id", "url", "api_url", "uri", "uuid"}
            or lowered in {"decimal", "precision", "scale", "offset", "limit", "page", "page_size"}
            or lowered.endswith(("_id", "_code", "_key", "_url", "_uri"))
            or lowered.endswith("_uuid")
            # ISO/Alpha 等标准编码有时没有下划线（iso2code、iso3code），
            # 仍属于稳定标识而非可用于业务排序的度量维度。
            or lowered.endswith("code")
            and lowered.startswith(("iso", "alpha", "numeric"))
        )

    @staticmethod
    def _is_fact_entity(entity_type: str) -> bool:
        """识别可作为目标实体事实来源的通用事实类型。"""

        return bool(
            semantic_tokens(entity_type).intersection(
                {"observation", "measurement", "event", "transaction", "reading"}
            )
        )

    def _validate_evidence_list(
        self,
        evidence: Any,
        resources: dict[str, dict[str, Any]],
        inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
        label: str,
    ) -> None:
        if not isinstance(evidence, list) or not evidence:
            self._error(report, "missing_evidence", f"$.research_report.{label}", "能力必须提供证据")
            return
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                self._error(
                    report,
                    "invalid_evidence",
                    f"$.research_report.{label}[{index}]",
                    "证据项必须是对象",
                )
                continue
            resource_id = item.get("resource_id")
            if resource_id not in resources:
                self._error(report, "unknown_evidence_resource", f"$.research_report.{label}[{index}]", f"证据引用未知资源：{resource_id}")
                continue
            resource = resources[resource_id]
            declared_entity_types = {
                entity_type
                for entity_type in item.get("entity_types", [])
                if isinstance(entity_type, str)
            }
            for entity_type in declared_entity_types:
                if entity_type not in inventory:
                    self._error(report, "unknown_evidence_entity", f"$.research_report.{label}[{index}]", f"证据引用未知实体：{entity_type}")
                elif resource_id not in inventory[entity_type]["resource_ids"]:
                    self._error(report, "evidence_resource_mismatch", f"$.research_report.{label}[{index}]", f"实体 {entity_type} 不属于资源 {resource_id}")
            if resource.get("data_type") not in {"raw", "entity", "derived", "output"}:
                self._error(
                    report,
                    "invalid_evidence_resource_type",
                    f"$.research_report.{label}[{index}].resource_id",
                    f"证据资源的数据类型无效：{resource.get('data_type')}",
                )
            if resource.get("data_type") != "entity" and item.get("field_refs"):
                self._error(
                    report,
                    "field_evidence_on_non_entity",
                    f"$.research_report.{label}[{index}].field_refs",
                    "只有 entity 资源可以提供结构化字段证据",
                )
            self._validate_field_refs(
                item.get("field_refs", []), inventory, report, f"$.research_report.{label}[{index}].field_refs"
            )
            # field_refs 是证据项内部的自洽声明，不能引用同一资源中但未
            # 列在 entity_types 的另一类实体。否则模型可以用一个无关实体
            # 的字段拼接出跨实体能力，绕过上层的目标对齐和关系校验。
            for field_ref in item.get("field_refs", []) if isinstance(item.get("field_refs", []), list) else []:
                if not isinstance(field_ref, str) or "." not in field_ref:
                    continue
                entity_type = field_ref.split(".", 1)[0]
                if entity_type not in declared_entity_types:
                    self._error(
                        report,
                        "evidence_field_entity_mismatch",
                        f"$.research_report.{label}[{index}].field_refs",
                        f"字段 {field_ref} 的实体未在同一证据项 entity_types 中声明",
                    )

    def _validate_field_refs(
        self,
        refs: Any,
        inventory: dict[str, dict[str, Any]],
        report: ValidationReport,
        pointer: str,
    ) -> None:
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, str) or "." not in ref:
                self._error(report, "invalid_field_ref", pointer, f"字段引用无效：{ref}")
                continue
            entity_type, field = ref.split(".", 1)
            if entity_type not in inventory or field not in inventory[entity_type]["fields"]:
                self._error(report, "unknown_field_ref", pointer, f"字段引用不存在：{ref}")

    def _validate_relations(
        self,
        relations: Any,
        inventory: dict[str, dict[str, Any]],
        policy: dict[str, Any],
        report: ValidationReport,
    ) -> None:
        if not isinstance(relations, list) or len(relations) < policy["min_relations"]:
            self._error(report, "insufficient_relations", "$.research_report.relations", f"至少需要 {policy['min_relations']} 条实体关系")
            return
        relation_ids: set[str] = set()
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                continue
            relation_id = relation.get("relation_id")
            if isinstance(relation_id, str):
                if relation_id in relation_ids:
                    self._error(
                        report,
                        "duplicate_relation_id",
                        f"$.research_report.relations[{index}].relation_id",
                        f"关系 ID 重复：{relation_id}",
                    )
                relation_ids.add(relation_id)
            source_type = relation.get("from_entity")
            target_type = relation.get("to_entity")
            source_field = relation.get("field")
            target_field = relation.get("target_field")
            pointer = f"$.research_report.relations[{index}]"
            if source_type not in inventory or target_type not in inventory:
                self._error(report, "unknown_relation_entity", pointer, f"关系实体不存在：{source_type} -> {target_type}")
                continue
            if source_field not in inventory[source_type]["fields"] or target_field not in inventory[target_type]["fields"]:
                self._error(report, "unknown_relation_field", pointer, f"关系字段不存在：{source_type}.{source_field} -> {target_type}.{target_field}")
                continue
            target_fields = inventory[target_type]["fields"]
            if self._is_bridge_entity(target_type, target_fields):
                self._error(
                    report,
                    "relation_targets_bridge_entity",
                    pointer,
                    f"关系目标 {target_type} 是关系实体，不能作为单列主键被引用",
                )
            target_keys = _metadata_unique_key_fields(
                target_type,
                inventory[target_type]["records"],
                {str(field) for field in target_fields},
            )
            if target_field not in target_keys:
                self._error(
                    report,
                    "relation_target_not_unique",
                    pointer,
                    f"关系目标字段必须是 {target_type} 的非空唯一键，实际为 {target_field}",
                )
            if not self._relation_field_compatible(source_field, target_type, target_field):
                self._error(
                    report,
                    "relation_semantic_mismatch",
                    pointer,
                    f"外键 {source_type}.{source_field} 无法解释为目标实体 {target_type}.{target_field}",
                )
            target_values = {
                record.get(target_field)
                for record in inventory[target_type]["records"]
                if record.get(target_field) not in (None, "")
            }
            missing = {
                record.get(source_field)
                for record in inventory[source_type]["records"]
                if record.get(source_field) not in (None, "")
                and record.get(source_field) not in target_values
            }
            source_values = {
                record.get(source_field)
                for record in inventory[source_type]["records"]
                if record.get(source_field) not in (None, "")
            }
            if not source_values:
                self._error(
                    report,
                    "empty_relation_source",
                    pointer,
                    f"关系源字段没有真实值：{source_type}.{source_field}",
                )
            if source_values and not source_values.intersection(target_values):
                self._error(
                    report,
                    "relation_has_no_matches",
                    pointer,
                    f"关系没有任何实际匹配：{source_type}.{source_field} -> {target_type}.{target_field}",
                )
            if missing:
                self._error(report, "broken_relation", pointer, f"关系存在 {len(missing)} 个无目标值，示例：{list(missing)[:3]}")

    def _relation_gap_candidates(
        self,
        inventory: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """从实体库存中找出有交集但未闭合的高置信外键候选。

        这里只报告字段语义能够解释且至少有一个实际匹配值的候选，避免把
        普通技术 ID 或完全无关的编号误报成关系。该结果用于检查报告是否
        显式记录数据覆盖缺口，不会自动把缺失记录补进环境。
        """

        def values(entity_type: str, field: str) -> set[str]:
            return {
                self._value_key(record.get(field))
                for record in inventory.get(entity_type, {}).get("records", [])
                if isinstance(record, dict) and record.get(field) not in (None, "")
            }

        target_keys: dict[str, list[str]] = {
            entity_type: _metadata_unique_key_fields(
                entity_type,
                entry.get("records", []),
                {str(field) for field in entry.get("fields", set())},
            )
            for entity_type, entry in inventory.items()
        }
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for source_type, source_entry in inventory.items():
            source_fields = source_entry.get("fields", set())
            for source_field in source_fields:
                if not isinstance(source_field, str) or not _is_relation_key_field(source_field):
                    continue
                source_values = values(source_type, source_field)
                if not source_values:
                    continue
                for target_type, target_entry in inventory.items():
                    if source_type == target_type:
                        continue
                    target_fields = target_entry.get("fields", set())
                    if self._is_bridge_entity(target_type, target_fields):
                        continue
                    # 主键优先，备用唯一编码其次；同一源字段指向同一目标
                    # 实体时只报告第一个有真实交集的键。
                    for target_field in target_keys.get(target_type, []):
                        if not self._relation_field_compatible(source_field, target_type, target_field):
                            continue
                        target_values = values(target_type, target_field)
                        matches = source_values.intersection(target_values)
                        missing = source_values - target_values
                        if not matches or not missing:
                            # 完全闭合的关系不属于 gap；继续尝试下一个
                            # 唯一键只会制造重复候选，因此直接结束目标实体。
                            if matches:
                                break
                            continue
                        key = (source_type, source_field, target_type, target_field)
                        if key in seen:
                            break
                        seen.add(key)
                        candidates.append(
                            {
                                "relation_id": self._relation_id(*key),
                                "from_entity": source_type,
                                "field": source_field,
                                "to_entity": target_type,
                                "target_field": target_field,
                                "source_value_count": len(source_values),
                                "matched_value_count": len(matches),
                                "missing_value_count": len(missing),
                                "coverage_ratio": round(len(matches) / len(source_values), 6),
                            }
                        )
                        break
        return candidates

    @staticmethod
    def _relation_id(
        source_type: str,
        source_field: str,
        target_type: str,
        target_field: str,
    ) -> str:
        """生成与 metadata 编译器一致的关系标识。"""

        value = re.sub(
            r"[^a-zA-Z0-9]+",
            "_",
            f"{source_type}_{source_field}_{target_type}_{target_field}",
        ).strip("_").lower()
        return value or "relation"

    def _validate_relation_gaps(
        self,
        relation_gaps: Any,
        inventory: dict[str, dict[str, Any]],
        closed_relations: Any,
        report: ValidationReport,
    ) -> None:
        """校验未闭合关系是否被报告，并且统计数字与真实库存一致。"""

        expected = {
            item["relation_id"]: item
            for item in self._relation_gap_candidates(inventory)
        }
        if relation_gaps is None:
            relation_gaps = []
        if not isinstance(relation_gaps, list):
            self._error(report, "invalid_relation_gaps", "$.research_report.relation_gaps", "relation_gaps 必须是数组")
            return
        actual: dict[str, dict[str, Any]] = {}
        closed_ids = {
            str(value.get("relation_id"))
            for value in closed_relations
            if isinstance(value, dict) and isinstance(value.get("relation_id"), str)
        }
        for index, item in enumerate(relation_gaps):
            pointer = f"$.research_report.relation_gaps[{index}]"
            if not isinstance(item, dict):
                self._error(report, "invalid_relation_gap", pointer, "关系缺口必须是对象")
                continue
            relation_id = item.get("relation_id")
            if not isinstance(relation_id, str):
                continue
            if relation_id in actual:
                self._error(report, "duplicate_relation_gap", pointer, f"关系缺口 ID 重复：{relation_id}")
            actual[relation_id] = item
            if relation_id in closed_ids:
                self._error(report, "relation_gap_is_closed", pointer, f"已闭合关系不能同时列为缺口：{relation_id}")
            expected_item = expected.get(relation_id)
            if expected_item is None:
                self._error(report, "unknown_relation_gap", pointer, f"关系缺口不是由当前实体库存推导出的候选：{relation_id}")
                continue
            for field in ("from_entity", "field", "to_entity", "target_field"):
                if item.get(field) != expected_item[field]:
                    self._error(
                        report,
                        "relation_gap_mismatch",
                        f"{pointer}.{field}",
                        f"关系缺口字段与实体库存不一致，期望 {expected_item[field]!r}，实际 {item.get(field)!r}",
                    )
            for field in ("source_value_count", "matched_value_count", "missing_value_count"):
                if item.get(field) != expected_item[field]:
                    self._error(
                        report,
                        "relation_gap_count_mismatch",
                        f"{pointer}.{field}",
                        f"关系缺口统计与实体库存不一致，期望 {expected_item[field]}，实际 {item.get(field)!r}",
                    )
            if item.get("coverage_ratio") != expected_item["coverage_ratio"]:
                self._error(
                    report,
                    "relation_gap_ratio_mismatch",
                    f"{pointer}.coverage_ratio",
                    f"关系缺口覆盖比例与实体库存不一致，期望 {expected_item['coverage_ratio']}，实际 {item.get('coverage_ratio')!r}",
                )
        missing = sorted(set(expected) - set(actual))
        if missing:
            self._error(
                report,
                "unreported_relation_gaps",
                "$.research_report.relation_gaps",
                f"检测到未闭合关系但报告没有显式记录：{missing}",
            )

    @staticmethod
    def _error(report: ValidationReport, code: str, path: str, message: str) -> None:
        report.errors.append(ValidationIssue(code=code, path=path, message=message))

    @staticmethod
    def _warning(report: ValidationReport, code: str, path: str, message: str) -> None:
        report.warnings.append(ValidationIssue(code=code, path=path, message=message))

    def _load_json(self, path: Path, report: ValidationReport, pointer: str) -> Any | None:
        if not path.is_file():
            self._error(report, "missing_file", pointer, f"缺少文件：{path.name}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self._error(report, "invalid_json", pointer, f"无法读取 JSON：{error}")
            return None

    def _resource_map(
        self,
        resources: list[Any],
        report: ValidationReport,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for index, resource in enumerate(resources):
            if not isinstance(resource, dict):
                continue
            resource_id = resource.get("resource_id")
            if not isinstance(resource_id, str):
                continue
            if resource_id in result:
                self._error(
                    report,
                    "duplicate_resource_id",
                    f"$.environment.resources[{index}].resource_id",
                    f"resource_id 重复：{resource_id}",
                )
            else:
                result[resource_id] = resource
        return result

    def _validate_resource_references(
        self,
        environment: dict[str, Any],
        resources: dict[str, dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        graph: dict[str, list[str]] = {resource_id: [] for resource_id in resources}
        for resource_id, resource in resources.items():
            sources = resource.get("source_resources", [])
            if not isinstance(sources, list):
                continue
            for source_id in sources:
                if source_id == resource_id:
                    self._error(
                        report,
                        "self_source_reference",
                        f"$.environment.resources.{resource_id}.source_resources",
                        f"资源 {resource_id} 不能引用自身",
                    )
                elif source_id not in resources:
                    self._error(
                        report,
                        "unknown_source_resource",
                        f"$.environment.resources.{resource_id}.source_resources",
                        f"引用了不存在的资源：{source_id}",
                    )
                elif isinstance(source_id, str):
                    graph[resource_id].append(source_id)

        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(resource_id: str, chain: list[str]) -> None:
            if resource_id in visiting:
                cycle = " -> ".join(chain + [resource_id])
                self._error(report, "resource_cycle", "$.environment.resources", f"资源来源形成环：{cycle}")
                return
            if resource_id in visited:
                return
            visiting.add(resource_id)
            for source_id in graph[resource_id]:
                visit(source_id, chain + [resource_id])
            visiting.remove(resource_id)
            visited.add(resource_id)

        for resource_id in graph:
            visit(resource_id, [])

        rules = environment.get("rules", [])
        if isinstance(rules, list):
            for index, rule in enumerate(rules):
                if not isinstance(rule, dict) or not isinstance(rule.get("resources"), list):
                    continue
                for resource_id in rule["resources"]:
                    if resource_id not in resources:
                        self._error(
                            report,
                            "unknown_rule_resource",
                            f"$.environment.rules[{index}].resources",
                            f"规则引用了不存在的资源：{resource_id}",
                        )

    def _validate_resource(
        self,
        workspace_root: Path,
        resource: dict[str, Any],
        pointer: str,
        report: ValidationReport,
        entity_counts: dict[str, int],
    ) -> list[Path]:
        relative = resource.get("path")
        storage_type = resource.get("storage_type")
        if not isinstance(relative, str) or not isinstance(storage_type, str):
            return []
        if "\\" in relative:
            self._error(report, "non_posix_path", f"{pointer}.path", "资源路径必须使用 POSIX 分隔符 /")
            return []

        if storage_type != "file_collection" and any(marker in relative for marker in self._GLOB_MARKERS):
            self._error(report, "unexpected_glob", f"{pointer}.path", "只有 file_collection 可以使用 glob")
            return []

        files: list[Path] = []
        if storage_type == "file_collection":
            if self._resolve_workspace_path(workspace_root, relative, allow_glob=True) is None:
                self._error(report, "unsafe_path", f"{pointer}.path", f"资源路径越过 workspace：{relative}")
                return []
            try:
                matches = sorted(workspace_root.glob(relative))
            except (ValueError, OSError) as error:
                self._error(report, "invalid_glob", f"{pointer}.path", f"无法解析 glob：{error}")
                return []
            files = [path for path in matches if path.is_file() and self._inside(workspace_root, path)]
            if not files:
                self._error(report, "empty_file_collection", f"{pointer}.path", f"glob 没有匹配文件：{relative}")
        else:
            target = self._resolve_workspace_path(workspace_root, relative)
            if target is None:
                self._error(report, "unsafe_path", f"{pointer}.path", f"资源路径越过 workspace：{relative}")
                return []
            if storage_type == "file":
                if not target.is_file():
                    self._error(report, "missing_resource_file", f"{pointer}.path", f"资源文件不存在：{relative}")
                else:
                    files = [target]
            elif storage_type == "directory":
                if not target.is_dir():
                    self._error(report, "missing_resource_directory", f"{pointer}.path", f"资源目录不存在：{relative}")
                else:
                    files = [path for path in target.rglob("*") if path.is_file() and self._inside(workspace_root, path)]
                    if resource.get("data_type") in {"raw", "entity", "derived"} and not files:
                        self._error(
                            report,
                            "empty_resource_directory",
                            f"{pointer}.path",
                            f"业务资源目录不能为空：{relative}",
                        )

        for file_path in files:
            self._validate_file_format(file_path, str(resource.get("format") or ""), pointer, report)

        if resource.get("data_type") == "entity" and isinstance(resource.get("entity_schema"), dict):
            if storage_type == "directory":
                self._error(report, "entity_directory", pointer, "entity 资源不能使用 directory 存储")
            for file_path in files:
                counts = self._validate_entity_file(
                    file_path,
                    str(resource.get("format") or ""),
                    resource["entity_schema"],
                    pointer,
                    report,
                )
                for entity_type, count in counts.items():
                    entity_counts[entity_type] = entity_counts.get(entity_type, 0) + count
        return files

    def _validate_workspace_inventory(
        self,
        workspace_root: Path,
        resources: dict[str, dict[str, Any]],
        resource_files: dict[str, list[Path]],
        report: ValidationReport,
    ) -> None:
        """确保业务目录中的每个文件都被唯一资源登记。"""

        if not workspace_root.is_dir():
            return
        owners: dict[str, list[str]] = {}
        for resource_id, resource in resources.items():
            if resource.get("data_type") not in {"raw", "entity", "derived"}:
                continue
            for path in resource_files.get(resource_id, []):
                relative = path.relative_to(workspace_root).as_posix()
                owners.setdefault(relative, []).append(resource_id)

        for relative in self._business_workspace_files(workspace_root):
            if relative not in owners:
                self._error(
                    report,
                    "unregistered_workspace_file",
                    "$.workspace",
                    f"workspace 业务文件没有对应的 raw/entity/derived 资源：{relative}",
                )
        for relative, resource_ids in owners.items():
            if len(resource_ids) > 1:
                self._error(
                    report,
                    "overlapping_resource_files",
                    "$.environment.resources",
                    f"同一业务文件被多个资源重复登记：{relative} -> {sorted(resource_ids)}",
                )

    @staticmethod
    def _business_workspace_files(workspace_root: Path) -> set[str]:
        files: set[str] = set()
        for bucket in ("raw", "entities", "derived"):
            root = workspace_root / bucket
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    files.add(path.relative_to(workspace_root).as_posix())
        return files

    @staticmethod
    def _is_protocol_metadata_path(relative: str) -> bool:
        """识别不应进入 raw/entity/derived 的流水线元数据文件。"""

        stem = Path(relative).stem.lower()
        return stem in {
            "source_manifest",
            "data_checkpoint",
            "research_request",
            "research_report",
            "sources",
            "metadata",
        }

    def _validate_file_format(
        self,
        path: Path,
        format_name: str,
        pointer: str,
        report: ValidationReport,
    ) -> None:
        try:
            if path.stat().st_size == 0:
                self._error(report, "empty_file", pointer, f"资源文件为空：{path.name}")
                return
            if format_name in {"json", "sarif"}:
                json.loads(path.read_text(encoding="utf-8"))
            elif format_name == "jsonl":
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if line.strip():
                        json.loads(line)
                    else:
                        self._error(report, "blank_jsonl_line", pointer, f"{path.name} 第 {line_number} 行为空")
            elif format_name == "csv":
                with path.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.reader(stream)
                    header = next(reader, None)
                    if not header or any(not name.strip() for name in header):
                        self._error(report, "invalid_csv_header", pointer, f"CSV 缺少有效表头：{path.name}")
            elif format_name == "sqlite":
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                    if not result or result[0] != "ok":
                        self._error(report, "invalid_sqlite", pointer, f"SQLite quick_check 未通过：{path.name}")
                finally:
                    connection.close()
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, sqlite3.Error) as error:
            self._error(report, "invalid_resource_content", pointer, f"资源内容与 {format_name} 不符：{path.name}: {error}")

    def _validate_entity_file(
        self,
        path: Path,
        format_name: str,
        entity_schema: dict[str, Any],
        pointer: str,
        report: ValidationReport,
    ) -> dict[str, int]:
        try:
            if format_name == "json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    self._error(report, "invalid_entity_layout", pointer, f"JSON entity 文件根节点必须是对象：{path.name}")
                    return {}
                expected = set(entity_schema)
                actual = set(payload)
                if actual != expected:
                    self._error(
                        report,
                        "entity_type_mismatch",
                        pointer,
                        f"{path.name} 的实体 key 与 entity_schema 不一致；缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}",
                    )
                return {
                    entity_type: self._validate_records(
                        entity_type,
                        payload.get(entity_type),
                        entity_schema[entity_type],
                        f"{pointer}.entity_schema.{entity_type}",
                        report,
                    )

                    for entity_type in expected
                }

            if format_name == "jsonl":
                entity_type = self._single_entity_type(entity_schema, pointer, report)
                if entity_type is None:
                    return {}
                records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                return {
                    entity_type: self._validate_records(
                        entity_type, records, entity_schema[entity_type], pointer, report
                    )
                }

            if format_name == "csv":
                entity_type = self._single_entity_type(entity_schema, pointer, report)
                if entity_type is None:
                    return {}
                with path.open("r", encoding="utf-8", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                return {
                    entity_type: self._validate_records(
                        entity_type,
                        rows,
                        entity_schema[entity_type],
                        pointer,
                        report,
                        csv_values=True,
                    )
                }

            if format_name == "sqlite":
                return self._validate_sqlite_entities(path, entity_schema, pointer, report)

            if format_name == "parquet":
                try:
                    import pyarrow.parquet as parquet  # type: ignore[import-not-found]
                except ImportError:
                    self._error(
                        report,
                        "parquet_validator_unavailable",
                        pointer,
                        "当前 DataGen 未安装 pyarrow，entity 资源请改用 json、jsonl、csv 或 sqlite",
                    )
                    return {}
                entity_type = self._single_entity_type(entity_schema, pointer, report)
                if entity_type is None:
                    return {}
                records = parquet.read_table(path).to_pylist()
                return {
                    entity_type: self._validate_records(
                        entity_type, records, entity_schema[entity_type], pointer, report
                    )
                }
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error, sqlite3.Error) as error:
            self._error(report, "invalid_entity_content", pointer, f"无法校验实体文件 {path.name}：{error}")
        return {}

    def _validate_sqlite_entities(
        self,
        path: Path,
        entity_schema: dict[str, Any],
        pointer: str,
        report: ValidationReport,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if not str(row[0]).startswith("sqlite_")
            }
            if tables != set(entity_schema):
                self._error(report, "entity_type_mismatch", pointer, f"SQLite 表与 entity_schema 不一致：{sorted(tables)}")
            for entity_type, definition in entity_schema.items():
                if entity_type not in tables:
                    counts[entity_type] = 0
                    continue
                # 表名来自 Schema 且只允许 snake_case；双引号仍做转义以封闭 SQL 标识符。
                quoted = entity_type.replace('"', '""')
                rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{quoted}"')]
                counts[entity_type] = self._validate_records(
                    entity_type, rows, definition, pointer, report
                )
        finally:
            connection.close()
        return counts

    def _single_entity_type(
        self,
        entity_schema: dict[str, Any],
        pointer: str,
        report: ValidationReport,
    ) -> str | None:
        if len(entity_schema) != 1:
            self._error(report, "multiple_entities_in_tabular_file", pointer, "jsonl、csv 和 parquet 资源只能声明一种实体")
            return None
        return next(iter(entity_schema))

    def _validate_records(
        self,
        entity_type: str,
        records: Any,
        definition: Any,
        pointer: str,
        report: ValidationReport,
        *,
        csv_values: bool = False,
    ) -> int:
        if not isinstance(records, list):
            self._error(report, "invalid_entity_records", pointer, f"实体 {entity_type} 的值必须是记录数组")
            return 0
        if not records:
            self._error(report, "empty_entity", pointer, f"实体 {entity_type} 没有记录")
            return 0
        fields = self._declared_field_types(definition)
        if not isinstance(fields, dict):
            return len(records)

        # 普通实体必须有稳定选择键，工具和任务才能把一次查询结果传给
        # inspect/update 等后续操作。*_link 这类桥接实体允许使用复合外键。
        primary_id = self._primary_id_field(entity_type, fields)
        id_field_count = sum(
            1
            for field_name in fields
            if field_name == "id" or field_name == "entity_id" or field_name.endswith("_id")
        )
        is_bridge = entity_type.endswith("_link") or id_field_count >= 2 and primary_id is None
        if primary_id is None and not is_bridge:
            self._error(
                report,
                "missing_entity_primary_key",
                pointer,
                f"实体 {entity_type} 必须声明 {entity_type}_id、entity_id 或 id 作为稳定主键",
            )
        seen_ids: set[str] = set()
        valid_records = 0
        for index, record in enumerate(records):
            record_pointer = f"{pointer}.records[{index}]"
            if not isinstance(record, dict):
                self._error(report, "invalid_entity_record", record_pointer, "实体记录必须是对象")
                continue
            record_valid = True
            missing = [field_name for field_name in fields if field_name not in record or record[field_name] is None]
            if missing:
                self._error(report, "missing_entity_fields", record_pointer, f"缺少必需且非空字段：{missing}")
                record_valid = False
            extra = sorted(set(record) - set(fields))
            if extra:
                self._error(report, "undeclared_entity_fields", record_pointer, f"包含 entity_schema 未声明字段：{extra}")
                record_valid = False
            for field_name, expected_type in fields.items():
                if field_name not in record or record[field_name] is None:
                    continue
                if not self._matches_type(record[field_name], expected_type, csv_values=csv_values):
                    self._error(
                        report,
                        "entity_field_type",
                        f"{record_pointer}.{field_name}",
                        f"字段应为 {expected_type}，实际值为 {record[field_name]!r}",
                    )
                    record_valid = False
            if primary_id is not None and primary_id in record:
                value = record[primary_id]
                if value is None or (isinstance(value, str) and not value.strip()):
                    self._error(report, "empty_entity_id", f"{record_pointer}.{primary_id}", "稳定主键不能为空")
                    record_valid = False
                key = self._value_key(value)
                if key in seen_ids:
                    self._error(report, "duplicate_entity_id", f"{record_pointer}.{primary_id}", f"实体 ID 重复：{value}")
                    record_valid = False
                else:
                    seen_ids.add(key)
            if record_valid:
                valid_records += 1
        return valid_records

    @staticmethod
    def _primary_id_field(entity_type: str, fields: Any) -> str | None:
        if not isinstance(fields, (dict, set, list, tuple)):
            return None
        return _metadata_choose_primary_id(str(entity_type), {str(field) for field in fields})

    @staticmethod
    def _declared_field_types(definition: Any) -> dict[str, str]:
        """从实体记录 Schema 提取内部使用的字段类型映射。

        新协议使用 ``properties`` 节点；读取旧测试材料时暂时接受
        ``fields`` 简写，避免内部画像/关系分析因协议升级而失去诊断能力。
        旧简写不会通过正式 validation/environment.schema.json，也不会被重新写入环境。
        """

        if not isinstance(definition, dict):
            return {}
        properties = definition.get("properties")
        if isinstance(properties, dict):
            return {
                str(field): str(spec.get("type"))
                for field, spec in properties.items()
                if isinstance(field, str) and isinstance(spec, dict) and isinstance(spec.get("type"), str)
            }
        fields = definition.get("fields")
        if isinstance(fields, dict):
            return {
                str(field): (
                    str(field_type.get("type"))
                    if isinstance(field_type, dict)
                    else str(field_type)
                )
                for field, field_type in fields.items()
                if isinstance(field, str)
                and (
                    isinstance(field_type, str)
                    or (isinstance(field_type, dict) and isinstance(field_type.get("type"), str))
                )
            }
        return {}

    @staticmethod
    def _is_bridge_entity(entity_type: str, fields: Any) -> bool:
        if not isinstance(fields, (dict, set, list, tuple)):
            return False
        return _metadata_is_bridge_entity(str(entity_type), {str(field) for field in fields})

    @staticmethod
    def _relation_field_compatible(
        source_field: str,
        target_type: str,
        target_field: str,
    ) -> bool:
        def tokens(value: str) -> set[str]:
            result: set[str] = set()
            for token in re.findall(r"[a-z0-9]+", value.lower()):
                if token.endswith("ies") and len(token) > 4:
                    token = f"{token[:-3]}y"
                elif token.endswith("s") and len(token) > 3:
                    token = token[:-1]
                result.add(token)
            return result

        source_base = source_field[:-3] if source_field.endswith("_id") else source_field
        target_base = target_field[:-3] if target_field.endswith("_id") else target_field
        if source_base in {"id", "entity"} or target_base in {"id", "entity"}:
            return False
        source_tokens = tokens(source_base)
        target_tokens = tokens(target_base)
        entity_tokens = tokens(target_type)
        target_is_code = _metadata_is_identifier_field(target_field) and not target_field.lower().endswith("_id")
        # 目标字段通常已经与目标实体同名；不能仅因
        # ``target_tokens ⊆ entity_tokens``（这对大多数主键天然成立）就把
        # 任意 source_field 视作外键，否则 country_id 会错误指向所有带 ID
        # 的实体。至少需要 source 与 target 字段或目标实体共享业务词。
        return bool(
            source_tokens
            and target_tokens
            and (
                bool(source_tokens.intersection(target_tokens))
                or (
                    bool(source_tokens.intersection(entity_tokens))
                    and (
                        bool(target_tokens.intersection(entity_tokens))
                        or target_is_code
                    )
                )
            )
        )

    @staticmethod
    def _value_key(value: Any) -> str:
        """把可能不可哈希的坏值也转换成可比较的稳定键。"""

        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _matches_type(value: Any, expected_type: Any, *, csv_values: bool) -> bool:
        if csv_values:
            if not isinstance(value, str) or value == "":
                return False
            try:
                if expected_type == "integer":
                    int(value)
                elif expected_type == "number":
                    float(value)
                elif expected_type == "boolean" and value.strip().lower() not in {"true", "false", "1", "0"}:
                    return False
                return expected_type in {"string", "integer", "number", "boolean"}
            except ValueError:
                return False
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        return False

    def _validate_provenance(
        self,
        path: Path,
        workspace_root: Path,
        resources: dict[str, dict[str, Any]],
        resource_files: dict[str, list[Path]],
        report: ValidationReport,
    ) -> list[dict[str, Any]]:
        payload = self._load_json(path, report, "$.provenance")
        if not isinstance(payload, dict):
            return []
        if set(payload) != {"schema_version", "sources"}:
            self._error(report, "invalid_provenance_fields", "$.provenance", "sources.json 顶层只能包含 schema_version 和 sources")
        if payload.get("schema_version") != "1.0":
            self._error(report, "invalid_provenance_version", "$.provenance.schema_version", "provenance schema_version 必须为 1.0")
        sources = payload.get("sources")
        if not isinstance(sources, list):
            self._error(report, "invalid_sources", "$.provenance.sources", "sources 必须是数组")
            return []

        raw_resources = {
            resource_id for resource_id, resource in resources.items() if resource.get("data_type") == "raw"
        }
        if not raw_resources:
            self._error(report, "missing_raw_resource", "$.environment.resources", "真实数据环境至少需要一个 raw 资源")

        seen_ids: set[str] = set()
        covered_resources: set[str] = set()
        covered_files: set[str] = set()
        for index, source in enumerate(sources):
            pointer = f"$.provenance.sources[{index}]"
            if not isinstance(source, dict):
                self._error(report, "invalid_source", pointer, "来源记录必须是对象")
                continue
            required = {
                "source_id",
                "url",
                "source_type",
                "retrieved_at",
                "license_or_access_note",
                "resource_ids",
                "files",
            }
            allowed = required
            missing = sorted(required - set(source))
            extra = sorted(set(source) - allowed)
            if missing:
                self._error(report, "missing_source_fields", pointer, f"来源记录缺少字段：{missing}")
            if extra:
                self._error(report, "unknown_source_fields", pointer, f"来源记录包含未定义字段：{extra}")

            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                self._error(report, "invalid_source_id", f"{pointer}.source_id", "source_id 必须是非空字符串")
            elif source_id in seen_ids:
                self._error(report, "duplicate_source_id", f"{pointer}.source_id", f"source_id 重复：{source_id}")
            elif not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", source_id):
                self._error(report, "invalid_source_id", f"{pointer}.source_id", "source_id 必须使用小写 snake_case")
            else:
                seen_ids.add(source_id)

            parsed = urlparse(str(source.get("url") or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                self._error(report, "invalid_source_url", f"{pointer}.url", "来源 URL 必须是完整 http/https 地址")
            self._validate_source_scope(parsed.hostname, f"{pointer}.url", report)
            if source.get("source_type") not in self._SOURCE_TYPES:
                self._error(report, "invalid_source_type", f"{pointer}.source_type", f"source_type 必须是 {sorted(self._SOURCE_TYPES)} 之一")
            self._validate_timestamp(source.get("retrieved_at"), f"{pointer}.retrieved_at", report)
            if not isinstance(source.get("license_or_access_note"), str) or not source["license_or_access_note"].strip():
                self._error(report, "missing_license_note", f"{pointer}.license_or_access_note", "必须说明许可证或访问限制")

            source_resource_ids = source.get("resource_ids")
            if not isinstance(source_resource_ids, list) or not source_resource_ids:
                self._error(report, "invalid_source_resources", f"{pointer}.resource_ids", "resource_ids 必须是非空数组")
                source_resource_ids = []
            for resource_id in source_resource_ids:
                if resource_id not in raw_resources:
                    self._error(report, "non_raw_provenance_resource", f"{pointer}.resource_ids", f"来源只能直接关联 raw 资源：{resource_id}")
                elif isinstance(resource_id, str):
                    covered_resources.add(resource_id)

            source_files = source.get("files")
            if not isinstance(source_files, list) or not source_files:
                self._error(report, "invalid_source_files", f"{pointer}.files", "files 必须是非空数组")
                source_files = []
            allowed_paths = {
                file_path.resolve()
                for resource_id in source_resource_ids
                for file_path in resource_files.get(str(resource_id), [])
            }
            for file_index, file_item in enumerate(source_files):
                file_pointer = f"{pointer}.files[{file_index}]"
                if not isinstance(file_item, dict) or not isinstance(file_item.get("path"), str):
                    self._error(report, "invalid_source_file", file_pointer, "文件记录必须包含字符串 path")
                    continue
                if set(file_item) - {"path", "sha256"}:
                    self._error(report, "unknown_source_file_fields", file_pointer, "文件记录只能包含 path 和由校验器生成的 sha256")
                file_path = self._resolve_workspace_path(workspace_root, file_item["path"])
                if file_path is None or not file_path.is_file():
                    self._error(report, "missing_source_file", file_pointer, f"来源文件不存在或越界：{file_item['path']}")
                    continue
                if file_path.resolve() not in allowed_paths:
                    self._error(report, "source_file_resource_mismatch", file_pointer, f"文件不属于该来源声明的 raw 资源：{file_item['path']}")
                covered_files.add(file_path.relative_to(workspace_root).as_posix())
                sha256 = file_item.get("sha256")
                if sha256 is not None and (
                    not isinstance(sha256, str)
                    or len(sha256) != 64
                    or any(character not in "0123456789abcdef" for character in sha256)
                ):
                    self._error(report, "invalid_sha256", f"{file_pointer}.sha256", "sha256 必须是 64 位小写十六进制字符串")
                elif isinstance(sha256, str) and sha256 != self._sha256(file_path):
                    self._error(report, "sha256_mismatch", f"{file_pointer}.sha256", f"文件哈希与内容不一致：{file_item['path']}")

        for resource_id in sorted(raw_resources - covered_resources):
            self._error(report, "untraced_raw_resource", "$.provenance.sources", f"raw 资源没有来源记录：{resource_id}")
        expected_raw_files = {
            path.relative_to(workspace_root).as_posix()
            for resource_id in raw_resources
            for path in resource_files.get(resource_id, [])
        }
        for relative_path in sorted(expected_raw_files - covered_files):
            self._error(report, "untraced_raw_file", "$.provenance.sources", f"raw 文件没有来源记录：{relative_path}")
        return [source for source in sources if isinstance(source, dict)]

    def _validate_source_scope(
        self,
        hostname: str | None,
        pointer: str,
        report: ValidationReport,
    ) -> None:
        """限制来源注册域必须属于当前种子，防止调研主题无边界漂移。"""

        if self.research_request is None:
            return
        quality_policy = self.research_request.get("quality_policy", {})
        source_scope = quality_policy.get("source_scope", {})
        allowed = {
            str(value).lower().strip(".")
            for value in source_scope.get("allowed_registered_domains", [])
            if isinstance(value, str) and value.strip()
        }
        if not allowed or not isinstance(hostname, str) or not hostname.strip():
            return
        host = hostname.lower().strip(".")
        labels = host.split(".")
        registered = ".".join(labels[-2:]) if len(labels) >= 2 else host
        if registered in allowed:
            return
        self._error(
            report,
            "source_outside_seed_scope",
            pointer,
            f"来源域 {host} 不在种子允许的业务来源域 {sorted(allowed)} 内",
        )

    def _validate_timestamp(self, value: Any, pointer: str, report: ValidationReport) -> None:
        if not isinstance(value, str):
            self._error(report, "invalid_timestamp", pointer, "retrieved_at 必须是 ISO 8601 字符串")
            return
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            self._error(report, "invalid_timestamp", pointer, "retrieved_at 不是合法 ISO 8601 时间")
            return
        if parsed.tzinfo is None:
            self._error(report, "timezone_required", pointer, "retrieved_at 必须包含时区")

    @classmethod
    def _resolve_workspace_path(
        cls,
        workspace_root: Path,
        relative: str,
        *,
        allow_glob: bool = False,
    ) -> Path | None:
        pure = PurePosixPath(relative)
        raw_parts = relative.split("/")
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in raw_parts):
            return None
        if not allow_glob and any(any(marker in part for marker in cls._GLOB_MARKERS) for part in pure.parts):
            return None
        fixed_parts: list[str] = []
        for part in pure.parts:
            if allow_glob and any(marker in part for marker in cls._GLOB_MARKERS):
                break
            fixed_parts.append(part)
        fixed_path = workspace_root.joinpath(*fixed_parts).resolve()
        return workspace_root.joinpath(*pure.parts) if cls._inside(workspace_root, fixed_path) else None

    @staticmethod
    def _inside(root: Path, path: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
