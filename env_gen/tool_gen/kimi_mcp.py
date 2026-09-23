"""Expose one published ToolGen delivery package as a stdio MCP server for Kimi Code."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any, TextIO

from jsonschema import Draft202012Validator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env_gen.tool_gen.mcp_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    SERVER_VERSION,
    RpcError,
    public_tools,
    serve_jsonrpc,
    tool_call_result,
)
from env_gen.tool_gen.runtime import (  # noqa: E402
    SCHEMA_ROOT,
    ToolPackage,
    ToolRuntime,
    state_diff,
)


@dataclass(frozen=True)
class DeliveryPackage:
    binding_path: Path
    delivery_root: Path
    binding: dict[str, Any]
    tool_document: dict[str, Any]
    package: ToolPackage
    software_root: Path | None
    python_path: Path | None


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label}不是可读的 JSON object：{path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是 JSON object：{path}")
    return value


def _relative_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label}必须位于交付根目录内：{value}")
    return path


def _resolve_delivery_path(root: Path, value: Any, label: str) -> Path:
    relative = _relative_path(value, label)
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{label}解析后越出交付根目录：{value}")
    return target


def _binding_root(binding_path: Path, binding: dict[str, Any]) -> Path:
    package_path = _relative_path(binding.get("package_path"), "binding.package_path")
    root = binding_path.parent
    for _part in package_path.parts:
        root = root.parent
    root = root.resolve()
    expected = _resolve_delivery_path(root, package_path.as_posix(), "binding.package_path")
    if expected != binding_path.parent.resolve():
        raise ValueError(
            "binding.json 的位置与 binding.package_path 不一致："
            f"{binding_path}"
        )
    return root


def _validate_binding(binding: dict[str, Any]) -> None:
    schema_path = SCHEMA_ROOT / "toolgen_delivery_binding.schema.json"
    schema = _read_object(schema_path, "ToolGen binding Schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(binding),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        details = " | ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in errors[:12]
        )
        raise ValueError(f"binding.json 不符合交付契约：{details}")


def _validate_receipts(
    environment_validation_path: Path,
    tool_validation_path: Path,
    tool_names: set[str],
) -> None:
    environment_receipt = _read_object(environment_validation_path, "DataGen 验收回执")
    if environment_receipt.get("valid") is not True:
        raise ValueError(f"DataGen 环境未通过验收：{environment_validation_path}")

    tool_receipt = _read_object(tool_validation_path, "ToolGen 验证回执")
    reports = tool_receipt.get("reports")
    if not isinstance(reports, list):
        raise ValueError(f"ToolGen 验证回执缺少 reports 数组：{tool_validation_path}")
    passed = {
        str(report.get("tool"))
        for report in reports
        if isinstance(report, dict) and report.get("status") == "passed"
    }
    missing = sorted(tool_names - passed)
    if missing:
        raise ValueError("ToolGen 正式工具缺少 passed 验证回执：" + ", ".join(missing))


def load_delivery(binding_path: Path) -> DeliveryPackage:
    binding_path = binding_path.expanduser().resolve()
    binding = _read_object(binding_path, "ToolGen binding")
    _validate_binding(binding)
    delivery_root = _binding_root(binding_path, binding)

    environment_root = _resolve_delivery_path(
        delivery_root, binding["environment_path"], "binding.environment_path"
    )
    tools_path = _resolve_delivery_path(
        delivery_root, binding["tools_path"], "binding.tools_path"
    )
    environment_validation_path = _resolve_delivery_path(
        delivery_root,
        binding["environment_validation_path"],
        "binding.environment_validation_path",
    )
    tool_validation_path = _resolve_delivery_path(
        delivery_root,
        binding["tool_validation_path"],
        "binding.tool_validation_path",
    )

    tool_document = _read_object(tools_path, "ToolGen tools")
    tools = tool_document.get("tools")
    if not isinstance(tools, list):
        raise ValueError(f"tools.json 缺少 tools 数组：{tools_path}")
    package = ToolPackage.load(environment_root, tools=tools)
    if package.environment.get("environment_id") != binding.get("environment_id"):
        raise ValueError("binding.environment_id 与 environment.json 不一致")
    if tool_document.get("environment_id") != binding.get("environment_id"):
        raise ValueError("binding.environment_id 与 tools.json 不一致")
    _validate_receipts(
        environment_validation_path,
        tool_validation_path,
        {str(tool["name"]) for tool in package.tools},
    )

    software_root: Path | None = None
    if binding.get("software_profile_path") is not None:
        software_root = _resolve_delivery_path(
            delivery_root,
            binding["software_profile_path"],
            "binding.software_profile_path",
        )
        if not software_root.is_dir():
            raise ValueError(f"软件 Profile 目录不存在：{software_root}")

    mapping_path = _resolve_delivery_path(
        delivery_root,
        binding["software_mapping_path"],
        "binding.software_mapping_path",
    )
    mapping = _read_object(mapping_path, "软件 Profile 映射")
    if mapping.get("profile_id") != binding.get("software_profile"):
        raise ValueError("binding.software_profile 与软件 Profile 映射不一致")
    python_path: Path | None = None
    if mapping.get("python_path") is not None:
        # Preserve the venv launcher rather than selecting its base executable.
        # Only the final executable may link outside; parent directories must
        # remain inside the declared software profile.
        relative = _relative_path(mapping['python_path'], 'software.python_path')
        launcher = delivery_root / Path(*relative.parts)
        python_path = launcher.parent.resolve() / launcher.name
        if software_root is None or not python_path.parent.is_relative_to(software_root):
            raise ValueError('软件 Python 必须位于其 software profile 内')
        if not python_path.is_file():
            raise ValueError(f"软件 Profile Python 不存在：{python_path}")
    if software_root is not None and python_path is None:
        raise ValueError("软件 Profile 映射缺少 python_path")
    if software_root is None and python_path is not None:
        raise ValueError("未绑定软件 Profile 时 python_path 必须为 null")

    return DeliveryPackage(
        binding_path=binding_path,
        delivery_root=delivery_root,
        binding=binding,
        tool_document=tool_document,
        package=package,
        software_root=software_root,
        python_path=python_path,
    )


class KimiMcpServer:
    def __init__(
        self,
        delivery: DeliveryPackage,
        *,
        trace_path: Path | None = None,
        max_tool_calls: int = 100,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        self.delivery = delivery
        self.trace_path = trace_path.expanduser().resolve() if trace_path else None
        self.max_tool_calls = max_tool_calls
        self.calls = 0
        self.runtime = ToolRuntime(
            delivery.package,
            software_root=delivery.software_root,
        )
        self._tools = {str(tool["name"]): tool for tool in delivery.package.tools}

    def close(self) -> None:
        self.runtime.close()

    def __enter__(self) -> "KimiMcpServer":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _trace(self, record: dict[str, Any]) -> None:
        if self.trace_path is None:
            return
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def _call_tool(self, params: Any) -> dict[str, Any]:
        if self.calls >= self.max_tool_calls:
            raise RpcError(-32000, "工具调用次数已达上限")
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call 缺少 params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in self._tools:
            raise RpcError(-32602, f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "工具 arguments 必须是 object")

        self.calls += 1
        before = self.runtime.snapshot()
        result: dict[str, Any]
        runtime_error: str | None = None
        try:
            result = self.runtime.call(name, arguments)
        except Exception as error:
            runtime_error = f"{type(error).__name__}: {error}"
            result = {
                "success": False,
                "error": {
                    "code": "runtime_error",
                    "path": "$",
                    "message": runtime_error,
                    "retryable": False,
                },
            }
        after = self.runtime.snapshot()
        changes = state_diff(before, after)
        is_error = runtime_error is not None or result.get("success") is False
        record = {
            "sequence": self.calls,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "package_id": self.delivery.binding["package_id"],
            "environment_id": self.delivery.binding["environment_id"],
            "tool": name,
            "arguments": arguments,
            "result": result,
            "runtime_error": runtime_error,
            "state_changes": changes,
        }
        self._trace(record)
        return tool_call_result(result, is_error=is_error)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "agent-world-toolgen",
                    "version": SERVER_VERSION,
                },
            }
        if method == "tools/list":
            return {"tools": public_tools(self.delivery.package.tools)}
        if method == "tools/call":
            return self._call_tool(request.get("params"))
        if method == "ping":
            return {}
        if request_id is None:
            return None
        raise RpcError(-32601, f"不支持的 MCP 方法：{method}")


def serve(
    binding_path: Path,
    *,
    trace_path: Path | None = None,
    max_tool_calls: int = 100,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    delivery = load_delivery(binding_path)
    with KimiMcpServer(
        delivery,
        trace_path=trace_path,
        max_tool_calls=max_tool_calls,
    ) as server:
        serve_jsonrpc(server.handle, stdin=stdin, stdout=stdout)


def kimi_config(
    delivery: DeliveryPackage,
    *,
    server_name: str,
    trace_path: Path | None,
    max_tool_calls: int,
) -> dict[str, Any]:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not server_name or any(char not in allowed for char in server_name):
        raise ValueError("server_name 只能包含字母、数字、下划线和连字符")
    # Keep a virtualenv launcher path intact. Resolving its symlink can select
    # the base interpreter and silently drop the environment's dependencies.
    command = delivery.python_path or Path(sys.executable).absolute()
    server_path = Path(__file__).resolve()
    arguments = [str(server_path), str(delivery.binding_path)]
    if trace_path is not None:
        arguments.extend(["--trace", str(trace_path.expanduser().resolve())])
    arguments.extend(["--max-tool-calls", str(max_tool_calls)])
    return {
        "mcpServers": {
            server_name: {
                "command": str(command),
                "args": arguments,
                "env": {"PYTHONPATH": str(server_path.parents[2])},
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
        max_tool_calls=arguments.max_tool_calls,
    )


if __name__ == "__main__":
    main()
