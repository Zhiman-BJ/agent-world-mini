"""Expose one published ToolGen delivery package as a stdio MCP server for Kimi Code."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.delivery import DeliveryPackage, load_delivery  # noqa: E402
from env_gen.tool_gen.mcp_protocol import serve_jsonrpc  # noqa: E402
from env_gen.tool_gen.mcp_server import ToolMcpServer  # noqa: E402
from env_gen.tool_gen.runtime_launch import stdio_launch  # noqa: E402


# Existing callers may keep this name while the implementation lives in the
# harness-neutral MCP module.
KimiMcpServer = ToolMcpServer


def serve(
    binding_path: Path,
    *,
    trace_path: Path | None = None,
    session_root: Path | None = None,
    max_tool_calls: int = 100,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    delivery = load_delivery(binding_path)
    # Keep large task-state copies beside the trace or in the configured data
    # directory instead of forcing them onto a small system /tmp partition.
    temp_root = trace_path.expanduser().resolve().parent if trace_path else None
    if session_root is None and trace_path is not None:
        session_root = trace_path.expanduser().resolve().parent / "sandbox"
    if temp_root is None and os.environ.get("AGENT_WORLD_TMPDIR"):
        temp_root = Path(os.environ["AGENT_WORLD_TMPDIR"])
    with ToolMcpServer(
        delivery,
        trace_path=trace_path,
        max_tool_calls=max_tool_calls,
        temp_root=temp_root,
        session_root=session_root,
    ) as server:
        serve_jsonrpc(server.handle, stdin=stdin, stdout=stdout)


def kimi_config(
    delivery: DeliveryPackage,
    *,
    server_name: str,
    trace_path: Path | None,
    session_root: Path | None = None,
    max_tool_calls: int,
) -> dict[str, Any]:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not server_name or any(char not in allowed for char in server_name):
        raise ValueError("server_name 只能包含字母、数字、下划线和连字符")
    server_path = Path(__file__).resolve()
    arguments = [str(server_path), str(delivery.binding_path)]
    writable_paths: list[Path] = []
    if trace_path is not None:
        resolved_trace = trace_path.expanduser().resolve()
        resolved_trace.parent.mkdir(parents=True, exist_ok=True)
        arguments.extend(["--trace", str(resolved_trace)])
        writable_paths.append(resolved_trace)
        if session_root is None:
            session_root = resolved_trace.parent / "sandbox"
    if session_root is not None:
        resolved_session = session_root.expanduser().resolve()
        resolved_session.parent.mkdir(parents=True, exist_ok=True)
        arguments.extend(["--session-root", str(resolved_session)])
        writable_paths.append(resolved_session)
    arguments.extend(["--max-tool-calls", str(max_tool_calls)])
    launch = stdio_launch(
        delivery,
        server_path=server_path,
        arguments=arguments,
        writable_paths=tuple(writable_paths),
    )
    return {
        "mcpServers": {
            server_name: {
                "transport": "stdio",
                "command": str(launch.command),
                "args": list(launch.arguments),
                "env": launch.environment,
                "cwd": str(delivery.package.package_root),
                "startupTimeoutMs": 30000,
                "toolTimeoutMs": 300000,
            }
        }
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="将 ToolGen 交付包作为 Kimi Code 可调用的 stdio MCP server"
    )
    parser.add_argument("binding", type=Path, help="交付包内的 binding.json")
    parser.add_argument("--trace", type=Path, help="可选的工具调用 JSONL 轨迹")
    parser.add_argument(
        "--session-root",
        type=Path,
        help="保存本次任务独立状态和最终状态的目录",
    )
    parser.add_argument("--max-tool-calls", type=int, default=100)
    parser.add_argument("--print-kimi-config", action="store_true")
    parser.add_argument("--server-name", default="agent_world")
    arguments = parser.parse_args(argv)

    if arguments.max_tool_calls < 1:
        parser.error("--max-tool-calls 必须大于 0")
    if arguments.print_kimi_config:
        delivery = load_delivery(arguments.binding)
        print(
            json.dumps(
                kimi_config(
                    delivery,
                    server_name=arguments.server_name,
                    trace_path=arguments.trace,
                    session_root=arguments.session_root,
                    max_tool_calls=arguments.max_tool_calls,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    serve(
        arguments.binding,
        trace_path=arguments.trace,
        session_root=arguments.session_root,
        max_tool_calls=arguments.max_tool_calls,
    )


if __name__ == "__main__":
    main()
