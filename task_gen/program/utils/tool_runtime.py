"""Program-form Solution 与 clean replay 共用的 Harness Runtime。

每次执行都通过 ``create_task_run_layout`` 分离 ``execution-state`` 与
``model-workspace``，所有业务工具统一委托 ``call_environment_tool`` 完成
Schema、资源 Scope、沙箱、状态提交和失败回滚。运行后端由 ToolGen
``binding.json`` 对应的 ``runtime.json`` 决定。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from harness.resources import ResourceCatalog
from harness.execution import call_environment_tool
from harness.layout import create_task_run_layout

from .schema import Draft202012Validator

from .environment import CompleteEnvironmentPackage


_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def _serialize_state_method(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize transactions/snapshots sharing one task-local state root."""
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        key = str(getattr(self, "state_root", ""))
        with _STATE_LOCKS_GUARD:
            lock = _STATE_LOCKS.setdefault(key, threading.RLock())
        with lock:
            return method(self, *args, **kwargs)

    wrapped.__name__ = getattr(method, "__name__", "wrapped")
    wrapped.__doc__ = method.__doc__
    return wrapped


from harness.state_snapshot import (
    MAX_INLINE_STATE_BYTES,
    snapshot_workspace,
    snapshot_state,
    workspace_diff,
    compact_state_snapshot,
    _clear_snapshot_database_cache,
    _snapshot_record_sets,
    _snapshot_sqlite_metadata,
)


def _json_native(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 不是严格 JSON-native 数据：{error}") from error


def _has_changes(change: dict[str, Any]) -> bool:
    if "changed_assets" in change:
        return bool(change["changed_assets"])
    return any(change.get(field) for field in ("created", "modified", "deleted"))


@dataclass(frozen=True)
class ToolContext:
    """工具 ``run(arguments, context)`` 可使用的受控路径。"""

    workspace_root: Path
    state_root: Path
    records_path: Path
    filesystem_scopes_root: Path
    environment: dict[str, Any]
    records: Any
    software_root: Path

    def scope_root(self, scope_id: str) -> Path:
        if not isinstance(scope_id, str) or not scope_id or "/" in scope_id or "\\" in scope_id:
            raise ValueError("非法 scope_id")
        path = (self.filesystem_scopes_root / scope_id).resolve()
        if path.parent != self.filesystem_scopes_root.resolve():
            raise ValueError("scope_id 越界")
        return path


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


def execute_profile_tool(
    *,
    profile_python: Path,
    software_root: Path,
    source: str,
    tool_name: str,
    arguments: dict[str, Any],
    state_root: Path,
    environment_contract: dict[str, Any],
    timeout_seconds: int = 300,
) -> Any:
    """Run one tool in the bound Profile Python against the supplied state copy."""
    if not profile_python.is_file() or not os.access(profile_python, os.X_OK):
        raise RuntimeError(f"ToolGen Profile Python 不可执行：{profile_python}")
    with tempfile.TemporaryDirectory(
        prefix="agent-world-profile-call-", ignore_cleanup_errors=True,
    ) as temporary:
        root = Path(temporary)
        request = root / "request.json"
        response = root / "response.json"
        request.write_text(json.dumps({
            "source": source,
            "tool_name": tool_name,
            "arguments": arguments,
            "state_root": str(state_root),
            "environment": environment_contract,
            "software_root": str(software_root),
        }, ensure_ascii=False), encoding="utf-8")
        environment = os.environ.copy()
        # Relocatable ToolGen profiles may retain an OpenSSL default path from
        # their original build root. Give the worker a valid host CA bundle
        # when the caller did not already provide one.
        ssl_file = environment.get("SSL_CERT_FILE")
        system_ssl_file = Path("/etc/ssl/certs/ca-certificates.crt")
        if (not ssl_file or not Path(ssl_file).is_file()) and system_ssl_file.is_file():
            environment["SSL_CERT_FILE"] = str(system_ssl_file)
        ssl_dir = environment.get("SSL_CERT_DIR")
        system_ssl_dir = Path("/etc/ssl/certs")
        if (not ssl_dir or not Path(ssl_dir).is_dir()) and system_ssl_dir.is_dir():
            environment["SSL_CERT_DIR"] = str(system_ssl_dir)
        project_root = str(Path(__file__).resolve().parents[3])
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            project_root
            if not current_pythonpath
            else project_root + os.pathsep + current_pythonpath
        )
        node_modules = software_root / "node" / "node_modules"
        if node_modules.is_dir():
            environment["NODE_PATH"] = str(node_modules)
        completed = subprocess.run(
            [
                str(profile_python),
                "-m",
                "task_gen.program.utils.profile_tool_worker",
                str(request),
                str(response),
            ],
            cwd=state_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not response.is_file():
            detail = (completed.stderr or completed.stdout or "no worker output")[-4000:]
            raise RuntimeError(
                f"工具 {tool_name} 的 Profile 工作进程失败({completed.returncode})：{detail}"
            )
        payload = json.loads(response.read_text(encoding="utf-8"))
        if payload.get("ok") is not True:
            raise RuntimeError(
                f"工具 {tool_name} 在 Profile 中执行失败：{payload.get('error')}"
            )
        return payload.get("result")


def _docker_call_tool(
    package: CompleteEnvironmentPackage, code: str, arguments: dict[str, Any],
    state_root: Path, timeout: int, memory_limit: int, write_limit: int,
    environment: dict[str, Any] | None = None, software_root: Path | None = None,
    *, process_limit: int = 1024, read_only: bool = False,
    record_database: Path | None = None, scope_roots: dict | None = None,
) -> dict[str, Any]:
    """Execute the frozen package through the shared Docker backend."""
    from harness.delivery import load_delivery
    from harness.sandbox import run_tool_in_docker

    binding = (package.delivery or {}).get("binding_path")
    if not isinstance(binding, str) or not binding:
        return {"kind": "runtime_error", "result": None,
                "error": "Docker 环境缺少原始 binding_path"}
    delivery = load_delivery(Path(binding))
    if delivery.runtime.get("backend") != "docker":
        return {"kind": "runtime_error", "result": None,
                "error": "冻结环境与原始 binding 的 Docker 后端不一致"}
    return run_tool_in_docker(
        code, arguments, state_root, timeout, memory_limit, write_limit,
        environment, runtime=delivery.runtime, process_limit=process_limit,
        read_only=read_only, record_database=record_database, scope_roots=scope_roots,
    )


def _program_v2_call_tool(
    code: str,
    arguments: dict[str, Any],
    state_root: Path,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    environment: dict[str, Any] | None = None,
    software_root: Path | None = None,
    *,
    process_limit: int = 1024,
    software: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one v2 tool inside ``call_environment_tool``'s transaction.

    ``call_environment_tool`` already copies state and commits it atomically.
    Python-profile tools run in their delivered interpreter without a nested
    bubblewrap namespace. Host-Python tools retain the Harness sandbox. Both
    paths perform the same writable-resource check before the outer transaction
    can commit.
    """
    from harness.sandbox import run_tool_in_sandbox

    environment = environment or {}
    try:
        before = snapshot_state(state_root, environment, package_format="v2")
        if software:
            result = execute_profile_tool(
                profile_python=Path(str(software["python"])),
                software_root=Path(str(software["root"])),
                source=code,
                tool_name="python_profile_tool",
                arguments=arguments,
                state_root=state_root,
                environment_contract=environment,
                timeout_seconds=timeout,
            )
            outcome = {"kind": None, "result": result, "error": None}
        else:
            outcome = run_tool_in_sandbox(
                code,
                arguments,
                state_root,
                timeout,
                memory_limit,
                write_limit,
                environment,
                software_root,
                process_limit=process_limit,
            )
        result = outcome.get("result")
        if outcome.get("error") or not isinstance(result, dict) or result.get("success") is not True:
            return outcome
        after = snapshot_state(state_root, environment, package_format="v2")
        change = workspace_diff(before, after)
        writable = {
            str(item["record_set_id"])
            for item in environment.get("record_sets", [])
            if item.get("access") == "copy_on_write"
        }
        writable.update({
            str(item["scope_id"])
            for item in environment.get("filesystem_scopes", [])
            if item.get("access") == "copy_on_write"
        })
        forbidden = set(change.get("changed_assets", [])) - writable
        if forbidden:
            raise PermissionError(
                "工具修改了只读或未声明状态：" + ", ".join(sorted(forbidden))
            )
        return outcome
    except Exception as error:
        return {
            "kind": "exception",
            "result": None,
            "error": f"{type(error).__name__}: {error}",
        }


def _with_harness_context_compatibility(tool: dict[str, Any]) -> dict[str, Any]:
    """Adapt pre-Harness context attributes without bypassing the Harness sandbox."""
    adapted = deepcopy(tool)
    internal = adapted.get("internal")
    if not isinstance(internal, dict) or not isinstance(internal.get("code"), str):
        return adapted
    internal["code"] = internal["code"] + r'''

_agent_world_original_run = run
def run(arguments, context):
    if hasattr(context, "state_root"):
        if not hasattr(context, "workspace_root"):
            context.workspace_root = context.state_root
        if not hasattr(context, "records_path"):
            context.records_path = context.state_root / "records.sqlite"
        if not hasattr(context, "filesystem_scopes_root"):
            context.filesystem_scopes_root = context.state_root / "filesystem_scopes"
    return _agent_world_original_run(arguments, context)
'''
    return adapted


class CompleteEnvironmentRuntime:
    """在一份独立状态副本上执行完整环境的工具。"""

    def __init__(
        self,
        package: CompleteEnvironmentPackage,
        *,
        tool_timeout_seconds: int = 300,
    ):
        self.package = package
        self.tool_timeout_seconds = max(1, int(tool_timeout_seconds))
        self._temporary = tempfile.TemporaryDirectory(
            prefix="agent-world-program-runtime-", ignore_cleanup_errors=True,
        )
        self.layout = create_task_run_layout(
            package.state_root,
            Path(self._temporary.name) / "task-run",
        )
        self.state_root = self.layout.execution_state
        # 模型工作区与工具执行状态严格分离；业务工具只接触 state_root。
        self.workspace_root = self.layout.model_workspace
        self.records_path = self.state_root / "records.sqlite"
        self.filesystem_scopes_root = self.state_root / "filesystem_scopes"
        self.software_root = package.software_root or package.package_root
        self.resources = ResourceCatalog(
            package.environment,
            lambda scope_id: self.filesystem_scopes_root / scope_id,
        )
        self._tools = {
            str(tool["name"]): _with_harness_context_compatibility(tool)
            for tool in package.tools
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
        return _schema_validation_errors(schema, value)

    def _restore(self, backup: Path) -> None:
        shutil.rmtree(self.state_root)
        shutil.copytree(backup, self.state_root)

    def _recover_missing_state(self) -> None:
        """Recreate a task-local state root if a transaction removed it.

        A Docker/Harness directory transaction should atomically leave
        ``execution-state`` in place. Under heavy concurrent Docker startup,
        a failed rename can nevertheless leave the temporary root absent. The
        immutable package state is a safe recovery source; the caller retries
        the current tool call so no partial result is accepted.
        """
        if self.state_root.exists():
            return
        self.state_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.package.state_root, self.state_root)

    def _context(self) -> ToolContext:
        from env_gen.tool_gen.runtime import RecordStore

        return ToolContext(
            workspace_root=self.workspace_root,
            state_root=self.state_root,
            records_path=self.records_path,
            filesystem_scopes_root=self.filesystem_scopes_root,
            environment=deepcopy(self.package.environment),
            records=RecordStore(self.records_path, self.package.environment),
            software_root=self.software_root,
        )

    def _run_in_profile(
        self,
        name: str,
        source: str,
        arguments: dict[str, Any],
    ) -> Any:
        python = self.package.profile_python
        if python is None:
            return self._handlers[name](deepcopy(arguments), self._context())
        return execute_profile_tool(
            profile_python=python,
            software_root=self.software_root,
            source=source,
            tool_name=name,
            arguments=arguments,
            state_root=self.state_root,
            environment_contract=self.package.environment,
        )

    def _read_only_changes(self, change: dict[str, Any]) -> list[str]:
        if not _has_changes(change):
            return []
        if self.package.package_format == "v2":
            access = {
                str(item["record_set_id"]): str(item["access"])
                for item in self.package.environment.get("record_sets", [])
            }
            access.update({
                str(item["scope_id"]): str(item["access"])
                for item in self.package.environment.get("filesystem_scopes", [])
            })
            return [
                asset for asset in change.get("changed_assets", [])
                if access.get(asset) != "copy_on_write"
            ]

        resources = self.package.environment.get("resources", [])
        changed_paths = [
            *change.get("created", []),
            *change.get("modified", []),
            *change.get("deleted", []),
        ]
        rejected: list[str] = []
        for path in changed_paths:
            if not any(
                item.get("writable") is True and _path_matches_v1_resource(path, item)
                for item in resources if isinstance(item, dict)
            ):
                rejected.append(path)
        return rejected

    @_serialize_state_method
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        arguments = _json_native(arguments, label=f"工具 {name} 的 arguments")
        if not isinstance(arguments, dict):
            raise TypeError("工具 arguments 必须是 object")
        before = self.snapshot()
        backend = str((self.package.runtime or {}).get("backend") or (
            "python_profile" if self.package.profile_python is not None else "host_python"
        ))
        software: dict[str, str] | None = None
        # Legacy Program tools always received a usable software_root, even
        # when the environment was loaded directly instead of through a
        # ToolGen binding.  Preserve that contract when Harness falls back to
        # host_python; otherwise older tools fail before their business logic
        # runs because Context has no software_root attribute.
        software_root: Path | None = self.software_root
        call_tool_fn: Callable[..., dict[str, Any]] | None = None
        if backend == "python_profile":
            if self.package.profile_python is None or self.package.software_root is None:
                raise RuntimeError("python_profile 后端缺少 Python 或 software root")
            software = {
                "root": str(self.package.software_root),
                "python": str(self.package.profile_python),
            }
        elif backend == "docker":
            runtime_root = (self.package.runtime or {}).get("software_root", "/opt/tool-software")
            software_root = Path(str(runtime_root))
            call_tool_fn = lambda *args, **kwargs: _docker_call_tool(
                self.package, *args, **kwargs
            )
        elif backend != "host_python":
            raise RuntimeError(f"不支持的 ToolGen runtime backend：{backend}")
        if self.package.package_format == "v2" and backend in {"host_python", "python_profile"}:
            call_tool_fn = _program_v2_call_tool

        for recovery_attempt in range(2):
            self._recover_missing_state()
            try:
                record = call_environment_tool(
                    name,
                    arguments,
                    self._tools,
                    self.state_root,
                    timeout=self.tool_timeout_seconds,
                    memory_limit=2 * 1024 * 1024 * 1024,
                    write_limit=256 * 1024 * 1024,
                    process_limit=1024,
                    call_tool_fn=call_tool_fn,
                    environment=self.package.environment,
                    software=software,
                    software_root=software_root,
                )
            except (FileNotFoundError, OSError):
                if recovery_attempt == 0 and not self.state_root.exists():
                    continue
                raise
            if recovery_attempt == 0 and not self.state_root.exists():
                continue
            break
        result = record.get("result")
        is_business_failure = (
            isinstance(result, dict)
            and result.get("success") is False
            and not record.get("failure_kind")
        )
        if record.get("error") and not is_business_failure:
            raise RuntimeError(
                f"工具 {name} 执行失败"
                f"[{record.get('failure_kind') or 'validation'}]：{record['error']}"
            )
        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            raise RuntimeError(f"工具 {name} 未返回统一 success envelope")
        arguments = deepcopy(record.get("arguments", arguments))
        after = self.snapshot()
        change = workspace_diff(before, after)

        record = ToolCallRecord(
            index=len(self.trace) + 1,
            tool=name,
            arguments=arguments,
            result=result,
            state_diff=change,
        )
        self.trace.append(record)
        return deepcopy(result)

    @_serialize_state_method
    def snapshot(self) -> dict[str, Any]:
        self._recover_missing_state()
        return snapshot_state(
            self.state_root,
            self.package.environment,
            package_format=self.package.package_format,
        )

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "CompleteEnvironmentRuntime":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _schema_validation_errors(schema: dict[str, Any], value: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


def _path_matches_v1_resource(path: str, resource: dict[str, Any]) -> bool:
    import fnmatch

    declared = str(resource.get("path") or "").replace("\\", "/").rstrip("/")
    if resource.get("storage_type") == "directory":
        return path == declared or path.startswith(declared + "/")
    return bool(declared and fnmatch.fnmatchcase(path, declared))
