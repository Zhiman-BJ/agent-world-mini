"""在只读、无网络的隔离环境中执行 Record Set 转换。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_record_transformation(
    run_dir: Path,
    *,
    script: Path,
    output: Path,
    asset_id: str,
    timeout_seconds: int,
) -> None:
    """运行一次转换；只允许写 ``output.parent``，并隐藏已有候选状态。"""

    run_dir = run_dir.resolve()
    script = script.resolve()
    output = output.resolve()
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        raise RuntimeError("受控 Record Set 转换需要 bubblewrap（bwrap）")
    output_directory = output.parent
    output_directory.mkdir(parents=True, exist_ok=True)
    state_directory = run_dir / "state"
    state_directory.mkdir(parents=True, exist_ok=True)
    command = [
        bubblewrap,
        "--die-with-parent",
        "--unshare-net",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        # 转换必须从 Raw 和协议推导，不能读取已物化结果反向生成输出。
        "--tmpfs", str(state_directory),
        "--bind", str(output_directory), str(output_directory),
        "--chdir", str(run_dir),
        "--clearenv",
        "--setenv", "PATH", os.environ.get("PATH", "/usr/bin:/bin"),
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONHASHSEED", "0",
        "--setenv", "LANG", "C.UTF-8",
        sys.executable, str(script), "--run-dir", str(run_dir),
        "--asset-id", asset_id, "--output", str(output),
    ]
    result = subprocess.run(
        command,
        cwd=run_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"转换脚本退出码 {result.returncode}：{result.stderr[-2000:]}"
        )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("转换脚本没有生成非空输出文件")


__all__ = ["run_record_transformation"]
