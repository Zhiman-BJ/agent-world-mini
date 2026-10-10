"""Logical resource discovery for one isolated Harness environment."""

from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit


RESOURCE_SCHEME = "aw"


def resource_ref(scope_id: str, relative_path: str) -> str:
    path = _relative_path(relative_path)
    encoded = "/".join(quote(part, safe="-._~") for part in path.parts)
    return f"{RESOURCE_SCHEME}://{scope_id}/{encoded}"


def parse_resource_ref(value: str) -> tuple[str, PurePosixPath]:
    parsed = urlsplit(value)
    if parsed.scheme != RESOURCE_SCHEME or not parsed.netloc:
        raise ValueError(f"资源引用必须使用 {RESOURCE_SCHEME}://scope/path：{value}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"资源引用不能包含 query 或 fragment：{value}")
    return parsed.netloc, _relative_path(unquote(parsed.path.lstrip("/")))


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("资源相对路径不能为空")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"资源路径必须是 Scope 内的普通相对路径：{value}")
    return path


class ResourceCatalog:
    """Build a current resource view from environment.json and runtime state."""

    def __init__(
        self,
        environment: dict[str, Any],
        scope_root: Callable[[str], Path],
        *,
        workspace_root: Path | None = None,
        public_workspace_root: Path | None = None,
        scope_mounts: dict[str, str] | None = None,
    ) -> None:
        self.environment = environment
        self._scope_root = scope_root
        self._scopes = {
            str(item["scope_id"]): item
            for item in environment.get("filesystem_scopes", [])
        }
        self._workspace_root = workspace_root.resolve() if workspace_root else None
        self._public_workspace_root = (
            public_workspace_root.resolve() if public_workspace_root else self._workspace_root
        )
        self._scope_mounts = {
            str(key): PurePosixPath(value)
            for key, value in (scope_mounts or {}).items()
        }

    def _business_relative(self, scope_id: str, value: str) -> PurePosixPath:
        """Convert a model-workspace path to a Scope-relative path."""
        raw = Path(value)
        root = self._scope_root(scope_id).resolve()
        if raw.is_absolute():
            if self._workspace_root is None:
                raise ValueError(f"资源路径必须使用相对于当前工作目录的相对路径：{value}")
            public_root = self._public_workspace_root or self._workspace_root
            public_target = raw.resolve(strict=False)
            if not (
                public_target == public_root
                or public_target.is_relative_to(public_root)
            ):
                raise ValueError(f"资源路径越出当前工作目录：{value}")
            workspace_relative = public_target.relative_to(public_root)
            target = (self._workspace_root / workspace_relative).resolve(strict=False)
            if not (target == root or target.is_relative_to(root)):
                raise ValueError(f"资源路径不属于 Scope {scope_id}：{value}")
            relative = target.relative_to(root)
            if not relative.parts:
                raise ValueError("资源路径必须指向 Scope 内的条目")
            return PurePosixPath(relative.as_posix())

        relative = _relative_path(value)
        mount = self._scope_mounts.get(scope_id)
        if mount is not None and mount != PurePosixPath("."):
            if relative.parts[: len(mount.parts)] != mount.parts:
                raise ValueError(
                    f"资源路径必须相对于当前工作目录并包含 Scope 挂载目录 {mount.as_posix()}/：{value}"
                )
            relative = PurePosixPath(*relative.parts[len(mount.parts):])
            if not relative.parts:
                raise ValueError("资源路径必须指向 Scope 内的条目")
        target = (root / Path(*relative.parts)).resolve(strict=False)
        if not (target == root or target.is_relative_to(root)):
            raise ValueError(f"资源路径越出 Scope：{scope_id}/{value}")
        return relative

    def _public_relative(self, scope_id: str, relative: PurePosixPath) -> str:
        mount = self._scope_mounts.get(scope_id)
        if mount is None or mount == PurePosixPath("."):
            return relative.as_posix()
        return (mount / relative).as_posix()

    def overview(self) -> dict[str, Any]:
        scopes = []
        for scope_id, definition in self._scopes.items():
            root = self._scope_root(scope_id)
            files = sum(1 for item in root.rglob("*") if item.is_file())
            scopes.append(
                {
                    "scope_id": scope_id,
                    "name": definition.get("name", scope_id),
                    "description": definition.get("description", ""),
                    "access": definition.get("access"),
                    "file_count": files,
                }
            )
        return {
            "environment_id": self.environment.get("environment_id"),
            "name": self.environment.get("name"),
            "summary": self.environment.get("summary"),
            "record_sets": [
                {
                    "record_set_id": item["record_set_id"],
                    "name": item.get("name", item["record_set_id"]),
                    "description": item.get("description", ""),
                    "access": item.get("access"),
                }
                for item in self.environment.get("record_sets", [])
            ],
            "filesystem_scopes": scopes,
        }

    def list(
        self,
        *,
        scope_id: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1..500")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset 必须是非负整数")
        selected = [scope_id] if scope_id is not None else list(self._scopes)
        unknown = [item for item in selected if item not in self._scopes]
        if unknown:
            raise ValueError("未知 Filesystem Scope：" + ", ".join(unknown))
        needle = (query or "").casefold()
        resources: list[dict[str, Any]] = []
        for current_scope in selected:
            root = self._scope_root(current_scope)
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root).as_posix()
                if needle and needle not in relative.casefold():
                    continue
                if offset:
                    offset -= 1
                    continue
                resources.append(self._describe(current_scope, path))
                if len(resources) >= limit:
                    return resources
        return resources

    def inspect(self, ref: str, *, preview_chars: int = 4000) -> dict[str, Any]:
        if not 0 <= preview_chars <= 20000:
            raise ValueError("preview_chars 必须在 0..20000")
        scope_id, relative = parse_resource_ref(ref)
        path = self.resolve(ref, must_exist=True)
        result = self._describe(scope_id, path)
        if path.is_file() and preview_chars:
            with path.open("rb") as stream:
                sample = stream.read(min(preview_chars * 4, 80000))
            try:
                decoded = sample.decode("utf-8")
            except UnicodeDecodeError as first_error:
                decoded = None
                # A bounded prefix can end in the middle of one UTF-8 codepoint.
                # Trim only that incomplete tail; preserve binary-file detection.
                for trim in range(1, min(4, len(sample) + 1)):
                    try:
                        decoded = sample[:-trim].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        pass
                if decoded is None:
                    result["text_preview"] = None
                    result["preview_truncated"] = False
                    decoded = ""
            if decoded or not sample:
                result["text_preview"] = decoded[:preview_chars]
                result["preview_truncated"] = (
                    path.stat().st_size > len(sample) or len(decoded) > preview_chars
                )
        result["relative_path"] = self._public_relative(scope_id, relative)
        return result

    def resolve(self, ref: str, *, must_exist: bool = False) -> Path:
        scope_id, relative = parse_resource_ref(ref)
        target = self.resolve_relative(scope_id, relative)
        if must_exist and not target.exists():
            raise FileNotFoundError(f"资源不存在：{ref}")
        return target

    def resolve_relative(
        self,
        scope_id: str,
        relative: str | PurePosixPath,
        *,
        must_exist: bool = False,
    ) -> Path:
        """Resolve a Scope-relative path without exposing the physical root."""
        scope_id = str(scope_id)
        if scope_id not in self._scopes:
            raise ValueError(f"未知 Filesystem Scope：{scope_id}")
        relative_path = (
            relative if isinstance(relative, PurePosixPath) else _relative_path(relative)
        )
        root = self._scope_root(scope_id).resolve()
        target = (root / Path(*relative_path.parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"资源路径越出 Scope：{scope_id}/{relative_path}")
        if must_exist and not target.exists():
            raise FileNotFoundError(
                f"资源不存在：{scope_id}/{relative_path.as_posix()}"
            )
        return target

    def normalize_arguments(
        self,
        value: Any,
        *,
        schema: Any = None,
        allowed_scopes: set[str] | None = None,
    ) -> Any:
        """Validate logical resource references and pass relative paths to tool code."""
        resource_scope = (
            schema.get("x-resource-scope")
            if isinstance(schema, dict)
            else None
        )
        if isinstance(value, str) and value.startswith(f"{RESOURCE_SCHEME}://"):
            if resource_scope is not None:
                raise ValueError(
                    "业务工具资源参数必须使用当前工作目录中的路径；"
                    "URI 仅用于 MCP Resources"
                )
            raise ValueError("未标注资源字段不能接收资源 URI")
        if resource_scope is not None and isinstance(value, str):
            scope_id = str(resource_scope)
            if allowed_scopes is not None and scope_id not in allowed_scopes:
                raise ValueError(f"工具未声明使用 Filesystem Scope：{scope_id}")
            relative = self._business_relative(scope_id, value)
            declared_kind = schema.get("x-resource-kind") if isinstance(schema, dict) else None
            target = self.resolve_relative(scope_id, relative)
            if target.exists() and declared_kind == "file" and not target.is_file():
                raise ValueError(f"资源参数要求文件，实际为目录：{value}")
            if target.exists() and declared_kind == "directory" and not target.is_dir():
                raise ValueError(f"资源参数要求目录，实际为文件：{value}")
            return relative.as_posix()
        if isinstance(value, list):
            item_schema = schema.get("items") if isinstance(schema, dict) else None
            return [
                self.normalize_arguments(
                    item,
                    schema=item_schema,
                    allowed_scopes=allowed_scopes,
                )
                for item in value
            ]
        if isinstance(value, dict):
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            return {
                key: self.normalize_arguments(
                    item,
                    schema=properties.get(key),
                    allowed_scopes=allowed_scopes,
                )
                for key, item in value.items()
            }
        return value

    def externalize_result(
        self,
        value: Any,
        *,
        schema: Any = None,
        allowed_scopes: set[str] | None = None,
    ) -> Any:
        """Validate annotated results and expose model-workspace paths."""

        def matches_shape(candidate: Any, current: Any) -> bool:
            if not isinstance(candidate, dict):
                return False
            expected_type = candidate.get("type")
            if expected_type == "object" and not isinstance(current, dict):
                return False
            if expected_type == "array" and not isinstance(current, list):
                return False
            if expected_type == "string" and not isinstance(current, str):
                return False
            if isinstance(current, dict):
                required = candidate.get("required", [])
                if isinstance(required, list) and any(key not in current for key in required):
                    return False
                properties = candidate.get("properties", {})
                if isinstance(properties, dict):
                    for key, child in properties.items():
                        if key not in current or not isinstance(child, dict):
                            continue
                        if "const" in child and current[key] != child["const"]:
                            return False
            return True

        def active_schemas(candidates: list[Any], current: Any) -> list[dict[str, Any]]:
            active: list[dict[str, Any]] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                active.append(candidate)
                for item in candidate.get("allOf", []):
                    active.extend(active_schemas([item], current))
                for keyword in ("oneOf", "anyOf"):
                    choices = candidate.get(keyword, [])
                    if not isinstance(choices, list):
                        continue
                    matched = [item for item in choices if matches_shape(item, current)]
                    active.extend(active_schemas(matched or choices, current))
            return active

        def convert(current: Any, candidates: list[Any]) -> Any:
            active = active_schemas(candidates, current)
            annotations = {
                (item.get("x-resource-scope"), item.get("x-resource-kind"))
                for item in active
                if item.get("x-resource-scope") is not None
                or item.get("x-resource-kind") is not None
            }
            if isinstance(current, str) and current.startswith(f"{RESOURCE_SCHEME}://"):
                raise ValueError("业务工具结果不能返回资源 URI；请返回当前工作目录中的路径")
            if isinstance(current, str) and annotations:
                if len(annotations) != 1:
                    raise ValueError("输出资源字段包含互相冲突的 Scope 标注")
                scope_id, declared_kind = next(iter(annotations))
                if scope_id not in self._scopes:
                    raise ValueError(f"未知 Filesystem Scope：{scope_id}")
                if allowed_scopes is not None and scope_id not in allowed_scopes:
                    raise ValueError(f"工具未声明使用 Filesystem Scope：{scope_id}")
                root = self._scope_root(str(scope_id)).resolve()
                raw = Path(current)
                if raw.is_absolute():
                    target = raw.resolve(strict=False)
                    if not (target == root or target.is_relative_to(root)):
                        raise ValueError(f"输出资源越出 Scope {scope_id}：{current}")
                    relative_path = target.relative_to(root)
                    if not relative_path.parts:
                        raise ValueError("输出资源必须指向 Scope 内的条目")
                    relative = PurePosixPath(relative_path.as_posix())
                else:
                    # Tool code receives Scope-relative inputs and returns the
                    # same internal representation. The mount prefix is added
                    # only when the result crosses back into the model view.
                    relative = _relative_path(current)
                target = self.resolve_relative(str(scope_id), relative)
                if not target.exists():
                    raise ValueError(f"输出资源不存在：{current}")
                if target.exists() and declared_kind == "file" and not target.is_file():
                    raise ValueError(f"输出资源要求文件，实际为目录：{current}")
                if target.exists() and declared_kind == "directory" and not target.is_dir():
                    raise ValueError(f"输出资源要求目录，实际为文件：{current}")
                return self._public_relative(str(scope_id), relative)
            if isinstance(current, list):
                item_schemas = [
                    item["items"]
                    for item in active
                    if isinstance(item.get("items"), dict)
                ]
                return [convert(item, item_schemas) for item in current]
            if isinstance(current, dict):
                converted: dict[str, Any] = {}
                for key, item in current.items():
                    child_schemas = [
                        properties[key]
                        for active_schema in active
                        if isinstance(
                            properties := active_schema.get("properties"), dict
                        )
                        and key in properties
                        and isinstance(properties[key], dict)
                    ]
                    converted[key] = convert(item, child_schemas)
                return converted
            return current

        return convert(value, [schema])

    def _describe(self, scope_id: str, path: Path) -> dict[str, Any]:
        root = self._scope_root(scope_id)
        scope_relative = PurePosixPath(path.relative_to(root).as_posix())
        relative = self._public_relative(scope_id, scope_relative)
        result: dict[str, Any] = {
            "ref": resource_ref(scope_id, scope_relative.as_posix()),
            "scope_id": scope_id,
            "relative_path": relative,
            "name": path.name,
            "description": self._scopes.get(scope_id, {}).get("description", ""),
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            result["size_bytes"] = path.stat().st_size
            result["media_type"] = mimetypes.guess_type(path.name)[0]
        return result
