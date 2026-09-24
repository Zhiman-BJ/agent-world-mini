"""Build reusable software images for ToolGen professional environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Any

from utils.io import write_json

from .software import SYSTEM_EXECUTABLES, expanded_packages


IMAGE_RECIPE_VERSION = "2"


def _load_plan(package_root: Path) -> dict[str, Any]:
    candidates = (
        package_root / "tool_generation/software_plan.json",
        package_root / "tool_runtime.json",
    )
    for path in candidates:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"软件计划必须是 JSON object：{path}")
            return value
    raise FileNotFoundError(f"没有找到软件计划：{package_root}")


def _node_packages(plan: dict[str, Any]) -> list[str]:
    normalized = []
    for item in plan.get("node_packages", []) or []:
        if isinstance(item, str):
            normalized.append(item)
        elif isinstance(item, dict) and item.get("name"):
            suffix = "@" + str(item["version"]) if item.get("version") else ""
            normalized.append(str(item["name"]) + suffix)
        else:
            raise ValueError("node_packages 包含无效条目")
    return normalized


def _container_spec(plan: dict[str, Any]) -> dict[str, Any]:
    container = plan.get("container")
    if not isinstance(container, dict):
        raise ValueError("需要 Docker 的环境必须在软件计划中提供 container 配置")
    version = str(plan.get("python") or "3.11")
    base_image = str(
        container.get("base_image") or f"python:{version}-slim-bookworm"
    )
    apt_packages = container.get("apt_packages", [])
    if not isinstance(apt_packages, list) or not all(
        isinstance(item, str) and item for item in apt_packages
    ):
        raise ValueError("container.apt_packages 必须是软件包名称数组")
    environment = container.get("environment", {})
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("container.environment 必须是字符串键值 object")
    executable_groups: list[list[str]] = []
    for item in plan.get("system_packages", []) or []:
        name = str(item.get("name") if isinstance(item, dict) else item).strip()
        normalized = name.lower().replace("_", " ").replace("-", " ")
        if isinstance(item, dict) and item.get("executable"):
            candidates = [str(item["executable"])]
        else:
            candidates = list(SYSTEM_EXECUTABLES.get(normalized, ()))
        if candidates:
            executable_groups.append(candidates)
    return {
        "recipe_version": IMAGE_RECIPE_VERSION,
        "base_image": base_image,
        "apt_packages": sorted(set(apt_packages)),
        "python_packages": sorted(set(expanded_packages(plan))),
        "node_packages": sorted(set(_node_packages(plan))),
        "environment": dict(sorted(environment.items())),
        "system_executables": executable_groups,
    }


def image_name(plan: dict[str, Any]) -> str:
    payload = json.dumps(
        _container_spec(plan), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return "agentworld/tool-runtime:" + hashlib.sha256(payload).hexdigest()[:12]


def dockerfile(plan: dict[str, Any]) -> str:
    """Render one deterministic image recipe from an explicit container plan."""
    spec = _container_spec(plan)
    base_image = spec["base_image"]
    apt_packages = spec["apt_packages"]
    environment = spec["environment"]

    lines = [
        f"FROM {base_image}",
        "ENTRYPOINT []",
        "ENV DEBIAN_FRONTEND=noninteractive",
    ]
    if apt_packages:
        names = " ".join(shlex.quote(item) for item in apt_packages)
        lines.append(
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            f"{names} && rm -rf /var/lib/apt/lists/*"
        )
    lines.extend(
        [
            "COPY requirements.txt /tmp/toolgen-requirements.txt",
            "RUN python -m pip install --no-cache-dir -r /tmp/toolgen-requirements.txt",
            "RUN mkdir -p /opt/tool-software/bin /opt/tool-software/python/bin "
            "&& ln -sf $(command -v python) /opt/tool-software/python/bin/python",
        ]
    )
    for candidates in spec["system_executables"]:
        names = " ".join(shlex.quote(item) for item in candidates)
        links = " ".join(
            f"ln -sf \"$resolved\" /opt/tool-software/bin/{shlex.quote(item)};"
            for item in candidates
        )
        lines.append(
            "RUN resolved=; for candidate in "
            f"{names}; do resolved=$(command -v \"$candidate\" || true); "
            "[ -z \"$resolved\" ] || break; done; "
            f"if [ -z \"$resolved\" ]; then exit 1; fi; {links}"
        )
    node_packages = spec["node_packages"]
    if node_packages:
        packages = " ".join(shlex.quote(item) for item in node_packages)
        lines.append(
            "RUN npm install --prefix /opt/tool-software/node "
            f"--no-audit --no-fund {packages}"
        )
    lines.append("ENV TOOLGEN_SOFTWARE_ROOT=/opt/tool-software")
    for key, value in sorted(environment.items()):
        lines.append(f"ENV {key}={json.dumps(value)}")
    lines.extend(["WORKDIR /workspace", "CMD [\"python\", \"--version\"]", ""])
    return "\n".join(lines)


def build_image(
    package_root: Path,
    *,
    image: str | None = None,
    docker_command: str = "docker",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Build an image once and save the runtime receipt consumed by delivery."""
    package_root = package_root.resolve()
    plan = _load_plan(package_root)
    target_image = image or image_name(plan)
    executable = shutil.which(docker_command)
    if executable is None:
        raise RuntimeError(f"找不到 Docker 命令：{docker_command}")
    timeout = timeout_seconds or int(
        os.environ.get("TOOLGEN_DOCKER_BUILD_TIMEOUT_SECONDS", "3600")
    )
    if timeout < 1:
        raise ValueError("Docker 构建超时必须大于 0 秒")
    exists = subprocess.run(
        [executable, "image", "inspect", target_image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    ).returncode == 0
    if not exists:
        requirements = ["jsonschema>=4.18", *expanded_packages(plan)]
        with tempfile.TemporaryDirectory(prefix="toolgen-image-") as temporary:
            context = Path(temporary)
            (context / "Dockerfile").write_text(dockerfile(plan), encoding="utf-8")
            (context / "requirements.txt").write_text(
                "\n".join(dict.fromkeys(requirements)) + "\n",
                encoding="utf-8",
            )
            try:
                subprocess.run(
                    [executable, "build", "--tag", target_image, str(context)],
                    check=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"Docker 镜像构建超过 {timeout} 秒：{target_image}"
                ) from error
    image_id = subprocess.check_output(
        [executable, "image", "inspect", "--format", "{{.Id}}", target_image],
        text=True,
        timeout=60,
    ).strip()
    runtime = {
        "schema_version": "1.0",
        "backend": "docker",
        "image": target_image,
        "delivery_mount": "/delivery",
        "code_mount": "/opt/agent-world",
        "software_root": "/opt/tool-software",
        "python_command": "python",
    }
    output = package_root / "tool_generation"
    output.mkdir(exist_ok=True)
    write_json(output / "container_runtime.json", runtime)
    write_json(
        output / "container_image.json",
        {"image": target_image, "image_id": image_id},
    )
    return runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="为一个 ToolGen 软件计划构建可复用镜像")
    parser.add_argument("package", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--timeout-seconds", type=int)
    arguments = parser.parse_args(argv)
    runtime = build_image(
        arguments.package,
        image=arguments.image,
        docker_command=arguments.docker_command,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(runtime, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
