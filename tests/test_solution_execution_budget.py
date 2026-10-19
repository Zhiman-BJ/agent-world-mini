from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from task_gen.program.step_2_solution_generate import _ExecutionBudget


class SolutionExecutionBudgetTests(unittest.TestCase):
    def run_solution(self, source, budget, namespace=None):
        previous = sys.gettrace()
        try:
            sys.settrace(budget.trace)
            exec(compile(source, "<solution-code>", "exec"), namespace or {})
        finally:
            sys.settrace(previous)

    def test_external_runtime_frame_has_no_line_trace(self):
        observed = []

        def external_work():
            observed.append(sys._getframe().f_trace)
            total = 0
            for index in range(1000):
                total += index
            return total

        budget = _ExecutionBudget(timeout_seconds=60, max_lines=2)
        namespace = {"external_work": external_work}
        self.run_solution("result = external_work()\nfinal_answer = str(result)\n", budget, namespace)

        self.assertEqual(observed, [None])
        self.assertEqual(budget.lines, 2)
        self.assertEqual(namespace["final_answer"], "499500")

    def test_nested_solution_function_still_exhausts_line_limit(self):
        budget = _ExecutionBudget(timeout_seconds=60, max_lines=8)
        source = "def repeat():\n    while True:\n        value = 1\nrepeat()\n"
        previous = sys.gettrace()

        with self.assertRaisesRegex(TimeoutError, "参考程序超过最大执行行数"):
            self.run_solution(source, budget)

        self.assertEqual(budget.lines, 9)
        self.assertIs(sys.gettrace(), previous)

    def test_external_work_time_still_counts_toward_deadline(self):
        clock = [100.0]

        def external_work():
            clock[0] = 161.0

        with patch("task_gen.program.step_2_solution_generate.time.monotonic", side_effect=lambda: clock[0]):
            budget = _ExecutionBudget(timeout_seconds=60, max_lines=10)
            with self.assertRaisesRegex(TimeoutError, "参考程序执行超时"):
                self.run_solution("external_work()\nfinal_answer = 'done'\n", budget,
                                  {"external_work": external_work})

        self.assertEqual(budget.lines, 2)

    def test_solution_exception_restores_previous_trace(self):
        budget = _ExecutionBudget(timeout_seconds=60, max_lines=10)
        previous = sys.gettrace()

        with self.assertRaisesRegex(ValueError, "expected"):
            self.run_solution("raise ValueError('expected')\n", budget)

        self.assertEqual(budget.lines, 1)
        self.assertIs(sys.gettrace(), previous)
