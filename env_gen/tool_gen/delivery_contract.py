"""Load and validate one published ToolGen delivery package."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from .runtime import SCHEMA_ROOT, ToolPackage


@dataclass(frozen=True)
class DeliveryPackage:
    binding_path: Path
    delivery_root: Path
    binding: dict[str, Any]
    tool_document: dict[str, Any]
    package: ToolPackage
    software_root: Path | None
    python_path: Path | None
    runtime: dict[str, Any]


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
    schema = _read_object(
        SCHEMA_ROOT / "toolgen_delivery_binding.schema.json",
        "ToolGen binding Schema",
    )
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


def _validate_runtime(runtime: dict[str, Any]) -> None:
    schema = _read_object(
        SCHEMA_ROOT / "toolgen_runtime.schema.json",
        "ToolGen runtime Schema",
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(runtime),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        details = " | ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in errors[:12]
        )
        raise ValueError(f"runtime.json 不符合交付契约：{details}")


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
        relative = _relative_path(mapping["python_path"], "software.python_path")
        launcher = delivery_root / Path(*relative.parts)
        python_path = launcher.parent.resolve() / launcher.name
        if software_root is None or not python_path.parent.is_relative_to(software_root):
            raise ValueError("软件 Python 必须位于其 software profile 内")
        if not python_path.is_file():
            raise ValueError(f"软件 Profile Python 不存在：{python_path}")
    if software_root is not None and python_path is None:
        raise ValueError("软件 Profile 映射缺少 python_path")
    if software_root is None and python_path is not None:
        raise ValueError("未绑定软件 Profile 时 python_path 必须为 null")

    if binding.get("runtime_path") is not None:
        runtime_path = _resolve_delivery_path(
            delivery_root,
            binding["runtime_path"],
            "binding.runtime_path",
        )
        runtime = _read_object(runtime_path, "运行后端配置")
        _validate_runtime(runtime)
    elif software_root is not None:
        runtime = {
            "schema_version": "1.0",
            "backend": "python_profile",
            "profile_id": str(binding["software_profile"]),
        }
    else:
        runtime = {"schema_version": "1.0", "backend": "host_python"}
    if runtime["backend"] == "python_profile":
        if runtime.get("profile_id") != binding.get("software_profile"):
            raise ValueError("runtime.profile_id 与 binding.software_profile 不一致")
        if python_path is None:
            raise ValueError("python_profile 运行后端缺少可执行 Python")

    return DeliveryPackage(
        binding_path=binding_path,
        delivery_root=delivery_root,
        binding=binding,
        tool_document=tool_document,
        package=package,
        software_root=software_root,
        python_path=python_path,
        runtime=runtime,
    )
