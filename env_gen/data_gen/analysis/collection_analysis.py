"""Coarse collection facts used between Step 2 and Step 3.

Step 2 does not build the final data model.  This module therefore records only
file identity, size, format, approximate record count and source ownership.
Field-level profiling and cross-source integration belong to Step 3.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
from typing import Any
from urllib.parse import parse_qs, urlparse
import zipfile

from jsonschema import Draft202012Validator

from env_gen.data_gen.steps.common.constants import (
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_PREPARATION_MANIFEST,
    CONTROL_RUN_CONFIG,
    SOURCE_INVENTORY_PATH,
)
from env_gen.data_gen.steps.common.control_io import control_path, read_json, write_json
from env_gen.data_gen.steps.common.download import load_download_ledger, simple_file_stats
from env_gen.data_gen.steps.common.workspace_files import file_sha256


_COMMIT_LENGTH = 40


def _repository_url_stability(url: str) -> str | None:
    """Classify GitHub URLs without inspecting repository contents."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    revision: str | None = None
    if host == "raw.githubusercontent.com" and len(parts) >= 3:
        revision = parts[2]
    elif host == "github.com" and len(parts) >= 4:
        if parts[2] in {"raw", "blob"}:
            revision = parts[3]
        elif parts[2] == "archive":
            revision = parts[-1].removesuffix(".zip").removesuffix(".tar.gz")
    elif host == "codeload.github.com" and len(parts) >= 4:
        revision = parts[-1]
    elif host == "api.github.com" and parts[:1] == ["repos"]:
        values = parse_qs(parsed.query).get("ref", [])
        revision = values[0] if values else None
    if revision is None:
        return None
    is_commit = len(revision) == _COMMIT_LENGTH and all(character in "0123456789abcdefABCDEF" for character in revision)
    return "immutable_repository" if is_commit else "mutable_repository"


def _stability(urls: list[str]) -> str:
    findings = {_repository_url_stability(url) for url in urls}
    findings.discard(None)
    if "mutable_repository" in findings:
        return "mutable_repository"
    if "immutable_repository" in findings:
        return "immutable_repository"
    return "timestamped_snapshot" if urls else "untracked"


def _content_roles(role: str) -> list[str]:
    return {
        "business_records": ["structured_records"],
        "task_domain_files": ["domain_file"],
        "semantic_evidence": ["documentation"],
    }.get(role, ["unknown"])


def _ledger_role(source: dict[str, Any]) -> str:
    roles = set(source.get("content_roles", []))
    if "structured_data" in roles:
        return "business_records"
    if "domain_files" in roles:
        return "task_domain_files"
    if "semantic_evidence" in roles:
        return "semantic_evidence"
    return "business_records"


def _card_by_path(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = control_path(run_dir, "collection_profile.json")
    if not path.is_file():
        return {}
    payload = read_json(path, "采集文件卡")
    return {
        str(item.get("path")): item
        for item in payload.get("file_cards", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def build_source_inventory(
    run_dir: Path,
    *,
    seed_global_id: str,
    seed_sha256: str,
    source_research: dict[str, Any],
) -> dict[str, Any]:
    """Build a small inventory directly from the download ledger and Raw files."""

    run_dir = run_dir.resolve()
    ledger = load_download_ledger(run_dir)
    cards = _card_by_path(run_dir)
    research_sources = {
        str(item.get("source_id")): item
        for item in source_research.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    source_status = {
        str(item.get("source_id")): str(item.get("status") or "complete")
        for item in source_research.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    entries = [
        item for item in ledger.get("downloads", [])
        if isinstance(item, dict) and item.get("status") == "downloaded"
    ]
    represented_paths = {str(item.get("path")) for item in entries}
    receipt_path = control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if receipt_path.is_file():
        receipts = read_json(receipt_path, "下载收据").get("downloads", [])
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            logical = str(receipt.get("path") or "")
            if not logical or logical in represented_paths:
                continue
            source_id = str(receipt.get("source_id") or "untracked")
            entries.append({
                "status": "downloaded",
                "path": logical,
                "source_id": source_id,
                "urls": [str(receipt.get("url"))] if receipt.get("url") else [],
                "sha256": receipt.get("sha256"),
                "format": "any",
                "role": _ledger_role(research_sources.get(source_id, {})),
            })
            represented_paths.add(logical)

    files: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") != "downloaded":
            continue
        logical = str(entry.get("path") or "")
        physical = run_dir / "workspace" / logical
        if not physical.is_file():
            continue
        stats = simple_file_stats(physical, format_hint=str(entry.get("format") or "any"))
        card = cards.get(logical, {})
        urls = [str(value) for value in entry.get("urls", []) if isinstance(value, str)]
        files.append({
            "path": logical,
            "source_id": str(entry.get("source_id") or "untracked"),
            "sha256": file_sha256(physical),
            "bytes": physical.stat().st_size,
            "format": str(stats.get("format") or "binary"),
            "record_count": stats.get("record_count"),
            "file_count": int(stats.get("file_count") or 1),
            "retrieval_stability": _stability(urls),
            "retrieval_urls": urls,
            "content_roles": _content_roles(str(entry.get("role") or "")),
            "summary": str(card.get("summary") or "尚未填写业务文件卡。"),
            "subjects": card.get("subjects", []),
            "prepared_paths": card.get("prepared_paths", []),
            "issues": list(card.get("limitations", [])),
        })

    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        by_source.setdefault(item["source_id"], []).append(item)
    sources = []
    for source_id in sorted(set(by_source) | set(research_sources)):
        owned = by_source.get(source_id, [])
        if not owned:
            sources.append({
                "source_id": source_id,
                "file_paths": [],
                "file_count": 0,
                "total_bytes": 0,
                "structured_record_count": 0,
                "formats": [],
                "content_roles": [],
                "source_status": source_status.get(source_id, "planned"),
                "profile_status": "not_collected",
                "issues": [],
            })
            continue
        roles = sorted({role for item in owned for role in item["content_roles"]})
        sources.append({
            "source_id": source_id,
            "file_paths": [item["path"] for item in owned],
            "file_count": len(owned),
            "total_bytes": sum(item["bytes"] for item in owned),
            "structured_record_count": sum(
                int(item["record_count"] or 0) for item in owned
                if "structured_records" in item["content_roles"]
            ),
            "formats": sorted({item["format"] for item in owned}),
            "content_roles": roles,
            "source_status": source_status.get(source_id, "complete"),
            "profile_status": "usable" if all(item["summary"] != "尚未填写业务文件卡。" for item in owned) else "partial",
            "issues": list(dict.fromkeys(
                issue for item in owned for issue in item["issues"]
            )),
        })

    payload = {
        "schema_version": "2.0",
        "seed_global_id": seed_global_id,
        "seed_sha256": seed_sha256,
        "summary": {
            "source_count": len(sources),
            "file_count": len(files),
            "total_bytes": sum(item["bytes"] for item in files),
            "usable_file_count": sum(bool(item["summary"] != "尚未填写业务文件卡。") for item in files),
            "structured_record_count": sum(
                int(item["record_count"] or 0) for item in files
                if "structured_records" in item["content_roles"]
            ),
            "formats": sorted({item["format"] for item in files}),
        },
        "sources": sources,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    return payload


def validate_source_inventory(payload: dict[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    return [
        f"$.{'.'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: tuple(str(part) for part in item.absolute_path))
    ]


def inspect_data_path(run_dir: Path, *, input_path: str) -> dict[str, Any]:
    """Inspect one Raw/Prepared file or directory without writing a profile file."""

    run_dir = run_dir.resolve()
    logical = input_path.removeprefix("workspace/").lstrip("/")
    if not logical.startswith(("raw/", "prepared/")):
        raise ValueError("input 必须位于 raw/ 或 prepared/")
    target = (run_dir / "workspace" / logical).resolve()
    root_name = "raw" if logical.startswith("raw/") else "prepared"
    allowed_root = (run_dir / "workspace" / root_name).resolve()
    try:
        target.relative_to(allowed_root)
    except ValueError as error:
        raise ValueError("input 不能越过 workspace") from error
    if not target.exists():
        raise FileNotFoundError(logical)
    if target.is_file():
        return {"path": logical, **simple_file_stats(target), "sha256": file_sha256(target)}
    files = [path for path in sorted(target.rglob("*")) if path.is_file()]
    return {
        "path": logical,
        "bytes": sum(path.stat().st_size for path in files),
        "format": "directory",
        "record_count": None,
        "file_count": len(files),
        "sha256": _tree_sha256(target, files),
    }


def _tree_sha256(root: Path, files: list[Path] | None = None) -> str:
    digest = hashlib.sha256()
    for path in files or [item for item in sorted(root.rglob("*")) if item.is_file()]:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(file_sha256(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _safe_member(name: str) -> Path:
    candidate = Path(name.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeError(f"归档包含不安全路径：{name}")
    return candidate


def _manifest(run_dir: Path) -> dict[str, Any]:
    path = control_path(run_dir, CONTROL_PREPARATION_MANIFEST)
    return read_json(path, "预处理清单") if path.is_file() else {"schema_version": "1.0", "artifacts": []}


def register_prepared_artifact(
    run_dir: Path,
    *,
    input_paths: list[str],
    prepared_path: str,
    operation: str,
    tool: str = "agent",
    content_effect: str = "derived view",
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not input_paths or not operation.strip():
        raise ValueError("Prepared 必须声明输入和操作")
    output = (run_dir / "workspace" / prepared_path).resolve()
    prepared_root = (run_dir / "workspace/prepared").resolve()
    try:
        output.relative_to(prepared_root)
    except ValueError as error:
        raise ValueError("prepared_path 必须位于 prepared/") from error
    if not output.exists():
        raise FileNotFoundError(prepared_path)
    for value in input_paths:
        candidate = (run_dir / "workspace" / value).resolve()
        root_name = "raw" if str(value).startswith("raw/") else "prepared"
        allowed_root = (run_dir / "workspace" / root_name).resolve()
        try:
            candidate.relative_to(allowed_root)
        except ValueError as error:
            raise ValueError(f"预处理输入越界：{value}") from error
        if not candidate.exists() or not str(value).startswith(("raw/", "prepared/")):
            raise ValueError(f"预处理输入无效：{value}")
    facts = inspect_data_path(run_dir, input_path=prepared_path)
    artifact = {
        "input_paths": sorted(set(input_paths)),
        "prepared_path": prepared_path,
        "operation": operation.strip(),
        "tool": tool.strip() or "agent",
        "content_effect": content_effect.strip() or "derived view",
        "file_count": facts["file_count"],
        "total_bytes": facts["bytes"],
        "tree_sha256": facts["sha256"],
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest = _manifest(run_dir)
    manifest["artifacts"] = [
        item for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("prepared_path") != prepared_path
    ] + [artifact]
    write_json(control_path(run_dir, CONTROL_PREPARATION_MANIFEST), manifest)
    return artifact


def prepare_archive(run_dir: Path, *, input_path: str) -> dict[str, Any]:
    """Safely extract one ZIP/TAR into Prepared and register one lineage row."""

    run_dir = run_dir.resolve()
    source = (run_dir / "workspace" / input_path).resolve()
    raw_root = (run_dir / "workspace/raw").resolve()
    prepared_root = (run_dir / "workspace/prepared").resolve()
    try:
        source.relative_to(raw_root)
    except ValueError as error:
        raise ValueError("extract 输入必须位于 raw/") from error
    if not source.is_file():
        raise FileNotFoundError(input_path)
    output_relative = f"prepared/{source.stem}-{file_sha256(source)[:10]}"
    output = run_dir / "workspace" / output_relative
    if output.exists():
        return inspect_data_path(run_dir, input_path=output_relative)
    temporary = prepared_root / f".{source.stem}-{os.getpid()}.part"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    policy = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置").get("collection_policy", {})
    maximum_bytes = int(policy.get("max_derived_bytes", 0) or 0)

    def check_size(size: int) -> None:
        if maximum_bytes and size > maximum_bytes:
            raise RuntimeError(f"归档展开大小超过 Prepared 上限 {maximum_bytes} bytes")

    try:
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                check_size(sum(item.file_size for item in archive.infolist() if not item.is_dir()))
                for item in archive.infolist():
                    _safe_member(item.filename)
                archive.extractall(temporary)
        elif tarfile.is_tarfile(source):
            with tarfile.open(source, "r:*") as archive:
                check_size(sum(item.size for item in archive.getmembers() if item.isfile()))
                for item in archive.getmembers():
                    _safe_member(item.name)
                    if item.issym() or item.islnk():
                        raise RuntimeError("归档符号链接不允许")
                archive.extractall(temporary, filter="data")
        elif source.suffix.lower() == ".gz":
            output_name = source.name[:-3] or "content"
            with gzip.open(source, "rb") as compressed, (temporary / output_name).open("wb") as target:
                total = 0
                while True:
                    chunk = compressed.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    check_size(total)
                    target.write(chunk)
        else:
            raise ValueError("只支持 ZIP、TAR 或单文件 GZIP")
        os.replace(temporary, output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    artifact = register_prepared_artifact(
        run_dir,
        input_paths=[input_path],
        prepared_path=output_relative,
        operation="extract archive",
        tool="python archive reader",
        content_effect="完整安全展开归档，未筛选成员",
    )
    return {"status": "prepared", **artifact}


__all__ = [
    "_repository_url_stability",
    "build_source_inventory",
    "inspect_data_path",
    "prepare_archive",
    "register_prepared_artifact",
    "validate_source_inventory",
]
