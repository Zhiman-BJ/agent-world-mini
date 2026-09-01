"""Step 5：从最终包视角独立复验并原子发布。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.artifact_integrity import table_digest, tree_digest
from env_gen.data_gen.analysis.environment_quality import (
    EnvironmentQualityPolicy,
    build_environment_quality_profile,
)
from env_gen.data_gen.analysis.field_review import field_review_issues
from env_gen.data_gen.analysis.integration_plan import load_and_validate_integration_plan
from env_gen.data_gen.analysis.integration_profiling import build_integration_profile
from env_gen.data_gen.analysis.v2_validator import V2EnvironmentPackageValidator

from .common.constants import (
    CONTROL_DIRECTORY,
    CONTROL_RUN_CONFIG,
    FIELD_REVIEW_PATH,
    FREEZE_MANIFEST_PATH,
    INTEGRATION_PLAN_PATH,
    INTEGRATION_PROFILE_PATH,
    QUALITY_PROFILE_PATH,
    REPRODUCIBILITY_REPORT_PATH,
    SCENARIO_RESEARCH_PATH,
    SOURCE_INVENTORY_PATH,
    SOURCE_MANIFEST_PATH,
    SOURCE_PLAN_PATH,
)
from .common.control_io import control_path, read_json, write_json
from .common.workspace_files import file_sha256


class FinalValidationError(RuntimeError):
    """最终包未通过独立发布门。"""


def _same_payload(path: Path, expected: dict[str, Any], label: str) -> None:
    actual = read_json(path, label)
    if actual != expected:
        raise FinalValidationError(f"{label} 与 Step 5 独立重算结果不同")


def _verify_source_evidence(
    run_dir: Path,
    *,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    manifest = read_json(run_dir / SOURCE_MANIFEST_PATH, "发布来源清单")
    files = [item for item in manifest.get("files", []) if isinstance(item, dict)]
    manifest_by_raw = {
        str(item.get("path"))[len("provenance/"):]: item
        for item in files
        if str(item.get("path", "")).startswith("provenance/raw/")
    }
    errors: list[str] = []
    for relative, item in manifest_by_raw.items():
        path = run_dir / "provenance" / relative
        if not path.is_file():
            errors.append(f"来源文件不存在：provenance/{relative}")
        elif item.get("sha256") != file_sha256(path):
            errors.append(f"来源文件哈希不符：provenance/{relative}")
        elif item.get("bytes") != path.stat().st_size:
            errors.append(f"来源文件大小不符：provenance/{relative}")
    inventoried = {
        str(item.get("path")): item
        for item in inventory.get("files", []) if isinstance(item, dict)
    }
    if set(inventoried) != set(manifest_by_raw):
        errors.append(
            "source_inventory 与 provenance/raw 文件集合不同："
            f"缺少 {sorted(set(manifest_by_raw) - set(inventoried))[:8]}，"
            f"多出 {sorted(set(inventoried) - set(manifest_by_raw))[:8]}"
        )
    for relative, item in inventoried.items():
        manifest_item = manifest_by_raw.get(relative)
        if manifest_item is not None and item.get("sha256") != manifest_item.get("sha256"):
            errors.append(f"来源画像哈希与最终 Raw 不同：{relative}")
    actual_raw = {
        "raw/" + path.relative_to(run_dir / "provenance/raw").as_posix()
        for path in sorted((run_dir / "provenance/raw").rglob("*"))
        if path.is_file()
    } if (run_dir / "provenance/raw").is_dir() else set()
    if actual_raw != set(manifest_by_raw):
        errors.append("provenance/raw 存在未登记或缺失的文件")
    if errors:
        raise FinalValidationError("；".join(errors[:12]))
    return manifest


def _verify_reproducibility(
    run_dir: Path,
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    report = read_json(run_dir / REPRODUCIBILITY_REPORT_PATH, "可复现性报告")
    assets = {
        str(item.get("asset_id")): item
        for item in report.get("assets", []) if isinstance(item, dict)
    }
    expected_ids = {
        str(item.get("record_set_id")) for item in plan.get("record_sets", [])
    } | {
        str(item.get("scope_id")) for item in plan.get("filesystem_scopes", [])
    }
    errors: list[str] = []
    if set(assets) != expected_ids:
        errors.append("可复现性报告没有恰好覆盖所有最终资产")
    for item in plan.get("record_sets", []):
        asset_id = str(item["record_set_id"])
        receipt = assets.get(asset_id, {})
        package_value = receipt.get("package_path")
        package = run_dir / str(package_value)
        script_value = receipt.get("script_path")
        script = run_dir / str(script_value)
        if not package_value or not package.is_dir():
            errors.append(f"Record Set 缺少冻结转换包：{asset_id}")
        elif receipt.get("package_sha256") != tree_digest(package):
            errors.append(f"转换包哈希不符：{asset_id}")
        if not script_value or not script.is_file():
            errors.append(f"Record Set 缺少冻结转换脚本：{asset_id}")
        elif receipt.get("script_sha256") != file_sha256(script):
            errors.append(f"转换脚本哈希不符：{asset_id}")
        try:
            digest = table_digest(run_dir / "state/records.sqlite", asset_id)
        except Exception as error:
            errors.append(f"无法计算 Record Set 摘要 {asset_id}：{error}")
        else:
            if digest != receipt.get("state_digest"):
                errors.append(f"Record Set 状态摘要不符：{asset_id}")
    for item in plan.get("filesystem_scopes", []):
        asset_id = str(item["scope_id"])
        receipt = assets.get(asset_id, {})
        digest = tree_digest(run_dir / "state/filesystem_scopes" / asset_id)
        if digest != receipt.get("state_digest"):
            errors.append(f"Filesystem Scope 状态摘要不符：{asset_id}")
    for asset_id, receipt in assets.items():
        for source in receipt.get("source_files", []):
            if not isinstance(source, dict):
                continue
            relative = str(source.get("path") or "")
            path = run_dir / "provenance" / relative
            if not path.is_file() or source.get("sha256") != file_sha256(path):
                errors.append(f"资产 {asset_id} 的最终来源证据失效：{relative}")
    if errors:
        raise FinalValidationError("；".join(errors[:12]))
    return report


def _verify_freeze_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / FREEZE_MANIFEST_PATH, "冻结清单")
    entries = {
        str(item.get("path")): item
        for item in manifest.get("files", []) if isinstance(item, dict)
    }
    errors: list[str] = []
    for relative, item in entries.items():
        path = run_dir / relative
        if not path.is_file():
            errors.append(f"冻结文件被删除：{relative}")
        elif item.get("sha256") != file_sha256(path):
            errors.append(f"冻结文件被修改：{relative}")
    actual = {
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*") if path.is_file()
        and not path.relative_to(run_dir).as_posix().startswith(".datagen/")
        and path.relative_to(run_dir).as_posix() not in {
            FREEZE_MANIFEST_PATH, "validation.json",
        }
    }
    if set(entries) != actual:
        errors.append("冻结后新增或删除了未登记的最终包文件")
    if errors:
        raise FinalValidationError("；".join(errors[:12]))
    return manifest


def _archive_control_audit(run_dir: Path) -> None:
    control_root = run_dir / CONTROL_DIRECTORY
    if not control_root.is_dir():
        return
    control_files: list[dict[str, Any]] = []
    for path in sorted(control_root.rglob("*")):
        if not path.is_file() or path.name.endswith(".lock"):
            continue
        relative = path.relative_to(control_root).as_posix()
        if relative.startswith("drafts/"):
            continue
        control_files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    write_json(
        run_dir / "provenance/generation_audit.json",
        {
            "schema_version": "3.0",
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "control_files": control_files,
        },
    )


def validate_and_publish(
    run_dir: Path,
    *,
    final_output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    """独立复验冻结状态；成功后删除控制面并原子发布。"""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    scenario = read_json(run_dir / SCENARIO_RESEARCH_PATH, "场景研究")
    source_plan = read_json(run_dir / SOURCE_PLAN_PATH, "来源计划")
    inventory = read_json(run_dir / SOURCE_INVENTORY_PATH, "来源画像")
    plan, plan_issues = load_and_validate_integration_plan(
        run_dir / INTEGRATION_PLAN_PATH,
        schema_path=Path(config["integration_plan_schema_path"]),
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        scenario_research=scenario,
        source_plan=source_plan,
        source_inventory=inventory,
    )
    if plan_issues:
        raise FinalValidationError(
            "最终集成计划语义无效："
            + "; ".join(f"{item.path}: {item.message}" for item in plan_issues[:12])
        )
    _verify_source_evidence(run_dir, inventory=inventory)
    _verify_reproducibility(run_dir, plan=plan)
    integration = build_integration_profile(
        run_dir,
        plan=plan,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
    )
    _same_payload(run_dir / INTEGRATION_PROFILE_PATH, integration, "集成画像")
    review_issues = field_review_issues(
        run_dir,
        profile=integration,
        plan=plan,
        review_path=run_dir / FIELD_REVIEW_PATH,
        integration_plan_path=run_dir / INTEGRATION_PLAN_PATH,
        integration_profile_path=run_dir / INTEGRATION_PROFILE_PATH,
    )
    if review_issues:
        raise FinalValidationError(
            "最终字段语义复核未闭合："
            + "; ".join(str(item["message"]) for item in review_issues[:12])
        )
    quality = build_environment_quality_profile(
        run_dir,
        plan=plan,
        scenario_research=scenario,
        source_plan=source_plan,
        source_inventory=inventory,
        integration_profile=integration,
        policy=EnvironmentQualityPolicy(**config.get("environment_quality_policy", {})),
    )
    _same_payload(run_dir / QUALITY_PROFILE_PATH, quality, "质量画像")
    quality_tier = str(quality.get("quality_tier") or "")
    if integration.get("integration_tier") != "integrated" or quality_tier not in {"rich", "not_rich"}:
        raise FinalValidationError("最终集成画像或质量画像未通过发布门")
    validator = V2EnvironmentPackageValidator(Path(config["environment_schema_path"]))
    validation = validator.validate(run_dir, integration_plan=plan)
    if not validation.valid:
        raise FinalValidationError(
            "最终 v2 校验失败："
            + "; ".join(item.message for item in validation.errors[:12])
        )
    _verify_freeze_manifest(run_dir)
    _archive_control_audit(run_dir)
    shutil.rmtree(run_dir / CONTROL_DIRECTORY, ignore_errors=True)
    write_json(
        run_dir / "validation.json",
        {
            **validation.to_dict(),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "seed_global_id": str(config["seed_global_id"]),
            "seed_sha256": str(config["seed_sha256"]),
            "quality_tier": quality_tier,
            "integration_tier": "integrated",
        },
    )
    final_output_dir = final_output_dir.resolve()
    final_output_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"输出目录已经存在：{final_output_dir}")
        shutil.rmtree(final_output_dir)
    run_dir.replace(final_output_dir)
    return {
        "output_dir": final_output_dir,
        "quality_tier": quality_tier,
        "integration_tier": "integrated",
        "validation": validation.to_dict(),
    }


__all__ = ["FinalValidationError", "validate_and_publish"]
