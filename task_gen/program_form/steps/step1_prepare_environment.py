"""Step 1：接收 DataGen/ToolGen 产物并冻结 TaskGen 初始环境。

这是本管线唯一不与 OmniaBench 相同的步骤。OmniaBench 在 Step 1 让模型生成
``init_config``；Agent-World Mini 已有 DataGen 生成的真实 ``state/``，因此本步
不生成业务数据，只验证并复制可执行环境包，作为后续全部任务共享的只读基线。

输入：``environment.json + validation.json + state/ + tools.json``。
输出：``step1_environment.json`` 和 ``baseline_environment/``。
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from ..utils.environment import CompleteEnvironmentPackage
from ..utils.io import write_json
from ..utils.tool_runtime import compact_state_snapshot, snapshot_state


def run_step1(
    *,
    environment_package: Path,
    output_dir: Path,
    tools_path: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """验证输入环境并冻结后续步骤使用的基线副本。"""
    source = CompleteEnvironmentPackage.load(environment_package, tools_path=tools_path)
    output_dir = output_dir.resolve()
    baseline = output_dir / "baseline_environment"
    receipt_path = output_dir / "step1_environment.json"
    if baseline.is_dir() and receipt_path.is_file() and not overwrite:
        CompleteEnvironmentPackage.load(baseline)
        return receipt_path
    if (baseline.exists() or receipt_path.exists()) and not overwrite:
        raise FileExistsError(f"Step 1 只有部分产物存在：{output_dir}")
    if baseline.exists():
        shutil.rmtree(baseline)
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline.mkdir(parents=True)

    shutil.copy2(source.environment_path, baseline / "environment.json")
    validation_path = source.package_root / "validation.json"
    if validation_path.is_file():
        shutil.copy2(validation_path, baseline / "validation.json")
    else:
        # 只有兼容 v1 包会走这里；v2 Loader 已强制要求上游回执。
        write_json(baseline / "validation.json", {"valid": True, "legacy": True})
    state_name = "state" if source.package_format == "v2" else "workspace"
    shutil.copytree(source.state_root, baseline / state_name)
    write_json(baseline / "tools.json", {"tools": list(source.tools)})

    frozen = CompleteEnvironmentPackage.load(baseline)
    initial_state = compact_state_snapshot(
        snapshot_state(
            frozen.state_root,
            frozen.environment,
            package_format=frozen.package_format,
        )
    )
    receipt: dict[str, Any] = {
        "step": 1,
        "status": "passed",
        "schema_version": "1.0",
        "env_id": frozen.environment["environment_id"],
        "environment_package": "baseline_environment",
        "package_format": frozen.package_format,
        "public_environment": frozen.public_environment(),
        "initial_state": initial_state,
    }
    write_json(receipt_path, receipt)
    return receipt_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-package", type=Path, required=True)
    parser.add_argument("--tools-path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    print(
        run_step1(
            environment_package=arguments.environment_package,
            tools_path=arguments.tools_path,
            output_dir=arguments.output_dir,
            overwrite=arguments.overwrite,
        )
    )


if __name__ == "__main__":
    main()
