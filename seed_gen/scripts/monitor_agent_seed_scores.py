"""Attach to a running scoring batch, then audit and repair only unfinished reviews."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from seed_gen.catalog import DEFAULT_SEED_OUTPUT
from seed_gen.scripts.agent_score_mcp_seeds import write_json
from seed_gen.scripts.finalize_agent_seed_scores import finalize


def scoring_processes(output_dir: Path) -> list[int]:
    """Enumerate only the scorer processes for this exact output directory on Windows."""
    command = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' "
        "-and $_.CommandLine -match 'seed_gen.scripts.agent_score_mcp_seeds' } "
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30, check=True)
    if not result.stdout.strip():
        return []
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    # Current launch commands use one unquoted --output-dir argument (no spaces).
    found = []
    for row in rows:
        parts = row["CommandLine"].split()
        if "--output-dir" not in parts:
            continue
        target = parts[parts.index("--output-dir") + 1].strip('"')
        if Path(target).resolve() == output_dir.resolve():
            found.append(row["ProcessId"])
    return found


def monitor(source: Path, output_dir: Path, workers: int, max_rounds: int):
    log_dir = output_dir / "monitor_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "monitor_status.json"
    state = {"started_at": datetime.now(timezone.utc).isoformat(), "state": "monitoring", "repair_round": 0}

    def publish():
        meta_path = output_dir / "agent_scores.meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            state.update(completed=meta["completed"], requested=meta["requested"], failures=len(meta["failures"]))
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(status_path, state)

    def wait_for_batch():
        while True:
            active = scoring_processes(output_dir)
            state["scoring_pids"] = active
            publish()
            if len(active) > 1:
                raise RuntimeError(f"duplicate scorers found: {active}; will not launch more")
            if not active:
                return
            time.sleep(30)

    try:
        for round_index in range(max_rounds + 1):
            wait_for_batch()
            state["state"] = "auditing"
            publish()
            audit = finalize(source, output_dir)
            if audit["passed"]:
                state["state"] = "complete"
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
                publish()
                return
            state["record_errors"] = len(audit["record_errors"])
            state["missing"] = len(audit["missing_ids"])
            if audit["source_errors"]:
                raise RuntimeError("source audit failed; source records will not be changed")
            if round_index == max_rounds:
                raise RuntimeError("repair-round limit reached; inspect final_audit.json")
            meta = json.loads((output_dir / "agent_scores.meta.json").read_text(encoding="utf-8"))
            interrupted = not meta.get("finished_at")
            if not interrupted:
                finalize(source, output_dir, prepare_repair=True)
            command = [sys.executable, "-u", "-m", "seed_gen.scripts.agent_score_mcp_seeds",
                       "--source", str(source), "--output-dir", str(output_dir), "--workers", str(workers),
                       "--retries", "1", "--timeout-seconds", "900", "--checkpoint-every", "1",
                       "--model", meta["model"], "--strict-routing", "--resume"]
            if interrupted:
                command.append("--recover-logs")
            if scoring_processes(output_dir):
                raise RuntimeError("a scorer appeared during audit; refusing a duplicate launch")
            state.update(state="repairing", repair_round=round_index + 1)
            publish()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            with (log_dir / f"round_{round_index + 1}_{stamp}.stdout.log").open("w", encoding="utf-8") as stdout, \
                 (log_dir / f"round_{round_index + 1}_{stamp}.stderr.log").open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
                while process.poll() is None:
                    state["scoring_pids"] = [process.pid]
                    publish()
                    time.sleep(30)
                if process.returncode:
                    state["last_exit_code"] = process.returncode
        raise RuntimeError("monitor ended without acceptance")
    except Exception as exc:
        state.update(state="needs_attention", error=f"{type(exc).__name__}: {exc}")
        publish()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-rounds", type=int, default=3)
    args = parser.parse_args()
    if args.workers < 1 or args.max_rounds < 0:
        parser.error("workers must be positive and max-rounds nonnegative")
    # Windows OS lock is released if the monitor exits or crashes.
    import msvcrt
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "monitor.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            monitor(args.source, args.output_dir, args.workers, args.max_rounds)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    main()
