"""Program-form 任务生成的环境包边界。

v2 数据环境不再把真实状态写入 JSON：``environment.json`` 只声明
Record Set、Relationship 和 Filesystem Scope，真实状态位于 ``state/``。
工具可以位于独立 ``tools.json``，也兼容旧版将 ``tools`` 内联在
``environment.json`` 中的完整环境包。
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PUBLIC_TOOL_FIELDS = ("name", "description", "inputSchema", "outputSchema")
TOOL_FIELDS = (*PUBLIC_TOOL_FIELDS, "internal")
V2_ENVIRONMENT_FIELDS = (
    "schema_version",
    "environment_id",
    "name",
    "summary",
    "description",
    "record_sets",
    "relationships",
    "filesystem_scopes",
)
V1_ENVIRONMENT_FIELDS = (
    "schema_version",
    "environment_id",
    "name",
    "description",
    "resources",
    "rules",
    "tools",
)


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"无法读取{label}：{path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label}不是合法 JSON：{path}: {error}") from error


def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


@dataclass(frozen=True)
class CompleteEnvironmentPackage:
    """一个可供 TaskGen 和 Runtime 使用的环境包。

    ``environment`` 是纯环境声明，``tools`` 是完整可执行工具。
    两者分开保存，避免 v2 ``environment.json`` 因为附加 tools 而违反
    闭合 Schema。``state_root`` 是本次任务的基线状态目录。
    """

    package_root: Path
    environment_path: Path
    state_root: Path
    environment: dict[str, Any]
    tools: tuple[dict[str, Any], ...]
    tools_path: Path | None
    package_format: str

    @classmethod
    def load(cls, value: Path, *, tools_path: Path | None = None) -> "CompleteEnvironmentPackage":
        value = value.resolve()
        environment_path = value / "environment.json" if value.is_dir() else value
        if not environment_path.is_file():
            raise FileNotFoundError(f"找不到环境声明：{environment_path}")
        package_root = environment_path.parent
        raw_environment = _read_json(environment_path, label="environment.json")
        if not isinstance(raw_environment, dict):
            raise ValueError("environment.json 根节点必须是 object")

        if raw_environment.get("schema_version") == "2.0":
            environment = deepcopy(raw_environment)
            cls._validate_v2_environment(environment)
            cls._validate_upstream_receipt(package_root)
            state_root = package_root / "state"
            if not state_root.is_dir():
                raise ValueError(f"v2 环境包缺少 state/：{package_root}")
            cls._validate_v2_state_shape(environment, state_root)
            package_format = "v2"
            embedded_tools: Any = None
        else:
            # 兼容旧版完整环境，便于 ToolGen 过渡和历史任务重放。
            cls._validate_v1_shape(raw_environment)
            embedded_tools = raw_environment.get("tools")
            # v1 调用方（尤其历史 ToolGen）会从 package.environment
            # 直接读取 tools，因此内部继续保留完整对象。公开投影另行删除。
            environment = deepcopy(raw_environment)
            state_root = package_root / "workspace"
            if not state_root.is_dir():
                raise ValueError(f"旧版环境包缺少 workspace/：{package_root}")
            package_format = "v1"

        resolved_tools_path, raw_tools = cls._load_tools(
            package_root,
            explicit_path=tools_path,
            embedded_tools=embedded_tools,
        )
        tools = cls._validate_tools(raw_tools)
        return cls(
            package_root=package_root,
            environment_path=environment_path,
            state_root=state_root,
            environment=environment,
            tools=tuple(tools),
            tools_path=resolved_tools_path,
            package_format=package_format,
        )

    @staticmethod
    def _validate_upstream_receipt(package_root: Path) -> None:
        path = package_root / "validation.json"
        if not path.is_file():
            raise ValueError(f"v2 环境包缺少 DataGen validation.json：{package_root}")
        payload = _read_json(path, label="validation.json")
        if not isinstance(payload, dict) or payload.get("valid") is not True:
            errors = payload.get("errors") if isinstance(payload, dict) else None
            raise ValueError(f"DataGen 环境未通过校验：valid != true，errors={errors}")

    @staticmethod
    def _validate_v2_environment(environment: dict[str, Any]) -> None:
        missing = [field for field in V2_ENVIRONMENT_FIELDS if field not in environment]
        extra = sorted(set(environment) - set(V2_ENVIRONMENT_FIELDS))
        if missing:
            raise ValueError(f"v2 environment.json 缺少字段：{', '.join(missing)}")
        if extra:
            raise ValueError(f"v2 environment.json 包含额外字段：{', '.join(extra)}")
        schema_path = Path(__file__).resolve().parents[3] / "schemas" / "environment.schema.json"
        schema = _read_json(schema_path, label="environment schema")
        errors = _schema_errors(schema, environment)
        if errors:
            raise ValueError("v2 environment.json 不符合 Schema：" + " | ".join(errors[:20]))

    @staticmethod
    def _validate_v2_state_shape(environment: dict[str, Any], state_root: Path) -> None:
        symlinks = [path.relative_to(state_root).as_posix() for path in state_root.rglob("*") if path.is_symlink()]
        if symlinks:
            raise ValueError(f"v2 state/ 禁止符号链接：{', '.join(symlinks[:20])}")
        record_sets = environment.get("record_sets", [])
        database = state_root / "records.sqlite"
        if record_sets and not database.is_file():
            raise ValueError("v2 环境声明了 record_sets，但 state/records.sqlite 不存在")
        if not record_sets and database.exists():
            raise ValueError("v2 环境没有 record_sets，但存在 state/records.sqlite")
        scopes_root = state_root / "filesystem_scopes"
        expected = {str(item["scope_id"]) for item in environment.get("filesystem_scopes", [])}
        actual = {
            item.name for item in scopes_root.iterdir() if item.is_dir()
        } if scopes_root.is_dir() else set()
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "v2 Filesystem Scope 目录与声明不一致："
                f"缺少={missing}，额外={extra}"
            )

    @staticmethod
    def _validate_v1_shape(environment: dict[str, Any]) -> None:
        missing = [field for field in V1_ENVIRONMENT_FIELDS if field not in environment]
        if missing:
            raise ValueError(
                "环境既不是 schema_version=2.0，也不是可兼容的 v1 完整环境；"
                f"缺少：{', '.join(missing)}"
            )

    @staticmethod
    def _load_tools(
        package_root: Path,
        *,
        explicit_path: Path | None,
        embedded_tools: Any,
    ) -> tuple[Path | None, Any]:
        candidate = explicit_path.resolve() if explicit_path is not None else package_root / "tools.json"
        if candidate.is_file():
            payload = _read_json(candidate, label="tools.json")
            if isinstance(payload, dict) and set(payload) == {"tools"}:
                payload = payload["tools"]
            return candidate, payload
        if explicit_path is not None:
            raise FileNotFoundError(f"找不到指定的工具文件：{candidate}")
        if embedded_tools is not None:
            return None, embedded_tools
        raise ValueError(
            "环境包没有可执行工具：请在包根目录提供 tools.json，"
            "或通过 --tools-path 指定工具集。TaskGen 不会凭空猜测工具。"
        )

    @staticmethod
    def _validate_tools(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ValueError("工具集必须是非空 array")
        schema_path = Path(__file__).resolve().parents[3] / "schemas" / "validation" / "tool.schema.json"
        schema = _read_json(schema_path, label="tool schema")
        names: set[str] = set()
        tools: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"tools[{index}] 必须是 object")
            errors = _schema_errors(schema, item)
            if errors:
                raise ValueError(f"tools[{index}] 不符合工具契约：" + " | ".join(errors[:12]))
            name = str(item["name"])
            if name in names:
                raise ValueError(f"工具名重复：{name}")
            names.add(name)
            tools.append(deepcopy(item))
        return tools

    @property
    def workspace_root(self) -> Path:
        """兼容旧 ToolGen 调用；v2 时它就是 ``state_root``。"""
        return self.state_root

    @property
    def executable_environment(self) -> dict[str, Any]:
        """返回 Runtime 内部使用的环境+工具组合视图。"""
        return deepcopy(self.environment) | {"tools": [deepcopy(tool) for tool in self.tools]}

    def public_environment(self) -> dict[str, Any]:
        """返回任务设计 Agent/求解 Agent 可见的完整契约。"""
        public_declaration = {
            key: deepcopy(value)
            for key, value in self.environment.items()
            if key != "tools"
        }
        return {
            "environment": public_declaration,
            "tools": [
                {field: deepcopy(tool[field]) for field in PUBLIC_TOOL_FIELDS}
                for tool in self.tools
            ],
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(str(tool["name"]) for tool in self.tools)


def load_frozen_package(step1_path: Path) -> CompleteEnvironmentPackage:
    """从 Step 1 回执解析并加载冻结环境包。"""
    receipt = _read_json(step1_path.resolve(), label="step1_environment.json")
    if not isinstance(receipt, dict) or receipt.get("status") != "passed":
        raise ValueError("Step 1 环境回执无效")
    relative = Path(str(receipt.get("environment_package") or ""))
    package_root = relative if relative.is_absolute() else step1_path.resolve().parent / relative
    return CompleteEnvironmentPackage.load(package_root)
