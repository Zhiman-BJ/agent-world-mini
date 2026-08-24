from __future__ import annotations

import json
from pathlib import Path

from agent_world_mini.schemas.models import ResearchBundle
from agent_world_mini.seed_gen.themes import ThemeSeed
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.utils.search_agent.deepseek_harness import DeepSeekHarnessResearchAgent
from agent_world_mini.utils.search_agent.web import WebResearchAgent


class DataGenerator:
    """Select a research adapter and produce the DataGen handoff object."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    @staticmethod
    def load(path: Path) -> ResearchBundle:
        return ResearchBundle.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def generate(
        self,
        seed: ThemeSeed,
        *,
        complexify_rounds: int = 2,
        deepseek_harness: bool = False,
        output_file: Path | None = None,
    ) -> ResearchBundle:
        if deepseek_harness:
            if output_file is None:
                raise ValueError("DeepSeek Harness requires an output_file")
            return DeepSeekHarnessResearchAgent().gather(seed, output_file)
        return WebResearchAgent(self.llm).gather(seed, complexify_rounds=complexify_rounds)
