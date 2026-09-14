#!/usr/bin/env python3
"""Build a standalone visual report for published DataGen v2 environments."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import sqlite3
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {
    ".csv", ".html", ".json", ".jsonl", ".md", ".sol", ".txt",
    ".xml", ".yaml", ".yml",
}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def read_optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.is_file() else {}


def json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return value


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_snapshot(database: Path, table: str) -> tuple[int, list[dict[str, Any]]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        quoted = quote_identifier(table)
        count = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
        rows = connection.execute(f"SELECT * FROM {quoted} LIMIT 3").fetchall()
    return count, [
        {key: json_value(row[key]) for key in row.keys()}
        for row in rows
    ]


def relationship_snapshots(
    database: Path, relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(database) as connection:
        for item in relationships:
            source = item.get("from", {})
            target = item.get("to", {})
            source_id = str(source.get("record_set_id") or "")
            target_id = str(target.get("record_set_id") or "")
            source_fields = [str(value) for value in source.get("fields", [])]
            target_fields = [str(value) for value in target.get("fields", [])]
            matched = missing = 0
            if source_id and target_id and source_fields and len(source_fields) == len(target_fields):
                joins = " AND ".join(
                    f"s.{quote_identifier(left)} = t.{quote_identifier(right)}"
                    for left, right in zip(source_fields, target_fields, strict=True)
                )
                populated = " AND ".join(
                    f"s.{quote_identifier(field)} IS NOT NULL" for field in source_fields
                )
                target_present = f"t.{quote_identifier(target_fields[0])} IS NOT NULL"
                query = (
                    "SELECT "
                    f"COALESCE(SUM(CASE WHEN {populated} AND {target_present} THEN 1 ELSE 0 END), 0), "
                    f"COALESCE(SUM(CASE WHEN {populated} AND NOT ({target_present}) THEN 1 ELSE 0 END), 0) "
                    f"FROM {quote_identifier(source_id)} AS s "
                    f"LEFT JOIN {quote_identifier(target_id)} AS t ON {joins}"
                )
                matched, missing = (int(value) for value in connection.execute(query).fetchone())
            rows.append({
                "relationship_id": item.get("relationship_id"),
                "description": item.get("description", ""),
                "cardinality": item.get("cardinality", ""),
                "from_record_set_id": source_id,
                "to_record_set_id": target_id,
                "matched_reference_count": matched,
                "missing_reference_count": missing,
                "valid": missing == 0,
            })
    return rows


def direct_coverage_needs(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    coverage = receipt.get("coverage", {})
    rows = []
    for key, label, minimum in (
        ("seed", "Seed 业务能力覆盖", 90),
        ("scenario", "Step 1 场景覆盖", 75),
    ):
        metric = coverage.get(key, {}).get("overall", {})
        percent = float(metric.get("percent") or 0)
        rows.append({
            "need_id": key + "_coverage",
            "description": (
                f"{label}：{metric.get('supported', 0)}/{metric.get('total', 0)}，"
                f"当前 {percent:g}%，完整环境最低线 {minimum}%。"
            ),
            "record_set_ids": [],
            "scope_ids": [],
            "status": "realized" if percent >= minimum else "partial",
        })
    return rows


def field_type(definition: dict[str, Any]) -> str:
    value = str(definition.get("type") or "unknown")
    if value == "array" and isinstance(definition.get("items"), dict):
        value += f"<{definition['items'].get('type', 'unknown')}>"
    return value


def _join_scope_path(prefix: str, value: str) -> str:
    value = value.strip().strip("/")
    if value in {"", "."}:
        return prefix
    return f"{prefix}/{value}" if prefix else value


def scope_file_rules(
    structure: dict[str, Any], *, prefix: str = "",
) -> list[dict[str, Any]]:
    """Flatten a declared filesystem layout into matchable file rules."""

    if not isinstance(structure, dict):
        return []
    kind = str(structure.get("kind") or "")
    current = _join_scope_path(prefix, str(structure.get("path") or "."))
    if kind in {"file", "file_collection"}:
        return [{
            "pattern": current or "**",
            "kind": kind,
            "description": str(structure.get("description") or ""),
            "format": str(structure.get("format") or ""),
            "content_validation": str(structure.get("content_validation") or ""),
            "required": bool(structure.get("required")),
        }]
    rules: list[dict[str, Any]] = []
    for child in structure.get("layout", []):
        if isinstance(child, dict):
            rules.extend(scope_file_rules(child, prefix=current))
    return rules


def _scope_rule_for_file(path: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [rule for rule in rules if fnmatchcase(path, str(rule["pattern"]))]
    if not candidates:
        return {}
    return max(
        candidates,
        key=lambda item: len(str(item["pattern"]).replace("*", "").replace("?", "")),
    )


def file_operation_note(path: Path) -> str:
    """Explain the practical role of common scientific and layout files."""

    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "poscar":
        return "作为计算输入晶体结构，可检查或修改晶格、元素和原子坐标。"
    if name == "contcar":
        return "保存计算后的结构，可与输入结构比较弛豫、畸变和位点变化。"
    if name == "outcar":
        return "用于审计计算参数、能量、电子数、收敛过程及其他详细输出。"
    if name == "vasprun.xml":
        return "可由材料计算工具解析结构、参数、能量、能带、DOS 和收敛结果。"
    if name == "locpot":
        return "保存局域电势网格，可用于电势对齐和带电缺陷有限尺寸修正。"
    if name.startswith("wswq"):
        return "保存波函数重叠数据，可用于构型坐标有限差分和非辐射过程分析。"
    if name == "incar":
        return "VASP 计算参数文件，可检查或修改计算协议与收敛设置。"
    if name == "kpoints":
        return "定义倒空间采样或高对称路径，可用于检查和调整计算采样。"
    if suffix in {".gds", ".gdsii"}:
        return "芯片版图几何原件，可读取层级和图形、执行几何修复并写出修订版。"
    if suffix in {".oas", ".oasis"}:
        return "OASIS 层级版图，可用于格式读取、几何检查和 GDS/OASIS 转换验证。"
    if suffix == ".lyrdb":
        return "KLayout DRC 报告数据库，可定位规则类别、单元和违规 marker。"
    if suffix == ".drc":
        return "KLayout DRC 规则或共享规则定义，可用于运行和比较验证规则。"
    if suffix == ".lyt":
        return "KLayout 工艺技术定义，连接数据库单位、图层属性和版图格式。"
    if suffix == ".lyp":
        return "KLayout 图层属性文件，说明工艺层名称、编号和显示配置。"
    if suffix == ".map":
        return "工艺层与 GDS layer/datatype 的映射，可校验跨文件图层身份。"
    if suffix == ".cif":
        return "晶体结构原件，可解析、标准化、转换并与材料性质记录关联。"
    if suffix in {".in", ".pw"}:
        return "第一性原理计算输入，可检查结构、赝势、采样和计算阶段设置。"
    if suffix in {".out", ".log"}:
        return "计算或流程输出日志，可检查运行结果、收敛状态和异常信息。"
    if suffix in {".json", ".jsonl", ".csv", ".yaml", ".yml"}:
        return "结构化业务数据，可按标识、字段和关系查询、比较或重新计算。"
    return "保留的领域原件，可按所在 Scope 的工作流语义读取、比较或复制后修改。"


def scope_files(package: Path, scope: dict[str, Any]) -> list[dict[str, Any]]:
    scope_id = str(scope.get("scope_id") or "")
    root = package / "state/filesystem_scopes" / scope_id
    if not root.is_dir():
        return []
    rules = scope_file_rules(scope.get("structure", {}))
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        preview = None
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 2 * 1024 * 1024:
            preview = path.read_text(encoding="utf-8", errors="replace")[:1600]
        relative = path.relative_to(root).as_posix()
        rule = _scope_rule_for_file(relative, rules)
        rows.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "format": path.suffix.lower().lstrip(".") or "file",
            "preview": preview,
            "description": rule.get("description") or "该文件属于当前工作区中保留的领域原件。",
            "declaredFormat": rule.get("format") or None,
            "validation": rule.get("content_validation") or None,
            "pattern": rule.get("pattern") or None,
            "operationNote": file_operation_note(path),
        })
    return rows


def build_environment(
    package: Path, *, annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    environment = read_json(package / "environment.json")
    validation = read_optional_json(package / "validation.json")
    quality = read_optional_json(package / "provenance/quality_profile.json")
    integration = read_optional_json(package / "provenance/integration_profile.json")
    plan = read_optional_json(package / "provenance/integration_plan.json")
    direct_receipt = read_optional_json(package / "provenance/integration_receipt.json")
    assessment = read_optional_json(package / ".datagen/integration_assessment.json")
    finalization = read_optional_json(package / ".datagen/integration_finalization.json")
    collection = read_optional_json(package / ".datagen/collection_profile.json")
    if not direct_receipt:
        direct_receipt = assessment
    database = package / "state/records.sqlite"

    record_sets: list[dict[str, Any]] = []
    for item in environment.get("record_sets", []):
        if not isinstance(item, dict):
            continue
        record_set_id = str(item.get("record_set_id") or "")
        count, samples = table_snapshot(database, record_set_id)
        fields = []
        for name, definition in item.get("fields", {}).items():
            if not isinstance(definition, dict):
                continue
            reference = definition.get("reference")
            fields.append({
                "name": name,
                "type": field_type(definition),
                "nullable": bool(definition.get("nullable")),
                "description": definition.get("description", ""),
                "reference": reference if isinstance(reference, dict) else None,
            })
        record_sets.append({
            "id": record_set_id,
            "name": item.get("name") or record_set_id,
            "description": item.get("description", ""),
            "importance": item.get("importance", "business"),
            "access": item.get("access", "read_only"),
            "keyFields": item.get("key_fields", []),
            "count": count,
            "fields": fields,
            "samples": samples,
        })

    scopes = []
    for scope in environment.get("filesystem_scopes", []):
        if not isinstance(scope, dict):
            continue
        files = scope_files(package, scope)
        scopes.append({
            "id": scope.get("scope_id"),
            "name": scope.get("name") or scope.get("scope_id"),
            "description": scope.get("description", ""),
            "access": scope.get("access", "read_only"),
            "structure": scope.get("structure", {}),
            "files": files,
            "bytes": sum(int(item["bytes"]) for item in files),
        })

    relationships = integration.get("relationship_profile", {}).get("relationships", [])
    if not relationships:
        relationships = relationship_snapshots(database, environment.get("relationships", []))
    for record_set in record_sets:
        record_set_id = record_set["id"]
        record_set["linksTo"] = sorted({
            str(item.get("to_record_set_id"))
            for item in relationships
            if item.get("from_record_set_id") == record_set_id
            and item.get("to_record_set_id")
        })
        record_set["linkedFrom"] = sorted({
            str(item.get("from_record_set_id"))
            for item in relationships
            if item.get("to_record_set_id") == record_set_id
            and item.get("from_record_set_id")
        })
    needs = plan.get("need_bindings", []) or direct_coverage_needs(direct_receipt)
    source_decisions = plan.get("source_decisions", [])
    direct_coverage = direct_receipt.get("coverage", {})
    scenario_coverage = direct_coverage.get("scenario", {}).get("overall", {}).get("percent", 0)
    source_count = len({
        str(item.get("source_id"))
        for item in direct_receipt.get("raw_files", [])
        if isinstance(item, dict) and item.get("source_id")
    })
    if not source_count:
        source_count = len({
            str(item.get("source_id"))
            for item in collection.get("file_cards", [])
            if isinstance(item, dict) and item.get("source_id")
        })
    state_verified = (
        assessment.get("decision") == "ready"
        and bool(assessment.get("state_digest"))
        and (
            not assessment.get("replay_state_digest")
            or assessment.get("state_digest") == assessment.get("replay_state_digest")
        )
    )
    valid = validation.get("valid") is True or state_verified
    quality_tier = (
        quality.get("quality_tier")
        or validation.get("quality_tier")
        or finalization.get("result")
        or "unknown"
    )
    integration_tier = (
        integration.get("integration_tier")
        or validation.get("integration_tier")
        or ("integrated" if state_verified else "unknown")
    )
    return {
        "id": environment.get("environment_id") or package.name,
        "packageName": package.name,
        "name": environment.get("name") or package.name,
        "summary": environment.get("summary", ""),
        "description": environment.get("description", ""),
        "guide": (annotations or {}).get(package.name, {}),
        "qualityTier": quality_tier,
        "integrationTier": integration_tier,
        "valid": valid,
        "recordSets": record_sets,
        "scopes": scopes,
        "relationships": relationships,
        "needs": needs,
        "sourceDecisions": source_decisions,
        "metrics": {
            "records": sum(item["count"] for item in record_sets),
            "coreRecords": quality.get("record_profile", {}).get("core_record_count", 0),
            "recordSets": len(record_sets),
            "relationships": len(relationships),
            "files": sum(len(item["files"]) for item in scopes),
            "fileBytes": sum(item["bytes"] for item in scopes),
            "needCoverage": quality.get("need_profile", {}).get("weighted_coverage_percent", scenario_coverage),
            "sources": integration.get("source_integration_profile", {}).get("selected_source_count", source_count),
        },
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentWorld 环境数据预览</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f6f7;
  --surface: #ffffff;
  --surface-2: #eef2f3;
  --line: #d5dcdf;
  --line-strong: #aeb9bd;
  --text: #172126;
  --muted: #617076;
  --green: #16784c;
  --green-bg: #e5f4ec;
  --blue: #215f9a;
  --blue-bg: #e8f1f8;
  --amber: #946200;
  --amber-bg: #fbf0cf;
  --red: #a43c38;
  --red-bg: #f9e9e7;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); }
body { min-width: 320px; }
button { font: inherit; letter-spacing: 0; }
.app { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }
.sidebar { position: sticky; top: 0; height: 100vh; overflow: auto; background: #202b30; color: #fff; border-right: 1px solid #11191d; }
.brand { padding: 22px 20px 18px; border-bottom: 1px solid #3b474c; }
.brand h1 { margin: 0; font-size: 17px; font-weight: 680; }
.brand p { margin: 6px 0 0; color: #b9c4c8; font-size: 12px; }
.env-list { padding: 12px; display: grid; gap: 8px; }
.env-button { width: 100%; min-height: 84px; padding: 12px; border: 1px solid #455258; border-radius: 5px; background: #29363b; color: #fff; text-align: left; cursor: pointer; }
.env-button:hover { border-color: #829096; }
.env-button.active { background: #f7faf9; color: var(--text); border-color: #f7faf9; }
.env-button strong { display: block; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
.env-button small { display: block; margin-top: 7px; color: #b9c4c8; font-size: 11px; }
.env-button.active small { color: var(--muted); }
.main { min-width: 0; }
.topbar { background: var(--surface); border-bottom: 1px solid var(--line); padding: 22px clamp(18px, 3vw, 42px) 0; }
.title-row { display: flex; gap: 18px; align-items: flex-start; justify-content: space-between; }
.title-row h2 { margin: 0; font-size: clamp(21px, 2.3vw, 30px); line-height: 1.2; letter-spacing: 0; }
.title-row p { margin: 8px 0 18px; max-width: 900px; color: var(--muted); font-size: 14px; line-height: 1.65; }
.status { display: flex; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }
.badge { display: inline-flex; align-items: center; min-height: 25px; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; white-space: nowrap; }
.badge.green { color: var(--green); background: var(--green-bg); }
.badge.blue { color: var(--blue); background: var(--blue-bg); }
.badge.amber { color: var(--amber); background: var(--amber-bg); }
.tabs { display: flex; gap: 4px; overflow-x: auto; }
.tab { min-width: 92px; height: 42px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: var(--muted); cursor: pointer; }
.tab.active { color: var(--text); border-bottom-color: var(--green); font-weight: 700; }
.content { padding: 24px clamp(18px, 3vw, 42px) 52px; max-width: 1500px; margin: 0 auto; }
.metric-grid { display: grid; grid-template-columns: repeat(6, minmax(100px, 1fr)); border: 1px solid var(--line); background: var(--surface); }
.metric { padding: 15px 16px; border-right: 1px solid var(--line); min-width: 0; }
.metric:last-child { border-right: 0; }
.metric span { display: block; color: var(--muted); font-size: 11px; }
.metric strong { display: block; margin-top: 5px; font-size: 21px; overflow-wrap: anywhere; }
.section { margin-top: 28px; }
.section-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.section h3 { margin: 0; font-size: 16px; }
.section-head p { margin: 0; color: var(--muted); font-size: 12px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.explain-panel { padding: 22px; }
.explain-intro { max-width: 1040px; }
.eyebrow { display: block; color: var(--green); font-size: 11px; font-weight: 750; text-transform: uppercase; }
.explain-intro h3 { margin: 7px 0 8px; font-size: 18px; }
.explain-intro p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
.positioning { margin-top: 10px !important; color: var(--text) !important; }
.explain-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 20px -22px -22px; border-top: 1px solid var(--line); }
.explain-block { padding: 18px 22px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.explain-block:nth-child(2n) { border-right: 0; }
.explain-block:nth-last-child(-n+2) { border-bottom: 0; }
.explain-block h4, .entity-context h5, .file-explanation h5 { margin: 0 0 9px; font-size: 12px; }
.explain-list { margin: 0; padding-left: 19px; color: var(--muted); font-size: 12px; line-height: 1.65; }
.explain-list li + li { margin-top: 5px; }
.boundary-list { color: #765c18; }
.model-wrap { overflow: auto; padding: 14px; }
.model-svg { display: block; min-width: 760px; width: 100%; }
.need-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--line); }
.need { padding: 14px 16px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.need:nth-child(2n) { border-right: 0; }
.need strong { font: 600 12px var(--mono); overflow-wrap: anywhere; }
.need p { margin: 7px 0 0; color: var(--muted); font-size: 12px; line-height: 1.55; }
.need .binding { margin-top: 9px; display: flex; gap: 5px; flex-wrap: wrap; }
code, .code { font-family: var(--mono); }
.token { display: inline-block; padding: 3px 6px; border: 1px solid var(--line); border-radius: 3px; background: var(--surface-2); font: 11px var(--mono); overflow-wrap: anywhere; }
.split { display: grid; grid-template-columns: minmax(240px, 0.34fr) minmax(0, 1fr); min-height: 620px; }
.list { border-right: 1px solid var(--line); overflow: auto; max-height: 760px; }
.list-button { width: 100%; min-height: 70px; padding: 12px 14px; border: 0; border-bottom: 1px solid var(--line); background: #fff; text-align: left; cursor: pointer; }
.list-button:hover { background: #f7f9f9; }
.list-button.active { background: var(--green-bg); box-shadow: inset 3px 0 var(--green); }
.list-button strong { display: block; font: 600 12px var(--mono); overflow-wrap: anywhere; }
.list-button small { display: block; margin-top: 6px; color: var(--muted); }
.detail { min-width: 0; padding: 20px; overflow: auto; }
.detail h4 { margin: 0; font-size: 17px; }
.detail .desc { margin: 8px 0 18px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.entity-context, .file-explanation { margin: 18px 0; padding: 15px 16px; border: 1px solid var(--line); background: #f8faf9; }
.entity-context p, .file-explanation p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }
.entity-context p + p, .file-explanation p + p { margin-top: 8px; }
.context-links, .file-meta { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { padding: 9px 10px; color: var(--muted); background: var(--surface-2); border-bottom: 1px solid var(--line); text-align: left; font-weight: 650; }
td { padding: 10px; border-bottom: 1px solid var(--line); vertical-align: top; line-height: 1.5; overflow-wrap: anywhere; }
tr:last-child td { border-bottom: 0; }
.table-scroll { overflow: auto; border: 1px solid var(--line); }
.sample-tabs { display: flex; gap: 5px; margin: 18px 0 8px; }
.sample-tab { border: 1px solid var(--line); background: #fff; border-radius: 4px; padding: 6px 9px; cursor: pointer; font-size: 11px; }
.sample-tab.active { border-color: var(--blue); color: var(--blue); background: var(--blue-bg); }
pre { margin: 0; max-height: 360px; overflow: auto; padding: 14px; background: #172126; color: #e8eff1; font: 11px/1.55 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.relations td:first-child, .relations td:nth-child(2), .relations td:nth-child(3) { font-family: var(--mono); }
.file-layout { display: grid; grid-template-columns: minmax(260px, .38fr) minmax(0, 1fr); min-height: 620px; }
.scope-column { border-right: 1px solid var(--line); }
.scope-head { padding: 16px; border-bottom: 1px solid var(--line); background: var(--surface-2); }
.scope-head h4 { margin: 0; font-size: 13px; }
.scope-head p { margin: 7px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
.file-button { width: 100%; min-height: 52px; padding: 10px 14px; border: 0; border-bottom: 1px solid var(--line); background: #fff; text-align: left; cursor: pointer; }
.file-button.active { background: var(--blue-bg); box-shadow: inset 3px 0 var(--blue); }
.file-button code { display: block; font-size: 11px; overflow-wrap: anywhere; }
.file-button small { display: block; margin-top: 4px; color: var(--muted); }
.file-button .file-summary { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.empty { padding: 42px; text-align: center; color: var(--muted); }
@media (max-width: 1050px) {
  .metric-grid { grid-template-columns: repeat(3, 1fr); }
  .metric:nth-child(3) { border-right: 0; }
  .metric:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
}
@media (max-width: 760px) {
  .app { display: block; }
  .sidebar { position: static; height: auto; }
  .env-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .title-row { display: block; }
  .status { justify-content: flex-start; margin-bottom: 10px; }
  .split, .file-layout { grid-template-columns: 1fr; }
  .list, .scope-column { border-right: 0; border-bottom: 1px solid var(--line); max-height: 280px; }
  .need-grid { grid-template-columns: 1fr; }
  .need { border-right: 0; }
  .explain-grid { grid-template-columns: 1fr; }
  .explain-block { border-right: 0; }
  .explain-block:nth-last-child(-n+2) { border-bottom: 1px solid var(--line); }
  .explain-block:last-child { border-bottom: 0; }
}
@media (max-width: 500px) {
  .env-list { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .metric { border-bottom: 1px solid var(--line); }
  .metric:nth-child(2n) { border-right: 0; }
  .metric:nth-last-child(-n+2) { border-bottom: 0; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><h1>AgentWorld 环境数据</h1><p>主体实体与非主体文件 · v2</p></div>
    <div class="env-list" id="envList"></div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="title-row">
        <div><h2 id="title"></h2><p id="description"></p></div>
        <div class="status" id="status"></div>
      </div>
      <nav class="tabs" id="tabs"></nav>
    </header>
    <div class="content" id="content"></div>
  </main>
</div>
<script>
const DATA = __DATA__;
const state = { env: 0, view: "overview", recordSet: null, sample: 0, scope: 0, file: 0 };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const fmt = (value) => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
const bytes = (value) => { const n=Number(value||0); if(n<1024)return `${n} B`; if(n<1048576)return `${(n/1024).toFixed(1)} KB`; return `${(n/1048576).toFixed(1)} MB`; };
const env = () => DATA.environments[state.env];
const badge = (text, tone) => `<span class="badge ${tone}">${esc(text)}</span>`;
const token = (text) => `<span class="token">${esc(text)}</span>`;

function renderSidebar() {
  document.getElementById("envList").innerHTML = DATA.environments.map((item, index) => `
    <button class="env-button ${index===state.env?'active':''}" data-env="${index}">
      <strong>${esc(item.name)}</strong>
      <small>${fmt(item.metrics.records)} 条记录 · ${fmt(item.metrics.files)} 个文件</small>
    </button>`).join("");
  document.querySelectorAll("[data-env]").forEach(button => button.onclick = () => {
    state.env = Number(button.dataset.env); state.recordSet = null; state.scope = 0; state.file = 0; render();
  });
}

function renderHeader() {
  const item=env();
  document.getElementById("title").textContent=item.name;
  document.getElementById("description").textContent=item.summary||item.description;
  document.getElementById("status").innerHTML = badge(item.qualityTier, item.qualityTier==='rich'?'green':'amber') + badge(item.integrationTier, 'blue') + badge(item.valid?'已验证':'验证失败', item.valid?'green':'red');
  const views=[["overview","总览"],["records","主体实体"],["relations","实体关系"],["files","非主体文件"]];
  document.getElementById("tabs").innerHTML=views.map(([id,label])=>`<button class="tab ${state.view===id?'active':''}" data-view="${id}">${label}</button>`).join("");
  document.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>{state.view=button.dataset.view;render();});
}

function metrics() {
  const m=env().metrics;
  return `<div class="metric-grid">
    <div class="metric"><span>主体记录</span><strong>${fmt(m.records)}</strong></div>
    <div class="metric"><span>主体实体类型</span><strong>${fmt(m.recordSets)}</strong></div>
    <div class="metric"><span>有效关系</span><strong>${fmt(m.relationships)}</strong></div>
    <div class="metric"><span>非主体文件</span><strong>${fmt(m.files)}</strong></div>
    <div class="metric"><span>需求覆盖</span><strong>${fmt(m.needCoverage)}%</strong></div>
    <div class="metric"><span>选中来源</span><strong>${fmt(m.sources)}</strong></div>
  </div>`;
}

function graphSvg(item) {
  const nodes=[...item.recordSets.map(r=>({id:`record:${r.id}`,label:r.id,kind:r.importance==='core'?'core':'record'})),...item.scopes.map(s=>({id:`scope:${s.id}`,label:s.id,kind:'scope'}))];
  const columns=3, nodeW=220, nodeH=52, gapX=70, gapY=48, margin=42;
  const rows=Math.ceil(nodes.length/columns), width=margin*2+columns*nodeW+(columns-1)*gapX, height=margin*2+rows*nodeH+(rows-1)*gapY;
  const pos={}; nodes.forEach((node,i)=>{const c=i%columns,r=Math.floor(i/columns);pos[node.id]={x:margin+c*(nodeW+gapX),y:margin+r*(nodeH+gapY)};});
  const edges=item.relationships.map(r=>({from:`record:${r.from_record_set_id}`,to:`record:${r.to_record_set_id}`,label:r.matched_reference_count}));
  item.recordSets.forEach(record=>record.fields.filter(f=>f.reference?.scope_id).forEach(f=>edges.push({from:`record:${record.id}`,to:`scope:${f.reference.scope_id}`,label:'path',file:true})));
  const lines=edges.filter(e=>pos[e.from]&&pos[e.to]).map(e=>{const a=pos[e.from],b=pos[e.to];const x1=a.x+nodeW/2,y1=a.y+nodeH/2,x2=b.x+nodeW/2,y2=b.y+nodeH/2;return `<g><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${e.file?'#215f9a':'#9aa7ac'}" stroke-width="2" ${e.file?'stroke-dasharray="5 4"':''}/><text x="${(x1+x2)/2}" y="${(y1+y2)/2-5}" text-anchor="middle" fill="#617076" font-size="10">${esc(e.label)}</text></g>`}).join('');
  const boxes=nodes.map(n=>{const p=pos[n.id],fill=n.kind==='core'?'#e5f4ec':n.kind==='scope'?'#e8f1f8':'#eef2f3',stroke=n.kind==='core'?'#16784c':n.kind==='scope'?'#215f9a':'#aeb9bd';return `<g><rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${nodeH}" rx="5" fill="${fill}" stroke="${stroke}"/><text x="${p.x+12}" y="${p.y+23}" fill="#172126" font-size="11" font-family="ui-monospace,monospace">${esc(n.label)}</text><text x="${p.x+12}" y="${p.y+40}" fill="#617076" font-size="10">${n.kind==='scope'?'Filesystem Scope':n.kind==='core'?'核心记录':'支持记录'}</text></g>`}).join('');
  return `<svg class="model-svg" viewBox="0 0 ${width} ${height}" height="${Math.min(520,height)}" role="img" aria-label="环境数据关系图">${lines}${boxes}</svg>`;
}

function overview() {
  const item=env();
  const guide=item.guide||{};
  const explainList=(values,ordered=false,tone='')=>{const tag=ordered?'ol':'ul';return `<${tag} class="explain-list ${tone}">${(values||[]).map(value=>`<li>${esc(value)}</li>`).join('')}</${tag}>`;};
  const explanation=`<section class="section"><div class="section-head"><h3>环境说明</h3><p>工作场景、数据基础和能力边界</p></div><div class="panel explain-panel"><div class="explain-intro"><span class="eyebrow">环境定位</span><h3>${esc(item.summary||item.name)}</h3><p>${esc(item.description)}</p>${guide.positioning?`<p class="positioning">${esc(guide.positioning)}</p>`:''}</div><div class="explain-grid"><div class="explain-block"><h4>真实工作流</h4>${explainList(guide.workflow||[])}</div><div class="explain-block"><h4>环境中的核心数据</h4>${explainList(guide.data_highlights||[])}</div><div class="explain-block"><h4>适合构造的复杂任务</h4>${explainList(guide.complex_tasks||[],true)}</div><div class="explain-block"><h4>当前数据边界</h4>${explainList(guide.boundaries||[],false,'boundary-list')}</div></div></div></section>`;
  const needs=item.needs.map(n=>`<div class="need"><strong>${esc(n.need_id)}</strong><p>${esc(n.description)}</p><div class="binding">${(n.record_set_ids||[]).map(token).join('')}${(n.scope_ids||[]).map(v=>token(`scope:${v}`)).join('')}${badge(n.status,n.status==='realized'?'green':'amber')}</div></div>`).join('');
  return `${metrics()}${explanation}<section class="section"><div class="section-head"><h3>环境主体与文件边界</h3><p>实线为主体关系，虚线连接非主体文件 Scope</p></div><div class="panel"><div class="model-wrap">${graphSvg(item)}</div><div class="need-grid">${needs}</div></div></section>`;
}

function renderRecordDetail(record) {
  const fieldRows=record.fields.map(f=>`<tr><td><code>${esc(f.name)}</code></td><td>${esc(f.type)}</td><td>${f.nullable?'可空':'必填'}</td><td>${esc(f.description)}${f.reference?`<div>${token(`scope:${f.reference.scope_id}`)}</div>`:''}</td></tr>`).join('');
  const samples=record.samples.length?record.samples:[{}];
  state.sample=Math.min(state.sample,samples.length-1);
  const accessText=record.access==='read_only'?'该记录集作为经过验证的业务基线供查询、筛选、关联和计算；原始记录不会被任务直接覆盖。':'该记录集允许任务按环境声明执行受控修改。';
  const links=[];
  if((record.linksTo||[]).length)links.push(`它通过外键或业务关系连接到 ${(record.linksTo||[]).join('、')}。`);
  if((record.linkedFrom||[]).length)links.push(`它同时被 ${(record.linkedFrom||[]).join('、')} 引用，是这些下游记录的关联基础。`);
  if(!links.length)links.push('该实体当前独立存在，主要与文件 Scope 或实体自身字段共同完成任务。');
  const context=`<div class="entity-context"><h5>这个实体在环境中的作用</h5><p>${esc(record.description)}</p><p>${esc(links.join(' '))}</p><p>${esc(accessText)}</p><div class="context-links">${(record.linksTo||[]).map(v=>token(`关联到:${v}`)).join('')}${(record.linkedFrom||[]).map(v=>token(`被引用:${v}`)).join('')}</div></div>`;
  return `<h4>${esc(record.name)}</h4><p class="desc"><code>${esc(record.id)}</code> · ${fmt(record.count)} 条真实记录</p><div class="status">${badge(record.importance,record.importance==='core'?'green':'amber')}${token(`${fmt(record.count)} rows`)}${(record.keyFields||[]).map(v=>token(`key:${v}`)).join('')}</div>${context}<div class="section"><div class="section-head"><h3>字段</h3><p>${fmt(record.fields.length)} 个声明字段</p></div><div class="table-scroll"><table><thead><tr><th>字段</th><th>类型</th><th>空值</th><th>含义</th></tr></thead><tbody>${fieldRows}</tbody></table></div></div><div class="sample-tabs">${samples.map((_,i)=>`<button class="sample-tab ${i===state.sample?'active':''}" data-sample="${i}">样例 ${i+1}</button>`).join('')}</div><pre>${esc(JSON.stringify(samples[state.sample],null,2))}</pre>`;
}

function recordsView() {
  const item=env(); if(!item.recordSets.length)return '<div class="empty">没有主体实体</div>';
  if(!state.recordSet||!item.recordSets.some(r=>r.id===state.recordSet))state.recordSet=item.recordSets[0].id;
  const selected=item.recordSets.find(r=>r.id===state.recordSet);
  const html=`${metrics()}<section class="section"><div class="panel split"><div class="list">${item.recordSets.map(r=>`<button class="list-button ${r.id===state.recordSet?'active':''}" data-record="${esc(r.id)}"><strong>${esc(r.id)}</strong><small>${fmt(r.count)} 条 · ${esc(r.importance)}</small></button>`).join('')}</div><div class="detail" id="recordDetail">${renderRecordDetail(selected)}</div></div></section>`;
  setTimeout(()=>{document.querySelectorAll('[data-record]').forEach(b=>b.onclick=()=>{state.recordSet=b.dataset.record;state.sample=0;render();});document.querySelectorAll('[data-sample]').forEach(b=>b.onclick=()=>{state.sample=Number(b.dataset.sample);render();});},0);return html;
}

function relationsView() {
  const rows=env().relationships.map(r=>`<tr><td>${esc(r.relationship_id)}</td><td>${esc(r.from_record_set_id)}</td><td>${esc(r.to_record_set_id)}</td><td>${fmt(r.matched_reference_count)}</td><td>${fmt(r.missing_reference_count)}</td><td>${r.valid?badge('有效','green'):badge('无效','red')}</td></tr>`).join('');
  return `${metrics()}<section class="section"><div class="section-head"><h3>实际关系闭合</h3><p>统计来自最终 SQLite 的独立画像</p></div><div class="panel table-scroll"><table class="relations"><thead><tr><th>关系</th><th>来源 Record Set</th><th>目标 Record Set</th><th>命中</th><th>缺失</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function filesView() {
  const scopes=env().scopes;if(!scopes.length)return `${metrics()}<section class="section"><div class="panel empty">该环境没有非主体文件工作区，全部能力来自结构化主体记录。</div></section>`;
  state.scope=Math.min(state.scope,scopes.length-1);const scope=scopes[state.scope];state.file=Math.min(state.file,Math.max(0,scope.files.length-1));const file=scope.files[state.file];
  const scopeTabs=scopes.map((s,i)=>`<button class="sample-tab ${i===state.scope?'active':''}" data-scope="${i}">${esc(s.id)} · ${fmt(s.files.length)}</button>`).join('');
  const fileList=scope.files.map((f,i)=>`<button class="file-button ${i===state.file?'active':''}" data-file="${i}"><code>${esc(f.path)}</code><small>${esc(f.format)} · ${bytes(f.bytes)}</small><small class="file-summary">${esc(f.description)}</small></button>`).join('');
  const preview=file?.preview?`<pre>${esc(file.preview)}</pre>`:`<div class="empty">该文件不提供文本预览</div>`;
  const editText=scope.access==='copy_on_write'?'任务可以复制后修改该文件，原始基线保持不变，适合执行“修改后重新验证”的工作流。':'该文件作为只读依据使用，适合解析、查询、比较和验证。';
  const explanation=file?`<div class="file-explanation"><h5>文件说明</h5><p>${esc(file.description)}</p><h5 style="margin-top:13px">可操作用途</h5><p>${esc(file.operationNote)} ${esc(editText)}</p><div class="file-meta">${file.declaredFormat?token(`声明格式:${file.declaredFormat}`):''}${file.validation?token(`内容校验:${file.validation}`):''}${file.pattern?token(`匹配规则:${file.pattern}`):''}</div></div>`:'';
  const html=`${metrics()}<section class="section"><div class="section-head"><h3>非主体文件说明</h3><p>这些文件是任务直接读取、比较或复制修改的工作对象</p></div><div class="sample-tabs">${scopeTabs}</div><div class="panel file-layout"><div class="scope-column"><div class="scope-head"><h4>${esc(scope.name)}</h4><p>${esc(scope.description)}</p></div>${fileList}</div><div class="detail"><h4>${esc(file?.path||scope.id)}</h4><p class="desc">${file?`${esc(file.format)} · ${bytes(file.bytes)}`:`${fmt(scope.files.length)} 个文件`}</p>${explanation}${preview}</div></div></section>`;
  setTimeout(()=>{document.querySelectorAll('[data-scope]').forEach(b=>b.onclick=()=>{state.scope=Number(b.dataset.scope);state.file=0;render();});document.querySelectorAll('[data-file]').forEach(b=>b.onclick=()=>{state.file=Number(b.dataset.file);render();});},0);return html;
}

function renderContent(){const views={overview,records:recordsView,relations:relationsView,files:filesView};document.getElementById('content').innerHTML=views[state.view]();}
function render(){renderSidebar();renderHeader();renderContent();}
render();
</script>
</body>
</html>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    annotations: dict[str, Any] = {}
    if args.annotations:
        payload = read_json(args.annotations.resolve())
        raw_annotations = payload.get("environments", {})
        if not isinstance(raw_annotations, dict):
            raise RuntimeError("annotations.environments must be an object")
        annotations = raw_annotations
    environments = [
        build_environment(path.resolve(), annotations=annotations)
        for path in args.packages
    ]
    payload = json.dumps(
        {"schemaVersion": "2.0", "environments": environments},
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(HTML_TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
    print(f"wrote {len(environments)} environments to {output}")


if __name__ == "__main__":
    main()
