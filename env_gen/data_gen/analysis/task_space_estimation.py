from __future__ import annotations

from collections import defaultdict
from typing import Any


_ALLOWED_TRANSITIONS = {
    "inspect": {"search", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export", "audit", "transform"},
    "list": {"inspect", "search", "filter", "rank", "compare", "aggregate", "timeline", "traverse"},
    "search": {"inspect", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "filter": {"inspect", "search", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "rank": {"inspect", "compare", "aggregate", "traverse", "export"},
    "compare": {"inspect", "aggregate", "traverse", "export"},
    "aggregate": {"inspect", "compare", "timeline", "traverse", "export"},
    "timeline": {"inspect", "filter", "rank", "compare", "aggregate", "traverse", "export"},
    "traverse": {"inspect", "list", "search", "filter", "rank", "compare", "aggregate", "timeline", "traverse", "export"},
    "transform": {"inspect", "search", "filter", "aggregate", "traverse", "export", "audit"},
    "audit": {"inspect", "transform", "export"},
    "export": set(),
}


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["capability_id"] == right["capability_id"]:
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
    for atom in chain:
        parts.extend(
            (
                str(atom.get("operation_family") or "other"),
                str(atom.get("input_kind") or "unknown"),
                str(atom.get("output_kind") or "unknown"),
            )
        )
    return tuple(parts)


def _representative_neighbors(
    atom: dict[str, Any],
    atoms: list[dict[str, Any]],
    *,
    per_family: int = 2,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in atoms:
        if _compatible(atom, candidate):
            grouped[str(candidate.get("operation_family") or "other")].append(candidate)
    selected: list[dict[str, Any]] = []
    for family in sorted(grouped):
        ordered = sorted(
            grouped[family],
            key=lambda item: (
                -int(item.get("parameter_cardinality", 0)),
                -int(item.get("support_count", 0)),
                str(item.get("capability_id")),
            ),
        )
        selected.extend(ordered[:per_family])
    return selected


def build_composition_profile(
    atoms: list[dict[str, Any]],
    *,
    minimum_length: int = 2,
    maximum_length: int = 5,
    maximum_shapes: int = 5_000,
) -> dict[str, Any]:
    """枚举可执行工具链形状，并估算能够实例化的任务数量。"""

    atoms = sorted(atoms, key=lambda item: str(item.get("capability_id")))
    neighbors = {
        atom["capability_id"]: _representative_neighbors(atom, atoms)
        for atom in atoms
    }
    transition_shapes = {
        (
            str(atom.get("operation_family")),
            str(atom.get("output_kind")),
            str(candidate.get("operation_family")),
        )
        for atom in atoms
        for candidate in neighbors[atom["capability_id"]]
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
                    "capability_ids": [
                        str(item.get("capability_id")) for item in chain
                    ],
                    "estimated_instances": contribution,
                }
                previous = shapes.get(key)
                if previous is None or contribution > previous["estimated_instances"]:
                    shapes[key] = candidate
        if len(chain) >= maximum_length:
            return
        used = {str(item.get("capability_id")) for item in chain}
        for candidate in neighbors[chain[-1]["capability_id"]]:
            if candidate["capability_id"] in used:
                continue
            visit([*chain, candidate])
            if len(shapes) >= maximum_shapes:
                return

    # 优先从检索、筛选、列表和文件解析入口开始，剩余能力作为兜底入口。
    entry_order = {"search": 0, "filter": 1, "list": 2, "transform": 3, "inspect": 4}
    for atom in sorted(
        atoms,
        key=lambda item: (
            entry_order.get(str(item.get("operation_family")), 9),
            str(item.get("capability_id")),
        ),
    ):
        visit([atom])
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
    return {
        "transition_shape_count": len(transition_shapes),
        "chain_shape_count": len(ordered_shapes),
        "long_chain_shape_count": sum(
            1 for item in ordered_shapes if int(item["length"]) >= 3
        ),
        "estimated_task_instances": sum(
            int(item["estimated_instances"]) for item in ordered_shapes
        ),
        "chains": ordered_shapes,
    }
