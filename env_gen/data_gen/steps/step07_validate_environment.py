"""Step 07：确定性校验环境声明、来源和实际文件。"""

from __future__ import annotations

from pathlib import Path

from env_gen.data_gen.validator import (
    EnvironmentPackageValidator,
    ValidationReport,
)


def validate_environment(
    validator: EnvironmentPackageValidator,
    package_root: Path,
) -> ValidationReport:
    """运行完整环境校验；该步骤不调用模型，也不修改业务数据。"""

    return validator.validate(package_root)
