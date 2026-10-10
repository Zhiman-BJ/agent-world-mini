"""Atomic task-local tool-call budget shared by MCP and native file tools."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any


def claim_tool_call(
    counter_path: Path,
    marker_path: Path,
    limit: int,
    tool: str,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("tool-call limit must be positive")
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(counter_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 4096)
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
            count = int(value.get("count", 0)) if isinstance(value, dict) else 0
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            count = 0
        if count >= limit:
            result = {
                "allowed": False,
                "reason": "total_tool_call_limit_exceeded",
                "limit": limit,
                "observed_count": count,
                "attempted_tool": tool,
            }
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return result
        count += 1
        payload = json.dumps({"count": count}, ensure_ascii=False).encode("utf-8")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, payload)
        os.fsync(descriptor)
        return {
            "allowed": True,
            "limit": limit,
            "observed_count": count,
            "attempted_tool": tool,
        }
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


__all__ = ["claim_tool_call"]
