from pathlib import Path
import unittest

from env_gen.tool_gen.compiler import ToolGenerator


class PlanResourceDependenciesTests(unittest.TestCase):
    def test_plan_includes_scopes_of_file_references_returned_from_records(self):
        prompt = ToolGenerator._build_action_plan_prompt(Path('example'))
        self.assertIn('asset_ids 和 usageConditions.targetResources', prompt)
        self.assertIn('只查询 Record Set、返回记录中的文件路径', prompt)
        self.assertIn('environment.json 的字段说明和实际文件', prompt)


if __name__ == '__main__':
    unittest.main()
