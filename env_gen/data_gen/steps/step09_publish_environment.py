"""Step 09：把通过校验的环境发布到最终目录。"""

from __future__ import annotations

import shutil
from pathlib import Path


def publish_environment(
    staging_path: Path,
    *,
    final_output_dir: Path,
    overwrite: bool,
) -> Path:
    """原子移动生成现场；该步骤不调用模型。"""

    final_output_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"输出目录已经存在：{final_output_dir}")
        shutil.rmtree(final_output_dir)
    staging_path.replace(final_output_dir)
    return final_output_dir
