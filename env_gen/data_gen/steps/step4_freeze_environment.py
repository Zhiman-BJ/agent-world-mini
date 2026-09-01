"""Step 4：独立重算后冻结 v2 状态，并整理最终环境包布局。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from env_gen.data_gen.analysis.artifact_integrity import table_digest, tree_digest
from env_gen.data_gen.analysis.environment_quality import (
    EnvironmentQualityPolicy,
    build_environment_quality_profile,
)
from env_gen.data_gen.analysis.field_review import field_review_issues
from env_gen.data_gen.analysis.integration_materialization import (
    environment_from_plan,
    materialize_record_set,
    materialize_scope,
)
from env_gen.data_gen.analysis.integration_profiling import build_integration_profile
from env_gen.data_gen.analysis.source_inventory import (
    build_source_inventory,
    validate_source_inventory,
)
from env_gen.data_gen.analysis.v2_validator import V2EnvironmentPackageValidator

from .collection.commands.download_raw import download_receipt_issues
from .collection.commands.save_source_plan import (
    read_saved_source_plan,
    source_plan_receipt_issues,
)
from .common.constants import (
    ENVIRONMENT_CONTEXT_PATH,
    FIELD_REVIEW_PATH,
    FREEZE_MANIFEST_PATH,
    INTEGRATION_PLAN_PATH,
    INTEGRATION_PROFILE_PATH,
    QUALITY_PROFILE_PATH,
    REPRODUCIBILITY_REPORT_PATH,
    SOURCE_INVENTORY_PATH,
    SOURCE_MANIFEST_PATH,
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_INTEGRATION_MATERIALIZATION_RECEIPTS,
    CONTROL_INTEGRATION_FINALIZATION,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
)
from .common.control_io import control_path, read_json, write_json
from .common.workspace_files import file_sha256
from .integration.commands import (
    integration_plan_receipt_issues,
    materialization_receipt_issues,
)
from .integration.commands import _validated_plan as validated_integration_plan
from .integration.transformation_runner import run_record_transformation


class EnvironmentFreezeError(RuntimeError):
    """候选状态无法在无 Agent 参与的情况下复验并冻结。"""


def _environment_markdown(environment: dict[str, Any]) -> str:
    lines = [
        f"# {environment['name']}",
        "",
        str(environment["description"]),
        "",
        "## 可查询记录",
        "",
    ]
    record_sets = environment.get("record_sets", [])
    if record_sets:
        for item in record_sets:
            fields = ", ".join(item.get("fields", {}))
            lines.append(
                f"- `{item['record_set_id']}`：{item['description']} 字段：{fields}。"
            )
    else:
        lines.append("- 无结构化 Record Set。")
    lines.extend(["", "## 可处理文件范围", ""])
    scopes = environment.get("filesystem_scopes", [])
    if scopes:
        for item in scopes:
            structure = item.get("structure", {})
            lines.append(
                f"- `{item['scope_id']}`：{item['description']} "
                f"入口为 `{structure.get('path')}`（{structure.get('kind')}）。"
            )
    else:
        lines.append("- 无 Filesystem Scope。")
    lines.extend(["", "## 数据关系", ""])
    relationships = environment.get("relationships", [])
    if relationships:
        for item in relationships:
            source = item["from"]
            target = item["to"]
            lines.append(
                f"- `{item['relationship_id']}`："
                f"`{source['record_set_id']}.{','.join(source['fields'])}` -> "
                f"`{target['record_set_id']}.{','.join(target['fields'])}`。"
            )
    else:
        lines.append("- 没有需要跨 Record Set 声明的关系。")
    lines.extend([
        "",
        "> 该文件只提供环境导航。任务侧通过工具访问记录与文件，不直接访问 SQLite 或 provenance。",
        "",
    ])
    return "\n".join(lines)


def _schema_validate(payload: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise EnvironmentFreezeError(
            f"{label} 不符合 Schema：" + "; ".join(error.message for error in errors[:12])
        )


def _reproducibility_report(
    run_dir: Path,
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    receipts_path = control_path(run_dir, CONTROL_INTEGRATION_MATERIALIZATION_RECEIPTS)
    receipts_payload = read_json(receipts_path, "集成物化收据")
    receipts = {
        str(item.get("asset_id")): item
        for item in receipts_payload.get("assets", []) if isinstance(item, dict)
    }
    database = run_dir / "state/records.sqlite"
    assets: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="datagen-freeze-replay-") as directory:
        replay_root = Path(directory)
        replay_database = replay_root / "records.sqlite"
        replay_scopes = replay_root / "filesystem_scopes"
        for item in plan.get("record_sets", []):
            asset_id = str(item["record_set_id"])
            receipt = receipts[asset_id]
            script = run_dir / str(receipt.get("script_path"))
            output_directory = replay_root / "outputs" / asset_id
            output = output_directory / "records.json"
            run_record_transformation(
                run_dir,
                script=script,
                output=output,
                asset_id=asset_id,
                timeout_seconds=300,
            )
            output_sha256 = file_sha256(output)
            if output_sha256 != receipt.get("output_sha256"):
                raise EnvironmentFreezeError(
                    f"Record Set {asset_id} 独立重放输出与 Step 3 不同"
                )
            replay_count = materialize_record_set(
                replay_database, record_set=item, input_path=output,
            )
            replay_digest = table_digest(replay_database, asset_id)
            state_digest = table_digest(database, asset_id)
            if replay_digest != state_digest:
                raise EnvironmentFreezeError(
                    f"Record Set {asset_id} 独立重放状态与候选状态不同"
                )
            if replay_count != int(receipt.get("item_count", -1)):
                raise EnvironmentFreezeError(
                    f"Record Set {asset_id} 独立重放记录数与收据不同"
                )
            assets.append({
                "asset_kind": "record_set",
                "asset_id": asset_id,
                "transformation_id": receipt.get("transformation_id"),
                "package_path": receipt.get("package_path"),
                "package_sha256": receipt.get("package_sha256"),
                "script_path": receipt.get("script_path"),
                "script_sha256": receipt.get("script_sha256"),
                "sandbox": receipt.get("sandbox"),
                "source_files": receipt.get("source_files", []),
                "output_sha256": output_sha256,
                "state_digest": state_digest,
                "replay_state_digest": replay_digest,
                "item_count": replay_count,
            })
        for item in plan.get("filesystem_scopes", []):
            asset_id = str(item["scope_id"])
            receipt = receipts[asset_id]
            source_paths = [
                run_dir / "workspace" / str(source["path"])
                for source in receipt.get("source_files", [])
            ]
            replay_count = materialize_scope(
                replay_scopes,
                scope_id=asset_id,
                sources=source_paths,
                mode=str(item["materialization"]),
            )
            replay_digest = tree_digest(replay_scopes / asset_id)
            state_digest = tree_digest(run_dir / "state/filesystem_scopes" / asset_id)
            if replay_digest != state_digest:
                raise EnvironmentFreezeError(
                    f"Filesystem Scope {asset_id} 独立重放状态与候选状态不同"
                )
            if replay_count != int(receipt.get("item_count", -1)):
                raise EnvironmentFreezeError(
                    f"Filesystem Scope {asset_id} 独立重放文件数与收据不同"
                )
            assets.append({
                "asset_kind": "filesystem_scope",
                "asset_id": asset_id,
                "transformation_id": receipt.get("transformation_id"),
                "package_path": None,
                "package_sha256": None,
                "script_path": None,
                "script_sha256": None,
                "sandbox": receipt.get("sandbox"),
                "source_files": receipt.get("source_files", []),
                "output_sha256": replay_digest,
                "state_digest": state_digest,
                "replay_state_digest": replay_digest,
                "item_count": replay_count,
            })
    return {
        "schema_version": "1.0",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "assets": sorted(assets, key=lambda value: str(value["asset_id"])),
    }


def _source_manifest(run_dir: Path, *, source_plan: dict[str, Any]) -> dict[str, Any]:
    receipt_path = control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    downloads = read_json(receipt_path, "下载收据").get("downloads", [])
    by_path: dict[str, list[dict[str, Any]]] = {}
    for item in downloads:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            by_path.setdefault(str(item["path"]), []).append(item)
    files: list[dict[str, Any]] = []
    raw_root = run_dir / "workspace/raw"
    for path in sorted(raw_root.rglob("*")) if raw_root.is_dir() else []:
        if not path.is_file():
            continue
        workspace_relative = path.relative_to(run_dir / "workspace").as_posix()
        receipts = by_path.get(workspace_relative, [])
        if not receipts:
            raise EnvironmentFreezeError(f"Raw 缺少下载收据：{workspace_relative}")
        source_ids = {str(item.get("source_id")) for item in receipts}
        if len(source_ids) != 1:
            raise EnvironmentFreezeError(
                f"Raw {workspace_relative} 的下载证据跨多个来源：{sorted(source_ids)}"
            )
        files.append({
            "path": "provenance/" + workspace_relative,
            "source_id": next(iter(source_ids)),
            "retrievals": [
                {
                    "url": item.get("url"),
                    "effective_url": item.get("effective_url"),
                    "reused_existing_file": bool(item.get("reused_existing_file")),
                }
                for item in sorted(receipts, key=lambda value: str(value.get("url")))
            ],
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    sources = [
        {
            "source_id": item.get("source_id"),
            "name": item.get("name"),
            "publisher_url": item.get("url"),
            "priority": item.get("priority"),
            "status": item.get("status"),
            "access_status": item.get("access_status"),
            "status_evidence": item.get("status_evidence"),
        }
        for item in source_plan.get("sources", []) if isinstance(item, dict)
    ]
    return {
        "schema_version": "1.0",
        "sources": sources,
        "files": files,
    }


def _freeze_manifest(run_dir: Path) -> dict[str, Any]:
    excluded = {
        ".datagen",
        "workspace",
        FREEZE_MANIFEST_PATH,
        "validation.json",
    }
    files: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if any(relative == value or relative.startswith(value + "/") for value in excluded):
            continue
        files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    return {
        "schema_version": "1.0",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def freeze_environment(run_dir: Path) -> dict[str, Any]:
    """重算所有关键事实，随后把生成现场收敛为 v2 最终包。"""

    run_dir = run_dir.resolve()
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    seed = read_json(control_path(run_dir, CONTROL_SELECTED_SEED), "选中 Seed")
    source_plan = read_saved_source_plan(run_dir)
    preparation = (
        source_plan_receipt_issues(run_dir)
        + download_receipt_issues(run_dir)
        + integration_plan_receipt_issues(run_dir)
    )
    if preparation:
        raise EnvironmentFreezeError(
            "冻结前来源或计划证据不完整："
            + "; ".join(str(item.get("message")) for item in preparation[:12])
        )
    integration_finalization_path = control_path(run_dir, CONTROL_INTEGRATION_FINALIZATION)
    if not integration_finalization_path.is_file():
        raise EnvironmentFreezeError("缺少 Step 3 集成收口证据")
    integration_finalization = read_json(
        integration_finalization_path, "集成收口"
    )
    if integration_finalization.get("decision") != "finalized" or integration_finalization.get("result") not in {"ready", "exhausted"}:
        raise EnvironmentFreezeError("Step 3 集成收口结果无效")
    source_inventory = build_source_inventory(
        run_dir,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        source_plan=source_plan,
    )
    inventory_issues = validate_source_inventory(
        source_inventory, Path(config["source_inventory_schema_path"])
    )
    if inventory_issues:
        raise EnvironmentFreezeError("来源画像无效：" + "; ".join(inventory_issues[:12]))
    write_json(run_dir / SOURCE_INVENTORY_PATH, source_inventory)
    plan = validated_integration_plan(run_dir)
    receipt_issues = materialization_receipt_issues(run_dir, plan)
    if receipt_issues:
        raise EnvironmentFreezeError(
            "冻结前物化证据失效："
            + "; ".join(str(item.get("message")) for item in receipt_issues[:12])
        )
    integration_profile = build_integration_profile(
        run_dir,
        plan=plan,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
    )
    _schema_validate(
        integration_profile, Path(config["integration_profile_schema_path"]), "integration_profile"
    )
    review_issues = field_review_issues(
        run_dir,
        profile=integration_profile,
        plan=plan,
        review_path=run_dir / FIELD_REVIEW_PATH,
        integration_plan_path=run_dir / INTEGRATION_PLAN_PATH,
        integration_profile_path=run_dir / INTEGRATION_PROFILE_PATH,
    )
    if review_issues:
        raise EnvironmentFreezeError(
            "冻结前字段语义复核未闭合："
            + "; ".join(str(item["message"]) for item in review_issues[:12])
        )
    quality_profile = build_environment_quality_profile(
        run_dir,
        plan=plan,
        scenario_research=read_json(
            run_dir / "provenance/scenario_research.json", "场景研究"
        ),
        source_plan=source_plan,
        source_inventory=source_inventory,
        integration_profile=integration_profile,
        policy=EnvironmentQualityPolicy(**config.get("environment_quality_policy", {})),
    )
    _schema_validate(
        quality_profile,
        Path(config["environment_quality_profile_schema_path"]),
        "quality_profile",
    )
    if integration_profile["integration_tier"] != "integrated":
        raise EnvironmentFreezeError("冻结前独立集成画像不是 integrated")
    if quality_profile["quality_tier"] not in {"rich", "not_rich"}:
        raise EnvironmentFreezeError(
            "冻结前独立质量画像无效："
            + "; ".join(item["message"] for item in quality_profile["quality_gaps"][:10])
        )
    environment = environment_from_plan(plan)
    write_json(run_dir / "environment.json", environment)
    write_json(run_dir / INTEGRATION_PROFILE_PATH, integration_profile)
    write_json(run_dir / QUALITY_PROFILE_PATH, quality_profile)
    validator = V2EnvironmentPackageValidator(Path(config["environment_v2_schema_path"]))
    validation = validator.validate(run_dir, integration_plan=plan)
    if not validation.valid:
        raise EnvironmentFreezeError(
            "冻结前 v2 独立校验失败："
            + "; ".join(item.message for item in validation.errors[:12])
        )

    if (run_dir / "state/records.sqlite").is_file():
        connection = sqlite3.connect(run_dir / "state/records.sqlite")
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        finally:
            connection.close()
    source_manifest = _source_manifest(run_dir, source_plan=source_plan)
    write_json(run_dir / SOURCE_MANIFEST_PATH, source_manifest)
    reproducibility = _reproducibility_report(run_dir, plan=plan)
    write_json(run_dir / REPRODUCIBILITY_REPORT_PATH, reproducibility)
    (run_dir / ENVIRONMENT_CONTEXT_PATH).write_text(
        _environment_markdown(environment), encoding="utf-8"
    )
    raw_source = run_dir / "workspace/raw"
    raw_target = run_dir / "provenance/raw"
    if raw_target.exists():
        shutil.rmtree(raw_target)
    if raw_source.is_dir():
        shutil.copytree(raw_source, raw_target)
    shutil.rmtree(run_dir / "workspace", ignore_errors=True)
    manifest = _freeze_manifest(run_dir)
    write_json(run_dir / FREEZE_MANIFEST_PATH, manifest)
    return {
        "environment": environment,
        "integration_profile": integration_profile,
        "quality_profile": quality_profile,
        "validation": validation.to_dict(),
        "freeze_manifest": manifest,
    }


__all__ = ["EnvironmentFreezeError", "freeze_environment"]
