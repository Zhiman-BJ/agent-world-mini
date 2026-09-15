"""Publish ToolGen outputs without coupling tools, environment state, and software."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from utils.io import write_json

from .compiler import ToolGenerationResult
from .software import runtime_info


BINDING_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ToolDelivery:
    tools_root: Path
    environment_root: Path
    binding_path: Path
    software_profile: str | None


def _copy_directory(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _replace_directory(staged: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    staged.rename(destination)


def _environment_validation_path(package_root: Path) -> Path:
    path = package_root / "validation.json"
    if not path.is_file():
        raise ValueError(f"DataGen 环境缺少 validation.json：{package_root}")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"DataGen validation.json 不是合法 JSON：{path}: {error}") from error
    if not isinstance(receipt, dict) or receipt.get("valid") is not True:
        raise ValueError(f"DataGen 环境未通过校验：{path}")
    return path


def publish(result: ToolGenerationResult, output_root: Path) -> ToolDelivery:
    """Publish one completed environment into the shared ToolGen result layout."""
    output_root = output_root.resolve()
    environment_id = json.loads(
        result.environment_path.read_text(encoding="utf-8")
    )["environment_id"]
    package_id = result.package_root.name
    tools_parent = output_root / "tools"
    environments_parent = output_root / "environments"
    tools_parent.mkdir(parents=True, exist_ok=True)
    environments_parent.mkdir(parents=True, exist_ok=True)
    tools_root = tools_parent / package_id
    environment_root = environments_parent / package_id
    package_root = result.package_root
    environment_validation_path = _environment_validation_path(package_root)
    software = runtime_info(package_root)
    profile = str(software["profile_id"]) if software else None

    with tempfile.TemporaryDirectory(prefix=f".{package_id}-", dir=output_root) as temporary:
        staging = Path(temporary)
        staged_tools = staging / "tools"
        staged_environment = staging / "environment"
        staged_tools.mkdir()
        staged_environment.mkdir()
        shutil.copy2(result.tools_path, staged_tools / "tools.json")
        for source, name in (
            (result.grounding_path, "tool_grounding.json"),
            (result.validation_path, "tool_validation.json"),
            (result.action_plan_path, "action_plan.json"),
        ):
            if source.is_file():
                shutil.copy2(source, staged_tools / name)

        for name in ("environment.json", "environment.md", "tool_runtime.json"):
            source = package_root / name
            if source.is_file():
                shutil.copy2(source, staged_environment / name)
        shutil.copy2(environment_validation_path, staged_environment / "validation.json")
        _copy_directory(package_root / "state", staged_environment / "state")
        _copy_directory(package_root / "provenance", staged_environment / "provenance")
        _copy_directory(package_root / "tool_runtime", staged_environment / "tool_runtime")
        if software:
            metadata_dir = staged_environment / "tool_generation"
            metadata_dir.mkdir(exist_ok=True)
            write_json(metadata_dir / "software_environment.json", software)
        _replace_directory(staged_tools, tools_root)
        _replace_directory(staged_environment, environment_root)

    binding_path = output_root / "bindings" / f"{package_id}.json"
    binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "package_id": package_id,
        "environment_id": environment_id,
        "tools_path": f"tools/{package_id}/tools.json",
        "tool_validation_path": f"tools/{package_id}/tool_validation.json",
        "environment_path": f"environments/{package_id}",
        "environment_validation_path": f"environments/{package_id}/validation.json",
        "software_profile": profile,
        "software_profile_path": (
            f"software_profiles/profiles/{profile}" if profile else None
        ),
    }
    write_json(binding_path, binding)
    return ToolDelivery(tools_root, environment_root, binding_path, profile)
