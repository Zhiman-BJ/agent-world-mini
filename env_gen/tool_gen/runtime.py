from __future__ import annotations

import ctypes
import errno
import io
import json
import hashlib
import os
import shutil
import sqlite3
import tempfile
import sys
from contextlib import redirect_stdout
from contextlib import contextmanager
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from jsonschema import Draft202012Validator

from .resources import ResourceCatalog

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"
MAX_CAPTURED_TOOL_STDOUT = 16 * 1024 * 1024


@contextmanager
def _capture_tool_stdout():
    """Keep tool diagnostics off the stdio MCP/JSON-RPC wire."""
    original_fd = os.dup(1)
    python_output = io.StringIO()
    native_output = tempfile.TemporaryFile()
    original_replace = os.replace
    original_rename = os.rename

    def same_filesystem_replace(source, destination, *args, **kwargs):
        try:
            return original_replace(source, destination, *args, **kwargs)
        except OSError as error:
            if error.errno != errno.EXDEV or args or kwargs:
                raise
            source_path = Path(source)
            destination_path = Path(destination)
            if source_path.is_dir():
                raise
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            handle, staged = tempfile.mkstemp(
                prefix=".replace-", dir=str(destination_path.parent)
            )
            os.close(handle)
            try:
                shutil.copy2(source_path, staged)
                with open(staged, "rb") as stream:
                    os.fsync(stream.fileno())
                original_replace(staged, destination_path)
                os.unlink(source_path)
            except Exception:
                try:
                    os.unlink(staged)
                except OSError:
                    pass
                raise

    os.replace = same_filesystem_replace
    os.rename = same_filesystem_replace
    try:
        sys.stdout.flush()
        os.dup2(native_output.fileno(), 1)
        with redirect_stdout(python_output):
            yield
    finally:
        try:
            ctypes.CDLL(None).fflush(None)
        except Exception:
            pass
        os.dup2(original_fd, 1)
        os.close(original_fd)
        os.replace = original_replace
        os.rename = original_rename
        native_output.seek(0)
        native_bytes = native_output.read(MAX_CAPTURED_TOOL_STDOUT + 1)
        native_output.close()
        python_bytes = python_output.getvalue().encode("utf-8", errors="replace")
        combined = python_bytes + native_bytes
        if len(combined) > MAX_CAPTURED_TOOL_STDOUT:
            raise ValueError("工具 stdout 输出超过 16 MiB")
        if combined:
            sys.stderr.write(combined.decode("utf-8", errors="replace"))
            sys.stderr.flush()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_native(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 不是严格 JSON 数据：{error}") from error


def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.path),
        )
    ]


def _table_digest(database: Path, table: str) -> str:
    quoted = _quote(table)
    digest = hashlib.sha256()
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")]
        digest.update(json.dumps(columns, ensure_ascii=False).encode('utf-8'))
        for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
            digest.update(b'\n')
            digest.update(json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    return digest.hexdigest()


def _file_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def _validate_v2_package(package_root: Path, environment: dict[str, Any]) -> None:
    schema = json.loads((SCHEMA_ROOT / "environment.schema.json").read_text(encoding="utf-8"))
    errors = _schema_errors(schema, environment)
    if errors:
        raise ValueError("environment.json 不符合 v2 Schema：" + " | ".join(errors[:12]))
    state = package_root / "state"
    record_sets = environment.get("record_sets", [])
    database = state / "records.sqlite"
    if record_sets and not database.is_file():
        raise ValueError("Record Set 非空但 state/records.sqlite 不存在")
    if record_sets:
        with closing(sqlite3.connect(database)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        declared = {str(item["record_set_id"]) for item in record_sets}
        if tables != declared:
            raise ValueError(f"SQLite 表与 Record Set 不一致：实际={sorted(tables)} 声明={sorted(declared)}")
    scopes_root = state / "filesystem_scopes"
    for scope in environment.get("filesystem_scopes", []):
        scope_root = scopes_root / str(scope["scope_id"])
        if not scope_root.is_dir():
            raise ValueError(f"Filesystem Scope 不存在：{scope['scope_id']}")


def _decode_record(row: sqlite3.Row, fields: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for name, definition in fields.items():
        value = result.get(name)
        if value is not None and definition.get("type") in {"object", "array"}:
            result[name] = json.loads(value)
        elif value is not None and definition.get("type") == "boolean":
            result[name] = bool(value)
    return result


def _encode_value(value: Any, definition: dict[str, Any]) -> Any:
    if value is None:
        return None
    if definition.get("type") in {"object", "array"}:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if definition.get("type") == "boolean":
        return int(bool(value))
    return value


@dataclass(frozen=True)
class ToolPackage:
    package_root: Path
    environment: dict[str, Any]
    tools: tuple[dict[str, Any], ...]

    @classmethod
    def load(
        cls,
        package_root: Path,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> "ToolPackage":
        package_root = package_root.resolve()
        environment_path = package_root / "environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        _validate_v2_package(package_root, environment)

        if tools is None:
            tools_path = package_root / "tools.json"
            document = json.loads(tools_path.read_text(encoding="utf-8"))
            tools = document.get("tools") if isinstance(document, dict) else None
        if not isinstance(tools, list) or not tools:
            raise ValueError("tools.json 的 tools 必须是非空数组")

        tool_schema = json.loads(
            (SCHEMA_ROOT / "validation/tool.schema.json").read_text(encoding="utf-8")
        )
        names: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise ValueError(f"tools[{index}] 必须是 object")
            errors = _schema_errors(tool_schema, tool)
            if errors:
                raise ValueError(f"工具 {tool.get('name', index)} 不符合 Schema：{' | '.join(errors)}")
            name = str(tool["name"])
            if name in names:
                raise ValueError(f"工具名重复：{name}")
            names.add(name)
            normalized.append(deepcopy(tool))
        return cls(package_root, environment, tuple(normalized))


from utils.record_store import RecordStore

def snapshot_state(root: Path, environment: dict[str, Any]) -> dict[str, Any]:
    database = root / "state/records.sqlite"
    records = {
        str(item["record_set_id"]): _table_digest(database, str(item["record_set_id"]))
        for item in environment.get("record_sets", [])
    }
    scopes: dict[str, dict[str, dict[str, Any]]] = {}
    for scope in environment.get("filesystem_scopes", []):
        scope_id = str(scope["scope_id"])
        scope_root = root / "state/filesystem_scopes" / scope_id
        scopes[scope_id] = {
            path.relative_to(scope_root).as_posix(): _file_fingerprint(path)
            for path in sorted(item for item in scope_root.rglob("*") if item.is_file())
        }
    return {"record_sets": records, "filesystem_scopes": scopes}


def state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed_records = sorted(
        name
        for name in set(before["record_sets"]) | set(after["record_sets"])
        if before["record_sets"].get(name) != after["record_sets"].get(name)
    )
    changed_scopes = sorted(
        name
        for name in set(before["filesystem_scopes"]) | set(after["filesystem_scopes"])
        if before["filesystem_scopes"].get(name) != after["filesystem_scopes"].get(name)
    )
    return {"record_sets": changed_records, "filesystem_scopes": changed_scopes}


class ToolRuntime:
    def __init__(
        self,
        package: ToolPackage,
        *,
        software_root: Path | None = None,
        temp_root: Path | None = None,
        session_root: Path | None = None,
        isolated: bool = False,
        execution_runtime: dict[str, Any] | None = None,
    ) -> None:
        self.package = package
        self.isolated = isolated
        self.resumed = False
        self.execution_runtime = execution_runtime
        receipt = package.package_root / 'tool_generation/container_runtime.json'
        if self.execution_runtime is None and receipt.is_file():
            self.execution_runtime = json.loads(receipt.read_text(encoding='utf-8'))
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if session_root is not None:
            self.root = Path(session_root).expanduser().resolve()
            self.root.parent.mkdir(parents=True, exist_ok=True)
            if self.root.exists():
                saved = self.root / 'environment.json'
                if not saved.is_file() or not (self.root / 'state').is_dir():
                    raise ValueError('已有任务目录缺少 environment.json 或 state')
                if json.loads(saved.read_text(encoding='utf-8')) != package.environment:
                    raise ValueError('已有任务目录属于不同的环境配置')
                self.resumed = True
            else:
                self.root.mkdir()
        else:
            temporary_parent = None
            if temp_root is not None:
                temporary_parent = Path(temp_root).expanduser().resolve()
                temporary_parent.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(
                prefix="agent-world-tool-runtime-",
                dir=str(temporary_parent) if temporary_parent is not None else None,
            )
            self.root = Path(self._temporary.name)
        if not self.resumed:
            shutil.copy2(package.package_root / "environment.json", self.root / "environment.json")
            shutil.copytree(package.package_root / "state", self.root / "state")
        self._software_root_path = self._software_root(software_root)
        self._software_environment = self._software_environment_for(self._software_root_path)
        self._tools = {str(tool["name"]): deepcopy(tool) for tool in package.tools}
        self._handlers = {} if isolated else {
            name: self._compile_handler(name, tool["internal"]["code"])
            for name, tool in self._tools.items()
        }
        self.resources = ResourceCatalog(package.environment, self._scope_root)
        self.context = SimpleNamespace(
            environment=deepcopy(package.environment),
            records=RecordStore(self.root / "state/records.sqlite", package.environment),
            scope_root=lambda scope_id: self._scope_root(str(scope_id)),
            resources=self.resources,
            resolve_resource=lambda ref, must_exist=False: self.resources.resolve(
                str(ref), must_exist=bool(must_exist)
            ),
            software_root=self._software_root_path,
        )

    @staticmethod
    def _software_environment_for(root: Path) -> dict[str, str]:
        """Build the native runtime environment without leaking it globally."""
        from .software import runtime_environment

        return runtime_environment(root, os.environ)

    @contextmanager
    def _software_environment_scope(self):
        previous = os.environ.copy()
        os.environ.update(self._software_environment)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def _software_root(self, override: Path | None) -> Path:
        """Return the installed dependency root when this package has one.

        The path is metadata supplied by ToolGen's software resolver.  Runtime
        still keeps the environment state in its isolated temporary copy; only
        imports and command-line assets come from the selected software profile.
        """
        if override is None and os.environ.get("TOOLGEN_SOFTWARE_ROOT"):
            override = Path(os.environ["TOOLGEN_SOFTWARE_ROOT"])
        if override is not None:
            root = override.expanduser().resolve()
            return root
        info_path = self.package.package_root / "tool_generation/software_environment.json"
        if info_path.is_file():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                root = Path(str(info.get("root", ""))).resolve()
                if root.is_dir():
                    return root
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        return self.package.package_root / "tool_generation/software"

    @staticmethod
    def _compile_handler(name: str, source: str) -> Callable[[dict[str, Any], Any], Any]:
        namespace: dict[str, Any] = {}
        try:
            exec(compile(source, f"<tool:{name}>", "exec"), namespace, namespace)
        except Exception as error:
            raise ValueError(f"工具 {name} 无法编译：{type(error).__name__}: {error}") from error
        handler = namespace.get("run")
        if not callable(handler):
            raise ValueError(f"工具 {name} 没有定义 run(arguments, context)")
        return handler

    def _scope_root(self, scope_id: str) -> Path:
        known = {str(item["scope_id"]) for item in self.package.environment.get("filesystem_scopes", [])}
        if scope_id not in known:
            raise ValueError(f"未知 Filesystem Scope：{scope_id}")
        return self.root / "state/filesystem_scopes" / scope_id

    def snapshot(self) -> dict[str, Any]:
        return snapshot_state(self.root, self.package.environment)

    def _restore(self, backup: Path) -> None:
        shutil.rmtree(self.root / "state")
        shutil.copytree(backup, self.root / "state")
        self.context.records = RecordStore(
            self.root / "state/records.sqlite", self.package.environment
        )

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        tool = self._tools[name]
        arguments = _json_native(arguments, label=f"工具 {name} 的 arguments")
        known_scopes = {
            str(item["scope_id"])
            for item in self.package.environment.get("filesystem_scopes", [])
        }
        declared_resources = set(
            str(item)
            for item in tool.get("usageConditions", {}).get("targetResources", [])
        )
        arguments = self.resources.normalize_arguments(
            arguments,
            schema=tool["inputSchema"],
            allowed_scopes=known_scopes & declared_resources,
        )
        input_errors = _schema_errors(tool["inputSchema"], arguments)
        if input_errors:
            raise ValueError(f"工具 {name} 输入不符合 Schema：{' | '.join(input_errors)}")

        if self.isolated:
            from harness.execution import call_environment_tool
            from .software import runtime_info

            info = runtime_info(self.package.package_root)
            software = ({'python': info['python'], 'prefix': info['prefix'], 'root': info['root']} if info
                        else {'python': sys.executable, 'prefix': sys.prefix,
                              'root': str(self._software_root_path if self._software_root_path.is_dir() else Path(sys.prefix))})
            record = call_environment_tool(
                name, arguments, self._tools, self.root / 'state',
                timeout=int(os.environ.get('TOOLGEN_TOOL_TIMEOUT_SECONDS', '300')),
                memory_limit=int(os.environ.get('TOOLGEN_TOOL_MEMORY_BYTES', str(2 * 1024**3))),
                write_limit=int(os.environ.get('TOOLGEN_TOOL_WRITE_BYTES', str(256 * 1024**2))),
                environment=self.package.environment, software=software,
                software_root=self._software_root_path, runtime=self.execution_runtime,
            )
            result = record.get('result')
            business_failure = (isinstance(result, dict) and result.get('success') is False
                                and record.get('error') == '工具返回值必须包含 success=true')
            if record.get('error') and not business_failure:
                raise RuntimeError(str(record['error']))
            output_errors = _schema_errors(tool['outputSchema'], result)
            if output_errors:
                raise ValueError('工具输出不符合 Schema：' + ' | '.join(output_errors))
            return deepcopy(result)

        before = self.snapshot()
        with tempfile.TemporaryDirectory(prefix="agent-world-tool-backup-", dir=self.root.parent) as temporary:
            backup = Path(temporary) / "state"
            shutil.copytree(self.root / "state", backup)
            try:
                with self._software_environment_scope(), _capture_tool_stdout():
                    result = _json_native(
                        self._handlers[name](deepcopy(arguments), self.context),
                        label=f"工具 {name} 的返回值",
                    )
                result = self.resources.externalize_result(
                    result,
                    schema=tool["outputSchema"],
                    allowed_scopes=known_scopes & declared_resources,
                )
                output_errors = _schema_errors(tool["outputSchema"], result)
                if output_errors:
                    raise ValueError(f"工具 {name} 输出不符合 Schema：{' | '.join(output_errors)}")
                after = self.snapshot()
                change = state_diff(before, after)
                if result.get("success") is False and any(change.values()):
                    raise RuntimeError(f"工具 {name} 业务失败后仍修改了环境状态")
                access = {
                    str(item.get("record_set_id") or item.get("scope_id")): item.get("access")
                    for item in [
                        *self.package.environment.get("record_sets", []),
                        *self.package.environment.get("filesystem_scopes", []),
                    ]
                }
                changed_assets = [*change["record_sets"], *change["filesystem_scopes"]]
                read_only = [asset for asset in changed_assets if access.get(asset) != "copy_on_write"]
                if read_only:
                    raise RuntimeError(f"工具 {name} 修改了只读资源：{', '.join(read_only)}")
                if any(change.values()):
                    _validate_v2_package(self.root, self.package.environment)
            except Exception:
                self._restore(backup)
                raise
        return deepcopy(result)

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()

    @property
    def persistent(self) -> bool:
        return self._temporary is None

    def __enter__(self) -> "ToolRuntime":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
