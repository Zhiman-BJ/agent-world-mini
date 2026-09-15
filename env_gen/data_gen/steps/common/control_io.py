"""DataGen 控制面 JSON 和控制目录路径。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .constants import CONTROL_DIRECTORY


def atomic_write_text(path: Path, content: str) -> None:
    """完整写入临时文件后原子替换，避免文件监视器看到半份内容。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, payload: object) -> None:
    """将 JSON 完整写入临时文件后原子替换目标文件。"""

    atomic_write_text(path, json_text(payload))


def json_text(payload: object) -> str:
    """生成统一缩进并保留非 ASCII 字符的 JSON 文本。"""

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def read_json(path: Path, label: str) -> dict[str, Any]:
    """读取根节点为对象的 JSON，并用业务标签生成清晰错误信息。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"缺少{label}：{path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label}无法读取：{path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}根节点必须是对象：{path}")
    return payload


def control_path(run_dir: Path, name: str) -> Path:
    """返回当前运行目录中的 `.datagen/<name>` 控制文件路径。"""

    return run_dir / CONTROL_DIRECTORY / name


__all__ = [
    "control_path",
    "atomic_write_text",
    "json_text",
    "read_json",
    "write_json",
]
