"""PreToolUse boundary for Kimi's native Read, Grep, and Glob tools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.tool_budget import claim_tool_call


_FILE_TOOLS = {"Read", "Grep", "Glob"}


def build_kimi_file_hook_command(
    workspace: Path,
    home: Path,
    audit_log: Path,
    *,
    python: str | Path = sys.executable,
    budget_counter: Path | None = None,
    budget_stop_marker: Path | None = None,
    max_tool_calls: int | None = None,
) -> str:
    """Return the shell command used by Kimi's external hook runner."""
    budget_values = (budget_counter, budget_stop_marker, max_tool_calls)
    if any(value is not None for value in budget_values) and not all(
        value is not None for value in budget_values
    ):
        raise ValueError("native file tool budget requires counter, marker, and limit")
    command = [
        str(Path(python).expanduser().resolve()),
        str(Path(__file__).resolve()),
        "--workspace", str(workspace.expanduser().resolve()),
        "--home", str(home.expanduser().resolve()),
        "--audit-log", str(audit_log.expanduser().resolve()),
    ]
    if budget_counter is not None:
        command.extend([
            "--budget-counter", str(budget_counter.expanduser().resolve()),
            "--budget-stop-marker", str(budget_stop_marker.expanduser().resolve()),
            "--max-tool-calls", str(max_tool_calls),
        ])
    return shlex.join(command)


class KimiFileAccessPolicy:
    """Allow native file tools only within one model workspace/session."""

    def __init__(self, workspace: Path, home: Path, audit_log: Path) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.home = home.expanduser().resolve()
        self.audit_log = audit_log.expanduser().resolve()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or path.is_relative_to(root)

    def _session_path_kind(self, path: Path, session_id: str) -> str | None:
        if not session_id or not self._is_within(path, self.home):
            return None
        parts = path.relative_to(self.home).parts
        for index, part in enumerate(parts):
            if part != "sessions" or len(parts) <= index + 5:
                continue
            if (
                parts[index + 2] != session_id
                or parts[index + 3 : index + 5] != ("agents", "main")
            ):
                continue
            suffix = parts[index + 5 :]
            if suffix and suffix[0] == "tool-results":
                return "session_tool_results"
            if suffix == ("wire.jsonl",):
                return "session_wire"
        return None

    def _resolve_path(self, value: str) -> Path:
        if value == "~" or value.startswith(("~/", "~\\")):
            # Kimi expands this against its host home, which is deliberately not
            # a model-visible root. Do not reinterpret it as a workspace name.
            raise ValueError("home-relative paths are not allowed")
        raw = Path(value)
        # Kimi resolves ordinary relative paths against the session cwd. Use the
        # configured workspace instead of trusting a cwd value from hook input.
        candidate = raw if raw.is_absolute() else self.workspace / raw
        return candidate.resolve(strict=False)

    def check(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._decision(False, "invalid_hook_payload", payload={})
        tool = payload.get("tool_name")
        tool_input = payload.get("tool_input")
        session_id = payload.get("session_id")
        base = {
            "session_id": session_id if isinstance(session_id, str) else None,
            "tool_call_id": payload.get("tool_call_id"),
            "tool": tool,
            "requested_path": tool_input.get("path") if isinstance(tool_input, dict) else None,
        }
        if tool not in _FILE_TOOLS:
            return self._decision(False, "unsupported_file_tool", **base)
        if not isinstance(tool_input, dict):
            return self._decision(False, "invalid_tool_input", **base)

        value = tool_input.get("path")
        if tool in {"Grep", "Glob"} and value is None:
            value = "."
        if not isinstance(value, str) or not value:
            return self._decision(False, "invalid_path", **base)
        if value.startswith("kimi-file://"):
            if tool == "Read":
                return self._decision(
                    True, "current_session_attachment", normalized_path=value, **base
                )
            return self._decision(False, "attachment_uri_requires_read", **base)

        try:
            target = self._resolve_path(value)
        except (OSError, RuntimeError, ValueError) as error:
            return self._decision(False, f"path_resolution_failed: {error}", **base)
        normalized = str(target)
        if self._is_within(target, self.workspace):
            return self._decision(
                True, "model_workspace", normalized_path=normalized, **base
            )
        kind = self._session_path_kind(
            target, session_id if isinstance(session_id, str) else ""
        )
        if kind == "session_tool_results":
            return self._decision(True, kind, normalized_path=normalized, **base)
        if kind == "session_wire" and tool in {"Read", "Grep"}:
            return self._decision(True, kind, normalized_path=normalized, **base)
        return self._decision(
            False, "path_outside_session_allowlist", normalized_path=normalized, **base
        )

    @staticmethod
    def _decision(allowed: bool, reason: str, **fields: Any) -> dict[str, Any]:
        return {"allowed": allowed, "reason": reason, **fields}

    def record(self, decision: dict[str, Any]) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(decision, ensure_ascii=False, allow_nan=False) + "\n"
        descriptor = os.open(
            self.audit_log,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    parser.add_argument("--budget-counter", type=Path)
    parser.add_argument("--budget-stop-marker", type=Path)
    parser.add_argument("--max-tool-calls", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = _arguments(argv)
    budget_values = (
        options.budget_counter,
        options.budget_stop_marker,
        options.max_tool_calls,
    )
    if any(value is not None for value in budget_values) and not all(
        value is not None for value in budget_values
    ):
        raise SystemExit("native file tool budget requires counter, marker, and limit")
    policy = KimiFileAccessPolicy(options.workspace, options.home, options.audit_log)
    try:
        payload = json.load(sys.stdin)
        decision = policy.check(payload)
        if decision["allowed"] and options.budget_counter is not None:
            budget = claim_tool_call(
                options.budget_counter,
                options.budget_stop_marker,
                options.max_tool_calls,
                str(decision.get("tool") or "native_file_tool"),
            )
            decision["tool_budget_count"] = budget["observed_count"]
            decision["tool_budget_limit"] = budget["limit"]
            if not budget["allowed"]:
                decision["allowed"] = False
                decision["reason"] = budget["reason"]
    except BaseException as error:
        decision = {
            "allowed": False,
            "reason": f"file_policy_failure: {type(error).__name__}: {error}",
        }
    try:
        policy.record(decision)
    except BaseException as error:
        print(f"Native file access denied: audit failure: {error}", file=sys.stderr)
        return 2
    if decision["allowed"]:
        return 0
    print(f"Native file access denied: {decision['reason']}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["KimiFileAccessPolicy", "build_kimi_file_hook_command", "main"]
