from __future__ import annotations

from typing import Any

from agent_world_mini.schemas.models import ResearchBundle, ToolSpec
from agent_world_mini.seed_gen.themes import ThemeSeed


def assemble_environment_manifest(
    bundle: ResearchBundle,
    seed: ThemeSeed,
    tools: list[ToolSpec],
) -> dict[str, Any]:
    """Build the environment-level handoff after tools pass validation."""
    return {
        "theme": bundle.theme,
        "theme_source": seed.to_dict(),
        "research_sources": bundle.sources,
        "state_contract": bundle.state_contract,
        "tool_count": len(tools),
        "agent_visible_contract": {
            "task": "Provided per task",
            "tools": "All retained schemas are visible before the rollout",
            "state": "Only observations returned by calls are visible; database snapshot and evaluators remain sandbox-internal",
        },
        "reset_policy": "Each reference execution and ReAct rollout starts from the same source records, local state seed, and researched resource snapshot.",
    }
