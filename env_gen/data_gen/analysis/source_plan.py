from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from env_gen.data_gen.config import CollectionPolicy

from .file_formats import normalize_file_format


_TERMINAL_ACCESS_FAILURES = {
    "authentication_required",
    "forbidden",
    "unreachable",
    "not_found",
}

_ATTEMPT_CODES_BY_ACCESS_STATUS = {
    "authentication_required": {"authentication_required", "authentication_page"},
    "forbidden": {"access_forbidden"},
    "rate_limited": {"rate_limited"},
    "unreachable": {
        "network_error",
        "download_timeout",
        "source_http_error",
    },
    "not_found": {"not_found"},
}

_RESOLVED_SOURCE_STATUSES = {"complete", "blocked", "unavailable"}
_UNAVAILABLE_ATTEMPT_CODES = {"http_error", "invalid_content"}
_LOCAL_BUDGET_ATTEMPT_CODES = {
    "single_file_budget_exceeded",
    "raw_budget_exceeded",
    "raw_file_budget_exceeded",
}
_TERMINAL_DOWNLOAD_ATTEMPT_CODES = {
    "authentication_required",
    "authentication_page",
    "access_forbidden",
    "not_found",
    *_LOCAL_BUDGET_ATTEMPT_CODES,
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collection_control_payload(
    run_dir: Path,
    name: str,
) -> dict[str, Any] | None:
    """读取运行中控制文件；发布后回退到结构化审计归档。"""

    live_path = run_dir / ".datagen" / name
    try:
        if live_path.is_file():
            payload = json.loads(live_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        audit_path = run_dir / "provenance" / "collection_audit.json"
        if not audit_path.is_file():
            return None
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(audit, dict):
            return None
        controls = audit.get("control_records")
        payload = controls.get(name) if isinstance(controls, dict) else None
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class SourcePlanIssue:
    """来源计划的一条确定性错误。"""

    code: str
    path: str
    message: str


class SourcePlanValidator:
    """校验 Agent 声明的来源、获取状态和实际 Raw 文件之间的对应关系。"""

    def __init__(
        self,
        schema_path: Path,
        collection_policy: CollectionPolicy | None = None,
    ) -> None:
        self.schema_path = schema_path.resolve()
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(
            self.schema,
            format_checker=FormatChecker(),
        )
        self.collection_policy = collection_policy or CollectionPolicy()

    def validate(
        self,
        plan_path: Path,
        *,
        seed_global_id: str,
        seed_sha256: str,
        checkpoint: dict[str, Any],
        scenario_research: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[SourcePlanIssue]]:
        issues: list[SourcePlanIssue] = []
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, [
                SourcePlanIssue(
                    "missing_source_plan",
                    "$.source_plan",
                    "缺少 provenance/source_plan.json；Agent 必须先保存来源计划",
                )
            ]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return None, [
                SourcePlanIssue(
                    "invalid_source_plan_json",
                    "$.source_plan",
                    f"source_plan.json 无法读取：{error}",
                )
            ]

        if not isinstance(plan, dict):
            return None, [
                SourcePlanIssue(
                    "invalid_source_plan",
                    "$.source_plan",
                    "source_plan.json 根节点必须是对象",
                )
            ]

        for error in sorted(self.validator.iter_errors(plan), key=lambda item: list(item.path)):
            pointer = "$.source_plan"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            issues.append(
                SourcePlanIssue("source_plan_schema", pointer, error.message)
            )

        # Keep the distinction between task-facing files and collection evidence
        # explicit. A format cannot be both a required final Scope format and an
        # evidence-only format, otherwise the quality gate has an ambiguous input.
        required_formats = {
            normalize_file_format(value)
            for value in plan.get("required_file_formats", [])
            if isinstance(value, str)
        }
        evidence_formats = {
            normalize_file_format(value)
            for value in plan.get("evidence_file_formats", [])
            if isinstance(value, str)
        }
        overlap = sorted(required_formats & evidence_formats)
        if overlap:
            issues.append(
                SourcePlanIssue(
                    "file_format_role_conflict",
                    "$.source_plan",
                    "同一格式不能同时作为最终 Scope 格式和仅来源证据格式："
                    + ", ".join(overlap),
                )
            )

        if plan.get("seed_global_id") != seed_global_id:
            issues.append(
                SourcePlanIssue(
                    "source_plan_seed_id_mismatch",
                    "$.source_plan.seed_global_id",
                    "来源计划没有引用本次 Seed global_id",
                )
            )
        if plan.get("seed_sha256") != seed_sha256:
            issues.append(
                SourcePlanIssue(
                    "source_plan_seed_hash_mismatch",
                    "$.source_plan.seed_sha256",
                    "来源计划没有引用本次选中 Seed 的 SHA-256",
                )
            )

        raw_files = {
            value
            for value in checkpoint.get("raw_files", [])
            if isinstance(value, str)
        }
        source_urls = {
            value
            for value in checkpoint.get("source_urls", [])
            if isinstance(value, str)
        }
        seen_source_ids: set[str] = set()
        plan_raw_files: set[str] = set()
        run_dir = plan_path.resolve().parents[1]
        download_attempts: list[dict[str, Any]] = []
        download_receipts: dict[str, dict[str, Any]] = {}
        attempts_payload = _collection_control_payload(run_dir, "download_attempts.json")
        if attempts_payload is not None:
            download_attempts = [
                item
                for item in attempts_payload.get("attempts", [])
                if isinstance(item, dict)
            ]
        receipts_payload = _collection_control_payload(
            run_dir,
            "download_receipts.json",
        )
        if receipts_payload is not None:
            download_receipts = {
                str(item.get("path")): item
                for item in receipts_payload.get("downloads", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
        for index, source in enumerate(plan.get("sources", [])):
            if not isinstance(source, dict):
                continue
            pointer = f"$.source_plan.sources[{index}]"
            source_id = source.get("source_id")
            if isinstance(source_id, str):
                if source_id in seen_source_ids:
                    issues.append(
                        SourcePlanIssue(
                            "duplicate_source_id",
                            f"{pointer}.source_id",
                            f"来源 ID 重复：{source_id}",
                        )
                    )
                seen_source_ids.add(source_id)
            url = source.get("url")
            registered_urls = {
                str(value)
                for value in source.get("registered_urls", [])
                if isinstance(value, str)
            }
            if isinstance(url, str) and url not in registered_urls:
                issues.append(
                    SourcePlanIssue(
                        "primary_source_url_not_registered",
                        f"{pointer}.registered_urls",
                        "来源的主 URL 必须同时出现在 registered_urls 中",
                    )
                )
            status = source.get("status")
            access_status = source.get("access_status", "unknown")
            # 失败证据必须绑定清单中登记的精确 URL。只按 source_id 匹配会
            # 允许一个人为构造的同域 404 冒充真实数据面的 not_found 证据。
            attempts_for_url = [
                item
                for item in download_attempts
                if item.get("url") == url
            ]
            if status in {"in_progress", "complete"} and access_status not in {"unknown", "public"}:
                issues.append(
                    SourcePlanIssue(
                        "collected_source_not_public",
                        f"{pointer}.access_status",
                            "已经采集的来源必须标记为 public",
                    )
                )
            if status == "blocked":
                evidence = source.get("status_evidence")
                evidence_type = evidence.get("type") if isinstance(evidence, dict) else None
                if access_status not in _TERMINAL_ACCESS_FAILURES | {"rate_limited"}:
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_without_access_reason",
                            f"{pointer}.access_status",
                            "blocked 数据面必须说明认证、禁止访问、限流、不可达或不存在",
                        )
                    )
                if evidence_type != "source_unavailable":
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_without_unavailable_evidence",
                            f"{pointer}.status_evidence",
                            "blocked 数据面必须使用 source_unavailable 记录实际访问证据",
                        )
                    )
                expected_attempt_codes = _ATTEMPT_CODES_BY_ACCESS_STATUS.get(
                    str(access_status),
                    set(),
                )
                matching_attempts = [
                    item
                    for item in attempts_for_url
                    if item.get("status") == "failed"
                    and item.get("code") in expected_attempt_codes
                ]
                if expected_attempt_codes and not matching_attempts:
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_without_download_attempt",
                            f"{pointer}.url",
                            (
                                f"access_status={access_status} 必须有 datagenctl download "
                                "产生的同类型失败尝试记录"
                            ),
                        )
                    )
                if not str(source.get("access_note") or "").strip():
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_without_access_note",
                            f"{pointer}.access_note",
                            "blocked 数据面必须用 access_note 说明实际访问结果和替代来源调查情况",
                        )
                    )
                if int(source.get("record_count", 0) or 0) != 0:
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_with_records",
                            f"{pointer}.record_count",
                            "blocked 表示该 URL 无法取得数据，record_count 必须为 0",
                        )
                    )
                if source.get("raw_files"):
                    issues.append(
                        SourcePlanIssue(
                            "blocked_source_with_raw_files",
                            f"{pointer}.raw_files",
                            "blocked 数据源不能关联成功安装的 Raw；错误响应只保留在下载审计中",
                        )
                    )
            elif status == "unavailable":
                evidence = source.get("status_evidence")
                evidence_type = evidence.get("type") if isinstance(evidence, dict) else None
                if access_status != "public":
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_not_public",
                            f"{pointer}.access_status",
                            "unavailable 只表示公开端点可访问但没有可用业务记录；访问障碍应使用 blocked",
                        )
                    )
                if source.get("coverage_strategy") != "unavailable":
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_wrong_mode",
                            f"{pointer}.coverage_strategy",
                            "status=unavailable 必须同时使用 coverage_strategy=unavailable",
                        )
                    )
                if evidence_type not in {"no_usable_records", "local_budget_exceeded"}:
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_without_evidence",
                            f"{pointer}.status_evidence",
                            (
                                "unavailable 数据面必须使用 no_usable_records 说明空结果/无效响应，"
                                "或使用 local_budget_exceeded 说明公开数据超过本地采集预算"
                            ),
                        )
                    )
                if not str(source.get("access_note") or "").strip():
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_without_access_note",
                            f"{pointer}.access_note",
                            "unavailable 数据面必须用 access_note 说明实际响应及无法安装 Raw 的原因",
                        )
                    )
                accepted_attempt_codes = (
                    _LOCAL_BUDGET_ATTEMPT_CODES
                    if evidence_type == "local_budget_exceeded"
                    else _UNAVAILABLE_ATTEMPT_CODES
                )
                exact_evidence = [
                    item
                    for item in attempts_for_url
                    if (
                        evidence_type != "local_budget_exceeded"
                        and item.get("status") in {"downloaded", "reused"}
                    )
                    or (
                        item.get("status") == "failed"
                        and item.get("code") in accepted_attempt_codes
                    )
                ]
                if not exact_evidence:
                    expected = (
                        "single_file_budget_exceeded/raw_budget_exceeded/"
                        "raw_file_budget_exceeded"
                        if evidence_type == "local_budget_exceeded"
                        else "成功空结果或 invalid_content/http_error"
                    )
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_without_download_attempt",
                            f"{pointer}.url",
                            f"unavailable 必须有该精确 URL 的 {expected} 记录",
                        )
                    )
                if int(source.get("record_count", 0) or 0) != 0:
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_source_with_records",
                            f"{pointer}.record_count",
                            "unavailable 数据面不能同时声明已采集业务记录",
                        )
                    )
            terminal_attempts = [
                item
                for item in attempts_for_url
                if item.get("code") in _TERMINAL_DOWNLOAD_ATTEMPT_CODES
            ]
            if len(terminal_attempts) > 1:
                issues.append(
                    SourcePlanIssue(
                        "terminal_source_retried",
                        f"{pointer}.url",
                        (
                            f"该精确 URL 已产生 {len(terminal_attempts)} 次不可重试失败；"
                            "首个终态证据出现后必须停止网络访问"
                        ),
                    )
                )
            if len(terminal_attempts) > self.collection_policy.max_source_attempts:
                issues.append(
                    SourcePlanIssue(
                        "source_attempt_limit_exceeded",
                        f"{pointer}.url",
                        (
                            f"确定性不可访问来源已尝试 {len(terminal_attempts)} 次，超过上限 "
                            f"{self.collection_policy.max_source_attempts}；应停止重试并寻找公开替代来源"
                        ),
                    )
                )
            if status in {"in_progress", "complete", "unavailable"} and source.get("raw_files") and url not in source_urls:
                issues.append(
                    SourcePlanIssue(
                        "source_url_not_in_checkpoint",
                        f"{pointer}.url",
                        f"已经采集的来源 URL 未登记在 checkpoint.source_urls：{url}",
                    )
                )
            raw_content_groups: dict[str, list[str]] = {}
            for raw_path in source.get("raw_files", []):
                if raw_path in plan_raw_files:
                    issues.append(
                        SourcePlanIssue(
                            "raw_file_in_multiple_sources",
                            f"{pointer}.raw_files",
                            f"同一个 Raw 文件不能同时归属于多个来源：{raw_path}",
                        )
                    )
                plan_raw_files.add(raw_path)
                receipt = download_receipts.get(str(raw_path))
                if (
                    receipt is not None
                    and receipt.get("source_id") is not None
                    and receipt.get("source_id") != source_id
                ):
                    issues.append(
                        SourcePlanIssue(
                            "raw_file_source_receipt_mismatch",
                            f"{pointer}.raw_files",
                            (
                                f"Raw 文件 {raw_path} 的下载收据属于来源 "
                                f"{receipt.get('source_id')}，不能登记到 {source_id}"
                            ),
                        )
                    )
                if raw_path not in raw_files:
                    issues.append(
                        SourcePlanIssue(
                            "source_raw_file_not_in_checkpoint",
                            f"{pointer}.raw_files",
                            f"来源引用的 Raw 文件未登记在 checkpoint：{raw_path}",
                        )
                    )
                raw_sha256 = receipt.get("sha256") if receipt is not None else None
                if not isinstance(raw_sha256, str) or len(raw_sha256) != 64:
                    raw_file = run_dir / "workspace" / raw_path
                    if raw_file.is_file():
                        try:
                            raw_sha256 = _file_sha256(raw_file)
                        except OSError:
                            raw_sha256 = None
                if isinstance(raw_sha256, str):
                    raw_content_groups.setdefault(raw_sha256, []).append(raw_path)
            if status == "complete" and not source.get("status_evidence"):
                issues.append(
                    SourcePlanIssue(
                        "complete_source_without_evidence",
                        f"{pointer}.status_evidence",
                        "status=complete 必须说明分页结束、总量取完或抽样策略已完成",
                    )
                )
            evidence = source.get("status_evidence")
            evidence_type = evidence.get("type") if isinstance(evidence, dict) else None
            retrieval = source.get("retrieval")
            reported_total = (
                retrieval.get("reported_total")
                if isinstance(retrieval, dict)
                else None
            )
            record_count = int(source.get("record_count", 0) or 0)
            if (
                status == "complete"
                and record_count == 0
                and not source.get("raw_files")
            ):
                issues.append(
                    SourcePlanIssue(
                        "complete_source_without_collected_data",
                        f"{pointer}.status",
                        (
                            "complete 来源必须至少包含真实记录或 Raw 文件；公开空结果使用 "
                            "no_usable_records，超过本地预算使用 local_budget_exceeded"
                        ),
                    )
                )
            if (
                status == "complete"
                and evidence_type == "reported_total_reached"
                and isinstance(reported_total, int)
                and record_count < reported_total
            ):
                issues.append(
                    SourcePlanIssue(
                        "reported_total_not_reached",
                        f"{pointer}.record_count",
                        f"声称 reported_total_reached，但实际声明采集 {record_count} < 总量 {reported_total}",
                    )
                )
            if (
                status == "complete"
                and source.get("coverage_strategy") == "representative_sample"
                and evidence_type != "sampling_target_reached"
            ):
                issues.append(
                    SourcePlanIssue(
                        "bounded_source_without_policy_evidence",
                        f"{pointer}.status_evidence",
                        "representative_sample 数据面必须使用 sampling_target_reached 说明分层采集已经完成",
                    )
                )
            if status == "complete" and source.get("coverage_strategy") == "representative_sample":
                units_collected = (
                    int(retrieval.get("units_collected", 0) or 0)
                    if isinstance(retrieval, dict)
                    else 0
                )
                duplicate_content_groups = [
                    paths
                    for paths in raw_content_groups.values()
                    if len(paths) > 1
                ]
                if (
                    duplicate_content_groups
                    and units_collected > len(raw_content_groups)
                ):
                    duplicate_examples = "; ".join(
                        ", ".join(paths[:4])
                        for paths in duplicate_content_groups[:3]
                    )
                    issues.append(
                        SourcePlanIssue(
                            "duplicate_raw_content_counted_as_pages",
                            f"{pointer}.retrieval.units_collected",
                            (
                                f"来源声明采集 {units_collected} 个分页/抽样单元，"
                                f"但 {len(raw_content_groups)} 份独立 Raw 内容中存在重复响应；"
                                "完全相同的响应只能计为一个单元。"
                                f"重复组：{duplicate_examples}"
                            ),
                        )
                    )
                population_not_exhausted = (
                    not isinstance(reported_total, int)
                    or record_count < reported_total
                )
                if (
                    population_not_exhausted
                    and record_count
                    < self.collection_policy.min_sample_records
                    and units_collected
                    < self.collection_policy.min_sample_units
                ):
                    issues.append(
                        SourcePlanIssue(
                            "shallow_bounded_source",
                            f"{pointer}.status_evidence",
                            (
                                "representative_sample 尚未形成可审计的分层样本：当前仅 "
                                f"{record_count} 条、{units_collected} 个分页/分层单元；"
                                "不能把单页浅样本声明为完成"
                            ),
                        )
                    )
            if status in {"in_progress", "complete"} and record_count > 0 and not source.get("raw_files"):
                issues.append(
                    SourcePlanIssue(
                        "collected_source_without_raw_files",
                        f"{pointer}.raw_files",
                        "声明已采集记录的数据面必须引用至少一个实际 raw 文件",
                    )
                )

        missing_plan_files = raw_files - plan_raw_files
        if missing_plan_files:
            issues.append(
                SourcePlanIssue(
                    "checkpoint_raw_file_missing_source",
                    "$.source_plan.sources",
                    f"checkpoint 中的 Raw 文件没有归属来源：{sorted(missing_plan_files)}",
                )
            )

        if checkpoint.get("status") == "insufficient_public_data":
            core_sources = [
                source
                for source in plan.get("sources", [])
                if isinstance(source, dict) and source.get("priority") == "core"
            ]
            if not core_sources:
                issues.append(
                    SourcePlanIssue(
                        "insufficient_without_core_source",
                        "$.source_plan.sources",
                        "insufficient_public_data 至少需要一个已经实际调查的核心来源",
                    )
                )
            unsettled = [
                str(source.get("source_id") or "unknown")
                for source in core_sources
                if source.get("status") not in _RESOLVED_SOURCE_STATUSES
            ]
            if unsettled:
                issues.append(
                    SourcePlanIssue(
                        "insufficient_with_unsettled_core_source",
                        "$.source_plan.sources",
                        f"核心来源仍未调查完成，不能声明公开数据不存在：{unsettled}",
                    )
                )
            positive_core = [
                str(source.get("source_id") or "unknown")
                for source in core_sources
                if int(source.get("record_count", 0) or 0) > 0
            ]
            if positive_core:
                issues.append(
                    SourcePlanIssue(
                        "insufficient_with_collected_core_records",
                        "$.source_plan.sources",
                        (
                            "已经取得核心业务记录时应使用 exhausted 表示天然不丰富，"
                            f"不能使用 insufficient_public_data：{positive_core}"
                        ),
                    )
                )
            for index, source in enumerate(core_sources):
                if source.get("status") != "complete":
                    continue
                url = source.get("url")
                successful_attempts = [
                    item
                    for item in download_attempts
                    if item.get("url") == url
                    and item.get("status") in {"downloaded", "reused"}
                ]
                if not successful_attempts:
                    issues.append(
                        SourcePlanIssue(
                            "empty_core_source_without_download_evidence",
                            f"$.source_plan.sources[{index}].url",
                            "声称核心公开数据源完整但没有记录时，必须有成功 download 的空结果证据",
                        )
                    )
        declared_source_ids = {
            source.get("source_id")
            for source in plan.get("sources", [])
            if isinstance(source, dict)
        }
        for index, source in enumerate(plan.get("sources", [])):
            if not isinstance(source, dict):
                continue
            for related_id in source.get("related_source_ids", []):
                if related_id not in declared_source_ids:
                    issues.append(
                        SourcePlanIssue(
                            "unknown_related_source",
                            f"$.source_plan.sources[{index}].related_source_ids",
                            f"related_source_ids 引用了未登记的来源：{related_id}",
                        )
                    )

        refinement_ids: set[str] = set()
        for index, refinement in enumerate(plan.get("research_refinements", [])):
            if not isinstance(refinement, dict):
                continue
            pointer = f"$.source_plan.research_refinements[{index}]"
            refinement_id = str(refinement.get("refinement_id") or "")
            if refinement_id in refinement_ids:
                issues.append(
                    SourcePlanIssue(
                        "duplicate_research_refinement",
                        f"{pointer}.refinement_id",
                        f"深度调研发现 ID 重复：{refinement_id}",
                    )
                )
            refinement_ids.add(refinement_id)
            unknown_sources = sorted(
                {
                    str(value)
                    for value in refinement.get("evidence_source_ids", [])
                    if isinstance(value, str)
                }
                - declared_source_ids
            )
            if unknown_sources:
                issues.append(
                    SourcePlanIssue(
                        "research_refinement_unknown_source",
                        f"{pointer}.evidence_source_ids",
                        f"深度调研发现引用了未登记来源：{unknown_sources}",
                    )
                )

        expected_requirements = {
            str(item.get("need_id"))
            for item in scenario_research.get("data_needs", [])
            if isinstance(item, dict) and isinstance(item.get("need_id"), str)
        }
        coverage_items = [
            item
            for item in plan.get("data_need_coverage", [])
            if isinstance(item, dict)
        ]
        seen_requirements: set[str] = set()
        sources_by_id = {
            str(source.get("source_id")): source
            for source in plan.get("sources", [])
            if isinstance(source, dict) and source.get("source_id")
        }
        for index, item in enumerate(coverage_items):
            pointer = f"$.source_plan.data_need_coverage[{index}]"
            requirement_id = str(item.get("need_id") or "")
            if requirement_id in seen_requirements:
                issues.append(
                    SourcePlanIssue(
                        "duplicate_data_need_coverage",
                        f"{pointer}.need_id",
                        f"数据需求覆盖声明重复：{requirement_id}",
                    )
                )
            seen_requirements.add(requirement_id)
            if requirement_id not in expected_requirements:
                issues.append(
                    SourcePlanIssue(
                        "unknown_data_need_coverage",
                        f"{pointer}.need_id",
                        f"预调研中不存在数据需求：{requirement_id}",
                    )
                )
            source_ids = [
                str(value) for value in item.get("source_ids", []) if isinstance(value, str)
            ]
            unknown_sources = sorted(set(source_ids) - set(sources_by_id))
            if unknown_sources:
                issues.append(
                    SourcePlanIssue(
                        "data_need_unknown_source",
                        f"{pointer}.source_ids",
                        f"数据需求覆盖引用了未登记的来源：{unknown_sources}",
                    )
                )
            mapped_sources = [sources_by_id[value] for value in source_ids if value in sources_by_id]
            status = item.get("status")
            if status in {"supported", "partial"}:
                usable = [
                    source
                    for source in mapped_sources
                    if source.get("status") == "complete"
                    and int(source.get("record_count", 0) or 0) > 0
                    and bool(source.get("raw_files"))
                    and requirement_id in source.get("need_ids", [])
                ]
                if not usable:
                    issues.append(
                        SourcePlanIssue(
                            "data_need_without_collected_source",
                            f"{pointer}.source_ids",
                            "supported/partial 数据需求必须绑定至少一个完整、有 Raw 证据且声明支持该需求的来源",
                        )
                    )
                declared_entities = {
                    str(value)
                    for value in item.get("evidence_entity_types", [])
                    if isinstance(value, str)
                }
                source_entities = {
                    str(value)
                    for source in usable
                    for value in source.get("target_entity_types", [])
                    if isinstance(value, str)
                }
                if not declared_entities or not declared_entities.intersection(source_entities):
                    issues.append(
                        SourcePlanIssue(
                            "data_need_without_entity_evidence",
                            f"{pointer}.evidence_entity_types",
                            "supported/partial 数据需求必须声明至少一个来自绑定来源的实体类型",
                        )
                    )
                evidence_fields = {
                    str(value)
                    for value in item.get("evidence_fields", [])
                    if isinstance(value, str)
                }
                invalid_field_entities = sorted(
                    field
                    for field in evidence_fields
                    if field.split(".", 1)[0] not in declared_entities
                )
                if not evidence_fields:
                    issues.append(
                        SourcePlanIssue(
                            "data_need_without_field_evidence",
                            f"{pointer}.evidence_fields",
                            "supported/partial 数据需求必须列出实际非空的 entity.field 证据",
                        )
                    )
                elif invalid_field_entities:
                    issues.append(
                        SourcePlanIssue(
                            "data_need_field_entity_mismatch",
                            f"{pointer}.evidence_fields",
                            f"字段证据必须属于 evidence_entity_types：{invalid_field_entities}",
                        )
                    )
            elif status == "blocked":
                blocked = [
                    source
                    for source in mapped_sources
                    if source.get("status") == "blocked"
                ]
                if not blocked:
                    issues.append(
                        SourcePlanIssue(
                            "blocked_data_need_without_blocked_source",
                            f"{pointer}.source_ids",
                            "blocked 数据需求必须绑定至少一个具有真实失败证据的 blocked 来源",
                        )
                    )
            elif status == "unavailable":
                unavailable = [
                    source
                    for source in mapped_sources
                    if source.get("status") in _RESOLVED_SOURCE_STATUSES
                ]
                if not unavailable:
                    issues.append(
                        SourcePlanIssue(
                            "unavailable_data_need_without_source",
                            f"{pointer}.source_ids",
                            "unavailable 数据需求必须绑定至少一个已调查到终态的来源",
                        )
                    )

        missing_requirements = sorted(expected_requirements - seen_requirements)
        if missing_requirements:
            issues.append(
                SourcePlanIssue(
                    "missing_data_need_coverage",
                    "$.source_plan.data_need_coverage",
                    f"必须逐项评估预调研数据需求；当前缺少：{missing_requirements}",
                )
            )
        return plan, issues


def build_next_actions(
    plan: dict[str, Any],
    quality_profile: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """生成下一轮 Agent 应处理的明确来源动作，不反馈“还差几条”。"""

    actions: list[dict[str, str]] = []
    for source in plan.get("sources", []):
        if not isinstance(source, dict):
            continue
        status = source.get("status")
        if status == "planned":
            actions.append(
                {
                    "code": "collect_planned_source",
                    "source_id": str(source.get("source_id") or "unknown"),
                    "action": "采集该来源；若支持分页，持续到分页结束或完成规定的代表性抽样。",
                }
            )
        elif status == "in_progress":
            actions.append(
                {
                    "code": "continue_source_collection",
                    "source_id": str(source.get("source_id") or "unknown"),
                    "action": "继续未完成的分页，并把新增页面保存为新的 raw 文件。",
                }
            )
        elif (
            status == "blocked"
            and source.get("priority") == "core"
            and source.get("access_status") == "rate_limited"
        ):
            action = (
                "不要立即重复请求；优先改用同一官方来源的批量下载，或在下一轮只重试一次。"
            )
            actions.append(
                {
                    "code": "resolve_blocked_core_source",
                    "source_id": str(source.get("source_id") or "unknown"),
                    "action": action,
                }
            )

    for gap in (quality_profile or {}).get("quality_gaps", []):
        if not isinstance(gap, dict) or not isinstance(gap.get("action"), str):
            continue
        actions.append(
            {
                "code": str(gap.get("code") or "quality_gap"),
                "source_id": str(gap.get("source_id") or "quality_profile"),
                "action": gap["action"],
            }
        )
    # 相同动作只保留一次，避免 Prompt 被同一缺口重复占满。
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in actions:
        key = (item["source_id"], item["action"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def core_sources_resolved(plan: dict[str, Any]) -> bool:
    """核心来源都必须有明确终态；planned/in_progress 不能冒充完成。"""

    core = [
        source
        for source in plan.get("sources", [])
        if isinstance(source, dict) and source.get("priority") == "core"
    ]
    return bool(core) and all(
        source.get("status") in _RESOLVED_SOURCE_STATUSES
        for source in core
    )
