"""Raw 下载及下载收据。"""

from __future__ import annotations

import fcntl
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ElementTree
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...common.constants import (
    CONTROL_RUN_CONFIG,
    CONTROL_DIRECTORY,
    CONTROL_DOWNLOAD_RECEIPTS,
    CONTROL_DOWNLOAD_ATTEMPTS,
)
from ...common.control_io import control_path, read_json, write_json
from ...common.workspace_files import file_sha256, workspace_files
from .save_source_plan import read_saved_source_plan

_control_path = control_path
_read_json = read_json
_write_json = write_json
_file_sha256 = file_sha256
_workspace_files = workspace_files


DOWNLOAD_FORMATS = {
    "json",
    "jsonl",
    "csv",
    "tsv",
    "parquet",
    "sqlite",
    "xml",
    "text",
    "html",
    "any",
}

_DOWNLOAD_TEMPORARY_NAME = re.compile(
    r"^\..+\.(?:part|headers)-\d+$"
)


def cleanup_download_temporaries(run_dir: Path) -> list[str]:
    """清理 Agent 进程组被终止后遗留的下载临时文件。"""

    raw_root = run_dir.resolve() / "workspace" / "raw"
    if not raw_root.is_dir():
        return []
    removed: list[str] = []
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or not _DOWNLOAD_TEMPORARY_NAME.fullmatch(path.name):
            continue
        removed.append(path.relative_to(raw_root).as_posix())
        path.unlink(missing_ok=True)
    return removed


_TERMINAL_DOWNLOAD_CODES = {
    "authentication_required",
    "authentication_page",
    "access_forbidden",
    "not_found",
    "single_file_budget_exceeded",
    "raw_budget_exceeded",
    "raw_file_budget_exceeded",
}


class DownloadFailure(RuntimeError):
    """可供 Agent 判断是否应重试或更换来源的结构化下载失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str,
        retryable: bool,
        http_status: int | None = None,
        source_id: str | None = None,
        effective_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.url = url
        self.retryable = retryable
        self.http_status = http_status
        self.source_id = source_id
        self.effective_url = effective_url

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "failed",
            "code": self.code,
            "url": self.url,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "source_id": self.source_id,
            "effective_url": self.effective_url,
            "message": str(self),
        }


def _locked_json_payload(path: Path, label: str, default: dict[str, Any]) -> dict[str, Any]:
    return _read_json(path, label) if path.is_file() else default


def _record_download_attempt(
    run_dir: Path,
    *,
    url: str,
    relative_path: str,
    result: dict[str, Any],
    source_id: str | None = None,
) -> None:
    """保留成功与失败尝试，供下一轮识别认证墙和不稳定来源。"""

    control_dir = run_dir / CONTROL_DIRECTORY
    control_dir.mkdir(parents=True, exist_ok=True)
    path = control_dir / CONTROL_DOWNLOAD_ATTEMPTS
    lock_path = control_dir / f"{CONTROL_DOWNLOAD_ATTEMPTS}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        payload = _locked_json_payload(
            path,
            "下载尝试历史",
            {"schema_version": "1.0", "attempts": []},
        )
        attempts = [item for item in payload.get("attempts", []) if isinstance(item, dict)]
        attempts.append(
            {
                "url": url,
                "path": relative_path,
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                **result,
                "source_id": result.get("source_id") or source_id,
            }
        )
        payload["attempts"] = attempts
        _write_json(path, payload)


def _registered_source(
    url: str,
    source_plan: dict[str, Any],
    source_id: str | None,
) -> dict[str, Any]:
    """把一次下载绑定到明确来源，避免同域 URL 丢失业务归属。"""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise RuntimeError(f"download 只接受完整的 http/https URL：{url}")
    candidates: list[dict[str, Any]] = []
    for source in source_plan.get("sources", []):
        if not isinstance(source, dict):
            continue
        registered = {
            str(value)
            for value in source.get("registered_urls", [])
            if isinstance(value, str)
        }
        if url in registered:
            candidates.append(source)
    if source_id is not None:
        selected = next(
            (
                source
                for source in candidates
                if source.get("source_id") == source_id
            ),
            None,
        )
        if selected is None:
            raise RuntimeError(
                f"精确 URL {url!r} 未登记到 source_plan 来源 {source_id!r}"
            )
        return selected
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        registered_urls = sorted(
            {
                str(value)
                for source in source_plan.get("sources", [])
                if isinstance(source, dict)
                for value in source.get("registered_urls", [])
                if isinstance(value, str)
            }
        )
        raise RuntimeError(
            f"精确 URL 未在 source_plan.registered_urls 中登记：{url}；"
            f"当前共登记 {len(registered_urls)} 个 URL"
        )
    raise RuntimeError(
        "该精确 URL 登记到多个来源，download 必须通过 --source-id 明确绑定："
        + ", ".join(
            sorted(str(source.get("source_id") or "unknown") for source in candidates)
        )
    )


def _validate_download_url(
    url: str,
    source_plan: dict[str, Any],
    source_id: str | None = None,
) -> str:
    source = _registered_source(url, source_plan, source_id)
    value = source.get("source_id")
    if not isinstance(value, str) or not value:
        raise RuntimeError("source_plan 来源缺少有效 source_id")
    return value


def _download_target(run_dir: Path, output: str) -> tuple[Path, str]:
    """把 workspace 相对路径解析为 raw 内的安全目标。"""

    relative = Path(output)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("download --output 必须是 workspace 下的安全相对路径")
    if relative.parts and relative.parts[0] == "workspace":
        relative = Path(*relative.parts[1:])
    if not relative.parts or relative.parts[0] != "raw":
        raise RuntimeError("download --output 必须位于 raw/，例如 raw/countries.json")
    workspace = (run_dir / "workspace").resolve()
    raw_root = (workspace / "raw").resolve()
    target = (workspace / relative).resolve()
    try:
        target.relative_to(raw_root)
    except ValueError as error:
        raise RuntimeError("download 目标不能离开 workspace/raw") from error
    return target, target.relative_to(workspace).as_posix()


def _validate_download_file(path: Path, expected_format: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"下载结果为空：{path}")
    if expected_format == "json":
        try:
            with path.open("r", encoding="utf-8") as stream:
                json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"下载结果不是完整 JSON：{path}: {error}") from error
    elif expected_format == "jsonl":
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                raise RuntimeError(f"下载结果没有 JSONL 记录：{path}")
            for line_number, line in enumerate(lines, start=1):
                json.loads(line)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"下载结果不是完整 JSONL（第 {locals().get('line_number', '?')} 行）：{path}: {error}"
            ) from error
    elif expected_format in {"csv", "tsv"}:
        delimiter = "\t" if expected_format == "tsv" else ","
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream, delimiter=delimiter)
                header = next(reader, None)
                if not header or not any(str(value).strip() for value in header):
                    raise RuntimeError(f"下载结果缺少 {expected_format.upper()} 表头：{path}")
        except (OSError, UnicodeError, csv.Error) as error:
            raise RuntimeError(f"下载结果不是完整 {expected_format.upper()}：{path}: {error}") from error
    elif expected_format == "sqlite":
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA schema_version").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise RuntimeError(f"下载结果不是可读 SQLite：{path}: {error}") from error
    elif expected_format == "parquet":
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise RuntimeError("校验 Parquet 需要可选依赖 pyarrow") from error
        try:
            parquet.read_metadata(path)
        except Exception as error:
            raise RuntimeError(f"下载结果不是可读 Parquet：{path}: {error}") from error
    elif expected_format == "xml":
        try:
            ElementTree.parse(path)
        except (OSError, ElementTree.ParseError) as error:
            raise RuntimeError(f"下载结果不是完整 XML：{path}: {error}") from error
    elif expected_format in {"text", "html"}:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError(f"下载结果不是 UTF-8 文本：{path}: {error}") from error


def _small_text_response(path: Path) -> str | None:
    """读取适合错误页判断的小响应；大正文不做关键词猜测。"""

    try:
        if path.stat().st_size > 64 * 1024:
            return None
        return path.read_bytes().decode("utf-8", errors="ignore").strip().lower()
    except OSError:
        return None


def _structured_error_text(sample: str) -> str | None:
    """只从明显的顶层错误对象取文本，避免扫描正常 JSON 业务正文。"""

    if not sample.startswith("{"):
        return None
    try:
        payload = json.loads(sample)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error_keys = {
        "code",
        "detail",
        "error",
        "error_description",
        "message",
        "status",
        "status_code",
        "title",
    }
    if not set(payload).intersection(error_keys):
        return None
    values = [
        str(payload[key])
        for key in error_keys
        if key in payload and isinstance(payload[key], (str, int))
    ]
    return " ".join(values).lower() if values else None


def _looks_like_auth_page(path: Path, content_type: str) -> bool:
    try:
        is_small = path.stat().st_size <= 64 * 1024
        sample = path.read_bytes()[:128 * 1024].decode(
            "utf-8", errors="ignore"
        ).strip().lower()
    except OSError:
        return False
    is_html = "html" in content_type.lower() or sample.startswith(
        ("<!doctype html", "<html")
    )
    structured_error = _structured_error_text(sample) if is_small else None
    strong_markers = (
        'type="password"',
        "type='password'",
        "authentication required",
        "please sign in to continue",
        "access denied",
        "verify you are human",
        "g-recaptcha",
        "h-captcha",
        "cf-chl-",
    )
    if is_html:
        return any(marker in sample for marker in strong_markers)
    if structured_error is not None:
        return any(marker in structured_error for marker in strong_markers) or any(
            marker in structured_error
            for marker in ("unauthorized", "unauthenticated", "login required")
        )
    return is_small and len(sample) <= 4096 and sample.startswith(
        ("authentication required", "unauthorized", "login required")
    )


def _looks_like_soft_not_found(path: Path, content_type: str) -> bool:
    """识别返回 HTTP 2xx 的短 404/不存在响应。"""

    sample = _small_text_response(path)
    if sample is None:
        return False
    structured_error = _structured_error_text(sample)
    if structured_error is not None and any(
        marker in structured_error
        for marker in ("not found", "does not exist", "doesn't exist", "no such file")
    ):
        return True
    is_html = "html" in content_type.lower() or sample.startswith(
        ("<!doctype html", "<html")
    )
    if is_html and any(
        re.search(pattern, sample)
        for pattern in (
            r"<title[^>]*>\s*(?:404|[^<]*not found)",
            r"<h1[^>]*>\s*(?:404|[^<]*not found)",
            r"(?:property|name)=[\"']og:title[\"'][^>]*content=[\"'][^\"']*404",
        )
    ):
        return True
    return len(sample) <= 4096 and sample.startswith(
        (
            "404 not found",
            "not found",
            "page not found",
            "the documentation page ",
            "the requested page does not exist",
            "the requested page doesn't exist",
        )
    ) and any(
        marker in sample
        for marker in ("not found", "does not exist", "doesn't exist", "no such file")
    )


def _previous_attempt_failure(
    run_dir: Path,
    *,
    url: str,
    source_id: str,
    run_config: dict[str, Any],
) -> DownloadFailure | None:
    """复用不可重试结论，并限制同一 URL 的失败网络尝试次数。"""

    attempts_path = _control_path(run_dir, CONTROL_DOWNLOAD_ATTEMPTS)
    if not attempts_path.is_file():
        return None
    payload = _read_json(attempts_path, "下载尝试历史")
    attempts = [
        item
        for item in payload.get("attempts", [])
        if isinstance(item, dict)
        and item.get("url") == url
        and item.get("status") == "failed"
    ]
    terminal = next(
        (
            item
            for item in reversed(attempts)
            if item.get("code") in _TERMINAL_DOWNLOAD_CODES
        ),
        None,
    )
    if terminal is not None:
        code = str(terminal.get("code"))
        return DownloadFailure(
            code,
            f"该精确 URL 已有不可重试的 {code} 证据，未再次访问网络：{url}",
            url=url,
            retryable=False,
            http_status=(
                int(terminal["http_status"])
                if isinstance(terminal.get("http_status"), int)
                else None
            ),
            source_id=source_id,
            effective_url=str(terminal.get("effective_url") or url),
        )
    policy = run_config.get("collection_policy", {})
    maximum = int(policy.get("max_source_attempts", 0) or 0)
    if maximum and len(attempts) >= maximum:
        return DownloadFailure(
            "source_attempt_limit_reached",
            f"该精确 URL 已失败 {len(attempts)} 次，达到尝试上限 {maximum}，未再次访问网络：{url}",
            url=url,
            retryable=False,
            source_id=source_id,
            effective_url=url,
        )
    return None


def _http_failure(
    *,
    url: str,
    status: int,
    detail: str,
    source_id: str,
    effective_url: str,
) -> DownloadFailure:
    if status == 401:
        return DownloadFailure(
            "authentication_required",
            f"来源要求登录或认证（HTTP 401）：{detail or url}",
            url=url,
            retryable=False,
            http_status=status,
            source_id=source_id,
            effective_url=effective_url,
        )
    if status == 403:
        return DownloadFailure(
            "access_forbidden",
            f"公开访问被拒绝（HTTP 403）：{detail or url}",
            url=url,
            retryable=False,
            http_status=status,
            source_id=source_id,
            effective_url=effective_url,
        )
    if status == 429:
        return DownloadFailure(
            "rate_limited",
            f"来源限流（HTTP 429）：{detail or url}",
            url=url,
            retryable=True,
            http_status=status,
            source_id=source_id,
            effective_url=effective_url,
        )
    if status == 404:
        return DownloadFailure(
            "not_found",
            f"来源不存在（HTTP 404）：{detail or url}",
            url=url,
            retryable=False,
            http_status=status,
            source_id=source_id,
            effective_url=effective_url,
        )
    return DownloadFailure(
        "source_http_error" if status >= 500 else "http_error",
        f"来源返回 HTTP {status}：{detail or url}",
        url=url,
        retryable=status >= 500,
        http_status=status,
        source_id=source_id,
        effective_url=effective_url,
    )


def _record_download_receipt(
    run_dir: Path,
    *,
    url: str,
    relative_path: str,
    path: Path,
    reused: bool,
    source_id: str,
    effective_url: str,
) -> None:
    """记录程序实际完成的 URL -> raw 文件映射，供后续来源审计。"""

    control_dir = run_dir / CONTROL_DIRECTORY
    control_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = control_dir / CONTROL_DOWNLOAD_RECEIPTS
    lock_path = control_dir / f"{CONTROL_DOWNLOAD_RECEIPTS}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        if receipt_path.is_file():
            payload = _read_json(receipt_path, "下载收据")
        else:
            payload = {"schema_version": "1.0", "downloads": []}
        downloads = [
            item
            for item in payload.get("downloads", [])
            if isinstance(item, dict)
            and not (item.get("url") == url and item.get("path") == relative_path)
        ]
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        downloads.append(
            {
                "url": url,
                "effective_url": effective_url,
                "source_id": source_id,
                "path": relative_path,
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "reused_existing_file": reused,
            }
        )
        payload["downloads"] = downloads
        _write_json(receipt_path, payload)


def _download_receipt(
    run_dir: Path,
    *,
    url: str,
    relative_path: str,
    source_id: str,
) -> dict[str, Any] | None:
    receipt_path = _control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if not receipt_path.is_file():
        return None
    payload = _read_json(receipt_path, "下载收据")
    return next(
        (
            item
            for item in payload.get("downloads", [])
            if isinstance(item, dict)
            and item.get("url") == url
            and item.get("path") == relative_path
            and item.get("source_id", source_id) == source_id
        ),
        None,
    )


def _download_receipt_for_url(
    run_dir: Path,
    *,
    url: str,
    source_id: str,
) -> dict[str, Any] | None:
    """查找同一来源中已安装的精确 URL，避免换文件名后重复下载。"""

    receipt_path = _control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if not receipt_path.is_file():
        return None
    payload = _read_json(receipt_path, "下载收据")
    return next(
        (
            item
            for item in reversed(payload.get("downloads", []))
            if isinstance(item, dict)
            and item.get("url") == url
            and item.get("source_id", source_id) == source_id
            and isinstance(item.get("path"), str)
        ),
        None,
    )


def _download_receipt_for_content(
    run_dir: Path,
    *,
    sha256: str,
    source_id: str,
) -> dict[str, Any] | None:
    """查找同一来源中内容完全相同的 Raw，避免安装物理副本。"""

    receipt_path = _control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if not receipt_path.is_file():
        return None
    payload = _read_json(receipt_path, "下载收据")
    return next(
        (
            item
            for item in payload.get("downloads", [])
            if isinstance(item, dict)
            and item.get("sha256") == sha256
            and item.get("source_id", source_id) == source_id
            and isinstance(item.get("path"), str)
        ),
        None,
    )


def _has_download_receipt(
    run_dir: Path,
    *,
    url: str,
    relative_path: str,
    source_id: str | None = None,
) -> bool:
    if source_id is None:
        receipt_path = _control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
        if not receipt_path.is_file():
            return False
        payload = _read_json(receipt_path, "下载收据")
        return any(
            isinstance(item, dict)
            and item.get("url") == url
            and item.get("path") == relative_path
            for item in payload.get("downloads", [])
        )
    return _download_receipt(
        run_dir,
        url=url,
        relative_path=relative_path,
        source_id=source_id,
    ) is not None


def download_raw_file(
    run_dir: Path,
    *,
    url: str,
    output: str,
    expected_format: str,
    timeout_seconds: int,
    source_id: str | None = None,
) -> dict[str, Any]:
    """带跨进程锁、完整性检查和原子替换的通用公开数据下载器。"""

    run_dir = run_dir.resolve()
    config = _read_json(_control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    plan = read_saved_source_plan(run_dir)
    resolved_source_id = _validate_download_url(url, plan, source_id)
    target, relative_path = _download_target(run_dir, output)
    target.parent.mkdir(parents=True, exist_ok=True)

    lock_root = _control_path(run_dir, "download_locks")
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_key = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{lock_key}.lock"
    url_lock_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    url_lock_path = lock_root / f"url-{url_lock_key}.lock"
    with ExitStack() as lock_stack:
        # 同时按目标路径和精确 URL 串行化。固定排序避免两个并发调用以
        # 不同次序持锁；URL 锁保证终态失败在不同输出路径之间也只请求一次。
        for current_lock_path in sorted((lock_path, url_lock_path)):
            lock_stream = lock_stack.enter_context(
                current_lock_path.open("a+", encoding="utf-8")
            )
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        if target.is_file():
            receipt = _download_receipt(
                run_dir,
                url=url,
                relative_path=relative_path,
                source_id=resolved_source_id,
            )
            if receipt is None:
                raise RuntimeError(
                    f"目标已存在但没有相同 URL 的下载收据，拒绝认领未知来源文件：{relative_path}"
                )
            actual_sha256 = _file_sha256(target)
            if receipt.get("sha256") != actual_sha256:
                raise RuntimeError(
                    f"目标文件与原下载收据 SHA-256 不一致，拒绝复用被改写的 raw：{relative_path}"
                )
            _validate_download_file(target, expected_format)
            effective_url = str(receipt.get("effective_url") or url)
            _record_download_receipt(
                run_dir,
                url=url,
                relative_path=relative_path,
                path=target,
                reused=True,
                source_id=resolved_source_id,
                effective_url=effective_url,
            )
            result = {
                "status": "reused",
                "url": url,
                "effective_url": effective_url,
                "source_id": resolved_source_id,
                "path": relative_path,
                "bytes": target.stat().st_size,
            }
            _record_download_attempt(
                run_dir,
                url=url,
                relative_path=relative_path,
                result=result,
                source_id=resolved_source_id,
            )
            return result

        existing_receipt = _download_receipt_for_url(
            run_dir,
            url=url,
            source_id=resolved_source_id,
        )
        if existing_receipt is not None:
            existing_relative = str(existing_receipt["path"])
            existing_target, _ = _download_target(run_dir, existing_relative)
            if existing_target.is_file():
                actual_sha256 = _file_sha256(existing_target)
                if existing_receipt.get("sha256") != actual_sha256:
                    raise RuntimeError(
                        "同一 URL 的已下载 Raw 与收据 SHA-256 不一致，拒绝复用："
                        f"{existing_relative}"
                    )
                _validate_download_file(existing_target, expected_format)
                effective_url = str(existing_receipt.get("effective_url") or url)
                result = {
                    "status": "reused",
                    "url": url,
                    "effective_url": effective_url,
                    "source_id": resolved_source_id,
                    "path": existing_relative,
                    "requested_path": relative_path,
                    "bytes": existing_target.stat().st_size,
                }
                _record_download_attempt(
                    run_dir,
                    url=url,
                    relative_path=existing_relative,
                    result=result,
                    source_id=resolved_source_id,
                )
                return result

        previous_failure = _previous_attempt_failure(
            run_dir,
            url=url,
            source_id=resolved_source_id,
            run_config=config,
        )
        if previous_failure is not None:
            raise previous_failure

        temporary = target.with_name(f".{target.name}.part-{os.getpid()}")
        header_path = target.with_name(f".{target.name}.headers-{os.getpid()}")
        temporary.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)
        curl = shutil.which("curl")
        if not curl:
            failure = DownloadFailure(
                "download_tool_missing",
                "系统中找不到 curl，无法执行 download",
                url=url,
                retryable=False,
                source_id=resolved_source_id,
            )
            _record_download_attempt(
                run_dir,
                url=url,
                relative_path=relative_path,
                result=failure.to_dict(),
                source_id=resolved_source_id,
            )
            raise failure
        command = [
            curl,
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "1",
            "--retry-connrefused",
            "--connect-timeout",
            "20",
            "--max-time",
            str(timeout_seconds),
            "--dump-header",
            str(header_path),
            "--output",
            str(temporary),
            "--write-out",
            "%{http_code}\n%{content_type}\n%{url_effective}",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_seconds + 30,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-1000:]
                raise DownloadFailure(
                    "network_error",
                    f"下载失败（curl exit {completed.returncode}）：{detail or url}",
                    url=url,
                    retryable=True,
                    source_id=resolved_source_id,
                )
            output_lines = completed.stdout.splitlines()
            try:
                http_status = int(output_lines[0]) if output_lines else 0
            except ValueError:
                http_status = 0
            content_type = output_lines[1] if len(output_lines) > 1 else ""
            effective_url = output_lines[2] if len(output_lines) > 2 else url
            if http_status < 200 or http_status >= 300:
                detail = ""
                if temporary.is_file():
                    detail = temporary.read_bytes()[:1000].decode(
                        "utf-8", errors="replace"
                    ).strip()
                raise _http_failure(
                    url=url,
                    status=http_status,
                    detail=detail[-500:],
                    source_id=resolved_source_id,
                    effective_url=effective_url,
                )
            if _looks_like_auth_page(temporary, content_type):
                raise DownloadFailure(
                    "authentication_page",
                    f"来源返回登录、认证或人机验证页面：{effective_url}",
                    url=url,
                    retryable=False,
                    http_status=http_status,
                    source_id=resolved_source_id,
                    effective_url=effective_url,
                )
            if _looks_like_soft_not_found(temporary, content_type):
                raise DownloadFailure(
                    "not_found",
                    f"来源返回 HTTP {http_status}，但正文明确表示资源不存在：{effective_url}",
                    url=url,
                    retryable=False,
                    http_status=http_status,
                    source_id=resolved_source_id,
                    effective_url=effective_url,
                )
            try:
                _validate_download_file(temporary, expected_format)
            except RuntimeError as error:
                raise DownloadFailure(
                    "invalid_content",
                    str(error),
                    url=url,
                    retryable=False,
                    http_status=http_status,
                    source_id=resolved_source_id,
                    effective_url=effective_url,
                ) from error
            size = temporary.stat().st_size
            content_sha256 = _file_sha256(temporary)
            acquisition = config.get("collection_policy", {})
            max_single = int(acquisition.get("max_single_file_bytes", 0) or 0)
            if max_single and size > max_single:
                raise DownloadFailure(
                    "single_file_budget_exceeded",
                    f"单文件 {size} bytes 超过上限 {max_single} bytes：{relative_path}",
                    url=url,
                    retryable=False,
                    http_status=http_status,
                    source_id=resolved_source_id,
                    effective_url=effective_url,
                )
            install_lock = _control_path(run_dir, "raw_install.lock")
            installed_target = target
            installed_relative_path = relative_path
            reused_content = False
            with install_lock.open("a+", encoding="utf-8") as install_stream:
                fcntl.flock(install_stream.fileno(), fcntl.LOCK_EX)
                content_receipt = _download_receipt_for_content(
                    run_dir,
                    sha256=content_sha256,
                    source_id=resolved_source_id,
                )
                if content_receipt is not None:
                    installed_relative_path = str(content_receipt["path"])
                    installed_target, _ = _download_target(
                        run_dir,
                        installed_relative_path,
                    )
                    if not installed_target.is_file():
                        raise RuntimeError(
                            "相同内容的下载收据引用了不存在的 Raw："
                            f"{installed_relative_path}"
                        )
                    if _file_sha256(installed_target) != content_sha256:
                        raise RuntimeError(
                            "相同内容的下载收据与现有 Raw SHA-256 不一致："
                            f"{installed_relative_path}"
                        )
                    reused_content = True
                    _record_download_receipt(
                        run_dir,
                        url=url,
                        relative_path=installed_relative_path,
                        path=installed_target,
                        reused=True,
                        source_id=resolved_source_id,
                        effective_url=effective_url,
                    )
                else:
                    raw_root = run_dir / "workspace" / "raw"
                    installed = [
                        path
                        for path in raw_root.rglob("*")
                        if path.is_file() and not path.name.startswith(".")
                    ]
                    max_raw = int(acquisition.get("max_raw_bytes", 0) or 0)
                    existing_raw_bytes = sum(path.stat().st_size for path in installed)
                    if max_raw and existing_raw_bytes + size > max_raw:
                        raise DownloadFailure(
                            "raw_budget_exceeded",
                            f"raw 总量将超过上限 {max_raw} bytes，拒绝写入 {relative_path}",
                            url=url,
                            retryable=False,
                            http_status=http_status,
                            source_id=resolved_source_id,
                            effective_url=effective_url,
                        )
                    max_files = int(acquisition.get("max_raw_files", 0) or 0)
                    if max_files and len(installed) + 1 > max_files:
                        raise DownloadFailure(
                            "raw_file_budget_exceeded",
                            f"raw 文件数将超过上限 {max_files}，拒绝写入 {relative_path}",
                            url=url,
                            retryable=False,
                            http_status=http_status,
                            source_id=resolved_source_id,
                            effective_url=effective_url,
                        )
                    os.replace(temporary, target)
                    _record_download_receipt(
                        run_dir,
                        url=url,
                        relative_path=relative_path,
                        path=target,
                        reused=False,
                        source_id=resolved_source_id,
                        effective_url=effective_url,
                    )
        except DownloadFailure as failure:
            _record_download_attempt(
                run_dir,
                url=url,
                relative_path=relative_path,
                result=failure.to_dict(),
                source_id=resolved_source_id,
            )
            raise
        except subprocess.TimeoutExpired as error:
            failure = DownloadFailure(
                "download_timeout",
                f"下载在 {timeout_seconds} 秒后超时：{url}",
                url=url,
                retryable=True,
                source_id=resolved_source_id,
            )
            _record_download_attempt(
                run_dir,
                url=url,
                relative_path=relative_path,
                result=failure.to_dict(),
                source_id=resolved_source_id,
            )
            raise failure from error
        finally:
            temporary.unlink(missing_ok=True)
            header_path.unlink(missing_ok=True)

        result = {
            "status": "reused" if reused_content else "downloaded",
            "url": url,
            "effective_url": effective_url,
            "source_id": resolved_source_id,
            "path": installed_relative_path,
            "bytes": installed_target.stat().st_size,
        }
        if reused_content:
            result["requested_path"] = relative_path
        _record_download_attempt(
            run_dir,
            url=url,
            relative_path=installed_relative_path,
            result=result,
            source_id=resolved_source_id,
        )
        return result


def download_raw_batch(
    run_dir: Path,
    *,
    items: list[dict[str, Any]],
    max_workers: int | None = None,
) -> dict[str, Any]:
    """并发下载互不相同的 Raw 目标，单项失败不会取消其它公开来源。"""

    run_dir = run_dir.resolve()
    config = _read_json(_control_path(run_dir, CONTROL_RUN_CONFIG), "运行配置")
    policy = config.get("collection_policy", {})
    configured_workers = int(policy.get("max_parallel_downloads", 1) or 1)
    workers = min(max_workers or configured_workers, configured_workers, max(1, len(items)))
    if workers <= 0:
        raise RuntimeError("download-batch --max-workers 必须大于 0")
    if not items:
        raise RuntimeError("download-batch manifest 至少需要一个下载项")
    if len(items) > 64:
        raise RuntimeError("单个 download-batch 最多接受 64 个下载项")

    outputs: set[str] = set()
    urls: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RuntimeError(f"download-batch items[{index}] 必须是对象")
        url = item.get("url")
        output = item.get("output")
        expected_format = item.get("format", "any")
        timeout_seconds = item.get("timeout_seconds", 240)
        source_id = item.get("source_id")
        if not isinstance(url, str) or not url:
            raise RuntimeError(f"download-batch items[{index}].url 不能为空")
        if not isinstance(output, str) or not output:
            raise RuntimeError(f"download-batch items[{index}].output 不能为空")
        if output in outputs:
            raise RuntimeError(f"download-batch 目标路径重复：{output}")
        if url in urls:
            raise RuntimeError(
                f"download-batch URL 重复：{url}；同一响应只下载一次并复用对应 Raw"
            )
        if expected_format not in DOWNLOAD_FORMATS:
            raise RuntimeError(f"download-batch items[{index}].format 不支持：{expected_format}")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise RuntimeError(f"download-batch items[{index}].timeout_seconds 必须是正整数")
        if source_id is not None and (not isinstance(source_id, str) or not source_id):
            raise RuntimeError(f"download-batch items[{index}].source_id 必须是非空字符串")
        outputs.add(output)
        urls.add(url)
        normalized.append(
            {
                "url": url,
                "output": output,
                "expected_format": expected_format,
                "timeout_seconds": timeout_seconds,
                "source_id": source_id,
            }
        )

    results: list[dict[str, Any] | None] = [None] * len(normalized)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="datagen-download") as executor:
        futures = {
            executor.submit(download_raw_file, run_dir, **item): index
            for index, item in enumerate(normalized)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except DownloadFailure as error:
                results[index] = error.to_dict()
            except Exception as error:
                results[index] = {
                    "status": "failed",
                    "code": "unexpected_download_error",
                    "url": normalized[index]["url"],
                    "retryable": False,
                    "http_status": None,
                    "message": str(error),
                }
    completed_results = [item for item in results if isinstance(item, dict)]
    succeeded = [item for item in completed_results if item.get("status") in {"downloaded", "reused"}]
    failed = [item for item in completed_results if item.get("status") == "failed"]
    return {
        "status": "completed" if not failed else "completed_with_failures",
        "workers": workers,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "results": completed_results,
    }

def download_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    """拒绝绕过 download 直接放入 workspace/raw 的文件。"""

    path = control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    receipts: dict[str, dict[str, Any]] = {}
    if path.is_file():
        payload = read_json(path, "下载收据")
        receipts = {
            str(item.get("path")): item
            for item in payload.get("downloads", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
    workspace = run_dir / "workspace"
    issues: list[dict[str, str]] = []
    raw_files = workspace_files(run_dir)["raw"]
    for relative in raw_files:
        receipt = receipts.get(relative)
        if receipt is None:
            issues.append(
                {
                    "code": "raw_file_not_downloaded",
                    "path": relative,
                    "message": f"raw 文件必须通过 datagenctl download 下载：{relative}",
                }
            )
        elif receipt.get("sha256") != file_sha256(workspace / relative):
            issues.append(
                {
                    "code": "downloaded_raw_modified",
                    "path": relative,
                    "message": f"download 后 raw 文件被直接修改：{relative}",
                }
            )
    return issues


_download_receipt_issues = download_receipt_issues

__all__ = [
    "DOWNLOAD_FORMATS",
    "DownloadFailure",
    "cleanup_download_temporaries",
    "download_receipt_issues",
    "download_raw_batch",
    "download_raw_file",
    "_download_receipt_issues",
    "_has_download_receipt",
    "_record_download_receipt",
    "_validate_download_file",
    "_validate_download_url",
]
