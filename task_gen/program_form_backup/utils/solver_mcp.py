"""为一次独立任务求解暴露环境工具的最小 stdio MCP server。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from task_gen.program_form.utils.environment import load_frozen_package  # noqa: E402
from task_gen.program_form.utils.io import write_json  # noqa: E402
from task_gen.program_form.utils.tool_runtime import (  # noqa: E402
    CompleteEnvironmentRuntime,
    compact_state_snapshot,
)


_PROTOCOL_VERSION = "2025-06-18"


def _reply(stdout: TextIO, request_id: Any, result: dict[str, Any]) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    stdout.flush()


def serve(config_path: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    package = load_frozen_package(Path(config["step1_path"]))
    trace_path = Path(config["trace_path"])
    state_path = Path(config["state_path"])
    max_tool_calls = int(config.get("max_tool_calls", 100))
    public_tools = {tool["name"]: tool for tool in package.public_environment()["tools"]}
    calls = 0
    with CompleteEnvironmentRuntime(package) as runtime:
        write_json(state_path, compact_state_snapshot(runtime.snapshot()))
        for line in stdin:
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                request_id = request.get("id")
                method = request.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "agent-world-program", "version": "1.0"},
                    }
                elif method == "tools/list":
                    result = {"tools": list(public_tools.values())}
                elif method == "tools/call":
                    if calls >= max_tool_calls:
                        raise ValueError("工具调用次数已达到上限")
                    params = request.get("params")
                    if not isinstance(params, dict):
                        raise ValueError("tools/call 缺少 params")
                    name = params.get("name")
                    arguments = params.get("arguments", {})
                    if not isinstance(name, str) or name not in public_tools:
                        raise ValueError(f"未知工具：{name}")
                    if not isinstance(arguments, dict):
                        raise ValueError("工具 arguments 必须是 object")
                    calls += 1
                    tool_result = runtime.call(name, arguments)
                    record = {
                        "tool": name,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                    trace_path.parent.mkdir(parents=True, exist_ok=True)
                    with trace_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    write_json(state_path, compact_state_snapshot(runtime.snapshot()))
                    result = {
                        "content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}],
                        "structuredContent": tool_result,
                        "isError": tool_result.get("success") is not True,
                    }
                elif method == "ping":
                    result = {}
                elif request_id is None:
                    continue
                else:
                    raise ValueError(f"不支持的 MCP 方法：{method}")
                if request_id is not None:
                    _reply(stdout, request_id, result)
            except Exception as error:
                request_id = request.get("id") if isinstance(request, dict) else None
                if request_id is not None:
                    stdout.write(json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": f"{type(error).__name__}: {error}"},
                    }) + "\n")
                    stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: solver_mcp.py CONFIG_JSON")
    serve(Path(sys.argv[1]))
