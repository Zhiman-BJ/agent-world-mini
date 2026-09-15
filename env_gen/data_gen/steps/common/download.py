"""Small, auditable downloader shared by collection and integration.

The downloader has four jobs only:

1. avoid fetching the same request or content twice;
2. install a non-empty, structurally readable file under ``workspace/raw``;
3. record URL and SHA-256 facts;
4. return a small file summary to the Agent.

Business usefulness is deliberately not inferred here.  The collection Agent
records that decision in a file card after looking at the downloaded content.
"""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tarfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile
import zlib

from .constants import CONTROL_DOWNLOAD_RECEIPTS, CONTROL_RUN_CONFIG
from .control_io import control_path, read_json, write_json
from .workspace_files import file_sha256


DOWNLOAD_LEDGER = "download_ledger.json"
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORMAT_ALIASES = {
    "db": "sqlite",
    "gz": "gzip",
    "htm": "html",
    "markdown": "text",
    "md": "text",
    "ndjson": "jsonl",
    "py": "python",
    "sol": "solidity",
    "ts": "typescript",
    "tar.gz": "tar",
    "tgz": "tar",
    "txt": "text",
    "yaml": "text",
    "yml": "text",
}


class DownloadFailure(RuntimeError):
    """A download failure with a small machine-readable receipt."""

    def __init__(self, message: str, *, url: str, code: str = "download_failed") -> None:
        super().__init__(message)
        self.url = url
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"status": "failed", "code": self.code, "url": self.url, "message": str(self)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_ledger() -> dict[str, Any]:
    return {"schema_version": "1.0", "downloads": [], "failures": []}


def load_download_ledger(run_dir: Path) -> dict[str, Any]:
    path = control_path(run_dir.resolve(), DOWNLOAD_LEDGER)
    if not path.is_file():
        return _empty_ledger()
    payload = read_json(path, "下载账本")
    if not isinstance(payload.get("downloads"), list) or not isinstance(payload.get("failures"), list):
        raise RuntimeError("下载账本结构无效")
    return payload


def _save_ledger(run_dir: Path, payload: dict[str, Any]) -> None:
    write_json(control_path(run_dir.resolve(), DOWNLOAD_LEDGER), payload)


def _load_receipts(run_dir: Path) -> dict[str, Any]:
    path = control_path(run_dir.resolve(), CONTROL_DOWNLOAD_RECEIPTS)
    if not path.is_file():
        return {"schema_version": "1.0", "downloads": []}
    payload = read_json(path, "下载收据")
    if not isinstance(payload.get("downloads"), list):
        raise RuntimeError("下载收据结构无效")
    return payload


def _append_receipt(run_dir: Path, receipt: dict[str, Any]) -> None:
    payload = _load_receipts(run_dir)
    payload["downloads"].append(receipt)
    write_json(control_path(run_dir.resolve(), CONTROL_DOWNLOAD_RECEIPTS), payload)


def _body(method: str, json_body: dict[str, Any] | None) -> tuple[str, bytes | None, str | None]:
    normalized = method.upper().strip()
    if normalized not in {"GET", "POST"}:
        raise ValueError("method 只支持 GET 或 POST")
    if normalized == "GET" and json_body is not None:
        raise ValueError("GET 不能携带 json_body")
    if normalized == "POST" and json_body is None:
        raise ValueError("POST 必须携带 json_body")
    encoded = (
        json.dumps(json_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if json_body is not None
        else None
    )
    return normalized, encoded, hashlib.sha256(encoded).hexdigest() if encoded else None


def _request_key(method: str, url: str, body_sha256: str | None) -> str:
    return hashlib.sha256(f"{method}\0{url}\0{body_sha256 or ''}".encode()).hexdigest()


def _raw_target(run_dir: Path, output: str) -> tuple[Path, str]:
    logical = output.removeprefix("workspace/").lstrip("/")
    if not logical.startswith("raw/") or logical.endswith("/"):
        raise ValueError("output 必须是 raw/ 下的文件路径")
    workspace = (run_dir.resolve() / "workspace").resolve()
    target = (workspace / logical).resolve()
    try:
        target.relative_to(workspace / "raw")
    except ValueError as error:
        raise ValueError("output 不能越过 workspace/raw") from error
    return target, logical


def _suffix_format(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar"
    if name.endswith(".gz"):
        return "gzip"
    suffix = path.suffix.lower().lstrip(".") or "binary"
    return _FORMAT_ALIASES.get(suffix, suffix)


def _detected_format(path: Path, content_type: str = "") -> str:
    suffix = _suffix_format(path)
    with path.open("rb") as stream:
        head = stream.read(32)
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.startswith((b"{", b"[")) or "json" in content_type.lower():
        return "geojson" if suffix == "geojson" else "json"
    return suffix


def _json_record_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("items", "records", "results", "data", "features"):
            if isinstance(value.get(key), list):
                return len(value[key])
        return 1
    return 0


def simple_file_stats(path: Path, *, format_hint: str | None = None) -> dict[str, Any]:
    """Return only coarse facts needed for a collection file card."""

    format_name = _FORMAT_ALIASES.get(str(format_hint or "").lower(), str(format_hint or "").lower())
    if not format_name or format_name == "any":
        format_name = _detected_format(path)
    result: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "format": format_name,
        "record_count": None,
        "file_count": 1,
    }
    if format_name in {"json", "geojson"}:
        with path.open(encoding="utf-8") as stream:
            result["record_count"] = _json_record_count(json.load(stream))
    elif format_name == "jsonl":
        count = 0
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    json.loads(line)
                    count += 1
        result["record_count"] = count
    elif format_name == "gzip":
        count = 0
        with gzip.open(path, "rb") as stream:
            for line in stream:
                if line.strip():
                    count += 1
        result["record_count"] = count
    elif format_name in {"csv", "tsv"}:
        delimiter = "\t" if format_name == "tsv" else ","
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = sum(1 for _ in csv.reader(stream, delimiter=delimiter))
        result["record_count"] = max(0, rows - 1)
    elif format_name == "sqlite":
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            check = connection.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise ValueError("SQLite quick_check 未通过")
            result["table_count"] = int(connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0])
        finally:
            connection.close()
    elif format_name == "zip":
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"ZIP 成员损坏：{bad}")
            result["file_count"] = sum(not item.is_dir() for item in archive.infolist())
    elif format_name == "tar":
        with tarfile.open(path, "r:*") as archive:
            result["file_count"] = sum(item.isfile() for item in archive.getmembers())
    elif format_name in {"text", "html", "xml", "yaml"}:
        with path.open(encoding="utf-8", errors="replace") as stream:
            result["line_count"] = sum(1 for _ in stream)
    return result


def _validate_file(path: Path, expected_format: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("下载结果为空")
    expected = _FORMAT_ALIASES.get(expected_format.lower(), expected_format.lower())
    detected = _detected_format(path)
    if expected not in {"", "any", detected}:
        text_family = {"text", "html", "xml", "yaml"}
        if not ({expected, detected} <= text_family):
            raise ValueError(f"下载格式不符：期望 {expected}，实际 {detected}")
    return simple_file_stats(path, format_hint=detected)


def _record_failure(run_dir: Path, *, url: str, code: str, message: str) -> None:
    ledger = load_download_ledger(run_dir)
    ledger["failures"].append({"url": url, "code": code, "message": message, "failed_at": _now()})
    _save_ledger(run_dir, ledger)


def _fetch(
    target: Path,
    *,
    url: str,
    method: str,
    body: bytes | None,
    timeout_seconds: int,
    maximum_bytes: int,
) -> tuple[str, str]:
    headers = {"User-Agent": "AgentWorld-DataGen/4", "Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout_seconds) as response, target.open("wb") as output:
            effective_url = response.geturl()
            content_type = str(response.headers.get("Content-Type") or "")
            source = gzip.GzipFile(fileobj=response) if response.headers.get("Content-Encoding") == "gzip" else response
            total = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if maximum_bytes and total > maximum_bytes:
                    raise DownloadFailure("文件超过单文件大小限制", url=url, code="single_file_budget_exceeded")
                output.write(chunk)
            return effective_url, content_type
    except DownloadFailure:
        raise
    except HTTPError as error:
        raise DownloadFailure(f"HTTP {error.code}", url=url, code=f"http_{error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise DownloadFailure(str(error), url=url) from error


def _default_source_id(url: str) -> str:
    parsed = urlparse(url)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", (parsed.hostname or "source") + "_" + Path(parsed.path).stem)
    return value.strip("_")[:128] or "source"


def download_raw_file(
    run_dir: Path,
    *,
    url: str,
    output: str,
    expected_format: str = "any",
    timeout_seconds: int = 240,
    source_id: str | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    role: str = "business_records",
) -> dict[str, Any]:
    """Download once, install under Raw, and return a compact file summary."""

    run_dir = run_dir.resolve()
    source_id = source_id or _default_source_id(url)
    if not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("source_id 只能包含字母、数字、点、下划线和连字符")
    if role not in {"business_records", "task_domain_files", "semantic_evidence"}:
        raise ValueError("role 无效")
    method, body, body_sha256 = _body(method, json_body)
    key = _request_key(method, url, body_sha256)
    ledger = load_download_ledger(run_dir)
    for entry in ledger["downloads"]:
        path = run_dir / "workspace" / str(entry.get("path") or "")
        if entry.get("request_key") == key and entry.get("status") == "downloaded" and path.is_file():
            if entry.get("sha256") == file_sha256(path):
                return {**entry, "status": "already_downloaded", "reused_existing_file": True}

    target, logical = _raw_target(run_dir, output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = control_path(run_dir, "download_tmp")
    temporary_root.mkdir(parents=True, exist_ok=True)
    suffix = "".join(target.suffixes)
    temporary = temporary_root / f"{key}{suffix}"
    temporary.unlink(missing_ok=True)
    config = read_json(control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    policy = config.get("collection_policy", {})
    timeout_seconds = int(timeout_seconds or policy.get("download_timeout_seconds", 900) or 900)
    maximum_bytes = int(policy.get("max_single_file_bytes", 0) or 0)
    started = time.monotonic()
    try:
        effective_url, content_type = _fetch(
            temporary,
            url=url,
            method=method,
            body=body,
            timeout_seconds=timeout_seconds,
            maximum_bytes=maximum_bytes,
        )
        stats = _validate_file(temporary, expected_format)
        digest = file_sha256(temporary)
        for entry in ledger["downloads"]:
            existing = run_dir / "workspace" / str(entry.get("path") or "")
            if entry.get("status") == "downloaded" and entry.get("sha256") == digest and existing.is_file():
                if url not in entry["urls"]:
                    entry["urls"].append(url)
                temporary.unlink(missing_ok=True)
                _save_ledger(run_dir, ledger)
                receipt = {
                    "url": url,
                    "effective_url": effective_url,
                    "source_id": entry["source_id"],
                    "path": entry["path"],
                    "bytes": entry["bytes"],
                    "sha256": digest,
                    "retrieved_at": _now(),
                    "reused_existing_file": True,
                }
                _append_receipt(run_dir, receipt)
                return {**entry, "status": "duplicate_content", "reused_existing_file": True}
        if target.exists():
            raise DownloadFailure(f"目标路径已经存在：{logical}", url=url, code="output_exists")
        raw_root = run_dir / "workspace/raw"
        current_files = [path for path in raw_root.rglob("*") if path.is_file()] if raw_root.is_dir() else []
        if len(current_files) >= int(policy.get("max_raw_files", 200) or 200):
            raise DownloadFailure("Raw 文件数量达到限制", url=url, code="raw_file_budget_exceeded")
        current_bytes = sum(path.stat().st_size for path in current_files)
        if current_bytes + temporary.stat().st_size > int(policy.get("max_raw_bytes", 0) or 0):
            raise DownloadFailure("Raw 总大小达到限制", url=url, code="raw_budget_exceeded")
        os.replace(temporary, target)
        entry = {
            "request_key": key,
            "method": method,
            "request_body_sha256": body_sha256,
            "urls": [url],
            "effective_url": effective_url,
            "source_id": source_id,
            "path": logical,
            "sha256": digest,
            "bytes": stats["bytes"],
            "format": stats["format"],
            "record_count": stats.get("record_count"),
            "file_count": stats.get("file_count", 1),
            "role": role,
            "status": "downloaded",
            "downloaded_at": _now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        ledger["downloads"].append(entry)
        _save_ledger(run_dir, ledger)
        _append_receipt(run_dir, {
            "url": url,
            "effective_url": effective_url,
            "source_id": source_id,
            "path": logical,
            "bytes": stats["bytes"],
            "sha256": digest,
            "retrieved_at": entry["downloaded_at"],
            "reused_existing_file": False,
        })
        return {**entry, "reused_existing_file": False}
    except DownloadFailure as error:
        _record_failure(run_dir, url=url, code=error.code, message=str(error))
        raise
    except (
        ValueError,
        EOFError,
        json.JSONDecodeError,
        csv.Error,
        gzip.BadGzipFile,
        sqlite3.Error,
        tarfile.TarError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        failure = DownloadFailure(f"文件基础检查失败：{error}", url=url, code="invalid_file")
        _record_failure(run_dir, url=url, code=failure.code, message=str(failure))
        raise failure from error
    finally:
        temporary.unlink(missing_ok=True)


def discard_download(run_dir: Path, *, path: str, reason: str) -> dict[str, Any]:
    """Remove a downloaded candidate that the Agent found semantically useless."""

    run_dir = run_dir.resolve()
    if not reason.strip():
        raise ValueError("discard 必须说明原因")
    ledger = load_download_ledger(run_dir)
    entry = next((item for item in ledger["downloads"] if item.get("path") == path and item.get("status") == "downloaded"), None)
    if entry is None:
        raise RuntimeError(f"没有可丢弃的下载：{path}")
    target, logical = _raw_target(run_dir, path)
    if target.is_file() and entry.get("sha256") != file_sha256(target):
        raise RuntimeError("文件内容已经变化，不能按原下载记录丢弃")
    target.unlink(missing_ok=True)
    entry["status"] = "rejected"
    entry["rejection_reason"] = reason.strip()
    entry["rejected_at"] = _now()
    _save_ledger(run_dir, ledger)
    return {"status": "rejected", "path": logical, "reason": reason.strip()}


def download_raw_batch(
    run_dir: Path,
    *,
    items: list[dict[str, Any]],
    max_workers: int | None = None,
) -> dict[str, Any]:
    """Download a small batch sequentially; network time dominates here."""

    del max_workers
    if not isinstance(items, list) or not items:
        raise ValueError("下载清单至少包含一项")
    results: list[dict[str, Any]] = []
    for item in items:
        try:
            results.append(download_raw_file(
                run_dir,
                url=str(item["url"]),
                output=str(item["output"]),
                expected_format=str(item.get("format") or "any"),
                timeout_seconds=int(item.get("timeout_seconds") or 240),
                source_id=item.get("source_id"),
                method=str(item.get("method") or "GET"),
                json_body=item.get("json_body"),
                role=str(item.get("role") or "business_records"),
            ))
        except (DownloadFailure, KeyError, TypeError, ValueError) as error:
            results.append(error.to_dict() if isinstance(error, DownloadFailure) else {"status": "failed", "message": str(error)})
    failed = sum(item.get("status") == "failed" for item in results)
    return {"status": "complete" if not failed else "partial", "succeeded": len(results) - failed, "failed": failed, "results": results}


def cleanup_download_temporaries(run_dir: Path) -> list[str]:
    root = control_path(run_dir.resolve(), "download_tmp")
    removed: list[str] = []
    if root.is_dir():
        for path in root.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed.append(path.name)
    return removed


def download_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    """Check that every current Raw file has one matching download receipt."""

    run_dir = run_dir.resolve()
    receipts = _load_receipts(run_dir).get("downloads", [])
    by_path = {
        str(item.get("path")): item
        for item in receipts
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    issues: list[dict[str, str]] = []
    raw = run_dir / "workspace/raw"
    for physical in sorted(raw.rglob("*")) if raw.is_dir() else []:
        if not physical.is_file():
            continue
        logical = physical.relative_to(run_dir / "workspace").as_posix()
        receipt = by_path.get(logical)
        if receipt is None:
            issues.append({"code": "missing_download_receipt", "path": logical, "message": "Raw 文件没有下载收据"})
        elif receipt.get("sha256") != file_sha256(physical):
            issues.append({"code": "raw_hash_mismatch", "path": logical, "message": "Raw 文件与下载哈希不一致"})
    return issues


__all__ = [
    "DOWNLOAD_LEDGER",
    "DownloadFailure",
    "cleanup_download_temporaries",
    "discard_download",
    "download_raw_batch",
    "download_raw_file",
    "download_receipt_issues",
    "load_download_ledger",
    "simple_file_stats",
]
