#!/usr/bin/env python3
"""Build a standalone visual report for published DataGen v2 environments."""

from __future__ import annotations

import argparse
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


def field_type(definition: dict[str, Any]) -> str:
    value = str(definition.get("type") or "unknown")
    if value == "array" and isinstance(definition.get("items"), dict):
        value += f"<{definition['items'].get('type', 'unknown')}>"
    return value


def scope_files(package: Path, scope: dict[str, Any]) -> list[dict[str, Any]]:
    scope_id = str(scope.get("scope_id") or "")
    root = package / "state/filesystem_scopes" / scope_id
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        preview = None
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 2 * 1024 * 1024:
            preview = path.read_text(encoding="utf-8", errors="replace")[:1600]
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "format": path.suffix.lower().lstrip(".") or "file",
            "preview": preview,
        })
    return rows


def build_environment(package: Path) -> dict[str, Any]:
    environment = read_json(package / "environment.json")
    quality = read_json(package / "provenance/quality_profile.json")
    integration = read_json(package / "provenance/integration_profile.json")
    plan = read_json(package / "provenance/integration_plan.json")
    validation = read_json(package / "validation.json")
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
            "importance": item.get("importance", "supporting"),
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
    needs = plan.get("need_bindings", [])
    source_decisions = plan.get("source_decisions", [])
    return {
        "id": environment.get("environment_id") or package.name,
        "packageName": package.name,
        "name": environment.get("name") or package.name,
        "description": environment.get("description", ""),
        "qualityTier": quality.get("quality_tier", "unknown"),
        "integrationTier": integration.get("integration_tier", "unknown"),
        "valid": validation.get("valid") is True,
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
            "needCoverage": quality.get("need_profile", {}).get("weighted_coverage_percent", 0),
            "sources": integration.get("source_integration_profile", {}).get("selected_source_count", 0),
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
.file-button small { color: var(--muted); }
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
    <div class="brand"><h1>AgentWorld 环境数据</h1><p>已发布环境 · v2 数据视角</p></div>
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
  document.getElementById("description").textContent=item.description;
  document.getElementById("status").innerHTML = badge(item.qualityTier, item.qualityTier==='rich'?'green':'amber') + badge(item.integrationTier, 'blue') + badge(item.valid?'已验证':'验证失败', item.valid?'green':'red');
  const views=[["overview","总览"],["records","记录数据"],["relations","关系"],["files","文件工作区"]];
  document.getElementById("tabs").innerHTML=views.map(([id,label])=>`<button class="tab ${state.view===id?'active':''}" data-view="${id}">${label}</button>`).join("");
  document.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>{state.view=button.dataset.view;render();});
}

function metrics() {
  const m=env().metrics;
  return `<div class="metric-grid">
    <div class="metric"><span>全部记录</span><strong>${fmt(m.records)}</strong></div>
    <div class="metric"><span>Record Set</span><strong>${fmt(m.recordSets)}</strong></div>
    <div class="metric"><span>有效关系</span><strong>${fmt(m.relationships)}</strong></div>
    <div class="metric"><span>任务侧文件</span><strong>${fmt(m.files)}</strong></div>
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
  const needs=item.needs.map(n=>`<div class="need"><strong>${esc(n.need_id)}</strong><p>${esc(n.description)}</p><div class="binding">${(n.record_set_ids||[]).map(token).join('')}${(n.scope_ids||[]).map(v=>token(`scope:${v}`)).join('')}${badge(n.status,n.status==='realized'?'green':'amber')}</div></div>`).join('');
  return `${metrics()}<section class="section"><div class="section-head"><h3>统一数据模型</h3><p>实线为记录关系，虚线为文件路径引用</p></div><div class="panel"><div class="model-wrap">${graphSvg(item)}</div><div class="need-grid">${needs}</div></div></section>`;
}

function renderRecordDetail(record) {
  const fieldRows=record.fields.map(f=>`<tr><td><code>${esc(f.name)}</code></td><td>${esc(f.type)}</td><td>${f.nullable?'可空':'必填'}</td><td>${esc(f.description)}${f.reference?`<div>${token(`scope:${f.reference.scope_id}`)}</div>`:''}</td></tr>`).join('');
  const samples=record.samples.length?record.samples:[{}];
  state.sample=Math.min(state.sample,samples.length-1);
  return `<h4>${esc(record.name)}</h4><p class="desc">${esc(record.description)}</p><div class="status">${badge(record.importance,record.importance==='core'?'green':'amber')}${token(`${fmt(record.count)} rows`)}${(record.keyFields||[]).map(v=>token(`key:${v}`)).join('')}</div><div class="section"><div class="section-head"><h3>字段</h3><p>${fmt(record.fields.length)} 个声明字段</p></div><div class="table-scroll"><table><thead><tr><th>字段</th><th>类型</th><th>空值</th><th>含义</th></tr></thead><tbody>${fieldRows}</tbody></table></div></div><div class="sample-tabs">${samples.map((_,i)=>`<button class="sample-tab ${i===state.sample?'active':''}" data-sample="${i}">样例 ${i+1}</button>`).join('')}</div><pre>${esc(JSON.stringify(samples[state.sample],null,2))}</pre>`;
}

function recordsView() {
  const item=env(); if(!item.recordSets.length)return '<div class="empty">没有 Record Set</div>';
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
  const scopes=env().scopes;if(!scopes.length)return `${metrics()}<section class="section"><div class="panel empty">该环境没有任务侧文件工作区，全部能力来自结构化记录。</div></section>`;
  state.scope=Math.min(state.scope,scopes.length-1);const scope=scopes[state.scope];state.file=Math.min(state.file,Math.max(0,scope.files.length-1));const file=scope.files[state.file];
  const scopeTabs=scopes.map((s,i)=>`<button class="sample-tab ${i===state.scope?'active':''}" data-scope="${i}">${esc(s.id)} · ${fmt(s.files.length)}</button>`).join('');
  const fileList=scope.files.map((f,i)=>`<button class="file-button ${i===state.file?'active':''}" data-file="${i}"><code>${esc(f.path)}</code><small>${esc(f.format)} · ${bytes(f.bytes)}</small></button>`).join('');
  const preview=file?.preview?`<pre>${esc(file.preview)}</pre>`:`<div class="empty">该文件不提供文本预览</div>`;
  const html=`${metrics()}<section class="section"><div class="sample-tabs">${scopeTabs}</div><div class="panel file-layout"><div class="scope-column"><div class="scope-head"><h4>${esc(scope.name)}</h4><p>${esc(scope.description)}</p></div>${fileList}</div><div class="detail"><h4>${esc(file?.path||scope.id)}</h4><p class="desc">${file?`${esc(file.format)} · ${bytes(file.bytes)}`:`${fmt(scope.files.length)} 个文件`}</p>${preview}</div></div></section>`;
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
    args = parser.parse_args()
    environments = [build_environment(path.resolve()) for path in args.packages]
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
