"""Step 0: validate a ToolGen delivery binding and freeze its environment."""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils.environment import CompleteEnvironmentPackage
from .utils.io import read_json, write_json
from .utils.schema import Draft202012Validator
from .utils.tool_runtime import compact_state_snapshot, snapshot_state


@dataclass(frozen=True)
class ToolGenDelivery:
    """Resolved and validated paths from one ToolGen delivery binding."""

    delivery_root: Path
    binding_path: Path
    binding: dict[str, Any]
    package_path: Path
    environment_path: Path
    environment_validation_path: Path
    tools_path: Path
    tool_validation_path: Path
    software_mapping_path: Path
    software_profile_path: Path | None
    profile_python: Path | None

    @classmethod
    def load(
        cls,
        binding_path: Path,
        *,
        delivery_root: Path | None = None,
    ) -> "ToolGenDelivery":
        binding_path = binding_path.expanduser().resolve()
        root = (
            delivery_root.expanduser().resolve()
            if delivery_root is not None
            else binding_path.parent.parent
        )
        if not binding_path.is_file():
            raise FileNotFoundError(f"找不到 ToolGen binding：{binding_path}")
        binding = read_json(binding_path)
        schema = read_json(
            Path(__file__).resolve().parent
            / "schemas"
            / "toolgen_delivery_binding.schema.json"
        )
        errors = [
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in Draft202012Validator(schema).iter_errors(binding)
        ]
        if errors:
            raise ValueError("ToolGen binding 不符合契约：" + " | ".join(errors[:20]))
        def resolve(field: str, *, directory: bool = False) -> Path:
            relative = Path(binding[field])
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise ValueError(f"binding.{field} 越出 delivery_root") from error
            exists = target.is_dir() if directory else target.is_file()
            if not exists:
                kind = "目录" if directory else "文件"
                raise FileNotFoundError(f"binding.{field} 指向的{kind}不存在：{target}")
            return target

        package_path = resolve("package_path", directory=True)
        if binding_path.parent != package_path:
            raise ValueError("binding.json 必须位于 binding.package_path 指向的交付单元中")
        if package_path.name != binding["package_id"]:
            raise ValueError("binding.package_path 的目录名必须等于 package_id")
        environment_path = resolve("environment_path", directory=True)
        environment_validation_path = resolve("environment_validation_path")
        tools_path = resolve("tools_path")
        tool_validation_path = resolve("tool_validation_path")
        software_mapping_path = resolve("software_mapping_path")
        if environment_path.parent != package_path:
            raise ValueError("binding.environment_path 必须位于 binding.package_path 中")
        if tools_path.parent.parent != package_path:
            raise ValueError("binding.tools_path 必须位于 binding.package_path 中")
        if software_mapping_path.parent.parent != package_path:
            raise ValueError("binding.software_mapping_path 必须位于 binding.package_path 中")
        if environment_validation_path != (environment_path / "validation.json").resolve():
            raise ValueError("binding.environment_validation_path 必须指向环境包内 validation.json")

        environment = read_json(environment_path / "environment.json")
        tools_document = read_json(tools_path)
        if environment.get("environment_id") != binding["environment_id"]:
            raise ValueError("binding.environment_id 与 environment.json 不一致")
        if tools_document.get("environment_id") != binding["environment_id"]:
            raise ValueError("binding.environment_id 与 tools.json 不一致")
        tools = tools_document.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ValueError("ToolGen tools.json.tools 必须是非空数组")
        public_fields = {
            "name",
            "description",
            "usageConditions",
            "inputSchema",
            "outputSchema",
        }
        incomplete = [
            str(tool.get("name") or index)
            for index, tool in enumerate(tools)
            if not isinstance(tool, dict) or not public_fields <= set(tool)
        ]
        if incomplete:
            raise ValueError(
                "正式工具缺少公开契约字段：" + ", ".join(incomplete)
            )

        validation = read_json(environment_validation_path)
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            raise ValueError("binding 对应的 DataGen validation.json 未通过")
        tool_validation = read_json(tool_validation_path)
        if tool_validation.get("environment_id") != binding["environment_id"]:
            raise ValueError("tool_validation.environment_id 与 binding 不一致")
        reports = {
            item.get("tool"): item
            for item in tool_validation.get("reports", [])
            if isinstance(item, dict) and isinstance(item.get("tool"), str)
        }
        rejected = [
            str(tool.get("name"))
            for tool in tools
            if reports.get(tool.get("name"), {}).get("status") != "passed"
        ]
        if rejected:
            raise ValueError(
                "正式 tools.json 包含未通过实际执行校验的工具：" + ", ".join(rejected)
            )

        profile_path: Path | None = None
        profile_python: Path | None = None
        software_mapping = read_json(software_mapping_path)
        if software_mapping.get("profile_id") != binding["software_profile"]:
            raise ValueError("software mapping 的 profile_id 与 binding 不一致")
        if binding["software_profile_path"] is not None:
            profile_path = resolve("software_profile_path", directory=True)
            profile = read_json(profile_path / "profile.json")
            if profile.get("profile_id") != binding["software_profile"]:
                raise ValueError("software Profile ID 与 binding 不一致")
            mapped_profile_path = (root / software_mapping.get("profile_path", "")).resolve()
            if mapped_profile_path != profile_path:
                raise ValueError("software mapping 的 profile_path 与 binding 不一致")
            profile_python = profile_path / "python" / "bin" / "python"
            if not profile_python.is_file() or not os.access(profile_python, os.X_OK):
                raise ValueError(f"software Profile Python 不可执行：{profile_python}")

        return cls(
            root,
            binding_path,
            binding,
            package_path,
            environment_path,
            environment_validation_path,
            tools_path,
            tool_validation_path,
            software_mapping_path,
            profile_path,
            profile_python,
        )


def _binding_path(
    *,
    binding_path: Path | None,
    delivery_root: Path | None,
    package_id: str | None,
) -> Path | None:
    if binding_path is not None:
        return binding_path
    if delivery_root is None and package_id is None:
        return None
    if delivery_root is None or not package_id:
        raise ValueError("使用 ToolGen delivery 时必须提供 binding，或同时提供 delivery_root 和 package_id")
    return delivery_root / "environments" / package_id / "binding.json"


def run_step0(
    *,
    output_dir: Path,
    binding_path: Path | None = None,
    delivery_root: Path | None = None,
    package_id: str | None = None,
    environment_package: Path | None = None,
    tools_path: Path | None = None,
    scenario_research_path: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Freeze one validated ToolGen delivery; legacy direct paths remain supported."""
    selected_binding = _binding_path(
        binding_path=binding_path,
        delivery_root=delivery_root,
        package_id=package_id,
    )
    delivery = (
        ToolGenDelivery.load(selected_binding, delivery_root=delivery_root)
        if selected_binding is not None
        else None
    )
    if delivery is not None:
        if environment_package is not None or tools_path is not None:
            raise ValueError("binding 模式不能同时指定 environment_package 或 tools_path")
        environment_package = delivery.environment_path
        tools_path = delivery.tools_path
    if environment_package is None:
        raise ValueError("必须通过 binding 或 environment_package 指定输入环境")

    source = CompleteEnvironmentPackage.load(environment_package, tools_path=tools_path)
    output_dir = output_dir.resolve()
    baseline = output_dir / "baseline_environment"
    receipt_path = output_dir / "step0_environment.json"
    if baseline.is_dir() and receipt_path.is_file() and not overwrite:
        CompleteEnvironmentPackage.load(baseline)
        receipt = read_json(receipt_path)
        frozen_delivery = receipt.get("toolgen_delivery")
        if delivery is not None and (
            not isinstance(frozen_delivery, dict)
            or frozen_delivery.get("package_id") != delivery.binding["package_id"]
        ):
            raise ValueError("现有 Step 0 产物来自不同的 ToolGen binding")
        return receipt_path
    if (baseline.exists() or receipt_path.exists()) and not overwrite:
        raise FileExistsError(f"Step 0 只有部分产物存在：{output_dir}")
    if baseline.exists():
        shutil.rmtree(baseline)
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline.mkdir(parents=True)

    shutil.copy2(source.environment_path, baseline / "environment.json")
    validation_path = source.package_root / "validation.json"
    if validation_path.is_file():
        shutil.copy2(validation_path, baseline / "validation.json")
    else:
        write_json(baseline / "validation.json", {"valid": True, "legacy": True})
    state_name = "state" if source.package_format == "v2" else "workspace"
    shutil.copytree(source.state_root, baseline / state_name)
    write_json(baseline / "tools.json", {
        "schema_version": "1.0",
        "environment_id": source.environment["environment_id"],
        "tools": list(source.tools),
    })

    delivery_receipt: dict[str, Any] | None = None
    if delivery is not None:
        shutil.copy2(delivery.binding_path, baseline / "binding.json")
        shutil.copy2(delivery.tool_validation_path, baseline / "tool_validation.json")
        tool_runtime = delivery.environment_path / "tool_runtime.json"
        if tool_runtime.is_file():
            shutil.copy2(tool_runtime, baseline / "tool_runtime.json")
        delivery_receipt = {
            "schema_version": "1.0",
            "package_id": delivery.binding["package_id"],
            "environment_id": delivery.binding["environment_id"],
            "delivery_root": str(delivery.delivery_root),
            "binding_path": str(delivery.binding_path),
            "tool_validation_path": str(delivery.tool_validation_path),
            "software_profile": delivery.binding["software_profile"],
            "software_profile_path": (
                str(delivery.software_profile_path)
                if delivery.software_profile_path is not None
                else None
            ),
            "profile_python": (
                str(delivery.profile_python)
                if delivery.profile_python is not None
                else None
            ),
            "software_root": (
                str(delivery.software_profile_path)
                if delivery.software_profile_path is not None
                else None
            ),
        }
        write_json(baseline / "delivery.json", delivery_receipt)

    source_research = (
        scenario_research_path.resolve()
        if scenario_research_path is not None
        else source.package_root / "provenance" / "scenario_research.json"
    )
    frozen_research: str | None = None
    if source_research.is_file():
        target = baseline / "provenance" / "scenario_research.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_research, target)
        frozen_research = "baseline_environment/provenance/scenario_research.json"
    elif scenario_research_path is not None:
        raise FileNotFoundError(f"找不到指定的场景调研文件：{source_research}")

    frozen = CompleteEnvironmentPackage.load(baseline)
    initial_state = compact_state_snapshot(snapshot_state(
        frozen.state_root,
        frozen.environment,
        package_format=frozen.package_format,
    ))
    receipt: dict[str, Any] = {
        "step": 0,
        "status": "passed",
        "schema_version": "1.0",
        "environment_id": frozen.environment["environment_id"],
        "env_id": frozen.environment["environment_id"],
        "environment_package": "baseline_environment",
        "package_format": frozen.package_format,
        "public_environment": frozen.public_environment(),
        "initial_state": initial_state,
        "scenario_research": frozen_research,
        "toolgen_delivery": delivery_receipt,
    }
    write_json(receipt_path, receipt)
    return receipt_path


def load_environment(input: dict[str, Any]) -> dict[str, Any]:
    """Pipeline adapter for Step 0."""
    config = input["config"]
    receipt = run_step0(
        output_dir=Path(input["run_dir"]),
        binding_path=config.binding_path,
        delivery_root=config.delivery_root,
        package_id=config.package_id,
        environment_package=config.environment_package,
        tools_path=config.tools_path,
        scenario_research_path=config.scenario_research_path,
    )
    payload = read_json(receipt)
    return {
        "environment": payload["public_environment"],
        "step0_path": str(receipt),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--delivery-root", type=Path)
    parser.add_argument("--package-id")
    parser.add_argument("--environment-package", type=Path)
    parser.add_argument("--tools-path", type=Path)
    parser.add_argument("--scenario-research", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    print(run_step0(
        binding_path=arguments.binding,
        delivery_root=arguments.delivery_root,
        package_id=arguments.package_id,
        environment_package=arguments.environment_package,
        tools_path=arguments.tools_path,
        scenario_research_path=arguments.scenario_research,
        output_dir=arguments.output_dir,
        overwrite=arguments.overwrite,
    ))


if __name__ == "__main__":
    main()
