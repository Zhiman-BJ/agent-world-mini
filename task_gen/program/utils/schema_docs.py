"""按步骤把必要的项目契约说明复制到 Agent 工作目录。

契约 Markdown 是给 Agent 阅读的背景材料，不属于最终任务输出。这里使用显式白名单，
避免把 ``schemas/`` 目录中与当前步骤无关的文档一股脑暴露给 Agent。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable


SCHEMA_DOCS_ROOT = Path(__file__).resolve().parents[3] / "schemas"


def copy_schema_docs(destination: Path, names: Iterable[str]) -> list[Path]:
    """将指定契约文档复制到 ``destination/references`` 并返回目标路径。

    ``names`` 只能包含仓库 ``schemas/`` 下的文件名；路径分隔符和 ``..`` 会被拒绝，
    这样调用方不会意外复制仓库外的文件。
    """
    references = destination / "references"
    references.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
            raise ValueError(f"契约文档名必须是 schemas/ 下的文件名：{name}")
        source = SCHEMA_DOCS_ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"找不到契约文档：{source}")
        target = references / relative.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


STEP1_SCHEMA_DOCS = (
    "环境契约-v2.0.md",
    "工具契约-v1.0.md",
)

STEP2_SCHEMA_DOCS = (
    "环境契约-v2.0.md",
    "工具契约-v1.0.md",
    "任务契约-v1.0.md",
)
