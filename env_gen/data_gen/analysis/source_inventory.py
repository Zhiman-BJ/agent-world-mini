"""从真实 Raw 文件确定性生成来源级画像，不推断最终 Record Set。"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from jsonschema import Draft202012Validator

from .file_formats import normalize_file_format
from .structured_io import count_structured_records


_STRUCTURED_FORMATS = {"json", "jsonl", "csv", "parquet", "sqlite"}
_TEXT_FORMATS = {"text", "markdown", "html", "yaml"}
_SOURCE_TEXT_FORMATS = {
    "def", "dxf", "javascript", "lef", "python", "solidity", "typescript",
}
_DOMAIN_FORMATS = {
    "xml", "pdf", "docx", "geojson", "geopackage", "shapefile", "gdsii",
    "oasis", "lef", "def", "dwg", "dxf", "png", "jpeg", "tiff", "svg",
    "wav", "mp3", "mp4", "solidity", "python", "javascript", "typescript",
}
_ARCHIVE_FORMATS = {"zip", "tar", "gz", "bz2", "xz"}
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _repository_url_stability(url: str) -> str | None:
    """返回 GitHub 内容 URL 的版本稳定性；普通网页/API 返回 None。"""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    revision: str | None = None
    if host == "raw.githubusercontent.com" and len(parts) >= 3:
        revision = parts[2]
    elif host == "codeload.github.com" and len(parts) >= 4:
        revision = parts[3] if parts[2] in {"zip", "tar.gz"} else None
        if revision == "refs" and len(parts) >= 6:
            revision = parts[5]
    elif host == "github.com" and len(parts) >= 4:
        if parts[2] in {"raw", "blob"}:
            revision = parts[3]
        elif parts[2] == "archive":
            if parts[3] == "refs" and len(parts) >= 6:
                revision = parts[5].removesuffix(".zip").removesuffix(".tar.gz")
            else:
                revision = parts[3].removesuffix(".zip").removesuffix(".tar.gz")
    elif host == "api.github.com" and len(parts) >= 5 and parts[:1] == ["repos"]:
        reference = parse_qs(parsed.query).get("ref", [])
        revision = reference[0] if reference else None
    if revision is None:
        return None
    return "immutable_repository" if _COMMIT_SHA.fullmatch(revision) else "mutable_repository"


def _retrieval_profiles(run_dir: Path) -> dict[str, dict[str, Any]]:
    """按 Raw 路径汇总成功下载 URL，并判定本次获取的版本稳定性。"""

    from env_gen.data_gen.steps.common.constants import CONTROL_DOWNLOAD_RECEIPTS
    from env_gen.data_gen.steps.common.control_io import control_path, read_json

    receipt_path = control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if not receipt_path.is_file():
        return {}
    payload = read_json(receipt_path, "下载收据")
    by_path: dict[str, list[str]] = {}
    for item in payload.get("downloads", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        values = [item.get("url"), item.get("effective_url")]
        by_path.setdefault(str(item["path"]), []).extend(
            str(value) for value in values if isinstance(value, str) and value
        )
    result: dict[str, dict[str, Any]] = {}
    for path, values in by_path.items():
        urls = sorted(set(values))
        repository_states = {
            state for url in urls
            if (state := _repository_url_stability(url)) is not None
        }
        stability = (
            "immutable_repository" if "immutable_repository" in repository_states
            else "mutable_repository" if "mutable_repository" in repository_states
            else "timestamped_snapshot"
        )
        result[path] = {"retrieval_stability": stability, "retrieval_urls": urls}
    return result


def _placeholder_issue(path: Path, format_name: str) -> str | None:
    """识别有明确内容证据的下载占位物，不用文件大小猜测价值。"""

    if format_name not in _TEXT_FORMATS | _SOURCE_TEXT_FORMATS and format_name != "xsd":
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    compact = " ".join(text.strip().lower().split())
    if compact.startswith("version https://git-lfs.github.com/spec/v1"):
        return "文件是 Git LFS pointer，不是目标内容"
    explicit_error_markers = (
        "<title>404 not found", "<title>403 forbidden", "404 not found",
        "access denied", "authentication required", "please sign in to continue",
    )
    if any(marker in compact for marker in explicit_error_markers):
        return "文件内容是错误、拒绝访问或登录占位页"
    if len(text.encode("utf-8")) <= 4096 and re.search(
        r"\b(as of|has moved|is now (?:available|found)|updates are found|click)\b",
        compact,
    ) and re.search(r"<a\s+[^>]*href=", compact):
        return "文件只是指向其他位置的跳转说明，不是目标正文"
    return None


def _format_name(path: Path) -> str:
    suffixes = [value.lower().lstrip(".") for value in path.suffixes]
    if suffixes[-2:] == ["tar", "gz"]:
        return "gz"
    value = suffixes[-1] if suffixes else "binary"
    return normalize_file_format(value)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _stable_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _record_group(path: str, records: list[dict[str, Any]], observed: int) -> dict[str, Any]:
    sampled = records[:500]
    names = sorted({str(name) for record in sampled for name in record})
    fields: list[dict[str, Any]] = []
    candidate_keys: list[str] = []
    for name in names:
        values = [record.get(name) for record in sampled]
        non_null = [value for value in values if value is not None]
        distinct = len({_stable_value(value) for value in non_null})
        types = sorted({_value_type(value) for value in values})
        fields.append({
            "name": name,
            "types": types,
            "non_null_count": len(non_null),
            "distinct_sample_count": distinct,
        })
        if sampled and len(non_null) == len(sampled) and distinct == len(sampled):
            candidate_keys.append(name)
    return {
        "path": path,
        "observed_record_count": observed,
        "sampled_record_count": len(sampled),
        "fields": fields,
        "candidate_key_fields": candidate_keys,
    }


def _json_groups(
    value: Any,
    *,
    path: str = "$",
    depth: int = 0,
    max_depth: int = 4,
) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        groups.append((path, value))
    if isinstance(value, dict) and depth < max_depth:
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, list) and child and all(isinstance(item, dict) for item in child):
                groups.append((child_path, child))
            elif isinstance(child, dict):
                groups.extend(_json_groups(child, path=child_path, depth=depth + 1, max_depth=max_depth))
    return groups


def _structured_groups(path: Path, format_name: str) -> tuple[list[dict[str, Any]], int]:
    if format_name in {"json", "geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups = _json_groups(payload)
        if not groups and isinstance(payload, dict):
            groups = [("$", [payload])]
        profiles = [_record_group(name, records, len(records)) for name, records in groups]
        profiles.sort(key=lambda item: (-int(item["observed_record_count"]), str(item["path"])))
        return profiles[:32], len(profiles)
    if format_name == "jsonl":
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        objects = [item for item in records if isinstance(item, dict)]
        if len(objects) != len(records):
            raise ValueError("JSONL 每一行必须是对象")
        profiles = [_record_group("$", objects, len(objects))] if objects else []
        return profiles, len(profiles)
    if format_name == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        profiles = [_record_group("$", rows, len(rows))] if rows else []
        return profiles, len(profiles)
    if format_name == "sqlite":
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            result: list[dict[str, Any]] = []
            for table in tables:
                quoted = table.replace('"', '""')
                count = int(connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0])
                rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{quoted}" LIMIT 500')]
                result.append(_record_group(f"$.{table}", rows, count))
            return result[:32], len(result)
        finally:
            connection.close()
    if format_name == "parquet":
        import pyarrow.parquet as parquet
        table = parquet.read_table(path)
        return [_record_group("$", table.slice(0, 500).to_pylist(), table.num_rows)], 1
    return [], 0


def _archive_shape(path: Path) -> tuple[int, list[str], list[str]]:
    names: list[str]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            names = [item.name for item in archive.getmembers() if item.isfile()]
    else:
        return 0, [], []
    formats = sorted({_format_name(Path(name)) for name in names})
    roots = sorted({Path(name).parts[0] for name in names if Path(name).parts})[:50]
    return len(names), formats, roots


def _shape(path: Path, format_name: str) -> tuple[list[str], str, dict[str, Any], list[str]]:
    empty_shape = {
        "discovered_record_group_count": 0, "record_groups": [], "root_name": None, "member_count": None,
        "member_formats": [], "top_level_entries": [], "line_count": None,
    }
    issues: list[str] = []
    placeholder = _placeholder_issue(path, format_name)
    if placeholder is not None:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            line_count = sum(1 for _line in stream)
        return ["documentation"], "placeholder", {
            "kind": "text", **empty_shape, "line_count": line_count,
        }, [placeholder]
    if format_name in _STRUCTURED_FORMATS or format_name == "geojson":
        try:
            groups, discovered_count = _structured_groups(path, format_name)
        except Exception as error:
            return ["structured_records"], "invalid", {"kind": "structured", **empty_shape}, [str(error)]
        roles = ["structured_records"]
        if format_name == "geojson":
            roles.append("domain_file")
        return roles, "parsed", {
            "kind": "structured", **empty_shape,
            "discovered_record_group_count": discovered_count, "record_groups": groups,
        }, issues
    if format_name in {"xml", "xsd"}:
        try:
            import xml.etree.ElementTree as element_tree
            root_name = element_tree.parse(path).getroot().tag
        except Exception as error:
            return ["domain_file", "structured_records"], "invalid", {"kind": "xml", **empty_shape}, [str(error)]
        return ["domain_file", "structured_records"], "parsed", {"kind": "xml", **empty_shape, "root_name": root_name}, issues
    if format_name in _ARCHIVE_FORMATS:
        try:
            count, formats, roots = _archive_shape(path)
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
            return ["archive"], "invalid", {"kind": "archive", **empty_shape}, [str(error)]
        status = "sampled" if count else "unsupported"
        return ["archive"], status, {
            "kind": "archive", **empty_shape, "member_count": count,
            "member_formats": formats, "top_level_entries": roots,
        }, ([] if count else ["无法枚举归档成员"])
    if format_name in _TEXT_FORMATS:
        try:
            line_count = sum(1 for _line in path.open("r", encoding="utf-8", errors="replace"))
        except OSError as error:
            return ["documentation"], "invalid", {"kind": "text", **empty_shape}, [str(error)]
        return ["documentation"], "sampled", {"kind": "text", **empty_shape, "line_count": line_count}, issues
    if format_name in _SOURCE_TEXT_FORMATS:
        try:
            line_count = sum(1 for _line in path.open("r", encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            return ["domain_file"], "invalid", {"kind": "text", **empty_shape}, [str(error)]
        return ["domain_file"], "sampled", {
            "kind": "text", **empty_shape, "line_count": line_count,
        }, issues
    if format_name in _DOMAIN_FORMATS:
        return ["domain_file"], "unsupported", {"kind": "binary", **empty_shape}, issues
    return ["unknown"], "unsupported", {"kind": "unknown", **empty_shape}, ["没有可用的轻量解析器"]


def _source_mapping(source_plan: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for source in source_plan.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            continue
        source_id = str(source["source_id"])
        for path in source.get("raw_files", []):
            if isinstance(path, str):
                result[path] = source_id
    return result


def _is_usable_file(item: dict[str, Any]) -> bool:
    """未知或占位内容不能仅凭“文件存在”成为可用来源。"""

    return (
        item.get("parse_status") not in {"invalid", "placeholder"}
        and "unknown" not in item.get("content_roles", [])
    )


def build_source_inventory(
    run_dir: Path,
    *,
    seed_global_id: str,
    seed_sha256: str,
    source_plan: dict[str, Any],
) -> dict[str, Any]:
    """画像每个 Raw 的真实结构；不会形成或推荐最终环境模型。"""

    from env_gen.data_gen.steps.common.workspace_files import file_sha256

    workspace = run_dir.resolve() / "workspace"
    mapping = _source_mapping(source_plan)
    retrievals = _retrieval_profiles(run_dir.resolve())
    file_profiles: list[dict[str, Any]] = []
    for relative, source_id in sorted(mapping.items()):
        path = workspace / relative
        if not path.is_file():
            continue
        format_name = _format_name(path)
        content_roles, parse_status, shape, issues = _shape(path, format_name)
        file_profiles.append({
            "path": relative,
            "source_id": source_id,
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
            "format": format_name,
            "retrieval_stability": retrievals.get(relative, {}).get(
                "retrieval_stability", "untracked"
            ),
            "retrieval_urls": retrievals.get(relative, {}).get("retrieval_urls", []),
            "content_roles": content_roles,
            "parse_status": parse_status,
            "shape": shape,
            "issues": issues,
        })

    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in file_profiles:
        by_source.setdefault(str(item["source_id"]), []).append(item)
    source_profiles: list[dict[str, Any]] = []
    source_items = [
        item
        for item in source_plan.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    ]
    for source in source_items:
        source_id = str(source["source_id"])
        items = by_source.get(source_id, [])
        usable = sum(_is_usable_file(item) for item in items)
        structured_count = sum(
            int(group["observed_record_count"])
            for item in items
            for group in item["shape"]["record_groups"]
        )
        issues = sorted({issue for item in items for issue in item["issues"]})
        status = (
            "not_collected" if not items
            else "unusable" if not usable
            else "usable" if usable == len(items)
            else "partial"
        )
        source_profiles.append({
            "source_id": source_id,
            "file_paths": [str(item["path"]) for item in items],
            "file_count": len(items),
            "total_bytes": sum(int(item["bytes"]) for item in items),
            "usable_file_count": usable,
            "structured_record_count": structured_count,
            "formats": sorted({str(item["format"]) for item in items}),
            "retrieval_stability_counts": {
                stability: sum(item["retrieval_stability"] == stability for item in items)
                for stability in (
                    "immutable_repository", "mutable_repository",
                    "timestamped_snapshot", "untracked",
                )
            },
            "source_status": str(source.get("status") or "unknown"),
            "profile_status": status,
            "issues": issues,
        })
    return {
        "schema_version": "1.0",
        "seed_global_id": seed_global_id,
        "seed_sha256": seed_sha256,
        "summary": {
            "source_count": len(source_profiles),
            "file_count": len(file_profiles),
            "total_bytes": sum(int(item["bytes"]) for item in file_profiles),
            "usable_file_count": sum(
                _is_usable_file(item)
                for item in file_profiles
            ),
            "structured_record_count": sum(int(item["structured_record_count"]) for item in source_profiles),
            "formats": sorted({str(item["format"]) for item in file_profiles}),
            "retrieval_stability_counts": {
                stability: sum(
                    item["retrieval_stability"] == stability for item in file_profiles
                )
                for stability in (
                    "immutable_repository", "mutable_repository",
                    "timestamped_snapshot", "untracked",
                )
            },
        },
        "sources": source_profiles,
        "files": file_profiles,
    }


def validate_source_inventory(payload: dict[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


__all__ = ["build_source_inventory", "validate_source_inventory"]
