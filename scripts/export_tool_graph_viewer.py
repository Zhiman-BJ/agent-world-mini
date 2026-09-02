"""把 Tool Graph run 或中间 Bundle 导出为交互式静态 HTML。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "scripts" / "tool_graph_results.html"
PUBLIC_TOOL_FIELDS = ("name", "description", "inputSchema", "outputSchema")


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"无法读取 JSON：{path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object：{path}")
    return value


def discover_inputs(inputs: list[Path]) -> list[Path]:
    """解析 Bundle、直接 run 或父目录，按输入顺序去重并只扫描一层。"""
    if not inputs:
        raise ValueError("至少需要一个 Bundle、run 目录或结果父目录")
    found: list[Path] = []
    seen: set[Path] = set()
    for supplied in inputs:
        path = supplied.expanduser().resolve()
        if path.is_file():
            _object(path)
            candidates = [path]
            direct = False
        elif (path / "run.json").is_file():
            candidates = [path]
            direct = True
        elif path.is_dir():
            candidates = sorted(
                child for child in path.iterdir()
                if child.is_dir() and (child / "run.json").is_file()
            )
            direct = False
        else:
            raise ValueError(f"输入路径不是 Bundle、run 或结果父目录：{path}")
        for candidate in candidates:
            if candidate.is_dir():
                meta = _object(candidate / "run.json")
                if meta.get("status") != "completed":
                    if direct:
                        raise ValueError(f"run 必须是 completed：{candidate}")
                    continue
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
    if not found:
        raise ValueError("输入中没有可导出的 Bundle 或 completed run")
    return found


def _public_tools(environment: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    raw = environment.get("tools")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"environment.tools 必须是非空 array：{source}")
    tools = []
    for index, tool in enumerate(raw):
        if not isinstance(tool, dict) or any(key not in tool for key in PUBLIC_TOOL_FIELDS):
            raise ValueError(f"environment.tools[{index}] 缺少公开字段：{source}")
        if not isinstance(tool["name"], str) or not tool["name"]:
            raise ValueError(f"environment.tools[{index}].name 非法：{source}")
        tools.append({key: deepcopy(tool[key]) for key in PUBLIC_TOOL_FIELDS})
    names = [tool["name"] for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError(f"environment.tools 工具名重复：{source}")
    return tools


def _edges(value: Any, names: set[str], source: Path) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"tool_graph 必须是 array：{source}")
    result = []
    seen: set[tuple[str, str]] = set()
    for index, edge in enumerate(value):
        if not isinstance(edge, dict):
            raise ValueError(f"tool_graph[{index}] 必须是 object：{source}")
        start, end, weight = edge.get("from_tool"), edge.get("to_tool"), edge.get("weight")
        if start not in names or end not in names:
            raise ValueError(f"tool_graph[{index}] 引用未知工具：{start!r} -> {end!r}")
        if type(weight) is not int or weight not in {1, 2, 3}:
            raise ValueError(f"tool_graph[{index}].weight 必须是 1、2 或 3：{source}")
        if (start, end) in seen:
            raise ValueError(f"tool_graph 存在重复边：{start} -> {end}")
        seen.add((start, end))
        result.append({
            "id": f"base-{index}",
            "source": start,
            "target": end,
            "weight": weight,
            "reason": str(edge.get("reason") or ""),
            "parameter_evidence": deepcopy(edge.get("parameter_evidence") or []),
            "state_evidence": deepcopy(edge.get("state_evidence") or []),
        })
    return result


def _task(candidate: Any, public_tools: list[dict[str, Any]], source: Path, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError(f"tasks[{index}] 必须是 object：{source}")
    task_id, chain = candidate.get("task_id"), candidate.get("chain")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError(f"tasks[{index}].task_id 非法：{source}")
    if not isinstance(chain, list) or not chain or any(not isinstance(item, str) for item in chain):
        raise ValueError(f"tasks[{index}].chain 必须是非空字符串数组：{source}")
    validation = candidate.get("validation")
    if validation is None:
        passed, errors = None, []
    else:
        if not isinstance(validation, dict) or type(validation.get("passed")) is not bool:
            raise ValueError(f"tasks[{index}].validation 非法：{source}")
        passed, errors = validation["passed"], validation.get("errors")
        if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
            raise ValueError(f"tasks[{index}].validation.errors 非法：{source}")
    execution = candidate.get("execution") if isinstance(candidate.get("execution"), dict) else {}
    review = candidate.get("llm_review") if isinstance(candidate.get("llm_review"), dict) else {}
    formal = deepcopy(candidate.get("task")) if isinstance(candidate.get("task"), dict) else None
    if formal is not None:
        formal["available_tools"] = deepcopy(public_tools)
    original = review.get("original_chain")
    if not isinstance(original, list) or any(not isinstance(item, str) for item in original):
        original = []
    calls = execution.get("tool_calls")
    if not isinstance(calls, list):
        calls = []
    attempts = execution.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    last_attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    failed_tool = last_attempt.get("failed_tool")
    if not isinstance(failed_tool, str) or failed_tool not in chain:
        failed_tool = chain[len(calls)] if execution.get("success") is False and len(calls) < len(chain) else None
    return {
        "task_id": task_id,
        "chain": chain,
        "original_chain": original,
        "score": candidate.get("score"),
        "passed": passed,
        "validation_errors": errors,
        "task_text": candidate.get("task_text"),
        "reference_answer": candidate.get("reference_answer"),
        "compose_error": candidate.get("compose_error"),
        "llm_review": {
            "reason": review.get("reason"),
            "error": review.get("error"),
        },
        "execution": {
            "success": execution.get("success"),
            "error": execution.get("error"),
            "tool_calls": deepcopy(calls),
            "attempt_count": len(attempts),
            "failed_tool": failed_tool,
            "failure_kind": last_attempt.get("failure_kind"),
        },
        "task": formal,
    }


def pack_bundle(bundle_path: Path, meta: dict[str, Any] | None = None, view_id: str | None = None) -> dict[str, Any]:
    """校验并投影一个平铺 Bundle，不携带 internal、配置路径或 workspace 内容。"""
    bundle = _object(bundle_path)
    stage = bundle.get("_step")
    if not isinstance(stage, str) or not stage:
        raise ValueError(f"Bundle._step 非法：{bundle_path}")
    environment = bundle.get("environment")
    if not isinstance(environment, dict):
        raise ValueError(f"environment 必须是 object：{bundle_path}")
    environment_id = environment.get("environment_id")
    if not isinstance(environment_id, str) or not environment_id:
        raise ValueError(f"environment_id 非法：{bundle_path}")
    tools = _public_tools(environment, bundle_path)
    edges = _edges(bundle.get("tool_graph", []), {tool["name"] for tool in tools}, bundle_path)
    raw_tasks = bundle.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise ValueError(f"tasks 必须是 array：{bundle_path}")
    tasks = [_task(candidate, tools, bundle_path, index) for index, candidate in enumerate(raw_tasks)]
    meta = meta or {}
    llm = meta.get("config", {}).get("llm", {}) if isinstance(meta.get("config"), dict) else {}
    if not isinstance(llm, dict):
        llm = {}
    timings = meta.get("stage_timings_seconds")
    if not isinstance(timings, dict):
        timings = {}
    passed = sum(task["passed"] is True for task in tasks)
    rejected = sum(task["passed"] is False for task in tasks)
    pending = len(tasks) - passed - rejected
    return {
        "id": view_id or f"{bundle_path.parent.parent.name}:{stage}",
        "stage": stage,
        "environment_id": environment_id,
        "environment_name": str(environment.get("name") or environment_id),
        "environment_description": str(environment.get("description") or ""),
        "created_at": str(meta.get("created_at") or ""),
        "model": str(llm.get("model") or "unknown"),
        "backend": str(llm.get("backend") or "unknown"),
        "stage_timings_seconds": deepcopy(timings),
        "tools": tools,
        "edges": edges,
        "tasks": tasks,
        "counts": {
            "tools": len(tools),
            "edges": len(edges),
            "candidates": len(tasks),
            "passed": passed,
            "rejected": rejected,
            "pending": pending,
        },
    }


def pack_run(run_dir: Path) -> dict[str, Any]:
    """校验并投影一个 completed run。"""
    meta = _object(run_dir / "run.json")
    if meta.get("status") != "completed":
        raise ValueError(f"run 必须是 completed：{run_dir}")
    result = pack_bundle(run_dir / "intermediate" / "step_5_bundle.json", meta, run_dir.name)
    if result["counts"]["pending"]:
        raise ValueError(f"completed run 包含未验证任务：{run_dir}")
    return result


def pack_input(path: Path) -> dict[str, Any]:
    if path.is_dir():
        return pack_run(path)
    meta_path = path.parent.parent / "run.json"
    meta = _object(meta_path) if meta_path.is_file() else {}
    return pack_bundle(path, meta)


def safe_json(value: Any) -> str:
    """生成可放入 application/json script 元素且不能闭合元素的 JSON。"""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_html(runs: list[dict[str, Any]]) -> str:
    return TEMPLATE.replace("__RUN_DATA__", safe_json(runs))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Tool Graph bundles or completed runs to one HTML viewer")
    parser.add_argument("inputs", nargs="+", type=Path, help="bundle files, run directories, or result parent directories")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        sources = discover_inputs(args.inputs)
        runs = [pack_input(path) for path in sources]
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(render_html(runs), encoding="utf-8")
        temporary.replace(output)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    candidates = sum(run["counts"]["candidates"] for run in runs)
    passed = sum(run["counts"]["passed"] for run in runs)
    rejected = sum(run["counts"]["rejected"] for run in runs)
    pending = sum(run["counts"]["pending"] for run in runs)
    print(f"written: {output} | runs={len(runs)} candidates={candidates} passed={passed} rejected={rejected} pending={pending}")


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tool Graph 任务结果查看器</title>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape@3.33.4/dist/cytoscape.min.js"></script>
  <style>
    :root {
      color-scheme: light;
      --bg: #edf1f0; --surface: #fff; --surface-soft: #f6f8f7;
      --ink: #17201e; --muted: #66736f; --line: #d7dfdc;
      --accent: #0b7562; --accent-soft: #e4f2ee;
      --pass: #0b8069; --pass-soft: #e2f3ee; --fail: #b13d46; --fail-soft: #fae9eb;
      --graph: #64726e; --chain: #26332f;
      font-family: Inter, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body { margin: 0; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr); overflow: hidden; background: var(--bg); color: var(--ink); }
    button, input, select { font: inherit; }
    button, select { cursor: pointer; }
    code, pre { font-family: "Cascadia Code", Consolas, monospace; }
    .topbar { min-height: 64px; display: flex; align-items: center; gap: 18px; padding: 10px 18px; background: #182321; color: white; border-bottom: 3px solid var(--accent); }
    .brand { min-width: 250px; }
    .brand h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
    .brand p { margin: 3px 0 0; color: #b9c7c3; font-size: 11px; }
    .controls { flex: 1; display: grid; grid-template-columns: minmax(150px, 1fr) minmax(190px, 1.25fr) 110px minmax(170px, .9fr); gap: 8px; }
    .controls select, .controls input { min-width: 0; width: 100%; height: 36px; padding: 6px 9px; color: var(--ink); background: #fff; border: 1px solid #8fa09b; border-radius: 5px; }
    .metrics { min-height: 54px; display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 1px; background: var(--line); border-bottom: 1px solid var(--line); }
    .metric { min-width: 0; padding: 8px 15px; background: var(--surface); }
    .metric strong { display: block; font-size: 18px; line-height: 1.05; }
    .metric span { display: block; margin-top: 4px; color: var(--muted); font-size: 10px; text-transform: uppercase; }
    .environment-summary { display: flex; align-items: baseline; gap: 10px; min-height: 38px; padding: 8px 15px; border-bottom: 1px solid var(--line); background: var(--surface-soft); font-size: 12px; line-height: 1.45; }
    .environment-summary strong { flex: 0 0 auto; font-size: 11px; }
    .environment-summary span { min-width: 0; color: #4f5e59; overflow-wrap: anywhere; }
    .workspace { min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 390px; }
    .graph-pane { position: relative; min-width: 0; min-height: 0; background: #f8faf9; border-right: 1px solid var(--line); }
    #cy { position: absolute; inset: 0; }
    .graph-toolbar { position: absolute; z-index: 5; top: 12px; left: 12px; display: flex; gap: 6px; }
    .icon-button { width: 36px; height: 36px; display: grid; place-items: center; padding: 0; border: 1px solid #aebbb7; border-radius: 5px; background: rgba(255,255,255,.94); color: #26332f; font-size: 18px; }
    .icon-button:hover { border-color: var(--accent); color: var(--accent); }
    .legend { position: absolute; z-index: 5; left: 12px; bottom: 12px; display: grid; gap: 5px; max-width: calc(100% - 24px); padding: 8px 10px; border: 1px solid var(--line); border-radius: 5px; background: rgba(255,255,255,.96); color: #4f5e59; font-size: 11px; }
    .legend-row { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 12px; }
    .legend strong { min-width: 88px; color: var(--ink); font-size: 10px; }
    .legend span { white-space: nowrap; }
    .line-swatch { display: inline-block; width: 20px; border-top: 3px solid; margin-right: 4px; vertical-align: middle; }
    .line-swatch.dashed { border-top-style: dashed; }
    .node-swatch { display: inline-block; width: 24px; height: 14px; margin-right: 4px; vertical-align: middle; border: 3px solid; border-radius: 3px; background: white; }
    .node-swatch.dashed { border-style: dashed; }
    .graph-detail { position: absolute; z-index: 6; top: 12px; right: 12px; width: min(520px, calc(100% - 24px)); max-height: calc(100% - 24px); overflow: auto; padding: 13px 15px; border: 1px solid #8fa09b; border-radius: 6px; background: rgba(255,255,255,.98); box-shadow: 0 5px 18px rgba(24,35,33,.16); }
    .graph-detail[hidden] { display: none; }
    .graph-detail h3 { margin: 0 30px 7px 0; font-size: 14px; }
    .graph-detail p { margin: 5px 0; font-size: 12px; line-height: 1.5; }
    .detail-label { margin-top: 12px !important; color: var(--muted); font-size: 10px !important; font-weight: 700; text-transform: uppercase; }
    .graph-detail pre { max-height: 190px; overflow: auto; margin: 7px 0 0; padding: 8px; background: var(--surface-soft); font-size: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .close-detail { position: absolute; top: 7px; right: 7px; width: 28px; height: 28px; border: 0; background: transparent; font-size: 19px; }
    .inspector { min-width: 0; min-height: 0; display: grid; grid-template-rows: minmax(120px, 42%) 9px minmax(120px, 1fr); overflow: hidden; background: var(--surface); }
    .chain-pane { min-height: 0; overflow: hidden; background: #eef2f1; }
    .pane-head { min-height: 41px; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 9px 12px; border-bottom: 1px solid var(--line); background: var(--surface); }
    .pane-head h2 { margin: 0; font-size: 13px; }
    .pane-head span { color: var(--muted); font-size: 11px; }
    .chain-list { height: calc(100% - 41px); overflow-y: auto; overscroll-behavior: contain; padding: 8px; }
    .chain-item { width: 100%; display: block; margin-bottom: 6px; padding: 9px 10px; text-align: left; color: var(--ink); background: var(--surface); border: 1px solid transparent; border-left: 4px solid var(--pass); border-radius: 5px; }
    .chain-item.rejected { border-left-color: var(--fail); }
    .chain-item.pending { border-left-color: #62716c; }
    .chain-item:hover { outline: 1px solid #aebbb7; outline-offset: -1px; }
    .chain-item.active { outline: 2px solid var(--chain); outline-offset: -2px; }
    .chain-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .task-id { color: var(--accent); font: 700 11px "Cascadia Code", Consolas, monospace; }
    .badge { display: inline-flex; align-items: center; min-height: 21px; padding: 2px 7px; border-radius: 10px; color: var(--pass); background: var(--pass-soft); font-size: 10px; font-weight: 700; }
    .badge.rejected { color: var(--fail); background: var(--fail-soft); }
    .badge.pending { color: #485853; background: #e8edeb; }
    .chain-path { margin-top: 6px; color: #36443f; font: 10px/1.4 "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }
    .chain-meta { margin-top: 5px; color: var(--muted); font-size: 10px; }
    .chain-error { margin-top: 5px; color: var(--fail); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .inspector-splitter { position: relative; z-index: 2; width: 100%; padding: 0; border: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: #e7ecea; cursor: row-resize; touch-action: none; }
    .inspector-splitter::after { content: ""; position: absolute; top: 3px; left: 50%; width: 34px; height: 3px; border-radius: 2px; background: #81908b; transform: translateX(-50%); }
    .inspector-splitter:hover, .inspector-splitter:focus-visible { background: var(--accent-soft); outline: none; }
    .task-pane { min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
    .task-detail { padding: 17px 18px 70px; }
    .detail-empty { padding: 54px 20px; color: var(--muted); text-align: center; }
    .detail-kicker { display: flex; align-items: center; gap: 7px; color: var(--accent); font: 700 11px "Cascadia Code", Consolas, monospace; }
    .task-detail h2 { margin: 9px 0 7px; font-size: 18px; line-height: 1.35; letter-spacing: 0; overflow-wrap: anywhere; }
    .submeta { color: var(--muted); font-size: 11px; }
    .section { padding: 16px 0; border-bottom: 1px solid var(--line); }
    .section h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; color: #51605b; }
    .chain-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
    .chain-step { padding: 5px 7px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface-soft); font: 10px/1.3 "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }
    .chain-arrow { color: #8a9893; }
    .answer, .error-box { margin: 0; padding: 11px 12px; border-radius: 5px; font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
    .answer { border-left: 4px solid var(--accent); background: var(--accent-soft); }
    .error-box { border-left: 4px solid var(--fail); background: var(--fail-soft); color: #70252c; }
    .error-list { margin: 0; padding-left: 19px; }
    .error-list li { margin: 4px 0; }
    details { border-top: 1px solid var(--line); }
    details:last-child { border-bottom: 1px solid var(--line); }
    summary { padding: 10px 3px; cursor: pointer; font-size: 11px; font-weight: 650; overflow-wrap: anywhere; }
    .call-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 0 0 12px; }
    .call-grid pre, .raw { min-width: 0; max-height: 260px; overflow: auto; margin: 0; padding: 9px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface-soft); font-size: 9px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
    .small-note { color: var(--muted); font-size: 10px; line-height: 1.45; }
    .fatal { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; padding: 24px; background: var(--bg); }
    .fatal div { max-width: 640px; padding: 22px; border: 1px solid var(--fail); border-radius: 7px; background: white; }
    .fatal h2 { margin-top: 0; }
    [hidden] { display: none !important; }
    @media (max-width: 980px) {
      .topbar { align-items: flex-start; }
      .brand { min-width: 210px; }
      .controls { grid-template-columns: 1fr 1fr; }
      .workspace { grid-template-columns: minmax(0, 1fr) 340px; }
    }
    @media (max-width: 800px) {
      body { display: block; overflow: auto; }
      .topbar { display: block; padding: 12px; }
      .brand { margin-bottom: 10px; }
      .controls { grid-template-columns: 1fr 1fr; }
      .metrics { height: auto; grid-template-columns: repeat(3, 1fr); }
      .metric { min-height: 52px; }
      .environment-summary { display: block; }
      .environment-summary strong { display: block; margin-bottom: 3px; }
      .workspace { display: block; height: auto; }
      .graph-pane { height: 56vh; min-height: 390px; border-right: 0; border-bottom: 1px solid var(--line); }
      .inspector { display: block; overflow: visible; }
      .chain-pane { height: 310px; }
      .inspector-splitter { display: none; }
      .task-pane { overflow: visible; }
    }
    @media (max-width: 480px) {
      .controls { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .graph-pane { height: 470px; min-height: 0; }
      .legend { right: 10px; left: 10px; }
      .call-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><h1>Tool Graph 任务结果</h1><p id="run-subtitle">加载运行数据</p></div>
    <div class="controls">
      <select id="environment-select" aria-label="选择环境"></select>
      <select id="run-select" aria-label="选择运行轮次"></select>
      <select id="status-select" aria-label="筛选链状态">
        <option value="all">全部链</option><option value="passed">仅通过</option><option value="rejected">仅拒绝</option><option value="pending">待验证</option>
      </select>
      <input id="search" type="search" placeholder="搜索 task、工具或文本" aria-label="搜索任务链">
    </div>
  </header>
  <section class="metrics" id="metrics" aria-label="当前运行统计"></section>
  <section class="environment-summary" id="environment-summary" aria-label="环境简介"></section>
  <main class="workspace">
    <section class="graph-pane" aria-label="工具依赖图">
      <div id="cy"></div>
      <div class="graph-toolbar">
        <button class="icon-button" id="fit-button" title="适配全部节点" aria-label="适配全部节点">&#x26F6;</button>
        <button class="icon-button" id="layout-button" title="重新计算图布局" aria-label="重新计算图布局">&#x21BB;</button>
      </div>
      <div class="legend">
        <div class="legend-row"><strong>1 · 直接依赖</strong><span>A → B：调用 B 前应先调用 A；线越粗，直接依赖越强</span>
          <span><i class="line-swatch" style="border-color:var(--graph);border-width:5px"></i>weight 3 强依赖</span>
          <span><i class="line-swatch" style="border-color:var(--graph);border-width:3px"></i>weight 2 条件依赖</span>
          <span><i class="line-swatch" style="border-color:var(--graph);border-width:1px"></i>weight 1 辅助依赖</span>
        </div>
        <div class="legend-row"><strong>2 · 候选链</strong>
          <span><i class="line-swatch" style="border-color:var(--chain)"></i>黑色箭头 = 候选链调用顺序（与灰色依赖边错开）</span>
          <span><i class="node-swatch dashed" style="border-color:var(--chain)"></i>LLM 新增工具</span>
          <span><i class="line-swatch dashed" style="border-color:var(--chain)"></i>LLM 新增路径</span>
          <span>再次点击可取消选择</span>
        </div>
        <div class="legend-row"><strong>3 · 执行结果</strong>
          <span><i class="line-swatch" style="border-color:var(--pass);border-width:8px;opacity:.32"></i>绿色底轨 = 已执行成功</span>
          <span><i class="node-swatch" style="border-color:var(--fail);background:var(--fail-soft)"></i>红色工具 = 执行卡点</span>
          <span><i class="line-swatch" style="border-color:var(--fail);border-width:8px;opacity:.32"></i>红色底轨 = 失败及后续步骤</span>
        </div>
      </div>
      <div class="graph-detail" id="graph-detail" hidden><button class="close-detail" id="close-detail" aria-label="关闭图详情">&times;</button><div id="graph-detail-content"></div></div>
    </section>
    <aside class="inspector" aria-label="任务链检查器">
      <section class="chain-pane"><div class="pane-head"><h2>候选链</h2><span id="visible-count"></span></div><div class="chain-list" id="chain-list"></div></section>
      <button class="inspector-splitter" id="inspector-splitter" type="button" role="separator" aria-label="调整候选链和任务详情高度" aria-orientation="horizontal"></button>
      <section class="task-pane" id="task-pane"><div class="detail-empty">选择一条链查看任务。</div></section>
    </aside>
  </main>
  <div class="fatal" id="fatal" hidden><div><h2>图渲染依赖加载失败</h2><p>页面数据已经嵌入，但无法从 CDN 加载 Cytoscape.js。请检查网络连接后重新打开此 HTML。</p></div></div>
  <script id="run-data" type="application/json">__RUN_DATA__</script>
  <script>
  (() => {
    "use strict";
    const runs = JSON.parse(document.getElementById("run-data").textContent);
    const $ = id => document.getElementById(id);
    const dom = {
      environment: $("environment-select"), run: $("run-select"), status: $("status-select"), search: $("search"),
      metrics: $("metrics"), environmentSummary: $("environment-summary"), subtitle: $("run-subtitle"), list: $("chain-list"), visible: $("visible-count"),
      task: $("task-pane"), inspector: document.querySelector(".inspector"), splitter: $("inspector-splitter"),
      fit: $("fit-button"), layout: $("layout-button"), graphDetail: $("graph-detail"),
      graphDetailContent: $("graph-detail-content"), closeDetail: $("close-detail"), fatal: $("fatal")
    };
    const state = { run: null, cy: null, tasks: [], selected: null, focused: null };
    const PASS = "#0b8069", FAIL = "#b13d46", PENDING = "#62716c";

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    }
    function pretty(value) { return JSON.stringify(value ?? null, null, 2); }
    function sameChain(a, b) { return JSON.stringify(a || []) === JSON.stringify(b || []); }
    function chainHtml(chain) {
      if (!chain?.length) return '<span class="small-note">无</span>';
      return '<div class="chain-flow">' + chain.map((tool, index) =>
        `${index ? '<span class="chain-arrow">→</span>' : ''}<span class="chain-step">${index + 1}. ${escapeHtml(tool)}</span>`
      ).join("") + "</div>";
    }
    function detailsJson(label, value, cssClass="raw") {
      return `<details><summary>${escapeHtml(label)}</summary><pre class="${cssClass}">${escapeHtml(pretty(value))}</pre></details>`;
    }
    function setupSplitter() {
      let dragging = false;
      const resize = clientY => {
        const bounds = dom.inspector.getBoundingClientRect();
        const height = Math.min(Math.max(clientY - bounds.top, 120), Math.max(120, bounds.height - 129));
        dom.inspector.style.gridTemplateRows = `${height}px 9px minmax(120px, 1fr)`;
        dom.splitter.setAttribute("aria-valuenow", Math.round(height));
      };
      dom.splitter.addEventListener("pointerdown", event => {
        dragging = true;
        dom.splitter.setPointerCapture(event.pointerId);
      });
      dom.splitter.addEventListener("pointermove", event => { if (dragging) resize(event.clientY); });
      dom.splitter.addEventListener("pointerup", event => {
        dragging = false;
        dom.splitter.releasePointerCapture(event.pointerId);
      });
      dom.splitter.addEventListener("keydown", event => {
        if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
        event.preventDefault();
        const edge = dom.splitter.getBoundingClientRect().top;
        resize(edge + (event.key === "ArrowDown" ? 24 : -24));
      });
    }

    function environments() {
      const values = [];
      const seen = new Set();
      runs.forEach(run => {
        if (!seen.has(run.environment_id)) { seen.add(run.environment_id); values.push(run); }
      });
      return values;
    }
    function fillEnvironmentOptions() {
      dom.environment.innerHTML = environments().map(run =>
        `<option value="${escapeHtml(run.environment_id)}">${escapeHtml(run.environment_name)}</option>`
      ).join("");
    }
    function fillRunOptions(preferred) {
      const available = runs.filter(run => run.environment_id === dom.environment.value);
      dom.run.innerHTML = available.map((run, index) => {
        const stamp = run.created_at ? run.created_at.replace("T", " ").slice(0, 19) : `轮次 ${index + 1}`;
        return `<option value="${escapeHtml(run.id)}">${escapeHtml(stamp)} · ${escapeHtml(run.model)} · ${escapeHtml(run.stage)}</option>`;
      }).join("");
      if (preferred && available.some(run => run.id === preferred)) dom.run.value = preferred;
    }
    function metric(label, value) { return `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`; }
    function renderMetrics() {
      const c = state.run.counts;
      dom.metrics.innerHTML = metric("工具", c.tools) + metric("直接边", c.edges) + metric("候选链", c.candidates) + metric("通过", c.passed) + metric("拒绝", c.rejected) + (c.pending ? metric("待验证", c.pending) : "");
      dom.subtitle.textContent = `${state.run.environment_id} · ${state.run.stage} · ${state.run.backend}/${state.run.model}`;
      dom.environmentSummary.innerHTML = `<strong>${escapeHtml(state.run.environment_name)}</strong><span>${escapeHtml(state.run.environment_description || "无环境描述")}</span>`;
    }

    function graphElements(run) {
      return [
        ...run.tools.map(tool => ({ data: { id: tool.name, label: tool.name, displayLabel: tool.name, description: tool.description, inputSchema: tool.inputSchema, outputSchema: tool.outputSchema } })),
        ...run.edges.map(edge => ({ data: edge, classes: `base-edge weight-${edge.weight}` }))
      ];
    }
    function graphStyle() {
      return [
        { selector: "node", style: {
          "width": 142, "height": 46, "shape": "round-rectangle", "background-color": "#fff",
          "border-color": "#72847e", "border-width": 1.5, "label": "data(displayLabel)", "font-size": 10,
          "font-family": "Cascadia Code, Consolas, monospace", "color": "#1d2926", "text-wrap": "wrap",
          "text-max-width": 126, "text-overflow-wrap": "anywhere", "text-valign": "center", "text-halign": "center", "text-events": "yes"
        }},
        { selector: "edge", style: {
          "width": 2, "curve-style": "bezier", "line-color": "#64726e", "target-arrow-color": "#64726e",
          "target-arrow-shape": "triangle", "arrow-scale": .75
        }},
        { selector: ".weight-3", style: { "width": 5 }},
        { selector: ".weight-2", style: { "width": 2.7 }},
        { selector: ".weight-1", style: { "width": 1.4 }},
        { selector: ".dimmed", style: { "opacity": .32 }},
        { selector: ".focus-unrelated", style: { "opacity": .16 }},
        { selector: ".focus-center", style: { "border-color": "#0b7562", "border-width": 4, "background-color": "#e4f2ee" }},
        { selector: ".focus-neighbor", style: { "background-color": "#f4f6f5" }},
        { selector: ".chain-node-complete", style: { "background-color": "#e2f3ee" }},
        { selector: ".chain-node-failed", style: { "border-color": FAIL, "border-width": 3, "background-color": "#fae9eb" }},
        { selector: ".llm-added-node", style: { "border-color": "#26332f", "border-width": 2.5, "border-style": "dashed" }},
        { selector: ".llm-added-node.chain-node-failed", style: { "border-color": FAIL }},
        { selector: ".chain-overlay", style: {
          "width": 2.2, "curve-style": "unbundled-bezier", "control-point-distances": 18, "control-point-weights": .5,
          "line-color": "#26332f", "target-arrow-color": "#26332f", "target-arrow-shape": "triangle", "arrow-scale": .8
        }},
        { selector: ".llm-added-edge", style: { "line-style": "dashed" }},
        { selector: ".track-complete", style: { "underlay-color": PASS, "underlay-opacity": .32, "underlay-padding": 7 }},
        { selector: ".track-failed", style: { "underlay-color": FAIL, "underlay-opacity": .32, "underlay-padding": 7 }},
        { selector: ".track-pending", style: { "underlay-color": PENDING, "underlay-opacity": .2, "underlay-padding": 6 }},
        { selector: ".interactive-hover", style: { "overlay-color": "#0b7562", "overlay-opacity": .1, "overlay-padding": 6 }}
      ];
    }
    function placeIsolatedNodes(connected) {
      const isolated = state.cy.nodes().not(connected).toArray().sort((a, b) => a.id().localeCompare(b.id()));
      if (!isolated.length) return;
      const box = connected.length ? connected.boundingBox({ includeLabels: true }) : { x1: 0, y2: 0, w: 600 };
      const columns = Math.max(1, Math.ceil(Math.sqrt(isolated.length * 1.8)));
      const xGap = 166, yGap = 66, gridWidth = (columns - 1) * xGap;
      const startX = box.x1 + box.w / 2 - gridWidth / 2;
      const startY = (connected.length ? box.y2 : 0) + 80;
      isolated.forEach((node, index) => node.position({ x: startX + (index % columns) * xGap, y: startY + Math.floor(index / columns) * yGap }));
    }
    function layoutDirectedComponent(component) {
      const xGap = 180, yGap = 64;
      const nodes = component.nodes().toArray().sort((a, b) => a.id().localeCompare(b.id()));
      const edges = component.edges(".base-edge").toArray();
      const indegree = new Map(nodes.map(node => [node.id(), 0]));
      const rank = new Map(nodes.map(node => [node.id(), 0]));
      edges.forEach(edge => indegree.set(edge.target().id(), indegree.get(edge.target().id()) + 1));
      const queue = nodes.filter(node => indegree.get(node.id()) === 0);
      const visited = new Set();
      for (let index = 0; index < queue.length; index += 1) {
        const node = queue[index];
        visited.add(node.id());
        edges.filter(edge => edge.source().id() === node.id()).forEach(edge => {
          const target = edge.target().id();
          rank.set(target, Math.max(rank.get(target), rank.get(node.id()) + 1));
          indegree.set(target, indegree.get(target) - 1);
          if (indegree.get(target) === 0) queue.push(edge.target());
        });
      }
      nodes.filter(node => !visited.has(node.id())).forEach(node => rank.set(node.id(), 0));
      const columns = new Map();
      nodes.forEach(node => columns.set(rank.get(node.id()), [...(columns.get(rank.get(node.id())) || []), node]));
      columns.forEach((column, level) => column.forEach((node, index) => node.position({
        x: level * xGap,
        y: (index - (column.length - 1) / 2) * yGap,
      })));
      return component.boundingBox({ includeLabels: true });
    }
    function layoutConnectedComponents() {
      const baseEdges = state.cy.edges(".base-edge");
      const connectedNodes = baseEdges.connectedNodes();
      const connectedElements = connectedNodes.union(baseEdges);
      const components = connectedElements.components().filter(component => component.edges().length);
      const boxes = components.map(layoutDirectedComponent);
      if (!components.length) return connectedNodes;
      const columns = Math.max(1, Math.ceil(Math.sqrt(components.length * 1.5)));
      const rows = Math.ceil(components.length / columns);
      const columnWidths = Array(columns).fill(0), rowHeights = Array(rows).fill(0);
      boxes.forEach((box, index) => {
        columnWidths[index % columns] = Math.max(columnWidths[index % columns], box.w);
        rowHeights[Math.floor(index / columns)] = Math.max(rowHeights[Math.floor(index / columns)], box.h);
      });
      const offsets = sizes => sizes.map((_, index) => sizes.slice(0, index).reduce((sum, size) => sum + size, 0) + index * 40);
      const xOffsets = offsets(columnWidths), yOffsets = offsets(rowHeights);
      components.forEach((component, index) => {
        const box = boxes[index];
        const dx = xOffsets[index % columns] - box.x1;
        const dy = yOffsets[Math.floor(index / columns)] - box.y1;
        component.nodes().positions(node => ({ x: node.position("x") + dx, y: node.position("y") + dy }));
      });
      return connectedNodes;
    }
    function runLayout(fit=true) {
      if (!state.cy) return;
      const connected = layoutConnectedComponents();
      placeIsolatedNodes(connected);
      if (fit) state.cy.fit(undefined, 34);
    }
    function showGraphDetail(html) {
      dom.graphDetailContent.innerHTML = html;
      dom.graphDetail.hidden = false;
    }
    function clearFocus(relayout=true) {
      if (!state.cy || !state.focused) return;
      state.cy.stop(true);
      state.cy.elements().removeClass("focus-unrelated focus-center focus-neighbor");
      state.focused = null;
      if (relayout) runLayout(true);
    }
    function focusNode(node) {
      if (!state.cy || !node?.length) return;
      if (state.focused === node.id()) {
        clearFocus(true);
        return;
      }
      state.cy.stop(true);
      if (state.selected) {
        state.selected = null;
        dom.list.querySelectorAll(".chain-item").forEach(item => item.classList.remove("active"));
        renderTask(null);
        clearChainGraph();
      }
      clearFocus(false);
      state.focused = node.id();
      const neighbors = node.connectedEdges(".base-edge").connectedNodes().difference(node);
      const visible = node.union(neighbors);
      state.cy.elements().addClass("focus-unrelated");
      visible.removeClass("focus-unrelated");
      node.addClass("focus-center");
      neighbors.addClass("focus-neighbor");
      state.cy.edges(".base-edge").forEach(edge => {
        if (edge.source().same(node) || edge.target().same(node)) edge.removeClass("focus-unrelated");
      });
      const extent = state.cy.extent();
      const center = { x: (extent.x1 + extent.x2) / 2, y: (extent.y1 + extent.y2) / 2 };
      const ring = state.cy.nodes().not(node).toArray();
      const radius = Math.max(220, ring.length * 30);
      const duration = 350;
      node.animate({ position: center, duration, easing: "ease-in-out" });
      ring.forEach((other, index) => {
        const angle = -Math.PI / 2 + (index * Math.PI * 2) / Math.max(1, ring.length);
        other.animate({ position: { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius }, duration, easing: "ease-in-out" });
      });
      const focusId = state.focused;
      setTimeout(() => {
        if (state.focused === focusId) {
          if (state.cy) state.cy.fit(undefined, 50);
        }
      }, duration);
    }
    function initGraph() {
      if (state.cy) state.cy.destroy();
      state.cy = cytoscape({ container: $("cy"), elements: graphElements(state.run), style: graphStyle(), minZoom: .12, maxZoom: 2.6, wheelSensitivity: .18, boxSelectionEnabled: false });
      state.cy.on("tap", "node", event => {
        const d = event.target.data();
        const added = event.target.hasClass("llm-added-node")
          ? `<p class="detail-label">LLM 新增原因</p><p>${escapeHtml(d.llmReason || "无修订说明")}</p>` : "";
        showGraphDetail(`<h3>工具详情 · ${escapeHtml(d.label)}</h3>${added}<p class="detail-label">工具描述</p><p>${escapeHtml(d.description || "无描述")}</p>${detailsJson("Input Schema", d.inputSchema)}${detailsJson("Output Schema", d.outputSchema)}`);
      });
      state.cy.on("dbltap", "node", event => focusNode(event.target));
      state.cy.on("tap", "edge.base-edge", event => {
        const d = event.target.data();
        showGraphDetail(`<h3>直接边详情 · ${escapeHtml(d.source)} → ${escapeHtml(d.target)}</h3><p><b>边权 weight ${escapeHtml(d.weight)}</b></p><p class="detail-label">连边原因</p><p>${escapeHtml(d.reason || "无连边原因")}</p>${detailsJson("Parameter Evidence", d.parameter_evidence)}${detailsJson("State Evidence", d.state_evidence)}`);
      });
      state.cy.on("tap", "edge.chain-overlay", event => {
        const d = event.target.data();
        const added = d.llmAdded ? `<p class="detail-label">LLM 新增路径原因</p><p>${escapeHtml(d.llmReason || "无修订说明")}</p>` : "";
        showGraphDetail(`<h3>候选链路径 · ${escapeHtml(d.source)} → ${escapeHtml(d.target)}</h3><p>第 ${escapeHtml(d.fromStep)} → ${escapeHtml(d.toStep)} 步 · ${escapeHtml(d.trackLabel)}</p>${added}`);
      });
      state.cy.on("mouseover", "node, edge.base-edge, edge.chain-overlay", event => { event.target.addClass("interactive-hover"); $("cy").style.cursor = "pointer"; });
      state.cy.on("mouseout", "node, edge.base-edge, edge.chain-overlay", event => { event.target.removeClass("interactive-hover"); $("cy").style.cursor = "default"; });
      runLayout(true);
    }

    function filteredTasks() {
      const status = dom.status.value, query = dom.search.value.trim().toLowerCase();
      return state.run.tasks.filter(task => {
        if (status === "passed" && !task.passed) return false;
        if (status === "rejected" && task.passed !== false) return false;
        if (status === "pending" && task.passed !== null) return false;
        if (!query) return true;
        return [task.task_id, task.task_text, task.chain.join(" "), task.validation_errors.join(" ")].join(" ").toLowerCase().includes(query);
      });
    }
    function renderList() {
      state.tasks = filteredTasks();
      dom.visible.textContent = `${state.tasks.length} / ${state.run.tasks.length}`;
      dom.list.innerHTML = state.tasks.length ? state.tasks.map(task => {
        const active = task.task_id === state.selected ? " active" : "";
        const status = task.passed === true ? "passed" : task.passed === false ? "rejected" : "pending";
        const statusClass = status === "passed" ? "" : ` ${status}`;
        const firstError = task.validation_errors[0] || task.compose_error || task.execution.error || "";
        return `<button class="chain-item${statusClass}${active}" data-task-id="${escapeHtml(task.task_id)}">
          <span class="chain-row"><span class="task-id">${escapeHtml(task.task_id)}</span><span class="badge${statusClass}">${status === "passed" ? "通过" : status === "rejected" ? "拒绝" : "待验证"}</span></span>
          <span class="chain-path">${escapeHtml(task.chain.join(" → "))}</span>
          <span class="chain-meta">${task.chain.length} tools · score ${escapeHtml(task.score ?? "—")}</span>
          ${firstError ? `<span class="chain-error">${escapeHtml(firstError)}</span>` : ""}
        </button>`;
      }).join("") : '<div class="detail-empty">没有符合筛选条件的链。</div>';
      dom.list.querySelectorAll("[data-task-id]").forEach(button => button.addEventListener("click", () => selectTask(button.dataset.taskId)));
      if (!state.tasks.some(task => task.task_id === state.selected)) {
        selectTask(state.tasks[0]?.task_id || null);
      }
    }

    function callsHtml(task) {
      const calls = task.execution.tool_calls || [];
      if (!calls.length) return '<p class="small-note">没有成功工具调用。</p>';
      return calls.map((call, index) => `<details><summary>${index + 1}. ${escapeHtml(call.tool || "unknown")}</summary>
        <div class="call-grid"><pre>${escapeHtml(pretty(call.arguments || {}))}</pre><pre>${escapeHtml(pretty(call.result ?? null))}</pre></div></details>`).join("");
    }
    function renderTask(task) {
      if (!task) { dom.task.innerHTML = '<div class="detail-empty">没有选中的任务。</div>'; return; }
      const status = task.passed === true ? "passed" : task.passed === false ? "rejected" : "pending";
      const statusClass = status === "passed" ? "" : ` ${status}`;
      const taskText = task.task_text || task.task?.task_text || "未生成任务文本";
      const executionState = task.execution.success === true ? "success" : task.execution.success === false ? "failed" : "not run";
      dom.task.innerHTML = `<article class="task-detail">
        <div class="detail-kicker"><span>${escapeHtml(task.task_id)}</span><span class="badge${statusClass}">${status === "passed" ? "通过" : status === "rejected" ? "拒绝" : "待验证"}</span></div>
        <h2>${escapeHtml(taskText)}</h2>
        <div class="submeta">${task.chain.length} tools · score ${escapeHtml(task.score ?? "—")} · execution ${executionState} · ${task.execution.attempt_count} attempts</div>
        ${task.execution.error ? `<section class="section"><h3>Step 3 执行中断</h3><div class="error-box"><p>${escapeHtml(task.execution.failed_tool || "未知工具")} · ${escapeHtml(task.execution.failure_kind || "unknown")}</p><p>${escapeHtml(task.execution.error)}</p></div></section>` : ""}
        ${task.compose_error ? `<section class="section"><h3>Step 4 任务生成问题</h3><div class="error-box">${escapeHtml(task.compose_error)}</div></section>` : ""}
        ${task.validation_errors.length ? `<section class="section"><h3>Step 5 未通过原因</h3><div class="error-box"><ul class="error-list">${task.validation_errors.map(error => `<li>${escapeHtml(error)}</li>`).join("")}</ul></div></section>` : ""}
        <section class="section"><h3>最终工具链</h3>${chainHtml(task.chain)}</section>
        ${task.original_chain.length && !sameChain(task.chain, task.original_chain) ? `<section class="section"><h3>LLM 修订前</h3>${chainHtml(task.original_chain)}<p class="small-note">${escapeHtml(task.llm_review.reason || "无修订说明")}</p></section>` : ""}
        ${task.reference_answer ? `<section class="section"><h3>Reference Answer</h3><p class="answer">${escapeHtml(task.reference_answer)}</p></section>` : ""}
        <section class="section"><h3>工具调用</h3>${callsHtml(task)}</section>
        <section class="section"><h3>原始正式 Task</h3>${task.task ? detailsJson("展开 Task JSON", task.task) : '<p class="small-note">没有正式 task；候选在前序阶段失败。</p>'}</section>
      </article>`;
    }

    function clearChainGraph() {
      if (!state.cy) return;
      state.cy.edges(".chain-overlay").remove();
      state.cy.elements().removeClass("dimmed chain-node-complete chain-node-failed llm-added-node");
      state.cy.nodes().forEach(node => {
        node.data("displayLabel", node.data("label"));
        node.removeData("llmReason");
      });
    }
    function layoutSelectedTask(task) {
      if (!state.cy || !task?.chain?.length) return;
      state.cy.stop(true);
      const allNodes = state.cy.nodes();
      const chainNames = [...new Set(task.chain)];
      const chainNodes = state.cy.collection(chainNames.map(name => state.cy.getElementById(name)).filter(node => node.length));
      if (!chainNodes.length) return;
      const related = chainNodes.connectedEdges(".base-edge").connectedNodes().difference(chainNodes);
      const extent = state.cy.extent();
      const center = { x: (extent.x1 + extent.x2) / 2, y: (extent.y1 + extent.y2) / 2 };
      const arcSpan = Math.PI * 1.5;
      const chainRadius = Math.max(280, Math.min(430, (chainNodes.length * 168) / arcSpan));
      const relatedInsideCount = Math.min(related.length, 4);
      const innerRelated = state.cy.collection(related.toArray().slice(0, relatedInsideCount));
      const outer = allNodes.difference(chainNodes.union(innerRelated));
      const relatedRadius = Math.min(chainRadius - 150, Math.max(100, relatedInsideCount * 30));
      const outerRadius = Math.max(chainRadius + 180, relatedRadius + 150, outer.length * 38);
      const duration = 350;
      const animateOnRing = (nodes, radius, startAngle, span) => {
        const divisor = Math.abs(span - Math.PI * 2) < .001 ? nodes.length : Math.max(1, nodes.length - 1);
        nodes.toArray().forEach((item, index) => {
          const angle = startAngle + (index * span) / divisor;
          item.animate({ position: { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius }, duration, easing: "ease-in-out" });
        });
      };
      if (chainNodes.length === 1) {
        chainNodes[0].animate({ position: center, duration, easing: "ease-in-out" });
      } else {
        animateOnRing(chainNodes, chainRadius, -Math.PI * .75, arcSpan);
      }
      animateOnRing(innerRelated, relatedRadius, -Math.PI * .5, Math.PI * 2);
      animateOnRing(outer, outerRadius, 0, Math.PI * 2);
      setTimeout(() => {
        if (state.selected === task.task_id && state.cy) state.cy.fit(undefined, 34);
      }, duration);
    }
    function selectTask(taskId) {
      if (state.focused) clearFocus(true);
      if (taskId && state.selected === taskId) taskId = null;
      state.selected = taskId;
      dom.list.querySelectorAll(".chain-item").forEach(item => item.classList.toggle("active", item.dataset.taskId === taskId));
      const task = state.run.tasks.find(item => item.task_id === taskId) || null;
      renderTask(task);
      clearChainGraph();
      if (!task || !state.cy) {
        if (state.cy) runLayout(true);
        return;
      }
      const indices = new Map();
      task.chain.forEach((name, index) => indices.set(name, [...(indices.get(name) || []), index + 1]));
      const hasOriginal = task.original_chain?.length > 0;
      const originalTools = new Set(task.original_chain || []);
      const originalEdges = new Set((task.original_chain || []).slice(0, -1).map((name, index) => `${name}\u0000${task.original_chain[index + 1]}`));
      const completedCount = task.execution.success === true ? task.chain.length : (task.execution.tool_calls || []).length;
      let failedIndex = -1;
      if (task.execution.success === false) {
        failedIndex = task.chain.findIndex((name, index) => index >= completedCount && name === task.execution.failed_tool);
        if (failedIndex < 0 && completedCount < task.chain.length) failedIndex = completedCount;
      }
      state.cy.elements().addClass("dimmed");
      const highlighted = state.cy.collection();
      indices.forEach((steps, name) => {
        const node = state.cy.getElementById(name);
        if (node.length) {
          const zeroBased = steps.map(step => step - 1);
          node.removeClass("dimmed");
          if (zeroBased.some(index => index < completedCount)) node.addClass("chain-node-complete");
          if (zeroBased.includes(failedIndex)) node.addClass("chain-node-failed");
          if (hasOriginal && !originalTools.has(name)) {
            node.addClass("llm-added-node");
            node.data("llmReason", task.llm_review.reason);
          }
          node.data("displayLabel", `${steps.join(",")} · ${name}`);
          highlighted.merge(node);
        }
      });
      for (let index = 0; index < task.chain.length - 1; index += 1) {
        const source = task.chain[index], target = task.chain[index + 1];
        if (!state.cy.getElementById(source).length || !state.cy.getElementById(target).length) continue;
        const llmAdded = hasOriginal && !originalEdges.has(`${source}\u0000${target}`);
        const track = task.execution.success === null ? "pending" : (index + 1 < completedCount ? "complete" : task.execution.success === true ? "complete" : "failed");
        const trackLabel = track === "complete" ? "已执行成功" : track === "failed" ? "失败或未继续执行" : "尚未执行";
        const added = state.cy.add({
          group: "edges",
          data: { id: `chain-overlay-${index}`, source, target, fromStep: index + 1, toStep: index + 2, llmAdded, llmReason: task.llm_review.reason, trackLabel },
          classes: `chain-overlay track-${track}${llmAdded ? " llm-added-edge" : ""}`,
        });
        highlighted.merge(added);
      }
      highlighted.connectedNodes().removeClass("dimmed");
      layoutSelectedTask(task);
    }

    function switchRun(runId) {
      clearFocus(false);
      state.run = runs.find(run => run.id === runId) || runs[0];
      state.selected = null;
      dom.graphDetail.hidden = true;
      renderMetrics();
      initGraph();
      renderList();
    }
    function boot() {
      if (typeof window.cytoscape !== "function") { dom.fatal.hidden = false; return; }
      if (!runs.length) { dom.fatal.hidden = false; dom.fatal.querySelector("h2").textContent = "没有可显示的运行数据"; return; }
      fillEnvironmentOptions();
      fillRunOptions();
      setupSplitter();
      switchRun(dom.run.value);
      dom.environment.addEventListener("change", () => { fillRunOptions(); switchRun(dom.run.value); });
      dom.run.addEventListener("change", () => switchRun(dom.run.value));
      dom.status.addEventListener("change", renderList);
      dom.search.addEventListener("input", renderList);
      dom.fit.addEventListener("click", () => state.cy?.fit(undefined, 50));
      dom.layout.addEventListener("click", () => { clearFocus(false); runLayout(true); });
      dom.closeDetail.addEventListener("click", () => { dom.graphDetail.hidden = true; });
      document.addEventListener("keydown", event => { if (event.key === "Escape") clearFocus(true); });
    }
    boot();
  })();
  </script>
</body>
</html>
'''


if __name__ == "__main__":
    main()
