"""统计完整环境集合中的资源、实体、工具和实际工作区文件。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ENTITY_DATA_KEYS = {
    "account": "accounts",
    "journal_entry": "journal_entries",
    "bank_transaction": "bank_transactions",
    "source_document": "documents",
    "review_item": "items",
    "adjustment_schedule": "adjustment_schedules",
    "coa": "coas",
    "workspace_correction": "workspace_corrections",
    "bug": "bugs",
    "bug_test_link": "links",
    "comment": "comments",
    "test_case": "test_cases",
    "test_case_folder": "test_case_folders",
    "test_case_review_flag": "review_flags",
    "test_suite": "test_suites",
    "appearance": "appearances",
    "company": "companies",
    "folder": "folders",
    "person": "people",
    "project": "projects",
    "read_file": "read_files",
    "transcription": "transcriptions",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def matched_files(workspace: Path, resource: dict[str, Any]) -> list[Path]:
    path = resource["path"].rstrip("/")
    if resource["storage_type"] == "file":
        candidate = workspace / path
        return [candidate] if candidate.is_file() else []
    if resource["storage_type"] == "file_collection":
        return sorted(candidate for candidate in workspace.glob(path) if candidate.is_file())
    directory = workspace / path
    return sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()) if directory.is_dir() else []


def entity_counts(resource: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    path = workspace / resource["path"]
    payload = read_json(path)
    result = []
    for entity_name, definition in resource["entity_schema"].items():
        data_key = ENTITY_DATA_KEYS.get(entity_name, entity_name)
        records = payload.get(data_key) if isinstance(payload, dict) else None
        if records is None and isinstance(payload, list) and len(resource["entity_schema"]) == 1:
            records = payload
            data_key = "$"
        result.append(
            {
                "entity_name": entity_name,
                "description": definition["description"],
                "data_key": data_key,
                "field_count": len(definition["fields"]),
                "record_count": len(records) if isinstance(records, list) else None,
            }
        )
    return result


def summarize_environment(root: Path, index_item: dict[str, Any]) -> dict[str, Any]:
    environment_path = root / index_item["path"]
    package = environment_path.parent
    workspace = package / "workspace"
    environment = read_json(environment_path)
    resource_details = []
    entities = []
    for resource in environment["resources"]:
        files = matched_files(workspace, resource)
        detail = {
            "resource_id": resource["resource_id"],
            "name": resource["name"],
            "data_type": resource["data_type"],
            "storage_type": resource["storage_type"],
            "format": resource["format"],
            "writable": resource["writable"],
            "path": resource["path"],
            "file_count": len(files),
            "byte_count": sum(path.stat().st_size for path in files),
        }
        if resource["data_type"] == "entity":
            counts = entity_counts(resource, workspace)
            detail["entities"] = counts
            entities.extend({"resource_id": resource["resource_id"], **item} for item in counts)
        resource_details.append(detail)

    workspace_files = [path for path in workspace.rglob("*") if path.is_file()]
    data_types = Counter(item["data_type"] for item in environment["resources"])
    storage_types = Counter(item["storage_type"] for item in environment["resources"])
    formats = Counter(item["format"] for item in environment["resources"])
    return {
        "environment_id": environment["environment_id"],
        "name": environment["name"],
        "source_mcp": index_item.get("source_mcp"),
        "environment_path": index_item["path"],
        "resource_count": len(environment["resources"]),
        "resources_by_data_type": dict(sorted(data_types.items())),
        "resources_by_storage_type": dict(sorted(storage_types.items())),
        "resources_by_format": dict(sorted(formats.items())),
        "writable_resource_count": sum(1 for item in environment["resources"] if item["writable"]),
        "rule_count": len(environment["rules"]),
        "tool_count": len(environment["tools"]),
        "tool_names": [item["name"] for item in environment["tools"]],
        "entity_resource_count": sum(1 for item in environment["resources"] if item["data_type"] == "entity"),
        "entity_type_count": len(entities),
        "entity_record_count": sum(item["record_count"] or 0 for item in entities),
        "entities": entities,
        "workspace_file_count": len(workspace_files),
        "workspace_byte_count": sum(path.stat().st_size for path in workspace_files),
        "resources": resource_details,
    }


def open_source_records(root: Path, environments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for environment in environments:
        package = (root / environment["environment_path"]).parent
        path = package / "provenance" / "open_source_provenance.json"
        if not path.exists():
            continue
        for source in read_json(path).get("sources", []):
            records.append({"environment_id": environment["environment_id"], **source})
    return records


def render_markdown(statistics: dict[str, Any]) -> str:
    overall = statistics["overall"]
    lines = [
        "# MCP Test 3 环境信息统计",
        "",
        "统计对象为各环境 `environment.json` 和对应 `workspace/` 的实际内容。实体数量来自实体文件中的真实数组，不是根据 Schema 推测。",
        "",
        "## 总览",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f'| 环境 | {overall["environment_count"]} |',
        f'| 工具 | {overall["tool_count"]} |',
        f'| 资源 | {overall["resource_count"]} |',
        f'| 业务规则 | {overall["rule_count"]} |',
        f'| 实体资源 | {overall["entity_resource_count"]} |',
        f'| 实体类型 | {overall["entity_type_count"]} |',
        f'| 实体记录 | {overall["entity_record_count"]} |',
        f'| 工作区文件 | {overall["workspace_file_count"]} |',
        f'| 工作区文件大小 | {human_bytes(overall["workspace_byte_count"])} |',
        f'| 开源原始样本 | {overall["open_source_file_count"]} |',
        "",
        "## 环境对比",
        "",
        "| 环境 | MCP 种子 | 资源 | 实体类型 | 实体记录 | 工具 | 规则 | 工作区文件 | 大小 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for environment in statistics["environments"]:
        lines.append(
            f'| {environment["name"]} | `{environment["source_mcp"]}` | {environment["resource_count"]} | '
            f'{environment["entity_type_count"]} | {environment["entity_record_count"]} | {environment["tool_count"]} | '
            f'{environment["rule_count"]} | {environment["workspace_file_count"]} | {human_bytes(environment["workspace_byte_count"])} |'
        )

    for environment in statistics["environments"]:
        lines.extend(
            [
                "",
                f'## {environment["name"]}',
                "",
                f'- 环境 ID：`{environment["environment_id"]}`',
                f'- 资源类型：`{json.dumps(environment["resources_by_data_type"], ensure_ascii=False)}`',
                f'- 存储类型：`{json.dumps(environment["resources_by_storage_type"], ensure_ascii=False)}`',
                f'- 可写资源：{environment["writable_resource_count"]}',
                f'- 工具数量：{environment["tool_count"]}',
                "",
                "### 实体记录",
                "",
                "| 实体资源 | 实体类型 | 数据键 | 字段数 | 记录数 |",
                "| --- | --- | --- | ---: | ---: |",
            ]
        )
        for entity in environment["entities"]:
            count = "无法识别" if entity["record_count"] is None else str(entity["record_count"])
            lines.append(
                f'| `{entity["resource_id"]}` | `{entity["entity_name"]}` | `{entity["data_key"]}` | '
                f'{entity["field_count"]} | {count} |'
            )
        lines.extend(["", "### 工具", "", ", ".join(f'`{name}`' for name in environment["tool_names"]), ""])

    lines.extend(
        [
            "## 开源数据",
            "",
            "| 环境 | 项目 | 文件 | 许可证 | 大小 | SHA-256 |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for source in statistics["open_source_files"]:
        lines.append(
            f'| `{source["environment_id"]}` | {source["project"]} | `{source["file"]}` | '
            f'{source["license"]} | {human_bytes(source["size"])} | `{source["sha256"]}` |'
        )
    lines.append("")
    return "\n".join(lines)


def summarize(root: Path) -> dict[str, Any]:
    index = read_json(root / "index.json")
    environments = [summarize_environment(root, item) for item in index["environments"]]
    sources = open_source_records(root, environments)
    overall = {
        "environment_count": len(environments),
        "tool_count": sum(item["tool_count"] for item in environments),
        "resource_count": sum(item["resource_count"] for item in environments),
        "rule_count": sum(item["rule_count"] for item in environments),
        "entity_resource_count": sum(item["entity_resource_count"] for item in environments),
        "entity_type_count": sum(item["entity_type_count"] for item in environments),
        "entity_record_count": sum(item["entity_record_count"] for item in environments),
        "workspace_file_count": sum(item["workspace_file_count"] for item in environments),
        "workspace_byte_count": sum(item["workspace_byte_count"] for item in environments),
        "open_source_file_count": len(sources),
        "open_source_byte_count": sum(item["size"] for item in sources),
    }
    return {"schema_version": "1.0", "overall": overall, "environments": environments, "open_source_files": sources}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="包含 index.json 和各环境目录的集合根目录")
    args = parser.parse_args()
    root = args.root.resolve()
    statistics = summarize(root)
    write_json(root / "environment_statistics.json", statistics)
    (root / "环境信息统计.md").write_text(render_markdown(statistics), encoding="utf-8")

    index = read_json(root / "index.json")
    # 集合目录只保存生成样本。正式契约由仓库中的 schemas/ 统一维护，
    # 不复制到每个环境集合，也不在索引中建立易失效的相对路径依赖。
    for key in ("schema", "schema_dependencies", "contract", "contract_dependencies"):
        index.pop(key, None)
    index["schema_version"] = "1.0"
    index["statistics"] = "environment_statistics.json"
    index["statistics_report"] = "环境信息统计.md"
    write_json(root / "index.json", index)
    print(json.dumps(statistics["overall"], ensure_ascii=False))


if __name__ == "__main__":
    main()
