"""Step 10：为通过 Step 9 的任务生成可审计 Rubric。

输入：``step9_filter_trace_state_kept.jsonl``。
输出：``step10_rubric.jsonl``。
"""

from __future__ import annotations

import argparse
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..utils.contracts import ProgramGenerationPolicy, TaskFields
from ..utils.io import read_json, read_records, write_json, write_jsonl


SCORE_TOLERANCE = 5


class RubricGenerationAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class Step10Result:
    output_path: Path
    total: int
    generated: int


def build_rubric_prompt(policy: ProgramGenerationPolicy) -> str:
    task_specific = policy.total_rubric_score - policy.general_rubric_score
    def score_parts(total: int) -> list[int]:
        return [3] * (total // 3) + ([total % 3] if total % 3 else [])

    general_lines = [
        f"G{index} | {points} | 可核对的通用完成条件 {index}"
        for index, points in enumerate(score_parts(policy.general_rubric_score), start=1)
    ]
    specific_lines = [
        f"T{index} | {points} | 可核对的任务特定条件 {index}"
        for index, points in enumerate(score_parts(task_specific), start=1)
    ]
    example_text = "[General]\\n" + "\\n".join(general_lines)
    example_text += "\\n\\n[Task-Specific]\\n" + "\\n".join(specific_lines)
    rubric_count = len(general_lines) + len(specific_lines)
    return f"""你正在执行 Program-form TaskGen Step 10。

读取 `rubric_request.json`，根据 task_public、output_schema、初始/最终状态、状态差异
和参考工具序列生成评分项。总分必须精确为 {policy.total_rubric_score}：

- general 项合计 {policy.general_rubric_score} 分，评价完成度、事实依据和结果表达；
- task_specific 项合计 {task_specific} 分，覆盖该任务独有的筛选、判断、状态变化和结果。

每项必须是可由 Agent 回答、工具轨迹或最终状态客观核对的单一条件。不要奖励特定
工具顺序，不要泄露 Ground Truth 的具体值，不要把多个独立条件揉成一个评分项。

每项只能为 1、2 或 3 分。最终只写 `rubric.json`，且顶层必须恰好包含以下四个字段：

```json
{{
  "rubric_count": {rubric_count},
  "total_score": {policy.total_rubric_score},
  "rubrics_text": "{example_text}",
  "explanation": "评分设计说明"
}}
```

`rubrics_text` 中必须先写 `[General]`，再空一行写 `[Task-Specific]`；每行严格使用
`G1 | 1 | ...` 或 `T1 | 3 | ...` 格式。G 只用于 general，T 只用于 task-specific。
"""


def _parse_rubric_lines(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([GT]\d+)\s*\|\s*([123])\s*\|\s*(.+?)\s*", line)
        if not match:
            continue
        identifier, points, content = match.groups()
        items.append({
            "section": "general" if identifier.startswith("G") else "task_specific",
            "id": identifier,
            "points": int(points),
            "text": content,
        })
    return items


def _normalize_rubric(
    payload: Any,
    policy: ProgramGenerationPolicy,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    required = {"rubric_count", "total_score", "rubrics_text", "explanation"}
    if not isinstance(payload, dict) or set(payload) != required:
        return None, ["rubric.json 顶层字段必须恰好为 rubric_count/total_score/rubrics_text/explanation"], {}
    text = str(payload.get("rubrics_text") or "").strip()
    explanation = str(payload.get("explanation") or "").strip()
    if "[General]" not in text or "[Task-Specific]" not in text:
        return None, ["rubrics_text 缺少 General 或 Task-Specific 分区"], {}
    items = _parse_rubric_lines(text)
    errors: list[str] = []
    ids: set[str] = set()
    general = task_specific = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"rubric_items[{index}] 不是 object")
            continue
        section = item.get("section")
        identifier = str(item.get("id") or "")
        points = item.get("points")
        text = str(item.get("text") or "").strip()
        if section not in {"general", "task_specific"}:
            errors.append(f"rubric_items[{index}].section 非法")
        if not identifier or identifier in ids:
            errors.append(f"rubric_items[{index}].id 缺失或重复")
        ids.add(identifier)
        if isinstance(points, bool) or not isinstance(points, int) or points not in {1, 2, 3}:
            errors.append(f"rubric_items[{index}].points 必须是 1、2 或 3")
            continue
        if len(text) < 8:
            errors.append(f"rubric_items[{index}].text 过短")
        if section == "general":
            general += points
        elif section == "task_specific":
            task_specific += points
    try:
        declared_count = int(payload["rubric_count"])
        declared_total = int(payload["total_score"])
    except (TypeError, ValueError):
        return None, ["rubric_count 和 total_score 必须可转换为整数"], {}
    if not explanation:
        errors.append("explanation 不能为空")
    if not items:
        errors.append("rubrics_text 没有可解析的评分行")
    if errors:
        return None, errors, {}

    expected_general = policy.general_rubric_score
    expected_specific = policy.total_rubric_score - expected_general
    actual_total = general + task_specific
    exact = (
        declared_count == len(items)
        and declared_total == policy.total_rubric_score
        and actual_total == policy.total_rubric_score
        and general == expected_general
        and task_specific == expected_specific
    )
    correction = {}
    if not exact:
        within_tolerance = (
            abs(actual_total - policy.total_rubric_score) <= SCORE_TOLERANCE
            and abs(general - expected_general) <= SCORE_TOLERANCE
            and abs(task_specific - expected_specific) <= SCORE_TOLERANCE
        )
        if not within_tolerance:
            return None, [
                "评分合计超出自动修正范围："
                f"actual={actual_total}/{general}/{task_specific}, "
                f"expected={policy.total_rubric_score}/{expected_general}/{expected_specific}"
            ], {}
        correction = {
            "score_autocorrected": True,
            "score_tolerance": SCORE_TOLERANCE,
            "declared_rubric_count": declared_count,
            "declared_total_score": declared_total,
            "actual_rubric_count": len(items),
            "actual_total_score": actual_total,
            "actual_general_score": general,
            "actual_task_specific_score": task_specific,
        }
    normalized = {
        "rubric_count": len(items),
        "total_score": actual_total,
        "rubrics_text": text,
        "explanation": explanation,
        "rubric_items": items,
    }
    return normalized, [], correction


def process_single_task(
    task: dict[str, Any],
    *,
    policy: ProgramGenerationPolicy,
    agent: RubricGenerationAgent | None,
    debug_root: Path,
) -> dict[str, Any]:
    item = deepcopy(task)
    if agent is None:
        item["rubric_generation_error"] = "Rubric generation agent is unavailable"
        return item
    task_dir = debug_root / str(task[TaskFields.TASK_ID])
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)
    ground_truth = task.get(TaskFields.GROUND_TRUTH) or {}
    write_json(task_dir / "rubric_request.json", {
        "task_public": task.get(TaskFields.TASK_PUBLIC),
        "output_schema": task.get(TaskFields.OUTPUT_SCHEMA),
        "initial_state": ground_truth.get("init_state"),
        "final_state": ground_truth.get("final_state"),
        "state_diff": ground_truth.get("state_diff"),
        "reference_tool_sequence": task.get(TaskFields.SOLUTION_TRACE),
    })
    prompt = build_rubric_prompt(policy)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    payload: dict[str, Any] | None = None
    correction_meta: dict[str, Any] = {}
    for attempt in range(1, 4):
        try:
            agent.run(prompt, working_directory=task_dir)
            candidate = read_json(task_dir / "rubric.json")
            normalized, errors, correction = _normalize_rubric(candidate, policy)
            attempts.append({"attempt": attempt, "errors": errors})
            if normalized is not None:
                payload = normalized
                correction_meta = correction
                break
        except Exception as error:
            attempts.append({
                "attempt": attempt,
                "errors": [f"{type(error).__name__}: {error}"],
            })
    if payload is None:
        item["rubric_generation_error"] = attempts
        return item
    rubric_items = deepcopy(payload["rubric_items"])
    item[TaskFields.RUBRIC_ITEMS] = rubric_items
    item[TaskFields.RUBRIC_COUNT] = payload["rubric_count"]
    item[TaskFields.RUBRIC_TOTAL_SCORE] = payload["total_score"]
    item[TaskFields.GENERAL_RUBRIC_SCORE] = policy.general_rubric_score
    item[TaskFields.TASK_SPECIFIC_RUBRIC_SCORE] = (
        policy.total_rubric_score - policy.general_rubric_score
    )
    item[TaskFields.RUBRICS_TEXT] = payload["rubrics_text"]
    item[TaskFields.RUBRIC_EXPLANATION] = str(payload.get("explanation") or "")
    item[TaskFields.RUBRIC_GENERATION_META] = {
        "attempts": attempts,
        "parse_success": True,
        "parse_correction_meta": correction_meta,
    }
    if correction_meta:
        item[TaskFields.RUBRIC_SCORE_AUTOCORRECTED] = True
        item[TaskFields.RUBRIC_ACTUAL_RUBRIC_COUNT] = payload["rubric_count"]
        item[TaskFields.RUBRIC_ACTUAL_TOTAL_SCORE] = payload["total_score"]
        item[TaskFields.RUBRIC_ACTUAL_GENERAL_SCORE] = correction_meta["actual_general_score"]
        item[TaskFields.RUBRIC_ACTUAL_TASK_SPECIFIC_SCORE] = correction_meta["actual_task_specific_score"]
    return item


def run_step10(
    *,
    step9_kept_path: Path,
    output_dir: Path,
    policy: ProgramGenerationPolicy,
    agent: RubricGenerationAgent | None,
) -> Step10Result:
    tasks = read_records(step9_kept_path)
    output_dir = output_dir.resolve()
    records = [
        process_single_task(
            task,
            policy=policy,
            agent=agent,
            debug_root=output_dir / "step10_debug",
        )
        for task in tasks
    ]
    output_path = output_dir / "step10_rubric.jsonl"
    write_jsonl(output_path, records)
    return Step10Result(
        output_path,
        len(records),
        sum(bool(item.get(TaskFields.RUBRIC_ITEMS)) for item in records),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step9-kept-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--total-score", type=int, default=14)
    parser.add_argument("--general-score", type=int, default=6)
    arguments = parser.parse_args()
    from utils.search_agent.codex import CodexAgentClient
    result = run_step10(
        step9_kept_path=arguments.step9_kept_path,
        output_dir=arguments.output_dir,
        policy=ProgramGenerationPolicy(
            total_rubric_score=arguments.total_score,
            general_rubric_score=arguments.general_score,
        ),
        agent=CodexAgentClient(model=arguments.model, sandbox="workspace-write"),
    )
    print(result.output_path)


if __name__ == "__main__":
    main()
