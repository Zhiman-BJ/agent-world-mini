"""workspace 文件枚举、哈希和 Raw append-only 检查。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .constants import CONTROL_RAW_INTEGRITY_SNAPSHOT
from .control_io import control_path, read_json


def workspace_files(run_dir: Path) -> dict[str, list[str]]:
    """列出三个业务目录中的全部文件；内容是否合法交给 Validator 判断。"""

    workspace = run_dir / "workspace"
    files: dict[str, list[str]] = {}
    for bucket in ("raw", "entities", "derived"):
        root = workspace / bucket
        files[bucket] = [
            path.relative_to(workspace).as_posix()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ] if root.is_dir() else []
    return files


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def business_snapshot(run_dir: Path) -> dict[str, str]:
    """计算已下载 raw 的指纹，防止后续画像轮次改写来源事实。"""

    workspace = run_dir / "workspace"
    return {
        relative: file_sha256(workspace / relative)
        for relative in workspace_files(run_dir)["raw"]
    }


def append_only_issues(run_dir: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    current = business_snapshot(run_dir)
    snapshot_path = control_path(run_dir, CONTROL_RAW_INTEGRITY_SNAPSHOT)
    if not snapshot_path.is_file():
        return current, []
    previous = read_json(snapshot_path, "上一次业务文件快照")
    issues: list[dict[str, str]] = []
    for relative, digest in previous.items():
        if not isinstance(digest, str):
            continue
        if relative not in current:
            issues.append(
                {
                    "code": "collection_removed_profiled_file",
                    "path": relative,
                    "message": f"删除了已经通过画像的 raw 来源文件：{relative}",
                }
            )
        elif current[relative] != digest:
            issues.append(
                {
                    "code": "collection_modified_profiled_file",
                    "path": relative,
                    "message": f"改写了已经通过画像的 raw 来源文件：{relative}；新分页必须保存为新文件",
                }
            )
    return current, issues


_workspace_files = workspace_files
_file_sha256 = file_sha256
_business_snapshot = business_snapshot
_append_only_issues = append_only_issues

__all__ = [
    "append_only_issues",
    "business_snapshot",
    "file_sha256",
    "workspace_files",
    "_append_only_issues",
    "_business_snapshot",
    "_file_sha256",
    "_workspace_files",
]
