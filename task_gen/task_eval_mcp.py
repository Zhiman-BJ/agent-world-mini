"""Minimal stdio MCP server exposing one generated environment's tools."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_gen.tool_graph.step_3_chain_execute import (  # noqa: E402
    _call_tool,
    _schema_error,
)


CallToolFn = Callable[..., dict[str, Any]]
_PROTOCOL_VERSION = "2025-06-18"


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def call_environment_tool(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    workspace: Path,
    *,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    call_tool_fn: CallToolFn = _call_tool,
) -> dict[str, Any]:
    tool = tools.get(name)
    if tool is None:
        return {"tool": name, "arguments": arguments, "result": None, "error": "未知工具"}
    schema_error = _schema_error(tool["inputSchema"], arguments)
    if schema_error:
        return {"tool": name, "arguments": arguments, "result": None, "error": schema_error}
    workspace = workspace.resolve()
    with tempfile.TemporaryDirectory(prefix=".task-eval-tool-", dir=workspace.parent) as temporary:
        temporary_path = Path(temporary)
        candidate = temporary_path / "workspace"
        shutil.copytree(workspace, candidate, symlinks=True)
        outcome = call_tool_fn(
            tool["internal"]["code"], arguments, candidate, timeout, memory_limit, write_limit,
        )
        result = outcome.get("result")
        error = outcome.get("error")
        if outcome.get("kind") is not None:
            error = error or f"工具执行失败：{outcome['kind']}"
        elif not isinstance(result, dict):
            error = "工具返回值必须是 object"
        elif result.get("success") is not True:
            error = "工具返回值必须包含 success=true"
        else:
            error = _schema_error(tool["outputSchema"], result)
        if error is None:
            previous = temporary_path / "previous"
            workspace.rename(previous)
            try:
                candidate.rename(workspace)
            except Exception:
                previous.rename(workspace)
                raise
    return {"tool": name, "arguments": arguments, "result": result, "error": error}


def serve(config_path: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in config["tools"]}
    workspace = Path(config["workspace"]).resolve()
    trace = Path(config["trace"]).resolve()
    calls = 0
    for line in stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                result = {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agent-world-task-eval", "version": "1.0"},
                }
            elif method == "tools/list":
                result = {"tools": [{
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": tool["inputSchema"],
                    "outputSchema": tool["outputSchema"],
                } for tool in tools.values()]}
            elif method == "tools/call":
                if calls >= int(config["max_tool_calls"]):
                    raise RpcError(-32000, "工具调用次数已达到上限")
                params = request.get("params")
                if not isinstance(params, dict):
                    raise RpcError(-32602, "tools/call 缺少 params")
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or name not in tools:
                    raise RpcError(-32602, f"未知工具：{name}")
                if not isinstance(arguments, dict):
                    raise RpcError(-32602, "工具 arguments 必须是 object")
                calls += 1
                record = call_environment_tool(
                    name,
                    arguments,
                    tools,
                    workspace,
                    timeout=int(config["timeout"]),
                    memory_limit=int(config["memory_limit"]),
                    write_limit=int(config["write_limit"]),
                )
                trace.parent.mkdir(parents=True, exist_ok=True)
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                payload = record["result"] if record["error"] is None else {
                    "error": record["error"],
                    "tool_result": record["result"],
                }
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "structuredContent": payload,
                    "isError": record["error"] is not None,
                }
            elif method == "ping":
                result = {}
            elif request_id is None:
                continue
            else:
                raise ValueError(f"不支持的 MCP 方法：{method}")
            if request_id is not None:
                stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
                stdout.flush()
        except Exception as error:
            request_id = request.get("id") if isinstance(request, dict) else None
            if request_id is not None:
                stdout.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": error.code if isinstance(error, RpcError) else -32603,
                        "message": str(error) if isinstance(error, RpcError) else f"{type(error).__name__}: {error}",
                    },
                }) + "\n")
                stdout.flush()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: task_eval_mcp.py CONFIG_JSON")
    serve(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
