"""Offline seed evidence audit; semantic suitability scores require reviewed evidence.

Run from the repository root with ``python -m seed_gen.scripts.analyze_mcp_seeds``.
The source snapshot is read only. No network, model, or MCP tools are called.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from seed_gen.catalog import DEFAULT_SEED_OUTPUT, select_reference_tools
from utils.io import write_json


WEIGHTS = {
    "commonness": 10,
    "entity_model": 20,
    "operation_coverage": 15,
    "workflow_closure": 20,
    "verifiability": 20,
    "task_composability": 15,
}


def score_review(review: dict) -> int:
    dimensions = review["dimensions"]
    if set(dimensions) != set(WEIGHTS):
        raise ValueError("Review must score exactly the six documented dimensions")
    for value in dimensions.values():
        if type(value["score"]) is not int or not 0 <= value["score"] <= 5 or not value["reason"].strip():
            raise ValueError("Each reviewed dimension needs an integer 0..5 and a reason")
    return sum(WEIGHTS[key] * dimensions[key]["score"] for key in WEIGHTS) // 5


def profile_seed(seed: dict) -> dict:
    original = seed["init_ref_tools"]
    retained, selection = select_reference_tools(original)
    basic = seed["environment"]["basic_info"]
    counts = Counter(tool["name"] for tool in retained)
    output_count = sum(bool(tool.get("output")) for tool in retained)
    # Parameter names are evidence of fields, NOT an entity count.
    fields = sorted({key for tool in retained for key in tool.get("input", {})})
    route = "needs_tool_evidence" if not retained else "small_toolset_review" if len(retained) < 5 else "semantic_review"
    flags = []
    if selection["removed_count"]:
        flags.append("truncated_preview")
    if retained and not output_count:
        flags.append("output_contract_unknown")
    if any(count > 1 for count in counts.values()):
        flags.append("duplicate_tool_names")
    expected_counts = {"class": 0, "function": len(original), "class_func": 0, "all_func": len(original)}
    if seed["environment"].get("nums") != expected_counts:
        flags.append("source_count_mismatch")
    metadata = seed.get("others", {}).get("source_metadata", {})
    return {
        "global_id": seed["global_id"], "name": basic["name"], "url": basic["url"],
        "source_index": basic["index"], "server_use_count": metadata.get("use_count"),
        "original_tool_count": len(original), "tool_selection_preview": selection,
        "retained_tool_names": [t["name"] for t in retained],
        "unique_tool_name_count": len(counts),
        "duplicate_tool_names": {name: n for name, n in counts.items() if n > 1},
        "nonempty_input_count": sum(bool(t.get("input")) for t in retained),
        "nonempty_output_count": output_count,
        "nonempty_description_count": sum(bool(t.get("description", "").strip()) for t in retained),
        "distinct_input_field_names": fields,
        "reference_task_count": len(seed.get("init_ref_tasks", [])),
        "route": route, "flags": flags,
        "suitability_score": None, "assessment_status": "not_reviewed",
        "runtime_verified": False,
    }


def analyze(seeds: list[dict], reviews: list[dict] | None = None) -> dict:
    records = [profile_seed(s) for s in seeds]
    by_name = {r["name"]: r for r in records}
    if len(by_name) != len(records) or len({s["global_id"] for s in seeds}) != len(seeds):
        raise ValueError("Duplicate seed names or global IDs must be resolved before review")
    seen_reviews = set()
    for review in reviews or []:
        name = review["name"]
        if name not in by_name or name in seen_reviews:
            raise ValueError(f"Unknown or repeated review seed: {name}")
        seen_reviews.add(name)
        record = by_name[name]
        evidence_tools = set(review["evidence_tools"]) | set(review["proposed_task_chain"])
        if not evidence_tools.issubset(record["retained_tool_names"]):
            raise ValueError(f"Review cites tools outside the retained snapshot: {name}")
        record.update({"suitability_score": score_review(review),
                       "assessment_status": "static_review", "review": review})

    # Exact tool-definition equality is a duplicate lead, not proof of identical services.
    signatures = defaultdict(list)
    for seed in seeds:
        if seed["init_ref_tools"]:
            serialized = sorted(json.dumps(t, ensure_ascii=False, sort_keys=True) for t in seed["init_ref_tools"])
            signature = hashlib.sha256(json.dumps(serialized, ensure_ascii=False).encode("utf-8")).hexdigest()
            signatures[signature].append(seed["environment"]["basic_info"]["name"])
    tools = [t for s in seeds for t in s["init_ref_tools"]]
    routes = Counter(r["route"] for r in records)
    summary = {
        "environment_count": len(seeds), "original_tool_count": len(tools),
        "retained_tool_count_preview": sum(r["tool_selection_preview"]["retained_count"] for r in records),
        "over_limit_environment_count": sum(len(s["init_ref_tools"]) > 100 for s in seeds),
        "tool_count_distribution": dict(Counter(
            "0" if not s["init_ref_tools"] else "1_4" if len(s["init_ref_tools"]) < 5
            else "5_100" if len(s["init_ref_tools"]) <= 100 else "over_100" for s in seeds)),
        "original_nonempty_output_count": sum(bool(t.get("output")) for t in tools),
        "original_environments_with_output": sum(any(t.get("output") for t in s["init_ref_tools"]) for s in seeds),
        "original_tools_with_usage_field": sum("useCount" in t or "use_count" in t for t in tools),
        "environments_with_reference_tasks": sum(bool(s.get("init_ref_tasks")) for s in seeds),
        "source_count_mismatch_count": sum("source_count_mismatch" in r["flags"] for r in records),
        "routes": dict(routes), "static_review_count": len(seen_reviews), "runtime_verified_count": 0,
    }
    return {"analysis_version": "1.0", "weights": WEIGHTS, "summary": summary,
            "identical_toolset_groups": [names for names in signatures.values() if len(names) > 1],
            "records": records}


def markdown_report(result: dict) -> str:
    s = result["summary"]
    lines = ["# MCP 种子静态筛选分析", "", f"来源：`{result['source_file']}`。",
             f"SHA-256：`{result['source_sha256']}`。", "",
             "本报告只分析本地快照；未调用 MCP、外部 API 或 LLM。评分为人工静态判断，未经运行验收。",
             "原始数据未修改；超限记录按爬取选择器模拟保留至多 100 个工具。所有结构指标及样例评分均基于保留后的工具。",
             "总量和标有 original 的指标使用原始快照。参数名数量不等于实体数量，非空 output 不等于有效验证器。", "",
             "## 全量材料审计", "", "| 指标 | 数量 |", "| --- | ---: |",
             f"| 环境 | {s['environment_count']} |", f"| 原始工具 | {s['original_tool_count']} |",
             f"| 0 工具，待补采 | {s['tool_count_distribution'].get('0', 0)} |",
             f"| 1–4 工具，专项复核 | {s['tool_count_distribution'].get('1_4', 0)} |",
             f"| 5–100 工具 | {s['tool_count_distribution'].get('5_100', 0)} |",
             f"| 超过 100 工具 | {s['over_limit_environment_count']} |",
             f"| 限制后工具数（预览） | {s['retained_tool_count_preview']} |",
             f"| 原始非空 output 工具数 | {s['original_nonempty_output_count']} |",
             f"| 原始存在非空 output 的环境数 | {s['original_environments_with_output']} |",
             f"| 原始含工具级使用次数字段的工具数 | {s['original_tools_with_usage_field']} |",
             f"| 有参考任务的环境数 | {s['environments_with_reference_tasks']} |",
             f"| 原始 environment.nums 不一致 | {s['source_count_mismatch_count']} |", "",
             "空工具仅表示当前快照没有材料，不能确定是服务无工具还是采集未成功。服务级 use_count 不能替代工具级调用量。",
             "现有爬取投影只保留 inputSchema/outputSchema.properties；空 output 也可能来自非 object 输出或投影信息丢失，不能直接认定上游没有输出契约。", "",
             "## 静态评分样例", "", "维度顺序：常见性 / 实体属性 / 操作覆盖 / 业务闭环 / 可验证性 / 多步任务。",
             "权重依次为 10 / 20 / 15 / 20 / 20 / 15；各项 0–5 分，加权总分 0–100。",
             "这是一组覆盖不同类型的定性样例，不是全量排名，也未用于自动淘汰其余环境。", "",
             "| 环境 | 原始→保留工具 | 六项分数 | 总分 | 建议 |", "| --- | ---: | --- | ---: | --- |"]
    reviewed = sorted(
        (r for r in result["records"] if r["assessment_status"] == "static_review"), key=lambda r: -r["suitability_score"])
    for r in reviewed:
        review = r["review"]
        scores = " / ".join(str(review["dimensions"][key]["score"]) for key in WEIGHTS)
        lines.append(f"| {r['name']} | {r['original_tool_count']}→{r['tool_selection_preview']['retained_count']} | {scores} | {r['suitability_score']} | {review['recommendation']} |")
    for r in reviewed:
        review = r["review"]
        lines.extend(["", f"### {r['name']}", "", f"来源：{r['url'][0]}", "",
                      f"实体线索：{review['entity_evidence']}", ""])
        for key in WEIGHTS:
            item = review["dimensions"][key]
            lines.append(f"- `{key}`：{item['score']}/5。{item['reason']}")
        lines.extend(["", "拟合成任务链（尚未执行）：`" + " → ".join(review["proposed_task_chain"]) + "`。",
                      "", f"依赖关系：{review['chain_dependencies']}", "",
                      f"拟验证断言：{review['proposed_assertion']}", "",
                      f"待补信息：{review['missing_evidence']}"])
    lines.extend(["", "## 超限环境", "", "以下只展示截取影响，未覆盖原文件。", "",
                  "| 环境 | 原始工具数 | 保留数 | 策略 |", "| --- | ---: | ---: | --- |"])
    for r in result["records"]:
        selection = r["tool_selection_preview"]
        if selection["removed_count"]:
            lines.append(f"| {r['name']} | {r['original_tool_count']} | {selection['retained_count']} | {selection['strategy']} |")
    lines.extend(["", "## 工具定义完全相同的服务组", "",
                  "仅按完整参考工具定义匹配，不自动合并；复核来源、版本和业务对象后再决定是否去重。", ""])
    for group in result["identical_toolset_groups"]:
        lines.append("- " + "、".join(f"`{name}`" for name in group))
    command = "python -m seed_gen.scripts.analyze_mcp_seeds"
    if result.get("reviews_file"):
        command += " --reviews " + result["reviews_file"]
    lines.extend(["", "## 复现", "", "```powershell", command,
                  "```", "", "完整维度锚点、门槛和验收要求见 `seed_gen/MCP种子筛选与评分-v1.md`。", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--reviews", type=Path, help="optional reviewed scores; defaults to no semantic scores")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/smithery_seed_screening_20260911"))
    args = parser.parse_args()
    raw = args.source.read_bytes()
    seeds = json.loads(raw)
    reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else []
    result = analyze(seeds, reviews)
    result.update({"source_file": args.source.as_posix(), "source_sha256": hashlib.sha256(raw).hexdigest()})
    if args.reviews:
        result["reviews_file"] = args.reviews.as_posix()
        result["reviews_sha256"] = hashlib.sha256(args.reviews.read_bytes()).hexdigest()
    write_json(args.output_dir / "analysis.json", result)
    (args.output_dir / "analysis.md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
