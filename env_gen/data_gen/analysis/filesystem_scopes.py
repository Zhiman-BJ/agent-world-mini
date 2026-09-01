"""Filesystem Scope 路径、层级和轻量格式验证。"""

from __future__ import annotations

import json
import sqlite3
import tarfile
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .file_formats import normalize_file_format


@dataclass(frozen=True)
class ScopeValidationIssue:
    code: str
    path: str
    message: str


_FORMAT_SUFFIXES = {
    "json": {".json"}, "geojson": {".geojson", ".json"},
    "jsonl": {".jsonl", ".ndjson"}, "csv": {".csv"},
    "tsv": {".tsv"}, "parquet": {".parquet"},
    "sqlite": {".sqlite", ".sqlite3", ".db"}, "xml": {".xml"},
    "yaml": {".yaml", ".yml"}, "markdown": {".md", ".markdown"},
    "text": {".txt"}, "html": {".html", ".htm"}, "pdf": {".pdf"},
    "docx": {".docx"}, "zip": {".zip"}, "tar": {".tar"},
    "dxf": {".dxf"}, "dwg": {".dwg"}, "gdsii": {".gds", ".gdsii"},
    "lef": {".lef"}, "def": {".def"}, "geopackage": {".gpkg"},
    "shapefile": {".shp"}, "png": {".png"}, "jpeg": {".jpg", ".jpeg"},
    "tiff": {".tif", ".tiff"}, "svg": {".svg"}, "wav": {".wav"},
    "mp3": {".mp3"}, "mp4": {".mp4"}, "python": {".py"},
    "javascript": {".js", ".mjs", ".cjs"}, "typescript": {".ts", ".tsx"},
    "solidity": {".sol"},
}
_TEXT_FORMATS = {
    "json", "geojson", "jsonl", "csv", "tsv", "xml", "yaml",
    "markdown", "text", "html", "dxf", "lef", "def", "svg", "python",
    "javascript", "typescript", "solidity", "source_text",
}


def structure_definition_issues(
    structure: dict[str, Any], *, path: str = "structure", top_level: bool = True,
) -> list[ScopeValidationIssue]:
    """校验不能靠 JSON Schema 表达的 kind/path/layout 条件。"""

    issues: list[ScopeValidationIssue] = []
    kind = str(structure.get("kind") or "")
    value = structure.get("path")
    if not isinstance(value, str) or not value:
        return [ScopeValidationIssue("invalid_scope_path", path, "path 必须是非空字符串")]
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        issues.append(ScopeValidationIssue(
            "unsafe_scope_path", f"{path}.path", f"不安全的 Scope 路径：{value}",
        ))
    globbed = any(character in value for character in "*?[")
    collection = kind in {"file_collection", "directory_collection"}
    if globbed and not collection:
        issues.append(ScopeValidationIssue(
            "glob_on_single_scope_node", f"{path}.path", "只有 collection kind 允许 glob",
        ))
    if value == "." and not (top_level and kind == "directory"):
        issues.append(ScopeValidationIssue(
            "invalid_scope_root_marker", f"{path}.path", "只有顶层 directory 可以使用 .",
        ))
    if kind in {"file", "file_collection"}:
        if not isinstance(structure.get("format"), str):
            issues.append(ScopeValidationIssue(
                "missing_scope_format", path, "文件节点必须声明 format",
            ))
        if "layout" in structure:
            issues.append(ScopeValidationIssue(
                "file_node_with_layout", path, "文件节点不能声明 layout",
            ))
        content_validation = structure.get("content_validation", "strict")
        if content_validation not in {"strict", "allow_invalid"}:
            issues.append(ScopeValidationIssue(
                "invalid_content_validation", f"{path}.content_validation",
                "content_validation 必须是 strict 或 allow_invalid",
            ))
    elif kind in {"directory", "directory_collection"}:
        if "content_validation" in structure:
            issues.append(ScopeValidationIssue(
                "directory_with_content_validation", path,
                "只有 file/file_collection 节点允许 content_validation",
            ))
        layout = structure.get("layout")
        if not isinstance(layout, list) or not layout:
            issues.append(ScopeValidationIssue(
                "missing_scope_layout", path, "目录节点必须声明非空 layout",
            ))
        else:
            for index, child in enumerate(layout):
                if isinstance(child, dict):
                    issues.extend(structure_definition_issues(
                        child, path=f"{path}.layout[{index}]", top_level=False,
                    ))
    return issues


def _matches(root: Path, pattern: str, *, want_directory: bool) -> list[Path]:
    if pattern == ".":
        candidates = [root]
    elif any(character in pattern for character in "*?["):
        candidates = sorted(root.glob(pattern))
    else:
        candidates = [root / pattern]
    return [
        item for item in candidates
        if item.is_dir() if want_directory
    ] if want_directory else [item for item in candidates if item.is_file()]


def _format_issue(path: Path, format_name: str, pointer: str) -> ScopeValidationIssue | None:
    value = normalize_file_format(format_name)
    suffixes = _FORMAT_SUFFIXES.get(value)
    if suffixes is not None and path.suffix.lower() not in suffixes:
        return ScopeValidationIssue(
            "scope_format_extension_mismatch", pointer,
            f"{path.name} 的扩展名与 format={format_name} 不一致",
        )
    try:
        if value in {"json", "geojson"}:
            json.loads(path.read_text(encoding="utf-8"))
        elif value == "jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)
        elif value == "xml":
            ElementTree.parse(path)
        elif value == "sqlite":
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA schema_version").fetchone()
            finally:
                connection.close()
        elif value == "zip" and not zipfile.is_zipfile(path):
            raise ValueError("不是 ZIP")
        elif value == "tar" and not tarfile.is_tarfile(path):
            raise ValueError("不是 TAR")
        elif value in _TEXT_FORMATS:
            path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, ElementTree.ParseError, sqlite3.Error) as error:
        return ScopeValidationIssue(
            "invalid_scope_file_format", pointer,
            f"{path.name} 无法按 {format_name} 读取：{error}",
        )
    return None


def _validate_node(
    root: Path,
    node: dict[str, Any],
    *,
    pointer: str,
    top_level: bool,
) -> tuple[list[Path], set[Path], list[ScopeValidationIssue]]:
    kind = str(node.get("kind") or "")
    pattern = str(node.get("path") or "")
    want_directory = kind in {"directory", "directory_collection"}
    matched = _matches(root, pattern, want_directory=want_directory)
    required = True if top_level else node.get("required") is True
    issues: list[ScopeValidationIssue] = []
    if required and not matched:
        issues.append(ScopeValidationIssue(
            "missing_required_scope_path", pointer,
            f"没有匹配必需的 {kind} 路径：{pattern}",
        ))
        return matched, set(), issues
    if kind in {"file", "file_collection"}:
        format_name = str(node.get("format") or "")
        for item in matched:
            # allow_invalid 仍检查格式家族/扩展名，但保留故障样本的初始内容。
            suffixes = _FORMAT_SUFFIXES.get(normalize_file_format(format_name))
            if suffixes is not None and item.suffix.lower() not in suffixes:
                issues.append(ScopeValidationIssue(
                    "scope_format_extension_mismatch", pointer,
                    f"{item.name} 的扩展名与 format={format_name} 不一致",
                ))
            elif node.get("content_validation", "strict") == "strict":
                issue = _format_issue(item, format_name, pointer)
                if issue is not None:
                    issues.append(issue)
        covered_files = {item.resolve() for item in matched}
    else:
        covered_files: set[Path] = set()
        for directory in matched:
            for index, child in enumerate(node.get("layout", [])):
                if not isinstance(child, dict):
                    continue
                _, child_files, child_issues = _validate_node(
                    directory,
                    child,
                    pointer=f"{pointer}.layout[{index}]",
                    top_level=False,
                )
                covered_files.update(child_files)
                issues.extend(child_issues)
    return matched, covered_files, issues


def permitted_invalid_files(
    scope_root: Path,
    structure: dict[str, Any],
) -> list[dict[str, str]]:
    """列出被 allow_invalid 明确保留、但严格轻量解析失败的文件。"""

    result: list[dict[str, str]] = []

    def visit(root: Path, node: dict[str, Any], *, pointer: str) -> None:
        kind = str(node.get("kind") or "")
        matched = _matches(
            root,
            str(node.get("path") or ""),
            want_directory=kind in {"directory", "directory_collection"},
        )
        if kind in {"file", "file_collection"}:
            if node.get("content_validation", "strict") != "allow_invalid":
                return
            for path in matched:
                issue = _format_issue(path, str(node.get("format") or ""), pointer)
                if issue is not None and issue.code == "invalid_scope_file_format":
                    result.append({
                        "path": path.relative_to(scope_root).as_posix(),
                        "format": str(node.get("format") or ""),
                        "message": issue.message,
                    })
            return
        for directory in matched:
            for index, child in enumerate(node.get("layout", [])):
                if isinstance(child, dict):
                    visit(directory, child, pointer=f"{pointer}.layout[{index}]")

    if scope_root.is_dir():
        visit(scope_root, structure, pointer="structure")
    return sorted(result, key=lambda item: item["path"])


def validate_scope_tree(
    scope_root: Path,
    structure: dict[str, Any],
    *,
    pointer: str = "structure",
) -> list[ScopeValidationIssue]:
    """检查实际 Scope 是否满足层级模板和轻量格式要求。"""

    issues = structure_definition_issues(structure, path=pointer)
    if not scope_root.is_dir():
        return issues + [ScopeValidationIssue(
            "missing_scope_directory", pointer, f"Scope 目录不存在：{scope_root}",
        )]
    for item in scope_root.rglob("*"):
        if item.is_symlink():
            issues.append(ScopeValidationIssue(
                "scope_symlink_not_allowed", pointer,
                f"Scope 不允许符号链接：{item.relative_to(scope_root).as_posix()}",
            ))
    matched, covered_files, node_issues = _validate_node(
        scope_root, structure, pointer=pointer, top_level=True,
    )
    issues.extend(node_issues)
    kind = structure.get("kind")
    all_files = {item.resolve() for item in scope_root.rglob("*") if item.is_file()}
    if kind == "file":
        selected = {item.resolve() for item in matched}
        if all_files != selected:
            issues.append(ScopeValidationIssue(
                "unexpected_files_in_single_file_scope", pointer,
                "file Scope 中存在声明文件之外的普通文件",
            ))
    elif kind == "file_collection":
        selected = {item.resolve() for item in matched}
        if all_files != selected:
            issues.append(ScopeValidationIssue(
                "unmatched_files_in_file_collection", pointer,
                "file_collection Scope 中存在不匹配顶层 glob 的普通文件",
            ))
    elif all_files != covered_files:
        unmatched = sorted(
            path.relative_to(scope_root.resolve()).as_posix()
            for path in all_files - covered_files
        )
        issues.append(ScopeValidationIssue(
            "unmodeled_files_in_directory_scope", pointer,
            "目录 Scope 中存在未被 layout 文件节点覆盖的普通文件："
            + ", ".join(unmatched[:8]),
        ))
    return issues


__all__ = [
    "ScopeValidationIssue",
    "permitted_invalid_files",
    "structure_definition_issues",
    "validate_scope_tree",
]
