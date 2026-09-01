"""Step 2 只读输入和程序文件的运行期完整性检查。"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable
from typing import Any

from ...common.constants import (
    COLLECTION_GUIDE_FILE,
    CONTROL_COLLECTION_LAUNCHER,
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    SCENARIO_RESEARCH_PATH,
)
from ...common.control_io import control_path, read_json
from ...common.workspace_files import file_sha256


class CollectionIntegrityError(RuntimeError):
    """Agent 修改或删除了本轮只读输入。"""


def _context_paths(value: Any) -> set[Path]:
    paths: set[Path] = set()
    if isinstance(value, dict):
        for child in value.values():
            paths.update(_context_paths(child))
    elif isinstance(value, list):
        for child in value:
            paths.update(_context_paths(child))
    elif isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute() and candidate.is_file():
            paths.add(candidate.resolve())
    return paths


def protected_input_snapshot(
    run_dir: Path,
    *,
    phase_control_files: Iterable[str] = (),
) -> dict[str, str]:
    """记录 Seed、协议、控制入口和 DataGen 程序文件的摘要。"""

    run_dir = run_dir.resolve()
    context_path = control_path(run_dir, CONTROL_RUN_CONFIG)
    context = read_json(context_path, "运行配置")
    paths = _context_paths(context)
    paths.update(
        {
            context_path,
            control_path(run_dir, CONTROL_SELECTED_SEED),
            control_path(run_dir, CONTROL_COLLECTION_LAUNCHER),
            control_path(run_dir, COLLECTION_GUIDE_FILE),
            run_dir / SCENARIO_RESEARCH_PATH,
        }
    )
    paths.update(control_path(run_dir, value) for value in phase_control_files)

    data_gen_root = Path(__file__).resolve().parents[3]
    paths.update(path.resolve() for path in data_gen_root.rglob("*.py") if path.is_file())
    paths.update(
        path.resolve()
        for path in (data_gen_root / "analysis/checkpoint_schemas").glob("*.json")
        if path.is_file()
    )
    return {
        str(path): file_sha256(path)
        for path in sorted(paths, key=lambda item: str(item))
        if path.is_file()
    }


def verify_protected_inputs(expected: dict[str, str]) -> None:
    """拒绝继续使用被 Agent 删除或改写的输入与程序文件。"""

    issues: list[str] = []
    for value, digest in expected.items():
        path = Path(value)
        if not path.is_file():
            issues.append(f"删除了只读文件：{path}")
        elif file_sha256(path) != digest:
            issues.append(f"修改了只读文件：{path}")
    if issues:
        raise CollectionIntegrityError("；".join(issues[:8]))


__all__ = [
    "CollectionIntegrityError",
    "protected_input_snapshot",
    "verify_protected_inputs",
]
