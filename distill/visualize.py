"""Generate a self-contained HTML viewer for one distilled trajectory."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Any


def _embedded_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")


def render_trajectory_html(trajectory_path: Path, output_path: Path) -> Path:
    trajectory_path = trajectory_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if not isinstance(trajectory, dict) or not isinstance(trajectory.get("steps"), list):
        raise ValueError("trajectory must be a JSON object containing steps")
    initial_request = _initial_model_request(trajectory_path.parent / "raw" / "model_requests.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _template(_embedded_json(trajectory), _embedded_json(initial_request)),
        encoding="utf-8",
    )
    return output_path


def _initial_model_request(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            request = record.get("request") if isinstance(record, dict) else None
            if isinstance(request, dict):
                return {
                    "source": {"path": "raw/model_requests.jsonl", "line": line_number},
                    "request_id": record.get("request_id"),
                    "recorded_at": record.get("recorded_at"),
                    "sha256": record.get("sha256"),
                    "request": request,
                }
    return None


def render_run_visualizations(
    run_directory: Path, output_path: Path | None = None, limit: int | None = None,
) -> Path:
    run_directory = run_directory.expanduser().resolve()
    summary_path = run_directory / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"batch summary is missing: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    results = summary.get("results") if isinstance(summary, dict) else None
    if not isinstance(results, list):
        raise ValueError("summary.json must contain a results array")
    if limit is not None:
        results = results[:limit]

    entries = []
    for index, result in enumerate(results, 1):
        case = result.get("case") if isinstance(result, dict) else None
        if not isinstance(case, str) or not case:
            raise ValueError(f"summary result {index} has no case name")
        case_directory = (run_directory / case).resolve()
        if not case_directory.is_relative_to(run_directory):
            raise ValueError(f"summary case escapes run directory: {case}")
        trajectory_path = case_directory / "trajectory.json"
        if not trajectory_path.is_file():
            entries.append({
                "index": index, "case": case, "status": result.get("status", "missing"),
                "environment_id": None, "task_id": None, "task_text": result.get("error", ""),
                "steps": 0, "tool_calls": 0, "compactions": 0, "total_tokens": 0,
                "evaluation": None, "href": None,
            })
            continue
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
        render_trajectory_html(trajectory_path, case_directory / "trajectory.html")
        steps = trajectory.get("steps", [])
        outcome = trajectory.get("outcome", {})
        usage = outcome.get("usage") or {}
        usage = usage.get("total", usage) if isinstance(usage, dict) else {}
        entries.append({
            "index": index,
            "case": case,
            "status": outcome.get("status", result.get("status", "unknown")),
            "environment_id": trajectory.get("environment", {}).get("environment_id"),
            "task_id": trajectory.get("task", {}).get("task_id"),
            "task_text": trajectory.get("task", {}).get("task_text") or trajectory.get("task", {}).get("prompt", ""),
            "steps": len(steps),
            "tool_calls": sum(len(step.get("tool_calls", [])) for step in steps),
            "compactions": len(trajectory.get("compactions", [])),
            "total_tokens": sum(value for value in usage.values() if isinstance(value, (int, float))),
            "evaluation": outcome.get("evaluation"),
            "href": f"{case}/trajectory.html",
        })

    payload = {
        "run_id": run_directory.name,
        "case_count": len(entries),
        "completed_count": sum(entry["status"] == "completed" for entry in entries),
        "failed_count": sum(entry["status"] != "completed" for entry in entries),
        "entries": entries,
    }
    output_path = (output_path or run_directory / "index.html").expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_run_index_template(_embedded_json(payload)), encoding="utf-8")
    return output_path


def render_combined_visualization(
    run_directory: Path, output_path: Path | None = None, limit: int | None = None,
) -> Path:
    run_directory = run_directory.expanduser().resolve()
    render_run_visualizations(run_directory, limit=limit)
    summary = json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))
    documents = []
    results = summary["results"][:limit] if limit is not None else summary["results"]
    for index, result in enumerate(results, 1):
        case = result["case"]
        case_directory = (run_directory / case).resolve()
        if not case_directory.is_relative_to(run_directory):
            raise ValueError(f"summary case escapes run directory: {case}")
        trajectory_path = case_directory / "trajectory.json"
        html_path = case_directory / "trajectory.html"
        if not trajectory_path.is_file() or not html_path.is_file():
            raise ValueError(f"completed visualization is missing: {case}")
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
        html = html_path.read_bytes()
        documents.append({
            "index": index,
            "case": case,
            "environment_id": trajectory.get("environment", {}).get("environment_id"),
            "task_id": trajectory.get("task", {}).get("task_id"),
            "task_text": trajectory.get("task", {}).get("task_text") or "",
            "status": trajectory.get("outcome", {}).get("status", "unknown"),
            "gzip_base64": base64.b64encode(gzip.compress(html, compresslevel=9, mtime=0)).decode("ascii"),
        })
    payload = {"run_id": run_directory.name, "documents": documents}
    output_path = (output_path or run_directory / "combined.html").expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_combined_template(_embedded_json(payload)), encoding="utf-8")
    return output_path


def _template(payload: str, initial_request_payload: str) -> str:
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kimi K3 蒸馏轨迹</title>
<style>
:root {{
  color-scheme: light;
  --ink: #18201f;
  --muted: #65706e;
  --line: #d9dedc;
  --soft: #f3f5f4;
  --paper: #ffffff;
  --teal: #087f73;
  --teal-soft: #e3f3ef;
  --amber: #a46107;
  --amber-soft: #fff0d6;
  --red: #b83a36;
  --red-soft: #fde9e7;
  --violet: #6651a3;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; background: var(--soft); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; line-height: 1.55; letter-spacing: 0; }}
button, input {{ font: inherit; letter-spacing: 0; }}
button {{ cursor: pointer; }}
.topbar {{ position: sticky; top: 0; z-index: 20; background: rgba(255,255,255,.96); border-bottom: 1px solid var(--line); backdrop-filter: blur(12px); }}
.topbar-inner {{ max-width: 1480px; margin: 0 auto; min-height: 58px; padding: 9px 24px; display: flex; align-items: center; gap: 18px; }}
.brand {{ display: flex; align-items: baseline; gap: 10px; min-width: 0; }}
.brand strong {{ font-size: 16px; white-space: nowrap; }}
.brand span {{ color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.status {{ margin-left: auto; display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
.status::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }}
.status.ok::before {{ background: var(--teal); }}
.status.bad::before {{ background: var(--red); }}
.layout {{ max-width: 1480px; margin: 0 auto; display: grid; grid-template-columns: 244px minmax(0,1fr); min-height: calc(100vh - 59px); }}
.rail {{ border-right: 1px solid var(--line); padding: 24px 18px; background: var(--paper); }}
.rail-sticky {{ position: sticky; top: 83px; }}
.nav {{ display: grid; gap: 4px; }}
.nav button {{ border: 0; background: transparent; color: var(--muted); text-align: left; padding: 9px 10px; border-radius: 6px; }}
.nav button:hover, .nav button.active {{ background: var(--soft); color: var(--ink); }}
.nav button.active {{ box-shadow: inset 3px 0 var(--teal); }}
.rail-note {{ margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
main {{ min-width: 0; padding: 28px 32px 64px; }}
.section {{ display: none; }}
.section.active {{ display: block; }}
h1 {{ font-size: 24px; line-height: 1.25; margin: 0; letter-spacing: 0; }}
h2 {{ font-size: 17px; margin: 0 0 14px; letter-spacing: 0; }}
h3 {{ font-size: 14px; margin: 0; letter-spacing: 0; }}
.lede {{ color: var(--muted); margin: 8px 0 22px; max-width: 980px; }}
.metrics {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); border: 1px solid var(--line); border-radius: 7px; background: var(--paper); overflow: hidden; margin-bottom: 24px; }}
.metric {{ padding: 16px; border-right: 1px solid var(--line); min-width: 0; }}
.metric:last-child {{ border-right: 0; }}
.metric-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 700; }}
.metric-value {{ display: block; margin-top: 5px; font-size: 20px; font-weight: 700; overflow-wrap: anywhere; }}
.toolbar {{ display: flex; gap: 10px; align-items: center; margin: 0 0 16px; }}
.search {{ width: min(440px, 100%); border: 1px solid var(--line); border-radius: 6px; background: var(--paper); color: var(--ink); padding: 9px 11px; outline: none; }}
.search:focus {{ border-color: var(--teal); box-shadow: 0 0 0 3px var(--teal-soft); }}
.timeline {{ position: relative; display: grid; gap: 12px; }}
.timeline::before {{ content: ""; position: absolute; top: 18px; bottom: 18px; left: 18px; width: 1px; background: var(--line); }}
.step {{ position: relative; display: grid; grid-template-columns: 38px minmax(0,1fr); gap: 14px; }}
.step-index {{ z-index: 1; width: 37px; height: 37px; border: 1px solid var(--line); border-radius: 50%; display: grid; place-items: center; background: var(--paper); color: var(--muted); font-size: 12px; font-weight: 700; }}
.step-body {{ background: var(--paper); border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }}
.step-head {{ min-height: 52px; padding: 11px 14px; display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--line); }}
.step-title {{ min-width: 0; }}
.step-title small {{ color: var(--muted); display: block; }}
.step-meta {{ margin-left: auto; color: var(--muted); font-size: 12px; white-space: nowrap; }}
.step-content {{ padding: 13px 14px; }}
.reasoning {{ color: #394441; white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }}
.assistant {{ margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--line); white-space: pre-wrap; overflow-wrap: anywhere; }}
.call-list {{ display: grid; gap: 7px; margin-top: 12px; }}
.call {{ border-left: 3px solid var(--teal); background: var(--soft); padding: 9px 11px; }}
.call.support {{ border-left-color: var(--violet); }}
.call.error {{ border-left-color: var(--red); background: var(--red-soft); }}
.call-line {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.call-name {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }}
.tag {{ display: inline-flex; align-items: center; min-height: 20px; padding: 1px 7px; border-radius: 999px; background: var(--teal-soft); color: #05665d; font-size: 10px; font-weight: 800; text-transform: uppercase; white-space: nowrap; }}
.tag.support {{ background: #eee9fb; color: var(--violet); }}
.tag.error {{ background: var(--red-soft); color: var(--red); }}
details {{ margin-top: 8px; }}
summary {{ color: var(--muted); cursor: pointer; font-size: 12px; user-select: none; }}
pre {{ margin: 8px 0 0; padding: 10px; max-height: 340px; overflow: auto; border: 1px solid var(--line); background: #fbfcfb; color: #28322f; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }}
.table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 7px; background: var(--paper); }}
table {{ border-collapse: collapse; width: 100%; min-width: 820px; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
th {{ color: var(--muted); background: var(--soft); font-size: 11px; text-transform: uppercase; }}
tr:last-child td {{ border-bottom: 0; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }}
.answer {{ background: var(--paper); border-left: 4px solid var(--teal); padding: 18px 20px; white-space: pre-wrap; overflow-wrap: anywhere; }}
.context-block {{ margin-bottom: 24px; }}
.context-meta {{ color: var(--muted); margin: -6px 0 12px; font-size: 12px; overflow-wrap: anywhere; }}
.context-pre {{ max-height: 70vh; }}
.message-list, .tool-definitions {{ display: grid; gap: 10px; }}
.message-card {{ border: 1px solid var(--line); border-radius: 7px; background: var(--paper); overflow: hidden; }}
.message-head {{ display: flex; align-items: center; gap: 9px; padding: 9px 12px; border-bottom: 1px solid var(--line); background: var(--soft); }}
.message-content {{ margin: 0; border: 0; max-height: 440px; }}
.tool-definition {{ margin: 0; border: 1px solid var(--line); border-radius: 7px; background: var(--paper); padding: 10px 12px; }}
.tool-definition summary {{ color: var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
.evaluation {{ margin-bottom: 18px; border: 1px solid var(--line); border-radius: 7px; background: var(--paper); }}
.evaluation-head {{ padding: 13px 15px; border-bottom: 1px solid var(--line); display: flex; gap: 10px; align-items: center; }}
.evaluation-body {{ padding: 14px 15px; }}
.requirement {{ padding: 12px 0; border-top: 1px solid var(--line); }}
.requirement:first-child {{ border-top: 0; }}
.requirement strong.pass {{ color: var(--teal); }}
.requirement strong.fail {{ color: var(--red); }}
.empty {{ padding: 28px; border: 1px dashed var(--line); color: var(--muted); background: var(--paper); text-align: center; }}
.usage-row {{ display: grid; grid-template-columns: 150px minmax(0,1fr) 90px; gap: 10px; align-items: center; margin: 9px 0; }}
.bar {{ height: 8px; background: var(--line); overflow: hidden; border-radius: 4px; }}
.bar > span {{ display: block; height: 100%; background: var(--teal); }}
@media (max-width: 980px) {{
  .layout {{ grid-template-columns: 1fr; }}
  .rail {{ border-right: 0; border-bottom: 1px solid var(--line); padding: 10px 16px; }}
  .rail-sticky {{ position: static; }}
  .nav {{ grid-template-columns: repeat(5, minmax(0,1fr)); }}
  .nav button {{ text-align: center; padding: 8px 4px; }}
  .rail-note {{ display: none; }}
  main {{ padding: 22px 18px 48px; }}
  .metrics {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
  .metric {{ border-bottom: 1px solid var(--line); }}
  .metric:nth-child(2n) {{ border-right: 0; }}
}}
@media (max-width: 600px) {{
  .topbar-inner {{ padding: 9px 14px; }}
  .brand span {{ display: none; }}
  .status {{ font-size: 10px; }}
  .nav button {{ font-size: 12px; }}
  main {{ padding: 18px 12px 40px; }}
  h1 {{ font-size: 20px; }}
  .metrics {{ grid-template-columns: 1fr 1fr; }}
  .metric-value {{ font-size: 17px; }}
  .step {{ grid-template-columns: 30px minmax(0,1fr); gap: 8px; }}
  .timeline::before {{ left: 14px; }}
  .step-index {{ width: 29px; height: 29px; }}
  .step-head {{ align-items: flex-start; flex-direction: column; gap: 3px; }}
  .step-meta {{ margin-left: 0; white-space: normal; }}
  .call-line {{ align-items: flex-start; flex-wrap: wrap; }}
  .usage-row {{ grid-template-columns: 100px minmax(0,1fr) 70px; }}
}}
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner"><div class="brand"><strong>Kimi K3 蒸馏轨迹</strong><span id="trajectoryId"></span></div><div id="runStatus" class="status">未知</div></div></header>
<div class="layout">
  <aside class="rail"><div class="rail-sticky"><nav class="nav" aria-label="轨迹视图"><button class="active" data-tab="timeline">时间线</button><button data-tab="context">初始上下文</button><button data-tab="tools">工具调用</button><button data-tab="answer">结果与验收</button><button data-tab="evidence">证据</button></nav><div class="rail-note"><div id="modelName"></div><div id="createdAt"></div></div></div></aside>
  <main>
    <section id="timeline" class="section active"><h1 id="taskTitle">任务轨迹</h1><p id="taskText" class="lede"></p><div id="metrics" class="metrics"></div><div class="toolbar"><input id="stepSearch" class="search" type="search" placeholder="筛选步骤、推理或工具名称" aria-label="筛选轨迹"></div><div id="timelineList" class="timeline"></div></section>
    <section id="context" class="section"><h1>首次模型上下文</h1><p class="lede">以下内容来自流式中继实际捕获的第一次 Chat Completions 请求。它是 Kimi K3 首次推理时收到的完整请求正文，不是根据轨迹重新拼装的摘要；HTTP header 和 API key 从未写入该记录。</p><div id="contextMetrics" class="metrics"></div><div id="initialContext"></div></section>
    <section id="tools" class="section"><h1>工具调用</h1><p class="lede">业务 MCP 调用与 Harness 辅助读取分开统计。失败调用保留原始参数和结果。</p><div id="toolTable" class="table-wrap"></div></section>
    <section id="answer" class="section"><h1>结果与验收</h1><p class="lede">最终回答来自 Kimi Session；正式 verifier 结果存在时在此逐项展示。</p><div id="evaluation"></div><h2>最终回答</h2><div id="finalAnswer" class="answer"></div><h2 style="margin-top:24px">Token 使用</h2><div id="usage"></div></section>
    <section id="evidence" class="section"><h1>证据与上下文</h1><p class="lede">所有哈希均来自轨迹清单。模型请求保存了每次真实输入上下文，SSE 文件保存原始流式响应。</p><div id="evidenceTable" class="table-wrap"></div></section>
  </main>
</div>
<script id="trajectory-data" type="application/json">{payload}</script>
<script id="initial-request-data" type="application/json">{initial_request_payload}</script>
<script>
const data = JSON.parse(document.getElementById('trajectory-data').textContent);
const initialRecord = JSON.parse(document.getElementById('initial-request-data').textContent);
const initialRequest = initialRecord?.request || null;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const fmt = value => new Intl.NumberFormat('zh-CN').format(Number(value || 0));
const json = value => esc(JSON.stringify(value, null, 2));
const allCalls = data.steps.flatMap(step => step.tool_calls.map(call => ({{...call, step: step.index + 1}})));
const usage = data.outcome.usage?.total || data.outcome.usage || {{}};
const evaluation = data.outcome.evaluation;
const evaluationOutcome = evaluation?.outcome;
const runOk = data.outcome.status === 'completed';
const reasoningEffort = data.model.reasoning_effort || initialRequest?.reasoning_effort || '未记录';
document.getElementById('trajectoryId').textContent = data.trajectory_id;
document.getElementById('modelName').textContent = `${{data.model.display_name || data.model.upstream_id}} · ${{reasoningEffort}} effort · ${{fmt(data.model.max_context_size)}} context`;
document.getElementById('createdAt').textContent = new Date(data.created_at).toLocaleString('zh-CN');
const status = document.getElementById('runStatus');
status.textContent = evaluationOutcome ? `Verifier ${{evaluationOutcome.toUpperCase()}}` : (runOk ? '运行完成 · 未验收' : data.outcome.status);
status.classList.add(evaluationOutcome === 'pass' || (!evaluationOutcome && runOk) ? 'ok' : 'bad');
document.getElementById('taskText').textContent = data.task.task_text || data.task.prompt || '';
const taskId = data.task.task_id || '任务轨迹';
document.getElementById('taskTitle').textContent = taskId;
const metricData = [
  ['模型 / 推理', `${{data.model.upstream_id}} / ${{reasoningEffort}}`],
  ['步骤', data.steps.length],
  ['工具调用', allCalls.length],
  ['压缩', data.compactions.length],
  ['总 Token', (usage.inputOther || 0) + (usage.inputCacheRead || 0) + (usage.output || 0)],
];
document.getElementById('metrics').innerHTML = metricData.map(([label,value]) => `<div class="metric"><span class="metric-label">${{esc(label)}}</span><span class="metric-value">${{typeof value === 'number' ? fmt(value) : esc(value)}}</span></div>`).join('');

function messageContent(content) {{
  return typeof content === 'string' ? content : JSON.stringify(content, null, 2);
}}
function toolName(tool, index) {{
  return tool?.function?.name || tool?.name || `tool_${{index + 1}}`;
}}
const contextNode = document.getElementById('initialContext');
const contextMetrics = document.getElementById('contextMetrics');
if (initialRequest) {{
  const messages = Array.isArray(initialRequest.messages) ? initialRequest.messages : [];
  const tools = Array.isArray(initialRequest.tools) ? initialRequest.tools : [];
  const systemMessages = messages.filter(message => message?.role === 'system');
  const conversationMessages = messages.filter(message => message?.role !== 'system');
  const contextMetricData = [
    ['Request ID', initialRecord.request_id ?? '未知'],
    ['消息', messages.length],
    ['工具 Schema', tools.length],
    ['流式', initialRequest.stream === true ? 'true' : String(initialRequest.stream ?? '未声明')],
    ['Max tokens', initialRequest.max_tokens ?? '未声明'],
  ];
  contextMetrics.innerHTML = contextMetricData.map(([label,value]) => `<div class="metric"><span class="metric-label">${{esc(label)}}</span><span class="metric-value">${{typeof value === 'number' ? fmt(value) : esc(value)}}</span></div>`).join('');
  const systemHtml = systemMessages.length
    ? systemMessages.map((message, index) => `<pre class="context-pre">${{esc(messageContent(message.content))}}</pre>`).join('')
    : '<div class="empty">首次请求中没有 system message</div>';
  const messagesHtml = conversationMessages.length
    ? `<div class="message-list">${{conversationMessages.map((message, index) => `<article class="message-card"><div class="message-head"><span class="tag ${{message.role === 'assistant' ? 'support' : ''}}">${{esc(message.role || 'unknown')}}</span><strong>Message ${{index + 1}}</strong></div><pre class="message-content">${{esc(messageContent(message.content))}}</pre></article>`).join('')}}</div>`
    : '<div class="empty">首次请求中没有 system 以外的消息</div>';
  const toolsHtml = tools.length
    ? `<div class="tool-definitions">${{tools.map((tool, index) => `<details class="tool-definition"><summary>${{esc(toolName(tool, index))}}</summary><pre>${{json(tool)}}</pre></details>`).join('')}}</div>`
    : '<div class="empty">首次请求中没有工具定义</div>';
  contextNode.innerHTML = `
    <div class="context-block"><h2>System Prompt</h2><div class="context-meta">${{esc(initialRecord.source?.path)}}:${{esc(initialRecord.source?.line)}} · SHA-256 ${{esc(initialRecord.sha256 || '未记录')}}</div>${{systemHtml}}</div>
    <div class="context-block"><h2>输入消息</h2><p class="context-meta">按发送顺序展示 system 之后的全部消息；上方 System Prompt 仍完整保留在末尾原始 JSON 中。</p>${{messagesHtml}}</div>
    <div class="context-block"><h2>工具定义</h2><p class="context-meta">首次请求实际发送的全部工具名称、说明、参数 Schema 与返回 Schema。</p>${{toolsHtml}}</div>
    <div class="context-block"><h2>完整原始请求 JSON</h2><p class="context-meta">这是上游模型端点收到的完整 JSON body，包含模型参数、全部 messages 和 tools。</p><pre class="context-pre">${{json(initialRequest)}}</pre></div>`;
}} else {{
  contextMetrics.hidden = true;
  contextNode.innerHTML = '<div class="empty">找不到 raw/model_requests.jsonl，无法展示首次模型上下文。</div>';
}}

function callHtml(call) {{
  const support = call.tool_kind === 'harness_support';
  const cls = `call${{support ? ' support' : ''}}${{call.is_error ? ' error' : ''}}`;
  return `<div class="${{cls}}"><div class="call-line"><span class="tag ${{support ? 'support' : ''}} ${{call.is_error ? 'error' : ''}}">${{call.is_error ? 'ERROR' : support ? 'SUPPORT' : 'MCP'}}</span><span class="call-name">${{esc(call.name)}}</span></div><details><summary>参数与返回</summary><pre>${{json({{arguments:call.arguments, output:call.output}})}}</pre></details></div>`;
}}
function stepHtml(step) {{
  const duration = step.finish?.llmStreamDurationMs;
  const searchable = [step.reasoning?.text, step.assistant_text, ...step.tool_calls.map(c => c.name)].join(' ').toLowerCase();
  return `<article class="step" data-search="${{esc(searchable)}}"><div class="step-index">${{step.index + 1}}</div><div class="step-body"><div class="step-head"><div class="step-title"><h3>Step ${{step.step}}</h3><small>Turn ${{step.turn_id}} · ${{step.step_id || '无 step id'}}</small></div><div class="step-meta">${{step.tool_calls.length}} 次工具调用${{duration != null ? ` · ${{fmt(duration)}} ms stream` : ''}}</div></div><div class="step-content"><p class="reasoning">${{esc(step.reasoning?.text || '本步骤没有记录 reasoning 文本。')}}</p>${{step.assistant_text ? `<div class="assistant">${{esc(step.assistant_text)}}</div>` : ''}}${{step.tool_calls.length ? `<div class="call-list">${{step.tool_calls.map(callHtml).join('')}}</div>` : ''}}</div></div></article>`;
}}
const timeline = document.getElementById('timelineList');
timeline.innerHTML = data.steps.map(stepHtml).join('') || '<div class="empty">没有步骤记录</div>';
document.getElementById('stepSearch').addEventListener('input', event => {{
  const query = event.target.value.trim().toLowerCase();
  timeline.querySelectorAll('.step').forEach(node => node.hidden = query && !node.dataset.search.includes(query));
}});

const toolRows = allCalls.map(call => `<tr><td>${{call.step}}</td><td class="mono">${{esc(call.name)}}</td><td><span class="tag ${{call.tool_kind === 'harness_support' ? 'support' : ''}}">${{call.tool_kind === 'harness_support' ? 'SUPPORT' : 'MCP'}}</span></td><td>${{call.is_error ? '<span class="tag error">ERROR</span>' : '成功'}}</td><td><details><summary>查看</summary><pre>${{json(call.arguments)}}</pre></details></td><td><details><summary>查看</summary><pre>${{json(call.output)}}</pre></details></td></tr>`).join('');
document.getElementById('toolTable').innerHTML = `<table><thead><tr><th>步骤</th><th>工具</th><th>类型</th><th>状态</th><th>参数</th><th>返回</th></tr></thead><tbody>${{toolRows}}</tbody></table>`;

document.getElementById('finalAnswer').textContent = data.outcome.final_answer || '没有最终回答';
const evaluationNode = document.getElementById('evaluation');
if (evaluation) {{
  const requirements = (evaluation.requirements || []).map(item => `<div class="requirement"><strong class="${{item.passed ? 'pass' : 'fail'}}">${{esc(item.id)}} · ${{item.passed ? 'PASS' : 'FAIL'}}</strong><div>${{esc(item.requirement)}}</div><div style="color:var(--muted);margin-top:4px">${{esc(item.analysis)}}</div></div>`).join('');
  evaluationNode.innerHTML = `<div class="evaluation"><div class="evaluation-head"><span class="tag ${{evaluation.outcome === 'pass' ? '' : 'error'}}">${{esc(evaluation.outcome || 'unknown')}}</span><strong>正式任务验收</strong></div><div class="evaluation-body"><p>${{esc(evaluation.summary || '')}}</p>${{requirements}}</div></div>`;
}} else {{
  evaluationNode.innerHTML = '<div class="empty">尚未写入正式 verifier 结果</div>';
}}
const usageEntries = Object.entries(usage).filter(([,value]) => typeof value === 'number');
const usageMax = Math.max(...usageEntries.map(([,value]) => value), 1);
document.getElementById('usage').innerHTML = usageEntries.map(([key,value]) => `<div class="usage-row"><span>${{esc(key)}}</span><div class="bar"><span style="width:${{Math.max(2, value / usageMax * 100)}}%"></span></div><span class="mono">${{fmt(value)}}</span></div>`).join('') || '<div class="empty">没有 usage 数据</div>';

const evidenceRows = data.artifacts.map(item => `<tr><td class="mono">${{esc(item.path)}}</td><td>${{fmt(item.bytes)}} B</td><td class="mono">${{esc(item.sha256)}}</td></tr>`).join('');
document.getElementById('evidenceTable').innerHTML = `<table><thead><tr><th>路径</th><th>大小</th><th>SHA-256</th></tr></thead><tbody>${{evidenceRows}}</tbody></table>`;

document.querySelectorAll('.nav button').forEach(button => button.addEventListener('click', () => {{
  document.querySelectorAll('.nav button').forEach(item => item.classList.toggle('active', item === button));
  document.querySelectorAll('.section').forEach(section => section.classList.toggle('active', section.id === button.dataset.tab));
  window.scrollTo({{top:0, behavior:'instant'}});
}}));
</script>
</body>
</html>
'''


def _run_index_template(payload: str) -> str:
    return '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kimi K3 蒸馏批次</title>
<style>
:root { color-scheme: light; --ink:#18201f; --muted:#65706e; --line:#d9dedc; --soft:#f3f5f4; --paper:#fff; --teal:#087f73; --teal-soft:#e3f3ef; --red:#b83a36; --red-soft:#fde9e7; }
* { box-sizing:border-box; }
body { margin:0; background:var(--soft); color:var(--ink); font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; letter-spacing:0; }
.topbar { background:var(--paper); border-bottom:1px solid var(--line); }
.topbar-inner, main { width:min(1500px,100%); margin:0 auto; }
.topbar-inner { min-height:58px; padding:10px 24px; display:flex; align-items:center; gap:12px; }
.topbar strong { font-size:16px; }
.run-id { color:var(--muted); overflow-wrap:anywhere; }
main { padding:28px 24px 60px; }
h1 { margin:0; font-size:24px; letter-spacing:0; }
.lede { color:var(--muted); margin:7px 0 22px; }
.metrics { display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); background:var(--paper); border:1px solid var(--line); border-radius:7px; overflow:hidden; margin-bottom:20px; }
.metric { padding:15px; border-right:1px solid var(--line); min-width:0; }
.metric:last-child { border-right:0; }
.metric span { display:block; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; }
.metric strong { display:block; margin-top:4px; font-size:20px; overflow-wrap:anywhere; }
.toolbar { display:flex; gap:10px; margin-bottom:14px; }
input, select { min-height:39px; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--ink); padding:8px 11px; font:inherit; letter-spacing:0; }
input { width:min(480px,100%); }
select { min-width:260px; }
input:focus, select:focus { outline:3px solid var(--teal-soft); border-color:var(--teal); }
.table-wrap { overflow:auto; border:1px solid var(--line); border-radius:7px; background:var(--paper); }
table { width:100%; min-width:1050px; border-collapse:collapse; }
th, td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
th { position:sticky; top:0; z-index:1; background:var(--soft); color:var(--muted); font-size:11px; text-transform:uppercase; }
tr:last-child td { border-bottom:0; }
tr[hidden] { display:none; }
.index { color:var(--muted); font-variant-numeric:tabular-nums; }
.environment { font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; overflow-wrap:anywhere; }
.task-link { color:var(--teal); font-weight:750; text-decoration:none; }
.task-link:hover { text-decoration:underline; }
.task-text { margin-top:4px; color:var(--muted); max-width:640px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.number { font-variant-numeric:tabular-nums; white-space:nowrap; }
.tag { display:inline-flex; min-height:20px; align-items:center; border-radius:999px; padding:1px 7px; background:var(--teal-soft); color:#05665d; font-size:10px; font-weight:800; text-transform:uppercase; white-space:nowrap; }
.tag.fail { background:var(--red-soft); color:var(--red); }
.unverified { color:var(--muted); white-space:nowrap; }
.empty { padding:30px; color:var(--muted); text-align:center; }
@media (max-width:760px) {
  .topbar-inner, main { padding-left:12px; padding-right:12px; }
  .topbar-inner { align-items:flex-start; flex-direction:column; gap:1px; }
  .metrics { grid-template-columns:1fr 1fr; }
  .metric { border-bottom:1px solid var(--line); }
  .toolbar { flex-direction:column; }
  input, select { width:100%; min-width:0; }
}
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner"><strong>Kimi K3 蒸馏批次</strong><span id="runId" class="run-id"></span></div></header>
<main>
  <h1>轨迹索引</h1>
  <p class="lede">按原始执行顺序列出本批次轨迹。点击任务编号打开完整时间线、首次模型上下文、工具调用、最终回答和原始证据清单。</p>
  <div id="metrics" class="metrics"></div>
  <div class="toolbar"><input id="search" type="search" placeholder="搜索环境、任务编号或任务文本" aria-label="搜索轨迹"><select id="environment" aria-label="筛选环境"></select></div>
  <div class="table-wrap"><table><thead><tr><th>序号</th><th>环境</th><th>任务</th><th>状态</th><th>步骤</th><th>工具调用</th><th>压缩</th><th>Token</th><th>验收</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="empty" hidden>没有匹配的轨迹</div></div>
</main>
<script id="batch-data" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('batch-data').textContent);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=value=>new Intl.NumberFormat('zh-CN').format(Number(value||0));
document.getElementById('runId').textContent=data.run_id;
const environments=[...new Set(data.entries.map(item=>item.environment_id).filter(Boolean))];
const totals={steps:data.entries.reduce((sum,item)=>sum+item.steps,0),tools:data.entries.reduce((sum,item)=>sum+item.tool_calls,0)};
const metricData=[['任务',data.case_count],['完成',data.completed_count],['失败',data.failed_count],['环境',environments.length],['工具调用',totals.tools]];
document.getElementById('metrics').innerHTML=metricData.map(([label,value])=>`<div class="metric"><span>${esc(label)}</span><strong>${fmt(value)}</strong></div>`).join('');
const select=document.getElementById('environment');
select.innerHTML='<option value="">全部环境</option>'+environments.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');
const rows=document.getElementById('rows');
rows.innerHTML=data.entries.map(item=>{
  const search=[item.environment_id,item.task_id,item.task_text,item.case].join(' ').toLowerCase();
  const task=item.href?`<a class="task-link" href="${esc(item.href)}">${esc(item.task_id||item.case)}</a>`:`<strong>${esc(item.task_id||item.case)}</strong>`;
  const evaluation=item.evaluation?.outcome?esc(item.evaluation.outcome):'<span class="unverified">未运行 verifier</span>';
  return `<tr data-environment="${esc(item.environment_id||'')}" data-search="${esc(search)}"><td class="index">${item.index}</td><td class="environment">${esc(item.environment_id||'未知')}</td><td>${task}<div class="task-text" title="${esc(item.task_text)}">${esc(item.task_text)}</div></td><td><span class="tag ${item.status==='completed'?'':'fail'}">${esc(item.status)}</span></td><td class="number">${fmt(item.steps)}</td><td class="number">${fmt(item.tool_calls)}</td><td class="number">${fmt(item.compactions)}</td><td class="number">${fmt(item.total_tokens)}</td><td>${evaluation}</td></tr>`;
}).join('');
function filter(){
  const query=document.getElementById('search').value.trim().toLowerCase();
  const environment=select.value;
  let visible=0;
  rows.querySelectorAll('tr').forEach(row=>{const show=(!query||row.dataset.search.includes(query))&&(!environment||row.dataset.environment===environment);row.hidden=!show;if(show)visible++;});
  document.getElementById('empty').hidden=visible!==0;
}
document.getElementById('search').addEventListener('input',filter);
select.addEventListener('change',filter);
</script>
</body>
</html>
'''.replace("__PAYLOAD__", payload)


def _combined_template(payload: str) -> str:
    return '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kimi K3 最终轨迹集</title>
<style>
:root { color-scheme:light; --ink:#18201f; --muted:#65706e; --line:#d9dedc; --soft:#f3f5f4; --paper:#fff; --teal:#087f73; --teal-soft:#e3f3ef; --red:#b83a36; }
* { box-sizing:border-box; }
html, body { margin:0; min-height:100%; background:var(--soft); color:var(--ink); font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; letter-spacing:0; }
button, input, select { font:inherit; letter-spacing:0; }
button { cursor:pointer; }
.topbar { height:59px; display:flex; align-items:center; gap:10px; padding:9px 18px; background:var(--paper); border-bottom:1px solid var(--line); }
.brand { font-size:16px; font-weight:750; white-space:nowrap; }
.run-id { color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.counter { margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
.workspace { min-height:calc(100vh - 59px); display:grid; grid-template-columns:340px minmax(0,1fr); }
.sidebar { min-width:0; padding:14px; background:var(--paper); border-right:1px solid var(--line); }
.filters { display:grid; gap:8px; }
input, select { width:100%; min-height:38px; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--ink); padding:8px 10px; }
input:focus, select:focus { outline:3px solid var(--teal-soft); border-color:var(--teal); }
.task-list { height:calc(100vh - 161px); overflow:auto; margin-top:10px; border-top:1px solid var(--line); }
.task { width:100%; border:0; border-bottom:1px solid var(--line); background:transparent; color:var(--ink); padding:10px 8px; text-align:left; }
.task:hover { background:var(--soft); }
.task.active { background:var(--teal-soft); box-shadow:inset 3px 0 var(--teal); }
.task[hidden] { display:none; }
.task-line { display:flex; align-items:center; gap:8px; }
.task-index { color:var(--muted); font-variant-numeric:tabular-nums; }
.task-id { font-weight:750; }
.environment { margin-top:3px; color:var(--muted); font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; overflow-wrap:anywhere; }
.viewer { min-width:0; padding:12px; }
.viewer-head { min-height:42px; display:flex; align-items:center; gap:8px; margin-bottom:10px; }
.selection { min-width:0; }
.selection strong { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.selection span { display:block; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.nav-button { width:34px; height:34px; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--ink); font-size:18px; }
.nav-button:disabled { color:#aeb5b3; cursor:default; }
.viewer-head .nav-button:first-of-type { margin-left:auto; }
.frame-wrap { position:relative; height:calc(100vh - 123px); min-height:620px; border:1px solid var(--line); background:var(--paper); }
iframe { display:block; width:100%; height:100%; border:0; background:var(--paper); }
.loading { position:absolute; inset:0; display:grid; place-items:center; background:var(--paper); color:var(--muted); }
.loading[hidden] { display:none; }
.empty { padding:24px; color:var(--muted); text-align:center; }
@media (max-width:820px) {
  .topbar { height:auto; min-height:59px; align-items:flex-start; flex-wrap:wrap; padding:9px 12px; }
  .run-id { width:100%; order:3; }
  .workspace { display:block; }
  .sidebar { border-right:0; border-bottom:1px solid var(--line); padding:10px 12px; }
  .filters { grid-template-columns:minmax(0,1fr) minmax(0,1fr); }
  .task-list { height:190px; }
  .viewer { padding:10px 0 0; }
  .viewer-head { padding:0 12px; }
  .frame-wrap { height:78vh; min-height:560px; border-left:0; border-right:0; }
}
@media (max-width:480px) {
  .filters { grid-template-columns:1fr; }
  .counter { margin-left:0; }
  .selection span { max-width:180px; }
}
</style>
</head>
<body>
<header class="topbar"><span class="brand">Kimi K3 最终轨迹集</span><span id="runId" class="run-id"></span><span id="counter" class="counter"></span></header>
<div class="workspace">
  <aside class="sidebar"><div class="filters"><input id="search" type="search" placeholder="搜索任务" aria-label="搜索任务"><select id="environment" aria-label="筛选环境"></select></div><div id="taskList" class="task-list"></div></aside>
  <main class="viewer"><div class="viewer-head"><div class="selection"><strong id="taskTitle"></strong><span id="environmentTitle"></span></div><button id="previous" class="nav-button" type="button" title="上一条" aria-label="上一条">&lt;</button><button id="next" class="nav-button" type="button" title="下一条" aria-label="下一条">&gt;</button></div><div class="frame-wrap"><iframe id="frame" title="完整蒸馏轨迹" sandbox="allow-scripts"></iframe><div id="loading" class="loading">正在载入完整轨迹...</div></div></main>
</div>
<script id="combined-data" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('combined-data').textContent);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const documents=data.documents;
let selected=0;
let loadVersion=0;
document.getElementById('runId').textContent=data.run_id;
document.getElementById('counter').textContent=`${documents.length} 条完整轨迹`;
const environments=[...new Set(documents.map(item=>item.environment_id).filter(Boolean))];
const environment=document.getElementById('environment');
environment.innerHTML='<option value="">全部环境</option>'+environments.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');
const list=document.getElementById('taskList');
list.innerHTML=documents.map((item,index)=>`<button class="task" type="button" data-index="${index}" data-environment="${esc(item.environment_id||'')}" data-search="${esc([item.task_id,item.environment_id,item.task_text].join(' ').toLowerCase())}"><span class="task-line"><span class="task-index">${item.index}</span><span class="task-id">${esc(item.task_id||item.case)}</span></span><span class="environment">${esc(item.environment_id||'未知环境')}</span></button>`).join('');
async function decompress(encoded){
  const binary=atob(encoded);
  const bytes=Uint8Array.from(binary,char=>char.charCodeAt(0));
  const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}
async function select(index){
  selected=index;
  const version=++loadVersion;
  const item=documents[index];
  document.querySelectorAll('.task').forEach((button,buttonIndex)=>button.classList.toggle('active',buttonIndex===index));
  document.querySelector(`.task[data-index="${index}"]`)?.scrollIntoView({block:'nearest'});
  document.getElementById('taskTitle').textContent=`${item.index}. ${item.task_id||item.case}`;
  document.getElementById('environmentTitle').textContent=item.environment_id||'';
  document.getElementById('counter').textContent=`${item.index} / ${documents.length}`;
  document.getElementById('previous').disabled=index===0;
  document.getElementById('next').disabled=index===documents.length-1;
  const loading=document.getElementById('loading');
  loading.hidden=false;
  const html=await decompress(item.gzip_base64);
  if(version!==loadVersion)return;
  document.getElementById('frame').srcdoc=html;
  loading.hidden=true;
}
function filter(){
  const query=document.getElementById('search').value.trim().toLowerCase();
  const selectedEnvironment=environment.value;
  list.querySelectorAll('.task').forEach(button=>button.hidden=Boolean((query&&!button.dataset.search.includes(query))||(selectedEnvironment&&button.dataset.environment!==selectedEnvironment)));
  const active=list.querySelector(`.task[data-index="${selected}"]`);
  const firstVisible=list.querySelector('.task:not([hidden])');
  if(active?.hidden&&firstVisible)select(Number(firstVisible.dataset.index));
}
list.addEventListener('click',event=>{const button=event.target.closest('.task');if(button)select(Number(button.dataset.index));});
document.getElementById('search').addEventListener('input',filter);
environment.addEventListener('change',filter);
document.getElementById('previous').addEventListener('click',()=>select(Math.max(0,selected-1)));
document.getElementById('next').addEventListener('click',()=>select(Math.min(documents.length-1,selected+1)));
if(documents.length)select(0);else document.getElementById('taskList').innerHTML='<div class="empty">没有轨迹</div>';
</script>
</body>
</html>
'''.replace("__PAYLOAD__", payload)
