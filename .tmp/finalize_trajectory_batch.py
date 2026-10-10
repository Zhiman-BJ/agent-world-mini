#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/data1/agent_world/trajectory")
BATCH_NAME = "semiconductor_kimi_k3_20260930"
BATCH = ROOT / BATCH_NAME
FINAL_ROOT = Path("/data1/agent_world/kimi_k3_distill/final_923_20260925")


def load(path: Path):
    return json.loads(path.read_text())


def link(target: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() and not destination.is_symlink():
        destination.symlink_to(target, target_is_directory=True)


ROOT.mkdir(parents=True, exist_ok=True)
if not BATCH.exists():
    BATCH.mkdir()
    for item in list(ROOT.iterdir()):
        if item == BATCH:
            continue
        item.rename(BATCH / item.name)

manifest_path = BATCH / "manifest.json"
manifest = load(manifest_path)
old_manifest = load(FINAL_ROOT / "manifest.json")
verifier = load(FINAL_ROOT / "verifier_summary.json")

entries_by_case = {entry["case"]: entry for entry in old_manifest["completed"]}
failed = []
for result in verifier["results"]:
    if result.get("outcome") != "fail":
        continue
    entry = entries_by_case[result["case"]]
    canonical = f"{entry['environment']}__{entry['task_id']}"
    task_dir = Path(entry["trajectory"]).parent
    if not (task_dir / "trajectory.json").is_file():
        raise SystemExit(f"missing failed trajectory: {task_dir}")
    link(task_dir, BATCH / "failed" / "old_verifier_fail" / "all" / canonical)
    failed.append({
        "case": canonical,
        "old_case": entry["case"],
        "category": "old_verifier_fail",
        "source": str(task_dir),
        "trajectory": str(task_dir / "trajectory.json"),
        "verifier_summary": result.get("summary"),
        "verdict": result.get("verdict"),
    })

if len(failed) != 447:
    raise SystemExit(f"expected 447 old verifier failures, got {len(failed)}")

manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
manifest["batch_name"] = BATCH_NAME
manifest["counts"]["old_verifier_fail_archived"] = len(failed)
manifest["failed"] = sorted(failed, key=lambda x: x["case"])
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

batch_readme = BATCH / "README.md"
text = batch_readme.read_text()
if "failed/old_verifier_fail/all/" not in text:
    text += f"\n## 失败轨迹归档\n\n- `failed/old_verifier_fail/all/`：旧 verifier 判定为 fail 的 {len(failed)} 条完整轨迹。\n"
batch_readme.write_text(text)

root_readme = f"""# Trajectory 批次目录

该目录用于存放多个独立轨迹批次。每个批次均包含自己的 `manifest.json`、状态分类和来源说明。

## 当前批次

- `{BATCH_NAME}/`：Kimi K3 半导体轨迹整理批次（2026-09-30）
  - 当前接受集：455 条
  - 待 verifier：55 条
  - 旧 verifier fail 归档：447 条
  - 工具错位尚未替换：6 条

`latest` 指向当前批次。
"""
(ROOT / "README.md").write_text(root_readme)
latest = ROOT / "latest"
if latest.is_symlink() or latest.exists():
    latest.unlink()
latest.symlink_to(BATCH, target_is_directory=True)

print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
print(BATCH)
