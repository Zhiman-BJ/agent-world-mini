"""Validate a tools-only recovery against its original backup and summarize runs."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.catalog import DEFAULT_SEED_OUTPUT, _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--report-dir", type=Path, default=Path("reports/smithery_tools_retry_20260911"))
    args = parser.parse_args()
    paths = sorted(args.report_dir.glob("*/report.json"))
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if not runs or any(r["status"] == "running" for r in runs):
        raise ValueError("Recovery runs must be finished or explicitly interrupted before validation")
    baseline = json.loads(Path(runs[0]["backup"]).read_text(encoding="utf-8"))
    current = json.loads(args.source.read_text(encoding="utf-8"))
    schema_path = Path(__file__).resolve().parents[2] / "schemas/validation/env_seeds.schema.json"
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")), format_checker=FormatChecker())
    validator.validate(current)
    assert len(current) == len(baseline)
    recovered = []
    unchanged_populated = 0
    for old, new in zip(baseline, current):
        assert old["global_id"] == new["global_id"]
        if old["init_ref_tools"] or not new["init_ref_tools"]:
            assert old == new, f"Unexpected modification: {old['global_id']}"
            unchanged_populated += bool(old["init_ref_tools"])
            continue
        assert 1 <= len(new["init_ref_tools"]) <= 100
        assert new["environment"]["nums"] == {"class": 0, "class_func": 0,
               "function": len(new["init_ref_tools"]), "all_func": len(new["init_ref_tools"])}
        allowed = deepcopy(new)
        allowed["init_ref_tools"] = old["init_ref_tools"]
        allowed["environment"]["nums"] = old["environment"]["nums"]
        for field in ("tool_selection", "tools_refresh"):
            if field in old["others"]:
                allowed["others"][field] = old["others"][field]
            else:
                allowed["others"].pop(field, None)
        assert allowed == old, f"Unexpected metadata modification: {old['global_id']}"
        assert Path(new["others"]["tools_refresh"]["raw_detail"]).is_file()
        recovered.append(new)
    # Generated JSON uses explicit LF, avoiding a full-file CRLF diff on Windows.
    _atomic_json(args.source, current)
    latest = {item["name"]: item for run in runs for item in run["records"]}
    unresolved = []
    for seed in current:
        if not seed["init_ref_tools"]:
            name = seed["environment"]["basic_info"]["name"]
            item = latest[name]
            last = item["events"][-1]
            reason = "invalid_tools" if last.get("received_tools", 0) and not last.get("valid_tools", 0) else str(last.get("http_status", last["status"]))
            unresolved.append({"name": name, "url": seed["environment"]["basic_info"]["url"], "reason": reason,
                               "events": item["events"]})
    summary = {
        "source": str(args.source), "baseline_backup": runs[0]["backup"],
        "baseline_sha256": runs[0]["source_sha256"],
        "output_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "environment_count": len(current), "initial_empty": sum(not s["init_ref_tools"] for s in baseline),
        "recovered": len(recovered), "remaining_empty": len(unresolved),
        "added_tools": sum(len(s["init_ref_tools"]) for s in recovered),
        "total_tools": sum(len(s["init_ref_tools"]) for s in current),
        "unchanged_populated": unchanged_populated, "schema_errors": 0,
        "moodtrip_tool_count": next(len(s["init_ref_tools"]) for s in current if s["environment"]["basic_info"]["name"] == "moodtrip/moodtrip-hotel-search"),
        "latest_unresolved_outcomes": dict(Counter(r["reason"] for r in unresolved)),
        "unresolved": unresolved, "runs": [str(p) for p in paths],
    }
    _atomic_json(args.report_dir / "summary.json", summary)
    lines = ["# Smithery 空工具补爬结果", "",
             "已对原快照中工具为 0 的记录补爬；详情请求包含空响应重试、指数退避和共享限流等待。", "",
             "| 指标 | 数量 |", "| --- | ---: |"]
    for key in ("environment_count", "initial_empty", "recovered", "remaining_empty", "added_tools", "total_tools", "unchanged_populated", "moodtrip_tool_count", "schema_errors"):
        lines.append(f"| {key} | {summary[key]} |")
    lines += ["", f"原始备份：`{summary['baseline_backup']}`。", "",
              f"结果 SHA-256：`{summary['output_sha256']}`。", "",
              "已有非空种子和最终未补回的种子均与备份逐项相等。补回记录仅修改工具、计数及采集审计字段，单条最多 100 个；身份、描述与原快照版本保留。",
              "新工具的实际采集时间见 others.tools_refresh.fetched_at，完整来源详情保存于各次运行 details/。",
              "没有调用 LLM 或执行任何 MCP 工具；数据结构校验不代表参考工具已经运行验证。", "",
              "## 仍为空的记录", "", "这些记录本次未获得有效工具，不代表网站一定没有工具。", "",
              "| 服务 | 最后尝试结果 | 次数 |", "| --- | --- | ---: |"]
    for item in unresolved:
        last = item["events"][-1]
        lines.append(f"| {item['name']} | {item['reason']} | {len(item['events'])} |")
    lines += ["", "## 复现命令", "", "```powershell",
              "python -m seed_gen.catalog --retry-empty --workers 2 --attempts 5 --retry-delay 2 --request-interval 1",
              "python -m seed_gen.scripts.validate_smithery_tools_retry", "```", ""]
    (args.report_dir / "summary.md").write_bytes("\n".join(lines).encode("utf-8"))
    print(json.dumps({k: v for k, v in summary.items() if k not in {"unresolved", "runs"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
