"""最终 Record Set 表和 Filesystem Scope 树的稳定摘要。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from env_gen.data_gen.steps.common.workspace_files import file_sha256


def table_digest(database: Path, table: str) -> str:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        quoted = table.replace('"', '""')
        columns = [
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{quoted}")')
        ]
        rows = [
            list(row)
            for row in connection.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid')
        ]
    finally:
        connection.close()
    payload = json.dumps(
        {"columns": columns, "rows": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def tree_digest(root: Path) -> str:
    items = [
        (path.relative_to(root).as_posix(), file_sha256(path))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ] if root.is_dir() else []
    return hashlib.sha256(
        json.dumps(items, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = ["table_digest", "tree_digest"]
