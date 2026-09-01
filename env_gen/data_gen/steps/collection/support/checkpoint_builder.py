"""根据 workspace 事实构造实时 checkpoint。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...common.constants import CONTROL_DOWNLOAD_RECEIPTS
from ...common.control_io import control_path, read_json
from ...common.workspace_files import workspace_files

_control_path = control_path
_read_json = read_json
_workspace_files = workspace_files


def _checkpoint_from_workspace(
    run_dir: Path,
    *,
    seed_global_id: str,
    seed_sha256: str,
    source_plan: dict[str, Any],
    status: str = "ready",
) -> dict[str, Any]:
    """根据真实文件和来源计划建立 checkpoint，不接受 Agent 自报文件清单。"""

    files = _workspace_files(run_dir)
    files_by_url: dict[str, list[str]] = {}
    source_urls: list[str] = []
    receipt_urls: dict[str, str] = {}
    receipt_path = _control_path(run_dir, CONTROL_DOWNLOAD_RECEIPTS)
    if receipt_path.is_file():
        receipts = _read_json(receipt_path, "下载收据")
        for item in receipts.get("downloads", []):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            url = item.get("url")
            if isinstance(path, str) and isinstance(url, str) and path and url:
                effective_url = item.get("effective_url")
                receipt_urls[path] = (
                    effective_url
                    if isinstance(effective_url, str) and effective_url
                    else url
                )
    for source in source_plan.get("sources", []):
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        raw_files = [
            value
            for value in source.get("raw_files", [])
            if isinstance(value, str)
        ]
        if isinstance(url, str) and url:
            source_urls.append(url)
            for raw_path in raw_files:
                # 下载收据是程序实际访问 URL 的逐文件证据；没有收据时回退到
                # source plan 中登记的精确 URL，供校验器报告缺失收据。
                file_url = receipt_urls.get(raw_path, url)
                if file_url not in source_urls:
                    source_urls.append(file_url)
                mapped = files_by_url.setdefault(file_url, [])
                if raw_path not in mapped:
                    mapped.append(raw_path)
    source_file_map = [
        {"url": url, "file_paths": file_paths}
        for url, file_paths in files_by_url.items()
    ]

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "seed_global_id": seed_global_id,
        "seed_sha256": seed_sha256,
        "status": status,
        "summary": (
            "Codex 完成数据采集；提交点由 datagenctl 根据实际 workspace 和来源清单生成。"
            if status == "ready"
            else "Codex 访问允许的公开来源后，未找到足以支撑核心要求的真实数据。"
        ),
        "raw_files": files["raw"],
        "entity_files": files["entities"],
        "derived_files": files["derived"],
        "source_urls": list(dict.fromkeys(source_urls)),
        "synthetic_business_record_count": 0,
    }
    if status == "ready":
        payload["source_file_map"] = source_file_map
    return payload

checkpoint_from_workspace = _checkpoint_from_workspace

__all__ = ["_checkpoint_from_workspace", "checkpoint_from_workspace"]
