from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.search_agent.codex import (
    CodexAgentClient,
    CodexLaunchError,
    CodexProcessError,
    CodexTimeoutError,
)


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


class CodexAgentClientTests(unittest.TestCase):
    def test_missing_executable_is_non_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = CodexAgentClient(executable=str(Path(directory) / "missing"))
            with self.assertRaises(CodexLaunchError) as raised:
                client.run("work", working_directory=Path(directory))
            self.assertFalse(raised.exception.retryable)

    def test_process_failure_is_classified_and_logs_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = executable(
                root / "fake-codex",
                'cat >/dev/null\necho "invalid authentication configuration" >&2\nexit 2\n',
            )
            logs = root / "logs"
            client = CodexAgentClient(executable=str(command), log_directory=logs)
            with self.assertRaises(CodexProcessError) as raised:
                client.run("work", working_directory=root)
            self.assertFalse(raised.exception.retryable)
            self.assertIn(
                "invalid authentication configuration",
                (logs / "run_01/stderr.log").read_text(),
            )

    def test_transient_process_failure_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = executable(
                root / "fake-codex",
                'cat >/dev/null\necho "service unavailable 503" >&2\nexit 2\n',
            )
            client = CodexAgentClient(executable=str(command))
            with self.assertRaises(CodexProcessError) as raised:
                client.run("work", working_directory=root)
            self.assertTrue(raised.exception.retryable)

    def test_session_timeout_is_retryable_and_keeps_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = executable(
                root / "fake-codex",
                'cat >/dev/null\necho "started" >&2\nsleep 5\n',
            )
            logs = root / "logs"
            client = CodexAgentClient(
                executable=str(command),
                timeout_seconds=1,
                log_directory=logs,
            )
            with self.assertRaises(CodexTimeoutError) as raised:
                client.run("work", working_directory=root)
            self.assertTrue(raised.exception.retryable)
            self.assertIn("started", (logs / "run_01/stderr.log").read_text())


if __name__ == "__main__":
    unittest.main()
