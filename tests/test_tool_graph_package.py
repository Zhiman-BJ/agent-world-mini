from __future__ import annotations

import importlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ToolGraphPackageTest(unittest.TestCase):
    def test_nested_package_uses_latest_repository_layout(self) -> None:
        contracts = importlib.import_module("agent_world_mini.task_gen.tool_graph.contracts")
        llm = importlib.import_module("agent_world_mini.task_gen.tool_graph.llm")
        run_io = importlib.import_module("agent_world_mini.task_gen.tool_graph.run_io")

        config = contracts.Config()
        self.assertEqual(config.schema_dir, ROOT / "schemas")
        self.assertEqual(llm.LLMClient.__module__, "utils.llm")
        loaded = run_io.load_config(ROOT / "config/tool_graph.yaml")
        self.assertEqual(loaded.schema_dir, ROOT / "schemas")


if __name__ == "__main__":
    unittest.main()
