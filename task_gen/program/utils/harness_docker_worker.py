"""Execute one Harness sandbox call inside a ToolGen Docker runtime."""

from __future__ import annotations

import contextlib
from copy import deepcopy
import json
import io
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


def _scope_root(state_root: Path, environment: dict, scope_id: str) -> Path:
    if (
        not isinstance(scope_id, str)
        or not scope_id
        or "/" in scope_id
        or "\\" in scope_id
    ):
        raise ValueError("invalid scope")
    declared = {
        str(item.get("scope_id"))
        for item in environment.get("filesystem_scopes", [])
    }
    if scope_id not in declared:
        raise KeyError("unknown scope")
    base = (state_root / "filesystem_scopes").resolve()
    path = (base / scope_id).resolve()
    if path.parent != base:
        raise ValueError("scope escape")
    return path


def _direct_call(request: dict, state_root: Path) -> dict:
    """Execute a delivered tool directly inside its Docker container.

    The outer TaskGen runtime still owns the transaction and output-schema
    checks. Docker supplies the process and dependency boundary, so this worker
    does not create a second bubblewrap namespace inside the container.
    """
    # Keep this compatibility worker standard-library-only. Several legacy
    # domain images contain their scientific stack but not the host Harness'
    # jsonschema dependency. Importing the full Harness Context here would
    # fail before the delivered tool is even entered.
    from task_gen.program.utils.profile_tool_worker import RecordStore

    environment = request.get("environment") or {}
    context = SimpleNamespace(
        state_root=state_root,
        workspace_root=state_root,
        records_path=state_root / "records.sqlite",
        filesystem_scopes_root=state_root / "filesystem_scopes",
        environment=environment,
        records=RecordStore(state_root / "records.sqlite", environment),
        scope_root=lambda scope_id: _scope_root(state_root, environment, scope_id),
    )
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
        outcome = _direct_call(request, state_root)
    else:
        from harness.sandbox import call_tool

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
