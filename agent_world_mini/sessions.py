from __future__ import annotations

from typing import Any, Callable, Hashable

from .runtime import LocalToolRuntime


def runtime_for_rollout(
    rollout: Any,
    key: Hashable,
    factory: Callable[[], LocalToolRuntime],
) -> LocalToolRuntime:
    """Return one shared environment runtime owned by a single rollout."""
    sessions = getattr(rollout, "_agentworld_runtime_sessions", None)
    if sessions is None:
        sessions = {}
        setattr(rollout, "_agentworld_runtime_sessions", sessions)
    runtime = sessions.get(key)
    if runtime is None:
        runtime = factory()
        sessions[key] = runtime
    return runtime
