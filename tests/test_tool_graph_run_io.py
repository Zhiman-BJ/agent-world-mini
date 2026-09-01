from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from task_gen.tool_graph import run_io
from task_gen.tool_graph.contracts import Config, PipelineStep


class ToolGraphRunIOTest(unittest.TestCase):
    def test_run_directory_bundle_progress_and_finish_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            config = Config(
                environment_dir=environment_dir,
                schema_dir=root / "schemas",
                output_root=root / "runs",
                llm={"model": "test/model"},
            )
            run_dir = run_io.create_run_dir(config)
            self.assertTrue((run_dir / "tasks").is_dir())
            self.assertTrue((run_dir / "intermediate").is_dir())

            run_io.save_run_meta(run_dir, config)
            run_io.update_run_meta(run_dir, {"stage_timings_seconds": {"step_0": 0.1}})
            self.assertEqual(
                json.loads((run_dir / "run.json").read_text())["stage_timings_seconds"],
                {"step_0": 0.1},
            )
            bundle = {"_step": PipelineStep.ENVIRONMENT_LOAD.value, "environment": {"id": 1}}
            run_io.save_bundle(run_dir, bundle)
            self.assertEqual(run_io.load_bundle(run_dir, PipelineStep.ENVIRONMENT_LOAD), bundle)
            self.assertEqual(run_io.load_latest_bundle(run_dir), (0, bundle))

            record = {"task_id": "task1", "ok": True}
            run_io.append_progress(run_dir, PipelineStep.CHAIN_EXECUTE, record)
            self.assertEqual(run_io.load_progress(run_dir, PipelineStep.CHAIN_EXECUTE), [record])

            accepted = {"task_id": "task1"}
            rejected = {
                "task_id": "task2",
                "task": {"task_id": "task2"},
                "validation": {"passed": False, "errors": ["bad"]},
            }
            result = run_io.finish_run(
                run_dir,
                {"tasks": [
                    {"task": accepted, "validation": {"passed": True, "errors": []}},
                    rejected,
                ]},
            )
            self.assertEqual(result.task_count, 1)
            self.assertEqual(result.rejected_count, 1)
            self.assertEqual(json.loads((run_dir / "tasks.json").read_text()), [accepted])
            self.assertEqual(json.loads((run_dir / "rejected.json").read_text()), [rejected])

    def test_finish_rejects_malformed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            with self.assertRaises(ValueError):
                run_io.finish_run(run_dir, {"tasks": [{"validation": {"passed": "yes"}}]})


if __name__ == "__main__":
    unittest.main()
