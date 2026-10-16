#!/usr/bin/env python3
"""Update only cleaned trajectory input.json files with repaired tool descriptions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill.clean import apply_repaired_tool_definitions





def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_dump(path: Path, value: Any) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def delivery_index(
    roots: list[Path],
) -> dict[str, list[tuple[Path, list[dict[str, Any]]]]]:
    result = {}
    for root in roots:
        for environment_dir in sorted((root / "environments").iterdir()):
            environment_path = environment_dir / "environment" / "environment.json"
            tools_path = environment_dir / "tools" / "tools_repaired.json"
            if not environment_path.is_file() or not tools_path.is_file():
                continue
            environment_id = load(environment_path).get("environment_id")
            tools = load(tools_path).get("tools")
            if not isinstance(environment_id, str) or not isinstance(tools, list):
                raise ValueError(f"invalid repaired delivery: {environment_dir}")
            result.setdefault(environment_id, []).append((tools_path, tools))
    return result


def update_case(
    case_dir: Path,
    index: dict[str, list[tuple[Path, list[dict[str, Any]]]]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    trajectory_path = case_dir / "trajectory.json"
    input_path = case_dir / "input.json"
    trajectory = load(trajectory_path)
    payload = load(input_path)
    request = payload.get("request")
    if not isinstance(request, dict):
        raise ValueError(f"{input_path} has no request object")
    environment = trajectory.get("environment", {})
    environment_id = environment.get("environment_id")
    expected_names = {
        str(name) for name in environment.get("tool_names", []) if isinstance(name, str)
    }
    candidates = [
        entry for entry in index.get(environment_id, [])
        if {str(tool.get("name")) for tool in entry[1]} == expected_names
    ]
    if len(candidates) != 1:
        raise ValueError(
            "expected one repaired delivery matching environment_id and tool names: "
            f"environment_id={environment_id}, matches={len(candidates)}"
        )
    tools_path, tools = candidates[0]
    before = json.loads(json.dumps(payload, ensure_ascii=False))
    replaced = apply_repaired_tool_definitions(
        request, tools, expected_names=expected_names
    )
    payload["sha256"] = canonical_hash(request)

    before_without_tools = json.loads(json.dumps(before, ensure_ascii=False))
    after_without_tools = json.loads(json.dumps(payload, ensure_ascii=False))
    before_without_tools["request"].pop("tools", None)
    after_without_tools["request"].pop("tools", None)
    before_without_tools.pop("sha256", None)
    after_without_tools.pop("sha256", None)
    if before_without_tools != after_without_tools:
        raise ValueError(f"non-tool cleaned input changed: {input_path}")
    if not dry_run:
        atomic_dump(input_path, payload)
    return {
        "case": case_dir.name,
        "environment_id": environment_id,
        "tools_replaced": len(replaced),
        "input_changed": before != payload,
        "tools_source": str(tools_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cleaned_root", type=Path, nargs="+")
    parser.add_argument("--delivery-root", type=Path, action="append", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    roots = args.delivery_root
    index = delivery_index(roots)
    records = []
    for cleaned_root in args.cleaned_root:
        trajectories = cleaned_root.expanduser().resolve() / "trajectories"
        if not trajectories.is_dir():
            raise SystemExit(f"missing trajectories directory: {trajectories}")
        for case_dir in sorted(path for path in trajectories.iterdir() if path.is_dir()):
            records.append(update_case(case_dir, index, dry_run=args.dry_run))
    report = {
        "cleaned_roots": [str(path.expanduser().resolve()) for path in args.cleaned_root],
        "dry_run": args.dry_run,
        "counts": {
            "trajectories": len(records),
            "changed": sum(record["input_changed"] for record in records),
            "tool_definitions": sum(record["tools_replaced"] for record in records),
        },
        "records": records,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        atomic_dump(args.report, report)
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
