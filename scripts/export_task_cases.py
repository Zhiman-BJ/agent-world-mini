"""Build a readable task-case package from the three Luna production batches."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BATCH_NAMES = (
    "luna-batch-30-fresh",
    "luna-batch-40-fresh",
    "luna-batch-50-fresh",
)

CATEGORIES = {
    "01_生命科学与健康": {
        "description": "药物、医学术语、生物活性、蛋白质和食品安全等真实数据上的检索与关联任务。",
        "environments": (
            "58-medical-terminologies-mcp",
            "11-pharma-regulatory",
            "12-chemical-safety",
            "14-food-safety",
            "chembl-bioactivity",
            "ncbi-gene",
            "uniprot-proteins",
            "pubchem-compounds",
        ),
    },
    "02_科研文献与知识检索": {
        "description": "从论文、图书、研究数据和技术知识库中定位实体，再沿作者、主题、版本或来源关系继续查询。",
        "environments": (
            "07-microsoft-learn",
            "08-hugging-face",
            "25-mrc-research-data",
            "crossref",
            "europe-pmc-publications",
            "open-library-books",
            "openalex",
            "07-tavily",
        ),
    },
    "03_航天航空与地球环境": {
        "description": "航天项目、系外行星、飞行、气候、地震、水文和地理位置等数据上的多步查询。",
        "environments": (
            "nasa-open",
            "nasa-exoplanet-archive",
            "noaa-climate",
            "usgs-earthquakes",
            "usgs-water-data",
            "opensky-flights",
            "26-thinair-geo",
            "59-recreation-gov",
        ),
    },
    "04_经济金融与公共政务": {
        "description": "宏观经济、统计指标、议会、灾害和政府文件中的查询、比较和来源追溯任务。",
        "environments": (
            "01-australian-economic-data",
            "02-brazil-central-bank",
            "03-ibge-brasil",
            "04-brazilian-senate",
            "ecb-economic-data",
            "fred",
            "fema-openfema-disasters",
            "federal-register-documents",
        ),
    },
    "05_软件包与开发者生态": {
        "description": "围绕软件包、版本、依赖、仓库、维护者和产品服务进行跨实体追踪。",
        "environments": (
            "08-supabase",
            "10-paypal",
            "20-grafbase",
            "21-reportflow-mcp",
            "cratesio-packages",
            "pypi-packages",
            "npm-packages",
            "maven-central-artifacts",
        ),
    },
    "06_工业商业与公共服务": {
        "description": "公司、商品、劳动力、风险、设备、招投标、交易市场和车辆安全等现实业务数据。",
        "environments": (
            "15-global-company-intelligence",
            "19-primary-source-commodities",
            "21-risk-models",
            "28-career-labor-market",
            "29-industrial-crane-discovery",
            "30-public-tender-intelligence",
            "mcp-paradex-py",
            "nhtsa-vehicle-safety",
        ),
    },
}

BOUNDARY_CASES = {
    "arxiv-publications": "答案重复拼接了大量工具字段，事实可追溯，但不适合直接作为优质 SFT 示范。",
    "clinicaltrials-gov-studies": "任务和调用链有价值，但部分答案过长且重复，需要重新生成更自然的最终回答。",
    "gwas-catalog": "多步关系能执行，但最终答案存在机械复述，训练前应做表达质量筛选。",
    "gitlab-public-projects": "工具调用成功，答案却混入大量内部标识符，用户可读性不足。",
    "musicbrainz-recordings": "证据链完整但答案冗长重复，说明当前通过判分不等于表达质量合格。",
    "openfoodfacts-products": "真实数据可用，但答案字段堆叠明显，需要更好的答案生成或示范选择。",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def json_text(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def collect_urls(value: Any) -> list[str]:
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {"url", "source_url"} and isinstance(child, str) and child.startswith("http"):
                    found.append(child)
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))


def answer_text(trajectory: dict[str, Any]) -> str:
    return json_text(trajectory.get("final_answer", {}))


def trajectory_score(trajectory: dict[str, Any]) -> float:
    """Prefer complete, readable runs instead of simply preferring fewer calls."""
    text = answer_text(trajectory)
    lowered = text.lower()
    length = len(text)
    steps = int(trajectory.get("chain_steps") or 0)
    calls = trajectory.get("calls") or []
    unique_tools = len({call.get("tool") for call in calls if call.get("tool")})
    required = trajectory.get("required_answer_sections") or []
    answer = trajectory.get("final_answer")
    answer_fields = len(answer) if isinstance(answer, dict) else 1

    score = min(steps, 11) * 2 + min(unique_tools, 8) * 2
    score += 18 if 120 <= length <= 2200 else 0
    score += 8 if isinstance(answer, dict) and answer_fields >= len(required) else 0
    score += 6 if any(mark in text for mark in (". ", "; ", ": ")) else 0
    score -= max(0, 90 - length) * 0.4
    score -= max(0, length - 3500) * 0.015
    score -= lowered.count("observed results") * 35
    score -= lowered.count("observed records") * 18
    score -= max(0, lowered.count("entity_id") - 6) * 1.5
    score -= max(0, lowered.count("entity id") - 3) * 2
    if calls and len(calls) * 2 < max(steps, 1):
        score -= 45
    if "\\\"entity_id\\\"" in text and len(text) > 300:
        score -= 10
    return score


def compact_value(value: Any, limit: int = 260) -> str:
    text = json_text(value)
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def quality_label(trajectory: dict[str, Any], boundary_reason: str | None) -> tuple[str, str]:
    if boundary_reason:
        return "边界案例", boundary_reason

    text = answer_text(trajectory).lower()
    steps = int(trajectory.get("chain_steps") or 0)
    calls = len(trajectory.get("calls") or [])
    issues: list[str] = []
    if "observed results" in text or "observed records" in text:
        issues.append("最终答案有机械拼接痕迹")
    if len(text) < 100:
        issues.append("最终答案偏短，需人工确认覆盖是否完整")
    if len(text) > 3000:
        issues.append("最终答案偏长，训练前适合再做简化")
    if text.count("entity_id") + text.count("entity id") > 8:
        issues.append("内部标识符偏多")
    if calls and calls * 2 < max(steps, 1):
        issues.append("实际调用远少于参考链，当前判分可能高估了答案覆盖")
    if "\\\"entity_id\\\"" in text and len(text) > 300:
        issues.append("答案更像序列化记录，缺少面向用户的归纳")

    if issues:
        return "可用但建议复核", "；".join(issues) + "。"
    if steps >= 7 and calls >= 7:
        return "较强案例", "任务需要多次检索和关系跳转，成功轨迹的回答也较完整。"
    return "正常案例", "任务可以执行并有真实证据支撑，复杂度属于该环境自然产生的水平。"


def chain_text(calls: list[dict[str, Any]]) -> str:
    return " -> ".join(call.get("tool", "unknown") for call in calls)


def load_batches(root: Path) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, Any],
]:
    trajectories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    environments: dict[str, dict[str, Any]] = {}
    tasks: dict[tuple[str, str], dict[str, Any]] = {}
    corpus_stats: Counter[str] = Counter()
    batch_summaries: list[dict[str, Any]] = []

    for batch_name in BATCH_NAMES:
        batch_dir = root / "runs" / batch_name
        dataset = read_json(batch_dir / "luna_final_dataset.json")
        stats = dataset.get("stats", {})
        batch_summaries.append({"batch": batch_name, **stats})
        for field in (
            "environments_completed",
            "real_records",
            "sampled_walks",
            "executable_unique_candidate_chains",
            "tasks_passed",
            "tasks_rejected",
            "successful_trajectories",
        ):
            corpus_stats[field] += int(stats.get(field) or 0)

        for environment in dataset.get("environments", []):
            item = dict(environment)
            item["batch"] = batch_name
            environments[item["environment_id"]] = item
        for task in dataset.get("tasks", []):
            tasks[(task["environment_id"], task["task_id"])] = task
        for trajectory in read_jsonl(batch_dir / "luna_successful_trajectories.jsonl"):
            trajectory["batch"] = batch_name
            trajectories[trajectory["environment_id"]].append(trajectory)

    return trajectories, environments, tasks, {
        "totals": dict(corpus_stats),
        "batches": batch_summaries,
    }


def select_case(
    environment_id: str,
    category: str,
    trajectories: dict[str, list[dict[str, Any]]],
    environments: dict[str, dict[str, Any]],
    tasks: dict[tuple[str, str], dict[str, Any]],
    boundary_reason: str | None = None,
    preferred_task_type: str | None = None,
) -> dict[str, Any]:
    candidates = trajectories.get(environment_id, [])
    if not candidates:
        raise RuntimeError(f"No successful trajectory found for {environment_id}")

    if preferred_task_type and not boundary_reason:
        preferred_candidates = []
        for candidate in candidates:
            candidate_task = tasks.get((environment_id, candidate["task_id"]), {})
            candidate_validation = candidate_task.get("validation") or {}
            is_composition = bool(candidate_validation.get("composition_source_task_ids"))
            candidate_type = "related_composition" if is_composition else "natural_graph_walk"
            if candidate_type == preferred_task_type:
                preferred_candidates.append(candidate)
        if preferred_candidates:
            best_preferred = max(preferred_candidates, key=trajectory_score)
            best_overall = max(candidates, key=trajectory_score)
            if trajectory_score(best_preferred) >= trajectory_score(best_overall) - 8:
                candidates = preferred_candidates

    if boundary_reason:
        chosen = max(
            candidates,
            key=lambda row: (
                answer_text(row).lower().count("observed results")
                + answer_text(row).lower().count("observed records"),
                len(answer_text(row)),
            ),
        )
    else:
        chosen = max(candidates, key=trajectory_score)

    task = tasks.get((environment_id, chosen["task_id"]), {})
    environment = environments.get(environment_id, {})
    validation = task.get("validation") or {}
    composition_sources = validation.get("composition_source_task_ids") or []
    bundle = environment.get("research_bundle") or {}
    source_urls = collect_urls(bundle.get("sources") or [])
    source_urls.extend(collect_urls(chosen.get("reference_answer")))
    source_urls.extend(collect_urls(chosen.get("calls")))
    source_urls = list(dict.fromkeys(source_urls))[:8]
    label, note = quality_label(chosen, boundary_reason)

    return {
        "case_id": "",
        "category": category,
        "is_boundary_case": bool(boundary_reason),
        "environment": {
            "environment_id": environment_id,
            "theme": environment.get("theme", environment_id),
            "batch": chosen["batch"],
            "source_name": environment.get("source_name", ""),
            "research_adapter": bundle.get("adapter", ""),
            "source_urls": source_urls,
        },
        "task": {
            "task_id": chosen["task_id"],
            "request": chosen.get("request", ""),
            "reference_steps": int(chosen.get("chain_steps") or 0),
            "task_type": "related_composition" if composition_sources else "natural_graph_walk",
            "composition_source_task_ids": composition_sources,
            "required_answer_sections": chosen.get("required_answer_sections") or [],
        },
        "available_tools": chosen.get("available_tools") or [],
        "reference_calls": chosen.get("reference_calls") or [],
        "reference_answer": chosen.get("reference_answer") or {},
        "successful_trajectory": {
            "trajectory_id": chosen.get("trajectory_id"),
            "tool_call_count": len(chosen.get("calls") or []),
            "calls": chosen.get("calls") or [],
            "final_answer": chosen.get("final_answer") or {},
            "judgment": chosen.get("judgment") or {},
        },
        "quality_review": {
            "label": label,
            "note": note,
            "selection_score": round(trajectory_score(chosen), 2),
        },
    }


def category_markdown(category: str, description: str, cases: list[dict[str, Any]]) -> str:
    lines = [f"# {category.split('_', 1)[1]}", "", description, ""]
    for case in cases:
        env = case["environment"]
        task = case["task"]
        trajectory = case["successful_trajectory"]
        review = case["quality_review"]
        lines.extend(
            [
                f"## {case['case_id']} · {env['theme']}",
                "",
                f"- **这是什么：** `{env['environment_id']}` 环境中的一条已通过 5-run 的任务。",
                f"- **来自哪批：** `{env['batch']}`；来源标识 `{env['source_name'] or '未标注'}`。",
                f"- **任务类型：** `{'相关任务组合' if task['task_type'] == 'related_composition' else '自然图游走'}`。",
                f"- **链长：** 参考链 {task['reference_steps']} 步；Luna 成功执行 {trajectory['tool_call_count']} 次工具调用。",
                f"- **质量判断：** {review['label']}。{review['note']}",
                "",
                "### 用户任务（原文）",
                "",
                task["request"],
                "",
                "### 可用工具",
                "",
                ", ".join(f"`{tool.get('name')}`" for tool in case["available_tools"]),
                "",
                "### 参考工具链",
                "",
                f"`{chain_text(case['reference_calls'])}`",
                "",
                "### Luna 实际成功工具链",
                "",
                f"`{chain_text(trajectory['calls'])}`",
                "",
                "### 每步返回摘要",
                "",
            ]
        )
        for index, call in enumerate(trajectory["calls"], start=1):
            lines.append(
                f"{index}. `{call.get('tool')}` 参数 `{compact_value(call.get('arguments', {}), 160)}`；"
                f"返回 `{compact_value(call.get('result'), 260)}`"
            )
        lines.extend(
            [
                "",
                "### 最终答案（原始输出）",
                "",
                "```json",
                json_text(trajectory["final_answer"], pretty=True),
                "```",
                "",
                "### 数据来源",
                "",
            ]
        )
        if env["source_urls"]:
            lines.extend(f"- {url}" for url in env["source_urls"])
        else:
            lines.append("- 原环境包未提取到可直接展示的 URL，请在完整 JSONL 的证据记录中复核。")
        lines.append("")
    return "\n".join(lines)


def write_html_viewer(
    root: Path,
    output_dir: Path,
    cases: list[dict[str, Any]],
    overview: dict[str, Any],
) -> None:
    template = (root / "scripts" / "task_case_viewer.html").read_text(encoding="utf-8")
    case_data = json_text(cases).replace("</", "<\\/")
    overview_data = json_text(overview).replace("</", "<\\/")
    html = template.replace("__CASE_DATA__", case_data).replace(
        "__OVERVIEW_DATA__", overview_data
    )
    (output_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")


def write_package(root: Path, output_dir: Path) -> dict[str, Any]:
    trajectories, environments, tasks, corpus = load_batches(root)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    categories_dir = output_dir / "categories"
    categories_dir.mkdir(parents=True)

    selected: list[dict[str, Any]] = []
    category_descriptions: dict[str, str] = {}
    for category, config in CATEGORIES.items():
        category_descriptions[category] = str(config["description"])
        for index, environment_id in enumerate(config["environments"]):
            preferred_task_type = (
                "natural_graph_walk" if index % 2 == 0 else "related_composition"
            )
            selected.append(
                select_case(
                    environment_id,
                    category,
                    trajectories,
                    environments,
                    tasks,
                    preferred_task_type=preferred_task_type,
                )
            )

    boundary_category = "07_边界案例"
    category_descriptions[boundary_category] = (
        "这些任务能执行且通过当前判分，但最终答案存在明显表达问题。它们用于暴露缺陷，不应直接当作最佳训练示范。"
    )
    for environment_id, reason in BOUNDARY_CASES.items():
        selected.append(
            select_case(
                environment_id,
                boundary_category,
                trajectories,
                environments,
                tasks,
                boundary_reason=reason,
            )
        )

    counters: Counter[str] = Counter()
    for case in selected:
        counters[case["category"]] += 1
        prefix = case["category"].split("_", 1)[0]
        case["case_id"] = f"C{prefix}-{counters[case['category']]:02d}"

    with (output_dir / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for case in selected:
            handle.write(json_text(case) + "\n")

    with (output_dir / "case_index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "case_id",
                "category",
                "environment_id",
                "theme",
                "batch",
                "task_id",
                "task_type",
                "reference_steps",
                "successful_tool_calls",
                "quality_label",
                "request",
            ),
        )
        writer.writeheader()
        for case in selected:
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "category": case["category"].split("_", 1)[1],
                    "environment_id": case["environment"]["environment_id"],
                    "theme": case["environment"]["theme"],
                    "batch": case["environment"]["batch"],
                    "task_id": case["task"]["task_id"],
                    "task_type": case["task"]["task_type"],
                    "reference_steps": case["task"]["reference_steps"],
                    "successful_tool_calls": case["successful_trajectory"]["tool_call_count"],
                    "quality_label": case["quality_review"]["label"],
                    "request": case["task"]["request"],
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in selected:
        grouped[case["category"]].append(case)
    for category, cases in grouped.items():
        (categories_dir / f"{category}.md").write_text(
            category_markdown(category, category_descriptions[category], cases),
            encoding="utf-8",
            newline="\n",
        )

    main_cases = [case for case in selected if not case["is_boundary_case"]]
    reference_steps = [case["task"]["reference_steps"] for case in main_cases]
    tool_calls = [case["successful_trajectory"]["tool_call_count"] for case in main_cases]
    quality_counts = Counter(case["quality_review"]["label"] for case in selected)
    selected_by_batch: dict[str, dict[str, int]] = {}
    for batch_name in BATCH_NAMES:
        batch_cases = [
            case
            for case in main_cases
            if case["environment"]["batch"] == batch_name
        ]
        selected_by_batch[batch_name] = {
            "cases": len(batch_cases),
            "strong": sum(case["quality_review"]["label"] == "较强案例" for case in batch_cases),
            "normal": sum(case["quality_review"]["label"] == "正常案例" for case in batch_cases),
            "needs_review": sum(
                case["quality_review"]["label"] == "可用但建议复核" for case in batch_cases
            ),
        }
    overview = {
        "corpus": corpus,
        "package": {
            "main_cases": len(main_cases),
            "boundary_cases": len(selected) - len(main_cases),
            "categories": {key: len(value) for key, value in grouped.items()},
            "average_reference_steps": round(sum(reference_steps) / len(reference_steps), 2),
            "average_successful_tool_calls": round(sum(tool_calls) / len(tool_calls), 2),
            "longest_reference_chain": max(reference_steps),
            "longest_successful_trajectory": max(tool_calls),
            "reference_step_distribution": dict(sorted(Counter(reference_steps).items())),
            "quality_labels": dict(quality_counts),
            "selected_by_batch": selected_by_batch,
            "composed_cases": sum(
                case["task"]["task_type"] == "related_composition" for case in main_cases
            ),
        },
    }
    (output_dir / "quality_overview.json").write_text(
        json_text(overview, pretty=True) + "\n", encoding="utf-8", newline="\n"
    )
    write_html_viewer(root, output_dir, selected, overview)

    category_lines = "\n".join(
        f"- **{category.split('_', 1)[1]}：** {description}"
        for category, description in category_descriptions.items()
        if category != boundary_category
    )
    newest = selected_by_batch["luna-batch-50-fresh"]
    readme = f"""# Agent-World 任务案例包

这个包从三批 Luna 最终数据中整理了 **{len(main_cases)} 个正文案例**和 **{len(selected) - len(main_cases)} 个边界案例**。正文按领域分类，边界案例则用于展示当前判分会放过什么问题。

## 先看结论

- 三批原始结果共有 **{corpus['totals']['environments_completed']} 个环境、{corpus['totals']['tasks_passed']} 个通过任务、{corpus['totals']['successful_trajectories']} 条成功轨迹**。
- 48 个正文案例的参考链平均 **{overview['package']['average_reference_steps']} 步**，Luna 成功轨迹平均调用 **{overview['package']['average_successful_tool_calls']} 次工具**；其中 {overview['package']['composed_cases']} 个是相关任务组合，其余是自然图游走。
- 人工可读性启发式检查得到：**{quality_counts['较强案例']} 个较强、{quality_counts['正常案例']} 个正常、{quality_counts['可用但建议复核']} 个建议复核**。另有 {quality_counts['边界案例']} 个明确问题案例。
- 最新 50 环境批次抽入 {newest['cases']} 个正文案例，其中 {newest['needs_review']} 个建议复核。主要问题不是工具执行失败，而是最终答案会重复字段、堆内部 ID，或直接返回序列化记录。这说明目前的 5-run 事实判分比表达质量判分更可靠。

这里的“较强/正常/建议复核”是为了看案例而做的可读性筛选，不是替代正式训练数据审核的新判分器。

## 六类正文分别是什么

{category_lines}

## 怎么看

- `categories/`：最适合人读。每条案例包含任务、可用工具、参考链、Luna 实际成功链、工具返回摘要、原始最终答案和质量说明。
- `index.html`：离线交互浏览器，可以搜索、筛选并逐步展开工具调用，建议优先使用。
- `case_index.csv`：快速筛选环境、领域、链长和质量标签，可直接用 Excel 打开。
- `cases.jsonl`：程序可读的完整案例，保留工具定义、完整工具返回、参考答案和成功轨迹。
- `quality_overview.json`：三批总数据和本案例包的统计。

## 这些数字分别是什么意思

- **参考链长**：pipeline 生成任务时，隐藏标准解需要调用多少步工具。
- **成功工具调用数**：5-run 中选中的 Luna 成功轨迹实际调用了多少次工具。它可能比参考链更长，因为模型会探索或换入口。
- **自然图游走**：由一个连贯的数据关系路径自然形成的任务。
- **相关任务组合**：把同一主题下确实相关的两个目标合并成一个任务，不是为了凑长度硬拼。
- **边界案例**：执行和事实判分通过，但答案表达明显不适合直接训练。

## 如何理解质量

5-run 通过只说明模型能在环境中找到足够证据并回答必要字段，并不自动保证语言自然。这个包因此同时展示两件事：任务和工具链是否成立，以及最后答案是否适合作为训练示范。正文已经优先选择可读性较好的成功 run，但标为“可用但建议复核”的案例仍应在正式训练前人工抽检。

本包不是从 935 个任务里随机抽 54 条，而是按领域覆盖环境，并尽量让每类同时包含自然图游走和相关组合任务。随后才在同类成功 run 中优先挑选完整、少重复、调用关系清楚的轨迹。
"""
    (output_dir / "README_ZH.md").write_text(readme, encoding="utf-8", newline="\n")

    return overview


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts") / "agent_world_task_cases_2026-08-17",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output_dir = args.output if args.output.is_absolute() else root / args.output
    overview = write_package(root, output_dir)
    archive = shutil.make_archive(str(output_dir), "zip", root_dir=output_dir)
    print(json_text({"output_dir": str(output_dir), "archive": archive, **overview["package"]}, pretty=True))


if __name__ == "__main__":
    main()
