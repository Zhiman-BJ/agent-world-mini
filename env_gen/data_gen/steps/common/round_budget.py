"""Agent 阶段的轮次预算判断。"""

from __future__ import annotations


def is_last_available_round(
    *,
    round_index: int,
    max_rounds: int,
    remaining_seconds: int,
    per_round_seconds: int,
) -> bool:
    """判断当前调用是否已经是轮数或总时间允许的最后一轮。"""

    return (
        round_index >= max_rounds
        or remaining_seconds <= per_round_seconds
    )


__all__ = ["is_last_available_round"]
