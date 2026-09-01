"""Deterministic validation for the concise environment research brief."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@dataclass(frozen=True)
class ScenarioResearchIssue:
    code: str
    path: str
    message: str


def _pointer(error: Any) -> str:
    value = "$"
    for part in error.absolute_path:
        value += f"[{part}]" if isinstance(part, int) else f".{part}"
    return value


def _duplicate_name_issues(
    payload: dict[str, Any],
    collection: str,
) -> list[ScenarioResearchIssue]:
    names = [
        str(item.get("name") or "").strip()
        for item in payload.get(collection, [])
        if isinstance(item, dict)
    ]
    normalized = [name.casefold() for name in names if name]
    duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
    if not duplicates:
        return []
    return [ScenarioResearchIssue(
        f"duplicate_{collection}_name",
        f"$.{collection}",
        f"名称不能重复：{duplicates}",
    )]


def validate_scenario_research_payload(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    seed: dict[str, Any],
    seed_sha256: str,
) -> list[ScenarioResearchIssue]:
    """Validate structure, Seed identity, and explicit reference-tool coverage."""

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    issues = [
        ScenarioResearchIssue("scenario_research_schema", _pointer(error), error.message)
        for error in sorted(
            validator.iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]
    if payload.get("seed_global_id") != seed.get("global_id"):
        issues.append(ScenarioResearchIssue(
            "scenario_research_seed_id_mismatch",
            "$.seed_global_id",
            "调研结果没有绑定当前 Seed global_id",
        ))
    if payload.get("seed_sha256") != seed_sha256:
        issues.append(ScenarioResearchIssue(
            "scenario_research_seed_hash_mismatch",
            "$.seed_sha256",
            "调研结果没有绑定当前 Seed SHA-256",
        ))

    for collection in ("entities", "tools", "tasks"):
        issues.extend(_duplicate_name_issues(payload, collection))

    reference_tool_names = {
        str(item.get("name") or "").strip()
        for item in seed.get("init_ref_tools", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    researched_tool_names = {
        str(item.get("name") or "").strip()
        for item in payload.get("tools", [])
        if isinstance(item, dict)
    }
    missing_tools = sorted(reference_tool_names - researched_tool_names)
    if missing_tools:
        issues.append(ScenarioResearchIssue(
            "missing_reference_tools",
            "$.tools",
            "这些 Seed 参考工具没有使用原名称形成独立说明：" + ", ".join(missing_tools),
        ))

    reference_task_count = sum(
        1 for item in seed.get("init_ref_tasks", []) if isinstance(item, dict)
    )
    researched_task_count = sum(
        1 for item in payload.get("tasks", []) if isinstance(item, dict)
    )
    if researched_task_count < max(1, reference_task_count):
        issues.append(ScenarioResearchIssue(
            "insufficient_task_coverage",
            "$.tasks",
            "任务数量不足以分别覆盖 Seed 中的参考任务",
        ))
    return issues


def validate_scenario_research_file(
    path: Path,
    *,
    schema: dict[str, Any],
    seed: dict[str, Any],
    seed_sha256: str,
) -> tuple[dict[str, Any] | None, list[ScenarioResearchIssue]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [ScenarioResearchIssue(
            "missing_scenario_research", "$.scenario_research", f"缺少 {path}",
        )]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [ScenarioResearchIssue(
            "invalid_scenario_research_json",
            "$.scenario_research",
            f"调研结果无法读取：{error}",
        )]
    if not isinstance(payload, dict):
        return None, [ScenarioResearchIssue(
            "invalid_scenario_research",
            "$.scenario_research",
            "调研结果根节点必须是对象",
        )]
    return payload, validate_scenario_research_payload(
        payload,
        schema=schema,
        seed=seed,
        seed_sha256=seed_sha256,
    )


__all__ = [
    "ScenarioResearchIssue",
    "validate_scenario_research_file",
    "validate_scenario_research_payload",
]
