from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


# 后续正式生成默认写入 OSS。显式传入 output_dir 时仍可用于测试和调试。
DEFAULT_OSS_OUTPUT_ROOT = Path(
    "/mnt/oss-bucket/sunshuo/AgentWorld/environment/data_gen_v2"
)


@dataclass(frozen=True)
class AcquisitionPolicy:
    """控制真实数据采集规模和轮次，不承担环境质量验收。

    这里的记录数是“完整下载与分层下载的分界/上限”，不会被解释成
    环境合格线。Agent 不能因为达到某个数字就宣布环境已经丰富。
    """

    max_collection_rounds: int = 4
    full_download_record_limit: int = 50_000
    large_surface_record_target: int = 25_000
    max_relation_edges: int = 100_000
    max_raw_bytes: int = 512 * 1024 * 1024
    max_workspace_bytes: int = 768 * 1024 * 1024
    max_single_file_bytes: int = 256 * 1024 * 1024
    max_raw_files: int = 200
    max_sources: int = 50
    diminishing_rounds: int = 2
    minimum_task_growth_percent: int = 5
    post_rich_rounds: int = 1

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"AcquisitionPolicy.{name} 必须是非负整数")
        if self.max_collection_rounds == 0:
            raise ValueError("max_collection_rounds 必须大于 0")


@dataclass(frozen=True)
class AcquisitionIssue:
    """数据面清单的一条确定性错误。"""

    code: str
    path: str
    message: str


class SourceInventoryValidator:
    """校验 Agent 声明的数据面、分页状态和实际 raw 文件之间的对应关系。"""

    def __init__(self, schema_path: Path) -> None:
        self.schema_path = schema_path.resolve()
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    def validate(
        self,
        inventory_path: Path,
        *,
        request_sha256: str,
        checkpoint: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[AcquisitionIssue]]:
        issues: list[AcquisitionIssue] = []
        try:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, [
                AcquisitionIssue(
                    "missing_source_inventory",
                    "$.source_inventory",
                    "缺少 provenance/source_inventory.json；Agent 必须先盘点数据面再提交数据",
                )
            ]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return None, [
                AcquisitionIssue(
                    "invalid_source_inventory_json",
                    "$.source_inventory",
                    f"source_inventory.json 无法读取：{error}",
                )
            ]

        if not isinstance(inventory, dict):
            return None, [
                AcquisitionIssue(
                    "invalid_source_inventory",
                    "$.source_inventory",
                    "source_inventory.json 根节点必须是对象",
                )
            ]

        for error in sorted(self.validator.iter_errors(inventory), key=lambda item: list(item.path)):
            pointer = "$.source_inventory"
            for part in error.absolute_path:
                pointer += f"[{part}]" if isinstance(part, int) else f".{part}"
            issues.append(
                AcquisitionIssue("source_inventory_schema", pointer, error.message)
            )

        if inventory.get("request_sha256") != request_sha256:
            issues.append(
                AcquisitionIssue(
                    "source_inventory_request_mismatch",
                    "$.source_inventory.request_sha256",
                    "数据面清单没有引用本次 research_request",
                )
            )

        raw_files = {
            value
            for value in checkpoint.get("raw_files", [])
            if isinstance(value, str)
        }
        source_urls = {
            value
            for value in checkpoint.get("source_urls", [])
            if isinstance(value, str)
        }
        seen_surface_ids: set[str] = set()
        inventory_raw_files: set[str] = set()
        for index, surface in enumerate(inventory.get("surfaces", [])):
            if not isinstance(surface, dict):
                continue
            pointer = f"$.source_inventory.surfaces[{index}]"
            surface_id = surface.get("surface_id")
            if isinstance(surface_id, str):
                if surface_id in seen_surface_ids:
                    issues.append(
                        AcquisitionIssue(
                            "duplicate_surface_id",
                            f"{pointer}.surface_id",
                            f"数据面 ID 重复：{surface_id}",
                        )
                    )
                seen_surface_ids.add(surface_id)
            url = surface.get("url")
            status = surface.get("collection_status")
            if status in {"partial", "complete"} and url not in source_urls:
                issues.append(
                    AcquisitionIssue(
                        "surface_source_not_in_checkpoint",
                        f"{pointer}.url",
                        f"已经采集的数据面 URL 未登记在 checkpoint.source_urls：{url}",
                    )
                )
            for raw_path in surface.get("raw_files", []):
                if raw_path in inventory_raw_files:
                    issues.append(
                        AcquisitionIssue(
                            "raw_file_in_multiple_surfaces",
                            f"{pointer}.raw_files",
                            f"同一个 raw 文件不能同时归属于多个数据面：{raw_path}",
                        )
                    )
                inventory_raw_files.add(raw_path)
                if raw_path not in raw_files:
                    issues.append(
                        AcquisitionIssue(
                            "surface_raw_file_not_in_checkpoint",
                            f"{pointer}.raw_files",
                            f"数据面引用的 raw 文件未登记在 checkpoint：{raw_path}",
                        )
                    )
            if status == "complete" and not surface.get("exhaustion_evidence"):
                issues.append(
                    AcquisitionIssue(
                        "complete_surface_without_evidence",
                        f"{pointer}.exhaustion_evidence",
                        "collection_status=complete 必须说明分页结束、总量取完或分层策略已完成",
                    )
                )
            evidence = surface.get("exhaustion_evidence")
            evidence_type = evidence.get("type") if isinstance(evidence, dict) else None
            pagination = surface.get("pagination")
            reported_total = (
                pagination.get("reported_total")
                if isinstance(pagination, dict)
                else None
            )
            records_collected = int(surface.get("records_collected", 0) or 0)
            if (
                status == "complete"
                and evidence_type == "reported_total_reached"
                and isinstance(reported_total, int)
                and records_collected < reported_total
            ):
                issues.append(
                    AcquisitionIssue(
                        "reported_total_not_reached",
                        f"{pointer}.records_collected",
                        f"声称 reported_total_reached，但实际声明采集 {records_collected} < 总量 {reported_total}",
                    )
                )
            if (
                status == "complete"
                and surface.get("collection_mode") == "bounded_stratified"
                and evidence_type != "bounded_policy_reached"
            ):
                issues.append(
                    AcquisitionIssue(
                        "bounded_surface_without_policy_evidence",
                        f"{pointer}.exhaustion_evidence",
                        "bounded_stratified 数据面必须使用 bounded_policy_reached 说明分层采集已经完成",
                    )
                )
            if status in {"partial", "complete"} and records_collected > 0 and not surface.get("raw_files"):
                issues.append(
                    AcquisitionIssue(
                        "collected_surface_without_raw_files",
                        f"{pointer}.raw_files",
                        "声明已采集记录的数据面必须引用至少一个实际 raw 文件",
                    )
                )

        missing_inventory_files = raw_files - inventory_raw_files
        if missing_inventory_files:
            issues.append(
                AcquisitionIssue(
                    "checkpoint_raw_file_missing_surface",
                    "$.source_inventory.surfaces",
                    f"checkpoint 中的 raw 文件没有归属数据面：{sorted(missing_inventory_files)}",
                )
            )

        declared_surface_ids = {
            surface.get("surface_id")
            for surface in inventory.get("surfaces", [])
            if isinstance(surface, dict)
        }
        for index, surface in enumerate(inventory.get("surfaces", [])):
            if not isinstance(surface, dict):
                continue
            for related_id in surface.get("related_surface_ids", []):
                if related_id not in declared_surface_ids:
                    issues.append(
                        AcquisitionIssue(
                            "unknown_related_surface",
                            f"$.source_inventory.surfaces[{index}].related_surface_ids",
                            f"related_surface_ids 引用了未登记的数据面：{related_id}",
                        )
                    )
        return inventory, issues


def acquisition_frontier(
    inventory: dict[str, Any],
    quality_profile: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """生成下一轮 Agent 应处理的明确数据面动作，不反馈“还差几条”。"""

    actions: list[dict[str, str]] = []
    for surface in inventory.get("surfaces", []):
        if not isinstance(surface, dict):
            continue
        status = surface.get("collection_status")
        if status == "pending":
            actions.append(
                {
                    "code": "collect_pending_surface",
                    "surface_id": str(surface.get("surface_id") or "unknown"),
                    "action": "采集该数据面；若支持分页，持续到分页结束或完成规定的分层采集。",
                }
            )
        elif status == "partial":
            actions.append(
                {
                    "code": "continue_partial_surface",
                    "surface_id": str(surface.get("surface_id") or "unknown"),
                    "action": "继续未完成的分页，并把新增页面保存为新的 raw 文件。",
                }
            )
        elif status == "blocked" and surface.get("priority") == "core":
            actions.append(
                {
                    "code": "resolve_blocked_core_surface",
                    "surface_id": str(surface.get("surface_id") or "unknown"),
                    "action": "尝试同一官方来源中的批量下载、仓库快照或其它公开端点；不得更换业务范围。",
                }
            )

    for gap in (quality_profile or {}).get("gaps", []):
        if not isinstance(gap, dict) or not isinstance(gap.get("action"), str):
            continue
        actions.append(
            {
                "code": str(gap.get("code") or "quality_gap"),
                "surface_id": str(gap.get("surface_id") or "quality_profile"),
                "action": gap["action"],
            }
        )
    # 相同动作只保留一次，避免 Prompt 被同一缺口重复占满。
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in actions:
        key = (item["surface_id"], item["action"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def core_surfaces_settled(inventory: dict[str, Any]) -> bool:
    """核心数据面都必须有明确终态；pending/partial 不能冒充完成。"""

    core = [
        surface
        for surface in inventory.get("surfaces", [])
        if isinstance(surface, dict) and surface.get("priority") == "core"
    ]
    return bool(core) and all(
        surface.get("collection_status") in {"complete", "blocked"}
        for surface in core
    )
