"""Publish ToolGen outputs without coupling tools, environment state, and software."""

from __future__ import annotations

import json
import hashlib
import os
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
    package_root: Path
    tools_root: Path
    environment_root: Path
    software_mapping_root: Path
    runtime_root: Path
    binding_path: Path
    software_profile: str | None


def _copy_directory(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _replace_directory(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = staged.with_name(staged.name + ".previous")
    if destination.exists():
        destination.rename(previous)
    try:
        staged.rename(destination)
    except OSError:
        if previous.exists():
            previous.rename(destination)
        raise


def _software_build_artifact(relative: Path) -> bool:
    """Select build caches and extracted system documentation at known locations."""
    parts = relative.parts
    return bool(parts) and (
        parts[0] in {"cache", ".cache", "conda-pkgs"}
        or parts[0].endswith("-failed")
        or parts[:4] == ("system", "usr", "share", "doc")
    )


def _copy_software(source: Path, staged: Path) -> None:
    source = source.resolve()

    def ignore(directory: str, names: list[str]) -> list[str]:
        relative = Path(directory).relative_to(source)
        return [name for name in names if _software_build_artifact(relative / name)]

    shutil.copytree(source, staged, symlinks=True, ignore=ignore)
    links = [path for path in staged.rglob("*") if path.is_symlink()]
    for link in links:
        original = source / link.relative_to(staged)
        # Resolve relative links from the source location before moving the package.
        target = Path(os.path.abspath(original.parent / os.readlink(original)))
        if not original.exists():
            raise FileNotFoundError(f"软件运行依赖链接失效：{original} -> {target}")
        relative = target.relative_to(source) if target.is_relative_to(source) else None
        link.unlink()
        if relative is not None and _software_build_artifact(relative):
            # A runtime may link into a download cache; ship that required target.
            if target.is_dir():
                shutil.copytree(target, link)
            else:
                shutil.copy2(target, link)
        else:
            destination = staged / relative if relative is not None else target
            link.symlink_to(
                os.path.relpath(destination, link.parent) if relative is not None else destination,
                target_is_directory=target.is_dir(),
            )
    for link in links:
        if not link.exists():
            raise FileNotFoundError(f"交付软件链接不可访问：{link}")


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


def _publish_software_profile(
    source: Path,
    destination: Path,
    requirements: Path,
) -> None:
    """Publish one immutable profile without racing another environment."""

    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / destination.name
        _copy_software(source, staged)
        if requirements.is_file():
            shutil.copy2(requirements, staged / "requirements.txt")
        try:
            staged.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise


def _software_profile_id(software: dict[str, object]) -> str:
    profile = software.get("profile_id")
    if profile:
        return str(profile)
    plan = software.get("plan", {})
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "py-" + hashlib.sha256(payload).hexdigest()[:12]


def publish(result: ToolGenerationResult, output_root: Path) -> ToolDelivery:
    """Publish one completed environment into the shared ToolGen result layout."""
    output_root = output_root.resolve()
    environment_id = json.loads(
        result.environment_path.read_text(encoding="utf-8")
    )["environment_id"]
    package_id = result.package_root.name
    environments_parent = output_root / "environments"
    environments_parent.mkdir(parents=True, exist_ok=True)
    delivery_package_root = environments_parent / package_id
    tools_root = delivery_package_root / "tools"
    environment_root = delivery_package_root / "environment"
    software_mapping_root = delivery_package_root / "software"
    runtime_root = delivery_package_root / "runtime"
    source_package_root = result.package_root
    package_root = delivery_package_root
    environment_validation_path = _environment_validation_path(source_package_root)
    container_runtime_path = (
        source_package_root / "tool_generation/container_runtime.json"
    )
    container_runtime: dict[str, object] | None = None
    if container_runtime_path.is_file():
        value = json.loads(container_runtime_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("backend") != "docker":
            raise ValueError(
                f"container_runtime.json 必须声明 docker 后端：{container_runtime_path}"
            )
        container_runtime = value
    software = runtime_info(source_package_root)
    profile = (
        _software_profile_id(software)
        if software and container_runtime is None
        else None
    )
    software_source = (
        Path(str(software["root"])).resolve()
        if software and container_runtime is None
        else None
    )
    if software_source and not software_source.is_dir():
        raise ValueError(f"软件运行目录不存在：{software_source}")
    software_profile_root = (
        output_root / "software_profiles" / "profiles" / profile if profile else None
    )
    python_relative = "python/bin/python"
    if software and software.get("python") and software_source:
        launcher = Path(str(software["python"])).expanduser().absolute()
        python_path = launcher.parent.resolve() / launcher.name
        try:
            python_relative = python_path.relative_to(software_source).as_posix()
        except ValueError as error:
            raise ValueError(f"软件 Python 不在运行目录内：{python_path}") from error

    if software_source and software_profile_root:
        _publish_software_profile(
            software_source,
            software_profile_root,
            source_package_root / "tool_runtime/requirements.txt",
        )

    with tempfile.TemporaryDirectory(prefix=f".{package_id}-", dir=output_root) as temporary:
        staging = Path(temporary) / "package"
        staged_tools = staging / "tools"
        staged_environment = staging / "environment"
        staged_software = staging / "software"
        staged_runtime = staging / "runtime"
        staged_tools.mkdir(parents=True)
        staged_environment.mkdir()
        staged_software.mkdir()
        staged_runtime.mkdir()
        shutil.copy2(result.tools_path, staged_tools / "tools.json")
        for source, name in (
            (result.grounding_path, "tool_grounding.json"),
            (result.validation_path, "tool_validation.json"),
            (result.action_plan_path, "action_plan.json"),
        ):
            if source.is_file():
                shutil.copy2(source, staged_tools / name)

        for name in ("environment.json", "environment.md", "tool_runtime.json"):
            source = source_package_root / name
            if source.is_file():
                shutil.copy2(source, staged_environment / name)
        shutil.copy2(environment_validation_path, staged_environment / "validation.json")
        _copy_directory(source_package_root / "state", staged_environment / "state")
        _copy_directory(source_package_root / "provenance", staged_environment / "provenance")
        _copy_directory(source_package_root / "tool_runtime", staged_environment / "tool_runtime")
        write_json(
            staged_software / "profile.json",
            {
                "profile_id": profile,
                "profile_path": (
                    f"software_profiles/profiles/{profile}" if profile else None
                ),
                "python_path": (
                    f"software_profiles/profiles/{profile}/{python_relative}" if profile else None
                ),
                "requirements_path": (
                    f"software_profiles/profiles/{profile}/requirements.txt" if profile else None
                ),
            },
        )
        if container_runtime is not None:
            runtime = container_runtime
        elif profile:
            runtime = {
                "schema_version": "1.0",
                "backend": "python_profile",
                "profile_id": profile,
            }
        else:
            runtime = {"schema_version": "1.0", "backend": "host_python"}
        write_json(staged_runtime / "runtime.json", runtime)
        binding = {
            "schema_version": BINDING_SCHEMA_VERSION,
            "package_id": package_id,
            "environment_id": environment_id,
            "package_path": f"environments/{package_id}",
            "tools_path": f"environments/{package_id}/tools/tools.json",
            "tool_validation_path": f"environments/{package_id}/tools/tool_validation.json",
            "environment_path": f"environments/{package_id}/environment",
            "environment_validation_path": f"environments/{package_id}/environment/validation.json",
            "software_mapping_path": f"environments/{package_id}/software/profile.json",
            "software_profile": profile,
            "software_profile_path": (
                f"software_profiles/profiles/{profile}" if profile else None
            ),
            "runtime_path": f"environments/{package_id}/runtime/runtime.json",
        }
        write_json(staging / "binding.json", binding)
        _replace_directory(staging, package_root)

    binding_path = package_root / "binding.json"
    return ToolDelivery(
        package_root=package_root,
        tools_root=tools_root,
        environment_root=environment_root,
        software_mapping_root=software_mapping_root,
        runtime_root=runtime_root,
        binding_path=binding_path,
        software_profile=profile,
    )
