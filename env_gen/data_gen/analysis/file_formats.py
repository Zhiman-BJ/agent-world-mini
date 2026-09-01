"""DataGen 各阶段共用的文件格式名称规范化。"""

from __future__ import annotations

from typing import Any


_FORMAT_ALIASES = {
    "db": "sqlite",
    "gds": "gdsii",
    "gpkg": "geopackage",
    "htm": "html",
    "js": "javascript",
    "jpg": "jpeg",
    "md": "markdown",
    "ndjson": "jsonl",
    "oas": "oasis",
    "py": "python",
    "shp": "shapefile",
    "sol": "solidity",
    "sqlite3": "sqlite",
    "tgz": "gz",
    "tif": "tiff",
    "ts": "typescript",
    "txt": "text",
    "yml": "yaml",
}


def normalize_file_format(value: Any) -> str:
    """把扩展名或协议中的格式名转换为统一的小写名称。"""

    normalized = str(value or "").strip().lower().lstrip(".")
    return _FORMAT_ALIASES.get(normalized, normalized)


__all__ = ["normalize_file_format"]
