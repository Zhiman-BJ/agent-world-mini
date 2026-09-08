from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_gen.program_form.run_pipeline import (
    build_parser,
    parse_step_range,
    run_selected_steps,
    validate_arguments,
)
from task_gen.program_form.utils.contracts import ProgramGenerationPolicy


class ProgramFormRunnerTests(unittest.TestCase):
    def test_parse_step_range_matches_omnia_runner(self):
        self.assertEqual(parse_step_range("3-7"), [3, 4, 5, 6, 7])
        self.assertEqual(parse_step_range("2,4,5"), [2, 4, 5])
        self.assertEqual(parse_step_range("2-4,7,10-12"), [2, 3, 4, 7, 10, 11, 12])

    def test_validate_arguments_rejects_invalid_pass_rate(self):
        with tempfile.TemporaryDirectory() as temporary:
            arguments = build_parser().parse_args([
                "--environment-package",
                temporary,
                "--output-dir",
                str(Path(temporary) / "output"),
                "--min-final-pass-rate",
                "1.1",
            ])
            with self.assertRaisesRegex(ValueError, "0..1"):
                validate_arguments(arguments)

    @patch("task_gen.program_form.run_pipeline._agent", return_value=object())
    def test_runner_reuses_complete_step_artifact(self, _mock_agent):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            artifact = output / "step2_task_solution.json"
            artifact.write_text("[]\n", encoding="utf-8")
            results = run_selected_steps(
                steps=[2],
                environment_package=root / "unused",
                output_dir=output,
                policy=ProgramGenerationPolicy(),
                model="test-model",
            )
            self.assertEqual(results[2], artifact)

    @patch("task_gen.program_form.run_pipeline._agent", return_value=object())
    def test_runner_rejects_partial_multi_artifact_step(self, _mock_agent):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            (output / "step8_filter_rewrite.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "部分产物"):
                run_selected_steps(
                    steps=[8],
                    environment_package=root / "unused",
                    output_dir=output,
                    policy=ProgramGenerationPolicy(),
                    model="test-model",
                )


if __name__ == "__main__":
    unittest.main()
