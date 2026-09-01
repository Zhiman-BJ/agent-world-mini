"""把规范 Entity 或可复算 Derived 文件加入正式 workspace。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from env_gen.data_gen.analysis.structured_io import (
    StructuredDataError,
    entity_format,
    entity_name_from_path,
    read_entity_groups,
    validate_entity_groups,
)

from ...common.constants import CONTROL_DATA_FILE_RECEIPTS
from ...common.control_io import control_path, read_json, write_json
from ...common.workspace_files import file_sha256, workspace_files


DERIVATION_TYPES = {"extract", "convert", "aggregate"}


def _data_target(run_dir: Path, data_type: str, output: str) -> tuple[Path, str]:
    relative = Path(output)
    expected_root = "entities" if data_type == "entity" else "derived"
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("--output 必须是 workspace 下的安全相对路径")
    if relative.parts and relative.parts[0] == "workspace":
        relative = Path(*relative.parts[1:])
    if not relative.parts or relative.parts[0] != expected_root:
        raise RuntimeError(
            f"{data_type} 输出必须位于 workspace/{expected_root}/"
        )
    workspace = (run_dir / "workspace").resolve()
    target = (workspace / relative).resolve()
    try:
        target.relative_to((workspace / expected_root).resolve())
    except ValueError as error:
        raise RuntimeError("输出路径不能离开对应 workspace 目录") from error
    return target, target.relative_to(workspace).as_posix()


def _validate_entity_file(
    path: Path,
    *,
    entity_name: str | None = None,
) -> dict[str, int]:
    try:
        groups = read_entity_groups(path, entity_name=entity_name)
        return validate_entity_groups(groups)
    except StructuredDataError as error:
        raise RuntimeError(f"Entity 文件不符合 canonical 契约：{error}") from error


def _source_file_records(
    run_dir: Path,
    *,
    data_type: str,
    source_files: list[str],
) -> list[dict[str, str]]:
    workspace = (run_dir / "workspace").resolve()
    allowed_roots = {"raw"} if data_type == "entity" else {"raw", "entities"}
    records: list[dict[str, str]] = []
    for value in source_files:
        relative = Path(value)
        if relative.parts and relative.parts[0] == "workspace":
            relative = Path(*relative.parts[1:])
        if (
            not relative.parts
            or relative.parts[0] not in allowed_roots
            or ".." in relative.parts
        ):
            roots = "/".join(sorted(allowed_roots))
            raise RuntimeError(f"--source 必须引用 workspace/{roots} 中的文件：{value}")
        source = (workspace / relative).resolve()
        if not source.is_file():
            raise RuntimeError(f"--source 文件不存在：{relative.as_posix()}")
        records.append(
            {
                "path": relative.as_posix(),
                "sha256": file_sha256(source),
            }
        )
    if not records:
        raise RuntimeError("至少需要一个 --source，保证新增文件可回溯")
    if len({item["path"] for item in records}) != len(records):
        raise RuntimeError("--source 不能重复引用同一文件")
    return records


def _record_data_file_receipt(
    run_dir: Path,
    *,
    data_type: str,
    relative_path: str,
    target: Path,
    source_files: list[dict[str, str]],
    derivation_type: str | None,
) -> None:
    path = control_path(run_dir, CONTROL_DATA_FILE_RECEIPTS)
    payload = (
        read_json(path, "数据文件收据")
        if path.is_file()
        else {"schema_version": "1.0", "files": []}
    )
    files = [
        item
        for item in payload.get("files", [])
        if isinstance(item, dict) and item.get("path") != relative_path
    ]
    files.append(
        {
            "data_type": data_type,
            "path": relative_path,
            "source_files": source_files,
            "derivation_type": derivation_type,
            "bytes": target.stat().st_size,
            "sha256": file_sha256(target),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    payload["files"] = files
    write_json(path, payload)


def _add_workspace_file(
    run_dir: Path,
    *,
    data_type: str,
    input_path: Path,
    output: str,
    source_files: list[str],
    entity_name: str | None = None,
    derivation_type: str | None = None,
) -> dict[str, Any]:
    """校验临时文件并原子加入 entities/derived。"""

    run_dir = run_dir.resolve()
    input_path = input_path.resolve()
    if data_type not in {"entity", "derived"}:
        raise RuntimeError(f"不支持的数据类型：{data_type}")
    if not input_path.is_file() or input_path.stat().st_size == 0:
        raise RuntimeError(f"输入文件不存在或为空：{input_path}")
    if input_path.suffix.lower() in {
        ".py", ".js", ".ts", ".sh", ".sql", ".ipynb", ".exe", ".so"
    }:
        raise RuntimeError("不能把程序文件作为 Entity/Derived 业务数据")
    if data_type == "derived" and derivation_type not in DERIVATION_TYPES:
        raise RuntimeError(
            "add-derived 必须使用 --derivation extract|convert|aggregate"
        )
    if data_type == "entity" and derivation_type is not None:
        raise RuntimeError("Entity 文件不能声明 derivation_type")

    source_records = _source_file_records(
        run_dir,
        data_type=data_type,
        source_files=source_files,
    )
    input_format: str | None = None
    if data_type == "entity":
        try:
            input_format = entity_format(input_path)
        except StructuredDataError as error:
            raise RuntimeError(str(error)) from error
        if input_format in {"jsonl", "csv", "parquet"}:
            if not entity_name:
                raise RuntimeError(
                    f"{input_format} Entity 必须通过 --entity-name 指定唯一实体类型"
                )
            try:
                output_name = entity_name_from_path(Path(output))
            except StructuredDataError as error:
                raise RuntimeError(str(error)) from error
            if output_name != entity_name:
                raise RuntimeError(
                    "单实体文件的输出文件名必须与 --entity-name 一致："
                    f"{output_name} != {entity_name}"
                )
        try:
            output_format = entity_format(Path(output))
        except StructuredDataError as error:
            raise RuntimeError(str(error)) from error
        if output_format != input_format:
            raise RuntimeError(
                f"add-entity 不转换格式：输入是 {input_format}，输出必须使用相同格式"
            )

    entity_counts = (
        _validate_entity_file(input_path, entity_name=entity_name)
        if data_type == "entity"
        else {}
    )
    if data_type == "derived" and input_path.suffix.lower() == ".json":
        try:
            json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Derived JSON 不完整：{error}") from error

    workspace = run_dir / "workspace"
    target, relative_path = _data_target(run_dir, data_type, output)
    input_sha256 = file_sha256(input_path)
    duplicate_paths = [
        relative
        for bucket in ("entities", "derived")
        for relative in workspace_files(run_dir)[bucket]
        if relative != relative_path
        and file_sha256(workspace / relative) == input_sha256
    ]
    if duplicate_paths:
        raise RuntimeError(
            "待加入内容与现有业务文件完全相同："
            + ", ".join(duplicate_paths)
            + "；修订 canonical 文件时应覆盖原路径，不创建 v2/copy/mirror 副本"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.add-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copyfile(input_path, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    _record_data_file_receipt(
        run_dir,
        data_type=data_type,
        relative_path=relative_path,
        target=target,
        source_files=source_records,
        derivation_type=derivation_type,
    )
    return {
        "status": "added",
        "data_type": data_type,
        "path": relative_path,
        "bytes": target.stat().st_size,
        "entity_counts": entity_counts,
        "format": input_format or input_path.suffix.lower().lstrip("."),
        "source_files": source_records,
        "derivation_type": derivation_type,
    }


def add_entity_file(
    run_dir: Path,
    *,
    input_path: Path,
    output: str,
    source_files: list[str],
    entity_name: str | None = None,
) -> dict[str, Any]:
    return _add_workspace_file(
        run_dir,
        data_type="entity",
        input_path=input_path,
        output=output,
        source_files=source_files,
        entity_name=entity_name,
    )


def add_derived_file(
    run_dir: Path,
    *,
    input_path: Path,
    output: str,
    source_files: list[str],
    derivation_type: str,
) -> dict[str, Any]:
    return _add_workspace_file(
        run_dir,
        data_type="derived",
        input_path=input_path,
        output=output,
        source_files=source_files,
        derivation_type=derivation_type,
    )


def data_file_receipt_issues(run_dir: Path) -> list[dict[str, str]]:
    """拒绝绕过 add-entity/add-derived 写入正式业务文件。"""

    receipt_path = control_path(run_dir, CONTROL_DATA_FILE_RECEIPTS)
    receipts: dict[str, dict[str, Any]] = {}
    if receipt_path.is_file():
        payload = read_json(receipt_path, "数据文件收据")
        receipts = {
            str(item.get("path")): item
            for item in payload.get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
    workspace = run_dir / "workspace"
    issues: list[dict[str, str]] = []
    files = workspace_files(run_dir)
    for relative in files["entities"] + files["derived"]:
        receipt = receipts.get(relative)
        expected_type = "entity" if relative.startswith("entities/") else "derived"
        if receipt is None:
            issues.append(
                {
                    "code": "workspace_data_file_not_added",
                    "path": relative,
                    "message": (
                        "正式业务文件必须通过 datagenctl add-entity 或 add-derived 加入："
                        f"{relative}"
                    ),
                }
            )
            continue
        if receipt.get("data_type") != expected_type:
            issues.append(
                {
                    "code": "data_file_receipt_type_mismatch",
                    "path": relative,
                    "message": f"数据文件收据类型与目录不一致：{receipt.get('data_type')}",
                }
            )
        if receipt.get("sha256") != file_sha256(workspace / relative):
            issues.append(
                {
                    "code": "added_data_file_modified",
                    "path": relative,
                    "message": f"文件加入后被直接修改；请重新执行对应 add 命令：{relative}",
                }
            )
        if expected_type == "derived" and receipt.get("derivation_type") not in DERIVATION_TYPES:
            issues.append(
                {
                    "code": "derived_file_without_derivation_type",
                    "path": relative,
                    "message": "Derived 文件必须声明 extract、convert 或 aggregate",
                }
            )
        for source in receipt.get("source_files", []):
            if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                issues.append(
                    {
                        "code": "invalid_data_file_source_receipt",
                        "path": relative,
                        "message": "数据文件收据中的 source_files 结构无效",
                    }
                )
                continue
            source_path = workspace / str(source["path"])
            if not source_path.is_file():
                issues.append(
                    {
                        "code": "data_file_source_missing",
                        "path": relative,
                        "message": f"数据文件来源不存在：{source['path']}",
                    }
                )
            elif source.get("sha256") != file_sha256(source_path):
                issues.append(
                    {
                        "code": "data_file_source_modified",
                        "path": relative,
                        "message": f"数据文件来源 SHA-256 已变化：{source['path']}",
                    }
                )

    by_sha256: dict[str, list[str]] = {}
    for relative in files["entities"] + files["derived"]:
        by_sha256.setdefault(file_sha256(workspace / relative), []).append(relative)
    for duplicates in by_sha256.values():
        if len(duplicates) > 1:
            issues.append(
                {
                    "code": "duplicate_business_file_content",
                    "path": duplicates[0],
                    "message": (
                        "多个业务文件内容完全相同："
                        + ", ".join(duplicates)
                        + "；只保留一个 canonical 输出路径"
                    ),
                }
            )

    available_file_targets = set(files["raw"] + files["derived"])
    for relative in files["entities"]:
        path = workspace / relative
        try:
            format_name = entity_format(path)
            entity_name = (
                entity_name_from_path(path)
                if format_name in {"jsonl", "csv", "parquet"}
                else None
            )
            groups = read_entity_groups(path, entity_name=entity_name)
        except StructuredDataError as error:
            issues.append(
                {
                    "code": "invalid_added_entity_file",
                    "path": relative,
                    "message": str(error),
                }
            )
            continue
        for entity_type, records in groups.items():
            for index, record in enumerate(records):
                for field, value in record.items():
                    if field != "file_path" and not field.endswith("_file_path"):
                        continue
                    reference = PurePosixPath(str(value))
                    normalized = reference.as_posix()
                    if (
                        reference.is_absolute()
                        or not reference.parts
                        or any(part in {"", ".", ".."} for part in reference.parts)
                        or reference.parts[0] not in {"raw", "derived"}
                    ):
                        issues.append(
                            {
                                "code": "unsafe_entity_file_reference",
                                "path": f"{relative}:{entity_type}[{index}].{field}",
                                "message": (
                                    "文件引用必须是 workspace/raw 或 workspace/derived 下的"
                                    f"安全相对路径：{value}"
                                ),
                            }
                        )
                    elif normalized not in available_file_targets:
                        issues.append(
                            {
                                "code": "missing_entity_file_reference",
                                "path": f"{relative}:{entity_type}[{index}].{field}",
                                "message": f"文件索引引用的业务文件不存在：{normalized}",
                            }
                        )
    return issues


__all__ = [
    "DERIVATION_TYPES",
    "add_derived_file",
    "add_entity_file",
    "data_file_receipt_issues",
]
