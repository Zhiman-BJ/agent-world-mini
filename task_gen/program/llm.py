"""LLM adapter shared by Program pipeline stages."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

_RECORDER: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("program_llm_recorder", default=None)


@contextmanager
def capture_calls(step: str, sink: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Record calls emitted by cooperating clients; legacy clients remain valid."""
    token = _RECORDER.set(lambda record: sink({"step": step, **record}))
    try:
        yield
    finally:
        _RECORDER.reset(token)


def record_call(record: dict[str, Any]) -> None:
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder(record)
