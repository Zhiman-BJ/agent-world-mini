#!/usr/bin/env python3
"""Render several complete compaction windows as one standalone HTML file."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Iterator


COMPACTION_MARKER = "The conversation so far has been compacted to free up context."
DEFAULT_IDS = (
    "76dbfae6-d511-4f42-8671-e53e7fabd160::window_000_pre_compaction",
    "76dbfae6-d511-4f42-8671-e53e7fabd160::window_001_after_compaction_000",
    "b4c13bfc-04cf-4582-9090-f83eddddcde3::window_004_after_compaction_003",
)
DISPLAY_LIMIT = 40_000


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def choose_records(path: Path, ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(ids)
    records: dict[str, dict[str, Any]] = {}
    for record in jsonl(path):
        trajectory_id = record.get("trajectory_id")
        if trajectory_id in wanted:
            records[str(trajectory_id)] = record
    missing = [trajectory_id for trajectory_id in ids if trajectory_id not in records]
    if missing:
        raise ValueError(f"missing sample IDs: {missing}")
    return [records[trajectory_id] for trajectory_id in ids]


def escaped(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return html.escape(value)


def display_text(value: Any) -> tuple[str, int, bool]:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if len(raw) <= DISPLAY_LIMIT:
        return raw, len(raw), False
    return raw[:DISPLAY_LIMIT], len(raw), True


def kind(message: dict[str, Any], index: int) -> tuple[str, str]:
    role = message.get("role")
    content = message.get("content")
    if role == "system":
        return "S", "System prompt"
    if role == "assistant":
        return "A", "Assistant target"
    if role == "tool":
        return "T", "Tool response"
    if isinstance(content, str) and COMPACTION_MARKER in content:
        return "C", "Compaction summary"
    if index == 1:
        return "Q", "Original query"
    return "R", "Runtime message"


def payload(title: str, value: Any, expanded: bool = False) -> str:
    if value in (None, ""):
        return ""
    raw, original_length, truncated = display_text(value)
    suffix = " · visualization preview" if truncated else ""
    notice = (
        f'<p class="notice">显示前 {DISPLAY_LIMIT:,} 字符；训练文件保留完整的 '
        f'{original_length:,} 字符。</p>'
        if truncated else ""
    )
    return f"""
      <details class="payload"{' open' if expanded else ''}>
        <summary>{html.escape(title)} <span>{original_length:,} chars{suffix}</span></summary>
        {notice}<pre>{html.escape(raw)}</pre>
      </details>
    """


def calls_block(calls: list[Any]) -> str:
    if not calls:
        return ""
    rows = []
    for call in calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        arguments, original_length, truncated = display_text(function.get("arguments", ""))
        note = " (preview)" if truncated else ""
        rows.append(f"""
          <tr>
            <td><code>{escaped(call.get('id'))}</code></td>
            <td><strong>{escaped(function.get('name'))}</strong></td>
            <td><pre>{html.escape(arguments)}</pre><small>{original_length:,} chars{note}</small></td>
          </tr>
        """)
    return f"""
      <section class="calls">
        <h4>Tool calls <span>{len(rows)}</span></h4>
        <div class="table-scroll"><table>
          <thead><tr><th>Call ID</th><th>Tool</th><th>Arguments</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>
      </section>
    """


def message_block(message: dict[str, Any], index: int) -> str:
    symbol, label = kind(message, index)
    is_target = symbol == "A"
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    calls = message.get("tool_calls") or []
    preview = str(content or reasoning or "").replace("\n", " ").strip()
    if len(preview) > 180:
        preview = preview[:180].rstrip() + "..."
    return f"""
      <article class="message kind-{symbol.lower()}{' target' if is_target else ''}">
        <div class="rail"><span>{symbol}</span></div>
        <div class="body">
          <header>
            <div><h3>{label}</h3><p>messages[{index}] · actual role=<code>{escaped(message.get('role'))}</code></p></div>
            <b class="{'target-tag' if is_target else 'input-tag'}">{'TRAIN TARGET' if is_target else 'MASKED INPUT'}</b>
          </header>
          <p class="preview">{html.escape(preview)}</p>
          {payload('Content', content, expanded=symbol in {'Q', 'C'})}
          {payload('Reasoning content', reasoning)}
          {calls_block(calls)}
        </div>
      </article>
    """


def record_block(record: dict[str, Any], number: int) -> str:
    messages = record["messages"]
    assistant_count = sum(message.get("role") == "assistant" for message in messages)
    tool_count = sum(message.get("role") == "tool" for message in messages)
    call_count = sum(len(message.get("tool_calls") or []) for message in messages)
    sequence = "<i>→</i>".join(
        f'<span class="seq-{kind(message, index)[0].lower()}">{kind(message, index)[0]}</span>'
        for index, message in enumerate(messages)
    )
    trajectory_id = str(record["trajectory_id"])
    case = str(record.get("provenance", {}).get("case", ""))
    return f"""
    <section class="sample" id="sample-{number}">
      <div class="sample-head">
        <p class="eyebrow">Sample {number} · complete context window</p>
        <h2>{html.escape(trajectory_id)}</h2>
        <p class="case">{html.escape(case)}</p>
        <div class="stats">
          <div><b>{len(messages)}</b><span>messages</span></div>
          <div><b>{assistant_count}</b><span>assistant targets</span></div>
          <div><b>{tool_count}</b><span>tool responses</span></div>
          <div><b>{call_count}</b><span>tool calls</span></div>
        </div>
        <div class="sequence">{sequence}</div>
      </div>
      <div class="timeline">{''.join(message_block(message, index) for index, message in enumerate(messages))}</div>
    </section>
    """


def render(records: list[dict[str, Any]]) -> str:
    nav = "".join(
        f'<a href="#sample-{index}">样本 {index}</a>'
        for index in range(1, len(records) + 1)
    )
    samples = "".join(
        record_block(record, index)
        for index, record in enumerate(records, 1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>上下文压缩窗口训练样本</title>
  <style>
    :root {{ --bg:#f4f5f6; --paper:#fff; --ink:#182026; --muted:#68757e; --line:#d8dde1; --s:#31546d; --q:#276849; --c:#986113; --r:#705c7e; --a:#a13f32; --t:#52616b; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; letter-spacing:0; }}
    .page-head, .sample {{ width:min(1200px,calc(100% - 32px)); margin:0 auto; }}
    .page-head {{ padding:28px 0 18px; }}
    h1 {{ margin:0; font-size:26px; }}
    .page-head p {{ color:var(--muted); max-width:850px; }}
    nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
    nav a {{ color:var(--ink); background:var(--paper); border:1px solid var(--line); padding:6px 10px; text-decoration:none; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:8px 18px; margin-top:16px; color:var(--muted); }}
    .legend b {{ color:var(--ink); }}
    .sample {{ margin-bottom:52px; scroll-margin-top:12px; }}
    .sample-head {{ border-top:3px solid var(--ink); padding:19px 0; }}
    .eyebrow {{ margin:0 0 4px; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    h2 {{ margin:0; font-size:20px; overflow-wrap:anywhere; }}
    .case {{ margin:5px 0 0; color:var(--muted); overflow-wrap:anywhere; }}
    .stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:14px; border:1px solid var(--line); background:var(--paper); }}
    .stats div {{ padding:10px 13px; border-right:1px solid var(--line); }}
    .stats div:last-child {{ border-right:0; }}
    .stats b,.stats span {{ display:block; }}
    .stats b {{ font-size:18px; }} .stats span {{ color:var(--muted); font-size:12px; }}
    .sequence {{ display:flex; flex-wrap:wrap; align-items:center; gap:5px; margin-top:12px; }}
    .sequence span {{ width:27px; height:27px; display:grid; place-items:center; color:#fff; font-weight:800; }}
    .sequence i {{ color:#9ca5ab; font-style:normal; }}
    .seq-s,.kind-s .rail span {{ background:var(--s); }} .seq-q,.kind-q .rail span {{ background:var(--q); }}
    .seq-c,.kind-c .rail span {{ background:var(--c); }} .seq-r,.kind-r .rail span {{ background:var(--r); }}
    .seq-a,.kind-a .rail span {{ background:var(--a); }} .seq-t,.kind-t .rail span {{ background:var(--t); }}
    .message {{ display:grid; grid-template-columns:52px minmax(0,1fr); background:var(--paper); border-top:1px solid var(--line); }}
    .message:last-child {{ border-bottom:1px solid var(--line); }}
    .message.target {{ background:#fff9f6; }}
    .rail {{ display:flex; justify-content:center; padding-top:16px; border-right:1px solid var(--line); }}
    .rail span {{ width:29px; height:29px; display:grid; place-items:center; color:#fff; font-weight:800; }}
    .body {{ min-width:0; padding:14px 17px 18px; }}
    .body header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }}
    h3 {{ margin:0; font-size:15px; }} h4 {{ margin:0 0 8px; }}
    header p {{ margin:2px 0 0; color:var(--muted); font-size:12px; }}
    .target-tag,.input-tag {{ flex:0 0 auto; padding:3px 7px; font-size:10px; border:1px solid var(--line); }}
    .target-tag {{ color:var(--a); border-color:#dca99c; background:#fff; }} .input-tag {{ color:var(--muted); background:#f7f8f9; }}
    .preview {{ color:#3f4a52; margin:10px 0; }}
    details.payload,.calls {{ border-top:1px solid var(--line); padding-top:9px; margin-top:9px; }}
    summary {{ cursor:pointer; font-weight:650; }} summary span,h4 span,small {{ color:var(--muted); font-weight:400; font-size:11px; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; margin:9px 0 0; padding:11px; border:1px solid #e1e5e8; background:#f6f7f8; max-height:520px; overflow:auto; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; letter-spacing:0; }}
    .notice {{ color:#8b5a16; margin:8px 0 0; }} .table-scroll {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ border:1px solid var(--line); padding:7px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); background:#f6f7f8; font-size:11px; }} td pre {{ min-width:300px; max-height:220px; margin:0; }}
    @media(max-width:720px) {{ .page-head,.sample {{ width:calc(100% - 20px); }} .stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .stats div:nth-child(2) {{ border-right:0; }} .stats div:nth-child(-n+2) {{ border-bottom:1px solid var(--line); }} .message {{ grid-template-columns:40px minmax(0,1fr); }} .body {{ padding:12px 10px 15px; }} .body header {{ display:block; }} .target-tag,.input-tag {{ display:inline-block; margin-top:6px; }} }}
  </style>
</head>
<body>
  <header class="page-head">
    <h1>上下文压缩窗口训练样本</h1>
    <p>每次压缩形成一个新的、互不重叠的训练窗口。所有 A 都是训练目标；S、Q、C、R、T 仅作为条件输入并屏蔽 loss。页面只对超长单项做显示截断，JSONL 中的数据保持完整。</p>
    <nav>{nav}</nav>
    <div class="legend"><span><b>S</b> system</span><span><b>Q</b> query</span><span><b>C</b> compaction summary (role=user)</span><span><b>R</b> runtime user message</span><span><b>A</b> assistant target</span><span><b>T</b> tool response</span></div>
  </header>
  {samples}
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--trajectory-id", action="append", dest="trajectory_ids")
    args = parser.parse_args()
    ids = args.trajectory_ids or list(DEFAULT_IDS)
    records = choose_records(args.input.expanduser().resolve(), ids)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(records), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
