from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from env_gen.tool_gen.batch import (
    BatchAlreadyRunning,
    BatchConfig,
    ToolGenBatchRunner,
)


FAKE_RUNNER = r'''
import json
import sys
from pathlib import Path

package = Path(sys.argv[1])
output = Path(sys.argv[2])
calls = Path(sys.argv[3])
with calls.open("a", encoding="utf-8") as stream:
    stream.write(package.name + "\n")
if package.name.startswith("broken"):
    raise SystemExit(7)
delivery = output / "environments" / package.name
(delivery / "tools").mkdir(parents=True, exist_ok=True)
(delivery / "environment").mkdir(parents=True, exist_ok=True)
(delivery / "tools/tools.json").write_text("[]", encoding="utf-8")
(delivery / "environment/environment.json").write_text(
    (package / "environment.json").read_text(encoding="utf-8"), encoding="utf-8"
)
(delivery / "binding.json").write_text(
    json.dumps({"package_id": package.name}), encoding="utf-8"
)
'''


class ToolGenBatchTests(unittest.TestCase):
    def make_source(self, root: Path, *names: str) -> Path:
        source = root / "source"
        source.mkdir()
        for name in names:
            package = source / name
            package.mkdir()
            (package / "environment.json").write_text(
                json.dumps({"environment_id": name}), encoding="utf-8"
            )
        return source

    def make_runner(
        self,
        root: Path,
        source: Path,
        *,
        max_attempts: int = 1,
    ) -> tuple[ToolGenBatchRunner, Path]:
        workspace = root / "workspace"
        script = root / "fake_runner.py"
        script.write_text(FAKE_RUNNER, encoding="utf-8")
        calls = root / "calls.txt"
        config = BatchConfig(
            source_root=source,
            workspace_root=workspace,
            output_root=workspace / "delivery",
            workers=2,
            max_attempts=max_attempts,
            retry_delay_seconds=0,
            heartbeat_seconds=1,
        )
        runner = ToolGenBatchRunner(
            config,
            command_builder=lambda package: [
                sys.executable,
                str(script),
                str(package),
                str(config.output_root),
                str(calls),
            ],
        )
        return runner, calls

    def test_one_environment_failure_does_not_stop_other_environments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root, "working_env", "broken_env")
            runner, calls = self.make_runner(root, source)

            self.assertEqual(runner.run(), 1)

            status = json.loads(runner.status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "completed_with_failures")
            self.assertEqual(
                status["environments"]["working_env"]["state"], "succeeded"
            )
            self.assertEqual(
                status["environments"]["broken_env"]["state"], "failed"
            )
            self.assertEqual(set(calls.read_text().splitlines()), {"working_env", "broken_env"})

    def test_completed_environment_is_not_run_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root, "working_env")
            first, calls = self.make_runner(root, source)
            self.assertEqual(first.run(), 0)

            second, _ = self.make_runner(root, source)
            self.assertEqual(second.run(), 0)

            self.assertEqual(calls.read_text().splitlines(), ["working_env"])
            status = json.loads(second.status_path.read_text(encoding="utf-8"))
            self.assertTrue(status["environments"]["working_env"]["resumed"])

    def test_workspace_lock_prevents_duplicate_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root, "working_env")
            first, _ = self.make_runner(root, source)
            second, _ = self.make_runner(root, source)
            first._prepare_directories()
            first._acquire_lock()
            try:
                second._prepare_directories()
                with self.assertRaises(BatchAlreadyRunning):
                    second._acquire_lock()
            finally:
                first._release_lock()

    @unittest.skipIf(sys.platform == "win32", "process-group signals are Unix-only")
    def test_sigterm_records_interruption_and_stops_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root, "slow_env")
            workspace = root / "workspace"
            child_pid = root / "child.pid"
            sleeper = root / "sleeper.py"
            sleeper.write_text(
                "import os,time,sys\n"
                "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                "time.sleep(120)\n",
                encoding="utf-8",
            )
            driver = root / "driver.py"
            driver.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "from env_gen.tool_gen.batch import BatchConfig, ToolGenBatchRunner\n"
                f"source=Path({str(source)!r})\n"
                f"workspace=Path({str(workspace)!r})\n"
                f"sleeper=Path({str(sleeper)!r})\n"
                f"child_pid=Path({str(child_pid)!r})\n"
                "config=BatchConfig(source, workspace, workspace/'delivery', "
                "workers=1, max_attempts=1, heartbeat_seconds=1)\n"
                "runner=ToolGenBatchRunner(config, command_builder=lambda package: "
                "[sys.executable, str(sleeper), str(child_pid)])\n"
                "raise SystemExit(runner.run())\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            project_root = str(Path(__file__).resolve().parents[1])
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(None, [project_root, environment.get("PYTHONPATH")])
            )
            process = subprocess.Popen([sys.executable, str(driver)], env=environment)
            deadline = time.monotonic() + 15
            while not child_pid.is_file() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(child_pid.is_file(), "batch child did not start")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=20), 130)

            status = json.loads(
                (workspace / "logs/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "interrupted")
            self.assertEqual(
                status["environments"]["slow_env"]["state"], "interrupted"
            )
            pid = int(child_pid.read_text(encoding="utf-8"))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
