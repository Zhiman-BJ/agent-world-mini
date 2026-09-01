#!/usr/bin/env python3
"""Run only the environment research stage for one or more Seeds."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from env_gen.data_gen.config import (
    CollectionPolicy,
    DataGenConfig,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
)
from env_gen.data_gen.steps.step0_prepare_run import prepare_generation_run
from env_gen.data_gen.steps.step1_research_scenario import run_scenario_research
from utils.search_agent.codex import CodexAgentClient


def _run_one(
    *,
    seed_path: Path,
    global_id: str,
    output_root: Path,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_dir = output_root / global_id
    if run_dir.exists():
        raise FileExistsError(f"输出目录已经存在：{run_dir}")
    run_dir.mkdir(parents=True)

    policy = CollectionPolicy(
        max_total_seconds=timeout_seconds,
        scenario_research_seconds=timeout_seconds,
        scenario_research_total_seconds=timeout_seconds,
        max_scenario_research_attempts=2,
    )
    prepare_generation_run(
        run_dir,
        DataGenConfig(
            seed_path=seed_path,
            global_id=global_id,
            seed_validation_schema_path=(
                ROOT / "schemas/validation/env_seeds.schema.json"
            ),
            contract_path=ROOT / "schemas/环境契约-v2.0.md",
            timeout_seconds=timeout_seconds,
        ),
        limits=asdict(policy),
    )
    client = CodexAgentClient(
        model=model,
        timeout_seconds=timeout_seconds,
        sandbox="danger-full-access",
        enable_web_search=True,
        network_access=True,
        reasoning_effort=reasoning_effort,
        disabled_mcp_servers=("openaiDeveloperDocs",),
        log_directory=run_dir / ".datagen/agent_runs",
    )

    def invoke(prompt: str, seconds: int, required_paths: tuple[Path, ...]) -> str:
        previous = client.timeout_seconds
        client.timeout_seconds = min(previous, seconds)
        try:
            return client.run_until_files(
                prompt,
                working_directory=run_dir,
                required_paths=required_paths,
            )
        finally:
            client.timeout_seconds = previous

    research, agent_calls = run_scenario_research(
        run_dir=run_dir,
        agent_runner=invoke,
    )
    return {
        "global_id": global_id,
        "path": str(run_dir / "provenance/scenario_research.json"),
        "agent_calls": agent_calls,
        "entity_count": len(research["entities"]),
        "tool_count": len(research["tools"]),
        "task_count": len(research["tasks"]),
        "source_count": len(research["research_notes"]["sources"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="只运行环境 Seed 初步调研")
    parser.add_argument("--seed-path", type=Path, required=True)
    parser.add_argument("--global-id", action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_RESEARCH_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--timeout-seconds", type=int, default=720)
    parser.add_argument("--max-workers", type=int, default=1)
    arguments = parser.parse_args()

    seed_path = arguments.seed_path.resolve()
    output_root = arguments.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=arguments.max_workers) as executor:
        futures = {
            executor.submit(
                _run_one,
                seed_path=seed_path,
                global_id=global_id,
                output_root=output_root,
                model=arguments.model,
                reasoning_effort=arguments.reasoning_effort,
                timeout_seconds=arguments.timeout_seconds,
            ): global_id
            for global_id in arguments.global_id
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    print(json.dumps({"results": sorted(results, key=lambda item: item["global_id"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
