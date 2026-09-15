"""Program-form 各步骤共用的 JSON 产物读写函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"无法读取 JSON：{path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} 不是合法 JSON：{error}") from error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def round_directory(output_dir: Path, step: str, round_index: int) -> Path:
    return output_dir / step / f"round_{round_index:03d}"


def read_records(path: Path) -> list[dict[str, Any]]:
    """读取 JSON list 或以最新 ``__idx`` 为准的 JSONL checkpoint。"""
    path = path.resolve()
    if path.suffix != ".jsonl":
        value = read_json(path)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"{path} 必须是任务 object 组成的 JSON list")
        return value
    latest: dict[int, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"无法读取 JSONL：{path}: {error}") from error
    for fallback_index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        index = record.get("__idx", fallback_index)
        item = record.get("item", record)
        if isinstance(index, int) and isinstance(item, dict):
            latest[index] = item
    return [latest[index] for index in sorted(latest)]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """原子写出 OmniaBench 使用的 ``__idx/item`` JSONL 格式。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for index, item in enumerate(records):
            stream.write(
                json.dumps(
                    {"__idx": index, "item": item},
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
    temporary.replace(path)
