from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_json_object(text: str) -> Any:
    """Accept an object or array optionally wrapped in Markdown fencing."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "[{":
            value, _ = decoder.raw_decode(text[index:])
            return value
    raise ValueError("No JSON object or array in model response")
