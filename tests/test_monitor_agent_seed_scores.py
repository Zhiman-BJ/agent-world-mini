from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from seed_gen.scripts.monitor_agent_seed_scores import monitor


class MonitorAgentScoresTests(unittest.TestCase):
    def setup_report(self, root):
        meta = {"completed": 2, "requested": 2, "failures": [], "finished_at": "done", "model": "test"}
        (root / "agent_scores.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return root / "source.json"

    def test_completed_batch_does_not_launch_agent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.setup_report(root)
            with patch("seed_gen.scripts.monitor_agent_seed_scores.scoring_processes", return_value=[]), \
                 patch("seed_gen.scripts.monitor_agent_seed_scores.finalize", return_value={"passed": True}), \
                 patch("seed_gen.scripts.monitor_agent_seed_scores.subprocess.Popen") as launch:
                monitor(source, root, 2, 3)
            launch.assert_not_called()
            self.assertEqual(json.loads((root / "monitor_status.json").read_text())["state"], "complete")

    def test_duplicate_batch_stops_monitor_without_launch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.setup_report(root)
            with patch("seed_gen.scripts.monitor_agent_seed_scores.scoring_processes", return_value=[10, 20]), \
                 patch("seed_gen.scripts.monitor_agent_seed_scores.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "duplicate scorers"):
                    monitor(source, root, 2, 3)
            launch.assert_not_called()

    def test_repair_uses_resume_and_strict_validation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.setup_report(root)
            bad = {"passed": False, "record_errors": [{"error": "wrong category"}], "missing_ids": [], "source_errors": []}
            process = SimpleNamespace(poll=lambda: 0, returncode=0)
            with patch("seed_gen.scripts.monitor_agent_seed_scores.scoring_processes", return_value=[]), \
                 patch("seed_gen.scripts.monitor_agent_seed_scores.finalize", side_effect=[bad, bad, {"passed": True}]) as audit, \
                 patch("seed_gen.scripts.monitor_agent_seed_scores.subprocess.Popen", return_value=process) as launch:
                monitor(source, root, 2, 3)
            self.assertTrue(audit.call_args_list[1].kwargs["prepare_repair"])
            command = launch.call_args.args[0]
            self.assertIn("--strict-routing", command)
            self.assertIn("--resume", command)
            self.assertNotIn("--recover-logs", command)


if __name__ == "__main__":
    unittest.main()
