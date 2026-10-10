#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import argparse
import shutil
import sys

REPO = Path("/data1/home/sunshuo/AgenticDataGeneration/agent-world-mini")
BATCH = Path("/data1/agent_world/trajectory/semiconductor_kimi_k3_20260930")
sys.path.insert(0, str(REPO))
from distill.clean import MCP_PREFIX, clean_trajectory, first_request_record_from_source
from distill.export import validate_trajectory
from distill.visualize import render_combined_visualization


DELIVERY_ROOTS = (
    Path("/data1/agent_world/toolgen_environments/delivery"),
    Path("/data1/agent_world/toolgen_semiconductor_expanded/delivery"),
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repaired_tools_by_environment_id() -> dict[str, list[tuple[Path, list[dict]]]]:
    result = {}
    for delivery_root in DELIVERY_ROOTS:
        for environment_dir in sorted((delivery_root / "environments").iterdir()):
            environment_path = environment_dir / "environment" / "environment.json"
            tools_path = environment_dir / "tools" / "tools_repaired.json"
            if not environment_path.is_file() or not tools_path.is_file():
                continue
            environment_id = load(environment_path).get("environment_id")
            tools = load(tools_path).get("tools")
            if not isinstance(environment_id, str) or not isinstance(tools, list):
                raise SystemExit(f"invalid repaired delivery: {environment_dir}")
            result.setdefault(environment_id, []).append((tools_path, tools))
    return result


parser = argparse.ArgumentParser()
parser.add_argument("--descriptions", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--selection", type=Path)
parser.add_argument("--allow-non-model-descriptions", action="store_true")
parser.add_argument("--task-text-only", action="store_true")
args = parser.parse_args()
OUTPUT = args.output.expanduser().resolve()

manifest = load(BATCH / "manifest.json")
rows = list(manifest["accepted"])
recovery_pass = {row["case"] for row in manifest["recovery_verifier"]["pass"]}
rows.extend(row for row in manifest["pending_verifier"] if row["case"] in recovery_pass)
rows_by_case = {row["case"]: row for row in rows}
expected_count = manifest.get("counts", {}).get("accepted_total", len(rows_by_case))
if len(rows_by_case) != expected_count:
    raise SystemExit(
        f"manifest accepted_total={expected_count}, but resolved {len(rows_by_case)} cases"
    )
if args.task_text_only:
    if args.descriptions is not None:
        raise SystemExit("--task-text-only cannot be combined with --descriptions")
    descriptions = None
    provenance = "not_applicable_task_text_only"
else:
    if args.descriptions is None:
        raise SystemExit("--descriptions is required unless --task-text-only is used")
    description_payload = load(args.descriptions)
    descriptions = description_payload.get("descriptions")
    if not isinstance(descriptions, dict):
        raise SystemExit("descriptions file must contain an object named descriptions")
    provenance = description_payload.get("provenance")
    if provenance != "model_generated" and not args.allow_non_model_descriptions:
        raise SystemExit(
            "formal cleaning requires provenance=model_generated; "
            "use --allow-non-model-descriptions only for an explicitly labeled smoke test"
        )
if args.selection:
    selection = load(args.selection)
    selection_rows = selection.get("samples", selection.get("records", []))
    selected_cases = [row["case"] for row in selection_rows]
else:
    selected_cases = sorted(rows_by_case)
unknown = sorted(set(selected_cases) - set(rows_by_case))
if unknown:
    raise SystemExit(f"selection contains unknown cases: {unknown[:5]}")
if descriptions is not None:
    missing = sorted(set(selected_cases) - set(descriptions))
    if missing:
        raise SystemExit(
            f"missing model-generated descriptions for {len(missing)} cases: {missing[:5]}"
        )
repaired_by_environment = repaired_tools_by_environment_id()
if OUTPUT.exists():
    shutil.rmtree(OUTPUT)
(OUTPUT / "trajectories").mkdir(parents=True)

records = []
totals = {
    "source_trajectory_bytes": 0,
    "cleaned_trajectory_bytes": 0,
    "source_prompt_chars": 0,
    "cleaned_prompt_chars": 0,
    "source_environment_chars": 0,
    "cleaned_environment_chars": 0,
    "tool_calls": 0,
    "prefixed_tool_calls_before": 0,
    "prefixed_tool_calls_after": 0,
    "undefined_tool_calls": 0,
}
for index, case in enumerate(selected_cases, 1):
    row = rows_by_case[case]
    source_dir = Path(row["source"])
    source_path = source_dir / "trajectory.json"
    source = load(source_path)
    environment_id = source.get("environment", {}).get("environment_id")
    expected_tool_names = set(source.get("environment", {}).get("tool_names", []))
    repaired_candidates = [
        entry for entry in repaired_by_environment.get(environment_id, [])
        if {tool.get("name") for tool in entry[1]} == expected_tool_names
    ]
    if len(repaired_candidates) != 1:
        raise SystemExit(
            f"expected one matching tools_repaired.json for {case}: "
            f"environment_id={environment_id}, matches={len(repaired_candidates)}"
        )
    repaired_path, repaired_tools = repaired_candidates[0]
    request_record = first_request_record_from_source(source_dir)
    cleaned, cleaned_input = clean_trajectory(
        source,
        request_record,
        source=str(source_path),
        environment_context=descriptions[case] if descriptions is not None else None,
        workspace_path=f"/{case}/model-workspace",
        tool_definitions=repaired_tools,
    )
    validate_trajectory(cleaned)
    case_dir = OUTPUT / "trajectories" / case
    case_dir.mkdir(parents=True)
    cleaned_path = case_dir / "trajectory.json"
    input_path = case_dir / "input.json"
    dump(cleaned_path, cleaned)
    dump(input_path, cleaned_input)
    (case_dir / "source").symlink_to(source_dir, target_is_directory=True)
    raw_evidence = source_dir / "raw"
    if raw_evidence.is_dir():
        (case_dir / "raw").symlink_to(raw_evidence, target_is_directory=True)

    source_prompt = source["task"]["prompt"]
    cleaned_prompt = cleaned["task"]["prompt"]
    source_environment_chars = len(json.dumps(source["environment"], ensure_ascii=False))
    cleaned_environment_chars = len(json.dumps(cleaned["environment"], ensure_ascii=False))
    source_names = [
        call["name"] for step in source["steps"] for call in step.get("tool_calls", [])
    ]
    cleaned_names = [
        call["name"] for step in cleaned["steps"] for call in step.get("tool_calls", [])
    ]
    defined_names = {
        tool.get("function", {}).get("name")
        for tool in cleaned_input.get("request", {}).get("tools", [])
        if isinstance(tool, dict)
    }
    undefined_names = sorted({name for name in cleaned_names if name not in defined_names})
    source_bytes = source_path.stat().st_size
    cleaned_bytes = cleaned_path.stat().st_size
    totals["source_trajectory_bytes"] += source_bytes
    totals["cleaned_trajectory_bytes"] += cleaned_bytes
    totals["source_prompt_chars"] += len(source_prompt)
    totals["cleaned_prompt_chars"] += len(cleaned_prompt)
    totals["source_environment_chars"] += source_environment_chars
    totals["cleaned_environment_chars"] += cleaned_environment_chars
    totals["tool_calls"] += len(source_names)
    totals["prefixed_tool_calls_before"] += sum(name.startswith(MCP_PREFIX) for name in source_names)
    totals["prefixed_tool_calls_after"] += sum(MCP_PREFIX in name for name in cleaned_names)
    totals["undefined_tool_calls"] += sum(name not in defined_names for name in cleaned_names)
    records.append({
        "case": case,
        "category": row.get("category") or "recovered_verified_pass",
        "source": str(source_dir),
        "trajectory": str(cleaned_path),
        "input": str(input_path),
        "source_sha256": digest(source_path),
        "cleaned_sha256": digest(cleaned_path),
        "source_prompt_chars": len(source_prompt),
        "cleaned_prompt_chars": len(cleaned_prompt),
        "source_environment_chars": source_environment_chars,
        "cleaned_environment_chars": cleaned_environment_chars,
        "tool_calls": len(source_names),
        "undefined_tool_names": undefined_names,
        "raw_evidence": str(raw_evidence) if raw_evidence.is_dir() else None,
        "initial_request_evidence": (
            str(raw_evidence / "model_requests.jsonl")
            if (raw_evidence / "model_requests.jsonl").is_file()
            else str(source_dir / "trajectory.html") + "#initial-request-data"
        ),
        "repaired_tools": str(repaired_path),
        "repaired_tools_sha256": digest(repaired_path),
    })
    if index % 25 == 0:
        print(f"cleaned {index}/{len(selected_cases)}", flush=True)

summary = {
    "schema_version": "agent_world.trajectory_clean_manifest.v2",
    "policy": {
        "environment": (
            "Task-conditioned natural-language context placed before the original task text."
            if descriptions is not None else
            "No synthesized environment prose; the user message is the original task_text."
        ),
        "environment_description_provenance": provenance,
        "prompt_order": (
            ["environment_description", "task_text"]
            if descriptions is not None else ["task_text"]
        ),
        "tools": "Canonical tool names with descriptions and recursive input field descriptions from the environment's tools_repaired.json; namespace prefixes and 64-character aliases normalized.",
        "tool_outputs": "Structured payloads without Kimi MCP result wrapper templates.",
        "system": "Official Kimi Code base prompt; dynamic working directory normalized per case.",
        "source_evidence": "Original trajectories are unchanged and linked from each case as source/.",
    },
    "counts": {"trajectories": len(records)},
    "totals": totals,
    "records": records,
}
dump(OUTPUT / "manifest.json", summary)
dump(OUTPUT / "summary.json", {
    "results": [{"case": row["case"], "status": "completed"} for row in records]
})

flagged_cases = [row["case"] for row in records if row["undefined_tool_names"]]
normal_cases = [row["case"] for row in records if not row["undefined_tool_names"]]
sample_cases = flagged_cases[:2]
if normal_cases:
    slots = 10 - len(sample_cases)
    indices = {
        round(index * (len(normal_cases) - 1) / max(slots - 1, 1))
        for index in range(slots)
    }
    sample_cases.extend(normal_cases[index] for index in sorted(indices))
sample_dir = OUTPUT / "visualization_sample"
sample_dir.mkdir()
sample_results = []
for case in sample_cases:
    target = OUTPUT / "trajectories" / case
    case_dir = sample_dir / case
    case_dir.mkdir()
    (case_dir / "trajectory.json").symlink_to(target / "trajectory.json")
    (case_dir / "input.json").symlink_to(target / "input.json")
    sample_results.append({"case": case, "status": "completed"})
dump(sample_dir / "summary.json", {"results": sample_results})
render_combined_visualization(sample_dir)

(OUTPUT / "README.md").write_text("""# Cleaned trajectory v2

This dataset is a training-oriented view of the accepted trajectories. Original evidence is unchanged.

- `trajectories/<case>/trajectory.json`: normalized cleaned trajectory.
- `trajectories/<case>/input.json`: cleaned first model request and tool schemas.
- `trajectories/<case>/source`: link to the original auditable trajectory directory.
- `visualization_sample/combined.html`: review sample using cleaned inputs.
- `manifest.json`: source mapping, hashes, policy, and aggregate reduction metrics.

Do not use files below `source/` as cleaned training inputs; they are retained only for audit.
""", encoding="utf-8")
print(json.dumps({"output": str(OUTPUT), "totals": totals}, ensure_ascii=False, indent=2))
