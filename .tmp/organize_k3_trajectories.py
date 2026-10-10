#!/usr/bin/env python3
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

FINAL_ROOT = Path("/data1/agent_world/kimi_k3_distill/final_923_20260925")
VERIFIER_SUMMARY = FINAL_ROOT / "verifier_summary.json"
MANIFEST = FINAL_ROOT / "manifest.json"
MISMATCH_SELECTION = Path("/data1/agent_world/aw-data/k3_rerun_all_mismatch_256k_20260929/selection_summary.json")
MISMATCH_RUNS = [
    Path("/data1/agent_world/aw-data/k3_rerun_pass_mismatch_256k_max_50x_20260929_v2/runs/20260929_222151_669841"),
    Path("/data1/agent_world/aw-data/k3_rerun_pass_mismatch_256k_max_50x_20260929_v2/resume_20260930/runs/20260930_005159_889162"),
]
RECOVERY_RUNS = [
    Path("/data1/agent_world/aw-data/k3_recoverable_failures_20260929/distill_runs/20260930_025101_137763"),
    Path("/data1/agent_world/aw-data/k3_recoverable_failures_20260929/distill_runs/20260930_025602_739549"),
    Path("/data1/agent_world/aw-data/k3_recoverable_failures_20260929/distill_runs_retry/20260930_125918_121052"),
]
OUTPUT = Path("/data1/agent_world/aw-data/k3_curated_trajectories_20260930")


def load(path: Path):
    return json.loads(path.read_text())


def completed_task_dirs(run_dirs):
    found = {}
    for run_dir in run_dirs:
        if not run_dir.exists():
            continue
        for task_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            trajectory = task_dir / "trajectory.json"
            result_path = task_dir / "raw" / "run_result.json"
            if not trajectory.is_file() or not result_path.is_file():
                continue
            try:
                result = load(result_path)
            except Exception:
                continue
            if result.get("reason") != "completed" or result.get("cli_returncode") != 0:
                continue
            found[task_dir.name] = task_dir
    return found


def link(target: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(target, target_is_directory=True)


verifier = load(VERIFIER_SUMMARY)
old_manifest = load(MANIFEST)
mismatch_selection = load(MISMATCH_SELECTION)

old_entries_by_case = {e["case"]: e for e in old_manifest["completed"]}
old_entries = {f"{e['environment']}__{e['task_id']}": e for e in old_manifest["completed"]}
if len(old_entries) != len(old_manifest["completed"]):
    raise SystemExit("environment + task_id is not unique in the old manifest")
pass_cases = {
    f"{old_entries_by_case[r['case']]['environment']}__{old_entries_by_case[r['case']]['task_id']}"
    for r in verifier["results"]
    if r.get("outcome") == "pass"
}
mismatch_pass = {
    r["case"]
    for r in mismatch_selection["results"]
    if r.get("original_outcome") == "pass" and r.get("status") == "selected"
}

if len(pass_cases) != 461:
    raise SystemExit(f"expected 461 old pass cases, got {len(pass_cases)}")
if len(mismatch_pass) != 286:
    raise SystemExit(f"expected 286 pass mismatch cases, got {len(mismatch_pass)}")
if mismatch_pass - pass_cases:
    raise SystemExit(f"mismatch cases absent from old pass set: {sorted(mismatch_pass - pass_cases)[:5]}")

all_replacements = completed_task_dirs(MISMATCH_RUNS)
replacements = {case: path for case, path in all_replacements.items() if case in mismatch_pass}
unresolved_mismatch = mismatch_pass - replacements.keys()
untouched_pass = pass_cases - mismatch_pass

recovery_completed = completed_task_dirs(RECOVERY_RUNS)

if len(untouched_pass) != 175:
    raise SystemExit(f"expected 175 untouched old pass cases, got {len(untouched_pass)}")
if len(replacements) != 280:
    raise SystemExit(f"expected 280 completed replacements, got {len(replacements)}")
if len(unresolved_mismatch) != 6:
    raise SystemExit(f"expected 6 unresolved mismatch cases, got {len(unresolved_mismatch)}")
if len(recovery_completed) != 55:
    raise SystemExit(f"expected 55 completed recovery cases, got {len(recovery_completed)}")

if OUTPUT.exists():
    raise SystemExit(f"refusing to overwrite existing output: {OUTPUT}")
OUTPUT.mkdir(parents=True)

accepted = []
pending = []
unresolved = []

for case in sorted(untouched_pass):
    entry = old_entries[case]
    task_dir = Path(entry["trajectory"]).parent
    if not (task_dir / "trajectory.json").is_file():
        raise SystemExit(f"missing old trajectory: {task_dir}")
    link(task_dir, OUTPUT / "accepted" / "untouched_old_pass" / case)
    link(task_dir, OUTPUT / "accepted" / "all" / case)
    accepted.append({
        "case": case,
        "old_case": entry["case"],
        "category": "untouched_old_pass",
        "source": str(task_dir),
        "trajectory": str(task_dir / "trajectory.json"),
        "verification": "old_verifier_pass",
    })

for case, task_dir in sorted(replacements.items()):
    link(task_dir, OUTPUT / "accepted" / "mismatch_replacements" / case)
    link(task_dir, OUTPUT / "accepted" / "all" / case)
    accepted.append({
        "case": case,
        "old_case": old_entries[case]["case"],
        "category": "mismatch_replacement",
        "source": str(task_dir),
        "trajectory": str(task_dir / "trajectory.json"),
        "verification": "accepted_by_policy_replaces_old_pass",
    })

for case, task_dir in sorted(recovery_completed.items()):
    link(task_dir, OUTPUT / "pending_verifier" / "recovered_old_fail" / case)
    link(task_dir, OUTPUT / "pending_verifier" / "all" / case)
    pending.append({
        "case": case,
        "category": "recovered_old_fail",
        "source": str(task_dir),
        "trajectory": str(task_dir / "trajectory.json"),
        "verification": "pending",
    })

for case in sorted(unresolved_mismatch):
    old_entry = old_entries[case]
    old_task_dir = Path(old_entry["trajectory"]).parent
    link(old_task_dir, OUTPUT / "unresolved" / "mismatch_not_replaced" / case)
    unresolved.append({
        "case": case,
        "old_case": old_entry["case"],
        "category": "mismatch_not_replaced",
        "old_source": str(old_task_dir),
        "old_trajectory": str(old_task_dir / "trajectory.json"),
        "verification": "old_pass_but_excluded_due_to_mismatch",
    })

manifest = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "policy": {
        "mismatch_replacements": "Completed Kimi K3 Max reruns are accepted and replace the old verifier-pass trajectories one-for-one.",
        "recovered_old_fail": "Kept outside the accepted set until the new verifier finishes.",
    },
    "counts": {
        "old_verifier_pass": len(pass_cases),
        "old_pass_selected_for_mismatch_rerun": len(mismatch_pass),
        "untouched_old_pass_accepted": len(untouched_pass),
        "completed_mismatch_replacements_accepted": len(replacements),
        "accepted_total": len(accepted),
        "completed_recovered_old_fail_pending_verifier": len(pending),
        "unresolved_mismatch_not_replaced": len(unresolved),
    },
    "sources": {
        "old_final_root": str(FINAL_ROOT),
        "mismatch_selection": str(MISMATCH_SELECTION),
        "mismatch_runs": [str(p) for p in MISMATCH_RUNS],
        "recovery_runs": [str(p) for p in RECOVERY_RUNS],
    },
    "accepted": sorted(accepted, key=lambda x: x["case"]),
    "pending_verifier": sorted(pending, key=lambda x: x["case"]),
    "unresolved": sorted(unresolved, key=lambda x: x["case"]),
}
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

readme = f"""# Kimi K3 轨迹整理（2026-09-30）

本目录只包含指向原始轨迹目录的符号链接，不复制或删除原始数据。每个链接目录内都包含 `trajectory.json`、`raw/` 和 `execution-state/`（若原始运行已生成）。

## 当前口径

- 当前接受集：**{len(accepted)} 条**
  - 未受工具错位影响的旧 verifier pass：{len(untouched_pass)} 条
  - 已完成并用于替换旧错位 pass 的新轨迹：{len(replacements)} 条
- 待 verifier 的旧 fail 恢复轨迹：**{len(pending)} 条**
- 尚未成功替换的错位旧 pass：**{len(unresolved)} 条**（不计入当前接受集）

## 目录

- `accepted/all/`：当前可直接使用的 {len(accepted)} 条轨迹。
- `accepted/untouched_old_pass/`：175 条未受影响的旧 pass。
- `accepted/mismatch_replacements/`：280 条新蒸馏替换轨迹。
- `pending_verifier/all/`：55 条等待 verifier 的恢复轨迹。
- `unresolved/mismatch_not_replaced/`：6 条尚未替换成功的旧错位轨迹，仅供排查。
- `manifest.json`：每条轨迹的来源、分类和验证状态。

最终可用数量将在恢复批次 verifier 完成后更新为：`455 + 恢复批次 pass 数`。
"""
(OUTPUT / "README.md").write_text(readme)

print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
print(OUTPUT)
