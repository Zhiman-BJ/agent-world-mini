from __future__ import annotations

import subprocess
import sys

from utils.search_agent.codex import CodexAgentClient


def test_terminate_process_group_stops_the_started_process() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    CodexAgentClient._terminate_process_group(process)
    assert process.poll() is not None
