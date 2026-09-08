"""Unified Agent-authored build and deterministic Step 3 acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.artifact_integrity import table_digest, tree_digest
from env_gen.data_gen.analysis.v2_validator import V2EnvironmentPackageValidator

from ..common.constants import (
    COLLECTION_PROFILE_PATH,
    CONTROL_INTEGRATION_ASSESSMENT,
    CONTROL_INTEGRATION_FINALIZATION,
    CONTROL_RUN_CONFIG,
    INTEGRATION_BUILD_PATH,
    SCENARIO_RESEARCH_PATH,
)
from ..common.control_io import control_path, read_json, write_json
from ..common.download import download_receipt_issues
from ..common.workspace_files import append_only_issues, file_sha256
from ..step2_collect_data import source_research_receipt_issues


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _raw_root(run_dir: Path) -> Path:
    workspace = run_dir / "workspace/raw"
    if workspace.is_dir():
        return workspace
    frozen = run_dir / "provenance/raw"
    if frozen.is_dir():
        return frozen
    raise RuntimeError("缺少 Raw 数据目录 workspace/raw 或 provenance/raw")


def run_environment_build(
    run_dir: Path,
    *,
    output_state: Path,
    timeout_seconds: int = 900,
) -> None:
    """Run the one build script with read-only inputs, no network and one writable output."""

    run_dir = run_dir.resolve()
    script = run_dir / INTEGRATION_BUILD_PATH
    if not script.is_file() or script.is_symlink():
        raise RuntimeError(f"缺少普通文件 {INTEGRATION_BUILD_PATH}")
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        raise RuntimeError("统一环境构建需要 bubblewrap（bwrap）")
    output_state = output_state.resolve()
    shutil.rmtree(output_state, ignore_errors=True)
    output_state.mkdir(parents=True)
    raw_root = _raw_root(run_dir).resolve()
    command = [
        bubblewrap,
        "--die-with-parent",
        "--unshare-net",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--bind", str(output_state), str(output_state),
        "--chdir", str(run_dir),
        "--clearenv",
        "--setenv", "PATH", os.environ.get("PATH", "/usr/bin:/bin"),
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONHASHSEED", "0",
        "--setenv", "LANG", "C.UTF-8",
        sys.executable,
        str(script),
        "--raw-dir", str(raw_root),
        "--state-dir", str(output_state),
    ]
    result = subprocess.run(
        command,
        cwd=run_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-3000:]
        raise RuntimeError(f"build.py 退出码 {result.returncode}：{detail}")
    for path in output_state.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"build.py 生成了不允许的符号链接：{path.relative_to(output_state)}")


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


def build_environment(run_dir: Path, *, timeout_seconds: int = 900) -> dict[str, Any]:
    """Materialize all Record Sets and Scopes from one script in one operation."""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    environment = read_json(run_dir / "environment.json", "环境声明")
    with tempfile.TemporaryDirectory(prefix="datagen-direct-build-") as directory:
        root = Path(directory)
        state = root / "state"
        run_environment_build(run_dir, output_state=state, timeout_seconds=timeout_seconds)
        write_json(root / "environment.json", environment)
        issues, statistics = _validation_issues(
            root, schema_path=Path(config["environment_schema_path"]),
        )
        if issues:
            messages = "; ".join(item["message"] for item in issues[:12])
            raise RuntimeError(f"统一构建结果无效：{messages}")
        target = run_dir / "state"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(state, target)
    manifest = _state_manifest(run_dir / "state", environment)
    return {"status": "built", "state": manifest, "statistics": statistics}


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
    for label, key, minimum in (
        ("Seed", "seed", int(policy.get("min_seed_coverage_percent", 90))),
        ("Step 1", "scenario", int(policy.get("min_scenario_coverage_percent", 75))),
    ):
        value = metrics.get(key, {}).get("overall", {}).get("percent", 0)
        if not isinstance(value, (int, float)) or value < minimum:
            issues.append(_issue(
                "collection_coverage_below_floor", COLLECTION_PROFILE_PATH,
                f"{label} 业务数据覆盖率 {value}% 低于最低线 {minimum}%",
            ))
    return issues, metrics


def assess_environment(
    run_dir: Path,
    *,
    replay: bool = True,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Validate the Agent's final declaration and state, optionally replaying build.py."""

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
    build_path = run_dir / INTEGRATION_BUILD_PATH
    if not build_path.is_file() or build_path.is_symlink():
        issues.append(_issue(
            "missing_build_script", INTEGRATION_BUILD_PATH,
            "缺少统一、可重放的 provenance/build.py",
        ))
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
    replay_manifest: dict[str, Any] | None = None
    if replay and not issues and candidate_manifest is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="datagen-direct-replay-") as directory:
                root = Path(directory)
                run_environment_build(
                    run_dir, output_state=root / "state", timeout_seconds=timeout_seconds,
                )
                write_json(root / "environment.json", environment)
                replay_issues, _ = _validation_issues(
                    root, schema_path=Path(config["environment_schema_path"]),
                )
                issues.extend(replay_issues)
                if not replay_issues:
                    replay_manifest = _state_manifest(root / "state", environment)
        except Exception as error:
            issues.append(_issue("build_replay_failed", INTEGRATION_BUILD_PATH, str(error)))
        if replay_manifest is not None and replay_manifest["digest"] != candidate_manifest["digest"]:
            issues.append(_issue(
                "build_state_mismatch", "state",
                "当前 state 与 provenance/build.py 独立重放结果不同，请重新执行统一构建",
            ))
    assessment = {
        "workflow_version": str(config.get("workflow_version") or "3.0"),
        "decision": "ready" if not issues else "fix",
        "blocking_issues": issues[:32],
        "coverage": coverage,
        "statistics": statistics,
        "environment_sha256": (
            file_sha256(environment_path) if environment_path.is_file() else None
        ),
        "build_sha256": file_sha256(build_path) if build_path.is_file() else None,
        "state_digest": candidate_manifest.get("digest") if candidate_manifest else None,
        "replay_state_digest": replay_manifest.get("digest") if replay_manifest else None,
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
    return assessment


def finalize_environment(run_dir: Path, *, timeout_seconds: int = 900) -> dict[str, Any]:
    """Record Step 3 completion only after deterministic Python acceptance."""

    run_dir = run_dir.resolve()
    assessment_path = control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT)
    assessment = (
        read_json(assessment_path, "集成验收")
        if assessment_path.is_file() else {}
    )
    try:
        current = (
            assessment.get("decision") == "ready"
            and assessment.get("environment_sha256") == file_sha256(run_dir / "environment.json")
            and assessment.get("build_sha256") == file_sha256(run_dir / INTEGRATION_BUILD_PATH)
            and assessment.get("state_digest")
            == _state_manifest(
                run_dir / "state", read_json(run_dir / "environment.json", "环境声明")
            )["digest"]
            and assessment.get("replay_state_digest") == assessment.get("state_digest")
        )
    except Exception:
        current = False
    if not current:
        assessment = assess_environment(
            run_dir, replay=True, timeout_seconds=timeout_seconds,
        )
    if assessment["decision"] != "ready":
        detail = "; ".join(
            str(item.get("message")) for item in assessment["blocking_issues"][:12]
        )
        raise RuntimeError(f"环境集成尚未通过：{detail}")
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    collection = read_json(run_dir / COLLECTION_PROFILE_PATH, "Step 2 采集画像")
    partial = (
        config.get("allow_partial_integration") is True
        and collection.get("decision") == "partial"
    )
    payload = {
        "workflow_version": assessment["workflow_version"],
        "decision": "finalized",
        "result": "partial" if partial else "ready",
        "environment_sha256": file_sha256(run_dir / "environment.json"),
        "build_sha256": file_sha256(run_dir / INTEGRATION_BUILD_PATH),
        "state_digest": assessment["state_digest"],
        "finalized_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_FINALIZATION), payload)
    return payload


def finalization_issues(run_dir: Path) -> list[dict[str, str]]:
    """Return precise errors when the Agent's finalization receipt is absent or stale."""

    run_dir = run_dir.resolve()
    path = control_path(run_dir, CONTROL_INTEGRATION_FINALIZATION)
    if not path.is_file():
        return [_issue(
            "integration_not_finalized", str(path.relative_to(run_dir)),
            "尚未执行 integratectl finalize",
        )]
    try:
        payload = read_json(path, "集成收口")
        environment = read_json(run_dir / "environment.json", "环境声明")
        state_digest = _state_manifest(run_dir / "state", environment)["digest"]
    except Exception as error:
        return [_issue("invalid_integration_finalization", str(path), str(error))]
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    collection = read_json(run_dir / COLLECTION_PROFILE_PATH, "Step 2 采集画像")
    partial = (
        config.get("allow_partial_integration") is True
        and collection.get("decision") == "partial"
    )
    expected = {
        "decision": "finalized",
        "result": "partial" if partial else "ready",
        "environment_sha256": file_sha256(run_dir / "environment.json"),
        "build_sha256": file_sha256(run_dir / INTEGRATION_BUILD_PATH),
        "state_digest": state_digest,
    }
    mismatches = [name for name, value in expected.items() if payload.get(name) != value]
    if mismatches:
        return [_issue(
            "stale_integration_finalization", str(path.relative_to(run_dir)),
            "集成收口与当前文件不一致：" + ", ".join(mismatches),
        )]
    return []


__all__ = [
    "assess_environment",
    "build_environment",
    "finalize_environment",
    "finalization_issues",
    "run_environment_build",
    "_state_manifest",
]
