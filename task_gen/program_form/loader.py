from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


PUBLIC_TOOL_FIELDS = ("name", "description", "inputSchema", "outputSchema")
ENVIRONMENT_FIELDS = (
    "schema_version",
    "environment_id",
    "name",
    "description",
    "resources",
    "rules",
    "tools",
)


@dataclass(frozen=True)
class CompleteEnvironmentPackage:
    package_root: Path
    environment_path: Path
    workspace_root: Path
    environment: dict[str, Any]

    @classmethod
    def load(cls, value: Path) -> "CompleteEnvironmentPackage":
        value = value.resolve()
        environment_path = value / "environment.json" if value.is_dir() else value
        if not environment_path.is_file():
            raise FileNotFoundError(f"找不到完整环境文件：{environment_path}")
        package_root = environment_path.parent
        workspace_root = package_root / "workspace"
        if not workspace_root.is_dir():
            raise ValueError(f"完整环境缺少 workspace/：{package_root}")
        try:
            environment = json.loads(environment_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"environment.json 不是合法 JSON：{error}") from error
        cls._validate_shape(environment)
        cls._validate_schema(environment)
        return cls(package_root, environment_path, workspace_root, environment)

    @staticmethod
    def _validate_shape(environment: Any) -> None:
        if not isinstance(environment, dict):
            raise ValueError("完整环境根节点必须是 object")
        missing = [field for field in ENVIRONMENT_FIELDS if field not in environment]
        extra = sorted(set(environment) - set(ENVIRONMENT_FIELDS))
        if missing:
            raise ValueError(f"完整环境缺少字段：{', '.join(missing)}")
        if extra:
            raise ValueError(f"完整环境包含额外字段：{', '.join(extra)}")
        if not isinstance(environment["tools"], list) or not environment["tools"]:
            raise ValueError("完整环境 tools 必须是非空数组")
        names: set[str] = set()
        for index, tool in enumerate(environment["tools"]):
            if not isinstance(tool, dict):
                raise ValueError(f"tools[{index}] 必须是 object")
            missing_tool = [
                field for field in (*PUBLIC_TOOL_FIELDS, "internal") if field not in tool
            ]
            if missing_tool:
                raise ValueError(
                    f"tools[{index}] 缺少字段：{', '.join(missing_tool)}"
                )
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(f"tools[{index}].name 必须是非空字符串")
            if name in names:
                raise ValueError(f"工具名重复：{name}")
            names.add(name)
            internal = tool.get("internal")
            if not isinstance(internal, dict) or not isinstance(internal.get("code"), str):
                raise ValueError(f"工具 {name} 缺少 internal.code")

    @staticmethod
    def _validate_schema(environment: dict[str, Any]) -> None:
        schema_root = Path(__file__).resolve().parents[2] / "schemas"
        schemas = []
        for name in (
            "validation/environment.schema.json",
            "validation/tool.schema.json",
            "validation/complete_environment.schema.json",
        ):
            path = schema_root / name
            try:
                schemas.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"无法读取环境 Schema {path}：{error}") from error
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas
        )
        validator = Draft202012Validator(schemas[-1], registry=registry)
        errors = sorted(
            validator.iter_errors(environment),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            detail = " | ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
                for error in errors[:20]
            )
            raise ValueError(f"完整环境不符合 validation/complete_environment.schema.json：{detail}")

    def public_environment(self) -> dict[str, Any]:
        value = deepcopy(self.environment)
        value["tools"] = [
            {field: deepcopy(tool[field]) for field in PUBLIC_TOOL_FIELDS}
            for tool in self.environment["tools"]
        ]
        return value

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(str(tool["name"]) for tool in self.environment["tools"])
