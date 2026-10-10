from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from task_gen.program.run_pipeline import (
    build_parser,
    parse_step_range,
    run_selected_steps,
    validate_arguments,
)
from task_gen.program.utils.contracts import ProgramGenerationPolicy


class ProgramFormRunnerTests(unittest.TestCase):
    def test_parse_step_range_supports_only_three_steps(self):
        self.assertEqual(parse_step_range("all"), [0, 1, 2])
        self.assertEqual(parse_step_range("0-2"), [0, 1, 2])
        self.assertEqual(parse_step_range("0,2"), [0, 2])
        with self.assertRaisesRegex(ValueError, "未知步骤"):
            parse_step_range("3")
        with self.assertRaisesRegex(ValueError, "起点大于终点"):
            parse_step_range("2-0")

    def test_validate_arguments_checks_clean_replay_minimum(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = build_parser().parse_args([
                "--environment-package",
                str(root),
                "--output-dir",
                str(root / "out"),
                "--clean-replays",
                "1",
            ])
            with self.assertRaisesRegex(ValueError, "clean_replays"):
                validate_arguments(arguments)

    def test_runner_reuses_complete_step0_without_loading_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            (output / "step0_environment.json").write_text("{}", encoding="utf-8")
            (output / "baseline_environment").mkdir()
            result = run_selected_steps(
                steps=[0],
                environment_package=root / "not-needed-when-skipped",
                output_dir=output,
                policy=ProgramGenerationPolicy(),
                model="test",
            )
            self.assertEqual(result[0], output / "step0_environment.json")

    def test_runner_rejects_partial_step2_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            (output / "step2_task_solution.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "部分产物"):
                run_selected_steps(
                    steps=[2],
                    environment_package=root,
                    output_dir=output,
                    policy=ProgramGenerationPolicy(),
                    model="test",
                )


if __name__ == "__main__":
    unittest.main()
