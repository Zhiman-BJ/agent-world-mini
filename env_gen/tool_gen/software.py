"""Prepare reusable Python and Node software profiles for ToolGen environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from utils.io import write_json


COMMON_MODULES_PATH = Path(__file__).with_name("common_modules.json")
DEFAULT_SHARED_ROOT = Path(
    os.environ.get("TOOLGEN_SHARED_SOFTWARE_ROOT", Path.home() / ".cache/agent-world-toolgen")
)


def _common_modules(shared_root: Path | None = None) -> dict[str, Any]:
    modules = json.loads(COMMON_MODULES_PATH.read_text(encoding="utf-8"))
    if shared_root is not None:
        catalog = shared_root / "catalog/common_modules.json"
        if not catalog.is_file() or json.loads(catalog.read_text(encoding="utf-8")) != modules:
            write_json(catalog, modules)
    return modules


def _package_spec(value: Any, *, ecosystem: str) -> str:
    if isinstance(value, str):
        spec = value.strip()
        if spec:
            return spec
    elif isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        version = str(value.get("version") or "").strip()
        if name:
            if not version:
                return name
            if ecosystem == "python":
                return name + (version if version[0] in "<>=!~" else "==" + version)
            return f"{name}@{version}"
    raise ValueError(f"无效的 {ecosystem} 软件包声明：{value!r}")


def expanded_packages(
    plan: dict[str, Any], *, shared_root: Path | None = None
) -> list[str]:
    modules = _common_modules(shared_root)
    packages = [
        _package_spec(item, ecosystem="python")
        for item in plan.get("python_packages", [])
    ]
    for name in plan.get("common_modules", []):
        if name not in modules:
            raise ValueError(f"未知通用软件模块：{name}")
        packages.extend(
            _package_spec(item, ecosystem="python")
            for item in modules[name].get("python_packages", [])
        )
    return list(dict.fromkeys(packages))


def expanded_node_packages(plan: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            _package_spec(item, ecosystem="node")
            for item in plan.get("node_packages", [])
        )
    )


def normalized_plan(
    plan: dict[str, Any], *, shared_root: Path | None = None
) -> dict[str, Any]:
    return {
        "python": str(plan.get("python") or f"{sys.version_info.major}.{sys.version_info.minor}"),
        "python_packages": sorted(expanded_packages(plan, shared_root=shared_root)),
        "node_packages": sorted(expanded_node_packages(plan)),
    }


def profile_id(plan: dict[str, Any], *, shared_root: Path | None = None) -> str:
    normalized = normalized_plan(plan, shared_root=shared_root)
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    python_tag = normalized["python"].replace(".", "")
    return f"py{python_tag}-{digest}"


def runtime_info(package_root: Path) -> dict[str, Any] | None:
    path = package_root / "tool_generation/software_environment.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def subprocess_environment(package_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [source_root, environment.get("PYTHONPATH")])
    )
    info = runtime_info(package_root)
    if info:
        root = Path(info["root"])
        executable_paths = [str(Path(info["python"]).parent)]
        node_bin = root / "node/node_modules/.bin"
        if node_bin.is_dir():
            executable_paths.append(str(node_bin))
            environment["NODE_PATH"] = str(root / "node/node_modules")
        environment["PATH"] = os.pathsep.join(
            [*executable_paths, environment.get("PATH", "")]
        )
        environment["VIRTUAL_ENV"] = info["prefix"]
        environment["TOOLGEN_SOFTWARE_ROOT"] = str(root)
    return environment


def _command(
    command: list[str],
    *,
    cwd: Path,
    log: Path,
    env: dict[str, str] | None = None,
) -> None:
    with log.open("a", encoding="utf-8") as output:
        output.write("\n" + json.dumps(command, ensure_ascii=False) + "\n")
        output.flush()
        result = subprocess.run(
            command, cwd=cwd, env=env, text=True, stdout=output, stderr=subprocess.STDOUT
        )
    if result.returncode:
        raise RuntimeError(f"软件准备失败（exit={result.returncode}），详情：{log}")


def prepare_software(
    package_root: Path,
    *,
    shared_root: Path | None = None,
    restore: bool = False,
) -> dict[str, Any]:
    """Create or reuse the profile requested by one environment's software plan."""
    package_root = package_root.resolve()
    output = package_root / "tool_generation"
    output.mkdir(exist_ok=True)
    plan_path = package_root / "tool_runtime.json" if restore else output / "software_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shared_root = (shared_root or DEFAULT_SHARED_ROOT).resolve()
    packages = expanded_packages(plan, shared_root=shared_root)
    node_packages = expanded_node_packages(plan)
    normalized = normalized_plan(plan, shared_root=shared_root)
    identifier = profile_id(plan, shared_root=shared_root)
    root = shared_root / "profiles" / identifier
    root.mkdir(parents=True, exist_ok=True)
    log = output / "software_install.log"
    metadata_path = root / "profile.json"
    previous_profile = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.is_file()
        else None
    )
    python_path = root / "python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    if previous_profile and previous_profile.get("normalized_plan") == normalized and python_path.is_file():
        info = {
            "profile_id": identifier,
            "python": str(python_path),
            "prefix": str(root / "python"),
            "root": str(root),
            "shared_root": str(shared_root),
            "plan": plan,
        }
        write_json(output / "software_environment.json", info)
        write_json(package_root / "tool_runtime.json", plan)
        return info

    cache = shared_root / "cache"
    (cache / "uv").mkdir(parents=True, exist_ok=True)
    (cache / "npm").mkdir(parents=True, exist_ok=True)
    bootstrap_env = dict(os.environ)
    bootstrap_env["UV_CACHE_DIR"] = str(cache / "uv")
    bootstrap_env["npm_config_cache"] = str(cache / "npm")
    uv = shutil.which("uv")
    if uv:
        uv_command = [uv]
    else:
        bootstrap = shared_root / "bootstrap"
        if not (bootstrap / "uv").is_dir():
            _command(
                [sys.executable, "-m", "pip", "install", "--target", str(bootstrap), "uv"],
                cwd=shared_root,
                log=log,
            )
        bootstrap_env["PYTHONPATH"] = str(bootstrap)
        uv_command = [sys.executable, "-m", "uv"]

    if not python_path.is_file():
        _command(
            [*uv_command, "venv", "--seed", "--python", normalized["python"], str(root / "python")],
            cwd=root,
            log=log,
            env=bootstrap_env,
        )
    locked = package_root / "tool_runtime/requirements.txt"
    install = ["-r", str(locked)] if restore and locked.is_file() else ["jsonschema>=4.18", *packages]
    _command(
        [*uv_command, "pip", "install", "--python", str(python_path), *install],
        cwd=root,
        log=log,
        env=bootstrap_env,
    )

    recipe = package_root / "tool_runtime"
    recipe.mkdir(exist_ok=True)
    frozen = subprocess.check_output([str(python_path), "-m", "pip", "freeze"], text=True)
    (recipe / "requirements.txt").write_text(frozen, encoding="utf-8")
    (root / "requirements.txt").write_text(frozen, encoding="utf-8")

    if node_packages:
        node_root = root / "node"
        node_root.mkdir(exist_ok=True)
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("所选软件需要 Node.js/npm；安装日志与软件计划已保留")
        lock = recipe / "package-lock.json"
        if restore and lock.is_file():
            shutil.copy2(recipe / "package.json", node_root / "package.json")
            shutil.copy2(lock, node_root / "package-lock.json")
            command = [npm, "ci", "--no-audit", "--no-fund"]
        else:
            if not (node_root / "package.json").is_file():
                write_json(node_root / "package.json", {"private": True})
            command = [npm, "install", "--save-exact", "--no-audit", "--no-fund", *node_packages]
        _command(command, cwd=node_root, log=log, env=bootstrap_env)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(node_root / name, recipe / name)

    metadata = {
        "profile_id": identifier,
        "normalized_plan": normalized,
        "python": str(python_path),
        "prefix": str(root / "python"),
        "root": str(root),
    }
    write_json(metadata_path, metadata)
    info = {**metadata, "shared_root": str(shared_root), "plan": plan}
    write_json(output / "software_environment.json", info)
    write_json(package_root / "tool_runtime.json", plan)
    return info


def validate_in_runtime(
    package_root: Path, drafts: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    info = runtime_info(package_root)
    if not info or Path(info["prefix"]).resolve() == Path(sys.prefix).resolve():
        return None
    output = package_root / "tool_generation"
    request = output / "runtime_validation_input.json"
    response = output / "runtime_validation_output.json"
    write_json(request, {"package_root": str(package_root), "drafts": drafts})
    response.unlink(missing_ok=True)
    _command(
        [info["python"], "-m", "env_gen.tool_gen.software", "validate", str(request), str(response)],
        cwd=package_root,
        log=output / "runtime_validation.log",
        env=subprocess_environment(package_root),
    )
    return json.loads(response.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="准备和使用 ToolGen 环境的软件依赖")
    parser.add_argument("action", choices=["prepare", "exec", "validate"])
    parser.add_argument("package", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "validate":
        from .compiler import ToolGenerator

        document = json.loads(args.package.read_text(encoding="utf-8"))
        root = Path(document["package_root"])
        environment = json.loads((root / "environment.json").read_text(encoding="utf-8"))
        reports = ToolGenerator(None)._validate_local(root, environment, document["drafts"])
        write_json(Path(args.arguments[0]), reports)
    else:
        info = prepare_software(args.package, restore=True)
        if args.action == "exec":
            command = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            raise SystemExit(
                subprocess.call([info["python"], *command], env=subprocess_environment(args.package))
            )
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
