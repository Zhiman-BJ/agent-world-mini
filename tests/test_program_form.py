from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from task_gen.program_form import ProgramGenerationPolicy, validate_solution_code
from task_gen.program_form.steps.step2_research_real_world_tasks import (
    build_task_research_prompt,
)
from task_gen.program_form.steps.step3_generate_task_solution import (
    build_task_solution_prompt,
)
from task_gen.program_form.steps.step4_generate_scoring_criteria import (
    build_scoring_prompt,
)
from task_gen.program_form.steps.step5_evaluate_difficulty import (
    build_rubric_judge_prompt,
    classify_difficulty,
    estimate_pass_at_k,
)
from task_gen.program_form.utils.io import read_records, write_jsonl
from task_gen.program_form.utils.verifier import (
    execute_verifier_code,
    state_verifier_smoke_test,
    verifier_smoke_test,
)


class ProgramFormContractTests(unittest.TestCase):
    def test_each_agent_prompt_keeps_its_step_responsibility_visible(self):
        policy = ProgramGenerationPolicy()
        research = build_task_research_prompt(1)
        generation = build_task_solution_prompt(1, policy)
        scoring = build_scoring_prompt(policy, 1)
        difficulty = build_rubric_judge_prompt(1)

        self.assertIn("不负责生成 benchmark 题目", research)
        self.assertIn("task_research.json", research)
        self.assertIn("真实 Runtime", generation)
        self.assertIn("solution_code", generation)
        self.assertIn("answer_verifier_code", scoring)
        self.assertIn("state_verifier_code", scoring)
        self.assertIn("不读取模型声明的总分", difficulty)

    def test_policy_rejects_impossible_difficulty_gate(self):
        with self.assertRaisesRegex(ValueError, "minimum_passing_runs"):
            ProgramGenerationPolicy(
                difficulty_eval_runs=2,
                minimum_passing_runs=3,
            ).validate()
        with self.assertRaisesRegex(ValueError, "infrastructure_retries"):
            ProgramGenerationPolicy(infrastructure_retries=-1).validate()

    def test_solution_language_rejects_direct_environment_access(self):
        errors = validate_solution_code(
            'value = open("state/data.json").read()\nfinal_answer = {"value": value}'
        )
        self.assertTrue(any("open" in error for error in errors))
        self.assertEqual(
            validate_solution_code('final_answer = {"status": "ok"}'),
            [],
        )

    def test_answer_and_state_verifier_smoke_tests_use_negative_cases(self):
        answer_code = """def verify(candidate_answer, ground_truth_answer):
    return 1.0 if candidate_answer == ground_truth_answer else 0.0
"""
        state_code = """def verify_state(candidate_state, ground_truth_state, initial_state):
    return 1.0 if candidate_state == ground_truth_state else 0.0
"""
        answer_ok, answer_errors = verifier_smoke_test(answer_code, {"value": 3})
        state_ok, state_errors = state_verifier_smoke_test(
            state_code,
            {"value": 1},
            {"value": 3},
        )
        self.assertTrue(answer_ok, answer_errors)
        self.assertTrue(state_ok, state_errors)

        permissive = """def verify(candidate_answer, ground_truth_answer):
    return 1.0
"""
        permissive_ok, _ = verifier_smoke_test(permissive, {"value": 3})
        self.assertFalse(permissive_ok)

    def test_verifier_execution_budget_stops_an_infinite_loop(self):
        code = """def verify(candidate_answer, ground_truth_answer):
    while True:
        candidate_answer = candidate_answer
"""
        with self.assertRaisesRegex(TimeoutError, "最大执行行数|执行超时"):
            execute_verifier_code(code, {"value": 1}, {"value": 1})

    def test_jsonl_reader_uses_latest_checkpoint_for_an_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            write_jsonl(path, [{"value": 1}, {"value": 2}])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"__idx": 0, "item": {"value": 9}}) + "\n")
            self.assertEqual(read_records(path), [{"value": 9}, {"value": 2}])

    def test_difficulty_math_does_not_label_zero_success_as_hard(self):
        self.assertEqual(classify_difficulty(0, 5), "unsolved_rework")
        self.assertEqual(classify_difficulty(1, 5), "very_hard")
        self.assertAlmostEqual(estimate_pass_at_k(5, 1, 1), 0.2)
        self.assertIsNone(estimate_pass_at_k(2, 1, 3))


if __name__ == "__main__":
    unittest.main()
