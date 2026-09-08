"""Step 4: independently replay, freeze and publish the final v2 package."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.collection_analysis import (
    build_source_inventory,
    validate_source_inventory,
)
from env_gen.data_gen.analysis.v2_validator import V2EnvironmentPackageValidator

from .step2_collect_data import read_saved_source_research
from .common.constants import (
    COLLECTION_PROFILE_PATH,
    ENVIRONMENT_CONTEXT_PATH,
    FREEZE_MANIFEST_PATH,
    INTEGRATION_BUILD_PATH,
    INTEGRATION_RECEIPT_PATH,
    SOURCE_INVENTORY_PATH,
    SOURCE_MANIFEST_PATH,
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_RUN_CONFIG,
)
from .common.control_io import control_path, read_json, write_json
from .common.workspace_files import file_sha256
from .integration.direct_commands import (
    _state_manifest,
    assess_environment,
    finalization_issues,
)


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


def _source_manifest(run_dir: Path, *, source_research: dict[str, Any]) -> dict[str, Any]:
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
            "content_roles": item.get("content_roles"),
            "status": item.get("status"),
            "findings": item.get("findings"),
            "limitations": item.get("limitations"),
        }
        for item in source_research.get("sources", []) if isinstance(item, dict)
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


def _archive_direct_control_audit(run_dir: Path) -> None:
    control_root = run_dir / ".datagen"
    if not control_root.is_dir():
        return
    files: list[dict[str, Any]] = []
    for path in sorted(control_root.rglob("*")):
        if not path.is_file() or path.name.endswith(".lock"):
            continue
        relative = path.relative_to(control_root).as_posix()
        if relative.startswith("drafts/") or relative.startswith("agent_runs/"):
            continue
        files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    write_json(
        run_dir / "provenance/generation_audit.json",
        {
            "schema_version": "1.0",
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "control_files": files,
        },
    )


def _remove_python_caches(root: Path) -> None:
    for directory in sorted(root.rglob("__pycache__"), reverse=True):
        if directory.is_dir():
            shutil.rmtree(directory)
    for path in root.rglob("*.py[co]"):
        if path.is_file():
            path.unlink()


def _direct_integration_receipt(
    run_dir: Path,
    *,
    environment: dict[str, Any],
    assessment: dict[str, Any],
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    collection = read_json(run_dir / COLLECTION_PROFILE_PATH, "Step 2 采集画像")
    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment_sha256": file_sha256(run_dir / "environment.json"),
        "build": {
            "path": INTEGRATION_BUILD_PATH,
            "sha256": file_sha256(run_dir / INTEGRATION_BUILD_PATH),
            "runner": "bubblewrap_read_only_no_network",
            "interface": "--raw-dir <path> --state-dir <path>",
        },
        "raw_files": [
            {
                "path": item.get("path"),
                "source_id": item.get("source_id"),
                "sha256": item.get("sha256"),
                "bytes": item.get("bytes"),
            }
            for item in source_manifest.get("files", [])
            if isinstance(item, dict)
        ],
        "state": _state_manifest(run_dir / "state", environment),
        "replay_state_digest": assessment.get("replay_state_digest"),
        "coverage": collection.get("metrics", {}),
    }


def freeze_and_publish_environment(
    run_dir: Path,
    *,
    final_output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    """Independently replay, freeze and atomically publish a direct Step 3 result."""

    run_dir = run_dir.resolve()
    receipt_issues = finalization_issues(run_dir)
    if receipt_issues:
        raise EnvironmentFreezeError(
            "Step 3 尚未可靠收口："
            + "; ".join(item["message"] for item in receipt_issues[:12])
        )
    assessment = assess_environment(run_dir, replay=True)
    if assessment.get("decision") != "ready":
        raise EnvironmentFreezeError(
            "最终独立验收失败："
            + "; ".join(
                str(item.get("message"))
                for item in assessment.get("blocking_issues", [])[:12]
                if isinstance(item, dict)
            )
        )

    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    collection = read_json(run_dir / COLLECTION_PROFILE_PATH, "Step 2 采集画像")
    partial = (
        config.get("allow_partial_integration") is True
        and collection.get("decision") == "partial"
    )
    quality_tier = "partial" if partial else "rich"
    source_research = read_saved_source_research(run_dir)
    source_inventory = build_source_inventory(
        run_dir,
        seed_global_id=str(config["seed_global_id"]),
        seed_sha256=str(config["seed_sha256"]),
        source_research=source_research,
    )
    inventory_issues = validate_source_inventory(
        source_inventory, Path(config["source_inventory_schema_path"])
    )
    if inventory_issues:
        raise EnvironmentFreezeError(
            "最终来源文件卡无效：" + "; ".join(inventory_issues[:12])
        )
    write_json(run_dir / SOURCE_INVENTORY_PATH, source_inventory)

    database = run_dir / "state/records.sqlite"
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        finally:
            connection.close()

    environment = read_json(run_dir / "environment.json", "环境声明")
    source_manifest = _source_manifest(run_dir, source_research=source_research)
    write_json(run_dir / SOURCE_MANIFEST_PATH, source_manifest)
    raw_source = run_dir / "workspace/raw"
    raw_target = run_dir / "provenance/raw"
    if raw_target.exists():
        shutil.rmtree(raw_target)
    if raw_source.is_dir():
        shutil.copytree(raw_source, raw_target)
    write_json(
        run_dir / INTEGRATION_RECEIPT_PATH,
        _direct_integration_receipt(
            run_dir,
            environment=environment,
            assessment=assessment,
            source_manifest=source_manifest,
        ),
    )
    (run_dir / ENVIRONMENT_CONTEXT_PATH).write_text(
        _environment_markdown(environment), encoding="utf-8"
    )
    _remove_python_caches(run_dir / "provenance")
    _archive_direct_control_audit(run_dir)
    shutil.rmtree(run_dir / "workspace", ignore_errors=True)

    validator = V2EnvironmentPackageValidator(Path(config["environment_schema_path"]))
    validation = validator.validate(run_dir)
    if not validation.valid:
        raise EnvironmentFreezeError(
            "冻结后的 v2 环境包无效："
            + "; ".join(item.message for item in validation.errors[:12])
        )
    manifest = _freeze_manifest(run_dir)
    write_json(run_dir / FREEZE_MANIFEST_PATH, manifest)
    shutil.rmtree(run_dir / ".datagen", ignore_errors=True)
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


__all__ = [
    "EnvironmentFreezeError",
    "freeze_and_publish_environment",
]
