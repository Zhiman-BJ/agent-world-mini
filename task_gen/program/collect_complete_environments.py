"""Build a lightweight index of environments that published all requested tasks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _published_task_count(attempt: Path) -> int:
    tasks_path = attempt / "tasks.json"
    if not tasks_path.is_file():
        return 0
    value = _read_json(tasks_path)
    return len(value) if isinstance(value, list) else 0


def _complete_attempts(batch_roots: list[Path], task_count: int) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for batch_root in batch_roots:
        for validation_path in batch_root.glob(
            "semiconductor_*/attempt_*/step2_validation.json"
        ):
            try:
                validation = _read_json(validation_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            attempt = validation_path.parent
            if (
                validation.get("status") != "passed"
                or int(validation.get("accepted", 0)) < task_count
                or _published_task_count(attempt) < task_count
            ):
                continue
            environment_id = attempt.parent.name
            result.setdefault(environment_id, []).append(attempt.resolve())
    return result


def collect(
    *,
    batch_roots: list[Path],
    output_dir: Path,
    task_count: int,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    links_dir = output_dir / "environments"
    links_dir.mkdir(parents=True, exist_ok=True)
    grouped = _complete_attempts(
        [path.expanduser().resolve() for path in batch_roots],
        task_count,
    )
    records = []
    for environment_id, attempts in sorted(grouped.items()):
        selected = max(
            attempts,
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
        )
        link = links_dir / environment_id
        if link.is_symlink():
            if link.resolve() != selected:
                link.unlink()
                link.symlink_to(selected, target_is_directory=True)
        elif link.exists():
            raise FileExistsError(f"收集目录中存在非链接项：{link}")
        else:
            link.symlink_to(selected, target_is_directory=True)
        validation = _read_json(selected / "step2_validation.json")
        records.append({
            "environment_id": environment_id,
            "task_count": _published_task_count(selected),
            "status": validation["status"],
            "source_batch": selected.parent.parent.name,
            "source_attempt": str(selected),
            "link": f"environments/{environment_id}",
            "tasks_path": f"environments/{environment_id}/tasks.json",
            "validation_path": (
                f"environments/{environment_id}/step2_validation.json"
            ),
        })
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "validation_status": "passed",
            "minimum_accepted_tasks": task_count,
            "minimum_published_tasks": task_count,
        },
        "environment_count": len(records),
        "task_count": sum(item["task_count"] for item in records),
        "batch_roots": [str(path.expanduser().resolve()) for path in batch_roots],
        "environments": records,
    }
    _write_json(output_dir / "manifest.json", manifest)
    (output_dir / "README.md").write_text(
        "# 完整 10 任务环境集合\n\n"
        f"- 环境数：{manifest['environment_count']}\n"
        f"- 任务数：{manifest['task_count']}\n"
        f"- 刷新时间：{manifest['generated_at']}\n\n"
        "`environments/` 中的项是指向原始通过 attempt 的符号链接，"
        "不复制、不移动、不修改原批次数据。\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-count", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.task_count < 1:
        raise ValueError("--task-count 必须至少为 1")
    manifest = collect(
        batch_roots=arguments.batch_root,
        output_dir=arguments.output_dir,
        task_count=arguments.task_count,
    )
    print(json.dumps({
        "output_dir": str(arguments.output_dir.resolve()),
        "environment_count": manifest["environment_count"],
        "task_count": manifest["task_count"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
