"""Step 0: validate a ToolGen delivery binding and freeze its environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils.environment import CompleteEnvironmentPackage, STATE_REFERENCE_FILENAME
from .utils.io import read_json, write_json
from .utils.schema import Draft202012Validator
from .utils.tool_runtime import compact_state_snapshot, snapshot_state


INITIAL_STATE_FILE_SAMPLE_LIMIT = 500


def _json_safe_summary(value: Any) -> Any:
    """Preserve non-finite database facts without emitting invalid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {str(key): _json_safe_summary(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_summary(item) for item in value]
    return value


def _compact_initial_state_for_research(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep discovery facts without embedding every file entry in Step 0."""
    compact = compact_state_snapshot(snapshot)
    if compact.get("format") != "state-v2":
        return _json_safe_summary(compact)

    def summarize_files(container: dict[str, Any]) -> None:
        files = container.get("files")
        if not isinstance(files, dict):
            return
        ordered = sorted(files.items())
        digest = hashlib.sha256()
        total_bytes = 0
        samples: dict[str, Any] = {}
        for index, (path, facts) in enumerate(ordered):
            encoded = json.dumps(
                [path, facts], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(encoded)
            if isinstance(facts, dict):
                total_bytes += int(facts.get("size", 0) or 0)
            if index < INITIAL_STATE_FILE_SAMPLE_LIMIT:
                samples[path] = facts
        container["file_count"] = len(ordered)
        container["total_bytes"] = total_bytes
        container["sha256"] = digest.hexdigest()
        container["files"] = samples
        container["files_truncated"] = len(ordered) > len(samples)

    for scope in compact.get("filesystem_scopes", {}).values():
        if isinstance(scope, dict):
            summarize_files(scope)
    undeclared = compact.get("undeclared_files")
    if isinstance(undeclared, dict):
        wrapper = {"files": undeclared}
        summarize_files(wrapper)
        compact["undeclared_files"] = wrapper
    return _json_safe_summary(compact)


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
    runtime: dict[str, Any]

    @classmethod
    def load(
        cls,
        binding_path: Path,
        *,
        delivery_root: Path | None = None,
    ) -> "ToolGenDelivery":
        # ToolGen owns the binding contract, including the environment-level
        # software/profile mapping. Reuse its loader instead of reconstructing
        # profile.json and Python paths here; the shared profile is not itself
        # an environment mapping directory.
        from harness.delivery import load_delivery

        binding_path = binding_path.expanduser().resolve()
        try:
            delivery = load_delivery(binding_path)
        except ValueError as error:
            # Early ToolGen deliveries used Python's leading ``(?i)`` regex
            # flag in a handful of path patterns. The Program runtime and its
            # JSON Schema validator can still consume those packages, but the
            # newer delivery loader rejects them before TaskGen sees the
            # environment. Resolve the already-frozen receipt paths directly;
            # do not mutate the ToolGen delivery on disk.
            if "使用了 Python 内联正则标志" not in str(error):
                raise
            return cls._load_legacy_regex_binding(
                binding_path,
                delivery_root=delivery_root,
            )
        if delivery_root is not None and delivery.delivery_root != delivery_root.expanduser().resolve():
            raise ValueError(
                "binding 解析出的 delivery_root 与显式 --delivery-root 不一致："
                f"{delivery.delivery_root} != {delivery_root.expanduser().resolve()}"
            )
        binding = delivery.binding
        package_path = binding_path.parent
        environment_path = delivery.package.package_root
        environment_validation_path = delivery.delivery_root / Path(
            binding["environment_validation_path"]
        )
        tools_path = delivery.delivery_root / Path(binding["tools_path"])
        tool_validation_path = delivery.delivery_root / Path(binding["tool_validation_path"])
        software_mapping_path = delivery.delivery_root / Path(binding["software_mapping_path"])

        return cls(
            delivery.delivery_root,
            binding_path,
            binding,
            package_path,
            environment_path,
            environment_validation_path,
            tools_path,
            tool_validation_path,
            software_mapping_path,
            delivery.software_root,
            delivery.python_path,
            dict(delivery.runtime),
        )

    @classmethod
    def _load_legacy_regex_binding(
        cls,
        binding_path: Path,
        *,
        delivery_root: Path | None,
    ) -> "ToolGenDelivery":
        binding = read_json(binding_path)
        if not isinstance(binding, dict):
            raise ValueError("ToolGen binding 必须是 object")
        if delivery_root is None:
            relative_package = Path(str(binding.get("package_path") or ""))
            roots = [
                parent
                for parent in binding_path.parents
                if relative_package.parts
                and (parent / relative_package).resolve() == binding_path.parent.resolve()
            ]
            if not roots:
                raise ValueError(f"无法从 binding 推导 delivery_root：{binding_path}")
            delivery_root = roots[0]
        delivery_root = delivery_root.expanduser().resolve()

        def required_path(field: str) -> Path:
            value = binding.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"旧 ToolGen binding 缺少 {field}")
            path = (delivery_root / value).resolve()
            if not path.exists():
                raise FileNotFoundError(f"旧 ToolGen binding 的 {field} 不存在：{path}")
            return path

        package_path = required_path("package_path")
        environment_path = required_path("environment_path")
        environment_validation_path = required_path("environment_validation_path")
        tools_path = required_path("tools_path")
        tool_validation_path = required_path("tool_validation_path")
        software_mapping_path = required_path("software_mapping_path")
        runtime_path = required_path("runtime_path")
        software_mapping = read_json(software_mapping_path)
        runtime = read_json(runtime_path)
        if not isinstance(software_mapping, dict) or not isinstance(runtime, dict):
            raise ValueError("旧 ToolGen software/profile 或 runtime 回执不是 object")
        profile_value = (
            binding.get("software_profile_path")
            or software_mapping.get("profile_path")
        )
        python_value = software_mapping.get("python_path")
        software_profile_path = (
            (delivery_root / str(profile_value)).resolve()
            if isinstance(profile_value, str) and profile_value
            else None
        )
        profile_python = (
            delivery_root / str(python_value)
            if isinstance(python_value, str) and python_value
            else None
        )
        return cls(
            delivery_root=delivery_root,
            binding_path=binding_path,
            binding=binding,
            package_path=package_path,
            environment_path=environment_path,
            environment_validation_path=environment_validation_path,
            tools_path=tools_path,
            tool_validation_path=tool_validation_path,
            software_mapping_path=software_mapping_path,
            software_profile_path=software_profile_path,
            profile_python=profile_python,
            runtime=runtime,
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
    if delivery is not None:
        # ToolGen delivery packages are frozen inputs. Keep an indexed, read-only
        # baseline here; every actual tool execution still receives its own
        # writable copy through CompleteEnvironmentRuntime.
        write_json(baseline / STATE_REFERENCE_FILENAME, {
            "schema_version": "1.0",
            "state_name": state_name,
            "path": str(source.state_root.resolve()),
        })
    else:
        # Legacy direct packages have no delivery immutability contract.
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
        write_json(baseline / "runtime.json", delivery.runtime)
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
            "runtime": dict(delivery.runtime),
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
    initial_state = _compact_initial_state_for_research(snapshot_state(
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
