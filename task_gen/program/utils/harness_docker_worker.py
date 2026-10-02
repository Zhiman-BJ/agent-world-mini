"""Execute one Harness sandbox call inside a ToolGen Docker runtime."""

from __future__ import annotations

import contextlib
from copy import deepcopy
import json
import io
from pathlib import Path
import sys
import traceback

from harness.sandbox import call_tool


def _direct_compat_call(request: dict, state_root: Path) -> dict:
    """Temporary Docker compatibility path when the image lacks bwrap.

    The outer TaskGen runtime still owns the transaction and output-schema
    checks. This inner fallback only replaces the unavailable second sandbox
    and is enabled by an explicit request flag.
    """
    from task_gen.tool_graph.state_runtime import Context

    context = Context(state_root, request.get("environment") or {})
    context.workspace_root = state_root
    context.records_path = state_root / "records.sqlite"
    context.filesystem_scopes_root = state_root / "filesystem_scopes"
    if request.get("software_root"):
        context.software_root = Path(request["software_root"])
    namespace: dict[str, object] = {"json": json, "sqlite3": __import__("sqlite3")}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            exec(compile(request["code"], "<docker-compat-tool>", "exec"), namespace, namespace)
            run = namespace.get("run")
            if not callable(run):
                raise ValueError("internal.code 没有定义可调用的 run")
            result = run(deepcopy(request["arguments"]), context)
        return {"kind": None, "result": result, "error": None}
    except BaseException as error:
        return {
            "kind": "exception",
            "result": None,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(limit=12),
        }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: harness_docker_worker REQUEST RESPONSE STATE_ROOT")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    state_root = Path(sys.argv[3])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("compat_direct") is True:
        outcome = _direct_compat_call(request, state_root)
    else:
        outcome = call_tool(
            request["code"],
            request["arguments"],
            state_root,
            int(request["timeout"]),
            int(request["memory_limit"]),
            int(request["write_limit"]),
            request.get("environment"),
            software_root=(
                Path(request["software_root"])
                if request.get("software_root")
                else None
            ),
        )
    response_path.write_text(
        json.dumps(outcome, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
