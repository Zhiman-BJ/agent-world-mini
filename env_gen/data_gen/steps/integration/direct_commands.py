"""Mechanical validation for Agent-authored environment state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.artifact_integrity import table_digest, tree_digest
from env_gen.data_gen.analysis.v2_validator import V2EnvironmentPackageValidator

from ..common.constants import (
    COLLECTION_PROFILE_PATH,
    CONTROL_INTEGRATION_ASSESSMENT,
    CONTROL_RUN_CONFIG,
    SCENARIO_RESEARCH_PATH,
)
from ..common.control_io import control_path, read_json, write_json
from ..common.download import download_receipt_issues
from ..common.workspace_files import append_only_issues, file_sha256
from ..step2_collect_data import source_research_receipt_issues


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _state_manifest(state: Path, environment: dict[str, Any]) -> dict[str, Any]:
    database = state / "records.sqlite"
    records: list[dict[str, Any]] = []
    for item in environment.get("record_sets", []):
        if not isinstance(item, dict):
            continue
        record_set_id = str(item.get("record_set_id") or "")
        count = 0
        if database.is_file() and record_set_id:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                quoted = record_set_id.replace('"', '""')
                count = int(connection.execute(
                    f'SELECT COUNT(*) FROM "{quoted}"'
                ).fetchone()[0])
            finally:
                connection.close()
        records.append({
            "record_set_id": record_set_id,
            "record_count": count,
            "digest": table_digest(database, record_set_id) if database.is_file() else None,
        })
    scopes: list[dict[str, Any]] = []
    for item in environment.get("filesystem_scopes", []):
        if not isinstance(item, dict):
            continue
        scope_id = str(item.get("scope_id") or "")
        root = state / "filesystem_scopes" / scope_id
        files = [path for path in root.rglob("*") if path.is_file()] if root.is_dir() else []
        scopes.append({
            "scope_id": scope_id,
            "file_count": len(files),
            "digest": tree_digest(root),
        })
    payload = {"record_sets": records, "filesystem_scopes": scopes}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def _validation_issues(
    root: Path,
    *,
    schema_path: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    report = V2EnvironmentPackageValidator(schema_path).validate(root)
    return [item.to_dict() for item in report.errors], report.statistics


def _coverage_issues(run_dir: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    path = run_dir / COLLECTION_PROFILE_PATH
    if not path.is_file():
        return [_issue("missing_collection_profile", COLLECTION_PROFILE_PATH, "缺少 Step 2 文件卡与覆盖结果")], {}
    profile = read_json(path, "Step 2 采集画像")
    issues: list[dict[str, str]] = []
    if config.get("allow_partial_integration") is True:
        return issues, profile.get("metrics", {})
    if profile.get("decision") != "ready":
        issues.append(_issue(
            "collection_not_ready", COLLECTION_PROFILE_PATH,
            "Step 2 尚未达到允许集成的最低业务数据覆盖线",
        ))
    policy = config.get("collection_policy", {})
    metrics = profile.get("metrics", {})
    for label, value, minimum in (
        (
            "现实工作",
            metrics.get("work", {}).get("percent", 0),
            int(policy.get("min_work_coverage_percent", 60)),
        ),
        (
            "核心实体",
            metrics.get("scenario", {}).get("entity", {}).get("percent", 0),
            int(policy.get("min_entity_coverage_percent", 60)),
        ),
    ):
        if not isinstance(value, (int, float)) or value < minimum:
            issues.append(_issue(
                "collection_coverage_below_floor", COLLECTION_PROFILE_PATH,
                f"{label} 业务数据覆盖率 {value}% 低于最低线 {minimum}%",
            ))
    supported_work = metrics.get("work", {}).get("supported", 0)
    configured_work_goals = int(policy.get("min_supported_work_goals", 2))
    total_work = metrics.get("work", {}).get("total", 0)
    min_work_goals = min(
        configured_work_goals,
        total_work if isinstance(total_work, int) else configured_work_goals,
    )
    if not isinstance(supported_work, int) or supported_work < min_work_goals:
        issues.append(_issue(
            "collection_work_below_floor",
            COLLECTION_PROFILE_PATH,
            f"完整支持的现实工作目标 {supported_work} 少于最低数量 {min_work_goals}",
        ))
    return issues, metrics


def assess_environment(run_dir: Path) -> dict[str, Any]:
    """Validate the environment declaration and final state written by the Agent."""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    issues = [
        *append_only_issues(run_dir)[1],
        *source_research_receipt_issues(run_dir),
        *download_receipt_issues(run_dir),
    ]
    coverage_issues, coverage = _coverage_issues(run_dir)
    issues.extend(coverage_issues)
    environment: dict[str, Any] = {}
    statistics: dict[str, Any] = {}
    candidate_manifest: dict[str, Any] | None = None
    environment_path = run_dir / "environment.json"
    if not environment_path.is_file():
        issues.append(_issue("missing_environment", "environment.json", "缺少最终 environment.json"))
    else:
        try:
            environment = read_json(environment_path, "环境声明")
        except RuntimeError as error:
            issues.append(_issue("invalid_environment_json", "environment.json", str(error)))
        else:
            scenario = read_json(run_dir / SCENARIO_RESEARCH_PATH, "场景研究")
            scenario_environment = scenario.get("environment", {})
            for field in ("summary", "description"):
                if environment.get(field) != scenario_environment.get(field):
                    issues.append(_issue(
                        "environment_context_changed",
                        f"environment.json.{field}",
                        f"{field} 必须沿用 Step 1 已确认的环境语义",
                    ))
            validation_issues, statistics = _validation_issues(
                run_dir, schema_path=Path(config["environment_schema_path"]),
            )
            issues.extend(validation_issues)
            if not validation_issues:
                candidate_manifest = _state_manifest(run_dir / "state", environment)
    assessment = {
        "workflow_version": str(config.get("workflow_version") or "3.0"),
        "decision": "ready" if not issues else "fix",
        "blocking_issues": issues[:32],
        "coverage": coverage,
        "statistics": statistics,
        "environment_sha256": (
            file_sha256(environment_path) if environment_path.is_file() else None
        ),
        "state_digest": candidate_manifest.get("digest") if candidate_manifest else None,
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
    return assessment


__all__ = [
    "assess_environment",
    "_state_manifest",
]
