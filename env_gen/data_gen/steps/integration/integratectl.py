"""Step 3 集成控制器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..collection.commands.download_raw import DownloadFailure, download_raw_batch, download_raw_file
from ..collection.commands.save_source_plan import save_source_plan
from .commands import (
    assess_integration,
    build_filesystem_scope,
    build_record_set,
    finalize_integration,
    save_field_review,
    save_integration_plan,
)


def _main() -> None:
    parser = argparse.ArgumentParser(description="DataGen Step 3 集成控制器")
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    save_source = commands.add_parser("save-source-plan")
    save_source.add_argument("--input", type=Path, required=True)
    download = commands.add_parser("download")
    download.add_argument("--url", required=True)
    download.add_argument("--source-id")
    download.add_argument("--output", required=True)
    download.add_argument("--format", dest="expected_format", default="any")
    download.add_argument("--timeout-seconds", type=int, default=240)
    batch = commands.add_parser("download-batch")
    batch.add_argument("--manifest", type=Path, required=True)
    batch.add_argument("--max-workers", type=int)
    save_plan = commands.add_parser("save-plan")
    save_plan.add_argument("--input", type=Path, required=True)
    build_records = commands.add_parser("build-record-set")
    build_records.add_argument("--record-set-id", required=True)
    build_records.add_argument("--script", type=Path, required=True)
    build_records.add_argument("--package-dir", type=Path)
    build_records.add_argument("--timeout-seconds", type=int, default=300)
    build_scope = commands.add_parser("build-scope")
    build_scope.add_argument("--scope-id", required=True)
    save_review = commands.add_parser("save-field-review")
    save_review.add_argument("--input", type=Path, required=True)
    commands.add_parser("assess")
    commands.add_parser("finalize")
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
    elif arguments.command == "save-plan":
        result = save_integration_plan(arguments.run_dir, input_path=arguments.input)
    elif arguments.command == "build-record-set":
        result = build_record_set(
            arguments.run_dir, record_set_id=arguments.record_set_id,
            script_path=arguments.script, package_directory=arguments.package_dir,
            timeout_seconds=arguments.timeout_seconds,
        )
    elif arguments.command == "build-scope":
        result = build_filesystem_scope(arguments.run_dir, scope_id=arguments.scope_id)
    elif arguments.command == "save-field-review":
        result = save_field_review(arguments.run_dir, input_path=arguments.input)
    elif arguments.command == "assess":
        result = assess_integration(arguments.run_dir)
    else:
        result = finalize_integration(arguments.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


main = _main

__all__ = ["_main", "main"]
