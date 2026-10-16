#!/usr/bin/env python3
"""Create a portable path view of verified PASS trajectories."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterator



from distill.visualize import render_combined_visualization



_CWD_DECLARATION = re.compile(r"The current working directory is `(/[^`\n]+)`")
_SESSION_MAIN = re.compile(
    r"/tmp/agent-world-kimi-home-[^/\s`\"']+/sessions/"
    r"[^/\s`\"']+/session_[^/\s`\"']+/agents/main"
)
_SESSION = re.compile(
    r"/tmp/agent-world-kimi-home-[^/\s`\"']+/sessions/"
    r"[^/\s`\"']+/session_[^/\s`\"']+"
)
_KIMI_HOME = re.compile(r"/tmp/agent-world-kimi-home-[^/\s`\"']+")
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_./:@+~-]+)")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            yield value


def first_request(source_directory: Path) -> dict[str, Any]:
    path = source_directory / "raw" / "model_requests.jsonl"
    for record in jsonl(path):
        if isinstance(record.get("request"), dict):
            return record
    raise ValueError(f"no model request in {path}")


def request_cwd(request_record: dict[str, Any]) -> str:
    request = request_record["request"]
    matches = set()
    for message in request.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        matches.update(match.group(1).rstrip("/") for match in _CWD_DECLARATION.finditer(content))
    if len(matches) != 1:
        raise ValueError(f"expected one current working directory, found {sorted(matches)}")
    return matches.pop()


class PathCleaner:
    def __init__(self, case: str, source_directory: Path, cwd: str):
        if not case or "/" in case or case in {".", ".."}:
            raise ValueError(f"unsafe case name: {case!r}")
        self.case = case
        self.source_directory = str(source_directory).rstrip("/")
        self.cwd = cwd.rstrip("/")
        self.workspace = f"/{case}/model-workspace"
        self.task_root = f"/{case}"
        self.tmp_root = f"/tmp/{case}"
        self.replacements: Counter[str] = Counter()

    def text(self, value: str) -> str:
        original = value

        # Replace the deepest paths first so a parent replacement cannot hide them.
        for source, target, label in (
            (self.cwd, self.workspace, "workspace"),
            (self.source_directory, self.task_root, "task_root"),
        ):
            count = value.count(source)
            if count:
                value = value.replace(source, target)
                self.replacements[label] += count

        value, count = _SESSION_MAIN.subn(self.tmp_root, value)
        self.replacements["session_main"] += count
        value, count = _SESSION.subn(self.tmp_root, value)
        self.replacements["session"] += count
        value, count = _KIMI_HOME.subn(self.tmp_root, value)
        self.replacements["kimi_home"] += count

        if value != original:
            self.replacements["strings_changed"] += 1
        return value

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        return value


def model_visible_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from model_visible_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from model_visible_strings(item)


def unresolved_paths(value: Any, case: str) -> list[str]:
    allowed_roots = (f"/{case}", f"/tmp/{case}")
    unresolved = set()
    for text in model_visible_strings(value):
        for match in _ABSOLUTE_PATH.finditer(text):
            path = match.group(1).rstrip(".,;:)]}")
            if path.startswith(allowed_roots):
                continue
            if path.startswith(("/data1/", "/tmp/agent-world-kimi-home-")):
                unresolved.add(path)
    return sorted(unresolved)


def cleaned_request_record(
    record: dict[str, Any], cleaner: PathCleaner,
) -> dict[str, Any]:
    result = {
        "trajectory_id": None,
        "request_id": record.get("request_id"),
        "recorded_at": record.get("recorded_at"),
        "source": {
            "path": record.get("path") or record.get("raw_path"),
            "sha256": record.get("sha256"),
        },
        "request": cleaner.value(deepcopy(record["request"])),
    }
    encoded = json.dumps(
        result["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    result["cleaned_request_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


def write_jsonl_line(stream: Any, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def choose_samples(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priorities = (
        "compaction_wire",
        "absolute_workspace",
        "tool_result",
        "mixed_absolute_relative",
        "relative_only",
    )
    selected: list[dict[str, Any]] = []
    used = set()
    for label in priorities:
        eligible = [
            row for row in candidates
            if label in row["features"] and row["trajectory_bytes"] <= 8_000_000
            and row["trajectory_id"] not in used
        ]
        if not eligible:
            continue
        row = sorted(eligible, key=lambda item: (item["trajectory_bytes"], item["case"]))[0]
        row = {**row, "sample_reason": label}
        selected.append(row)
        used.add(row["trajectory_id"])
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    output.mkdir(parents=True)

    trajectory_output = output / "trajectories.jsonl"
    request_output = output / "initial_requests.jsonl"
    mapping_output = output / "path_mappings.jsonl"
    sample_candidates: dict[str, list[dict[str, Any]]] = {}
    cleaners: dict[str, PathCleaner] = {}
    stats = Counter()

    with (
        trajectory_output.open("w", encoding="utf-8") as trajectory_stream,
        request_output.open("w", encoding="utf-8") as request_stream,
        mapping_output.open("w", encoding="utf-8") as mapping_stream,
    ):
        for record in jsonl(source / "trajectories.jsonl"):
            verification = record.get("verification")
            if not (
                record.get("canonical") is True
                and isinstance(verification, dict)
                and verification.get("outcome") == "pass"
            ):
                continue
            trajectory = record.get("trajectory")
            if not isinstance(trajectory, dict):
                raise ValueError(f"missing trajectory for {record.get('trajectory_id')}")
            case = str(record["case"])
            source_path = Path(record["trajectory_path"])
            request_record = first_request(source_path.parent)
            cwd = request_cwd(request_record)
            cleaner = PathCleaner(case, source_path.parent, cwd)
            cleaned_trajectory = cleaner.value(deepcopy(trajectory))
            cleaned_request = cleaned_request_record(request_record, cleaner)
            cleaned_request["trajectory_id"] = record["trajectory_id"]

            flags = unresolved_paths(cleaned_trajectory, case)
            flags.extend(unresolved_paths(cleaned_request["request"], case))
            flags = sorted(set(flags))

            cleaned_record = deepcopy(record)
            cleaned_record["trajectory"] = cleaned_trajectory
            cleaned_record["path_cleaning"] = {
                "policy": "host_and_session_prefixes_only",
                "workspace": cleaner.workspace,
                "temporary_root": cleaner.tmp_root,
                "unresolved_host_paths": flags,
                "training_path_clean": not flags,
            }
            write_jsonl_line(trajectory_stream, cleaned_record)
            write_jsonl_line(request_stream, cleaned_request)
            write_jsonl_line(mapping_stream, {
                "trajectory_id": record["trajectory_id"],
                "case": case,
                "source_workspace": cwd,
                "cleaned_workspace": cleaner.workspace,
                "source_task_directory": str(source_path.parent),
                "cleaned_task_root": cleaner.task_root,
                "cleaned_temporary_root": cleaner.tmp_root,
                "replacement_counts": dict(cleaner.replacements),
                "unresolved_host_paths": flags,
            })
            cleaners[record["trajectory_id"]] = cleaner

            calls = [
                call
                for step in cleaned_trajectory.get("steps", [])
                for call in step.get("tool_calls", [])
                if isinstance(call, dict) and call.get("name") in {"Read", "Grep", "Glob"}
            ]
            paths = [
                call.get("arguments", {}).get("path")
                for call in calls
                if isinstance(call.get("arguments"), dict)
                and isinstance(call.get("arguments", {}).get("path"), str)
            ]
            features = set()
            if any(path.startswith(cleaner.workspace) for path in paths):
                features.add("absolute_workspace")
            if any(path.startswith(cleaner.tmp_root + "/tool-results/") for path in paths):
                features.add("tool_result")
            if any(path == cleaner.tmp_root + "/wire.jsonl" for path in paths):
                features.add("compaction_wire")
            has_absolute = any(path.startswith("/") for path in paths)
            has_relative = any(not path.startswith("/") for path in paths)
            if has_absolute and has_relative:
                features.add("mixed_absolute_relative")
            if has_relative and not has_absolute:
                features.add("relative_only")
            candidate = {
                "trajectory_id": record["trajectory_id"],
                "case": case,
                "features": sorted(features),
                "trajectory": cleaned_trajectory,
                "input": cleaned_request,
                "trajectory_bytes": len(json.dumps(cleaned_trajectory, ensure_ascii=False).encode()),
            }
            if candidate["trajectory_bytes"] <= 8_000_000:
                for feature in features:
                    rows = sample_candidates.setdefault(feature, [])
                    rows.append(candidate)
                    rows.sort(key=lambda item: (item["trajectory_bytes"], item["case"]))
                    del rows[5:]
            stats["trajectories"] += 1
            stats["training_path_clean"] += not flags
            stats["flagged_unresolved_host_paths"] += bool(flags)
            stats.update(cleaner.replacements)
            if stats["trajectories"] % 100 == 0:
                print(f"cleaned {stats['trajectories']} PASS trajectories", flush=True)
            if args.limit is not None and stats["trajectories"] >= args.limit:
                break

    compaction_output = output / "post_compaction_contexts.jsonl"
    with compaction_output.open("w", encoding="utf-8") as stream:
        for record in jsonl(source / "post_compaction_contexts.jsonl"):
            trajectory_id = record.get("trajectory_id")
            cleaner = cleaners.get(trajectory_id)
            if cleaner is None:
                continue
            cleaned = cleaner.value(deepcopy(record))
            write_jsonl_line(stream, cleaned)
            stats["post_compaction_contexts"] += 1

    candidate_rows = list({
        row["trajectory_id"]: row
        for rows in sample_candidates.values()
        for row in rows
    }.values())
    samples = choose_samples(candidate_rows)
    visualization = output / "visualization_sample"
    visualization.mkdir()
    summary_results = []
    sample_manifest = []
    for row in samples:
        case_directory = visualization / row["case"]
        case_directory.mkdir()
        dump_json(case_directory / "trajectory.json", row["trajectory"])
        dump_json(case_directory / "input.json", row["input"])
        summary_results.append({"case": row["case"], "status": "completed"})
        sample_manifest.append({
            key: value for key, value in row.items()
            if key not in {"trajectory", "input"}
        })
    dump_json(visualization / "summary.json", {"results": summary_results})
    dump_json(visualization / "sample_manifest.json", {"samples": sample_manifest})
    render_combined_visualization(visualization)

    manifest = {
        "schema_version": "agent_world.path_cleaning.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "policy": {
            "scope": "canonical verifier PASS trajectories",
            "relative_paths": "preserved unchanged",
            "workspace_absolute_paths": "/<actual-case>/model-workspace/...",
            "long_tool_results": "/tmp/<actual-case>/tool-results/<original-filename>",
            "compaction_wire": "/tmp/<actual-case>/wire.jsonl",
            "file_names_and_result_ids": "preserved unchanged",
            "external_system_paths": "preserved and flagged; never rewritten into valid workspace paths",
            "non_path_content": "unchanged",
        },
        "counts": dict(stats),
        "files": {
            "trajectories": trajectory_output.name,
            "initial_requests": request_output.name,
            "post_compaction_contexts": compaction_output.name,
            "path_mappings": mapping_output.name,
            "visualization": "visualization_sample/combined.html",
        },
        "sample_count": len(samples),
    }
    dump_json(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        """# 路径清洗数据

本目录只清洗路径中的宿主机、批次和随机会话信息。源数据未修改。

- 相对路径保持不变。
- 工作区绝对路径映射为 `/<实际任务名>/model-workspace/...`。
- 长工具结果映射为 `/tmp/<实际任务名>/tool-results/...`。
- 压缩恢复日志映射为 `/tmp/<实际任务名>/wire.jsonl`。
- 文件名、结果 ID、工具调用、推理和回答均不因本步骤改写。
- 无法安全归属于任务的宿主机路径保持原样，并在 `path_mappings.jsonl` 中标记。
- `trajectory_path`、verifier verdict 等审计指针仍指向原始证据，不作为模型输入。
""",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
