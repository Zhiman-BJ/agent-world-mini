"""Step 1 受控命令行分发。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .add_workspace_data import DERIVATION_TYPES, add_derived_file, add_entity_file
from .assess_workspace import assess_workspace
from .download_raw import (
    DOWNLOAD_FORMATS,
    DownloadFailure,
    download_raw_batch,
    download_raw_file,
)
from .finalize_collection import finalize_collection
from .save_source_plan import save_source_plan


def _main() -> None:
    parser = argparse.ArgumentParser(description="DataGen Step 1 控制器")
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    save_plan = commands.add_parser(
        "save-source-plan",
        help="校验并保存来源、需求覆盖和采集状态计划",
    )
    save_plan.add_argument("--input", type=Path, required=True)

    download = commands.add_parser("download", help="下载一个已登记的公开 Raw 文件")
    download.add_argument("--url", required=True)
    download.add_argument("--source-id", help="source_plan 中负责该 URL 的来源 ID")
    download.add_argument("--output", required=True, help="例如 raw/data.json")
    download.add_argument(
        "--format",
        dest="expected_format",
        choices=sorted(DOWNLOAD_FORMATS),
        default="any",
    )
    download.add_argument("--timeout-seconds", type=int, default=240)

    download_batch = commands.add_parser(
        "download-batch",
        help="按 manifest 并发下载多个互不相同的 Raw 文件",
    )
    download_batch.add_argument("--manifest", type=Path, required=True)
    download_batch.add_argument("--max-workers", type=int)

    add_entity = commands.add_parser(
        "add-entity",
        help="校验并加入规范 Entity 文件",
    )
    add_entity.add_argument("--input", type=Path, required=True)
    add_entity.add_argument("--output", required=True, help="entities/ 下的相对路径")
    add_entity.add_argument("--entity-name")
    add_entity.add_argument(
        "--source",
        action="append",
        default=[],
        help="直接来源的 Raw 文件；可重复传入",
    )

    add_derived = commands.add_parser(
        "add-derived",
        help="加入由 Raw/Entity 确定性得到的 Derived 文件",
    )
    add_derived.add_argument("--input", type=Path, required=True)
    add_derived.add_argument("--output", required=True, help="derived/ 下的相对路径")
    add_derived.add_argument(
        "--source",
        action="append",
        default=[],
        help="直接来源的 Raw 或 Entity 文件；可重复传入",
    )
    add_derived.add_argument(
        "--derivation",
        required=True,
        choices=sorted(DERIVATION_TYPES),
    )

    commands.add_parser("assess", help="重算数据事实、质量缺口和下一步动作")
    finalize = commands.add_parser("finalize", help="校验并结束 Step 1")
    finalize.add_argument(
        "--result",
        required=True,
        choices=["complete", "exhausted", "insufficient_public_data"],
    )

    arguments = parser.parse_args()
    if arguments.command == "save-source-plan":
        result = save_source_plan(arguments.run_dir, input_path=arguments.input)
    elif arguments.command == "download":
        if arguments.timeout_seconds <= 0:
            parser.error("--timeout-seconds 必须大于 0")
        try:
            result = download_raw_file(
                arguments.run_dir,
                url=arguments.url,
                output=arguments.output,
                expected_format=arguments.expected_format,
                timeout_seconds=arguments.timeout_seconds,
                source_id=arguments.source_id,
            )
        except DownloadFailure as error:
            print(json.dumps(error.to_dict(), ensure_ascii=False, indent=2))
            raise SystemExit(2) from error
    elif arguments.command == "download-batch":
        try:
            manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            parser.error(f"--manifest 无法读取：{error}")
        items = manifest.get("items") if isinstance(manifest, dict) else manifest
        if not isinstance(items, list):
            parser.error("--manifest 根节点必须是数组，或包含 items 数组的对象")
        if arguments.max_workers is not None and arguments.max_workers <= 0:
            parser.error("--max-workers 必须大于 0")
        result = download_raw_batch(
            arguments.run_dir,
            items=items,
            max_workers=arguments.max_workers,
        )
    elif arguments.command == "add-entity":
        result = add_entity_file(
            arguments.run_dir,
            input_path=arguments.input,
            output=arguments.output,
            source_files=arguments.source,
            entity_name=arguments.entity_name,
        )
    elif arguments.command == "add-derived":
        result = add_derived_file(
            arguments.run_dir,
            input_path=arguments.input,
            output=arguments.output,
            source_files=arguments.source,
            derivation_type=arguments.derivation,
        )
    elif arguments.command == "assess":
        result = assess_workspace(arguments.run_dir)
    else:
        result = finalize_collection(arguments.run_dir, result=arguments.result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()


main = _main

__all__ = ["_main", "main"]
