"""Logical resource discovery for one isolated Agent-World environment."""

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
    ) -> None:
        self.environment = environment
        self._scope_root = scope_root
        self._scopes = {
            str(item["scope_id"]): item
            for item in environment.get("filesystem_scopes", [])
        }

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
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1..500")
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
            sample = path.read_bytes()[: min(preview_chars * 4, 80000)]
            try:
                result["text_preview"] = sample.decode("utf-8")[:preview_chars]
                result["preview_truncated"] = path.stat().st_size > len(sample)
            except UnicodeDecodeError:
                result["text_preview"] = None
                result["preview_truncated"] = False
        result["relative_path"] = relative.as_posix()
        return result

    def resolve(self, ref: str, *, must_exist: bool = False) -> Path:
        scope_id, relative = parse_resource_ref(ref)
        if scope_id not in self._scopes:
            raise ValueError(f"未知 Filesystem Scope：{scope_id}")
        root = self._scope_root(scope_id).resolve()
        target = (root / Path(*relative.parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"资源引用越出 Scope：{ref}")
        if must_exist and not target.exists():
            raise FileNotFoundError(f"资源不存在：{ref}")
        return target

    def normalize_arguments(
        self,
        value: Any,
        *,
        schema: Any = None,
        allowed_scopes: set[str] | None = None,
    ) -> Any:
        """Validate logical resource references and pass relative paths to tool code."""
        if isinstance(value, str) and value.startswith(f"{RESOURCE_SCHEME}://"):
            scope_id, relative = parse_resource_ref(value)
            if scope_id not in self._scopes:
                raise ValueError(f"未知 Filesystem Scope：{scope_id}")
            declared_scope = (
                schema.get("x-resource-scope") if isinstance(schema, dict) else None
            )
            if declared_scope is not None and scope_id != declared_scope:
                raise ValueError(
                    f"资源参数要求 Filesystem Scope {declared_scope}，实际为 {scope_id}"
                )
            if allowed_scopes is not None and scope_id not in allowed_scopes:
                raise ValueError(f"工具未声明使用 Filesystem Scope：{scope_id}")
            declared_kind = (
                schema.get("x-resource-kind") if isinstance(schema, dict) else None
            )
            target = self.resolve(value)
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
        """Turn annotated scope-relative result paths into stable aw:// references."""

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
            if isinstance(current, str) and annotations:
                if len(annotations) != 1:
                    raise ValueError("输出资源字段包含互相冲突的 Scope 标注")
                scope_id, declared_kind = next(iter(annotations))
                if scope_id not in self._scopes:
                    raise ValueError(f"未知 Filesystem Scope：{scope_id}")
                if allowed_scopes is not None and scope_id not in allowed_scopes:
                    raise ValueError(f"工具未声明使用 Filesystem Scope：{scope_id}")
                if current.startswith(f"{RESOURCE_SCHEME}://"):
                    actual_scope, relative = parse_resource_ref(current)
                    if actual_scope != scope_id:
                        raise ValueError(
                            f"输出资源要求 Filesystem Scope {scope_id}，实际为 {actual_scope}"
                        )
                else:
                    relative = _relative_path(current)
                target = self._scope_root(str(scope_id)) / Path(*relative.parts)
                if target.exists() and declared_kind == "file" and not target.is_file():
                    raise ValueError(f"输出资源要求文件，实际为目录：{current}")
                if target.exists() and declared_kind == "directory" and not target.is_dir():
                    raise ValueError(f"输出资源要求目录，实际为文件：{current}")
                return resource_ref(str(scope_id), relative.as_posix())
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
        relative = path.relative_to(root).as_posix()
        result: dict[str, Any] = {
            "ref": resource_ref(scope_id, relative),
            "scope_id": scope_id,
            "relative_path": relative,
            "name": path.name,
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            result["size_bytes"] = path.stat().st_size
            result["media_type"] = mimetypes.guess_type(path.name)[0]
        return result
