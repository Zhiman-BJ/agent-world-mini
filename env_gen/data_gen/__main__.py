from __future__ import annotations

import argparse
import json
from pathlib import Path

from env_gen.data_gen.acquisition import DEFAULT_OSS_OUTPUT_ROOT
from env_gen.data_gen.pipeline import (
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
    DataGenerator,
)
from env_gen.data_gen.policy import ResearchPolicy
from utils.search_agent.codex import CodexAgentClient


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 Codex Agent 生成 Agent-World 环境数据包")
    parser.add_argument("--seed-path", type=Path, required=True, help="种子 JSON 文件")
    parser.add_argument("--seed-id", required=True, help="本次要生成的 theme_id")
    parser.add_argument(
        "--schema-path",
        type=Path,
        required=True,
        help="schemas/environment.schema.json（契约结构示例；校验自动读取同目录 validation/）",
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
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--max-collection-rounds",
        type=int,
        default=4,
        help="首次采集加定向扩展的总轮数（默认 4）",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default=DEFAULT_REASONING_EFFORT,
        help=f"Codex 推理强度（默认 {DEFAULT_REASONING_EFFORT}）",
    )
    parser.add_argument(
        "--enable-web-search",
        action="store_true",
        help="启用 Codex 搜索 MCP；默认仅允许 Agent 在沙箱内使用网络命令",
    )
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    agent = CodexAgentClient(
        model=arguments.model,
        timeout_seconds=arguments.timeout_seconds,
        sandbox="workspace-write",
        enable_web_search=arguments.enable_web_search,
        network_access=True,
        reasoning_effort=arguments.reasoning_effort,
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    result = DataGenerator(
        agent,
        max_repair_rounds=arguments.max_repair_rounds,
        max_collection_rounds=arguments.max_collection_rounds,
        research_policy=ResearchPolicy(max_total_seconds=arguments.timeout_seconds),
    ).generate(
        seed_path=arguments.seed_path,
        seed_id=arguments.seed_id,
        schema_path=arguments.schema_path,
        contract_path=arguments.contract_path,
        output_dir=arguments.output_dir,
        output_root=(arguments.output_root if arguments.output_dir is None else None),
        overwrite=arguments.overwrite,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "environment": str(result.environment_path),
                "workspace": str(result.workspace_path),
                "research_request": str(result.research_request_path),
                "provenance": str(result.provenance_path),
                "research_report": str(result.research_report_path),
                "source_inventory": str(result.source_inventory_path),
                "data_profile": str(result.data_profile_path),
                "quality_profile": str(result.quality_profile_path),
                "validation": str(result.validation_path),
                "quality_tier": result.quality_tier,
                "collection_rounds": result.collection_rounds,
                "repair_rounds": result.repair_rounds,
                "elapsed_seconds": round(result.elapsed_seconds, 3),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
