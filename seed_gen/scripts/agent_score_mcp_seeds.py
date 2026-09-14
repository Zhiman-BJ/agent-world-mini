"""Use a read-only Codex Agent to score and route MCP environment seeds.

The deterministic audit remains the source of counts and tool names. The Agent
only supplies semantic evidence: six rubric scores, a candidate task chain, and
a routing decision. Results are checkpointed so a long run can be resumed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import threading
import time
from typing import Any

from utils.io import extract_json_object
from utils.search_agent.codex import CodexAgentClient

from seed_gen.catalog import DEFAULT_SEED_OUTPUT, select_reference_tools


DIMENSIONS = {
    "commonness": 10,
    "entity_model": 20,
    "operation_coverage": 15,
    "workflow_closure": 20,
    "verifiability": 20,
    "task_composability": 15,
}
DECISIONS = {"priority_candidate", "limited_candidate", "specialized_candidate", "needs_tool_evidence", "reject"}
MAX_TOOL_DESCRIPTION = 600
MAX_ENV_DESCRIPTION = 1200


def write_json(path: Path, value: Any) -> None:
    """Replace a complete checkpoint atomically so readers never see partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            # Windows readers and antivirus may briefly prevent replacement.
            # Keep the previous complete checkpoint until the retry succeeds.
            if attempt == 19:
                raise
            time.sleep(0.1 * min(attempt + 1, 5))


def recover_logs(output_dir: Path, seeds: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    """Recover only valid responses whose logged material matches the current prompt."""
    by_id = {seed["global_id"]: seed for seed in seeds}
    recovered, invalid = [], []
    for last_message in sorted((output_dir / "agent_logs").glob("run_*/last_message.txt")):
        if not last_message.stat().st_size:
            continue
        try:
            log = last_message.with_name("stderr.log").read_text(encoding="utf-8")
            match = re.search(r"<seed>\s*(.*?)\s*</seed>", log, re.S)
            if match is None:
                raise ValueError("missing source prompt")
            payload = json.loads(match[1])
            seed = by_id[payload["global_id"]]
            expected = json.loads(build_prompt(seed).split("<seed>\n", 1)[1].rsplit("\n</seed>", 1)[0])
            if payload != expected:
                raise ValueError("logged prompt does not match current seed")
            review = validate_result(extract_json_object(last_message.read_text(encoding="utf-8")), seed)
            global_id = seed["global_id"]
            if global_id not in existing:
                existing[global_id] = {"global_id": global_id, "name": seed["environment"]["basic_info"]["name"],
                    "source_index": seed["environment"]["basic_info"]["index"],
                    "tool_count": len(seed.get("init_ref_tools", [])), **review}
                recovered.append({"global_id": global_id, "log": last_message.as_posix()})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            invalid.append({"log": last_message.as_posix(), "error": str(exc)})
    report = {"recovered_count": len(recovered), "recovered": recovered, "invalid_logs": invalid}
    write_json(output_dir / "recovery_report.json", report)
    return report


def export_screening(output_dir: Path, seeds: list[dict[str, Any]], records: dict[str, Any]) -> None:
    """Export source records by decision without changing their reference tools."""
    counts = {}
    for decision in sorted(DECISIONS):
        selected = [seed for seed in seeds if records.get(seed["global_id"], {}).get("decision") == decision]
        counts[decision] = len(selected)
        write_json(output_dir / "screened" / f"{decision}.json", selected)
    pending = [seed["global_id"] for seed in seeds if seed["global_id"] not in records]
    write_json(output_dir / "screened" / "pending_ids.json", pending)
    lines = ["# MCP 种子 Agent 筛选进度", "", f"更新时间（UTC）：{datetime.now(timezone.utc).isoformat()}", "",
        f"已完成 {len(records)}/{len(seeds)} 条；待完成 {len(pending)} 条。", "",
        "所有分数均为静态证据评估，尚未实际运行工具验证。分类文件只包含已完成项；未评分项不视为淘汰。", "",
        "| 分类 | 数量 |", "|---|---:|"]
    lines.extend(f"| {decision} | {count} |" for decision, count in counts.items())
    lines.extend(["", "分类种子保存在 `screened/`；逐条分数、工具证据、任务链和缺口见 `agent_scores.progress.json`。", ""])
    (output_dir / "screening_summary.md").write_text("\n".join(lines), encoding="utf-8")


def _tool_view(tool: dict[str, Any]) -> dict[str, Any]:
    description = str(tool.get("description") or "")
    input_value = tool.get("input")
    output_value = tool.get("output")
    return {
        "name": tool.get("name"),
        "type": tool.get("type"),
        "description": description[:MAX_TOOL_DESCRIPTION],
        "input_fields": sorted(input_value) if isinstance(input_value, dict) else input_value,
        "output_fields": sorted(output_value) if isinstance(output_value, dict) else output_value,
    }


def build_prompt(seed: dict[str, Any]) -> str:
    environment = seed["environment"]
    tools = seed.get("init_ref_tools", [])
    payload = {
        "global_id": seed["global_id"],
        "name": environment["basic_info"]["name"],
        "url": environment["basic_info"]["url"],
        "description": str(environment.get("description") or "")[:MAX_ENV_DESCRIPTION],
        "domain": environment.get("domain"),
        "tool_count": len(tools),
        "tools": [_tool_view(tool) for tool in tools],
    }
    return f"""你是 MCP 环境种子审核 Agent。只审核下面给出的一个种子，不访问网络、不读取文件、不调用工具，不补造来源中没有的能力。

目标是判断它是否适合合成：实体和属性明确、工具操作覆盖、完整业务闭环、可验证终态、多步任务依赖。参数名数量不等于实体数量；相似的 list/search 工具不能重复加分；实时外部 API、空 output、缺少状态和输出契约都要写入缺口。若工具集合被截断，只评价当前给出的集合。

按六个维度各打 0–5 分：commonness(常见性与迁移性)、entity_model(实体与属性模型)、operation_coverage(工具操作覆盖)、workflow_closure(环境业务闭环)、verifiability(可验证性)、task_composability(多步任务组合性)。权重为 10/20/15/20/20/15，总分是权重乘分数除以 5，四舍五入到整数。

必须只输出一个 JSON object，不要 Markdown。格式必须是：
{{
  "decision": "priority_candidate|limited_candidate|specialized_candidate|needs_tool_evidence|reject",
  "dimensions": {{"commonness": {{"score": 0, "reason": ""}}, "entity_model": {{"score": 0, "reason": ""}}, "operation_coverage": {{"score": 0, "reason": ""}}, "workflow_closure": {{"score": 0, "reason": ""}}, "verifiability": {{"score": 0, "reason": ""}}, "task_composability": {{"score": 0, "reason": ""}}}},
  "entity_evidence": "引用工具参数或描述中的实体及属性线索；没有则写无",
  "evidence_tools": ["必须来自给定 tools 的 name"],
  "proposed_task_chain": ["必须来自给定 tools 的 name，按调用顺序；没有可信链则为空数组"],
  "chain_dependencies": "逐条说明参数/状态如何从前一步到后一步；未知不得假设",
  "proposed_assertion": "用读回、最终状态、独立公式或不变量描述客观验收条件",
  "missing_evidence": "运行前仍需补齐的输出、状态、数据、权限、时钟或隔离信息",
  "recommendation": "一句处置建议"
}}

评分规则：总分 >=80 且四个核心维度 entity_model/workflow_closure/verifiability/task_composability 均 >=3 才能 priority_candidate；65–79 为 limited_candidate；低于 65 但仍有明确只读/计算价值为 specialized_candidate；没有工具为 needs_tool_evidence；只有有证据确认与目标完全不匹配才 reject。未知要降低分数并在 missing_evidence 中说明，不能声称已运行验证。以下是唯一审核材料：
<seed>
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
</seed>"""


def validate_result(value: Any, seed: dict[str, Any], *, strict_routing: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Agent response must be a JSON object")
    if value.get("decision") not in DECISIONS:
        raise ValueError("invalid decision")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise ValueError("dimensions must contain exactly the six rubric keys")
    for key in DIMENSIONS:
        item = dimensions[key]
        if not isinstance(item, dict) or type(item.get("score")) is not int or not 0 <= item["score"] <= 5:
            raise ValueError(f"invalid score for {key}")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError(f"missing reason for {key}")
    for key in ("entity_evidence", "chain_dependencies", "proposed_assertion", "missing_evidence", "recommendation"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"missing {key}")
    tool_names = {tool.get("name") for tool in seed.get("init_ref_tools", [])}
    for key in ("evidence_tools", "proposed_task_chain"):
        if not isinstance(value.get(key), list) or any(item not in tool_names for item in value[key]):
            raise ValueError(f"{key} contains a tool outside the seed")
    total = round(sum(DIMENSIONS[key] * dimensions[key]["score"] / 5 for key in DIMENSIONS))
    core = min(dimensions[key]["score"] for key in ("entity_model", "workflow_closure", "verifiability", "task_composability"))
    decision = value["decision"]
    if not seed.get("init_ref_tools") and decision != "needs_tool_evidence":
        raise ValueError("empty tool seed must be needs_tool_evidence")
    if decision == "priority_candidate" and not (total >= 80 and core >= 3):
        raise ValueError("priority_candidate violates score threshold")
    if strict_routing:
        if seed.get("init_ref_tools") and decision == "needs_tool_evidence":
            raise ValueError("needs_tool_evidence is reserved for seeds without tools")
        if seed.get("init_ref_tools") and not value["evidence_tools"]:
            raise ValueError("nonempty seed requires evidence_tools")
        if decision == "limited_candidate" and not (65 <= total < 80 or total >= 80 and core < 3):
            raise ValueError("limited_candidate requires 65-79, or >=80 with a core score below 3")
        if decision == "specialized_candidate" and total >= 65:
            raise ValueError("specialized_candidate requires total_score < 65; reconcile scores with decision")
        if decision == "priority_candidate" and len(set(value["proposed_task_chain"])) < 2:
            raise ValueError("priority_candidate requires a chain of at least two distinct tools")
    value["total_score"] = total
    value["score_version"] = "mcp_seed_rubric_v1"
    value["assessment_status"] = "agent_static_review"
    value["runtime_verified"] = False
    return value


def _agent_review(seed: dict[str, Any], client: CodexAgentClient, workspace: Path, retries: int,
                  strict_routing: bool = False) -> dict[str, Any]:
    if not seed.get("init_ref_tools"):
        return {"decision": "needs_tool_evidence", "dimensions": {key: {"score": 0, "reason": "当前快照没有有效参考工具，不能判断语义适用性。"} for key in DIMENSIONS},
                "entity_evidence": "无", "evidence_tools": [], "proposed_task_chain": [],
                "chain_dependencies": "没有工具材料，无法建立调用依赖。", "proposed_assertion": "补齐工具后再定义终态断言。",
                "missing_evidence": "详情工具列表、输入输出契约和可验证的初始数据。", "recommendation": "进入补采队列。",
                "total_score": 0, "score_version": "mcp_seed_rubric_v1", "assessment_status": "deterministic_missing_tools", "runtime_verified": False}
    last_error = None
    for attempt in range(retries + 1):
        try:
            prompt = build_prompt(seed)
            if strict_routing:
                prompt += "\n最终校验要求：有工具必须引用 evidence_tools；优先候选至少包含两个不同工具的真实依赖链。总分 >=80 但核心维度不足 3 时可为 limited_candidate；specialized_candidate 总分必须低于 65。不得为满足分类而无证据抬高或压低分数。"
            if last_error:
                prompt += "\n上一响应未通过校验，请重审并返回完整 JSON。校验错误：" + last_error[:1500]
            text = client.run(prompt, working_directory=workspace)
            return validate_result(extract_json_object(text), seed, strict_routing=strict_routing)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(last_error or "Agent review failed")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_bytes = args.source.read_bytes()
    seeds = json.loads(source_bytes)
    if not isinstance(seeds, list):
        raise ValueError("source must be a seed array")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "agent_scores.json"
    progress_path = output_dir / "agent_scores.progress.json"
    existing: dict[str, Any] = {}
    if args.resume and progress_path.is_file():
        existing = json.loads(progress_path.read_text(encoding="utf-8"))
        meta_path = output_dir / "agent_scores.meta.json"
        if meta_path.is_file():
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
            if previous.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest():
                raise ValueError("source changed since checkpoint; use a new output directory")
    selected = seeds[: args.limit] if args.limit else seeds
    if getattr(args, "recover_logs", False):
        if not args.resume:
            raise ValueError("--recover-logs requires --resume")
        recover_logs(output_dir, selected, existing)
    work = [seed for seed in selected if seed["global_id"] not in existing]
    config = {"source": args.source.as_posix(), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "started_at": datetime.now(timezone.utc).isoformat(), "model": args.model,
              "max_concurrency": args.workers, "requested": len(selected), "completed": len(existing),
              "failures": []}
    write_json(progress_path, existing)
    write_json(output_dir / "agent_scores.meta.json", config)
    export_screening(output_dir, seeds, existing)
    if getattr(args, "recover_only", False):
        return {"completed": len(existing), "remaining": len(work)}
    lock = threading.Lock()
    client_args = {"model": args.model, "timeout_seconds": args.timeout_seconds, "sandbox": "read-only",
                   "enable_web_search": False, "network_access": False, "reasoning_effort": args.reasoning_effort,
                   "log_directory": output_dir / "agent_logs"}

    def one(seed: dict[str, Any]):
        # Empty records are handled deterministically; avoid starting an Agent
        # process for a case whose score is necessarily needs_tool_evidence.
        client = CodexAgentClient(**client_args) if seed.get("init_ref_tools") else None
        return seed["global_id"], _agent_review(seed, client, output_dir, args.retries,
                                               strict_routing=getattr(args, "strict_routing", False))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, seed): seed for seed in work}
        for index, future in enumerate(as_completed(futures), start=1):
            seed = futures[future]
            try:
                global_id, review = future.result()
                with lock:
                    existing[global_id] = {"global_id": global_id, "name": seed["environment"]["basic_info"]["name"],
                                           "source_index": seed["environment"]["basic_info"]["index"], "tool_count": len(seed.get("init_ref_tools", [])), **review}
            except Exception as exc:
                config["failures"].append({"global_id": seed["global_id"], "error": f"{type(exc).__name__}: {exc}"})
            config["completed"] = len(existing)
            if index % args.checkpoint_every == 0:
                write_json(progress_path, existing)
                write_json(output_dir / "agent_scores.meta.json", config)
                if index % 10 == 0:
                    export_screening(output_dir, seeds, existing)
            print(f"[agent-score] {index}/{len(work)} completed={len(existing)} {seed['global_id']}", flush=True)
    write_json(progress_path, existing)
    records = list(existing.values())
    counts: dict[str, int] = {}
    for record in records:
        counts[record["decision"]] = counts.get(record["decision"], 0) + 1
    output = {"analysis_version": "agent_mcp_seed_scoring_v1", "source_file": args.source.as_posix(),
              "source_sha256": config["source_sha256"], "model": args.model,
              "rubric_weights": DIMENSIONS, "requested": len(selected), "completed": len(records),
              "failures": config["failures"], "decision_counts": counts,
              "records": sorted(records, key=lambda item: item["source_index"])}
    write_json(result_path, output)
    write_json(output_dir / "agent_scores.meta.json", {**config, "finished_at": datetime.now(timezone.utc).isoformat()})
    export_screening(output_dir, seeds, existing)
    return {key: output[key] for key in ("requested", "completed", "failures", "decision_counts", "source_sha256")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/smithery_agent_scoring_20260911"))
    parser.add_argument("--limit", type=int, default=0, help="number of seeds to review; 0 means all")
    parser.add_argument("--workers", type=int, default=1, help="parallel Agent sessions; start with 1")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"], default="medium")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--strict-routing", action="store_true", help="also enforce limited/specialized thresholds and task-chain evidence")
    parser.add_argument("--recover-logs", action="store_true", help="restore validated responses missing from checkpoint")
    parser.add_argument("--recover-only", action="store_true", help="recover/export without any Agent calls; requires --resume --recover-logs")
    args = parser.parse_args()
    if args.recover_only and not (args.resume and args.recover_logs):
        parser.error("--recover-only requires --resume --recover-logs")
    if args.limit < 0 or args.workers < 1 or args.retries < 0 or args.checkpoint_every < 1 or args.timeout_seconds < 1:
        parser.error("limit must be nonnegative; workers/checkpoint/timeout must be positive; retries must be nonnegative")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
