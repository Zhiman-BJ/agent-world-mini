"""Prepare environment-scoped Python/Node dependencies and launch their runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from utils.io import write_json


# Domain packages are selected from official documentation for each environment.
COMMON_MODULES = {
    "numerical": {"purpose": "数组、统计与数值计算", "python_packages": ["numpy", "scipy"]},
    "tables": {"purpose": "表格与 Parquet 文件处理", "python_packages": ["pandas", "pyarrow"]},
    "pdf": {"purpose": "读取、搜索和渲染 PDF", "python_packages": ["pymupdf"]},
    "images": {"purpose": "读取和处理图片", "python_packages": ["pillow"]},
    "hdf5": {"purpose": "读取和处理 HDF5 数据", "python_packages": ["h5py"]},
    "yaml": {"purpose": "读取和写入 YAML 配置", "python_packages": ["PyYAML"]},
}


def expanded_packages(plan: dict[str, Any]) -> list[str]:
    packages = []
    for item in plan.get("python_packages", []):
        if isinstance(item, str):
            packages.append(item)
        elif isinstance(item, dict) and item.get("name"):
            name = str(item["name"])
            version = item.get("version")
            packages.append(package_requirement(name, version))
    for name in plan.get("common_modules", []):
        if isinstance(name, dict):
            packages.extend(expanded_packages({"python_packages": [name]}))
            continue
        if name in COMMON_MODULES:
            packages.extend(COMMON_MODULES[name]["python_packages"])
        elif name not in sys.stdlib_module_names:
            packages.append(name)
    return list(dict.fromkeys(packages))


def package_requirement(name: str, version: Any) -> str:
    if not version:
        return name
    version = str(version).strip()
    if version[0].isdigit():
        return f"{name}=={version}"
    if version.startswith((">", "<", "=", "!", "~")):
        return name + version
    raise ValueError(f"{name} 的版本 {version!r} 不是版本号或范围，请核对软件计划")


def runtime_info(package_root: Path) -> dict[str, Any] | None:
    path = package_root / "tool_generation/software_environment.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def subprocess_environment(package_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, environment.get("PYTHONPATH")]))
    info = runtime_info(package_root)
    if info:
        environment["PATH"] = os.pathsep.join([str(Path(info["python"]).parent), environment.get("PATH", "")])
        environment["VIRTUAL_ENV"] = info["prefix"]
        environment["TOOLGEN_SOFTWARE_ROOT"] = info["root"]
    return environment


def _command(command: list[str], *, cwd: Path, log: Path, env: dict[str, str] | None = None) -> str:
    with log.open("a", encoding="utf-8") as output:
        output.write("\n" + json.dumps(command, ensure_ascii=False) + "\n")
        output.flush()
        result = subprocess.run(command, cwd=cwd, env=env, text=True, stdout=output, stderr=subprocess.STDOUT, timeout=900)
    if result.returncode:
        raise RuntimeError(f"软件准备失败（exit={result.returncode}），详情：{log}")
    return ""


def prepare_software(package_root: Path, *, restore: bool = False) -> dict[str, Any]:
    """Install requested packages; keep downloads cached by pip/uv/npm."""
    package_root = package_root.resolve()
    output = package_root / "tool_generation"
    output.mkdir(exist_ok=True)
    plan_path = package_root / "tool_runtime.json" if restore else output / "software_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    packages = expanded_packages(plan)
    node_packages = [item if isinstance(item, str) else item["name"] + ("@" + item["version"] if item.get("version") else "") for item in plan.get("node_packages", [])]
    root = output / "software"
    root.mkdir(exist_ok=True)
    log = output / "software_install.log"
    previous = runtime_info(package_root)
    if previous and previous.get("plan") == plan and Path(previous["python"]).is_file():
        return previous

    python = sys.executable
    prefix = sys.prefix
    version = plan.get("python")
    if packages or version:
        # uv can obtain the requested Python without changing the host interpreter.
        uv = shutil.which("uv")
        bootstrap_env = dict(os.environ)
        bootstrap_env["UV_CACHE_DIR"] = str(root / "cache/uv")
        bootstrap_env["UV_PYTHON_INSTALL_DIR"] = str(root / "interpreters")
        if uv:
            uv_command = [uv]
        else:
            bootstrap = root / "bootstrap"
            if not (bootstrap / "uv").is_dir():
                _command([sys.executable, "-m", "pip", "install", "--target", str(bootstrap), "uv"], cwd=root, log=log)
            bootstrap_env["PYTHONPATH"] = str(bootstrap)
            uv_command = [sys.executable, "-m", "uv"]
        requested = version or f"{sys.version_info.major}.{sys.version_info.minor}"
        venv = root / ("python-" + str(requested).replace("/", "-"))
        python_path = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python_path.is_file():
            _command([*uv_command, "venv", "--seed", "--python", version or f"{sys.version_info.major}.{sys.version_info.minor}", str(venv)], cwd=root, log=log, env=bootstrap_env)
        python, prefix = str(python_path), str(venv)
        locked = package_root / "tool_runtime/requirements.txt"
        install = ["-r", str(locked)] if restore and locked.is_file() else ["jsonschema>=4.18", *packages]
        _command([*uv_command, "pip", "install", "--python", python, *install], cwd=root, log=log, env=bootstrap_env)

    recipe = package_root / "tool_runtime"
    recipe.mkdir(exist_ok=True)
    if packages or version:
        frozen = subprocess.check_output([python, "-m", "pip", "freeze"], text=True)
        (recipe / "requirements.txt").write_text(frozen, encoding="utf-8")
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
        _command(command, cwd=node_root, log=log)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(node_root / name, recipe / name)

    info = {"python": python, "prefix": prefix, "root": str(root), "plan": plan}
    write_json(output / "software_environment.json", info)
    write_json(package_root / "tool_runtime.json", plan)
    return info


def validate_in_runtime(package_root: Path, drafts: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    info = runtime_info(package_root)
    if not info or Path(info["prefix"]).absolute() == Path(sys.prefix).absolute():
        return None
    output = package_root / "tool_generation"
    request, response = output / "runtime_validation_input.json", output / "runtime_validation_output.json"
    write_json(request, {"package_root": str(package_root), "drafts": drafts})
    response.unlink(missing_ok=True)
    _command([info["python"], "-m", "env_gen.tool_gen.software", "validate", str(request), str(response)],
             cwd=package_root, log=output / "runtime_validation.log", env=subprocess_environment(package_root))
    return json.loads(response.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="准备和使用 ToolGen 环境的软件依赖")
    parser.add_argument("action", choices=["prepare", "install", "exec", "validate"])
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
        info = prepare_software(args.package, restore=args.action != "install")
        if args.action == "exec":
            command = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            raise SystemExit(subprocess.call([info["python"], *command], env=subprocess_environment(args.package)))
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
