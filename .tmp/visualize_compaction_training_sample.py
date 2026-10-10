#!/usr/bin/env python3
"""Render one standard compaction-continuation record as a standalone HTML file."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Iterator


COMPACTION_MARKER = "The conversation so far has been compacted to free up context."


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def choose_record(path: Path, trajectory_id: str | None) -> dict[str, Any]:
    for record in jsonl(path):
        if trajectory_id is None or record.get("trajectory_id") == trajectory_id:
            return record
    raise ValueError(f"trajectory not found: {trajectory_id}")


def escaped(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return html.escape(value)


def message_kind(message: dict[str, Any], index: int, last_index: int) -> tuple[str, str]:
    role = message.get("role")
    content = message.get("content")
    if role == "system":
        return "S", "System prompt"
    if role == "tool":
        return "T", "Tool response"
    if role == "assistant":
        return "A", "Assistant target" if index == last_index else "Assistant"
    if isinstance(content, str) and COMPACTION_MARKER in content:
        return "C", "Compaction summary"
    if index == 1:
        return "Q", "Original query"
    return "R", "Runtime reminder"


def text_block(title: str, text: Any, kind: str, expanded: bool = False) -> str:
    if text in (None, ""):
        return ""
    raw = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=2)
    open_attribute = " open" if expanded else ""
    return f"""
      <details class="payload"{open_attribute}>
        <summary>{html.escape(title)} <span>{len(raw):,} chars</span></summary>
        <pre>{escaped(raw)}</pre>
      </details>
    """


def tool_calls_block(calls: list[Any]) -> str:
    if not calls:
        return ""
    rows = []
    for call in calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        rows.append(f"""
          <tr>
            <td><code>{escaped(call.get('id'))}</code></td>
            <td><strong>{escaped(function.get('name'))}</strong></td>
            <td><pre>{escaped(function.get('arguments'))}</pre></td>
          </tr>
        """)
    return f"""
      <section class="tool-calls">
        <h3>Tool calls <span>{len(rows)}</span></h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Call ID</th><th>Tool</th><th>Arguments</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
      </section>
    """


def message_block(message: dict[str, Any], index: int, last_index: int) -> str:
    symbol, label = message_kind(message, index, last_index)
    role = str(message.get("role", "unknown"))
    is_target = symbol == "A" and index == last_index
    classes = f"message kind-{symbol.lower()}" + (" target" if is_target else "")
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    calls = message.get("tool_calls") or []
    preview_source = content or reasoning or ""
    preview = str(preview_source).replace("\n", " ").strip()
    if len(preview) > 220:
        preview = preview[:220].rstrip() + "..."
    return f"""
    <article class="{classes}" data-kind="{symbol}">
      <div class="rail"><span>{symbol}</span></div>
      <div class="message-body">
        <header>
          <div>
            <h2>{html.escape(label)}</h2>
            <p>messages[{index}] · actual role=<code>{html.escape(role)}</code></p>
          </div>
          {'<span class="target-tag">TRAIN TARGET</span>' if is_target else '<span class="input-tag">MASKED INPUT</span>'}
        </header>
        <p class="preview">{html.escape(preview)}</p>
        {text_block('Content', content, symbol, expanded=symbol in {'Q'})}
        {text_block('Reasoning content', reasoning, symbol)}
        {tool_calls_block(calls)}
      </div>
    </article>
    """


def render(record: dict[str, Any]) -> str:
    messages = record.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise ValueError("record has no messages")
    parts = [
        message_block(message, index, len(messages) - 1)
        for index, message in enumerate(messages)
        if isinstance(message, dict)
    ]
    tools = record.get("tools", [])
    call_count = sum(
        len(message.get("tool_calls") or [])
        for message in messages
        if isinstance(message, dict)
    )
    trajectory_id = str(record.get("trajectory_id", ""))
    case = str(record.get("provenance", {}).get("case", ""))
    task_text = str(record.get("task", {}).get("task_text", ""))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compaction continuation · {html.escape(trajectory_id)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --ink: #182026;
      --muted: #65727c;
      --line: #d7dde2;
      --system: #31546d;
      --query: #256b4b;
      --compact: #925d13;
      --reminder: #6c5d78;
      --assistant: #9f3e31;
      --target-bg: #fff8f4;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: 0; }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 56px; }}
    .top {{ border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 22px; }}
    .eyebrow {{ color: var(--muted); margin: 0 0 5px; font-size: 12px; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.25; overflow-wrap: anywhere; letter-spacing: 0; }}
    .case {{ margin: 8px 0 0; color: var(--muted); overflow-wrap: anywhere; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--line); background: var(--panel); margin-top: 18px; }}
    .stat {{ padding: 13px 15px; border-right: 1px solid var(--line); }}
    .stat:last-child {{ border-right: 0; }}
    .stat b {{ display: block; font-size: 18px; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 8px 18px; padding: 14px 0 2px; color: var(--muted); }}
    .legend b {{ color: var(--ink); }}
    .sequence {{ display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
    .sequence span {{ width: 30px; height: 30px; display: inline-grid; place-items: center; border: 1px solid var(--line); background: var(--panel); font-weight: 750; }}
    .sequence i {{ font-style: normal; color: #96a0a8; }}
    .message {{ display: grid; grid-template-columns: 54px minmax(0, 1fr); border-top: 1px solid var(--line); background: var(--panel); }}
    .message:last-child {{ border-bottom: 1px solid var(--line); }}
    .message.target {{ background: var(--target-bg); border: 1px solid #efc6b9; margin-top: 14px; }}
    .rail {{ display: flex; justify-content: center; padding-top: 18px; border-right: 1px solid var(--line); }}
    .rail span {{ width: 30px; height: 30px; display: grid; place-items: center; color: white; font-weight: 800; }}
    .kind-s .rail span {{ background: var(--system); }}
    .kind-q .rail span {{ background: var(--query); }}
    .kind-c .rail span {{ background: var(--compact); }}
    .kind-r .rail span {{ background: var(--reminder); }}
    .kind-a .rail span {{ background: var(--assistant); }}
    .message-body {{ min-width: 0; padding: 16px 18px 20px; }}
    .message header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
    h2 {{ margin: 0; font-size: 16px; letter-spacing: 0; }}
    .message header p {{ margin: 2px 0 0; color: var(--muted); font-size: 12px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
    .input-tag, .target-tag {{ flex: 0 0 auto; font-size: 11px; font-weight: 750; border: 1px solid var(--line); padding: 3px 7px; }}
    .target-tag {{ color: var(--assistant); border-color: #dca796; background: #fff; }}
    .input-tag {{ color: var(--muted); background: #f7f8f9; }}
    .preview {{ margin: 12px 0; color: #3f4a52; }}
    details.payload {{ border-top: 1px solid var(--line); padding-top: 9px; margin-top: 9px; }}
    summary {{ cursor: pointer; font-weight: 650; }}
    summary span, .tool-calls h3 span {{ color: var(--muted); font-weight: 400; font-size: 12px; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 10px 0 0; padding: 12px; background: #f6f7f8; border: 1px solid #e1e5e8; max-height: 560px; overflow: auto; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: 0; }}
    .tool-calls {{ border-top: 1px solid var(--line); margin-top: 12px; padding-top: 10px; }}
    .tool-calls h3 {{ margin: 0 0 8px; font-size: 14px; }}
    .table-scroll {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; vertical-align: top; border: 1px solid var(--line); padding: 8px; }}
    th {{ color: var(--muted); font-size: 11px; background: #f6f7f8; }}
    td pre {{ margin: 0; max-height: 240px; min-width: 320px; }}
    .task {{ margin-top: 24px; border-top: 1px solid var(--line); padding-top: 18px; }}
    .task h2 {{ margin-bottom: 8px; }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 18px; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .stat:nth-child(2) {{ border-right: 0; }}
      .stat:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
      .message {{ grid-template-columns: 42px minmax(0, 1fr); }}
      .message-body {{ padding: 13px 12px 16px; }}
      .message header {{ display: block; }}
      .input-tag, .target-tag {{ display: inline-block; margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="top">
      <p class="eyebrow">Current training format · compaction continuation</p>
      <h1>{html.escape(trajectory_id)}</h1>
      <p class="case">{html.escape(case)}</p>
      <div class="stats">
        <div class="stat"><b>{len(messages)}</b><span>messages</span></div>
        <div class="stat"><b>{len(tools)}</b><span>tool definitions</span></div>
        <div class="stat"><b>{call_count}</b><span>target tool calls</span></div>
        <div class="stat"><b>user</b><span>actual role of C</span></div>
      </div>
      <div class="legend">
        <span><b>S</b> system prompt</span><span><b>Q</b> original query</span><span><b>C</b> compaction summary</span><span><b>R</b> runtime reminder</span><span><b>A</b> assistant target</span>
      </div>
      <div class="sequence">{'<i>→</i>'.join(f'<span>{message_kind(m, i, len(messages)-1)[0]}</span>' for i, m in enumerate(messages) if isinstance(m, dict))}</div>
    </section>
    <section class="timeline">{''.join(parts)}</section>
    <section class="task">
      <h2>Task metadata</h2>
      <pre>{html.escape(task_text)}</pre>
    </section>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--trajectory-id")
    args = parser.parse_args()
    record = choose_record(args.input.expanduser().resolve(), args.trajectory_id)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(record), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
