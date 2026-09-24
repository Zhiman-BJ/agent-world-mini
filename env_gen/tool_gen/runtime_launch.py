"""Build process launch settings for local Python and Docker runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from .delivery_contract import DeliveryPackage
from .software import runtime_environment


@dataclass(frozen=True)
class StdioLaunch:
    command: Path
    arguments: tuple[str, ...]
    environment: dict[str, str]


def local_stdio_launch(
    delivery: DeliveryPackage,
    *,
    server_path: Path,
    arguments: list[str],
    writable_paths: tuple[Path, ...] = (),
) -> StdioLaunch:
    """Launch an adapter with the delivery's ordinary Python software profile."""
    command = delivery.python_path or Path(os.sys.executable).absolute()
    environment = {"PYTHONPATH": str(server_path.resolve().parents[2])}
    if delivery.software_root is not None:
        prepared = runtime_environment(delivery.software_root, os.environ)
        for name in (
            "TOOLGEN_SOFTWARE_ROOT",
            "PATH",
            "LD_LIBRARY_PATH",
            "PETSC_DIR",
            "PETSC_ARCH",
            "SLEPC_DIR",
        ):
            if prepared.get(name):
                environment[name] = prepared[name]
    return StdioLaunch(command, tuple(arguments), environment)


def docker_stdio_launch(
    delivery: DeliveryPackage,
    *,
    server_path: Path,
    arguments: list[str],
    writable_paths: tuple[Path, ...] = (),
) -> StdioLaunch:
    """Launch the adapter in the reusable image selected by runtime.json."""
    runtime = delivery.runtime
    if runtime.get("backend") != "docker":
        raise ValueError("交付包没有选择 Docker 运行后端")
    project_root = server_path.resolve().parents[2]
    delivery_mount = str(runtime["delivery_mount"])
    code_mount = str(runtime["code_mount"])
    translated: list[str] = []
    extra_mounts: dict[Path, str] = {}
    writable = {
        path.expanduser().resolve()
        for path in writable_paths
    }
    for item in arguments:
        candidate = Path(item).expanduser()
        resolved_candidate = candidate.resolve() if candidate.is_absolute() else None
        if resolved_candidate in writable:
            source = resolved_candidate.parent
            target_root = extra_mounts.setdefault(
                source, f"/external/{len(extra_mounts)}"
            )
            translated.append(f"{target_root}/{resolved_candidate.name}")
        elif candidate.is_absolute() and candidate.is_relative_to(delivery.delivery_root):
            relative = candidate.relative_to(delivery.delivery_root).as_posix()
            translated.append(f"{delivery_mount}/{relative}")
        elif candidate.is_absolute() and candidate.is_relative_to(project_root):
            relative = candidate.relative_to(project_root).as_posix()
            translated.append(f"{code_mount}/{relative}")
        elif candidate.is_absolute():
            source = candidate.parent.resolve()
            target_root = extra_mounts.setdefault(
                source, f"/external/{len(extra_mounts)}"
            )
            translated.append(f"{target_root}/{candidate.name}")
        else:
            translated.append(item)

    docker = shutil.which("docker") or "docker"
    command = [
        "run",
        "--rm",
        "-i",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,source={delivery.delivery_root},target={delivery_mount},readonly",
        "--mount",
        f"type=bind,source={project_root},target={code_mount},readonly",
    ]
    for source, target in extra_mounts.items():
        command.extend(
            ["--mount", f"type=bind,source={source},target={target}"]
        )
    command.extend(
        [
            "--env",
            f"TOOLGEN_SOFTWARE_ROOT={runtime['software_root']}",
            str(runtime["image"]),
            str(runtime["python_command"]),
            *translated,
        ]
    )
    return StdioLaunch(Path(docker), tuple(command), {})


def stdio_launch(
    delivery: DeliveryPackage,
    *,
    server_path: Path,
    arguments: list[str],
    writable_paths: tuple[Path, ...] = (),
) -> StdioLaunch:
    if delivery.runtime.get("backend") == "docker":
        return docker_stdio_launch(
            delivery,
            server_path=server_path,
            arguments=arguments,
            writable_paths=writable_paths,
        )
    return local_stdio_launch(
        delivery,
        server_path=server_path,
        arguments=arguments,
        writable_paths=writable_paths,
    )
