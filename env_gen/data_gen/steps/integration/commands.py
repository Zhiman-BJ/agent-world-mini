"""Step 3 集成计划、确定性物化、画像和收口命令。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.integration_materialization import (
    environment_from_plan,
    materialize_record_set,
    materialize_scope,
)
from env_gen.data_gen.analysis.artifact_integrity import table_digest, tree_digest
from env_gen.data_gen.analysis.integration_plan import load_and_validate_integration_plan
from env_gen.data_gen.analysis.integration_profiling import build_integration_profile
from env_gen.data_gen.analysis.field_review import (
    build_field_review_payload,
    field_review_issues,
    profile_review_findings,
)
from env_gen.data_gen.analysis.environment_quality import (
    EnvironmentQualityPolicy,
    build_environment_quality_profile,
)
from env_gen.data_gen.analysis.source_inventory import (
    build_source_inventory,
    validate_source_inventory,
)

from ..collection.commands.download_raw import download_receipt_issues
from ..collection.commands.save_source_plan import (
    read_saved_source_plan,
    source_plan_receipt_issues,
    source_plan_state_issues,
)
from ..common.constants import (
    CONTROL_INTEGRATION_ASSESSMENT,
    CONTROL_INTEGRATION_FINALIZATION,
    CONTROL_INTEGRATION_MATERIALIZATION_RECEIPTS,
    CONTROL_INTEGRATION_PLAN_RECEIPT,
    CONTROL_RAW_INTEGRITY_SNAPSHOT,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    FIELD_REVIEW_PATH,
    INTEGRATION_PLAN_PATH,
    INTEGRATION_PROFILE_PATH,
    SOURCE_INVENTORY_PATH,
)
from ..common.control_io import control_path, read_json, write_json
from ..common.workspace_files import append_only_issues, file_sha256
from ..step1_research_scenario import read_saved_scenario_research
from .transformation_runner import run_record_transformation


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_draft(run_dir: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label}必须位于当前运行目录内") from error
    if not resolved.is_file():
        raise RuntimeError(f"{label}不存在：{resolved}")
    return resolved


def _current_inventory(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    source_plan = read_saved_source_plan(run_dir)
    inventory = build_source_inventory(
        run_dir,
        seed_global_id=str(seed.get("global_id") or ""),
        seed_sha256=str(config["seed_sha256"]),
        source_plan=source_plan,
    )
    errors = validate_source_inventory(
        inventory, Path(config["source_inventory_schema_path"])
    )
    if errors:
        raise RuntimeError("source_inventory 不符合 Schema：" + "; ".join(errors[:12]))
    write_json(run_dir / SOURCE_INVENTORY_PATH, inventory)
    return source_plan, inventory


def _validated_plan(run_dir: Path) -> dict[str, Any]:
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    scenario = read_saved_scenario_research(run_dir)
    source_plan, inventory = _current_inventory(run_dir)
    plan, issues = load_and_validate_integration_plan(
        run_dir / INTEGRATION_PLAN_PATH,
        schema_path=Path(config["integration_plan_schema_path"]),
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        scenario_research=scenario,
        source_plan=source_plan,
        source_inventory=inventory,
    )
    if issues:
        raise RuntimeError(
            "integration_plan 不符合要求："
            + "; ".join(f"{item.path}: {item.message}" for item in issues[:16])
        )
    return plan


def save_integration_plan(run_dir: Path, *, input_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    draft = _safe_draft(run_dir, input_path, "integration plan 草稿")
    payload = json.loads(draft.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("integration plan 草稿根节点必须是对象")
    target = run_dir / INTEGRATION_PLAN_PATH
    write_json(target, payload)
    try:
        plan = _validated_plan(run_dir)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    transformation_ids = [
        str(item.get("transformation_id"))
        for item in [*plan.get("record_sets", []), *plan.get("filesystem_scopes", [])]
    ]
    if len(transformation_ids) != len(set(transformation_ids)):
        target.unlink(missing_ok=True)
        raise RuntimeError("每个最终资产必须使用唯一 transformation_id")
    receipt = {
        "schema_version": "1.0",
        "path": INTEGRATION_PLAN_PATH,
        "sha256": file_sha256(target),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "record_set_count": len(plan.get("record_sets", [])),
        "filesystem_scope_count": len(plan.get("filesystem_scopes", [])),
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_PLAN_RECEIPT), receipt)
    return {"status": "saved", **receipt}


def integration_plan_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    plan_path = run_dir / INTEGRATION_PLAN_PATH
    receipt_path = control_path(run_dir, CONTROL_INTEGRATION_PLAN_RECEIPT)
    if not plan_path.is_file() or not receipt_path.is_file():
        return [{
            "code": "integration_plan_not_saved", "path": INTEGRATION_PLAN_PATH,
            "message": "集成计划必须通过 integratectl save-plan 保存。",
        }]
    receipt = read_json(receipt_path, "集成计划收据")
    if receipt.get("sha256") != file_sha256(plan_path):
        return [{
            "code": "integration_plan_modified_after_save", "path": INTEGRATION_PLAN_PATH,
            "message": "集成计划保存后被直接修改。",
        }]
    return []


def _asset(plan: dict[str, Any], kind: str, asset_id: str) -> dict[str, Any]:
    collection = plan["record_sets"] if kind == "record_set" else plan["filesystem_scopes"]
    key = "record_set_id" if kind == "record_set" else "scope_id"
    item = next((value for value in collection if value.get(key) == asset_id), None)
    if item is None:
        raise RuntimeError(f"集成计划没有声明 {kind} {asset_id}")
    return item


def _receipts(run_dir: Path) -> dict[str, Any]:
    path = control_path(run_dir, CONTROL_INTEGRATION_MATERIALIZATION_RECEIPTS)
    return read_json(path, "集成物化收据") if path.is_file() else {
        "schema_version": "1.0", "assets": []
    }


def _save_asset_receipt(run_dir: Path, receipt: dict[str, Any]) -> None:
    payload = _receipts(run_dir)
    assets = [
        item for item in payload.get("assets", [])
        if isinstance(item, dict) and item.get("asset_id") != receipt["asset_id"]
    ]
    assets.append(receipt)
    payload["assets"] = sorted(assets, key=lambda item: str(item.get("asset_id")))
    write_json(control_path(run_dir, CONTROL_INTEGRATION_MATERIALIZATION_RECEIPTS), payload)


def _source_records(run_dir: Path, source_paths: list[str]) -> list[dict[str, str]]:
    workspace = run_dir / "workspace"
    result: list[dict[str, str]] = []
    for relative in source_paths:
        path = workspace / relative
        if not path.is_file():
            raise RuntimeError(f"集成来源不存在：{relative}")
        result.append({"path": relative, "sha256": file_sha256(path)})
    return result


def _install_transformation_package(
    run_dir: Path,
    *,
    script: Path,
    package_directory: Path,
    transformation_id: str,
) -> tuple[Path, Path]:
    """安装可独立重放的 Python 转换包，并返回包目录与入口脚本。"""

    package_directory = package_directory.resolve()
    try:
        package_directory.relative_to(run_dir.resolve())
        entry_relative = script.resolve().relative_to(package_directory)
    except ValueError as error:
        raise RuntimeError("转换包和入口脚本必须位于当前运行目录内") from error
    if not package_directory.is_dir():
        raise RuntimeError(f"转换包目录不存在：{package_directory}")
    if script.suffix.lower() != ".py":
        raise RuntimeError("转换入口脚本必须是 Python 文件")
    package_files = sorted(
        path for path in package_directory.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )
    if script.resolve() not in {path.resolve() for path in package_files}:
        raise RuntimeError("转换入口脚本不属于转换包")
    for path in package_directory.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"转换包不允许符号链接：{path}")
    target = run_dir / "provenance/transformations" / transformation_id
    shutil.rmtree(target, ignore_errors=True)
    for source in package_files:
        relative = source.relative_to(package_directory)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    entry = target / entry_relative
    if not entry.is_file():
        raise RuntimeError("转换包安装后缺少入口脚本")
    return target, entry


def build_record_set(
    run_dir: Path,
    *,
    record_set_id: str,
    script_path: Path,
    package_directory: Path | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    plan = _validated_plan(run_dir)
    record_set = _asset(plan, "record_set", record_set_id)
    draft_script = _safe_draft(run_dir, script_path, "转换脚本")
    transformation_id = str(record_set["transformation_id"])
    package, script = _install_transformation_package(
        run_dir,
        script=draft_script,
        package_directory=package_directory or draft_script.parent,
        transformation_id=transformation_id,
    )
    # 两次执行使用互不重叠的临时目录，第二次不能观察第一次的输出。
    with tempfile.TemporaryDirectory(prefix=f"datagen-{record_set_id}-first-") as directory:
        first = Path(directory) / "records.json"
        run_record_transformation(
            run_dir, script=script, output=first, asset_id=record_set_id,
            timeout_seconds=timeout_seconds,
        )
        first_sha256 = file_sha256(first)
    with tempfile.TemporaryDirectory(prefix=f"datagen-{record_set_id}-second-") as directory:
        second = Path(directory) / "records.json"
        run_record_transformation(
            run_dir, script=script, output=second, asset_id=record_set_id,
            timeout_seconds=timeout_seconds,
        )
        output_sha256 = file_sha256(second)
        if first_sha256 != output_sha256:
            raise RuntimeError("转换脚本两次运行输出不同，不能登记为确定性转换")
        count = materialize_record_set(
            run_dir / "state/records.sqlite", record_set=record_set, input_path=second,
        )
    receipt = {
        "asset_kind": "record_set",
        "asset_id": record_set_id,
        "transformation_id": transformation_id,
        "package_path": package.relative_to(run_dir).as_posix(),
        "package_sha256": tree_digest(package),
        "script_path": script.relative_to(run_dir).as_posix(),
        "script_sha256": file_sha256(script),
        "sandbox": "bubblewrap_read_only_no_network",
        "source_files": _source_records(run_dir, list(record_set["source_paths"])),
        "output_sha256": output_sha256,
        "state_digest": table_digest(run_dir / "state/records.sqlite", record_set_id),
        "item_count": count,
        "materialized_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_asset_receipt(run_dir, receipt)
    return {"status": "materialized", **receipt}


def build_filesystem_scope(run_dir: Path, *, scope_id: str) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    plan = _validated_plan(run_dir)
    scope = _asset(plan, "filesystem_scope", scope_id)
    mode = str(scope["materialization"])
    if mode == "convert":
        raise RuntimeError("convert Scope 必须先转换成真实文件再使用 copy；当前不接受不可复现的隐式转换")
    source_records = _source_records(run_dir, list(scope["source_paths"]))
    sources = [run_dir / "workspace" / item["path"] for item in source_records]
    count = materialize_scope(
        run_dir / "state/filesystem_scopes", scope_id=scope_id,
        sources=sources, mode=mode,
    )
    root = run_dir / "state/filesystem_scopes" / scope_id
    receipt = {
        "asset_kind": "filesystem_scope",
        "asset_id": scope_id,
        "transformation_id": str(scope["transformation_id"]),
        "script_path": None,
        "script_sha256": None,
        "sandbox": "builtin_copy_or_safe_extract",
        "source_files": source_records,
        "output_sha256": tree_digest(root),
        "state_digest": tree_digest(root),
        "item_count": count,
        "materialized_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_asset_receipt(run_dir, receipt)
    return {"status": "materialized", **receipt}


def materialization_receipt_issues(run_dir: Path, plan: dict[str, Any]) -> list[dict[str, str]]:
    payload = _receipts(run_dir)
    receipts = {
        str(item.get("asset_id")): item
        for item in payload.get("assets", []) if isinstance(item, dict)
    }
    issues: list[dict[str, str]] = []
    for asset in [*plan.get("record_sets", []), *plan.get("filesystem_scopes", [])]:
        asset_id = str(asset.get("record_set_id") or asset.get("scope_id"))
        expected_kind = "record_set" if "record_set_id" in asset else "filesystem_scope"
        receipt = receipts.get(asset_id)
        if receipt is None:
            issues.append({
                "code": "asset_not_materialized", "path": asset_id,
                "message": f"资产尚未通过 integratectl 物化：{asset_id}",
            })
            continue
        expected_paths = sorted(str(value) for value in asset.get("source_paths", []))
        receipt_sources = [
            item for item in receipt.get("source_files", []) if isinstance(item, dict)
        ]
        receipt_paths = sorted(str(item.get("path") or "") for item in receipt_sources)
        if receipt.get("asset_kind") != expected_kind:
            issues.append({
                "code": "materialization_receipt_kind_mismatch", "path": asset_id,
                "message": f"{asset_id} 的物化收据资产类型与计划不一致。",
            })
        if receipt.get("transformation_id") != asset.get("transformation_id"):
            issues.append({
                "code": "materialization_transformation_mismatch", "path": asset_id,
                "message": f"{asset_id} 的物化收据 transformation_id 与计划不一致。",
            })
        if receipt_paths != expected_paths or len(receipt_sources) != len(expected_paths):
            issues.append({
                "code": "materialization_sources_mismatch", "path": asset_id,
                "message": f"{asset_id} 的物化来源没有恰好覆盖计划 source_paths。",
            })
        for source in receipt.get("source_files", []):
            if not isinstance(source, dict):
                continue
            path = run_dir / "workspace" / str(source.get("path"))
            if not path.is_file() or source.get("sha256") != file_sha256(path):
                issues.append({
                    "code": "integration_source_changed", "path": asset_id,
                    "message": f"{asset_id} 的来源文件在物化后发生变化，请重建该资产。",
                })
                break
        if receipt.get("asset_kind") == "record_set":
            package = run_dir / str(receipt.get("package_path"))
            script = run_dir / str(receipt.get("script_path"))
            if (
                not package.is_dir()
                or receipt.get("package_sha256") != tree_digest(package)
                or not script.is_file()
                or receipt.get("script_sha256") != file_sha256(script)
            ):
                issues.append({
                    "code": "transformation_changed", "path": asset_id,
                    "message": f"{asset_id} 的转换包发生变化，请重新物化。",
                })
            database = run_dir / "state/records.sqlite"
            try:
                digest = table_digest(database, asset_id)
            except sqlite3.Error:
                digest = ""
            if not isinstance(receipt.get("output_sha256"), str):
                issues.append({
                    "code": "missing_transformation_output_digest", "path": asset_id,
                    "message": f"{asset_id} 的转换收据缺少输出摘要。",
                })
        else:
            root = run_dir / "state/filesystem_scopes" / asset_id
            digest = tree_digest(root) if root.is_dir() else ""
            if receipt.get("output_sha256") != digest:
                issues.append({
                    "code": "scope_output_digest_mismatch", "path": asset_id,
                    "message": f"{asset_id} 的 Scope 输出摘要与实际文件树不同。",
                })
        if digest != receipt.get("state_digest"):
            issues.append({
                "code": "materialized_state_changed", "path": asset_id,
                "message": f"{asset_id} 的物化状态被直接修改，请重新执行受控构建。",
            })
    return issues


_TERMINAL_SOURCE_STATUSES = {"complete", "blocked", "unavailable"}
_TERMINAL_NEED_STATUSES = {
    "supported", "partial", "blocked", "unavailable", "not_applicable",
}


def _terminal_coverage(run_dir: Path) -> tuple[bool, bool]:
    """判断来源和需求是否已收口，允许合法的 not_rich exhausted 包。"""

    source_plan = read_saved_source_plan(run_dir)
    sources = [item for item in source_plan.get("sources", []) if isinstance(item, dict)]
    needs = [
        item for item in source_plan.get("data_need_coverage", [])
        if isinstance(item, dict)
    ]
    sources_done = bool(sources) and all(
        item.get("status") in _TERMINAL_SOURCE_STATUSES for item in sources
    )
    needs_done = bool(needs) and all(
        item.get("status") in _TERMINAL_NEED_STATUSES for item in needs
    )
    return sources_done, needs_done


def save_field_review(run_dir: Path, *, input_path: Path) -> dict[str, Any]:
    """Save semantic review evidence bound to the current deterministic profile."""

    run_dir = run_dir.resolve()
    preparation = (
        append_only_issues(run_dir)[1]
        + source_plan_receipt_issues(run_dir)
        + download_receipt_issues(run_dir)
        + source_plan_state_issues(run_dir)
        + integration_plan_receipt_issues(run_dir)
    )
    if preparation:
        raise RuntimeError(
            "字段复核前集成现场无效："
            + "; ".join(str(item.get("message")) for item in preparation[:12])
        )
    plan = _validated_plan(run_dir)
    receipt_issues = materialization_receipt_issues(run_dir, plan)
    if receipt_issues:
        raise RuntimeError(
            "字段复核前必须完成当前资产物化："
            + "; ".join(str(item.get("message")) for item in receipt_issues[:12])
        )
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    profile = build_integration_profile(
        run_dir,
        plan=plan,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
    )
    profile_path = run_dir / INTEGRATION_PROFILE_PATH
    write_json(profile_path, profile)
    draft_path = _safe_draft(run_dir, input_path, "字段复核草稿")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    if not isinstance(draft, dict):
        raise RuntimeError("字段复核草稿根节点必须是对象")
    try:
        payload = build_field_review_payload(
            run_dir,
            draft=draft,
            profile=profile,
            plan=plan,
            integration_plan_path=run_dir / INTEGRATION_PLAN_PATH,
            integration_profile_path=profile_path,
        )
    except ValueError as error:
        raise RuntimeError(f"字段复核草稿无效：{error}") from error
    target = run_dir / FIELD_REVIEW_PATH
    write_json(target, payload)
    return {
        "status": "saved",
        "path": FIELD_REVIEW_PATH,
        "reviewed_finding_count": len(payload["findings"]),
        "integration_profile_sha256": payload["integration_profile_sha256"],
    }


def assess_integration(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    current_raw_snapshot, raw_issues = append_only_issues(run_dir)
    preparation = (
        raw_issues
        + source_plan_receipt_issues(run_dir)
        + download_receipt_issues(run_dir)
        + integration_plan_receipt_issues(run_dir)
    )
    if preparation:
        assessment = {
            "workflow_version": "3.0", "decision": "fix",
            "quality_tier": None, "blocking_issues": preparation, "next_actions": [],
        }
        write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
        return assessment
    try:
        plan = _validated_plan(run_dir)
    except RuntimeError as error:
        assessment = {
            "workflow_version": "3.0", "decision": "fix", "quality_tier": None,
            "blocking_issues": [{"code": "invalid_integration_plan", "path": INTEGRATION_PLAN_PATH, "message": str(error)}],
            "next_actions": [],
        }
        write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
        return assessment
    receipt_issues = materialization_receipt_issues(run_dir, plan)
    if receipt_issues:
        assessment = {
            "workflow_version": "3.0", "decision": "fix", "quality_tier": None,
            "blocking_issues": receipt_issues,
            "next_actions": [
                {"code": item["code"], "asset_id": item["path"], "action": item["message"]}
                for item in receipt_issues
            ],
        }
        write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
        return assessment
    profile = build_integration_profile(
        run_dir,
        plan=plan,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
    )
    schema = json.loads(Path(config["integration_profile_schema_path"]).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(profile))
    if errors:
        raise RuntimeError("integration_profile 不符合 Schema：" + "; ".join(error.message for error in errors[:12]))
    write_json(run_dir / INTEGRATION_PROFILE_PATH, profile)
    write_json(run_dir / "environment.json", environment_from_plan(plan))
    quality = build_environment_quality_profile(
        run_dir,
        plan=plan,
        scenario_research=read_saved_scenario_research(run_dir),
        source_plan=read_saved_source_plan(run_dir),
        source_inventory=read_json(run_dir / SOURCE_INVENTORY_PATH, "来源画像"),
        integration_profile=profile,
        policy=EnvironmentQualityPolicy(**config.get("environment_quality_policy", {})),
    )
    quality_schema = json.loads(
        Path(config["environment_quality_profile_schema_path"]).read_text(encoding="utf-8")
    )
    quality_errors = list(Draft202012Validator(quality_schema).iter_errors(quality))
    if quality_errors:
        raise RuntimeError(
            "quality_profile 不符合 Schema："
            + "; ".join(error.message for error in quality_errors[:12])
        )
    write_json(run_dir / "provenance/quality_profile.json", quality)
    write_json(
        control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT),
        current_raw_snapshot,
    )
    all_sources_resolved, all_data_needs_assessed = _terminal_coverage(run_dir)
    review_issues = field_review_issues(
        run_dir,
        profile=profile,
        plan=plan,
        review_path=run_dir / FIELD_REVIEW_PATH,
        integration_plan_path=run_dir / INTEGRATION_PLAN_PATH,
        integration_profile_path=run_dir / INTEGRATION_PROFILE_PATH,
    )
    if profile["integration_tier"] != "integrated":
        decision = "continue"
    elif review_issues:
        decision = "continue"
    elif quality["quality_tier"] == "rich":
        decision = "ready"
    elif all_sources_resolved and all_data_needs_assessed:
        # The facts are valid and exhaustive, but the public data cannot meet
        # the richness policy. Preserve it as a truthful not_rich package.
        decision = "exhausted"
    else:
        decision = "continue"
    assessment = {
        "workflow_version": "3.0",
        "decision": decision,
        "quality_tier": quality["quality_tier"],
        "all_sources_resolved": all_sources_resolved,
        "all_data_needs_assessed": all_data_needs_assessed,
        "field_review_required": bool(profile_review_findings(profile)),
        "field_review_complete": not review_issues,
        "blocking_issues": [],
        "next_actions": [
            {"code": item["code"], "asset_ids": item["asset_ids"], "action": item["action"]}
            for item in profile["integration_gaps"]
        ] + [
            {"code": item["code"], "asset_ids": [], "action": item["action"]}
            for item in quality["quality_gaps"]
        ] + [
            {"code": item["code"], "asset_ids": item["asset_ids"], "action": item["action"]}
            for item in review_issues
        ],
        "integration_summary": profile["summary"],
        "quality_summary": quality["summary"],
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_ASSESSMENT), assessment)
    return assessment


def finalize_integration(run_dir: Path, *, result: str | None = None) -> dict[str, Any]:
    assessment = assess_integration(run_dir)
    requested = result or str(assessment.get("decision") or "")
    if requested not in {"ready", "exhausted"}:
        raise RuntimeError("集成画像尚未通过，不能收口")
    if requested == "ready" and assessment.get("decision") != "ready":
        raise RuntimeError("集成画像尚未达到 rich，不能以 ready 收口")
    if requested == "exhausted":
        if assessment.get("decision") not in {"exhausted", "ready"}:
            raise RuntimeError("来源或数据需求尚未收口，不能以 exhausted 收口")
    plan = _validated_plan(run_dir)
    payload = {
        "workflow_version": "3.0", "decision": "finalized", "result": requested,
        "integration_plan_sha256": file_sha256(run_dir / INTEGRATION_PLAN_PATH),
        "field_review_sha256": (
            file_sha256(run_dir / FIELD_REVIEW_PATH)
            if (run_dir / FIELD_REVIEW_PATH).is_file() else None
        ),
        "environment_sha256": file_sha256(run_dir / "environment.json"),
        "asset_count": len(plan.get("record_sets", [])) + len(plan.get("filesystem_scopes", [])),
    }
    write_json(control_path(run_dir, CONTROL_INTEGRATION_FINALIZATION), payload)
    return payload


__all__ = [
    "assess_integration",
    "build_filesystem_scope",
    "build_record_set",
    "finalize_integration",
    "integration_plan_receipt_issues",
    "materialization_receipt_issues",
    "save_field_review",
    "save_integration_plan",
]
