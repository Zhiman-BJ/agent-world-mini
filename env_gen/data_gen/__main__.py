from __future__ import annotations

import argparse
import json
from pathlib import Path

from env_gen.data_gen.config import (
    DEFAULT_OSS_OUTPUT_ROOT,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
    DataGenConfig,
)
from env_gen.data_gen.run_pipeline import run_pipeline
from utils.search_agent.codex import CodexAgentClient


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 Codex Agent 生成 Agent-World 环境数据包")
    parser.add_argument("--seed-path", type=Path, required=True, help="种子 JSON 文件")
    parser.add_argument("--global-id", required=True, help="最终 Seed 集合中的 global_id")
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=Path("schemas/validation/environment-v2.schema.json"),
        help="兼容参数；v2 管线固定使用 schemas/validation/environment-v2.schema.json",
    )
    parser.add_argument("--contract-path", type=Path, help="可选的人类可读环境契约；默认读取 Schema 同目录文件")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output-dir",
        type=Path,
        help="显式最终目录，主要用于测试；不会自动增加 rich/not_rich 层级",
    )
    output_group.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OSS_OUTPUT_ROOT,
        help=f"分类发布根目录（默认 {DEFAULT_OSS_OUTPUT_ROOT}）",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_RESEARCH_MODEL,
        help=f"调研 Agent 使用的模型（默认 {DEFAULT_RESEARCH_MODEL}）",
    )
    parser.add_argument("--timeout-seconds", type=int, default=4200)
    parser.add_argument(
        "--max-collection-rounds",
        type=int,
        default=4,
        help="兼容参数；新管线分别使用来源探索和集成轮次策略",
    )
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default=DEFAULT_REASONING_EFFORT,
        help=f"Codex 推理强度（默认 {DEFAULT_REASONING_EFFORT}）",
    )
    parser.add_argument(
        "--enable-web-search",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否启用 Codex Web Search（默认启用，Step 1 场景研究需要）",
    )
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.timeout_seconds,
        # OSS mount is root-owned in the production runner; bwrap cannot start
        # its nested workspace sandbox there.  DataGen already confines output
        # to a disposable staging directory and verifies frozen-file hashes.
        sandbox="danger-full-access",
        enable_web_search=arguments.enable_web_search,
        network_access=True,
        reasoning_effort=arguments.reasoning_effort,
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    config = DataGenConfig(
        seed_path=arguments.seed_path,
        global_id=arguments.global_id,
        schema_path=arguments.schema_path,
        contract_path=arguments.contract_path,
        output_dir=arguments.output_dir,
        output_root=(arguments.output_root if arguments.output_dir is None else None),
        overwrite=arguments.overwrite,
        model=arguments.model,
        reasoning_effort=arguments.reasoning_effort,
        timeout_seconds=arguments.timeout_seconds,
        max_collection_rounds=arguments.max_collection_rounds,
        max_repair_rounds=arguments.max_repair_rounds,
        enable_web_search=arguments.enable_web_search,
    )
    result = run_pipeline(config, agent=agent)
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "environment": str(result.environment_path),
                "environment_context": str(result.environment_context_path),
                "state": str(result.state_path),
                "seed_global_id": result.seed_global_id,
                "seed_sha256": result.seed_sha256,
                "scenario_research": str(result.scenario_research_path),
                "source_plan": str(result.source_plan_path),
                "source_inventory": str(result.source_inventory_path),
                "integration_plan": str(result.integration_plan_path),
                "integration_profile": str(result.integration_profile_path),
                "quality_profile": str(result.quality_profile_path),
                "source_manifest": str(result.source_manifest_path),
                "validation": str(result.validation_path),
                "quality_tier": result.quality_tier,
                "integration_tier": result.integration_tier,
                "scenario_research_agent_calls": result.scenario_research_agent_calls,
                "exploration_agent_calls": result.exploration_agent_calls,
                "integration_agent_calls": result.integration_agent_calls,
                "integration_assessment_runs": result.integration_assessment_runs,
                "elapsed_seconds": round(result.elapsed_seconds, 3),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
