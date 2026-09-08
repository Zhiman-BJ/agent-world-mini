"""Process-mining event-log downloader and analyser.

The analyser accepts CSV (case id, timestamp, activity, optional attributes)
and XES files.  It emits normalized trajectories and a compact HTML dashboard.
The default download is deliberately a metadata/source manifest; pass a direct
file URL (for example a 4TU/Zenodo export) to download a real snapshot.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, sys, urllib.request, zipfile, tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

BPI2019_DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
BPI2019_LANDING = "https://data.4tu.nl/articles/_/12715853/1"
BPI2019_DOWNLOAD = "https://data.4tu.nl/ndownloader/items/35ed7122-966a-484e-a0e1-749b64e3366d/versions/1"

def download(url: str, destination: Path) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-world-mini-process-mining/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r: data = r.read()
    destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
    return {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "downloaded_at": datetime.now(timezone.utc).isoformat()}

def _events(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as td:
            for name in z.namelist():
                if name.lower().endswith((".csv", ".xes")):
                    target = Path(td) / Path(name).name
                    target.write_bytes(z.read(name))
                    yield from _events(target)
        return
    if path.suffix.lower() == ".xes":
        for _, trace in ET.iterparse(path, events=("end",)):
            if trace.tag.rsplit("}", 1)[-1] != "trace": continue
            attrs = {x.attrib.get("key"): x.attrib.get("value") for x in trace if x.tag.rsplit("}", 1)[-1] in {"string", "date", "int", "float"}}
            case = attrs.get("concept:name") or attrs.get("case_id") or "unknown"
            for ev in trace:
                if ev.tag.rsplit("}", 1)[-1] != "event": continue
                row = {x.attrib.get("key"): x.attrib.get("value") for x in ev}
                row.update({k: v for k, v in attrs.items() if k not in row})
                yield {"case_id": case, "timestamp": row.get("time:timestamp", ""), "activity": row.get("concept:name", ""), "attributes": row}
            trace.clear()
    else:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = csv.DictReader(f)
            fields = rows.fieldnames or []
            def pick(names):
                for n in names:
                    for k in fields:
                        if k.lower().replace(" ", "_") == n: return k
                return None
            ck, tk, ak = pick(["case_id", "caseid", "case"]), pick(["timestamp", "time", "date"]), pick(["activity", "event", "concept:name", "event_name"])
            if not (ck and tk and ak): raise ValueError("CSV must contain case_id, timestamp and activity columns")
            for r in rows:
                yield {"case_id": r.get(ck, ""), "timestamp": r.get(tk, ""), "activity": r.get(ak, ""), "attributes": dict(r)}

def analyse(path: Path, output_dir: Path, dataset="BPI Challenge 2019") -> dict:
    cases = defaultdict(list); activities = Counter(); workers = Counter(); attrs = Counter()
    for e in _events(path):
        cases[str(e["case_id"])].append(e); activities[e["activity"]] += 1
        a = e.get("attributes", {})
        for k, v in a.items():
            if k and k.lower().replace(" ", "_") not in {"case_id", "caseid", "case", "concept:name", "time:timestamp", "activity", "event", "timestamp", "time", "date"} and v not in (None, ""): attrs[k] += 1
        for k in ("worker", "org:resource", "user", "employee"):
            if a.get(k): workers[str(a[k])] += 1; break
    trajectories=[]; verifiable=0
    for cid, evs in cases.items():
        evs.sort(key=lambda x: x.get("timestamp", "")); acts=[e["activity"] for e in evs]
        ok = len(evs)>0 and all(e.get("timestamp") and e.get("activity") for e in evs)
        # Prefix/suffix framing mirrors process-mining task construction: the
        # observed prefix is the initial state and the suffix is the target plan.
        cut = max(1, len(evs)//2)
        verifiable += bool(ok); trajectories.append({"task_id": cid, "initial_state": {"event_count": cut, "last_activity": acts[cut-1]}, "goal_state": {"event_count": len(evs), "last_activity": acts[-1]}, "prefix": acts[:cut], "suffix": acts[cut:], "actions": acts, "verifiable": bool(ok)})
    summary={"dataset":dataset,"source_doi":BPI2019_DOI,"source_url":BPI2019_LANDING,"license":"CC BY 4.0 (verify dataset terms before redistribution)","environments":[{"name":"BPI 2019 Purchase-to-Pay","case_key":"case_id","event_log":str(path)}],"entities":["Company","Vendor","PurchaseOrder","PurchaseOrderItem","GoodsReceipt","Invoice","InvoiceItem","Payment","Employee","SpendCategory","DocumentType","PaymentBlock"],"events":sum(activities.values()),"cases":len(cases),"activities":len(activities),"workers":len(workers),"attribute_fields":len(attrs),"top_activities":activities.most_common(20),"entity_attributes":sorted(attrs),"tools_methods":[{"name":a,"event_count":n} for a,n in activities.most_common()],"verifiable_cases":verifiable,"verifiable_case_rate":round(verifiable/len(cases),4) if cases else 0,"task_count":len(trajectories)}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir/"tasks.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in trajectories),encoding="utf-8")
    (output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    _html(output_dir/"overview.html", summary)
    return summary

def _html(path: Path, s: dict):
    metrics="".join(f"<div><b>{(f'{s[k]*100:.1f}%' if k.endswith('rate') else s[k])}</b><span>{label}</span></div>" for k,label in [("cases","业务 case"),("events","事件"),("activities","工具/方法"),("attribute_fields","属性字段"),("verifiable_case_rate","可验证率")])
    bars="".join(f"<p>{a}<i style='width:{n/max((x[1] for x in s['top_activities']),default=1)*100:.1f}%'></i>{n}</p>" for a,n in s['top_activities'])
    path.write_text(f"<!doctype html><meta charset='utf-8'><title>Process Mining 数据概览</title><style>body{{font-family:system-ui,'Microsoft YaHei';max-width:960px;margin:30px auto;padding:0 18px;color:#243047}}.m{{display:flex;gap:10px;flex-wrap:wrap}}.m div{{border:1px solid #ccd5e0;padding:12px;min-width:110px;border-radius:8px}}b{{display:block;font-size:24px}}span{{color:#667085;font-size:13px}}p{{display:flex;gap:8px;align-items:center}}i{{height:12px;background:#2563eb;display:inline-block;min-width:2px}}</style><h1>Process Mining / BPI Challenge 2019</h1><p>真实企业采购到付款事件日志；事件序列可直接映射为 agent trajectory。来源：<a href='{s['source_url']}'>{s['source_doi']}</a></p><section class='m'>{metrics}</section><h2>高频活动（工具/方法候选）</h2>{bars}<h2>建模提示</h2><p>实体可从属性字段映射（PO、供应商、发票、收货、员工等）；每个 case 的完整事件序列作为任务轨迹，前缀/后缀切分生成初始态与目标态。可验证率是结构完整性指标，不代表业务成功率。</p>",encoding="utf-8")

def main(argv=None):
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    d=sub.add_parser('download'); d.add_argument('--url',default=BPI2019_DOWNLOAD); d.add_argument('--destination',type=Path,default=Path('seed_raw/process_mining/BPI_Challenge_2019_1_all.zip'))
    a=sub.add_parser('analyze'); a.add_argument('input',type=Path); a.add_argument('--output-dir',type=Path,default=Path('reports/process_mining'))
    x=p.parse_args(argv)
    if x.cmd=='download':
        info=download(x.url,x.destination); (x.destination.parent/'download_manifest.json').write_text(json.dumps({'dataset':'BPI Challenge 2019','source_doi':BPI2019_DOI,**info},ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(info,ensure_ascii=False))
    else: print(json.dumps(analyse(x.input,x.output_dir),ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': sys.exit(main())
