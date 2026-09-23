"""Classify execution-layer failures recorded by the environment MCP server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CODES = {"backend_unavailable", "runtime_error"}
_MARKERS = (
    "importerror:",
    "modulenotfounderror:",
    "oserror:",
    "memoryerror",
    "failed to map segment",
    "cannot allocate memory",
    "resource temporarily unavailable",
    "pthread_create",
    "thread creation failed",
    "no space left on device",
    "启动工具沙箱失败",
    "工具沙箱异常退出",
    "工具沙箱输出超过",
)


def environment_errors(trace: Path) -> list[dict[str, Any]]:
    if not trace.is_file():
        return []
    errors = []
    for line_number, line in enumerate(trace.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if _is_timeout(record):
            continue
        result = record.get("result")
        error = result.get("error") if isinstance(result, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        runtime_error = record.get("runtime_error")
        is_environment_error = code in _CODES or bool(runtime_error)
        message = (
            error.get("message") if isinstance(error, dict) else None
        ) or (runtime_error if isinstance(runtime_error, str) else None)
        if not is_environment_error and result is None and isinstance(record.get("error"), str):
            candidate = record["error"]
            if any(marker in candidate.lower() for marker in _MARKERS):
                message = candidate
                is_environment_error = True
        if not is_environment_error:
            continue
        errors.append({
            "line": line_number,
            "tool": record.get("tool"),
            "code": code or "runtime_error",
            "message": str(message or record.get("error") or "environment runtime failure")[:1000],
        })
    return errors


def _is_timeout(record: dict[str, Any]) -> bool:
    if record.get("failure_kind") == "timeout":
        return True
    message = record.get("error")
    return isinstance(message, str) and (
        "tool invocation exceeded" in message.lower() or "工具调用超过" in message
    )


def tool_timeouts(trace: Path) -> list[dict[str, Any]]:
    if not trace.is_file():
        return []
    timeouts = []
    for line_number, line in enumerate(trace.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not _is_timeout(record):
            continue
        timeouts.append({
            "line": line_number,
            "tool": record.get("tool"),
            "code": "timeout",
            "message": str(record.get("error") or "tool execution timed out")[:1000],
            "retryable": True,
        })
    return timeouts
