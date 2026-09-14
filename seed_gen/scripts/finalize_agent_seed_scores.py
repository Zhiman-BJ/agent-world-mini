"""Audit completed Agent reviews, prepare targeted repair, and export a final report."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from seed_gen.catalog import DEFAULT_SEED_OUTPUT
from seed_gen.scripts.agent_score_mcp_seeds import export_screening, validate_result, write_json


def audit_records(seeds, records):
    by_id = {seed["global_id"]: seed for seed in seeds}
    errors = []
    if len(by_id) != len(seeds):
        errors.append({"global_id": None, "error": "duplicate source global_id"})
    for global_id, record in records.items():
        try:
            seed = by_id[global_id]
            checked = validate_result(dict(record), seed, strict_routing=True)
            info = seed["environment"]["basic_info"]
            for key, expected in {"global_id": global_id, "name": info["name"], "source_index": info["index"],
                                  "tool_count": len(seed["init_ref_tools"]), "total_score": checked["total_score"]}.items():
                if record.get(key) != expected:
                    raise ValueError(f"{key} differs from source/computed value")
            if record.get("runtime_verified") is not False:
                raise ValueError("static review cannot claim runtime verification")
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"global_id": global_id, "error": str(exc)})
    return errors, [global_id for global_id in by_id if global_id not in records]


def finalize(source: Path, output_dir: Path, *, prepare_repair=False):
    source_bytes = source.read_bytes()
    seeds = json.loads(source_bytes)
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    meta = json.loads((output_dir / "agent_scores.meta.json").read_text(encoding="utf-8"))
    if source_hash != meta["source_sha256"]:
        raise ValueError("source hash changed")
    records = json.loads((output_dir / "agent_scores.progress.json").read_text(encoding="utf-8"))
    errors, missing = audit_records(seeds, records)
    schema_path = Path(__file__).resolve().parents[2] / "schemas/validation/env_seeds.schema.json"
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")), format_checker=FormatChecker())
    source_errors = [f"{error.json_path}: {error.message}" for error in validator.iter_errors(seeds)]
    for seed in seeds:
        count = len(seed["init_ref_tools"])
        if count > 100 or seed["environment"]["nums"] != {"class": 0, "function": count, "class_func": 0, "all_func": count}:
            source_errors.append(f"{seed['global_id']}: invalid tool limit/counts")
    final_path = output_dir / "agent_scores.json"
    final_errors = []
    if not meta.get("finished_at"):
        final_errors.append("batch is still running or has not finalized")
    if not final_path.exists():
        final_errors.append("agent_scores.json is missing")
    else:
        final = json.loads(final_path.read_text(encoding="utf-8"))
        final_records = final.get("records", [])
        if len(final_records) != len(records) or {row["global_id"]: row for row in final_records} != records:
            final_errors.append("final records differ from checkpoint")
        if final.get("source_sha256") != source_hash:
            final_errors.append("final source hash differs")
        if final.get("requested") != len(seeds) or final.get("completed") != len(records):
            final_errors.append("final requested/completed counts differ")
        if final.get("decision_counts") != dict(Counter(row["decision"] for row in records.values())):
            final_errors.append("final decision counts differ")
        if final.get("failures"):
            final_errors.append("final result contains unresolved failures")
    audit = {"source_sha256": source_hash, "audited_at": datetime.now(timezone.utc).isoformat(),
             "requested": len(seeds), "completed": len(records), "missing_ids": missing,
             "record_errors": errors, "source_errors": source_errors, "final_errors": final_errors}
    audit["passed"] = not (errors or missing or source_errors or final_errors)
    write_json(output_dir / "final_audit.json", audit)
    if prepare_repair:
        if not meta.get("finished_at"):
            raise ValueError("wait for the scoring batch to finish before preparing repair")
        if source_errors or any(error["global_id"] not in records for error in errors):
            raise ValueError("source or identity errors cannot be repaired by rescoring")
        repair_ids = {error["global_id"] for error in errors}
        if repair_ids:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            write_json(output_dir / f"pre_repair_{stamp}.json", {"meta": meta, "records": records, "issues": errors})
            write_json(output_dir / "agent_scores.progress.json", {k: v for k, v in records.items() if k not in repair_ids})
        audit["prepared_repair_count"] = len(repair_ids)
    if not audit["passed"]:
        return audit
    export_screening(output_dir, seeds, records)
    export_errors = []
    for decision in sorted(set(record["decision"] for record in records.values())):
        expected = [seed for seed in seeds if records[seed["global_id"]]["decision"] == decision]
        actual = json.loads((output_dir / "screened" / f"{decision}.json").read_text(encoding="utf-8"))
        if actual != expected:
            export_errors.append(decision)
    if export_errors:
        audit["passed"] = False
        audit["export_errors"] = export_errors
        write_json(output_dir / "final_audit.json", audit)
        raise ValueError(f"export mismatch: {export_errors}")
    audit["exports_verified"] = True
    write_json(output_dir / "final_audit.json", audit)
    labels = {"priority_candidate": "优先候选", "limited_candidate": "有限候选", "specialized_candidate": "专项候选",
              "needs_tool_evidence": "待补工具", "reject": "淘汰"}
    counts = Counter(record["decision"] for record in records.values())
    lines = ["# Smithery MCP 种子 Agent 评分与筛选结果", "", f"全量完成：{len(records)}/{len(seeds)} 条。", "",
             f"源文件：`{source.as_posix()}`；SHA-256：`{source_hash}`。", "",
             f"有效工具 {sum(len(seed['init_ref_tools']) for seed in seeds)} 个，单环境最多 100 个。",
             f"其中 {sum(bool(seed['init_ref_tools']) for seed in seeds)} 条使用 Agent 静态审核，其余无工具条目由程序直接标记。", "",
             "| 分类 | 数量 | 占比 |", "|---|---:|---:|"]
    for decision, label in labels.items():
        lines.append(f"| {label} `{decision}` | {counts[decision]} | {counts[decision] / len(seeds):.1%} |")
    lines += ["", "## 优先候选示例", "", "以下按总分排序；并列按来源序号排序。高分只是合成潜力，不代表已有运行验收。", "",
              "| 环境 | 分数 | 工具数 |", "|---|---:|---:|"]
    priority = sorted((r for r in records.values() if r["decision"] == "priority_candidate"), key=lambda r: (-r["total_score"], r["source_index"]))
    for record in priority[:20]:
        lines.append(f"| {record['name']} | {record['total_score']} | {record['tool_count']} |")
    lines += ["", "## 交付文件", "", "- `agent_scores.json`：完整分数、理由、工具证据、任务链、依赖及待补项。",
              "- `screened/*.json`：按分类保留完整源种子，工具内容不改写。",
              "- `final_audit.json`：完整性、源 Schema、数量、分数和工具引用校验。",
              "- `agent_logs/`：逐次 Agent 提示词和原始响应；`pre_repair_*.json` 保留定向复核前的评分（如有）。", "",
              "## 解释与边界", "",
              "六维权重依次为常见性 10%、实体模型 20%、操作覆盖 15%、业务闭环 20%、可验证性 20%、多步任务 15%。",
              "审核材料是环境描述、工具描述摘要及输入输出字段名，并非完整工具实现；描述会截断、输入输出类型和约束未完整送入评分。",
              "空 output 也可能来自采集投影的丢失，不能断言上游没有返回值。数值是当前材料支持的暂定评分，证据不足见 missing_evidence。",
              "优先候选需总分 ≥80 且四项核心分 ≥3；65–79 分为有限候选，≥80 但核心分不足也只作有限候选；专项候选低于 65 分。",
              "分类与分数不一致的条目定向调用 Agent 复核；不靠程序改分来凑档位。待补工具不是淘汰。",
              "这些分数未经实际合成成功率校准；所有 runtime_verified=false。未执行 MCP 工具、真实业务操作或任务验收。", ""]
    (output_dir / "final_summary.md").write_text("\n".join(lines), encoding="utf-8")
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prepare-repair", action="store_true")
    args = parser.parse_args()
    result = finalize(args.source, args.output_dir, prepare_repair=args.prepare_repair)
    print(json.dumps({k: v for k, v in result.items() if k not in {"missing_ids", "record_errors"}}, ensure_ascii=False, indent=2))
    print(f"record_errors={len(result['record_errors'])} missing={len(result['missing_ids'])}")
    if not result["passed"] and not args.prepare_repair:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
