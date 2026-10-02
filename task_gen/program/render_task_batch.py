"""Render a self-contained quality report for Program task batches."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from task_gen.program.step_2_solution_generate import (
    _solution_complexity_profile,
    _solution_difficulty_forms,
)


FORM_LABELS = {
    "result_chain": "结果依赖链",
    "result_batch": "结果驱动批处理",
    "per_item_tool_work": "逐项工具处理",
    "multiple_runtime_decisions": "多次运行时决策",
    "conditional_tool_path": "条件工具路径",
    "result_calculation": "结果计算",
    "multiple_evidence_sources": "多来源证据",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        rows.append(value.get("item", value))
    return rows


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: list[int]) -> dict[str, int]:
    return {str(key): count for key, count in sorted(Counter(values).items())}


def _find_attempts(batch_dir: Path) -> dict[str, list[dict[str, Any]]]:
    attempts: dict[str, list[dict[str, Any]]] = {}
    for validation_path in sorted(batch_dir.rglob("step2_validation.json")):
        run_dir = validation_path.parent
        validation = _read_json(validation_path)
        environment_id = run_dir.name
        step0_path = run_dir / "step0_environment.json"
        if step0_path.exists():
            step0 = _read_json(step0_path)
            environment_id = str(
                step0.get("environment_id")
                or step0.get("environment", {}).get("environment_id")
                or environment_id
            )
        attempts.setdefault(environment_id, []).append(
            {
                "environment_id": environment_id,
                "run_dir": run_dir,
                "validation": validation,
            }
        )
    return attempts


def _select_runs(
    attempts: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    environment_status: list[dict[str, Any]] = []
    for environment_id, environment_attempts in sorted(attempts.items()):
        passed = [
            item
            for item in environment_attempts
            if item["validation"].get("status") == "passed"
            and int(item["validation"].get("accepted", 0)) > 0
        ]
        chosen = passed[-1] if passed else environment_attempts[-1]
        selected.append(chosen) if passed else None
        environment_status.append(
            {
                "environment_id": environment_id,
                "status": "passed" if passed else "failed",
                "accepted": int(chosen["validation"].get("accepted", 0)),
                "rejected": int(chosen["validation"].get("rejected", 0)),
                "attempt_count": len(environment_attempts),
                "selected_run": str(chosen["run_dir"]),
            }
        )
    return selected, environment_status


def _collect_tasks(selected_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for run in selected_runs:
        run_dir = run["run_dir"]
        public_tasks = {
            str(item["task_id"]): item
            for item in _read_json(run_dir / "tasks.json")
            if isinstance(item, dict) and item.get("task_id")
        }
        internal_tasks = {
            str(item["task_id"]): item
            for item in _read_jsonl(run_dir / "step2_task_solution.jsonl")
            if item.get("task_id")
        }
        for task_id, public in public_tasks.items():
            internal = internal_tasks.get(task_id, {})
            source = str(
                internal.get("solution_code_fixed")
                or internal.get("solution_code")
                or internal.get("solution_code_original")
                or ""
            )
            profile = _solution_complexity_profile(source)
            forms = sorted(_solution_difficulty_forms(profile))
            reference = public.get("reference") or {}
            raw_answer = reference.get("answer")
            try:
                answer = json.loads(raw_answer) if isinstance(raw_answer, str) else raw_answer
            except json.JSONDecodeError:
                answer = raw_answer
            validation = internal.get("solution_validation") or {}
            tasks.append(
                {
                    "task_id": task_id,
                    "environment_id": str(public.get("environment_id") or run["environment_id"]),
                    "archetype_id": internal.get("archetype_id"),
                    "archetype_name": (internal.get("task_archetype") or {}).get("name"),
                    "summary": public.get("task_summary", ""),
                    "task_text": public.get("task_text", ""),
                    "workspace_brief": public.get("workspace_brief", ""),
                    "resources": public.get("task_resources") or {},
                    "output_schema": public.get("output_schema") or {},
                    "tool_calls": reference.get("tool_calls") or [],
                    "answer": answer,
                    "solution_code": source,
                    "state_changed": bool(validation.get("state_changed")),
                    "clean_replay_count": int(validation.get("clean_replay_count", 0)),
                    "profile": asdict(profile),
                    "difficulty_forms": forms,
                    "difficulty_form_labels": [FORM_LABELS[item] for item in forms],
                    "source_run": str(run_dir),
                }
            )
    return sorted(tasks, key=lambda item: (item["environment_id"], item["task_id"]))


def _task_band(call_count: int) -> str:
    if call_count <= 3:
        return "short"
    if call_count <= 7:
        return "medium"
    return "long"


def _build_stats(
    batch_dir: Path,
    tasks: list[dict[str, Any]],
    environment_status: list[dict[str, Any]],
) -> dict[str, Any]:
    call_counts = [len(item["tool_calls"]) for item in tasks]
    depths = [int(item["profile"]["max_dependency_depth"]) for item in tasks]
    dependent_calls = [int(item["profile"]["dependent_tool_calls"]) for item in tasks]
    distinct_tools = [int(item["profile"]["distinct_tools"]) for item in tasks]
    static_call_sites = [int(item["profile"]["tool_calls"]) for item in tasks]
    bands = Counter(_task_band(value) for value in call_counts)
    tool_usage: Counter[str] = Counter()
    form_usage: Counter[str] = Counter()
    for task in tasks:
        tool_usage.update(str(call.get("tool")) for call in task["tool_calls"])
        form_usage.update(task["difficulty_forms"])

    per_environment = []
    for status in environment_status:
        environment_tasks = [
            item for item in tasks if item["environment_id"] == status["environment_id"]
        ]
        environment_calls = [len(item["tool_calls"]) for item in environment_tasks]
        environment_depths = [
            int(item["profile"]["max_dependency_depth"]) for item in environment_tasks
        ]
        per_environment.append(
            {
                **status,
                "task_count": len(environment_tasks),
                "average_tool_calls": round(mean(environment_calls), 2)
                if environment_calls
                else 0,
                "maximum_tool_calls": max(environment_calls, default=0),
                "average_dependency_depth": round(mean(environment_depths), 2)
                if environment_depths
                else 0,
                "maximum_dependency_depth": max(environment_depths, default=0),
            }
        )

    passed_envs = sum(item["status"] == "passed" for item in environment_status)
    failed_envs = len(environment_status) - passed_envs
    total_rejected = sum(
        int(item["rejected"])
        for item in environment_status
        if item["status"] == "passed"
    )
    long_count = bands["long"]
    deep_count = sum(value >= 3 for value in depths)
    dependency_count = sum(value >= 2 for value in dependent_calls)
    assessment = {
        "rating": "中等偏强，但长链占比仍不足",
        "summary": (
            f"执行调用中位数为 {median(call_counts):g}，{long_count}/{len(tasks)} 条任务达到 8 次以上；"
            f"{deep_count}/{len(tasks)} 条达到至少 3 层静态结果依赖。"
            "这批数据已明显不是单工具任务，但如果目标是专项训练长程工具规划，"
            "应提高 8 次以上调用和 3 层以上结果依赖任务的比例。"
        ),
        "recommended_batch_targets": {
            "executed_tool_calls_median": ">= 7",
            "tasks_with_at_least_8_calls": ">= 35%",
            "tasks_with_dependency_depth_at_least_3": ">= 40%",
            "tasks_with_at_least_2_dependent_call_sites": ">= 50%",
            "note": "这些是批次组合目标，不应作为每条任务的硬性模板。",
        },
    }
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "batch_directory": str(batch_dir),
        "environment_count": len(environment_status),
        "passed_environment_count": passed_envs,
        "failed_environment_count": failed_envs,
        "task_count": len(tasks),
        "rejected_candidate_count_for_passed_runs": total_rejected,
        "tool_calls": {
            "total": sum(call_counts),
            "mean": round(mean(call_counts), 2),
            "median": median(call_counts),
            "p75": _percentile(call_counts, 0.75),
            "p90": _percentile(call_counts, 0.90),
            "minimum": min(call_counts, default=0),
            "maximum": max(call_counts, default=0),
            "distribution": _distribution(call_counts),
            "bands": {
                "short_2_to_3": bands["short"],
                "medium_4_to_7": bands["medium"],
                "long_8_plus": bands["long"],
            },
        },
        "dependency": {
            "static_call_sites_mean": round(mean(static_call_sites), 2),
            "dependent_call_sites_mean": round(mean(dependent_calls), 2),
            "distinct_tools_mean": round(mean(distinct_tools), 2),
            "max_depth_mean": round(mean(depths), 2),
            "max_depth_median": median(depths),
            "max_depth_distribution": _distribution(depths),
            "tasks_with_depth_at_least_3": deep_count,
            "tasks_with_at_least_2_dependent_call_sites": dependency_count,
            "definition": (
                "max_dependency_depth 是基于 solution 变量传播计算的近似静态深度；"
                "executed tool calls 是真实执行轨迹中的调用次数，两者含义不同。"
            ),
        },
        "state_changing_task_count": sum(item["state_changed"] for item in tasks),
        "read_only_task_count": sum(not item["state_changed"] for item in tasks),
        "difficulty_forms": {
            key: {"label": FORM_LABELS[key], "count": count}
            for key, count in sorted(form_usage.items())
        },
        "top_tools": [
            {"tool": tool, "count": count} for tool, count in tool_usage.most_common(30)
        ],
        "environment_status": per_environment,
        "assessment": assessment,
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Program 任务批次质量报告</title>
<style>
:root{--ink:#18211f;--muted:#64706c;--line:#d8dfdc;--paper:#f5f7f6;--white:#fff;--teal:#087f6b;--teal-soft:#dff3ed;--amber:#a65d00;--amber-soft:#fff0d3;--red:#b42318;--red-soft:#fee4e2;--blue:#2457a7;--shadow:0 8px 28px rgba(23,42,36,.10)}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--paper);color:var(--ink);font-family:Inter,"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif;letter-spacing:0}button,input,select{font:inherit}button{cursor:pointer}.topbar{background:#123c36;color:#fff;padding:22px 28px;border-bottom:4px solid #d8a22e}.topline{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.topbar h1{font-size:24px;line-height:1.25;margin:0 0 7px}.subtitle{margin:0;color:#cfe0dc;font-size:13px;overflow-wrap:anywhere}.stamp{text-align:right;color:#cfe0dc;font-size:12px;white-space:nowrap}.page{max-width:1680px;margin:0 auto;padding:20px 24px 32px}.kpis{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));border:1px solid var(--line);background:var(--white);box-shadow:var(--shadow)}.kpi{padding:17px 18px;border-right:1px solid var(--line)}.kpi:last-child{border-right:0}.kpi-label{font-size:12px;color:var(--muted);margin-bottom:7px}.kpi-value{font-size:25px;font-weight:750;font-variant-numeric:tabular-nums}.kpi-note{font-size:11px;color:var(--muted);margin-top:3px}.analysis{display:grid;grid-template-columns:1.2fr 1fr .9fr;gap:14px;margin-top:14px}.panel{background:var(--white);border:1px solid var(--line);padding:16px;min-width:0}.panel h2{font-size:15px;margin:0 0 13px}.histogram{height:142px;display:flex;align-items:flex-end;gap:7px;border-bottom:1px solid var(--line);padding:4px 3px 0}.bar-wrap{height:100%;flex:1;min-width:20px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px}.bar-value{font-size:10px;color:var(--muted)}.bar{width:100%;max-width:34px;background:var(--teal);min-height:2px}.bar.depth{background:var(--blue)}.bar-label{font-size:10px;color:var(--muted);height:17px}.assessment{border-left:5px solid #d8a22e;background:#fffaf0}.assessment strong{display:block;font-size:16px;margin-bottom:7px}.assessment p{font-size:13px;line-height:1.7;margin:0;color:#4e554f}.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:11px;font-size:11px;color:var(--muted)}.legend i{display:inline-block;width:9px;height:9px;margin-right:5px}.workspace{margin-top:14px;display:grid;grid-template-columns:minmax(430px,42%) minmax(0,58%);height:calc(100vh - 355px);min-height:600px;background:var(--white);border:1px solid var(--line);box-shadow:var(--shadow)}.master{border-right:1px solid var(--line);display:flex;flex-direction:column;min-width:0}.toolbar{padding:13px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr 170px 110px;gap:8px}.toolbar input,.toolbar select{height:38px;border:1px solid #bcc8c4;background:#fff;padding:0 10px;color:var(--ink);min-width:0}.resultline{padding:9px 14px;color:var(--muted);font-size:12px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.task-list{overflow:auto}.task-row{width:100%;display:grid;grid-template-columns:52px 1fr 48px;border:0;border-bottom:1px solid #e6ebe9;background:#fff;text-align:left;padding:13px 12px;gap:10px;color:var(--ink)}.task-row:hover{background:#f2f8f6}.task-row.active{background:var(--teal-soft);box-shadow:inset 4px 0 0 var(--teal)}.count-box{height:42px;border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-weight:750;font-variant-numeric:tabular-nums;background:#fff}.task-main{min-width:0}.task-env{font-size:11px;color:var(--teal);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:5px}.task-title{font-size:13px;line-height:1.45;font-weight:650}.task-meta{display:flex;gap:8px;align-items:center;margin-top:7px;color:var(--muted);font-size:10px}.depth-box{text-align:right;font-size:10px;color:var(--muted);padding-top:4px}.depth-box b{display:block;font-size:16px;color:var(--blue)}.detail{overflow:auto;padding:20px 22px 50px}.empty{height:100%;display:grid;place-items:center;color:var(--muted)}.detail-head{border-bottom:1px solid var(--line);padding-bottom:16px}.eyebrow{font-size:11px;color:var(--teal);overflow-wrap:anywhere}.detail h2{font-size:20px;line-height:1.5;margin:6px 0 10px}.badges{display:flex;gap:6px;flex-wrap:wrap}.badge{font-size:11px;padding:3px 7px;border:1px solid #aed0c7;background:var(--teal-soft);color:#075e50}.badge.amber{border-color:#e7c27f;background:var(--amber-soft);color:#744300}.badge.blue{border-color:#b6c8e5;background:#eaf1fb;color:#194785}.section{padding:18px 0;border-bottom:1px solid var(--line)}.section h3{font-size:14px;margin:0 0 10px}.prose{white-space:pre-wrap;font-size:13px;line-height:1.75;color:#2d3734}.metric-strip{display:grid;grid-template-columns:repeat(5,minmax(90px,1fr));border:1px solid var(--line)}.mini{padding:10px;border-right:1px solid var(--line)}.mini:last-child{border-right:0}.mini span{display:block;color:var(--muted);font-size:10px;margin-bottom:4px}.mini b{font-size:17px}.call{display:grid;grid-template-columns:32px 190px 1fr;gap:10px;align-items:start;padding:10px 0;border-bottom:1px solid #e6ebe9}.call:last-child{border-bottom:0}.call-index{width:26px;height:26px;background:#123c36;color:#fff;display:grid;place-items:center;font-size:11px}.call-tool{font-size:12px;font-weight:700;overflow-wrap:anywhere;padding-top:5px}.code{margin:0;background:#17211f;color:#dce8e4;padding:12px;overflow:auto;max-height:420px;font:11px/1.55 "SFMono-Regular",Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.resource-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.resource-title{font-size:11px;color:var(--muted);margin-bottom:6px}.resource-items{font-size:12px;line-height:1.7;overflow-wrap:anywhere}.tabs{display:flex;border-bottom:1px solid var(--line);margin-bottom:10px}.tab{border:0;background:#fff;padding:8px 12px;color:var(--muted);border-bottom:3px solid transparent}.tab.active{color:var(--teal);border-bottom-color:var(--teal);font-weight:700}.tab-pane{display:none}.tab-pane.active{display:block}.failure{color:var(--red);background:var(--red-soft);padding:2px 6px}.footer-note{font-size:11px;color:var(--muted);margin-top:12px;line-height:1.6}@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.kpi:nth-child(3){border-right:0}.analysis{grid-template-columns:1fr 1fr}.assessment{grid-column:1/-1}.workspace{grid-template-columns:1fr;height:auto}.master{border-right:0;border-bottom:1px solid var(--line);height:620px}.detail{min-height:700px}.call{grid-template-columns:32px 1fr}.call pre{grid-column:2}}@media(max-width:700px){.topbar,.page{padding-left:14px;padding-right:14px}.topline{display:block}.stamp{text-align:left;margin-top:10px}.kpis{grid-template-columns:repeat(2,1fr)}.kpi:nth-child(2n){border-right:0}.analysis{grid-template-columns:1fr}.assessment{grid-column:auto}.toolbar{grid-template-columns:1fr}.workspace{display:block}.master{height:580px}.metric-strip{grid-template-columns:repeat(2,1fr)}.mini{border-bottom:1px solid var(--line)}.resource-grid{grid-template-columns:1fr}.call{grid-template-columns:30px 1fr}.detail{padding:17px 14px}.task-row{grid-template-columns:46px 1fr 42px}}
</style>
</head>
<body>
<header class="topbar"><div class="topline"><div><h1>Program 任务批次质量报告</h1><p class="subtitle" id="batchPath"></p></div><div class="stamp" id="stamp"></div></div></header>
<main class="page">
  <section class="kpis" id="kpis"></section>
  <section class="analysis">
    <div class="panel"><h2>真实执行工具调用数分布</h2><div class="histogram" id="callChart"></div><div class="legend"><span><i style="background:var(--teal)"></i>每条任务真实执行轨迹</span></div></div>
    <div class="panel"><h2>结果依赖深度分布</h2><div class="histogram" id="depthChart"></div><div class="legend"><span><i style="background:var(--blue)"></i>基于 solution 变量传播的静态近似</span></div></div>
    <div class="panel assessment"><h2>链长判断</h2><strong id="rating"></strong><p id="assessmentText"></p><div class="footer-note" id="definition"></div></div>
  </section>
  <section class="workspace">
    <aside class="master">
      <div class="toolbar"><input id="search" type="search" placeholder="搜索任务、环境或工具"><select id="environment"><option value="">全部环境</option></select><select id="calls"><option value="">全部链长</option><option value="short">2-3 次</option><option value="medium">4-7 次</option><option value="long">8 次以上</option></select></div>
      <div class="resultline"><span id="resultCount"></span><span>点击任务查看详情</span></div>
      <div class="task-list" id="taskList"></div>
    </aside>
    <article class="detail" id="detail"><div class="empty">从左侧选择一条任务</div></article>
  </section>
</main>
<script>
const REPORT_DATA = __REPORT_DATA__;
const stats = REPORT_DATA.stats;
const tasks = REPORT_DATA.tasks;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const pretty = (value) => JSON.stringify(value, null, 2);
const band = (count) => count <= 3 ? 'short' : count <= 7 ? 'medium' : 'long';
let filtered = tasks.slice();
let selectedId = tasks[0]?.task_id;

function renderKpis(){
  const values = [
    ['有效任务', stats.task_count, `${stats.passed_environment_count}/${stats.environment_count} 个环境完成`],
    ['平均调用数', stats.tool_calls.mean, `中位数 ${stats.tool_calls.median}`],
    ['最长调用链', stats.tool_calls.maximum, `P90 ${stats.tool_calls.p90}`],
    ['8 次以上任务', stats.tool_calls.bands.long_8_plus, `${(stats.tool_calls.bands.long_8_plus/stats.task_count*100).toFixed(1)}%`],
    ['依赖深度 >= 3', stats.dependency.tasks_with_depth_at_least_3, `${(stats.dependency.tasks_with_depth_at_least_3/stats.task_count*100).toFixed(1)}%`],
    ['状态变更任务', stats.state_changing_task_count, `只读 ${stats.read_only_task_count}`]
  ];
  $('kpis').innerHTML = values.map(([label,value,note]) => `<div class="kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`).join('');
}
function renderChart(id, distribution, className=''){
  const entries = Object.entries(distribution).map(([key,value])=>[Number(key),value]).sort((a,b)=>a[0]-b[0]);
  const max = Math.max(...entries.map(item=>item[1]),1);
  $(id).innerHTML = entries.map(([key,value]) => `<div class="bar-wrap"><span class="bar-value">${value}</span><div class="bar ${className}" style="height:${Math.max(3,value/max*103)}px" title="${key}: ${value} 条"></div><span class="bar-label">${key}</span></div>`).join('');
}
function populateFilters(){
  const envs = [...new Set(tasks.map(item=>item.environment_id))].sort();
  $('environment').innerHTML += envs.map(env=>`<option value="${esc(env)}">${esc(env.replace('semiconductor_',''))}</option>`).join('');
}
function applyFilters(){
  const query = $('search').value.trim().toLowerCase();
  const environment = $('environment').value;
  const callBand = $('calls').value;
  filtered = tasks.filter(task => {
    const haystack = [task.task_id,task.environment_id,task.summary,task.task_text,...task.tool_calls.map(call=>call.tool)].join(' ').toLowerCase();
    return (!query || haystack.includes(query)) && (!environment || task.environment_id===environment) && (!callBand || band(task.tool_calls.length)===callBand);
  });
  if (!filtered.some(item=>item.task_id===selectedId)) selectedId=filtered[0]?.task_id;
  renderList(); renderDetail();
}
function renderList(){
  $('resultCount').textContent = `${filtered.length} / ${tasks.length} 条`;
  $('taskList').innerHTML = filtered.map(task => `<button class="task-row ${task.task_id===selectedId?'active':''}" data-id="${esc(task.task_id)}"><span class="count-box" title="真实执行调用数">${task.tool_calls.length}</span><span class="task-main"><span class="task-env">${esc(task.environment_id.replace('semiconductor_',''))}</span><span class="task-title">${esc(task.summary)}</span><span class="task-meta"><span>${task.profile.distinct_tools} 种工具</span><span>${task.state_changed?'改变状态':'只读'}</span></span></span><span class="depth-box">深度<b>${task.profile.max_dependency_depth}</b></span></button>`).join('') || '<div class="empty">没有符合条件的任务</div>';
  document.querySelectorAll('.task-row').forEach(row=>row.addEventListener('click',()=>{selectedId=row.dataset.id;renderList();renderDetail();}));
}
function resourceBlock(label, values){
  const normalized = (values||[]).map(value => typeof value==='string' ? value : `${value.scope_id||''}:${value.path||pretty(value)}`);
  return `<div><div class="resource-title">${esc(label)} (${normalized.length})</div><div class="resource-items">${normalized.length?normalized.map(esc).join('<br>'):'无'}</div></div>`;
}
function renderDetail(){
  const task = tasks.find(item=>item.task_id===selectedId);
  if(!task){$('detail').innerHTML='<div class="empty">没有可显示的任务</div>';return;}
  const p=task.profile, r=task.resources;
  $('detail').innerHTML = `<div class="detail-head"><div class="eyebrow">${esc(task.task_id)}</div><h2>${esc(task.summary)}</h2><div class="badges"><span class="badge">${task.tool_calls.length} 次调用</span><span class="badge blue">依赖深度 ${p.max_dependency_depth}</span><span class="badge amber">${task.state_changed?'改变环境状态':'只读任务'}</span>${task.difficulty_form_labels.map(label=>`<span class="badge">${esc(label)}</span>`).join('')}</div></div>
  <section class="section"><h3>复杂度结构</h3><div class="metric-strip"><div class="mini"><span>执行调用</span><b>${task.tool_calls.length}</b></div><div class="mini"><span>静态调用点</span><b>${p.tool_calls}</b></div><div class="mini"><span>依赖调用点</span><b>${p.dependent_tool_calls}</b></div><div class="mini"><span>最大依赖深度</span><b>${p.max_dependency_depth}</b></div><div class="mini"><span>业务判断</span><b>${p.business_decisions}</b></div></div></section>
  <section class="section"><h3>任务正文</h3><div class="prose">${esc(task.task_text)}</div></section>
  <section class="section"><h3>工具调用链</h3>${task.tool_calls.map((call,index)=>`<div class="call"><span class="call-index">${index+1}</span><span class="call-tool">${esc(call.tool)}</span><pre class="code">${esc(pretty(call.arguments))}</pre></div>`).join('')}</section>
  <section class="section"><h3>任务资源</h3><div class="resource-grid">${resourceBlock('记录集合',r.record_sets)}${resourceBlock('关系',r.relationships)}${resourceBlock('文件',r.files)}${resourceBlock('允许工具',r.allowed_tools)}</div></section>
  <section class="section"><div class="tabs"><button class="tab active" data-tab="answer">标准答案</button><button class="tab" data-tab="schema">输出 Schema</button><button class="tab" data-tab="solution">隐藏 Solution</button></div><div id="answer" class="tab-pane active"><pre class="code">${esc(pretty(task.answer))}</pre></div><div id="schema" class="tab-pane"><pre class="code">${esc(pretty(task.output_schema))}</pre></div><div id="solution" class="tab-pane"><pre class="code">${esc(task.solution_code)}</pre></div></section>
  <div class="footer-note">来源：${esc(task.source_run)}<br>clean replay：${task.clean_replay_count} 次。依赖深度为静态近似，不等同于调用总数。</div>`;
  document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>{document.querySelectorAll('.tab,.tab-pane').forEach(node=>node.classList.remove('active'));tab.classList.add('active');$(tab.dataset.tab).classList.add('active');}));
}
function init(){
  $('batchPath').textContent=stats.batch_directory;
  $('stamp').textContent=`生成时间 ${stats.generated_at}`;
  $('rating').textContent=stats.assessment.rating;
  $('assessmentText').textContent=stats.assessment.summary;
  $('definition').textContent=stats.dependency.definition;
  renderKpis();renderChart('callChart',stats.tool_calls.distribution);renderChart('depthChart',stats.dependency.max_depth_distribution,'depth');populateFilters();renderList();renderDetail();
  ['search','environment','calls'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',applyFilters));
}
init();
</script>
</body>
</html>'''


def render_report(batch_dir: Path, html_path: Path, stats_path: Path) -> None:
    attempts = _find_attempts(batch_dir)
    selected_runs, environment_status = _select_runs(attempts)
    tasks = _collect_tasks(selected_runs)
    if not tasks:
        raise RuntimeError(f"No accepted tasks found under {batch_dir}")
    stats = _build_stats(batch_dir, tasks, environment_status)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload = json.dumps({"stats": stats, "tasks": tasks}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__REPORT_DATA__", payload)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--stats", type=Path)
    args = parser.parse_args()
    batch_dir = args.batch_dir.resolve()
    html_path = args.html or batch_dir / "task_quality_report.html"
    stats_path = args.stats or batch_dir / "task_quality_stats.json"
    render_report(batch_dir, html_path.resolve(), stats_path.resolve())
    print(f"HTML: {html_path.resolve()}")
    print(f"Stats: {stats_path.resolve()}")


if __name__ == "__main__":
    main()
