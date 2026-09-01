"""Bind semantic field-review decisions to deterministic profile facts and Raw evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


FIELD_REVIEW_SCHEMA_VERSION = "1.0"
FIELD_REVIEW_DECISION = "verified_against_raw"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_review_findings(profile: dict[str, Any]) -> list[dict[str, Any]]:
    review = profile.get("asset_profile", {}).get("field_review", {})
    return [item for item in review.get("findings", []) if isinstance(item, dict)]


def _issue(
    code: str,
    message: str,
    action: str,
    asset_ids: list[str],
) -> dict[str, Any]:
    return {
        "code": code,
        "path": "provenance/field_review.json",
        "message": message,
        "action": action,
        "asset_ids": sorted(set(asset_ids)),
    }


def _source_exists(run_dir: Path, relative: str) -> bool:
    return (
        (run_dir / "workspace" / relative).is_file()
        or (run_dir / "provenance" / relative).is_file()
    )


def _content_issues(
    run_dir: Path,
    payload: dict[str, Any],
    *,
    profile: dict[str, Any],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    findings = profile_review_findings(profile)
    finding_map = {str(item.get("finding_id")): item for item in findings}
    affected = sorted({str(item.get("record_set_id")) for item in findings})
    if payload.get("schema_version") != FIELD_REVIEW_SCHEMA_VERSION:
        return [_issue(
            "invalid_field_review",
            f"field_review.schema_version 必须为 {FIELD_REVIEW_SCHEMA_VERSION}。",
            "重新生成字段复核草稿并通过 integratectl save-field-review 保存。",
            affected,
        )]
    entries = payload.get("findings")
    if not isinstance(entries, list):
        return [_issue(
            "invalid_field_review", "field_review.findings 必须是数组。",
            "逐项核对 integration_profile.asset_profile.field_review.findings。",
            affected,
        )]
    entry_map: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    record_sources = {
        str(item.get("record_set_id")): {
            str(value) for value in item.get("source_paths", [])
        }
        for item in plan.get("record_sets", []) if isinstance(item, dict)
    }
    expected_keys = {"finding_id", "decision", "reason", "evidence_paths"}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            issues.append(_issue(
                "invalid_field_review_entry",
                f"field_review.findings[{index}] 必须且只能包含 {sorted(expected_keys)}。",
                "按字段复核格式重写该条目。", affected,
            ))
            continue
        finding_id = str(entry.get("finding_id") or "")
        finding = finding_map.get(finding_id)
        if not finding or finding_id in entry_map:
            issues.append(_issue(
                "unknown_or_duplicate_field_review",
                f"字段复核引用了未知或重复提示：{finding_id!r}。",
                "只复核当前 integration_profile 中且未重复的 finding_id。",
                affected,
            ))
            continue
        entry_map[finding_id] = entry
        record_set_id = str(finding.get("record_set_id") or "")
        if entry.get("decision") != FIELD_REVIEW_DECISION:
            issues.append(_issue(
                "invalid_field_review_decision",
                f"{finding_id} 尚未声明 verified_against_raw。",
                "回到 Raw 核对；若发现错误先修转换，不要接受错误数据。",
                [record_set_id],
            ))
        reason = entry.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            issues.append(_issue(
                "insufficient_field_review_reason",
                f"{finding_id} 的核实理由过短，无法说明为何该分布合理。",
                "说明核对了什么 Raw 结构，以及为何保留当前结果。",
                [record_set_id],
            ))
        evidence_paths = entry.get("evidence_paths")
        if (
            not isinstance(evidence_paths, list)
            or not evidence_paths
            or any(not isinstance(value, str) or not value for value in evidence_paths)
            or len(evidence_paths) != len(set(evidence_paths))
        ):
            issues.append(_issue(
                "invalid_field_review_evidence",
                f"{finding_id} 必须列出至少一个且不重复的 Raw evidence_path。",
                "使用该 Record Set 的 source_paths 中实际核对过的路径。",
                [record_set_id],
            ))
            continue
        unknown_paths = sorted(set(evidence_paths) - record_sources.get(record_set_id, set()))
        missing_paths = sorted(
            value for value in evidence_paths if not _source_exists(run_dir, value)
        )
        if unknown_paths or missing_paths:
            issues.append(_issue(
                "invalid_field_review_evidence",
                f"{finding_id} 的证据不属于该 Record Set 或文件不存在："
                f"unknown={unknown_paths}, missing={missing_paths}。",
                "改用计划中已登记且实际存在的 source_paths。",
                [record_set_id],
            ))
    missing = sorted(set(finding_map) - set(entry_map))
    if missing:
        issues.append(_issue(
            "incomplete_field_review",
            "以下字段画像提示尚未复核：" + ", ".join(missing[:8]),
            "抽查完整记录和对应 Raw；错误则修转换，合理则保存带证据的复核决定。",
            affected,
        ))
    return issues


def build_field_review_payload(
    run_dir: Path,
    *,
    draft: dict[str, Any],
    profile: dict[str, Any],
    plan: dict[str, Any],
    integration_plan_path: Path,
    integration_profile_path: Path,
) -> dict[str, Any]:
    """Validate an Agent draft and bind it to the exact plan/profile snapshots."""

    if set(draft) != {"schema_version", "findings"}:
        raise ValueError("字段复核草稿必须且只能包含 schema_version 和 findings")
    issues = _content_issues(run_dir, draft, profile=profile, plan=plan)
    if issues:
        raise ValueError("；".join(str(item["message"]) for item in issues[:12]))
    return {
        "schema_version": FIELD_REVIEW_SCHEMA_VERSION,
        "integration_plan_sha256": _file_sha256(integration_plan_path),
        "integration_profile_sha256": _file_sha256(integration_profile_path),
        "findings": draft["findings"],
    }


def field_review_issues(
    run_dir: Path,
    *,
    profile: dict[str, Any],
    plan: dict[str, Any],
    review_path: Path,
    integration_plan_path: Path,
    integration_profile_path: Path,
) -> list[dict[str, Any]]:
    """Verify that every current review signal was checked against relevant Raw."""

    findings = profile_review_findings(profile)
    affected = sorted({str(item.get("record_set_id")) for item in findings})
    if not review_path.is_file():
        if not findings:
            return []
        return [_issue(
            "missing_field_review",
            f"当前字段画像有 {len(findings)} 条待复核提示，但缺少 field_review.json。",
            "读取字段分布并对照 Raw；修复错误，或通过 save-field-review 保存核实证据。",
            affected,
        )]
    try:
        payload = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [_issue(
            "invalid_field_review", f"field_review.json 无法读取：{error}",
            "重新通过 integratectl save-field-review 保存。", affected,
        )]
    if not isinstance(payload, dict):
        return [_issue(
            "invalid_field_review", "field_review.json 根节点必须是对象。",
            "重新通过 integratectl save-field-review 保存。", affected,
        )]
    expected_keys = {
        "schema_version", "integration_plan_sha256", "integration_profile_sha256", "findings",
    }
    if set(payload) != expected_keys:
        return [_issue(
            "invalid_field_review",
            f"field_review.json 必须且只能包含 {sorted(expected_keys)}。",
            "不要手工编辑正式复核文件；重新运行 save-field-review。", affected,
        )]
    hash_issues: list[dict[str, Any]] = []
    if (
        not integration_plan_path.is_file()
        or payload.get("integration_plan_sha256") != _file_sha256(integration_plan_path)
    ):
        hash_issues.append(_issue(
            "stale_field_review", "字段复核绑定的 integration_plan 已变化。",
            "按当前计划和画像重新核对字段。", affected,
        ))
    if (
        not integration_profile_path.is_file()
        or payload.get("integration_profile_sha256") != _file_sha256(integration_profile_path)
    ):
        hash_issues.append(_issue(
            "stale_field_review", "字段复核绑定的 integration_profile 已变化。",
            "按当前字段分布重新核对 Raw。", affected,
        ))
    return hash_issues + _content_issues(run_dir, payload, profile=profile, plan=plan)


__all__ = [
    "FIELD_REVIEW_DECISION",
    "FIELD_REVIEW_SCHEMA_VERSION",
    "build_field_review_payload",
    "field_review_issues",
    "profile_review_findings",
]
