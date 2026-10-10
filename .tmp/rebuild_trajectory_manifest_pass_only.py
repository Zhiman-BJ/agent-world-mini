from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any


ROOT = Path("/data1/agent_world/trajectory/semiconductor_kimi_k3_20260930")
MANIFEST = ROOT / "manifest.json"
CLASSIFIED = Path(
    "/data1/agent_world/aw-data/k3_mismatch_replacements_verifier_20261003/classified"
)


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def reset_links(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def link(directory: Path, name: str, target: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    destination.symlink_to(target)


def main() -> None:
    manifest = read(MANIFEST)
    pass_rows = {row["case"]: row for row in read_jsonl(CLASSIFIED / "pass.jsonl")}
    fail_rows = {row["case"]: row for row in read_jsonl(CLASSIFIED / "fail.jsonl")}

    old_accepted = manifest["accepted"]
    replacement_rows = {
        row["case"]: row
        for row in old_accepted
        if row.get("category") == "mismatch_replacement"
    }
    untouched_rows = [
        row for row in old_accepted if row.get("category") == "untouched_old_pass"
    ]
    if set(replacement_rows) != set(pass_rows) | set(fail_rows):
        raise ValueError("replacement cases do not match verifier cases")

    accepted_replacements: list[dict[str, Any]] = []
    failed_replacements: list[dict[str, Any]] = []
    for case, old in replacement_rows.items():
        verifier = pass_rows.get(case) or fail_rows[case]
        row = dict(old)
        row["source"] = str(Path(verifier["trajectory"]).parent)
        row["trajectory"] = verifier["trajectory"]
        if case in pass_rows:
            row["verification"] = "replacement_verifier_pass"
            accepted_replacements.append(row)
        else:
            row["category"] = "mismatch_replacement_verifier_fail"
            row["verification"] = "replacement_verifier_fail"
            row["verifier_summary"] = verifier["verifier_summary"]
            row["verdict"] = verifier["verdict"]
            failed_replacements.append(row)

    pending = manifest.get("pending_verifier", [])
    pending_by_case = {row["case"]: row for row in pending}
    recovered_pass_dir = ROOT / "accepted/recovered_verified_pass"
    recovered_fail_dir = ROOT / "failed/recovered_verified_fail"
    recovered_pass_cases = sorted(
        child.name for child in recovered_pass_dir.iterdir() if child.is_symlink()
    )
    recovered_fail_cases = sorted(
        child.name for child in recovered_fail_dir.iterdir() if child.is_symlink()
    )
    if len(recovered_pass_cases) != 26 or len(recovered_fail_cases) != 29:
        raise ValueError(
            f"unexpected recovered counts: pass={len(recovered_pass_cases)} fail={len(recovered_fail_cases)}"
        )

    recovered_pass: list[dict[str, Any]] = []
    for case in recovered_pass_cases:
        pending_row = pending_by_case[case]
        target = (recovered_pass_dir / case).resolve()
        recovered_pass.append(
            {
                **pending_row,
                "category": "recovered_verified_pass",
                "source": str(target),
                "trajectory": str(target / "trajectory.json"),
                "verification": "recovered_verifier_pass",
            }
        )
    recovered_fail: list[dict[str, Any]] = []
    for case in recovered_fail_cases:
        pending_row = pending_by_case[case]
        target = (recovered_fail_dir / case).resolve()
        recovered_fail.append(
            {
                **pending_row,
                "category": "recovered_verified_fail",
                "source": str(target),
                "trajectory": str(target / "trajectory.json"),
                "verification": "recovered_verifier_fail",
            }
        )

    old_failed = list(manifest["failed"])
    failed = old_failed + failed_replacements + recovered_fail
    accepted = untouched_rows + accepted_replacements + recovered_pass
    failed_unique_cases = len({row["case"] for row in failed})
    if len(accepted) != 413 or len(failed) != 544 or failed_unique_cases != 527:
        raise ValueError(f"unexpected final counts: accepted={len(accepted)} failed={len(failed)}")

    # Rebuild the aggregate links used by downstream distillation.
    accepted_all = ROOT / "accepted/all"
    reset_links(accepted_all)
    for row in accepted:
        link(accepted_all, row["case"], Path(row["source"]))
    failed_all = ROOT / "failed/all"
    reset_links(failed_all)
    for row in failed:
        link(failed_all, row["case"], Path(row["source"]))

    # Replace the previously policy-accepted replacement links with only PASS.
    replacement_dir = ROOT / "accepted/mismatch_replacements"
    reset_links(replacement_dir)
    for row in accepted_replacements:
        link(replacement_dir, row["case"], Path(row["source"]))
    failed_replacement_dir = ROOT / "failed/mismatch_replacements_verifier_fail"
    reset_links(failed_replacement_dir)
    for row in failed_replacements:
        link(failed_replacement_dir, row["case"], Path(row["source"]))

    manifest["accepted"] = sorted(accepted, key=lambda row: row["case"])
    manifest["failed"] = sorted(failed, key=lambda row: row["case"])
    manifest["pending_verifier"] = []
    manifest["counts"].update(
        {
            "completed_mismatch_replacements_accepted": len(accepted_replacements),
            "accepted_total": len(accepted),
            "recovered_verified_pass": len(recovered_pass),
            "recovered_verified_fail": len(recovered_fail),
            "pending_verifier": 0,
            "completed_recovery_candidates": len(recovered_pass) + len(recovered_fail),
            "failed_total": len(failed),
            "failed_unique_cases": failed_unique_cases,
        }
    )
    manifest["policy"]["mismatch_replacements"] = (
        "Only replacement trajectories with an independent verifier PASS are accepted; verifier FAIL trajectories are archived outside the distillation input."
    )
    manifest["policy"]["recovered_old_fail"] = (
        "Recovered trajectories are accepted only after independent verifier PASS; recovered FAIL trajectories remain outside the distillation input."
    )
    write(MANIFEST, manifest)

    readme = ROOT / "README.md"
    readme.write_text(
        """# Kimi K3 半导体轨迹批次（2026-09-30）

本目录通过符号链接整理轨迹，不复制或删除原始数据。失败轨迹保留在 `failed/`，但不进入蒸馏输入。

## 最终状态

- 当前可蒸馏接受集：**413 条**
  - 未受工具错位影响的旧 verifier PASS：175 条
  - 工具错位 replacement 且重新 verifier PASS：212 条
  - 旧 FAIL 恢复后重新 verifier PASS：26 条
- 失败归档：**544 条历史记录，527 个去重 case**
  - 旧 verifier FAIL：447 条
  - replacement 重新 verifier FAIL：68 条
  - 恢复旧 FAIL 后重新 verifier FAIL：29 条
- verifier error / 待验证：0 条
- 尚未成功替换的错位旧 PASS：6 条（不计入接受集）

## 目录

- `accepted/all/`：当前可蒸馏的 413 条 PASS 轨迹。
- `accepted/mismatch_replacements/`：212 条 replacement PASS 轨迹。
- `accepted/recovered_verified_pass/`：26 条恢复后 PASS 轨迹。
- `failed/all/`：按 case 去重后的 527 条失败轨迹，仅归档，不用于蒸馏。
- `failed/old_verifier_fail/all/`：447 条旧 verifier FAIL 轨迹。
- `failed/mismatch_replacements_verifier_fail/`：68 条 replacement verifier FAIL 轨迹。
- `failed/recovered_verified_fail/`：29 条恢复后仍 FAIL 的轨迹。
- `unresolved/mismatch_not_replaced/`：6 条尚未替换成功的旧错位轨迹。
- `manifest.json`：逐条来源、分类和 verifier 结果。
""",
        encoding="utf-8",
    )
    print(json.dumps({"accepted": len(accepted), "failed": len(failed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
