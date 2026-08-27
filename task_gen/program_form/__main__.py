from __future__ import annotations

import argparse
import json
from pathlib import Path

from task_gen.program_form.generator import (
    DEFAULT_PROGRAM_MODEL,
    DEFAULT_REASONING_EFFORT,
    ProgramTaskGenerator,
)
from task_gen.program_form.models import ProgramGenerationPolicy
from utils.search_agent.codex import CodexAgentClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="基于完整 Agent-World 环境生成并重放验证 Program-form 任务"
    )
    parser.add_argument(
        "--environment-package",
        type=Path,
        required=True,
        help="包含 environment.json 和 workspace/ 的完整环境包",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-count", type=int, default=2)
    parser.add_argument("--candidate-multiplier", type=int, default=2)
    parser.add_argument("--min-tool-calls", type=int, default=4)
    parser.add_argument("--min-distinct-tools", type=int, default=2)
    parser.add_argument("--clean-replays", type=int, default=2)
    parser.add_argument("--require-state-change", action="store_true")
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--execution-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--model", default=DEFAULT_PROGRAM_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default=DEFAULT_REASONING_EFFORT,
    )
    parser.add_argument("--agent-timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--candidates",
        type=Path,
        help="跳过模型，验证已有 candidates.json；用于调试和复现",
    )
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    policy = ProgramGenerationPolicy(
        task_count=arguments.task_count,
        candidate_multiplier=arguments.candidate_multiplier,
        min_tool_calls=arguments.min_tool_calls,
        min_distinct_tools=arguments.min_distinct_tools,
        clean_replays=arguments.clean_replays,
        require_state_change=arguments.require_state_change,
        max_repair_rounds=arguments.max_repair_rounds,
        execution_timeout_seconds=arguments.execution_timeout_seconds,
    )
    agent = None
    if arguments.candidates is None:
        agent = CodexAgentClient(
            model=arguments.model,
            timeout_seconds=arguments.agent_timeout_seconds,
            sandbox="workspace-write",
            enable_web_search=False,
            network_access=False,
            reasoning_effort=arguments.reasoning_effort,
            disabled_mcp_servers=("openaiDeveloperDocs",),
        )
    result = ProgramTaskGenerator(agent, policy=policy).generate(
        environment_package=arguments.environment_package,
        output_dir=arguments.output_dir,
        candidates_path=arguments.candidates,
        overwrite=arguments.overwrite,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "tasks": str(result.tasks_path),
                "validation": str(result.validation_path),
                "task_count": result.task_count,
                "repair_rounds": result.repair_rounds,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
