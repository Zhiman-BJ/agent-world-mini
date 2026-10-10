#!/usr/bin/env python3
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

BATCH = Path("/data1/agent_world/trajectory/semiconductor_kimi_k3_20260930")
SUMMARY = Path("/data1/agent_world/aw-data/k3_recoverable_failures_20260929/verifier/summary.json")


def load(path: Path):
    return json.loads(path.read_text())


def reset_dir(path: Path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def link(target: Path, destination: Path, replace=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.exists():
        if not replace:
            return
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.symlink_to(target, target_is_directory=True)


manifest_path = BATCH / "manifest.json"
manifest = load(manifest_path)
summary = load(SUMMARY)
pending_by_case = {row["case"]: row for row in manifest["pending_verifier"]}

pass_rows = [r for r in summary["results"] if r.get("outcome") == "pass"]
fail_rows = [r for r in summary["results"] if r.get("outcome") == "fail"]
error_rows = [r for r in summary["results"] if r.get("status") == "verification_error"]

for path in [
    BATCH / "accepted/recovered_verified_pass",
    BATCH / "failed/recovered_verified_fail",
    BATCH / "verification_error/recovered_old_fail",
    BATCH / "pending_verifier/all",
    BATCH / "pending_verifier/recovered_old_fail",
]:
    reset_dir(path)

for result in pass_rows:
    row = pending_by_case[result["case"]]
    source = Path(row["source"])
    link(source, BATCH / "accepted/recovered_verified_pass" / result["case"])
    link(source, BATCH / "accepted/all" / result["case"], replace=True)

for result in fail_rows:
    row = pending_by_case[result["case"]]
    source = Path(row["source"])
    link(source, BATCH / "failed/recovered_verified_fail" / result["case"])

for result in error_rows:
    row = pending_by_case[result["case"]]
    source = Path(row["source"])
    link(source, BATCH / "verification_error/recovered_old_fail" / result["case"])
    link(source, BATCH / "pending_verifier/all" / result["case"])
    link(source, BATCH / "pending_verifier/recovered_old_fail" / result["case"])

manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
manifest["counts"].pop("completed_recovered_old_fail_pending_verifier", None)
manifest["counts"].update({
    "completed_recovery_candidates": len(summary["results"]),
    "recovered_verified_pass": len(pass_rows),
    "recovered_verified_fail": len(fail_rows),
    "recovered_verification_error": len(error_rows),
    "pending_verifier": len(error_rows),
    "accepted_total": 455 + len(pass_rows),
})
manifest["recovery_verifier"] = {
    "summary": str(SUMMARY),
    "counts": summary.get("counts", {}),
    "pass": pass_rows,
    "fail": fail_rows,
    "verification_error": error_rows,
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

(BATCH / "README.md").write_text(f"""# Kimi K3 半导体轨迹批次（2026-09-30）

本目录通过符号链接整理轨迹，不复制或删除原始数据。每条链接目录保留 `trajectory.json`、`raw/` 和 `execution-state/`。

## 最终状态

- 当前接受集：**{455 + len(pass_rows)} 条**
  - 未受工具错位影响的旧 verifier pass：175 条
  - 已完成并替换旧错位 pass 的新轨迹：280 条
  - 旧 fail 恢复后重新验证为 pass：{len(pass_rows)} 条
- 旧 verifier fail 归档：447 条
- 恢复后重新验证仍为 fail：{len(fail_rows)} 条
- verifier error / 待验证：{len(error_rows)} 条
- 尚未成功替换的错位旧 pass：6 条（不计入接受集）

## 目录

- `accepted/all/`：当前接受的全部轨迹。
- `accepted/mismatch_replacements/`：280 条工具错位替换轨迹。
- `accepted/recovered_verified_pass/`：恢复后通过 verifier 的轨迹。
- `failed/old_verifier_fail/all/`：447 条旧 verifier fail 历史轨迹。
- `failed/recovered_verified_fail/`：恢复后仍未通过的最新轨迹。
- `unresolved/mismatch_not_replaced/`：6 条尚未替换成功的旧错位轨迹。
- `manifest.json`：逐条来源、分类和 verifier 结果。
""")

root = BATCH.parent
(root / "README.md").write_text(f"""# Trajectory 批次目录

该目录用于存放多个独立轨迹批次。每个批次包含自己的 `manifest.json`、状态分类和来源说明。

## 当前批次

- `{BATCH.name}/`：Kimi K3 半导体轨迹整理批次（2026-09-30）
  - 当前接受集：{455 + len(pass_rows)} 条
  - 旧 verifier fail 归档：447 条
  - 恢复后仍 fail：{len(fail_rows)} 条
  - 待 verifier：{len(error_rows)} 条
  - 工具错位尚未替换：6 条

`latest` 指向当前批次。
""")

print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
