"""Download and analyse Google's Schema-Guided Dialogue (SGD) dataset.

The default command is intentionally small (one dev shard) so that a smoke
run is cheap.  Use ``download --max-files 0 --splits train dev test`` for the
complete snapshot, then run ``analyze``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


API_ROOT = "https://api.github.com/repos/google-research-datasets/dstc8-schema-guided-dialogue/contents"
USER_AGENT = "agent-world-mini-sgd-analysis/1.0"


def _json_url(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _download(url: str, destination: Path) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    destination.write_bytes(data)
    return {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def download_dataset(raw_dir: Path, splits: list[str], max_files: int) -> dict[str, Any]:
    """Download schema and dialogue shards, returning a manifest."""
    previous: dict[tuple[str, str], str] = {}
    previous_records: dict[tuple[str, str], dict[str, Any]] = {}
    manifest_path = raw_dir / "download_manifest.json"
    if manifest_path.exists():
        try:
            previous_records = {(f["split"], f["name"]): f for f in json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])}
            previous = {key: record["sha256"] for key, record in previous_records.items()}
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            previous = {}
    manifest: dict[str, Any] = {
        "dataset": "Schema-Guided Dialogue (SGD)",
        "repository": "https://github.com/google-research-datasets/dstc8-schema-guided-dialogue",
        "license": "CC BY-SA 4.0",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": list(previous_records.values()),
    }
    for split in splits:
        if split not in {"train", "dev", "test", "sgd_x"}:
            raise ValueError(f"unsupported split: {split}")
        entries = _json_url(f"{API_ROOT}/{split}")
        files = [e for e in entries if e.get("type") == "file" and e.get("name") == "schema.json"]
        files += [e for e in entries if e.get("type") == "file" and e.get("name", "").startswith("dialogues_")]
        dialogue_files = [e for e in files if e["name"].startswith("dialogues_")]
        if max_files > 0:
            dialogue_files = dialogue_files[:max_files]
        selected = files[:1] + dialogue_files
        split_dir = raw_dir / split
        for entry in selected:
            target = split_dir / entry["name"]
            # The API's ``sha`` is a Git blob SHA, not a SHA-256 digest.
            # Reuse our manifest digest for idempotent reruns instead.
            if target.exists() and previous.get((split, entry["name"])):
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest == previous[(split, entry["name"])]:
                    record = {"split": split, "name": entry["name"], "bytes": target.stat().st_size, "sha256": digest, "url": entry.get("download_url")}
                    manifest["files"] = [f for f in manifest["files"] if (f.get("split"), f.get("name")) != (split, entry["name"])] + [record]
                    continue
            info = _download(entry["download_url"], target)
            info.update({"split": split, "name": entry["name"]})
            manifest["files"] = [f for f in manifest["files"] if (f.get("split"), f.get("name")) != (split, entry["name"])] + [info]
    manifest["file_count"] = len(manifest["files"])
    manifest["dialogue_shards"] = sum(f["name"].startswith("dialogues_") for f in manifest["files"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "download_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _iter_dialogues(raw_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    for path in sorted(raw_dir.glob("*/dialogues_*.json")):
        split = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"expected dialogue list: {path}")
        for dialogue in payload:
            yield split, dialogue


def _iter_frames(dialogue: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for turn in dialogue.get("turns", []):
        for frame in turn.get("frames", []):
            yield frame


def analyse_dataset(raw_dir: Path, output_dir: Path, visualization_path: Path | None = None) -> dict[str, Any]:
    """Create normalized task records and machine-readable aggregate stats."""
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(raw_dir.glob("*/schema.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for service in payload:
            schemas[service["service_name"]] = service

    service_stats: dict[str, dict[str, Any]] = {}
    intent_stats: list[dict[str, Any]] = []
    categorical_values = 0
    for service_name, service in sorted(schemas.items()):
        slots = service.get("slots", [])
        intents = service.get("intents", [])
        categorical = [s for s in slots if s.get("is_categorical")]
        categorical_values += sum(len(s.get("possible_values", [])) for s in categorical)
        service_stats[service_name] = {"service": service_name, "description": service.get("description", ""), "slots": len(slots), "slot_names": [s.get("name") for s in slots], "categorical_slots": len(categorical), "categorical_slot_names": [s.get("name") for s in categorical], "intents": len(intents), "transactional_intents": sum(bool(i.get("is_transactional")) for i in intents), "methods": [i.get("name") for i in intents]}
        for intent in intents:
            intent_stats.append({"service": service_name, "intent": intent.get("name"), "transactional": bool(intent.get("is_transactional")), "required_slots": len(intent.get("required_slots", [])), "optional_slots": len(intent.get("optional_slots", {})), "result_slots": len(intent.get("result_slots", [])), "required_slot_names": intent.get("required_slots", []), "result_slot_names": intent.get("result_slots", [])})

    dialogue_count = 0
    turn_count = 0
    call_count = 0
    result_count = 0
    success_count = 0
    service_dialogues: Counter[str] = Counter()
    intent_dialogues: Counter[tuple[str, str]] = Counter()
    user_tasks: list[dict[str, Any]] = []
    for split, dialogue in _iter_dialogues(raw_dir):
        dialogue_count += 1
        turns = dialogue.get("turns", [])
        turn_count += len(turns)
        services = dialogue.get("services", [])
        service_dialogues.update(services)
        intents: set[str] = set()
        frame_intents: set[tuple[str, str]] = set()
        calls: list[dict[str, Any]] = []
        requested: set[str] = set()
        for frame in _iter_frames(dialogue):
            state = frame.get("state", {})
            active = state.get("active_intent")
            if active and active != "NONE":
                intents.add(active)
                if frame.get("service"):
                    frame_intents.add((frame["service"], active))
            requested.update(state.get("requested_slots", []))
            call = frame.get("service_call")
            if call:
                call_count += 1
                results = frame.get("service_results") or []
                result_count += bool(results)
                calls.append({"method": call.get("method"), "parameters": call.get("parameters", {}), "result_count": len(results), "has_result": bool(results)})
            acts = {a.get("act") for a in frame.get("actions", [])}
            if "NOTIFY_SUCCESS" in acts:
                success_count += 1
        for service, intent in frame_intents:
            intent_dialogues[(service, intent)] += 1
        user_tasks.append({"task_id": dialogue.get("dialogue_id"), "split": split, "services": services, "intents": sorted(intents), "turns": len(turns), "user_turns": sum(t.get("speaker") == "USER" for t in turns), "requested_slots": sorted(requested), "service_calls": calls, "verifiable": bool(calls and all(c["has_result"] for c in calls)), "utterances": [t.get("utterance", "") for t in turns if t.get("speaker") == "USER"]})

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tasks.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in user_tasks) + ("\n" if user_tasks else ""), encoding="utf-8")
    with (output_dir / "service_stats.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["service", "description", "slots", "slot_names", "categorical_slots", "categorical_slot_names", "intents", "transactional_intents", "methods"])
        writer.writeheader()
        for row in service_stats.values():
            writer.writerow({**row, "slot_names": "|".join(row["slot_names"]), "categorical_slot_names": "|".join(row["categorical_slot_names"]), "methods": "|".join(row["methods"])})
    with (output_dir / "intent_stats.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["service", "intent", "transactional", "required_slots", "optional_slots", "result_slots"])
        writer.writeheader()
        writer.writerows({k: row[k] for k in writer.fieldnames} for row in intent_stats)

    summary = {"dataset": "Schema-Guided Dialogue (SGD)", "license": "CC BY-SA 4.0", "raw_dir": str(raw_dir), "environments": len(schemas), "services": len(schemas), "slots": sum(len(s.get("slots", [])) for s in schemas.values()), "categorical_slots": sum(sum(bool(x.get("is_categorical")) for x in s.get("slots", [])) for s in schemas.values()), "categorical_possible_values": categorical_values, "intents": len(intent_stats), "transactional_intents": sum(x["transactional"] for x in intent_stats), "dialogues": dialogue_count, "turns": turn_count, "service_calls": call_count, "calls_with_results": result_count, "successful_calls": success_count, "call_result_rate": round(result_count / call_count, 4) if call_count else 0, "verifiable_dialogue_rate": round(sum(t["verifiable"] for t in user_tasks) / dialogue_count, 4) if dialogue_count else 0, "service_dialogues": service_dialogues, "intent_dialogues": {f"{s}:{i}": n for (s, i), n in sorted(intent_dialogues.items())}, "top_services": service_dialogues.most_common(15), "service_catalog": [{"service": r["service"], "entity_attributes": r["slot_names"], "tools": r["methods"], "transactional_tools": r["transactional_intents"]} for r in service_stats.values()]}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_html(visualization_path or (raw_dir.parent / "sgd_overview.html"), summary, user_tasks)
    return summary


def _select_examples(tasks: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """Pick deterministic, diverse examples, including a multi-call trajectory."""
    selected: list[dict[str, Any]] = []
    predicates = [
        lambda t: len(t.get("service_calls", [])) >= 2 and bool(t.get("verifiable")) and not any(str(s).startswith(prefix) for prefix in ("Restaurants_", "Flights_", "Weather_") for s in t.get("services", [])),
        lambda t: any(str(s).startswith("Restaurants_") for s in t.get("services", [])),
        lambda t: any(str(s).startswith("Flights_") for s in t.get("services", [])),
        lambda t: any(str(s).startswith("Weather_") for s in t.get("services", [])),
    ]
    for predicate in predicates:
        match = next((task for task in tasks if predicate(task) and task.get("task_id") not in {x.get("task_id") for x in selected}), None)
        if match:
            selected.append(match)
    if len(selected) < limit:
        for task in tasks:
            if task.get("task_id") not in {x.get("task_id") for x in selected}:
                selected.append(task)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _example_html(task: dict[str, Any], index: int) -> str:
    services = ", ".join(str(s) for s in task.get("services", [])) or "未标注服务"
    intents = ", ".join(str(i) for i in task.get("intents", [])) or "未标注意图"
    utterances = task.get("utterances", [])[:2]
    user_text = " / ".join(str(u) for u in utterances) or "（无用户话语）"
    status = "可验证" if task.get("verifiable") else "结果不完整"
    calls = []
    for call in task.get("service_calls", []):
        params = json.dumps(call.get("parameters", {}), ensure_ascii=False, indent=2)
        calls.append(f'<li><div class="call-title"><code>{html.escape(str(call.get("method", "")))}</code><span>{"有结果" if call.get("has_result") else "无结果"} · {call.get("result_count", 0)} 条</span></div><pre>{html.escape(params)}</pre></li>')
    return f'<article class="case"><div class="case-head"><h3>案例 {index} · {html.escape(services)}</h3><span class="status">{status}</span></div><p class="case-task"><b>用户任务：</b>{html.escape(user_text)}</p><p class="case-meta">意图：{html.escape(intents)} · {task.get("turns", 0)} 轮 · task_id：{html.escape(str(task.get("task_id", "")))}</p><h4>工具轨迹</h4><ol class="calls">{"".join(calls) or "<li>未记录 service_call</li>"}</ol></article>'


def _write_html(path: Path, summary: dict[str, Any], tasks: list[dict[str, Any]] | None = None) -> None:
    top = summary.get("top_services", [])
    max_count = max((n for _, n in top), default=1)
    bars = "".join(f'<div class="row"><span>{name}</span><div class="bar" style="width:{n / max_count * 100:.1f}%"></div><b>{n}</b></div>' for name, n in top)
    metrics = "".join(f'<div><strong>{(f"{summary[k] * 100:.1f}%" if k.endswith("rate") else summary[k])}</strong><span>{label}</span></div>' for k, label in [("environments", "候选环境（服务）"), ("slots", "实体属性（槽位）"), ("intents", "工具/方法"), ("dialogues", "任务/对话"), ("service_calls", "服务调用"), ("verifiable_dialogue_rate", "可验证率")])
    catalog = "".join(f'<tr><td>{row["service"]}</td><td>{len(row["entity_attributes"])}</td><td>{"、".join(row["tools"])}</td><td>{row["transactional_tools"]}</td></tr>' for row in summary.get("service_catalog", []))
    examples = "".join(_example_html(task, index) for index, task in enumerate(_select_examples(tasks or []), 1))
    html = f'''<!doctype html><meta charset="utf-8"><title>SGD 数据概览</title><style>body{{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#1f2937}}h1{{margin-bottom:4px}}.muted{{color:#6b7280}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:24px 0}}.metrics div{{border:1px solid #d1d5db;border-radius:8px;padding:14px}}strong{{display:block;font-size:25px}}.metrics span{{color:#6b7280;font-size:13px}}.row{{display:grid;grid-template-columns:150px 1fr 48px;gap:10px;align-items:center;margin:8px 0;font-size:14px}}.bar{{height:14px;background:#2563eb;border-radius:3px;min-width:2px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid #e5e7eb;text-align:left;padding:7px;vertical-align:top}}.case{{border:1px solid #d1d5db;border-radius:8px;padding:16px;margin:14px 0}}.case-head{{display:flex;justify-content:space-between;gap:12px;align-items:baseline}}.case h3{{margin:0}}.case h4{{margin:14px 0 6px;font-size:14px}}.case-task{{line-height:1.55}}.case-meta{{color:#6b7280;font-size:13px}}.status{{font-size:13px;color:#047857;white-space:nowrap}}.calls{{margin:0;padding-left:24px}}.calls li{{padding:5px 0}}.call-title{{display:flex;justify-content:space-between;gap:12px;font-size:14px}}.call-title span{{color:#6b7280;font-size:12px}}pre{{background:#f3f4f6;padding:9px;overflow:auto;font-size:12px;margin:5px 0 0;white-space:pre-wrap}}@media(max-width:500px){{.row{{grid-template-columns:110px 1fr 38px;font-size:12px}}table{{font-size:12px}}th,td{{padding:5px}}.case{{padding:12px}}.case-head{{display:block}}.status{{display:block;margin-top:5px}}.call-title{{display:block}}.call-title span{{display:block;margin-top:3px}}}}</style><h1>Google Schema-Guided Dialogue</h1><p class="muted">从 seed_raw 读取的 schema、任务和可验证性概览（CC BY-SA 4.0）</p><section class="metrics">{metrics}</section><h2>服务 → 实体属性 → 工具/方法</h2><table><thead><tr><th>服务/环境</th><th>属性数</th><th>方法</th><th>事务方法数</th></tr></thead><tbody>{catalog}</tbody></table><h2>案例示例</h2><p class="muted">以下案例直接来自真实对话记录，用于展示“自然语言任务 → 工具调用 → 服务结果”的转换方式。</p>{examples}<h2>对话覆盖最多的服务（Top 15）</h2>{bars}<p class="muted">调用结果覆盖率：{summary["call_result_rate"] * 100:.1f}%。 “可验证”定义为该对话的每个 service_call 都带有至少一条 service_results；这是离线结构指标，不等同于真实 API 成功率。</p>'''
    path.write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    down = sub.add_parser("download", help="download raw SGD files")
    down.add_argument("--raw-dir", type=Path, default=Path("seed_raw/sgd"))
    down.add_argument("--splits", nargs="+", default=["dev"], choices=["train", "dev", "test", "sgd_x"])
    down.add_argument("--max-files", type=int, default=1, help="dialogue shards per split; 0 means all")
    ana = sub.add_parser("analyze", help="aggregate schema/dialogue statistics")
    ana.add_argument("--raw-dir", type=Path, default=Path("seed_raw/sgd"))
    ana.add_argument("--output-dir", type=Path, default=Path("reports/sgd_full"))
    ana.add_argument("--visualization-path", type=Path, default=None, help="HTML output; defaults to seed_raw/sgd_overview.html for the standard layout")
    args = parser.parse_args(argv)
    if args.command == "download":
        manifest = download_dataset(args.raw_dir, args.splits, args.max_files)
        print(json.dumps({"raw_dir": str(args.raw_dir), "file_count": manifest["file_count"], "dialogue_shards": manifest["dialogue_shards"]}, ensure_ascii=False))
    else:
        summary = analyse_dataset(args.raw_dir, args.output_dir, args.visualization_path)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
