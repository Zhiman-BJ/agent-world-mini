from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_shell_startup_hooks_are_isolated_without_changing_parent_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = root / "shell-hook.sh"
            marker = root / "hook-ran"
            hook.write_text(
                f"printf inherited-hook > '{marker}'\nexit 91\n", encoding="utf-8"
            )
            command = executable(
                root / "fake-codex",
                "cat >/dev/null\n"
                'printf "%s\\n" "$@" > args.txt\n'
                'printf "%s\\n%s\\n%s\\n" "${BASH_ENV-unset}" "${ENV-unset}" '
                '"${AGENT_TEST_VALUE-unset}" > child-environment.txt\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = "--output-last-message" ]; then\n'
                '    shift\n'
                '    printf ready > "$1"\n'
                "  fi\n"
                "  shift\n"
                "done\n",
            )
            inherited = {
                "CODEX_HOME": directory,
                "BASH_ENV": str(hook),
                "ENV": str(hook),
                "AGENT_TEST_VALUE": "preserved",
            }
            with patch.dict(os.environ, inherited):
                client = CodexAgentClient(
                    executable=str(command),
                    timeout_seconds=5,
                    log_directory=root / "logs",
                )
                self.assertEqual(client.run("work", working_directory=root), "ready")
                self.assertEqual(
                    (root / "child-environment.txt").read_text().splitlines(),
                    ["", "", "preserved"],
                )
                self.assertEqual(os.environ["BASH_ENV"], str(hook))
                self.assertEqual(os.environ["ENV"], str(hook))
            self.assertFalse(marker.exists())
            args = (root / "args.txt").read_text().splitlines()
            self.assertTrue(any(line.startswith("sqlite_home=") for line in args))
            self.assertTrue((root / "logs/run_01/sqlite_home").is_dir())

    def test_missing_mcp_server_does_not_create_an_incomplete_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = CodexAgentClient(disabled_mcp_servers=("openaiDeveloperDocs",))
            environment = {"CODEX_HOME": directory}
            self.assertEqual(client._mcp_arguments(environment), [])
            (Path(directory) / "config.toml").write_text('model = "test"\n', encoding="utf-8")
            self.assertEqual(client._mcp_arguments(environment), [])

    def test_only_configured_mcp_servers_are_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.toml").write_text(
                '[mcp_servers.openaiDeveloperDocs]\nurl = "https://example.com/mcp"\n',
                encoding="utf-8",
            )
            client = CodexAgentClient(disabled_mcp_servers=("openaiDeveloperDocs", "missing"))
            self.assertEqual(
                client._mcp_arguments({"CODEX_HOME": directory}),
                ["--config", 'mcp_servers."openaiDeveloperDocs".enabled=false'],
            )

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

    def test_multiple_json_checkpoints_wait_until_every_file_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = executable(
                root / "fake-codex",
                "cat >/dev/null\n"
                "printf '{\"first\":' > first.json\n"
                "printf '{\"second\":2}' > second.json\n"
                "sleep 1\n"
                "printf '1}' >> first.json\n"
                "sleep 5\n",
            )
            client = CodexAgentClient(executable=str(command), timeout_seconds=10)
            result = client.run_until_files(
                "write two files",
                working_directory=root,
                required_paths=(root / "first.json", root / "second.json"),
            )

            self.assertEqual(result, "已到达文件提交点。")
            self.assertEqual(json.loads((root / "first.json").read_text()), {"first": 1})
            self.assertEqual(
                json.loads((root / "second.json").read_text()), {"second": 2}
            )


if __name__ == "__main__":
    unittest.main()
