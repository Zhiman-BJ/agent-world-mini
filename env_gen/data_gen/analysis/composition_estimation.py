from __future__ import annotations

from collections import defaultdict
from typing import Any


_ALLOWED_TRANSITIONS = {
    "inspect": {"search", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export", "audit", "transform", "edit", "validate", "simulate"},
    "list": {"inspect", "search", "filter", "rank", "compare", "aggregate", "timeline", "traverse"},
    "search": {"inspect", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "filter": {"inspect", "search", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "rank": {"inspect", "compare", "aggregate", "traverse", "export"},
    "compare": {"inspect", "aggregate", "traverse", "export"},
    "aggregate": {"inspect", "compare", "timeline", "traverse", "export"},
    "timeline": {"inspect", "filter", "rank", "compare", "aggregate", "traverse", "export"},
    "traverse": {"inspect", "list", "search", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "resolve_file": {"inspect", "transform", "edit", "validate", "simulate", "export", "audit"},
    "transform": {"inspect", "search", "filter", "aggregate", "traverse", "edit", "validate", "simulate", "export", "audit"},
    "edit": {"inspect", "transform", "validate", "simulate", "export", "audit"},
    "validate": {"inspect", "transform", "edit", "simulate", "export", "audit"},
    "simulate": {"inspect", "compare", "aggregate", "transform", "edit", "validate", "export", "audit"},
    "audit": {"inspect", "transform", "export"},
    "export": set(),
}


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["candidate_id"] == right["candidate_id"]:
        return False
    if left.get("output_kind") != right.get("input_kind"):
        return False
    left_family = str(left.get("operation_family") or "")
    right_family = str(right.get("operation_family") or "")
    if left_family == right_family and left_family != "traverse":
        return False
    return right_family in _ALLOWED_TRANSITIONS.get(left_family, set())


def _shape_key(chain: list[dict[str, Any]]) -> tuple[str, ...]:
    """工具链形状只看操作和资源流，不用不同字段制造重复链。"""

    parts: list[str] = []
    for candidate in chain:
        parts.extend(
            (
                str(candidate.get("operation_family") or "other"),
                str(candidate.get("input_kind") or "unknown"),
                str(candidate.get("output_kind") or "unknown"),
            )
        )
    return tuple(parts)


def _representative_neighbors(
    current: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    per_family: int = 2,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if _compatible(current, candidate):
            grouped[str(candidate.get("operation_family") or "other")].append(candidate)
    selected: list[dict[str, Any]] = []
    for family in sorted(grouped):
        ordered = sorted(
            grouped[family],
            key=lambda item: (
                -int(item.get("parameter_cardinality", 0)),
                -int(item.get("support_count", 0)),
                str(item.get("candidate_id")),
            ),
        )
        selected.extend(ordered[:per_family])
    return selected


def build_composition_estimate(
    candidates: list[dict[str, Any]],
    *,
    minimum_length: int = 2,
    maximum_length: int = 5,
    maximum_shapes: int = 1_000,
    maximum_stored_chains: int = 100,
) -> dict[str, Any]:
    """估算候选操作的类型兼容组合；不表示工具链已经实现或执行。"""

    candidates = sorted(candidates, key=lambda item: str(item.get("candidate_id")))
    neighbors = {
        candidate["candidate_id"]: _representative_neighbors(candidate, candidates)
        for candidate in candidates
    }
    transition_shapes = {
        (
            str(current.get("operation_family")),
            str(current.get("output_kind")),
            str(candidate.get("operation_family")),
        )
        for current in candidates
        for candidate in neighbors[current["candidate_id"]]
    }

    shapes: dict[tuple[str, ...], dict[str, Any]] = {}

    def visit(chain: list[dict[str, Any]]) -> None:
        if len(shapes) >= maximum_shapes:
            return
        if len(chain) >= minimum_length:
            families = {str(item.get("operation_family")) for item in chain}
            if len(families) >= 2:
                key = _shape_key(chain)
                cards = [
                    max(1, int(item.get("parameter_cardinality", 0)))
                    for item in chain
                ]
                contribution = min(100, min(cards))
                candidate = {
                    "chain_id": f"chain_{len(shapes) + 1:04d}",
                    "length": len(chain),
                    "operation_families": [
                        str(item.get("operation_family")) for item in chain
                    ],
                    "resource_flow": [
                        str(chain[0].get("input_kind")),
                        *[str(item.get("output_kind")) for item in chain],
                    ],
                    "candidate_ids": [
                        str(item.get("candidate_id")) for item in chain
                    ],
                    "estimated_instances": contribution,
                }
                previous = shapes.get(key)
                if previous is None or contribution > previous["estimated_instances"]:
                    shapes[key] = candidate
        if len(chain) >= maximum_length:
            return
        used = {str(item.get("candidate_id")) for item in chain}
        used_families = {str(item.get("operation_family")) for item in chain}
        for candidate in neighbors[chain[-1]["candidate_id"]]:
            if candidate["candidate_id"] in used:
                continue
            candidate_family = str(candidate.get("operation_family"))
            # 同一资源上反复 filter/search/inspect 只是在排列字段，不是新的
            # 多步任务形状。跨实体遍历可以连续发生，因此 traverse 例外。
            if candidate_family in used_families and candidate_family != "traverse":
                continue
            visit([*chain, candidate])
            if len(shapes) >= maximum_shapes:
                return

    # 优先从检索、筛选、列表和文件解析入口开始，剩余能力作为兜底入口。
    entry_order = {
        "search": 0,
        "filter": 1,
        "list": 2,
        "resolve_file": 3,
        "transform": 4,
        "inspect": 5,
    }
    for candidate in sorted(
        candidates,
        key=lambda item: (
            entry_order.get(str(item.get("operation_family")), 9),
            str(item.get("candidate_id")),
        ),
    ):
        visit([candidate])
        if len(shapes) >= maximum_shapes:
            break

    ordered_shapes = sorted(
        shapes.values(),
        key=lambda item: (
            -int(item["length"]),
            tuple(item["operation_families"]),
            tuple(item["resource_flow"]),
        ),
    )
    # 重新生成稳定 ID，避免 DFS 插入顺序泄漏到最终协议。
    for index, item in enumerate(ordered_shapes, start=1):
        item["chain_id"] = f"chain_{index:04d}"
    if len(ordered_shapes) <= maximum_stored_chains:
        stored_chains = ordered_shapes
    elif maximum_stored_chains <= 1:
        stored_chains = ordered_shapes[:maximum_stored_chains]
    else:
        indexes = {
            round(index * (len(ordered_shapes) - 1) / (maximum_stored_chains - 1))
            for index in range(maximum_stored_chains)
        }
        stored_chains = [ordered_shapes[index] for index in sorted(indexes)]
    return {
        "transition_shape_count": len(transition_shapes),
        "chain_shape_count": len(ordered_shapes),
        "long_chain_shape_count": sum(
            1 for item in ordered_shapes if int(item["length"]) >= 3
        ),
        "estimated_parameterized_cases": sum(
            int(item["estimated_instances"]) for item in ordered_shapes
        ),
        "chain_enumeration_limit": maximum_shapes,
        "chain_enumeration_saturated": len(ordered_shapes) >= maximum_shapes,
        "stored_chain_count": len(stored_chains),
        "chains": stored_chains,
    }
