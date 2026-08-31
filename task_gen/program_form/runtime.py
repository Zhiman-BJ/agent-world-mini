from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .loader import CompleteEnvironmentPackage


MAX_INLINE_STATE_BYTES = 512 * 1024


def _json_native(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 不是严格 JSON-native 数据：{error}") from error


def snapshot_workspace(root: Path) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        item: dict[str, Any] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        if len(content) <= MAX_INLINE_STATE_BYTES:
            try:
                item["json"] = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        files[relative] = item
    return {"files": files}


def workspace_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_files = before.get("files", {})
    after_files = after.get("files", {})
    before_names = set(before_files)
    after_names = set(after_files)
    modified = sorted(
        name
        for name in before_names & after_names
        if before_files[name].get("sha256") != after_files[name].get("sha256")
    )
    return {
        "created": sorted(after_names - before_names),
        "modified": modified,
        "deleted": sorted(before_names - after_names),
        "changes": {
            name: {
                "before": deepcopy(before_files.get(name)),
                "after": deepcopy(after_files.get(name)),
            }
            for name in sorted(
                (after_names - before_names) | (before_names - after_names) | set(modified)
            )
        },
    }


@dataclass
class ToolCallRecord:
    index: int
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    state_diff: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "arguments": deepcopy(self.arguments),
            "result": deepcopy(self.result),
            "state_diff": deepcopy(self.state_diff),
        }


class CompleteEnvironmentRuntime:
    """Execute one rollout against an isolated copy of a complete environment."""

    def __init__(self, package: CompleteEnvironmentPackage):
        self.package = package
        self._temporary = tempfile.TemporaryDirectory(prefix="agent-world-program-runtime-")
        self.workspace_root = Path(self._temporary.name) / "workspace"
        shutil.copytree(package.workspace_root, self.workspace_root)
        self._tools = {
            str(tool["name"]): deepcopy(tool) for tool in package.environment["tools"]
        }
        self._handlers = {
            name: self._compile_handler(name, tool["internal"]["code"])
            for name, tool in self._tools.items()
        }
        self.trace: list[ToolCallRecord] = []

    @staticmethod
    def _compile_handler(name: str, source: str) -> Callable[[dict[str, Any], Any], Any]:
        try:
            code = compile(source, f"<tool:{name}>", "exec")
            namespace: dict[str, Any] = {}
            exec(code, namespace, namespace)
        except Exception as error:
            raise ValueError(f"工具 {name} 无法编译：{type(error).__name__}: {error}") from error
        handler = namespace.get("run")
        if not callable(handler):
            raise ValueError(f"工具 {name} 的 internal.code 没有定义可调用的 run")
        return handler

    @staticmethod
    def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
        validator = Draft202012Validator(schema)
        return [
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in sorted(
                validator.iter_errors(value),
                key=lambda item: tuple(str(part) for part in item.path),
            )
        ]

    def _restore(self, backup: Path) -> None:
        shutil.rmtree(self.workspace_root)
        shutil.copytree(backup, self.workspace_root)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        arguments = _json_native(arguments, label=f"工具 {name} 的 arguments")
        if not isinstance(arguments, dict):
            raise TypeError("工具 arguments 必须是 object")
        tool = self._tools[name]
        input_errors = self._schema_errors(tool["inputSchema"], arguments)
        if input_errors:
            raise ValueError(f"工具 {name} 输入不符合 Schema：{' | '.join(input_errors)}")

        before = snapshot_workspace(self.workspace_root)
        with tempfile.TemporaryDirectory(prefix="agent-world-call-backup-") as temporary:
            backup = Path(temporary) / "workspace"
            shutil.copytree(self.workspace_root, backup)
            try:
                result = self._handlers[name](
                    deepcopy(arguments),
                    SimpleNamespace(workspace_root=self.workspace_root),
                )
                result = _json_native(result, label=f"工具 {name} 的返回值")
                output_errors = self._schema_errors(tool["outputSchema"], result)
                if output_errors:
                    raise ValueError(
                        f"工具 {name} 输出不符合 Schema：{' | '.join(output_errors)}"
                    )
                if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
                    raise ValueError(f"工具 {name} 未返回统一 success envelope")
            except Exception:
                self._restore(backup)
                raise
            after = snapshot_workspace(self.workspace_root)
            change = workspace_diff(before, after)
            if result["success"] is False and any(
                change[field] for field in ("created", "modified", "deleted")
            ):
                self._restore(backup)
                raise RuntimeError(f"工具 {name} 业务失败后仍修改了工作区")

        record = ToolCallRecord(
            index=len(self.trace) + 1,
            tool=name,
            arguments=arguments,
            result=result,
            state_diff=change,
        )
        self.trace.append(record)
        return deepcopy(result)

    def snapshot(self) -> dict[str, Any]:
        return snapshot_workspace(self.workspace_root)

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "CompleteEnvironmentRuntime":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
