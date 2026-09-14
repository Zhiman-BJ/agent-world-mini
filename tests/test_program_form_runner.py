from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from task_gen.program_form.run_pipeline import (
    build_parser,
    parse_step_range,
    run_selected_steps,
    validate_arguments,
)
from task_gen.program_form.utils.contracts import ProgramGenerationPolicy


class ProgramFormRunnerTests(unittest.TestCase):
    def test_parse_step_range_supports_only_five_steps(self):
        self.assertEqual(parse_step_range("all"), [1, 2, 3, 4, 5])
        self.assertEqual(parse_step_range("2-4"), [2, 3, 4])
        self.assertEqual(parse_step_range("1,3,5"), [1, 3, 5])
        with self.assertRaisesRegex(ValueError, "未知步骤"):
            parse_step_range("6")
        with self.assertRaisesRegex(ValueError, "起点大于终点"):
            parse_step_range("4-2")

    def test_validate_arguments_checks_minimum_passing_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = build_parser().parse_args([
                "--environment-package",
                str(root),
                "--output-dir",
                str(root / "out"),
                "--difficulty-runs",
                "2",
                "--minimum-passing-runs",
                "3",
            ])
            with self.assertRaisesRegex(ValueError, "minimum_passing_runs"):
                validate_arguments(arguments)

    def test_runner_reuses_complete_step1_without_creating_an_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            (output / "step1_environment.json").write_text("{}", encoding="utf-8")
            (output / "baseline_environment").mkdir()
            result = run_selected_steps(
                steps=[1],
                environment_package=root / "not-needed-when-skipped",
                output_dir=output,
                policy=ProgramGenerationPolicy(),
                model="test",
            )
            self.assertEqual(result[1], output / "step1_environment.json")

    def test_runner_rejects_partial_step5_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            (output / "step5_difficulty.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "部分产物"):
                run_selected_steps(
                    steps=[5],
                    environment_package=root,
                    output_dir=output,
                    policy=ProgramGenerationPolicy(),
                    model="test",
                )


if __name__ == "__main__":
    unittest.main()
