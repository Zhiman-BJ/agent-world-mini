"""Step 2 只暴露来源探索所需命令。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..collection.commands.download_raw import (
    DOWNLOAD_FORMATS,
    DownloadFailure,
    download_raw_batch,
    download_raw_file,
)
from ..collection.commands.save_source_plan import save_source_plan
from .commands import assess_exploration, finalize_exploration


def _main() -> None:
    parser = argparse.ArgumentParser(description="DataGen Step 2 来源探索控制器")
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save-source-plan")
    save.add_argument("--input", type=Path, required=True)
    download = commands.add_parser("download")
    download.add_argument("--url", required=True)
    download.add_argument("--source-id")
    download.add_argument("--output", required=True)
    download.add_argument("--format", dest="expected_format", choices=sorted(DOWNLOAD_FORMATS), default="any")
    download.add_argument("--timeout-seconds", type=int, default=240)
    batch = commands.add_parser("download-batch")
    batch.add_argument("--manifest", type=Path, required=True)
    batch.add_argument("--max-workers", type=int)
    commands.add_parser("assess")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--result", choices=["ready", "insufficient_public_data"], required=True)
    arguments = parser.parse_args()
    if arguments.command == "save-source-plan":
        result = save_source_plan(arguments.run_dir, input_path=arguments.input)
    elif arguments.command == "download":
        try:
            result = download_raw_file(
                arguments.run_dir, url=arguments.url, output=arguments.output,
                expected_format=arguments.expected_format,
                timeout_seconds=arguments.timeout_seconds, source_id=arguments.source_id,
            )
        except DownloadFailure as error:
            print(json.dumps(error.to_dict(), ensure_ascii=False, indent=2))
            raise SystemExit(2) from error
    elif arguments.command == "download-batch":
        payload = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            parser.error("manifest 必须是数组或包含 items 数组")
        result = download_raw_batch(arguments.run_dir, items=items, max_workers=arguments.max_workers)
    elif arguments.command == "assess":
        result = assess_exploration(arguments.run_dir)
    else:
        result = finalize_exploration(arguments.run_dir, result=arguments.result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


main = _main

__all__ = ["_main", "main"]
