from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utils.search_agent.codex import CodexAgentClient

from .compiler import ToolGenerator


DEFAULT_TOOL_MODEL = "gpt-5.6-sol"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} 不是合法 JSON：{error}") from error


def _reference_tools(
    *,
    hints_path: Path | None,
    seed_path: Path | None,
    seed_id: str | None,
) -> list[dict[str, Any]]:
    if hints_path is not None:
        value = _load_json(hints_path)
        if not isinstance(value, list):
            raise ValueError("--tool-hints 必须是 JSON 数组")
        return [dict(item) for item in value if isinstance(item, dict)]

    if seed_path is None:
        return []
    if not seed_id:
        raise ValueError("使用 --seed-path 时必须同时提供 --seed-id")
    document = _load_json(seed_path)
    if isinstance(document, dict) and isinstance(document.get("themes"), list):
        seeds = document["themes"]
    elif isinstance(document, list):
        seeds = document
    elif isinstance(document, dict):
        seeds = [document]
    else:
        raise ValueError("--seed-path 必须是种子对象、数组或包含 themes 的对象")
    matches = [
        item
        for item in seeds
        if isinstance(item, dict)
        and (item.get("global_id") == seed_id or item.get("theme_id") == seed_id)
    ]
    if len(matches) != 1:
        raise ValueError(f"--seed-id 必须唯一匹配一条种子，实际匹配 {len(matches)} 条")
    seed = matches[0]
    value = seed.get(
        "init_ref_tools",
        seed.get("documented_tools", seed.get("candidate_tools", [])),
    )
    if not isinstance(value, list):
        raise ValueError("种子中的参考工具必须是数组")
    return [dict(item) for item in value if isinstance(item, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用 Codex Agent 为 DataGen v2 环境包生成并执行验证工具"
    )
    parser.add_argument("environment", type=Path, help="DataGen 环境包目录或 environment.json")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--tool-hints", type=Path, help="可选的参考工具 JSON 数组")
    source_group.add_argument("--seed-path", type=Path, help="可选的上游种子 JSON")
    parser.add_argument("--seed-id", help="上游种子的 global_id 或 theme_id")
    parser.add_argument("--model", default=DEFAULT_TOOL_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default="high",
    )
    parser.add_argument(
        "--draft-reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default="medium",
    )
    parser.add_argument("--draft-batch-size", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--software-repair-attempts", type=int, default=3)
    arguments = parser.parse_args()

    tools = _reference_tools(
        hints_path=arguments.tool_hints,
        seed_path=arguments.seed_path,
        seed_id=arguments.seed_id,
    )
    package_root = (
        arguments.environment
        if arguments.environment.is_dir()
        else arguments.environment.parent
    ).resolve()
    log_directory = package_root / "tool_generation/agent_runs"
    agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.timeout_seconds,
        sandbox="workspace-write",
        enable_web_search=False,
        network_access=True,
        reasoning_effort=arguments.reasoning_effort,
        log_directory=log_directory,
    )
    draft_agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.timeout_seconds,
        sandbox="workspace-write",
        enable_web_search=False,
        network_access=True,
        reasoning_effort=arguments.draft_reasoning_effort,
        log_directory=log_directory,
    )
    result = ToolGenerator(
        agent,
        draft_agent=draft_agent,
        draft_batch_size=arguments.draft_batch_size,
        max_repairs=arguments.max_repairs,
        software_repair_attempts=arguments.software_repair_attempts,
    ).generate(
        arguments.environment,
        tool_hints=tools,
    )
    print(
        json.dumps(
            {
                "environment": str(result.environment_path),
                "tools_file": str(result.tools_path),
                "tools": result.tool_names,
                "action_plan": str(result.action_plan_path),
                "validation": str(result.validation_path),
                "grounding": str(result.grounding_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
