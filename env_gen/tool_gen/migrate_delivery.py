"""Migrate legacy ToolGen deliveries to the explicit runtime contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utils.io import write_json


def _delivery_root(binding_path: Path, binding: dict[str, Any]) -> Path:
    package_path = binding.get("package_path")
    if not isinstance(package_path, str) or not package_path:
        raise ValueError(f"binding.package_path 无效：{binding_path}")
    root = binding_path.parent
    for _ in Path(package_path).parts:
        root = root.parent
    expected = (root / package_path).resolve()
    if expected != binding_path.parent.resolve():
        raise ValueError(f"binding.json 路径与 package_path 不一致：{binding_path}")
    return root.resolve()


def _runtime_for_package(package_root: Path, binding: dict[str, Any]) -> dict[str, Any]:
    """Infer only facts present in a legacy package; never invent a Docker image."""
    candidates = (
        package_root / "tool_generation/container_runtime.json",
        package_root / "environment/tool_generation/container_runtime.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("backend") != "docker":
                raise ValueError(f"Docker runtime 配置无效：{candidate}")
            return value
    profile = binding.get("software_profile")
    if isinstance(profile, str) and profile:
        return {"schema_version": "1.0", "backend": "python_profile", "profile_id": profile}
    return {"schema_version": "1.0", "backend": "host_python"}


def migrate_delivery(root: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Add runtime.json and binding.runtime_path to legacy packages.

    A legacy package with a Python software profile is explicitly marked as
    ``python_profile``.  Docker is selected only when a real container runtime
    receipt exists; an image name is never guessed during migration.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"交付根目录不存在：{root}")
    reports: list[dict[str, Any]] = []
    for binding_path in sorted(root.glob("environments/*/binding.json")):
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        if not isinstance(binding, dict):
            raise ValueError(f"binding.json 必须是 object：{binding_path}")
        delivery_root = _delivery_root(binding_path, binding)
        package_root = binding_path.parent.resolve()
        relative_runtime = f"{binding['package_path']}/runtime/runtime.json"
        runtime_path = (delivery_root / relative_runtime).resolve()
        if not runtime_path.is_relative_to(delivery_root):
            raise ValueError(f"runtime 路径越出交付根目录：{binding_path}")
        runtime = (
            json.loads(runtime_path.read_text(encoding="utf-8"))
            if runtime_path.is_file()
            else _runtime_for_package(package_root, binding)
        )
        if not isinstance(runtime, dict):
            raise ValueError(f"runtime.json 必须是 object：{runtime_path}")
        changed = not runtime_path.is_file() or binding.get("runtime_path") != relative_runtime
        reports.append({
            "package_id": binding.get("package_id"),
            "binding": str(binding_path),
            "runtime": str(runtime_path),
            "backend": runtime.get("backend"),
            "changed": changed,
        })
        if dry_run or not changed:
            continue
        write_json(runtime_path, runtime)
        binding["runtime_path"] = relative_runtime
        write_json(binding_path, binding)
    return reports


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="迁移旧 ToolGen 交付包到显式 runtime.json 契约")
    parser.add_argument("delivery", type=Path, help="包含 environments/ 的交付根目录")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(migrate_delivery(args.delivery, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
