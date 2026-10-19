from __future__ import annotations

import unittest

from task_gen.program.step_2_solution_generate import (
    _authoring_call_target,
    build_task_solution_prompt,
)
from task_gen.program.utils.contracts import ProgramGenerationPolicy


class Step2AuthoringSignalTests(unittest.TestCase):
    def test_authoring_target_equals_configured_minimum(self):
        for minimum in (15, 25):
            policy = ProgramGenerationPolicy(minimum_effective_tool_calls=minimum)
            for environment_id in ("carbon_isotope", "fept", "pbs"):
                for task_index in (0, 1, 7):
                    with self.subTest(
                        minimum=minimum,
                        environment_id=environment_id,
                        task_index=task_index,
                    ):
                        self.assertEqual(
                            _authoring_call_target(policy, environment_id, task_index),
                            minimum,
                        )

    def test_unset_minimum_does_not_create_a_target(self):
        self.assertIsNone(
            _authoring_call_target(ProgramGenerationPolicy(), "fept", 0)
        )

    def test_baseline_allows_value_preserving_replacement_without_old_operations(self):
        prompt = build_task_solution_prompt(
            1,
            ProgramGenerationPolicy(minimum_effective_tool_calls=15),
            baseline_task_available=True,
        )

        self.assertIn("同领域用户价值和实质难度", prompt)
        self.assertIn("同等或更有价值的业务成果替代", prompt)
        self.assertIn("不必逐项保留旧操作和报告栏目", prompt)
        self.assertIn("在 `change_manifest` 中说明替代依据", prompt)
        self.assertNotIn("用于逐项保留核心成果", prompt)
        self.assertIn("--minimum-effective-tool-calls 15", prompt)
        self.assertIn("参考解至少保留 15 次有效业务调用", prompt)
        self.assertIn("不能重复查询或拆分批量结果凑数", prompt)
        self.assertIn("不能复制隐藏答案进公开题文", prompt)

    def test_missing_baseline_still_uses_research_without_claiming_preservation(self):
        prompt = build_task_solution_prompt(1, ProgramGenerationPolicy())

        self.assertIn("本轮没有提供原任务正文", prompt)
        self.assertIn("不要声称保留了未提供的原任务成果", prompt)
        self.assertNotIn("- `baseline_task.json`", prompt)
        self.assertNotIn("--minimum-effective-tool-calls", prompt)


if __name__ == "__main__":
    unittest.main()
